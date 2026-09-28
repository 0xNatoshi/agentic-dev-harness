"""Isolated subprocess environment for disposable Git repositories."""

import functools
import os
from pathlib import Path, PureWindowsPath
import shlex
import shutil
import subprocess
import sys
import tempfile


def write_fixture(path: Path, text: str) -> None:
    """Write fixture text byte for byte: UTF-8, no newline translation."""
    path.write_text(text, encoding="utf-8", newline="")


def is_wsl_launcher(path: str) -> bool:
    """True for the Windows System32 or WindowsApps bash.exe that starts WSL."""
    parts = [part.lower() for part in PureWindowsPath(path).parts]
    return parts[-1:] == ["bash.exe"] and ("system32" in parts or "windowsapps" in parts)


def resolve_bash(environ: dict[str, str] | None = None, which=shutil.which) -> str:
    """HARNESS_BASH if set, else bash on PATH; the WSL launcher is rejected."""
    environ = os.environ if environ is None else environ
    bash = environ.get("HARNESS_BASH") or which("bash")
    if not bash:
        raise RuntimeError("bash not found: install Git Bash or set HARNESS_BASH to its bash.exe")
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
    tool_shim(tools, "python3", sys.executable)
    environment = {
        "PATH": str(tools) + os.pathsep + os.environ.get("PATH", os.defpath),
        "XDG_CONFIG_HOME": str(fixture_config),
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
    return environment, tools


def run(
    args: list[str], cwd: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    # A bare "bash" goes through the single resolved Bash.
    if args and args[0] == "bash":
        args = [bash(), *args[1:]]
    return subprocess.run(
        args, cwd=cwd, env=environment, capture_output=True, text=True,
        encoding="utf-8", timeout=20
    )
