"""Run the documented PowerShell package bootstrap against disposable downloads."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
GUIDES = ("INSTALL-CODEX.md", "INSTALL-CLAUDE.md")
FENCE = re.compile(r"```powershell\r?\n(.*?)\r?\n```", re.DOTALL)
MARKED = re.compile(
    r"<!-- package-bootstrap:start -->\s*```powershell\r?\n(.*?)\r?\n```\s*<!-- package-bootstrap:end -->",
    re.DOTALL,
)
POISON = (
    "from pathlib import Path\n"
    "import os\n"
    "Path(os.environ['HARNESS_TEST_POISON_MARKER']).write_text('executed', encoding='utf-8')\n"
    "raise SystemExit(67)\n"
).encode("utf-8")


def guide_script(name: str) -> tuple[str, bool]:
    guide_root = Path(os.environ.get("HARNESS_INSTALL_GUIDES_ROOT", ROOT))
    source = (guide_root / "docs" / name).read_text(encoding="utf-8")
    marked = MARKED.findall(source)
    if marked:
        if len(marked) != 1:
            raise AssertionError(f"Expected one bootstrap in {name}")
        return marked[0], True

    # The pre-fix guides gave these as two real PowerShell blocks, executed from
    # the extracted package root. Keep the same security assertion on that path.
    fences = FENCE.findall(source)
    discovery = [block for block in fences if "$pythonCandidates =" in block]
    verification = [block for block in fences if "verify-package $packageZip" in block]
    if len(discovery) != 1 or len(verification) != 1:
        raise AssertionError(f"Cannot identify historical package commands in {name}")
    return discovery[0] + "\n" + verification[0], False


def powershell_executable() -> str:
    selected = os.environ.get("HARNESS_POWERSHELL")
    if selected:
        resolved = shutil.which(selected)
        if not resolved or not Path(resolved).is_file():
            raise AssertionError("HARNESS_POWERSHELL does not resolve to an executable")
        return resolved
    candidates = ("powershell", "pwsh") if os.name == "nt" else ("pwsh", "powershell")
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved and Path(resolved).is_file():
            return resolved
    raise unittest.SkipTest("No PowerShell executable available; install one or set HARNESS_POWERSHELL")


def replace_member(original: bytes, member_name: str, replacement: bytes) -> bytes:
    output = io.BytesIO()
    found = False
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(output, "w") as target:
        for member in source.infolist():
            data = source.read(member)
            if member.filename == member_name:
                data = replacement
                found = True
            target.writestr(member, data)
    if not found:
        raise AssertionError(f"Missing archive member: {member_name}")
    return output.getvalue()


class InstallGuideBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.powershell = powershell_executable()
        cls.package_workspace = tempfile.TemporaryDirectory(prefix="harness-guide-build-")
        try:
            output = Path(cls.package_workspace.name)
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "build.py"), "--output", str(output)],
                cwd=ROOT, capture_output=True, text=True, check=True,
            )
            built = json.loads(result.stdout)
            cls.package_name = "dev-harness-v" + built["version"]
            cls.archive_name = built["archive"]
            cls.checksum_name = cls.package_name + "-SHA256SUMS.txt"
            cls.archive_bytes = (output / cls.archive_name).read_bytes()
            cls.checksum_bytes = (output / cls.checksum_name).read_bytes()
        except BaseException:
            cls.package_workspace.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.package_workspace.cleanup()

    def setUp(self):
        workspace = tempfile.TemporaryDirectory(prefix="harness-guide-case-")
        self.addCleanup(workspace.cleanup)
        self.base = Path(workspace.name)
        self.download = self.base / "downloads with spaces"
        self.download.mkdir()
        self.archive = self.download / self.archive_name
        self.sums = self.download / self.checksum_name
        self.archive.write_bytes(self.archive_bytes)
        self.sums.write_bytes(self.checksum_bytes)
        self.poison_marker = self.base / "poison-ran.txt"

    def run_guide(self, name: str, *, prelude: str = "", legacy_root: Path | None = None,
                  extra_env: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess[str], bool]:
        block, fixed = guide_script(name)
        working_directory = legacy_root if legacy_root is not None else self.download
        script = self.base / "run-guide.ps1"
        script.write_text(
            "try {\n" + prelude + "\n" + block + "\nexit 0\n"
            "} catch {\n[Console]::Error.WriteLine($_.Exception.ToString())\nexit 1\n}\n",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment["HARNESS_TEST_POISON_MARKER"] = str(self.poison_marker)
        environment.update(extra_env or {})
        result = subprocess.run(
            [self.powershell, "-NoProfile", "-NonInteractive", "-File", str(script)],
            cwd=working_directory, env=environment, capture_output=True, text=True,
            timeout=60,
        )
        return result, fixed

    def verified_root(self) -> Path:
        roots = list(self.download.glob("dev-harness-verified-*"))
        self.assertEqual(len(roots), 1, f"Expected one fresh verified workspace, got {roots}")
        return roots[0]

    def assert_bootstrap_succeeded(self, result: subprocess.CompletedProcess[str]) -> Path:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        root = self.verified_root()
        self.assertEqual((root / self.archive_name).read_bytes(), self.archive_bytes)
        self.assertTrue((root / self.package_name / "install.py").is_file())
        return root

    def test_tampered_archive_never_runs_installer(self):
        """The same final assertion is red on the historical guide and green after the fix."""
        tampered = replace_member(
            self.archive_bytes, self.package_name + "/install.py", POISON,
        )
        self.archive.write_bytes(tampered)
        for guide in GUIDES:
            with self.subTest(guide=guide):
                self.poison_marker.unlink(missing_ok=True)
                _, fixed = guide_script(guide)
                legacy_root = None
                if not fixed:
                    # Historical instructions explicitly started in an already extracted ZIP.
                    with zipfile.ZipFile(io.BytesIO(tampered)) as bundle:
                        bundle.extractall(self.download)
                    legacy_root = self.download / self.package_name
                    if os.name != "nt":
                        # Native Python on POSIX treats PowerShell's .\install.py
                        # argument as a literal filename. Mirror the extracted
                        # installer under that spelling to exercise the same call.
                        (legacy_root / ".\\install.py").write_bytes((legacy_root / "install.py").read_bytes())
                result, _ = self.run_guide(guide, legacy_root=legacy_root)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(self.poison_marker.exists(), "Untrusted install.py executed before authentication")

    def test_valid_archive_works_from_path_with_spaces(self):
        for guide in GUIDES:
            with self.subTest(guide=guide):
                if not guide_script(guide)[1]:
                    self.skipTest("Historical guides have no authenticated bootstrap")
                result, _ = self.run_guide(guide)
                self.assert_bootstrap_succeeded(result)
                shutil.rmtree(self.verified_root())

    def test_existing_poisoned_extraction_is_ignored(self):
        for guide in GUIDES:
            with self.subTest(guide=guide):
                if not guide_script(guide)[1]:
                    self.skipTest("Historical guides have no authenticated bootstrap")
                stale = self.download / self.package_name
                stale.mkdir(exist_ok=True)
                (stale / "install.py").write_bytes(POISON)
                result, _ = self.run_guide(guide)
                root = self.assert_bootstrap_succeeded(result)
                self.assertFalse(self.poison_marker.exists())
                self.assertEqual((stale / "install.py").read_bytes(), POISON)
                shutil.rmtree(root)

    def test_replaced_original_zip_after_real_hash_cannot_run_replacement(self):
        replacement = self.base / "replacement.zip"
        replacement.write_bytes(replace_member(
            self.archive_bytes, self.package_name + "/install.py", POISON,
        ))
        hook = self.base / "hash-hook-ran.txt"
        prelude = """
function Get-FileHash {
    [CmdletBinding()]
    param([System.IO.Stream] $InputStream, [string] $Algorithm)
    $result = Microsoft.PowerShell.Utility\\Get-FileHash -InputStream $InputStream -Algorithm $Algorithm -ErrorAction Stop
    [IO.File]::WriteAllBytes($env:HARNESS_TEST_ARCHIVE_SOURCE, [IO.File]::ReadAllBytes($env:HARNESS_TEST_REPLACEMENT_ZIP))
    [IO.File]::WriteAllText($env:HARNESS_TEST_HASH_HOOK, 'called')
    return $result
}
"""
        for guide in GUIDES:
            with self.subTest(guide=guide):
                if not guide_script(guide)[1]:
                    self.skipTest("Historical guides have no authenticated bootstrap")
                self.archive.write_bytes(self.archive_bytes)
                self.poison_marker.unlink(missing_ok=True)
                hook.unlink(missing_ok=True)
                result, _ = self.run_guide(guide, prelude=prelude, extra_env={
                    "HARNESS_TEST_ARCHIVE_SOURCE": str(self.archive),
                    "HARNESS_TEST_REPLACEMENT_ZIP": str(replacement),
                    "HARNESS_TEST_HASH_HOOK": str(hook),
                })
                self.assertTrue(hook.exists(), result.stdout + result.stderr)
                self.assertEqual(self.archive.read_bytes(), replacement.read_bytes())
                self.assertFalse(self.poison_marker.exists(), "Replacement install.py executed")
                if result.returncode == 0:
                    root = self.verified_root()
                    self.assertEqual((root / self.archive_name).read_bytes(), self.archive_bytes)
                    shutil.rmtree(root)

    def test_python_startup_ignores_cwd_and_pythonpath(self):
        startup = self.download / "sitecustomize.py"
        startup.write_text(POISON.decode("utf-8"), encoding="utf-8")
        for guide in GUIDES:
            with self.subTest(guide=guide):
                if not guide_script(guide)[1]:
                    self.skipTest("Historical guides have no authenticated bootstrap")
                result, _ = self.run_guide(guide, extra_env={"PYTHONPATH": str(self.download)})
                root = self.assert_bootstrap_succeeded(result)
                self.assertFalse(self.poison_marker.exists(), "Python loaded untrusted sitecustomize.py")
                shutil.rmtree(root)

    def test_malformed_missing_and_duplicate_archive_sums_refuse(self):
        archive_line = next(
            line for line in self.checksum_bytes.decode("utf-8").splitlines()
            if line.endswith("  " + self.archive_name)
        )
        manifest_line = next(
            line for line in self.checksum_bytes.decode("utf-8").splitlines()
            if line.endswith("  " + self.package_name + "-MANIFEST.json")
        )
        cases = {
            "malformed": "invalid checksum line\n" + manifest_line + "\n",
            "missing": manifest_line + "\n",
            "duplicate": archive_line + "\n" + archive_line + "\n" + manifest_line + "\n",
        }
        for guide in GUIDES:
            if not guide_script(guide)[1]:
                self.skipTest("Historical guides have no authenticated bootstrap")
            for label, content in cases.items():
                with self.subTest(guide=guide, case=label):
                    self.sums.write_text(content, encoding="utf-8")
                    result, _ = self.run_guide(guide)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(list(self.download.glob("dev-harness-verified-*")), [])

    def test_authenticated_unsafe_member_refused_before_full_extraction(self):
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(self.archive_bytes)) as source, zipfile.ZipFile(output, "w") as target:
            for member in source.infolist():
                target.writestr(member, source.read(member))
            target.writestr(self.package_name + "/../unsafe.txt", b"unexpected")
        changed = output.getvalue()
        original_hash = hashlib.sha256(self.archive_bytes).hexdigest()
        new_hash = hashlib.sha256(changed).hexdigest()
        sums = self.checksum_bytes.decode("utf-8").replace(original_hash, new_hash)
        self.archive.write_bytes(changed)
        self.sums.write_text(sums, encoding="utf-8")
        for guide in GUIDES:
            with self.subTest(guide=guide):
                if not guide_script(guide)[1]:
                    self.skipTest("Historical guides have no authenticated bootstrap")
                result, _ = self.run_guide(guide)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Unsafe package path", result.stdout + result.stderr)
                root = self.verified_root()
                self.assertTrue((root / "bootstrap-install.py").is_file())
                self.assertFalse((root / self.package_name).exists(), "Full extraction preceded member verification")
                shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
