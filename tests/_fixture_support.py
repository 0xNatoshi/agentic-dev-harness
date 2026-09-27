"""Isolated subprocess environment for disposable Git repositories."""

import os
from pathlib import Path
import subprocess
import sys


def fixture_environment(root: Path) -> tuple[dict[str, str], Path]:
    fixture_config = root / "fixture-config"
    fixture_config.mkdir()
    tools = root / "bin"
    tools.mkdir()
    (tools / "python3").symlink_to(sys.executable)
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
    return subprocess.run(
        args, cwd=cwd, env=environment, capture_output=True, text=True, timeout=20
    )
