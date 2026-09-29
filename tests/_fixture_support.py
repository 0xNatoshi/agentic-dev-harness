"""Isolated subprocess environment for disposable Git repositories."""

import functools
import os
from pathlib import Path, PureWindowsPath
import shlex
import shutil
import subprocess
import sys
import tempfile


# Native Windows Python cannot execute the extensionless Python fakes that Bash
# finds on PATH. This startup adapter exists only inside disposable fixtures;
# production code and lookup without a matching fixture remain unchanged.
WINDOWS_FIXTURE_STARTUP = '''import inspect
import os
from pathlib import Path
import subprocess
import sys

_tools = Path(__file__).resolve().parent.parent / "bin"
_Popen = subprocess.Popen


class FixturePopen(_Popen):
    _fixture_bin = str(_tools)

    def __init__(self, args, *positional, **options):
        requested = args
        name = args[0] if isinstance(args, (list, tuple)) and args else None
        if isinstance(name, str) and name and Path(name).name == name and not Path(name).suffix:
            script = _tools / name
            if script.is_file():
                invocation = inspect.signature(_Popen).bind(args, *positional, **options).arguments
                environment = invocation.get("env")
                if environment is None:
                    environment = os.environ
                path = next((value for key, value in environment.items() if key.upper() == "PATH"), "")
                if not any(Path(part).resolve() == _tools for part in path.split(os.pathsep) if part):
                    # CreateProcess may use the parent's PATH instead of env's PATH.
                    # Refuse rather than let an excluded fake fall through to a real CLI.
                    raise FileNotFoundError("Fixture directory is not on the command PATH: " + name)
                with script.open("rb") as source:
                    python_fixture = source.readline(128).rstrip(b"\\r\\n") == b"#!/usr/bin/env python3"
                if not python_fixture:
                    raise ValueError("Native Python cannot run non-Python fixture: " + name)
                if invocation.get("shell") or invocation.get("executable") is not None:
                    raise ValueError("Python fixtures do not support shell or executable overrides")
                args = [sys.executable, str(script), *args[1:]]
        super().__init__(args, *positional, **options)
        self.args = requested


subprocess.Popen = FixturePopen
'''


def write_fixture(path: Path, text: str) -> None:
    """Write fixture text byte for byte: UTF-8, no newline translation."""
    path.write_text(text, encoding="utf-8", newline="")


def is_wsl_launcher(path: str) -> bool:
    """True for the System32 or WindowsApps bash(.exe) that starts WSL."""
    launcher = PureWindowsPath(path)
    return (launcher.stem.lower() == "bash" and launcher.suffix.lower() in ("", ".exe")
            and launcher.parent.name.lower() in ("system32", "windowsapps"))


def resolve_bash(environ: dict[str, str] | None = None, which=shutil.which) -> str:
    """HARNESS_BASH if set and non-empty, else bash on PATH, as an absolute path; the WSL launcher is rejected."""
    environ = os.environ if environ is None else environ
    bash = environ.get("HARNESS_BASH") or "bash"
    if PureWindowsPath(bash).name == bash:  # a bare name, on either path syntax
        bash = which(bash)
    if not bash:
        raise RuntimeError("bash not found: install Git Bash or set HARNESS_BASH to its bash.exe")
    # Absolute, because a relative program path is resolved against each fixture's cwd.
    bash = os.path.abspath(bash)
    if is_wsl_launcher(bash):
        raise RuntimeError(f"{bash} starts WSL, not Git Bash: set HARNESS_BASH to Git Bash's bash.exe")
    return bash


@functools.cache
def bash() -> str:
    # Resolved once, so every fixture runs the same Bash; on Windows, CreateProcess
    # searches System32 before an overridden PATH.
    return resolve_bash()


@functools.cache
def symlinks_supported() -> bool:
    """Windows needs Developer Mode or elevation to create symlinks."""
    with tempfile.TemporaryDirectory() as directory:
        try:
            (Path(directory) / "link").symlink_to(directory, target_is_directory=True)
        except (OSError, NotImplementedError):
            return False
    return True


def tool_shim(directory: Path, name: str, target: str) -> Path:
    """An executable sh script forwarding to target, instead of a symlink."""
    shim = directory / name
    write_fixture(shim, f'#!/bin/sh\nexec {shlex.quote(Path(target).as_posix())} "$@"\n')
    shim.chmod(0o755)
    return shim


def fixture_environment(root: Path) -> tuple[dict[str, str], Path]:
    fixture_config = root / "fixture-config"
    fixture_config.mkdir()
    tools = root / "bin"
    tools.mkdir()
    temporary = root / "tmp"
    temporary.mkdir()
    tool_shim(tools, "python3", sys.executable)
    environment = {
        "PATH": str(tools) + os.pathsep + os.environ.get("PATH", os.defpath),
        "XDG_CONFIG_HOME": str(fixture_config),
        # Git Bash's fallback /tmp need not be writable. Keep native and shell
        # temporary files inside the fixture so isolation includes their cleanup.
        "TMPDIR": temporary.as_posix(),
        "TMP": str(temporary),
        "TEMP": str(temporary),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "LC_ALL": "C",
    }
    if os.name == "nt":
        # Git's bin/bash.exe prepends its own tools. Restore fixture precedence
        # only when the caller's PATH includes it, preserving restricted-PATH tests.
        # Builtins work without PATH tools; logical pwd preserves Windows short names in PATH.
        bash_startup = fixture_config / "bash-env"
        write_fixture(bash_startup,
                      f"_fixture_tools=$(cd -- {shlex.quote(tools.as_posix())} && pwd -L) || exit 1\n"
                      'case ":$PATH:" in\n'
                      '  ":$_fixture_tools:"*) ;;\n'
                      '  *":$_fixture_tools:"*) export PATH="$_fixture_tools:$PATH" ;;\n'
                      'esac\n'
                      'unset _fixture_tools\n')
        environment["BASH_ENV"] = str(bash_startup)
        write_fixture(fixture_config / "sitecustomize.py", WINDOWS_FIXTURE_STARTUP)
        environment["PYTHONPATH"] = str(fixture_config)
        # Python reports startup import errors but then continues. Fail before
        # a missing adapter could let a fixture invoke an installed real CLI.
        witness = "import subprocess, sys; sys.exit(getattr(subprocess.Popen, '_fixture_bin', None) != sys.argv[1])"
        initialized = subprocess.run(
            [sys.executable, "-c", witness, str(tools.resolve())], env=environment,
            capture_output=True, text=True, encoding="utf-8", errors="backslashreplace", timeout=20,
        )
        if initialized.returncode:
            raise RuntimeError("Windows fixture adapter did not initialize: " + initialized.stderr.strip())
    return environment, tools


def run(
    args: list[str], cwd: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    # A bare "bash" goes through the single resolved Bash.
    if args and args[0] == "bash":
        args = [bash(), *args[1:]]
    return subprocess.run(
        args, cwd=cwd, env=environment, capture_output=True, text=True,
        encoding="utf-8", errors="backslashreplace", timeout=20
    )
