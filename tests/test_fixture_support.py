"""Keep the local gate runnable where symlinks, UTF-8 locales or Git Bash are not the default."""

import json
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from tests import _fixture_support as support


PYTHON_FIXTURE = """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

print(json.dumps({
    "arguments": sys.argv[1:],
    "cwd": Path.cwd().resolve().as_posix(),
    "environment": os.environ.get("FIXTURE_VALUE"),
    "stdin": sys.stdin.read(),
}))
print("fixture stderr", file=sys.stderr)
sys.exit(7)
"""

NESTED_PYTHON_PROBE = """import json
import os
from pathlib import Path
import subprocess
import sys

arguments = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
environment = dict(os.environ, FIXTURE_VALUE="forwarded environment")
if os.name == "nt":
    environment["Path"] = environment.pop("PATH")
result = subprocess.run(
    [sys.argv[1], *arguments], cwd=sys.argv[2],
    env=environment,
    input="forwarded stdin", capture_output=True, text=True, encoding="utf-8",
)
ordinary = subprocess.run(
    [sys.executable, "-c", "print('ordinary child')"],
    capture_output=True, text=True, encoding="utf-8",
)
print(json.dumps({
    "fixture": [result.returncode, result.stdout, result.stderr],
    "invocation": result.args,
    "ordinary": [ordinary.returncode, ordinary.stdout, ordinary.stderr],
}))
"""


class FixtureEnvironmentTests(unittest.TestCase):
    def test_fixture_environment_needs_no_symlinks(self) -> None:
        # Windows without Developer Mode or elevation refuses to create symlinks.
        with TemporaryDirectory(prefix="fixture-support-") as directory, \
                mock.patch("os.symlink", side_effect=OSError("symlinks unavailable")):
            environment, _ = support.fixture_environment(Path(directory))
            result = support.run(
                ["bash", "-c", "python3 -c 'import sys; print(sys.version_info[0])'"], Path(directory), environment
            )
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "3"), result.stderr)

    def test_fixture_text_keeps_its_bytes(self) -> None:
        with TemporaryDirectory(prefix="fixture-support-") as directory:
            path = Path(directory) / "fixture.md"
            support.write_fixture(path, "a\r\nb\n\ufeff\u2011\u2212\n")
            self.assertEqual(path.read_bytes(), "a\r\nb\n\ufeff\u2011\u2212\n".encode("utf-8"))

    def test_fixture_text_ignores_locale_and_newline_defaults(self) -> None:
        # POSIX in a UTF-8 locale writes the same bytes either way; Windows does not.
        with mock.patch.object(Path, "write_text") as write_text:
            support.write_fixture(Path("fixture.md"), "text")
        self.assertEqual(write_text.call_args.kwargs, {"encoding": "utf-8", "newline": ""})

    def test_fixture_temporary_files_stay_inside_disposable_root(self) -> None:
        with TemporaryDirectory(prefix="fixture-support-") as directory:
            root = Path(directory) / "root with spaces"
            root.mkdir()
            environment, _ = support.fixture_environment(root)
            # Git Bash reports mounted /tmp paths; native Path needs their Windows spelling.
            print_temporary = 'cygpath -m "$temporary"' if os.name == "nt" else 'printf \'%s\\n\' "$temporary"'
            commands = (
                ["bash", "-c", 'temporary=$(mktemp) || exit; trap \'rm -f "$temporary"\' EXIT; ' + print_temporary],
                [sys.executable, "-c", "from tempfile import NamedTemporaryFile\nwith NamedTemporaryFile() as temporary:\n    print(temporary.name)"],
            )
            for command in commands:
                with self.subTest(command=command[0]):
                    result = support.run(command, root, environment)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    temporary = Path(result.stdout.strip()).resolve()
                    self.assertTrue(temporary.is_relative_to(root.resolve()), result.stdout)
                    self.assertFalse(temporary.exists())

    def test_bash_uses_fixture_tools_and_respects_restricted_path(self) -> None:
        with TemporaryDirectory(prefix="fixture-support-") as directory:
            root = Path(directory) / "root with spaces"
            root.mkdir()
            environment, tools = support.fixture_environment(root)
            real_git = shutil.which("git", path=environment["PATH"])
            self.assertIsNotNone(real_git)
            fake = tools / "git"
            support.write_fixture(fake, "#!/bin/sh\nprintf '%s\\n' 'fixture git'\nexit 7\n")
            fake.chmod(0o755)
            for command in ("git --version", "bash -c 'git --version'"):
                with self.subTest(command=command):
                    result = support.run(["bash", "-c", command], root, environment)
                    self.assertEqual((result.returncode, result.stdout), (7, "fixture git\n"), result.stderr)
            restricted = environment | {"PATH": str(Path(real_git).parent)}
            result = support.run(["bash", "-c", "git --version"], root, restricted)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(result.stdout.startswith("git version "), result.stdout)

    def test_nested_bash_starts_with_no_tools_on_path(self) -> None:
        with TemporaryDirectory(prefix="fixture-support-") as directory:
            root = Path(directory) / "root with spaces and 'quotes'"
            root.mkdir()
            environment, _ = support.fixture_environment(root)
            # BASH names the running shell, bypassing launchers that add their own tools.
            result = support.run(
                ["bash", "-c", 'PATH=; export PATH; exec "$BASH" -c \'[ -z "$PATH" ] && printf "%s\\n" "fixture started"\''],
                root, environment,
            )
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "fixture started\n", ""))

    def test_native_python_runs_python_fixture_scripts(self) -> None:
        # Exercise Bash -> native Python -> a real fake process, with no installed CLI lookup.
        newlines = ("\n", "\r\n") if os.name == "nt" else ("\n",)
        arguments = ["two words", 'a"quote', "", "trailing\\", "semi;colon", "& exit 99", "caf\u00e9"]
        for newline in newlines:
            with self.subTest(newline=repr(newline)), TemporaryDirectory(prefix="fixture-support-") as directory:
                root = Path(directory) / "root with spaces"
                root.mkdir()
                environment, tools = support.fixture_environment(root)
                fake = tools / "harness_fixture_python_probe"
                support.write_fixture(fake, PYTHON_FIXTURE.replace("\n", newline))
                fake.chmod(0o755)
                child = root / "child work"
                child.mkdir()
                driver = root / "driver.py"
                support.write_fixture(driver, NESTED_PYTHON_PROBE)
                # Keep Windows-to-Bash quoting separate from the native child boundary under test.
                argument_file = root / "arguments.json"
                support.write_fixture(argument_file, json.dumps(arguments))
                result = support.run(
                    ["bash", "-c", 'python3 "$@"', "fixture-driver", str(driver), fake.name, str(child), str(argument_file)],
                    root, environment,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["invocation"], [fake.name, *arguments])
                status, stdout, stderr = report["fixture"]
                self.assertEqual(status, 7)
                self.assertEqual(json.loads(stdout), {
                    "arguments": arguments, "cwd": child.resolve().as_posix(),
                    "environment": "forwarded environment", "stdin": "forwarded stdin",
                })
                self.assertEqual(stderr, "fixture stderr\n")
                self.assertEqual(report["ordinary"], [0, "ordinary child\n", ""])

    @unittest.skipUnless(os.name == "nt", "native fixture startup adapter is Windows-only")
    def test_native_python_fixture_rejects_shell_and_executable_overrides(self) -> None:
        with TemporaryDirectory(prefix="fixture-support-") as directory:
            root = Path(directory)
            environment, tools = support.fixture_environment(root)
            fake = tools / "harness_fixture_python_probe"
            support.write_fixture(fake, PYTHON_FIXTURE)
            probe = """import subprocess
import sys

for options in ({"shell": True}, {"executable": sys.executable}):
    try:
        subprocess.run([sys.argv[1]], capture_output=True, **options)
    except ValueError:
        print("refused override")
    else:
        raise AssertionError("fixture override was not refused")
"""
            result = support.run([sys.executable, "-c", probe, fake.name], root, environment)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.splitlines(), ["refused override", "refused override"])

    @unittest.skipUnless(os.name == "nt", "native fixture startup adapter is Windows-only")
    def test_native_python_rejects_non_python_fixture_instead_of_running_installed_tool(self) -> None:
        with TemporaryDirectory(prefix="fixture-support-") as directory:
            root = Path(directory)
            environment, tools = support.fixture_environment(root)
            fake = tools / "harness_fixture_dispatch_probe"
            installed = root / "installed"
            installed.mkdir()
            shutil.copyfile(sys.executable, installed / (fake.name + ".exe"))
            environment["PATH"] += os.pathsep + str(installed)
            probe = """import subprocess
import sys

try:
    subprocess.run([sys.argv[1], "-c", "print('unexpected executable')"], capture_output=True)
except ValueError as error:
    print(error)
else:
    raise AssertionError("native lookup ignored the fixture")
"""
            for content in ("#!/bin/sh\nexit 71\n", "print('missing shebang')\n"):
                with self.subTest(content=content):
                    support.write_fixture(fake, content)
                    result = support.run([sys.executable, "-c", probe, fake.name], root, environment)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(result.stdout.strip(), "Native Python cannot run non-Python fixture: " + fake.name)

    @unittest.skipUnless(os.name == "nt", "native fixture startup adapter is Windows-only")
    def test_native_python_respects_fixture_exclusion_from_path(self) -> None:
        with TemporaryDirectory(prefix="fixture-support-") as directory:
            root = Path(directory)
            environment, tools = support.fixture_environment(root)
            fake = tools / "harness_fixture_path_probe"
            support.write_fixture(fake, PYTHON_FIXTURE)
            installed = root / "installed"
            installed.mkdir()
            shutil.copyfile(sys.executable, installed / (fake.name + ".exe"))
            environment["PATH"] += os.pathsep + str(installed)
            probe = """import os
import subprocess
import sys

for inherited in (False, True):
    options = {"env": dict(os.environ, PATH="")}
    if inherited:
        os.environ["PATH"] = ""
        options = {}
    try:
        subprocess.run([sys.argv[1], "-c", "print('unexpected executable')"], input="", capture_output=True, text=True, **options)
    except FileNotFoundError:
        print("fixture excluded")
    else:
        raise AssertionError("native lookup ignored the fixture exclusion")
"""
            result = support.run([sys.executable, "-c", probe, fake.name], root, environment)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.splitlines(), ["fixture excluded", "fixture excluded"])

    @unittest.skipUnless(os.name == "nt", "native fixture startup adapter is Windows-only")
    def test_native_python_fixture_startup_failure_is_reported(self) -> None:
        write_fixture = support.write_fixture

        def corrupt_startup(path, text):
            if path.name == "sitecustomize.py":
                text = "raise RuntimeError('fixture startup rejected')\n"
            write_fixture(path, text)

        with TemporaryDirectory(prefix="fixture-support-") as directory, \
                mock.patch.object(support, "write_fixture", side_effect=corrupt_startup):
            with self.assertRaisesRegex(RuntimeError, "Windows fixture adapter"):
                support.fixture_environment(Path(directory))


class BashResolutionTests(unittest.TestCase):
    def test_override_wins(self) -> None:
        found = support.resolve_bash({"HARNESS_BASH": "/opt/git/bin/bash"}, lambda name: "/usr/bin/bash")
        self.assertEqual(found, os.path.abspath("/opt/git/bin/bash"))

    def test_empty_override_falls_back_to_path(self) -> None:
        self.assertEqual(support.resolve_bash({"HARNESS_BASH": ""}, lambda name: "/usr/bin/bash"),
                         os.path.abspath("/usr/bin/bash"))

    def test_bare_name_override_is_looked_up_on_path(self) -> None:
        self.assertEqual(support.resolve_bash({"HARNESS_BASH": "bash.exe"}, {"bash.exe": "/opt/git/bin/bash.exe"}.get),
                         os.path.abspath("/opt/git/bin/bash.exe"))

    def test_relative_override_is_made_absolute(self) -> None:
        # Fixtures run in other directories, where a relative program path would not resolve.
        self.assertEqual(support.resolve_bash({"HARNESS_BASH": "tools/bash"}, lambda name: None),
                         os.path.abspath("tools/bash"))

    def test_path_lookup_is_the_fallback(self) -> None:
        self.assertEqual(support.resolve_bash({}, lambda name: "/usr/bin/bash"), os.path.abspath("/usr/bin/bash"))

    def test_missing_bash_is_reported(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "HARNESS_BASH"):
            support.resolve_bash({}, lambda name: None)

    def test_wsl_launcher_is_rejected(self) -> None:
        for launcher in (r"C:\Windows\System32\bash.exe", r"c:\windows\system32\BASH.EXE",
                         r"C:\Windows\System32\bash",
                         r"D:\Profiles\AppData\Local\Microsoft\WindowsApps\bash.exe"):
            with self.subTest(launcher=launcher), self.assertRaisesRegex(RuntimeError, "WSL"):
                support.resolve_bash({}, lambda name: launcher)
            with self.subTest(override=launcher), self.assertRaisesRegex(RuntimeError, "WSL"):
                support.resolve_bash({"HARNESS_BASH": launcher}, lambda name: "/usr/bin/bash")

    def test_relative_override_into_system32_is_rejected(self) -> None:
        # Checked on the absolute path: "bash.exe" run from C:\Windows\System32 is the launcher.
        with mock.patch("os.path.abspath", return_value=r"C:\Windows\System32\bash.exe"), \
                self.assertRaisesRegex(RuntimeError, "WSL"):
            support.resolve_bash({"HARNESS_BASH": r".\bash.exe"}, lambda name: None)

    def test_git_bash_below_a_system32_folder_is_accepted(self) -> None:
        for bash in (r"D:\tools\System32\PortableGit\usr\bin\bash.exe", r"C:\Windows\System32\bash-helper.exe"):
            with self.subTest(bash=bash):
                self.assertFalse(support.is_wsl_launcher(bash))

    def test_bare_bash_runs_the_resolved_bash(self) -> None:
        with TemporaryDirectory(prefix="fixture-support-") as directory, \
                mock.patch.object(support, "bash", return_value="/resolved/bash"), \
                mock.patch("subprocess.run") as run:
            support.run(["bash", "-c", "true"], Path(directory), {})
        self.assertEqual(run.call_args.args[0], ["/resolved/bash", "-c", "true"])


if __name__ == "__main__":
    unittest.main()
