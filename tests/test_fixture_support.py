"""Keep the local gate runnable where symlinks, UTF-8 locales or Git Bash are not the default."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from tests import _fixture_support as support


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


class BashResolutionTests(unittest.TestCase):
    def test_override_wins(self) -> None:
        found = support.resolve_bash({"HARNESS_BASH": "/opt/git/bin/bash"}, lambda name: "/usr/bin/bash")
        self.assertEqual(found, os.path.abspath("/opt/git/bin/bash"))

    def test_empty_override_falls_back_to_path(self) -> None:
        self.assertEqual(support.resolve_bash({"HARNESS_BASH": ""}, lambda name: "/usr/bin/bash"),
                         os.path.abspath("/usr/bin/bash"))

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
