"""Rebuild an immutable unfixed installer and prove the final DACL tests on native Windows."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
# Delivered package limits, before the trusted-state ownership correction. This main commit
# remains reachable after squash delivery; no mutable branch, tag or downloaded code is used.
BASELINE = "c7d1eaba2e179b3276be45dc3941835d7394c4c8"
CASES = (
    "tests.test_installer.InstallerTests.test_windows_refuses_foreign_write_dacl_before_creating_lock",
    "tests.test_installer.InstallerTests.test_windows_recover_refuses_delete_child_or_write_dac",
    "tests.test_installer.InstallerTests.test_windows_refuses_unsafe_intermediate_junction_destination",
)


def run_cases(directory: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "-v", *CASES],
        cwd=directory, capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    print(result.stdout + result.stderr, end="")
    return result


def main() -> int:
    if os.name != "nt":
        raise RuntimeError("DACL regression proof requires native Windows Python")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()
    archive = subprocess.run(["git", "archive", "--format=tar", BASELINE], cwd=ROOT,
                             check=True, capture_output=True).stdout
    final_tests = (ROOT / "tests/test_installer.py").read_bytes()
    with tempfile.TemporaryDirectory(prefix="harness-unfixed-state-") as temporary:
        baseline = Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            for member in source.getmembers():
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                    raise RuntimeError("Unexpected member in the pinned repository snapshot")
            source.extractall(baseline)
        (baseline / "tests/test_installer.py").write_bytes(final_tests)
        print("Expected unfixed failure on " + BASELINE, flush=True)
        unfixed = run_cases(baseline)
        if unfixed.returncode != 1 or not re.search(r"FAILED \(failures=4\)", unfixed.stderr):
            raise RuntimeError("The same final DACL tests did not demonstrate the four expected unfixed failures")
        print("Fixed qualification on " + head, flush=True)
        fixed = run_cases(ROOT)
        if fixed.returncode != 0 or "skipped=" in fixed.stderr:
            raise RuntimeError("The same final DACL tests did not pass without skips on the fixed installer")
        if (final_tests != (baseline / "tests/test_installer.py").read_bytes()
                or final_tests != (ROOT / "tests/test_installer.py").read_bytes()):
            raise RuntimeError("The regression test bytes changed between the two states")
        print(json.dumps({
            "baseline": BASELINE,
            "head": head,
            "test_sha256": hashlib.sha256(final_tests).hexdigest(),
            "unfixed_failures": 4,
            "fixed_tests": len(CASES),
            "native_platform": "Windows",
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
