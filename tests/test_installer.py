"""Black-box tests of install.py, run from the extracted package against disposable fake homes."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import tracemalloc
import unittest
from unittest import mock
import warnings
import zipfile

from tests._fixture_support import symlinks_supported

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("harness_build_for_installer", ROOT / "scripts/build.py")
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)

RETIRED = [
    "templates/LICENSE-MIT",
    "templates/LICENSE-PolyForm-Noncommercial",
    "templates/LICENSE-proprietary",
    "readme-guide.md",
    "templates/README.md",
]
HOOKS = ("DEV_HARNESS_INSTALL_TEST_FAULT", "DEV_HARNESS_INSTALL_TEST_CRASH", "DEV_HARNESS_INSTALL_TEST_TRACE",
         "DEV_HARNESS_INSTALL_TEST_PROCESSES")


def snapshot(root: Path) -> dict | None:
    """Independent of the installer: every path with its type, bytes and permission bits."""
    if not os.path.lexists(root):
        return None
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            result[relative] = ("link", os.readlink(path))
        elif path.is_dir():
            result[relative] = ("dir", mode)
        else:
            result[relative] = ("file", mode, path.read_bytes())
    return result


def visible_copies(*roots: Path) -> list:
    """Every SKILL.md a runtime could discover as github-workflow under the given skill roots."""
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        for skill in root.rglob("SKILL.md"):
            text = skill.read_text(encoding="utf-8", errors="replace")
            if skill.parent.name == "github-workflow" or "\nname: github-workflow\n" in text:
                found.append(skill)
    return found


def link_directory(link: Path, target: Path) -> None:
    """A directory link: a junction on Windows, which needs no symlink privilege."""
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


def unlink_directory(link: Path) -> None:
    """Remove a directory link without touching its target; a Windows junction is removed as a directory."""
    if os.name == "nt":
        os.rmdir(link)
    else:
        os.unlink(link)


def relocated_transaction(value, old: Path, new: Path):
    """Rebind every generated receipt path while preserving its valid schema."""
    if isinstance(value, dict):
        return {key: relocated_transaction(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [relocated_transaction(item, old, new) for item in value]
    return value.replace(str(old), str(new)) if isinstance(value, str) else value


class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="harness-installer-")
        base = Path(cls.workspace.name)
        result = BUILD.build(base / "dist")
        prefix = "dev-harness-v" + result["version"]
        cls.archive = base / "dist" / result["archive"]
        cls.checksums = base / "dist" / (prefix + "-SHA256SUMS.txt")
        with zipfile.ZipFile(cls.archive) as archive:
            archive.extractall(base / "extracted")
        cls.package = base / "extracted" / prefix
        cls.installer = cls.package / "install.py"
        manifest = json.loads((cls.package / "MANIFEST.json").read_text(encoding="utf-8"))
        cls.skill_files = sorted(name[len("skills/github-workflow/"):] for name in manifest["files"]
                                 if name.startswith("skills/github-workflow/"))

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="harness-home-")
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.processes = self.base / "processes.txt"
        self.processes.write_text("/usr/sbin/sshd -D\n/bin/zsh -l\n", encoding="utf-8")
        # HOME and USERPROFILE point here unless a test sets them: nothing may be written to it.
        self.guard = self.base / "guard"
        self.guard.mkdir()
        self.addCleanup(lambda: self.assertEqual(os.listdir(self.guard), [], "the installer wrote to the default home"))

    # Helpers.
    def environment(self, env=None, processes=True):
        environment = {key: value for key, value in os.environ.items()
                       if key not in HOOKS and key not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        environment.update(HOME=str(self.guard), USERPROFILE=str(self.guard))
        if processes:
            environment["DEV_HARNESS_INSTALL_TEST_PROCESSES"] = str(self.processes)
        environment.update(env or {})
        return environment

    def run_installer(self, *arguments, env=None, processes=True, installer=None):
        return subprocess.run([sys.executable, "-B", str(installer or self.installer), *map(str, arguments)], cwd=self.base,
                              env=self.environment(env, processes), capture_output=True, text=True, encoding="utf-8", timeout=120)

    def run_limited_installer(self, limits, *arguments):
        # Exercise the same CLI with smaller budgets, so resource-limit cases stay small and deterministic.
        runner = """import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('harness_limited_install', sys.argv[1])
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
for name, value in json.loads(sys.argv[2]).items():
    setattr(installer, name, value)
sys.exit(installer.main(sys.argv[3:]))
"""
        return subprocess.run([sys.executable, "-B", "-c", runner, str(self.installer), json.dumps(limits),
                               *map(str, arguments)], cwd=self.base, env=self.environment(), capture_output=True,
                              text=True, encoding="utf-8", timeout=120)

    def run_with_state_drift(self, trigger, *arguments, env=None):
        """Exercise the public command after a deterministic filesystem callback changes state permissions."""
        runner = """import importlib.util, os, pathlib, stat, sys
spec = importlib.util.spec_from_file_location('harness_state_drift_install', sys.argv[1])
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
trigger, state, target = sys.argv[2], pathlib.Path(sys.argv[3]), pathlib.Path(sys.argv[4])
drifted = False
def change_state():
    global drifted
    if not drifted:
        state.chmod(stat.S_IMODE(state.stat().st_mode) | stat.S_IWGRP)
        drifted = True
original_rename = installer.rename
def rename(source, destination, point):
    try:
        result = original_rename(source, destination, point)
    except OSError:
        if trigger == point + ':fault':
            change_state()
        raise
    if point == trigger:
        change_state()
    return result
installer.rename = rename
original_discoverable = installer.discoverable
def discoverable(roots):
    result = original_discoverable(roots)
    if trigger == 'apply:post-rename-discovery' and (state / 'CURRENT').exists() and target.exists():
        change_state()
    return result
installer.discoverable = discoverable
original_write_json = installer.write_json
def write_json(path, value, *args, **kwargs):
    result = original_write_json(path, value, *args, **kwargs)
    if isinstance(value, dict):
        if trigger == 'apply:receipt' and value.get('receipt_format') == 1 and value.get('state') == 'installed':
            change_state()
        if trigger == 'rollback:receipt' and value.get('receipt_format') == 1 and value.get('state') == 'rolled back':
            change_state()
        if path.name == 'journal.json' and value.get('state') == 'committed' and trigger == value.get('operation') + ':committed':
            change_state()
    return result
installer.write_json = write_json
sys.exit(installer.main(sys.argv[5:]))
"""
        return subprocess.run([sys.executable, "-B", "-c", runner, str(self.installer), trigger,
                               str(self.state()), str(self.target), *map(str, arguments)], cwd=self.base,
                              env=self.environment(env), capture_output=True, text=True, encoding="utf-8", timeout=120)

    def run_recorded(self, command, *extra):
        """Run a recorded argument list as the operator would, without the test's -B or installer path."""
        return subprocess.run([*command, *extra], cwd=self.base, env=self.environment(), capture_output=True, text=True,
                              encoding="utf-8", timeout=120)

    def plan(self, runtime="claude", **kwargs):
        output = self.base / "plan.json"
        result = self.run_installer("plan", "--runtime", runtime, "--home", self.home, "--checksums", self.checksums,
                                    "--output", output, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        return output

    def apply(self, plan, *extra, env=None):
        return self.run_installer("apply", "--plan", plan, "--checksums", self.checksums, "--maintenance-confirmed",
                                  *extra, env=env)

    def installed(self, runtime="claude", *extra):
        result = self.apply(self.plan(runtime), *extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return Path(json.loads(result.stdout)["receipt"])

    @property
    def target(self):
        return self.home / ".claude" / "skills" / "github-workflow"

    @property
    def roots(self):
        return (self.home / ".claude" / "skills", self.home / ".agents" / "skills", self.home / ".codex" / "skills")

    def state(self):
        return self.home / ".claude" / "dev-harness-install"

    def v52_layout(self, target=None):
        target = target or self.target
        (target / "templates" / "history").mkdir(parents=True)
        (target / "scripts" / "__pycache__").mkdir(parents=True)
        (target / "SKILL.md").write_bytes(b"---\nname: github-workflow\ndescription: v5.2\n---\nOld body\n")
        (target / "scripts" / "merge-preflight.sh").write_bytes(b"#!/bin/sh\necho v5.2\n")
        (target / "scripts" / "merge-preflight.sh").chmod(0o755)
        (target / "scripts" / "__pycache__" / "helper.cpython-311.pyc").write_bytes(b"\x00stale cache")
        for name in RETIRED:
            (target / name).write_bytes(b"obsolete " + name.encode() + b"\n")
        (target / "templates" / "history" / "AGENTS-v5.1.md").write_bytes(b"authentic v5.1 snapshot\n")
        (target / "local-notes.md").write_bytes(b"PRIVATE-CUSTOMIZATION-MARKER\n")
        (target / "empty-custom-dir").mkdir()
        return snapshot(target)

    def assert_refused(self, result, code):
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stderr)["exit"], code)

    # Package verification.
    def test_installer_is_python_38_syntax(self):
        ast.parse((ROOT / "scripts/install.py").read_text(encoding="utf-8"), feature_version=(3, 8))

    def test_verify_package_directory_and_archive(self):
        directory = self.run_installer("verify-package", self.package, "--checksums", self.checksums)
        self.assertEqual(directory.returncode, 0, directory.stderr)
        report = json.loads(directory.stdout)
        self.assertEqual(report["archive_digest"], "not checked: directory input has no archive bytes")
        self.assertIn("not independent provenance", report["scope"])
        archive = self.run_installer("verify-package", self.archive, "--checksums", self.checksums)
        self.assertEqual(archive.returncode, 0, archive.stderr)
        self.assertEqual(json.loads(archive.stdout)["archive_digest"], "verified")

    def test_stored_and_deflated_archives_verify_like_the_directory(self):
        manifest = (self.package / "MANIFEST.json").read_bytes()
        for label, method in (("stored", zipfile.ZIP_STORED), ("deflated", zipfile.ZIP_DEFLATED)):
            with self.subTest(label):
                archive = self.base / f"{label}.zip"
                with zipfile.ZipFile(archive, "w") as bundle:
                    for path in sorted(self.package.rglob("*")):
                        if path.is_file():
                            name = self.package.name + "/" + path.relative_to(self.package).as_posix()
                            bundle.write(path, name, compress_type=method)
                checksums = self.base / f"{label}-SHA256SUMS.txt"
                checksums.write_text(
                    f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {self.package.name}-codex-claude.zip\n"
                    f"{hashlib.sha256(manifest).hexdigest()}  {self.package.name}-MANIFEST.json\n",
                    encoding="utf-8")
                verified = self.run_installer("verify-package", archive, "--checksums", checksums)
                directory = self.run_installer("verify-package", self.package, "--checksums", checksums)
                self.assertEqual(verified.returncode, 0, verified.stderr)
                self.assertEqual(directory.returncode, 0, directory.stderr)
                zip_report, directory_report = json.loads(verified.stdout), json.loads(directory.stdout)
                self.assertEqual(zip_report["archive_digest"], "verified")
                self.assertEqual(zip_report["files_verified"], directory_report["files_verified"])
                self.assertEqual(zip_report["manifest_sha256"], directory_report["manifest_sha256"])
                self.assertEqual(zip_report["checksums_sha256"], directory_report["checksums_sha256"])

    def test_verify_package_hashes_the_parsed_checksum_snapshot(self):
        spec = importlib.util.spec_from_file_location("harness_installer_for_snapshot", ROOT / "scripts/install.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        package = installer.load_package(self.package)
        checksums = self.base / "SHA256SUMS.txt"
        original = self.checksums.read_bytes()
        checksums.write_bytes(original)
        replacement = self.base / "replacement-SHA256SUMS.txt"
        replacement.write_bytes(original + b"\n")  # Same entries, different file identity and bytes.
        original_open = Path.open
        opens = []

        def replace_after_first_open(path, *args, **kwargs):
            if path == checksums:
                mode = kwargs.get("mode", args[0] if args else "r")
                opens.append(mode)
                if len(opens) == 1:
                    # Keep the first stream's bytes available while replacing its pathname. This
                    # models a replacement exactly between opens without a timing race or OS lock.
                    with original_open(path, "rb") as stream:
                        captured = stream.read()
                    replacement.replace(checksums)
                    return io.BytesIO(captured) if "b" in mode else io.StringIO(captured.decode("utf-8"))
            return original_open(path, *args, **kwargs)

        with mock.patch.object(Path, "open", replace_after_first_open):
            report = installer.verify_package(package, checksums)
        self.assertEqual(report["checksums_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(checksums.read_bytes(), original + b"\n")
        self.assertEqual(len(opens), 1)

    def test_checksum_capture_accepts_exact_budget_and_refuses_oversize_before_plan(self):
        limit = 128 * 1024
        original = self.checksums.read_bytes()
        self.assertLess(len(original), limit)
        checksums = self.base / "bounded-SHA256SUMS.txt"
        exact = original + b"\n" * (limit - len(original))
        checksums.write_bytes(exact)
        verified = self.run_installer("verify-package", self.package, "--checksums", checksums)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)["checksums_sha256"], hashlib.sha256(exact).hexdigest())

        checksums.write_bytes(exact + b"\xff")
        plan = self.base / "oversized-checksums-plan.json"
        refused = self.run_installer("plan", "--runtime", "claude", "--home", self.home,
                                     "--package", self.package, "--checksums", checksums, "--output", plan)
        self.assert_refused(refused, 1)
        self.assertIn("Checksums exceed", json.loads(refused.stderr)["error"])
        self.assertFalse(plan.exists())
        self.assertFalse(self.state().exists())
        self.assertFalse(self.target.exists())

    def test_verify_package_rejects_tampering(self):
        copy = self.base / "package"
        shutil.copytree(self.package, copy)
        (copy / "extra.md").write_text("extra\n", encoding="utf-8")
        self.assert_refused(self.run_installer("verify-package", copy, "--checksums", self.checksums), 1)
        (copy / "extra.md").unlink()
        (copy / "skills/github-workflow/SKILL.md").write_bytes(b"tampered")
        self.assert_refused(self.run_installer("verify-package", copy, "--checksums", self.checksums), 1)
        shutil.copy2(self.package / "skills/github-workflow/SKILL.md", copy / "skills/github-workflow/SKILL.md")
        (copy / "VERSION").unlink()
        self.assert_refused(self.run_installer("verify-package", copy, "--checksums", self.checksums), 1)
        sums = self.base / "sums.txt"
        sums.write_text(self.checksums.read_text(encoding="utf-8").replace("MANIFEST.json", "OTHER.json"), encoding="utf-8")
        self.assert_refused(self.run_installer("verify-package", self.package, "--checksums", sums), 1)
        archive = self.base / "tampered.zip"
        archive.write_bytes(self.archive.read_bytes() + b"\x00")
        self.assert_refused(self.run_installer("verify-package", archive, "--checksums", self.checksums), 1)

    def test_verify_package_rejects_malformed_checksum_bytes(self):
        first_line = self.checksums.read_bytes().splitlines(keepends=True)[0]
        for data, message in ((b"\xff", "Cannot read checksums"),
                              (b"not a checksum\n", "Malformed checksum line"),
                              (first_line + first_line, "Checksum listed twice")):
            with self.subTest(message=message):
                checksums = self.base / "invalid-SHA256SUMS.txt"
                checksums.write_bytes(data)
                result = self.run_installer("verify-package", self.package, "--checksums", checksums)
                self.assert_refused(result, 1)
                self.assertIn(message, json.loads(result.stderr)["error"])

    def test_verify_package_rejects_same_size_edit_and_bad_manifest_paths(self):
        copy = self.base / "package"
        shutil.copytree(self.package, copy)
        name = "skills/github-workflow/" + next(item for item in self.skill_files if item != "SKILL.md")
        data = bytearray((copy / name).read_bytes())
        data[0] ^= 1
        (copy / name).write_bytes(bytes(data))
        result = self.run_installer("verify-package", copy, "--checksums", self.checksums)
        self.assert_refused(result, 1)
        self.assertIn(f"hash or size mismatch: {name}", json.loads(result.stderr)["details"])
        shutil.copy2(self.package / name, copy / name)
        manifest = json.loads((copy / "MANIFEST.json").read_text(encoding="utf-8"))
        entry = manifest["files"]["skills/github-workflow/SKILL.md"]
        for added, problem in (("skills/github-workflow/Skill.md", "colliding path: skills/github-workflow/Skill.md"),
                               ("skills/../escape.md", "Unsafe package path: skills/../escape.md")):
            with self.subTest(added):
                changed = dict(manifest, files=dict(manifest["files"], **{added: entry}))
                (copy / "MANIFEST.json").write_text(json.dumps(changed), encoding="utf-8")
                result = self.run_installer("verify-package", copy, "--checksums", self.checksums)
                self.assert_refused(result, 1)
                self.assertIn(problem, json.loads(result.stderr)["details"])

    def test_verify_package_rejects_crafted_archives(self):
        prefix = self.package.name
        manifest = (self.package / "MANIFEST.json").read_bytes()
        link = zipfile.ZipInfo(prefix + "/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        cases = {
            "traversal": ([(prefix + "/MANIFEST.json", manifest), (prefix + "/../escape.md", b"x")], "Unsafe package path"),
            "symlink entry": ([(prefix + "/MANIFEST.json", manifest), (link, b"/etc/passwd")], "not a regular file"),
            "duplicate member": ([(prefix + "/MANIFEST.json", manifest), (prefix + "/MANIFEST.json", manifest)], "repeats a member name"),
            "two top-level directories": ([(prefix + "/MANIFEST.json", manifest), ("other/file.md", b"x")], "exactly one top-level"),
        }
        for label, (members, problem) in cases.items():
            with self.subTest(label):
                archive = self.base / (label.replace(" ", "-") + ".zip")
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    with zipfile.ZipFile(archive, "w") as bundle:
                        for member, data in members:
                            bundle.writestr(member, data)
                result = self.run_installer("verify-package", archive, "--checksums", self.checksums)
                self.assert_refused(result, 1)
                self.assertIn(problem, json.loads(result.stderr)["error"])

    def test_archive_metadata_limits_reject_empty_members_and_long_names(self):
        archive = self.base / "many-empty.zip"
        prefix = self.package.name
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr(prefix + "/MANIFEST.json", (self.package / "MANIFEST.json").read_bytes())
            for index in range(6):
                bundle.writestr(prefix + f"/empty-{index:02d}.txt", b"")
        for limits, phrase in (({"PACKAGE_ENTRY_LIMIT": 6}, "archive entry count exceeds"),
                               ({"PACKAGE_NAME_BYTES_LIMIT": 80}, "archive filename bytes exceed"),
                               ({"CENTRAL_DIRECTORY_LIMIT": 100}, "archive central directory exceeds")):
            with self.subTest(phrase):
                result = self.run_limited_installer(limits, "verify-package", archive, "--checksums", self.checksums)
                self.assert_refused(result, 1)
                self.assertIn(phrase, json.loads(result.stderr)["error"])

    def test_archive_preflight_rejects_lying_directory_metadata(self):
        archive = self.base / "metadata.zip"
        prefix = self.package.name
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr(prefix + "/MANIFEST.json", (self.package / "MANIFEST.json").read_bytes())
            bundle.writestr(prefix + "/extra.txt", b"")
        original = bytearray(archive.read_bytes())
        eocd = original.rfind(b"PK\x05\x06")
        self.assertGreaterEqual(eocd, 0)
        cases = (
            ("count", 10, "<H", 1, "archive central directory count differs"),
            ("size", 12, "<L", 1024 * 1024, "archive central directory exceeds"),
        )
        for label, offset, format_code, value, phrase in cases:
            with self.subTest(label):
                changed = bytearray(original)
                struct.pack_into(format_code, changed, eocd + offset, value)
                if label == "count":
                    struct.pack_into("<H", changed, eocd + 8, value)
                altered = self.base / f"metadata-{label}.zip"
                altered.write_bytes(changed)
                result = self.run_installer("verify-package", altered, "--checksums", self.checksums)
                self.assert_refused(result, 1)
                self.assertIn(phrase, json.loads(result.stderr)["error"])

    def test_zip64_locator_is_refused_before_zipfile_constructs_members(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            for index in range(1200):
                bundle.writestr(self.package.name + f"/empty-{index:04d}.txt", b"")
        normal = buffer.getvalue()
        eocd = normal.rfind(b"PK\x05\x06")
        self.assertGreaterEqual(eocd, 0)
        size, offset = struct.unpack_from("<2L", normal, eocd + 12)
        zip64_end = struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, 1200, 1200, size, offset)
        locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, eocd, 1)
        classic_end = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 0, 0, 0, 0, 0)
        source = self.base / "many-empty-zip64.zip"
        source.write_bytes(normal[:eocd] + zip64_end + locator + classic_end)
        with zipfile.ZipFile(source) as bundle:
            self.assertEqual(len(bundle.infolist()), 1200)
        spec = importlib.util.spec_from_file_location("harness_zip64_preflight", self.installer)
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("ZipFile constructed")):
            with self.assertRaisesRegex(installer.Refused, "ZIP64 central directory"):
                installer.load_archive(source)

    def test_archive_and_directory_share_entry_and_name_budgets(self):
        cases = (
            ("entries", (f"extra-{index:04d}/item.txt" for index in range(600)), "entry count exceeds"),
            ("names", (f"extra-{index:04d}-{'x' * 128}/item.txt" for index in range(470)),
             "filename bytes exceed"),
        )
        for label, names, phrase in cases:
            with self.subTest(label):
                directory = self.base / label / self.package.name
                shutil.copytree(self.package, directory)
                manifest = json.loads((directory / "MANIFEST.json").read_text(encoding="utf-8"))
                for name in names:
                    path = directory / name
                    path.parent.mkdir(parents=True)
                    path.write_bytes(b"x")
                    manifest["files"][name] = {"sha256": hashlib.sha256(b"x").hexdigest(), "bytes": 1}
                manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
                (directory / "MANIFEST.json").write_bytes(manifest_bytes)
                archive = self.base / f"parity-{label}.zip"
                with zipfile.ZipFile(archive, "w") as bundle:
                    for path in sorted(directory.rglob("*")):
                        if path.is_file():
                            bundle.writestr(self.package.name + "/" + path.relative_to(directory).as_posix(),
                                            path.read_bytes())
                checksums = self.base / f"parity-{label}-SHA256SUMS.txt"
                checksums.write_text(
                    f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {self.package.name}-codex-claude.zip\n"
                    f"{hashlib.sha256(manifest_bytes).hexdigest()}  {self.package.name}-MANIFEST.json\n",
                    encoding="utf-8")
                for package in (archive, directory):
                    result = self.run_installer("verify-package", package, "--checksums", checksums)
                    self.assert_refused(result, 1)
                    self.assertIn(phrase, json.loads(result.stderr)["error"])

    def test_small_archive_and_deep_path_reject_without_large_allocations(self):
        spec = importlib.util.spec_from_file_location("harness_allocation_limits", self.installer)
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        cases = (
            ("small", "file.txt", 64 * 1024 * 1024, "No MANIFEST.json"),
            ("deep", "a/" * 8000 + "file.txt", 1024 * 1024, "archive entry count exceeds"),
        )
        for label, name, limit, phrase in cases:
            with self.subTest(label):
                archive = self.base / f"{label}-allocation.zip"
                with zipfile.ZipFile(archive, "w") as bundle:
                    bundle.writestr(self.package.name + "/" + name, b"")
                with mock.patch.object(installer, "ARCHIVE_LIMIT", limit):
                    tracemalloc.start()
                    try:
                        with self.assertRaises(installer.Refused) as refusal:
                            installer.load_archive(archive)
                    finally:
                        _, peak = tracemalloc.get_traced_memory()
                        tracemalloc.stop()
                self.assertIn(phrase, str(refusal.exception))
                self.assertLess(peak, 16 * 1024 * 1024)

    def test_unsupported_zip_codecs_refuse_before_zipfile_and_large_allocations(self):
        spec = importlib.util.spec_from_file_location("harness_codec_preflight", ROOT / "scripts/install.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        for label, method in (("bzip2", zipfile.ZIP_BZIP2), ("lzma", zipfile.ZIP_LZMA)):
            archive = self.base / f"{label}-oversized.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(self.package.name + "/first.txt", b"small", compress_type=zipfile.ZIP_STORED)
                bundle.writestr(self.package.name + "/large.txt", b"x" * (8 * 1024 * 1024),
                                compress_type=method)
            # The central directory understates expansion. A bounded ZipExtFile.read() is not
            # enough for codecs that decompress the entire compressed block before slicing it.
            altered = bytearray(archive.read_bytes())
            second = altered.rfind(b"PK\x01\x02")
            self.assertGreaterEqual(second, 0)
            struct.pack_into("<L", altered, second + 24, 1024)
            archive.write_bytes(altered)
            with self.subTest(codec=label, phase="allocation"):
                with mock.patch.object(installer, "MEMBER_LIMIT", 1024), \
                     mock.patch.object(installer, "ARCHIVE_LIMIT", 1024 * 1024):
                    tracemalloc.start()
                    try:
                        with self.assertRaises(installer.Refused) as refusal:
                            installer.load_archive(archive)
                    finally:
                        _, peak = tracemalloc.get_traced_memory()
                        tracemalloc.stop()
                self.assertLess(peak, 4 * 1024 * 1024)
                self.assertIn("Unsupported ZIP compression method", str(refusal.exception))
            with self.subTest(codec=label, phase="preflight"):
                with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("ZipFile constructed")):
                    with self.assertRaisesRegex(installer.Refused, "Unsupported ZIP compression method"):
                        installer.load_archive(archive)

    def test_manifest_only_deep_paths_refuse_before_collision_expansion(self):
        spec = importlib.util.spec_from_file_location("harness_manifest_limits", self.installer)
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        package_name_bytes = sum(len(path.relative_to(self.package).as_posix().encode("utf-8"))
                                 for path in self.package.rglob("*"))
        cases = (
            ("valid", "a/" * 4000 + "missing.txt", "MANIFEST.json entry count exceeds", None),
            ("invalid", "a/" * 4000 + "bad?.txt", "Package paths must be portable relative POSIX paths", None),
            ("name-bytes", "missing-" + "n" * 80 + ".txt", "MANIFEST.json filename bytes exceed", package_name_bytes),
        )
        for label, name, phrase, name_budget in cases:
            with self.subTest(label):
                directory = self.base / f"manifest-{label}" / self.package.name
                shutil.copytree(self.package, directory)
                manifest = json.loads((directory / "MANIFEST.json").read_text(encoding="utf-8"))
                manifest["files"][name] = {"sha256": "0" * 64, "bytes": 0}
                (directory / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
                archive = self.base / f"manifest-{label}.zip"
                with zipfile.ZipFile(archive, "w") as bundle:
                    for path in sorted(directory.rglob("*")):
                        if path.is_file():
                            bundle.writestr(self.package.name + "/" + path.relative_to(directory).as_posix(),
                                            path.read_bytes())
                for package in (directory, archive):
                    with self.subTest(package="archive" if package == archive else "directory"):
                        with mock.patch.object(installer, "PACKAGE_NAME_BYTES_LIMIT",
                                               name_budget or installer.PACKAGE_NAME_BYTES_LIMIT):
                            with mock.patch.object(installer, "colliding", side_effect=AssertionError("collision expanded")):
                                with self.assertRaises(installer.Refused) as refusal:
                                    installer.verify_package(installer.load_package(package), self.checksums)
                        self.assertIn(phrase, str(refusal.exception) + str(refusal.exception.details))

    def test_manifest_size_and_file_count_are_bounded(self):
        manifest = (self.package / "MANIFEST.json").read_bytes()
        count = len(json.loads(manifest)["files"])
        for limits, phrase in (({"MANIFEST_LIMIT": len(manifest) - 1}, "MANIFEST.json exceeds"),
                               ({"MANIFEST_ENTRY_LIMIT": count - 1}, "MANIFEST.json file count exceeds")):
            with self.subTest(phrase):
                for package in (self.package, self.archive):
                    result = self.run_limited_installer(limits, "verify-package", package,
                                                        "--checksums", self.checksums)
                    self.assert_refused(result, 1)
                    self.assertIn(phrase, json.loads(result.stderr)["error"])

    def test_directory_limits_reject_empty_entries_names_and_aggregate_bytes_before_plan(self):
        copy = self.base / "many-empty-directory"
        shutil.copytree(self.package, copy)
        count = sum(1 for _ in copy.rglob("*"))
        for index in range(6):
            (copy / f"empty-{index:02d}.txt").write_bytes(b"")
        name_bytes = sum(len(path.relative_to(copy).as_posix().encode("utf-8")) for path in copy.rglob("*"))
        payloads = [path.stat().st_size for path in copy.rglob("*") if path.is_file()]
        aggregate = sum(payloads)
        self.assertGreater(aggregate, max(payloads))
        cases = (
            ({"PACKAGE_ENTRY_LIMIT": count + 2}, "package directory entry count exceeds"),
            ({"PACKAGE_NAME_BYTES_LIMIT": name_bytes - 1}, "package directory filename bytes exceed"),
            ({"ARCHIVE_LIMIT": aggregate - 1}, "package directory expands beyond"),
            ({"MEMBER_LIMIT": max(payloads) - 1}, "package directory member exceeds"),
        )
        for limits, phrase in cases:
            with self.subTest(phrase):
                plan = self.base / "rejected-plan.json"
                result = self.run_limited_installer(limits, "plan", "--runtime", "claude", "--home", self.home,
                                                    "--package", copy, "--checksums", self.checksums,
                                                    "--output", plan)
                self.assert_refused(result, 1)
                self.assertIn(phrase, json.loads(result.stderr)["error"])
                self.assertFalse(plan.exists())
                self.assertFalse(self.target.exists())

    # Planning.
    def test_plan_is_read_only_and_classifies_v52(self):
        before = self.v52_layout()
        home = snapshot(self.home)
        plan = json.loads(self.plan().read_text(encoding="utf-8"))
        self.assertEqual(snapshot(self.home), home)
        self.assertEqual(snapshot(self.target), before)
        classes = plan["classification"]
        for name in RETIRED:
            self.assertEqual(classes[name], "named obsolete")
        self.assertEqual(classes["scripts/__pycache__/helper.cpython-311.pyc"], "regenerable cache")
        self.assertEqual(classes["templates/history/AGENTS-v5.1.md"], "authentic history")
        self.assertEqual(classes["local-notes.md"], "customization")
        self.assertEqual(classes["SKILL.md"], "package")
        self.assertEqual(plan["before"]["SKILL.md"]["sha256"], hashlib.sha256((self.target / "SKILL.md").read_bytes()).hexdigest())
        self.assertEqual(plan["retirement_list"]["version"], 1)
        self.assertEqual(sorted(plan["retirement_list"]["paths"]), sorted(RETIRED))

    # Apply and rollback.
    def test_apply_replaces_v52_and_rollback_restores_exact_bytes(self):
        before = self.v52_layout()
        receipt_path = self.installed()
        self.assertEqual(len(visible_copies(*self.roots)), 1)
        after = snapshot(self.target)
        for name in self.skill_files:
            self.assertEqual(after[name][2], (self.package / "skills/github-workflow" / name).read_bytes(), name)
        for name in RETIRED + ["scripts/__pycache__", "scripts/__pycache__/helper.cpython-311.pyc"]:
            self.assertNotIn(name, after)
        self.assertEqual(after["local-notes.md"], before["local-notes.md"])
        self.assertEqual(after["templates/history/AGENTS-v5.1.md"], before["templates/history/AGENTS-v5.1.md"])
        self.assertEqual(after["empty-custom-dir"][0], "dir")
        packaged = {name for name in self.skill_files if name.startswith("templates/history/")}
        history = {name for name in after if name.startswith("templates/history/") and after[name][0] == "file"}
        self.assertEqual(history, packaged | {"templates/history/AGENTS-v5.1.md"})
        text = receipt_path.read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE-CUSTOMIZATION-MARKER", text)
        receipt = json.loads(text)
        self.assertFalse(receipt_path.resolve().is_relative_to(self.roots[0].resolve()))
        self.assertEqual(receipt["retired_paths"], sorted(RETIRED))
        self.assertIn("scripts/__pycache__/helper.cpython-311.pyc", receipt["dropped_paths"])
        self.assertEqual(receipt["preserved_paths"], ["local-notes.md", "templates/history/AGENTS-v5.1.md"])
        self.assertEqual(sorted(receipt["instruction_files"]), sorted(str(self.home / ".claude" / name) for name in ("AGENTS.md", "CLAUDE.md")))
        self.assertIn("limitations", receipt["maintenance_boundary"])
        self.assertIn("DEV_HARNESS_INSTALL_TEST_PROCESSES", receipt["test_hooks"])
        self.assertEqual(snapshot(Path(receipt["backup"])), before)
        self.assertEqual(snapshot(Path(receipt["retired"])), before)
        self.assertFalse((self.state() / "CURRENT").exists())
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertEqual(snapshot(self.target), before)
        self.assertEqual(len(visible_copies(*self.roots)), 1)
        self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8"))["state"], "rolled back")
        self.assert_refused(self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed"), 1)

    def test_rollback_uses_verified_backup_when_retired_copy_is_gone(self):
        before = self.v52_layout()
        receipt_path = self.installed()
        shutil.rmtree(json.loads(receipt_path.read_text(encoding="utf-8"))["retired"])
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertEqual(snapshot(self.target), before)

    def test_plan_and_apply_surface_each_retired_path_with_copies_and_rollback(self):
        # Retirement is by pathname (#43): an edited copy at a named path is retired too, so the plan, the
        # apply output and the receipt name it, and its exact bytes stay in both copies until rollback.
        self.v52_layout()
        edited = {"templates/README.md": b"operator edit\n", "templates/LICENSE-MIT": b"Copyright operator edit\n"}
        for name, data in edited.items():
            (self.target / name).write_bytes(data)
        # Unknown files beside or named like a listed path, and authentic history, are not retirements.
        kept = {"templates/README.md.orig": ("customization", b"unknown file beside a named path\n"),
                "notes/readme-guide.md": ("customization", b"listed file name in another folder\n"),
                "templates/history/AGENTS-v5.2.md": ("authentic history", b"authentic v5.2 snapshot\n")}
        (self.target / "notes").mkdir()
        for name, (_, data) in kept.items():
            (self.target / name).write_bytes(data)
        before = snapshot(self.target)
        plan_path = self.plan()
        self.assertEqual(snapshot(self.target), before)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual([entry["path"] for entry in plan["retirements"]], sorted(RETIRED))
        for entry in plan["retirements"]:
            data = before[entry["path"]][2]
            self.assertEqual(entry, {"path": entry["path"], "listed_as": "v5.2", "sha256": hashlib.sha256(data).hexdigest(),
                                     "bytes": len(data)})
        self.assertEqual(plan["retirement_copies"]["state_root"], str(self.state()))
        self.assertIsNone(plan["retirement_copies"]["transaction"])
        for name, (kind, _) in kept.items():
            self.assertEqual(plan["classification"][name], kind, name)

        result = self.apply(plan_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        receipt_path = Path(output["receipt"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(output["retirements"], receipt["retirements"])
        self.assertEqual([entry["path"] for entry in receipt["retirements"]], sorted(RETIRED))
        after = snapshot(self.target)
        transaction = Path(receipt["transaction"])
        for entry in receipt["retirements"]:
            self.assertNotIn(entry["path"], after)
            self.assertEqual({key: entry[key] for key in ("path", "listed_as", "sha256", "bytes")},
                             next(item for item in plan["retirements"] if item["path"] == entry["path"]))
            for key, copy in (("retired_copy", "retired"), ("backup", "backup")):
                location = Path(entry[key])
                self.assertEqual(location, transaction / copy / "github-workflow" / entry["path"])
                self.assertEqual(location.read_bytes(), before[entry["path"]][2], entry[key])
        located = {entry["path"]: entry for entry in receipt["retirements"]}
        for name, data in edited.items():
            self.assertEqual(Path(located[name]["retired_copy"]).read_bytes(), data)
            self.assertEqual(Path(located[name]["backup"]).read_bytes(), data)
        for name in kept:
            self.assertEqual(after[name], before[name], name)
            self.assertIn(name, output["preserved_paths"])

        command = output["rollback_command"]
        self.assertEqual(command, receipt["rollback_command"])
        self.assertEqual(command[2:], ["rollback", "--receipt", str(receipt_path)])
        self.assertTrue(os.path.samefile(command[0], sys.executable))
        # The installer runs from its verified copy in the transaction, not from the extracted package.
        packaged = (self.package / "install.py").read_bytes()
        self.assertEqual(Path(command[1]), transaction / "installer" / "install.py")
        self.assertEqual(Path(command[1]).read_bytes(), packaged)
        self.assertEqual(receipt["installer_copy"], {"path": command[1], "sha256": hashlib.sha256(packaged).hexdigest()})
        run = dict(cwd=self.base, env=self.environment(), capture_output=True, text=True, encoding="utf-8", timeout=120)
        # The recorded command leaves the maintenance confirmation to the operator.
        self.assert_refused(subprocess.run(command, **run), 2)
        self.assertEqual(snapshot(self.target), after)
        rollback = subprocess.run(command + ["--maintenance-confirmed"], **run)
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertEqual(snapshot(self.target), before)

    def test_rollback_accepts_a_receipt_without_retirement_details(self):
        # Receipts written by installer 1.0.0 have the same format number and neither new key.
        before = self.v52_layout()
        receipt_path = self.installed()
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        for key in ("retirements", "rollback_command", "installer_copy"):
            receipt.pop(key, None)
        receipt["installer_version"] = "1.0.0"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertEqual(snapshot(self.target), before)

    def test_recorded_rollback_command_outlives_the_extracted_package(self):
        # The operator runs the extracted package, deletes it, and may later extract another build under the
        # same folder name. The recorded command must still run the installer that made the receipt.
        download = self.base / "download"
        extracted = download / self.package.name
        shutil.copytree(self.package, extracted)
        installer = extracted / "install.py"
        before = self.v52_layout()
        plan = self.base / "plan.json"
        planned = self.run_installer("plan", "--runtime", "claude", "--home", self.home, "--checksums", self.checksums,
                                     "--output", plan, installer=installer)
        self.assertEqual(planned.returncode, 0, planned.stderr)
        applied = self.run_installer("apply", "--plan", plan, "--checksums", self.checksums, "--maintenance-confirmed",
                                     installer=installer)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        after = snapshot(self.target)
        command = json.loads(applied.stdout)["rollback_command"]
        shutil.rmtree(download)
        extracted.mkdir(parents=True)
        installer.write_text("import sys\nsys.exit(99)\n", encoding="utf-8")
        self.assert_refused(self.run_recorded(command), 2)
        self.assertEqual(snapshot(self.target), after)
        rollback = self.run_recorded(command, "--maintenance-confirmed")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertEqual(snapshot(self.target), before)
        receipt = json.loads(Path(command[4]).read_text(encoding="utf-8"))
        self.assertEqual(receipt["state"], "rolled back")

    def test_only_present_named_files_are_listed(self):
        # A fresh home has nothing to retire.
        plan = self.plan()
        self.assertEqual(json.loads(plan.read_text(encoding="utf-8"))["retirements"], [])
        result = self.apply(plan)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["retirements"], [])
        self.assertEqual(json.loads(Path(output["receipt"]).read_text(encoding="utf-8"))["retirements"], [])
        # A partial v5.2 layout lists only the named files it holds.
        shutil.rmtree(self.home)
        self.home.mkdir()
        self.v52_layout()
        present = ["templates/LICENSE-PolyForm-Noncommercial", "templates/README.md"]
        for name in RETIRED:
            if name not in present:
                (self.target / name).unlink()
        before = snapshot(self.target)
        plan = self.plan()
        self.assertEqual([entry["path"] for entry in json.loads(plan.read_text(encoding="utf-8"))["retirements"]], present)
        result = self.apply(plan)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        receipt = json.loads(Path(output["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(output["retirements"], receipt["retirements"])
        self.assertEqual([entry["path"] for entry in receipt["retirements"]], present)
        self.assertEqual(receipt["retired_paths"], present)
        for entry in receipt["retirements"]:
            for key in ("retired_copy", "backup"):
                self.assertTrue(Path(entry[key]).is_file(), entry[key])
                self.assertEqual(Path(entry[key]).read_bytes(), before[entry["path"]][2])

    def test_recover_after_a_crash_at_commit_names_the_receipt_and_its_retirements(self):
        # A kill after the commit and before the output: recover reports what apply could not print.
        before = self.v52_layout()
        crashed = self.apply(self.plan(), env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:committed"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        self.assertEqual(crashed.stdout, "")
        report = self.recover()
        self.assertEqual(report["result"], "already committed")
        receipt_path = Path(report["receipt"])
        self.assertEqual(receipt_path, Path(report["journal"]).parent / "receipt.json")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["state"], "installed")
        self.assertEqual(report["retirements"], receipt["retirements"])
        self.assertEqual([entry["path"] for entry in report["retirements"]], sorted(RETIRED))
        self.assertEqual(report["rollback_command"], receipt["rollback_command"])
        rollback = self.run_recorded(report["rollback_command"], "--maintenance-confirmed")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertEqual(snapshot(self.target), before)

    def test_new_installation_and_rollback_to_absence(self):
        self.assertEqual(len(visible_copies(*self.roots)), 0)
        receipt_path = self.installed()
        self.assertEqual(len(visible_copies(*self.roots)), 1)
        self.assertIsNone(json.loads(receipt_path.read_text(encoding="utf-8"))["backup"])
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertIsNone(snapshot(self.target))
        self.assertEqual(len(visible_copies(*self.roots)), 0)

    def test_rollback_refuses_after_later_edit(self):
        self.v52_layout()
        receipt_path = self.installed()
        (self.target / "SKILL.md").write_bytes(b"edited later\n")
        edited = snapshot(self.target)
        result = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assert_refused(result, 1)
        self.assertEqual([item["path"] for item in json.loads(result.stderr)["details"]], ["SKILL.md"])
        self.assertEqual(snapshot(self.target), edited)

    def test_apply_requires_integer_plan_format_before_installing(self):
        for label, plan_format in (("boolean", True), ("float", 1.0), ("integer", 1)):
            with self.subTest(plan_format=label):
                self.home = self.base / label
                self.home.mkdir()
                before = self.v52_layout()
                plan = self.plan()
                value = json.loads(plan.read_text(encoding="utf-8"))
                value["plan_format"] = plan_format
                plan.write_text(json.dumps(value), encoding="utf-8")
                saved_plan = plan.read_bytes()

                result = self.apply(plan)
                if label == "integer":
                    self.assertEqual(result.returncode, 0, result.stderr)
                    receipt = Path(json.loads(result.stdout)["receipt"])
                    self.assertTrue(receipt.is_file())
                    self.assertNotEqual(snapshot(self.target), before)
                    self.assertFalse((self.state() / "CURRENT").exists())
                else:
                    self.assert_refused(result, 1)
                    self.assertIn("Unsupported plan format", json.loads(result.stderr)["error"])
                    self.assertEqual(snapshot(self.target), before)
                    self.assertFalse(self.state().exists())
                self.assertEqual(plan.read_bytes(), saved_plan)

    def test_apply_refuses_malformed_operational_plan_as_json_before_lock(self):
        before = self.v52_layout()
        plan = self.plan()
        original = json.loads(plan.read_text(encoding="utf-8"))
        operative = (
            "installer_version", "runtime", "home", "config_root", "target", "skill_roots", "state_root",
            "package", "retirement_list", "before", "classification", "retirements", "retirement_copies",
            "duplicates", "duplicate_inventories",
        )
        malformed = [(f"missing {field}", field, "missing") for field in operative]
        wrong_type = {field: [] for field in operative}
        wrong_type.update(skill_roots={}, retirements={}, duplicates={})
        malformed.extend((f"wrong type {field}", field, wrong_type[field]) for field in operative)
        malformed.extend((f"null {field}", field, None) for field in operative if field != "before")
        malformed.extend([
            ("home has NUL", "home", str(self.home) + "\x00"),
            ("config is not normalized", "config_root", str(self.home / ".claude" / "..")),
            ("target has NUL", "target", str(self.target) + "\x00"),
            ("state has NUL", "state_root", str(self.state()) + "\x00"),
            ("skill root has NUL", "skill_roots", [str(self.roots[0]) + "\x00"]),
            ("duplicate has NUL", "duplicates", [str(self.roots[0] / "copy") + "\x00"]),
            ("duplicate inventory has NUL", "duplicate_inventories", {str(self.roots[0]) + "\x00": {}}),
            ("package source has NUL", "package", {**original["package"], "source": str(self.package) + "\x00"}),
            ("package source is not a path", "package", {**original["package"], "source": 42}),
            ("package archive field missing", "package",
             {key: item for key, item in original["package"].items() if key != "archive_sha256"}),
            ("before inventory path has NUL", "before",
             {**original["before"], "bad\x00": original["before"]["SKILL.md"]}),
            ("classification path has NUL", "classification",
             {**original["classification"], "bad\x00": "package"}),
            ("retirement copy root has NUL", "retirement_copies",
             {**original["retirement_copies"], "state_root": str(self.state()) + "\x00"}),
            ("retirement copy transaction field missing", "retirement_copies",
             {key: item for key, item in original["retirement_copies"].items() if key != "transaction"}),
        ])
        for field in original["package"]:
            malformed.append((f"package field missing {field}", "package",
                              {key: item for key, item in original["package"].items() if key != field}))
            malformed.append((f"package field wrong type {field}", "package",
                              {**original["package"], field: []}))
            if field != "archive_sha256":
                malformed.append((f"package field null {field}", "package",
                                  {**original["package"], field: None}))
        malformed.append(("package manifest hash is an integer", "package",
                          {**original["package"], "manifest_sha256": 42}))
        for label, field, replacement in malformed:
            with self.subTest(label=label):
                # Each candidate starts from the same disposable before-state, even on an unfixed installer
                # that wrongly opened state or applied a malformed plan in an earlier candidate.
                shutil.rmtree(self.state(), ignore_errors=True)
                shutil.rmtree(self.target, ignore_errors=True)
                before = self.v52_layout()
                value = json.loads(json.dumps(original))
                if replacement == "missing":
                    del value[field]
                else:
                    value[field] = replacement
                plan.write_text(json.dumps(value), encoding="utf-8")
                saved = plan.read_bytes()
                refused = self.apply(plan)
                self.assert_refused(refused, 1)
                self.assertIn("plan", json.loads(refused.stderr)["error"])
                self.assertEqual(snapshot(self.target), before)
                self.assertFalse(self.state().exists())
                self.assertFalse((self.state() / "LOCK").exists())
                self.assertFalse((self.state() / "CURRENT").exists())
                self.assertEqual(plan.read_bytes(), saved)

    @unittest.skipIf(os.name == "nt", "POSIX mode-bit authority; Windows DACL cases are exercised separately")
    def test_apply_refuses_untrusted_state_or_parent_without_creating_lock(self):
        for unsafe in ("state", "parent"):
            with self.subTest(unsafe=unsafe):
                self.home = self.base / unsafe
                self.home.mkdir()
                before = self.v52_layout()
                plan = self.plan()
                path = self.state() if unsafe == "state" else self.state().parent
                if unsafe == "state":
                    path.mkdir()
                path.chmod(stat.S_IMODE(path.stat().st_mode) | stat.S_IWGRP)
                mode = stat.S_IMODE(path.stat().st_mode)
                witness = self.home / "witness.txt"
                witness.write_bytes(b"outside the installer state\n")
                state_before = snapshot(self.state())
                result = self.apply(plan)
                self.assert_refused(result, 2)
                self.assertIn("not protected", json.loads(result.stderr)["error"])
                self.assertEqual(snapshot(self.target), before)
                self.assertEqual(snapshot(self.state()), state_before)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
                self.assertFalse((self.state() / "LOCK").exists())
                self.assertFalse((self.state() / "CURRENT").exists())
                self.assertEqual(witness.read_bytes(), b"outside the installer state\n")

    @unittest.skipIf(os.name == "nt", "relative POSIX link targets; Windows junction paths have native coverage")
    def test_apply_refuses_an_unsafe_intermediate_link_destination(self):
        for target_style in ("absolute", "relative"):
            with self.subTest(target_style=target_style):
                case = self.base / target_style
                safe, middle, real = (case / name for name in ("safe", "middle", "real"))
                safe.mkdir(parents=True)
                middle.mkdir()
                middle.chmod(0o777)
                real_home = real / "home"
                real_home.mkdir(parents=True)
                alias = safe / "home"
                first = str(middle / "hop") if target_style == "absolute" else "../middle/hop"
                second = str(real_home) if target_style == "absolute" else "../real/home"
                os.symlink(first, alias, target_is_directory=True)
                os.symlink(second, middle / "hop", target_is_directory=True)
                self.home = alias
                before = self.v52_layout()
                plan = self.plan()
                witness = middle / "witness.txt"
                witness.write_bytes(b"intermediate directory witness\n")
                state_before = snapshot(self.state())
                refused = self.apply(plan)
                self.assert_refused(refused, 2)
                self.assertIn("not protected", json.loads(refused.stderr)["error"])
                self.assertEqual(snapshot(self.target), before)
                self.assertEqual(snapshot(self.state()), state_before)
                self.assertFalse((self.state() / "LOCK").exists())
                self.assertEqual(witness.read_bytes(), b"intermediate directory witness\n")
                self.assertEqual(os.readlink(alias), first)
                self.assertEqual(os.readlink(middle / "hop"), second)
                middle.chmod(0o700)
                applied = self.apply(plan)
                self.assertEqual(applied.returncode, 0, applied.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX mode-bit authority; Windows DACL cases are exercised separately")
    def test_recover_refuses_untrusted_selector_or_journal_before_creating_lock(self):
        for unsafe in ("CURRENT", "journal.json"):
            with self.subTest(unsafe=unsafe):
                self.home = self.base / ("recover-" + unsafe)
                self.home.mkdir()
                before = self.v52_layout()
                plan = self.plan()
                crashed = self.apply(plan, env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:parked"})
                self.assertEqual(crashed.returncode, 70, crashed.stderr)
                state = self.state()
                (state / "LOCK").unlink()
                current = state / "CURRENT"
                journal = state / current.read_text(encoding="utf-8").strip()
                control = current if unsafe == "CURRENT" else journal
                control.chmod(stat.S_IMODE(control.stat().st_mode) | stat.S_IWGRP)
                target_before, state_before = snapshot(self.target), snapshot(state)
                witness = self.home / "witness.txt"
                witness.write_bytes(b"outside the installer state\n")
                result = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed")
                self.assert_refused(result, 2)
                self.assertEqual(snapshot(self.target), target_before)
                self.assertEqual(snapshot(state), state_before)
                self.assertFalse((state / "LOCK").exists())
                self.assertEqual(witness.read_bytes(), b"outside the installer state\n")
                control.chmod(stat.S_IMODE(control.stat().st_mode) & ~stat.S_IWGRP)
                recovered = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed")
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                self.assertEqual(json.loads(recovered.stdout)["result"], "restored")
                self.assertEqual(snapshot(self.target), before)

    @unittest.skipIf(os.name == "nt", "POSIX mode-bit authority; Windows DACL cases are exercised separately")
    def test_rollback_refuses_untrusted_canonical_receipt_before_creating_lock(self):
        before = self.v52_layout()
        receipt = self.installed()
        (self.state() / "LOCK").unlink()
        receipt.chmod(stat.S_IMODE(receipt.stat().st_mode) | stat.S_IWGRP)
        state_before, target_before = snapshot(self.state()), snapshot(self.target)
        witness = self.home / "witness.txt"
        witness.write_bytes(b"outside the installer state\n")
        result = self.run_installer("rollback", "--receipt", receipt, "--maintenance-confirmed")
        self.assert_refused(result, 2)
        self.assertEqual(snapshot(self.state()), state_before)
        self.assertEqual(snapshot(self.target), target_before)
        self.assertFalse((self.state() / "LOCK").exists())
        self.assertEqual(witness.read_bytes(), b"outside the installer state\n")
        receipt.chmod(stat.S_IMODE(receipt.stat().st_mode) & ~stat.S_IWGRP)
        restored = self.run_installer("rollback", "--receipt", receipt, "--maintenance-confirmed")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(snapshot(self.target), before)

    @unittest.skipUnless(sys.platform == "darwin", "requires a real macOS extended ACL")
    def test_apply_refuses_inheritable_mac_acl_before_creating_state(self):
        before = self.v52_layout()
        plan = self.plan()
        parent = self.state().parent
        parent.chmod(0o700)
        subprocess.run(["chmod", "+a", "everyone allow add_file,delete_child,file_inherit,directory_inherit",
                        str(parent)], check=True, capture_output=True)
        self.addCleanup(lambda: subprocess.run(["chmod", "-N", str(parent)], check=True, capture_output=True))
        acl_before = subprocess.run(["ls", "-lde", str(parent)], check=True, capture_output=True).stdout
        witness = self.home / "witness.txt"
        witness.write_bytes(b"outside the installer state\n")
        refused = self.apply(plan)
        self.assert_refused(refused, 2)
        self.assertEqual(snapshot(self.target), before)
        self.assertFalse(self.state().exists())
        self.assertEqual(subprocess.run(["ls", "-lde", str(parent)], check=True, capture_output=True).stdout, acl_before)
        self.assertEqual(witness.read_bytes(), b"outside the installer state\n")
        subprocess.run(["chmod", "-N", str(parent)], check=True, capture_output=True)
        installed = self.apply(plan)
        self.assertEqual(installed.returncode, 0, installed.stderr)

    @unittest.skipUnless(sys.platform == "darwin", "requires a real macOS extended ACL")
    def test_recover_refuses_mac_acl_on_journal_before_creating_lock(self):
        before = self.v52_layout()
        plan = self.plan()
        crashed = self.apply(plan, env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:parked"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        state = self.state()
        (state / "LOCK").unlink()
        journal = state / (state / "CURRENT").read_text(encoding="utf-8").strip()
        self.assertEqual(stat.S_IMODE(journal.stat().st_mode), 0o600)
        subprocess.run(["chmod", "+a", "everyone allow write,delete,writesecurity", str(journal)],
                       check=True, capture_output=True)
        self.addCleanup(lambda: subprocess.run(["chmod", "-N", str(journal)], check=True, capture_output=True))
        acl_before = subprocess.run(["ls", "-le", str(journal)], check=True, capture_output=True).stdout
        target_before, state_before = snapshot(self.target), snapshot(state)
        refused = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed")
        self.assert_refused(refused, 2)
        self.assertEqual(snapshot(self.target), target_before)
        self.assertEqual(snapshot(state), state_before)
        self.assertFalse((state / "LOCK").exists())
        self.assertEqual(subprocess.run(["ls", "-le", str(journal)], check=True, capture_output=True).stdout, acl_before)
        subprocess.run(["chmod", "-N", str(journal)], check=True, capture_output=True)
        recovered = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(snapshot(self.target), before)

    @unittest.skipUnless(sys.platform == "darwin", "requires a real macOS extended ACL")
    def test_mac_inherited_read_acl_allows_private_install(self):
        self.v52_layout()
        plan = self.plan()
        parent = self.state().parent
        parent.chmod(0o700)
        subprocess.run(["chmod", "+a", "everyone allow read,file_inherit,directory_inherit", str(parent)],
                       check=True, capture_output=True)
        self.addCleanup(lambda: subprocess.run(["chmod", "-N", str(parent)], check=True, capture_output=True))
        installed = self.apply(plan)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertTrue((self.state() / "LOCK").is_file())

    @unittest.skipUnless(os.name == "nt", "requires a real Windows DACL")
    def test_windows_accepts_native_volume_root_only_as_an_ancestor(self):
        spec = importlib.util.spec_from_file_location("harness_native_volume_owner", self.installer)
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        root = Path(self.base.anchor)
        # A second native implementation observes the owner without changing volume permissions.
        observed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-Acl -LiteralPath $env:HARNESS_NATIVE_VOLUME -ErrorAction Stop)"
             ".GetOwner([System.Security.Principal.SecurityIdentifier]).Value"],
            env={**self.environment(), "HARNESS_NATIVE_VOLUME": str(root)},
            check=True, capture_output=True, text=True, encoding="utf-8", timeout=30,
        ).stdout.strip()
        installer._windows_control_component(root, "ancestor")
        if observed == "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464":
            with self.assertRaisesRegex(installer.UntrustedState, "owner SID is not trusted for this role"):
                installer._windows_control_component(root, "container")

    @unittest.skipUnless(os.name == "nt", "requires a real Windows DACL")
    def test_windows_refuses_foreign_write_dacl_before_creating_lock(self):
        before = self.v52_layout()
        plan = self.plan()
        self.state().mkdir()
        witness = self.home / "witness.txt"
        witness.write_bytes(b"outside the installer state\n")
        subprocess.run(["icacls", str(self.state()), "/grant", "*S-1-1-0:(W)"], check=True, capture_output=True)
        acl_before = subprocess.run(["icacls", str(self.state())], check=True, capture_output=True).stdout
        state_before = snapshot(self.state())
        refused = self.apply(plan)
        self.assert_refused(refused, 2)
        self.assertIn(str(self.state()).casefold(), json.loads(refused.stderr)["error"].casefold())
        self.assertIn("another principal has a DACL mutation grant", json.loads(refused.stderr)["error"])
        self.assertEqual(snapshot(self.target), before)
        self.assertEqual(snapshot(self.state()), state_before)
        self.assertFalse((self.state() / "LOCK").exists())
        self.assertEqual(witness.read_bytes(), b"outside the installer state\n")
        self.assertEqual(subprocess.run(["icacls", str(self.state())], check=True, capture_output=True).stdout,
                         acl_before)

    @unittest.skipUnless(os.name == "nt", "requires a real Windows DACL")
    def test_windows_recover_refuses_delete_child_or_write_dac(self):
        for label, right in (("container", "DC"), ("CURRENT", "WDAC")):
            with self.subTest(right=right):
                self.home = self.base / label
                self.home.mkdir()
                before = self.v52_layout()
                plan = self.plan()
                crashed = self.apply(plan, env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:parked"})
                self.assertEqual(crashed.returncode, 70, crashed.stderr)
                state = self.state()
                (state / "LOCK").unlink()
                control = state if label == "container" else state / "CURRENT"
                witness = self.home / "witness.txt"
                witness.write_bytes(b"outside the installer state\n")
                subprocess.run(["icacls", str(control), "/grant", f"*S-1-1-0:({right})"],
                               check=True, capture_output=True)
                acl_before = subprocess.run(["icacls", str(control)], check=True, capture_output=True).stdout
                target_before, state_before = snapshot(self.target), snapshot(state)
                refused = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed")
                self.assert_refused(refused, 2)
                self.assertIn(str(control).casefold(), json.loads(refused.stderr)["error"].casefold())
                self.assertIn("another principal has a DACL mutation grant", json.loads(refused.stderr)["error"])
                self.assertEqual(snapshot(self.target), target_before)
                self.assertEqual(snapshot(state), state_before)
                self.assertFalse((state / "LOCK").exists())
                self.assertEqual(witness.read_bytes(), b"outside the installer state\n")
                self.assertEqual(subprocess.run(["icacls", str(control)], check=True, capture_output=True).stdout,
                                 acl_before)
                self.assertIsNone(target_before)
                self.assertIsNotNone(before)

    @unittest.skipUnless(os.name == "nt", "requires real Windows junctions and DACLs")
    def test_windows_refuses_unsafe_intermediate_junction_destination(self):
        case = self.base / "junction-chain"
        safe, middle, real = (case / name for name in ("safe", "middle", "real"))
        safe.mkdir(parents=True)
        middle.mkdir()
        real_home = real / "home"
        real_home.mkdir(parents=True)
        link_directory(middle / "hop", real_home)
        alias = safe / "home"
        link_directory(alias, middle / "hop")
        self.home = alias
        before = self.v52_layout()
        plan = self.plan()
        witness = middle / "witness.txt"
        witness.write_bytes(b"intermediate junction witness\n")
        subprocess.run(["icacls", str(middle), "/grant", "*S-1-1-0:(DC)"], check=True, capture_output=True)
        state_before = snapshot(self.state())
        refused = self.apply(plan)
        self.assert_refused(refused, 2)
        self.assertIn(str(middle).casefold(), json.loads(refused.stderr)["error"].casefold())
        self.assertIn("another principal has a DACL mutation grant", json.loads(refused.stderr)["error"])
        self.assertEqual(snapshot(self.target), before)
        self.assertEqual(snapshot(self.state()), state_before)
        self.assertFalse((self.state() / "LOCK").exists())
        self.assertEqual(witness.read_bytes(), b"intermediate junction witness\n")

    @unittest.skipUnless(os.name == "nt", "requires a real Windows DACL")
    def test_windows_safe_read_inheritance_allows_install_and_rollback(self):
        before = self.v52_layout()
        parent = self.state().parent
        subprocess.run(["icacls", str(parent), "/grant", "*S-1-1-0:(OI)(CI)(RX)"],
                       check=True, capture_output=True)
        receipt = self.installed()
        restored = self.run_installer("rollback", "--receipt", receipt, "--maintenance-confirmed")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(snapshot(self.target), before)

    @unittest.skipIf(os.name == "nt", "POSIX umask does not define Windows DACL creation")
    def test_new_control_metadata_is_private_even_with_permissive_umask(self):
        self.v52_layout()
        plan = self.plan()
        launcher = ("import os, runpy, sys; os.umask(0); "
                    "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')")
        applied = subprocess.run([sys.executable, "-B", "-c", launcher, str(self.installer), "apply", "--plan", str(plan),
                                  "--checksums", str(self.checksums), "--maintenance-confirmed"], cwd=self.base,
                                 env=self.environment(), capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        receipt = Path(json.loads(applied.stdout)["receipt"])
        transaction = receipt.parent
        for directory in (self.state(), transaction, transaction / "installer"):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700, directory)
        for control in (self.state() / "LOCK", receipt, transaction / "journal.json",
                        transaction / "installer" / "install.py"):
            self.assertEqual(stat.S_IMODE(control.stat().st_mode), 0o600, control)

    @unittest.skipIf(os.name == "nt", "POSIX mkdir race; Windows CreateDirectoryW needs native DACL coverage")
    def test_concurrent_private_state_creation_rechecks_existing_directory(self):
        runner = """import errno, importlib.util, os, pathlib, sys
spec = importlib.util.spec_from_file_location('harness_raced_install', sys.argv[1])
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
state, unsafe = pathlib.Path(sys.argv[2]), sys.argv[3] == 'unsafe'
original = os.mkdir
raced = False
def mkdir(path, *args, **kwargs):
    global raced
    if not raced and os.fspath(path) == os.fspath(state):
        raced = True
        original(path, *args, **kwargs)
        if unsafe:
            os.chmod(path, 0o777)
        raise FileExistsError(errno.EEXIST, 'created concurrently', os.fspath(path))
    return original(path, *args, **kwargs)
os.mkdir = mkdir
sys.exit(installer.main(sys.argv[4:]))
"""
        for kind in ("safe", "unsafe"):
            with self.subTest(kind=kind):
                self.home = self.base / ("concurrent-" + kind)
                self.home.mkdir()
                before = self.v52_layout()
                plan = self.plan()
                witness = self.home / "witness.txt"
                witness.write_bytes(b"outside installer state\n")
                result = subprocess.run([sys.executable, "-B", "-c", runner, str(self.installer), str(self.state()), kind,
                                         "apply", "--plan", str(plan), "--checksums", str(self.checksums),
                                         "--maintenance-confirmed"], cwd=self.base, env=self.environment(),
                                        capture_output=True, text=True, encoding="utf-8", timeout=120)
                if kind == "safe":
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(stat.S_IMODE(self.state().stat().st_mode), 0o700)
                    self.assertTrue((self.state() / "LOCK").is_file())
                    self.assertNotEqual(snapshot(self.target), before)
                else:
                    self.assert_refused(result, 2)
                    self.assertEqual(stat.S_IMODE(self.state().stat().st_mode), 0o777)
                    self.assertFalse((self.state() / "LOCK").exists())
                    self.assertFalse((self.state() / "CURRENT").exists())
                    self.assertEqual(snapshot(self.target), before)
                self.assertEqual(witness.read_bytes(), b"outside installer state\n")

    @unittest.skipIf(os.name == "nt", "legacy POSIX modes are separate from Windows DACLs")
    def test_legacy_public_read_control_modes_remain_usable(self):
        before = self.v52_layout()
        receipt = self.installed()
        transaction = receipt.parent
        self.state().chmod(0o755)
        transaction.chmod(0o755)
        for control in (self.state() / "LOCK", receipt, transaction / "journal.json"):
            control.chmod(0o644)
        rolled_back = self.run_installer("rollback", "--receipt", receipt, "--maintenance-confirmed")
        self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
        self.assertEqual(snapshot(self.target), before)

    def test_apply_refuses_plan_drift(self):
        self.v52_layout()
        plan = self.plan()
        (self.target / "local-notes.md").write_bytes(b"changed after planning\n")
        changed = snapshot(self.target)
        result = self.apply(plan)
        self.assert_refused(result, 1)
        self.assertIn("before", json.loads(result.stderr)["details"])
        self.assertEqual(snapshot(self.target), changed)
        self.assertFalse((self.state() / "CURRENT").exists())

    def test_apply_refuses_checksum_byte_drift_with_unchanged_entries(self):
        self.v52_layout()
        checksums = self.base / "SHA256SUMS.txt"
        original = self.checksums.read_bytes()
        checksums.write_bytes(original)
        plan = self.base / "plan.json"
        planned = self.run_installer("plan", "--runtime", "claude", "--home", self.home, "--checksums", checksums,
                                     "--package", self.package, "--output", plan)
        self.assertEqual(planned.returncode, 0, planned.stderr)
        self.assertEqual(json.loads(plan.read_text(encoding="utf-8"))["package"]["checksums_sha256"],
                         hashlib.sha256(original).hexdigest())
        before = snapshot(self.target)
        checksums.write_bytes(original + b"\n")
        applied = self.run_installer("apply", "--plan", plan, "--checksums", checksums, "--maintenance-confirmed")
        self.assert_refused(applied, 1)
        self.assertIn("package", json.loads(applied.stderr)["details"])
        self.assertEqual(snapshot(self.target), before)
        self.assertFalse((self.state() / "CURRENT").exists())

    def test_apply_refuses_a_changed_retirement_preview(self):
        # The operator approves the retirement preview in the saved plan (#43). Apply retires from a fresh
        # plan, so a preview that was edited or truncated after planning must stop it before any mutation.
        tampering = {
            "emptied retirements": ("retirements", lambda value: value.update(retirements=[])),
            "one retirement hidden": ("retirements", lambda value: value["retirements"].pop()),
            "retirements removed": ("retirements", lambda value: value.pop("retirements")),
            "copy location moved": ("retirement_copies",
                                    lambda value: value["retirement_copies"].update(state_root=str(self.base / "elsewhere"))),
            "copy location removed": ("retirement_copies", lambda value: value.pop("retirement_copies")),
            "retirement list emptied": ("retirement_list", lambda value: value.update(retirement_list=[])),
        }
        for label, (key, change) in tampering.items():
            with self.subTest(label):
                # Each case starts from the same layout, even after a case that was wrongly applied.
                for path in (self.target, self.state()):
                    shutil.rmtree(path, ignore_errors=True)
                before = self.v52_layout()
                plan = self.plan()
                value = json.loads(plan.read_text(encoding="utf-8"))
                change(value)
                plan.write_text(json.dumps(value), encoding="utf-8")
                result = self.apply(plan)
                self.assert_refused(result, 1)
                self.assertEqual(json.loads(result.stderr)["details"], [key])
                self.assertEqual(snapshot(self.target), before)
                self.assertFalse((self.state() / "CURRENT").exists())
                self.assertEqual(list(self.state().rglob("receipt.json")), [])

    def test_plan_from_the_previous_installer_version_is_drift(self):
        # A 1.0.0 plan does not show the retirement details, so it must be made again.
        before = self.v52_layout()
        plan = self.plan()
        value = json.loads(plan.read_text(encoding="utf-8"))
        for key in ("retirements", "retirement_copies"):
            value.pop(key, None)
        value["installer_version"] = "1.0.0"
        plan.write_text(json.dumps(value), encoding="utf-8")
        result = self.apply(plan)
        self.assert_refused(result, 1)
        self.assertEqual(json.loads(result.stderr)["details"], ["retirements", "retirement_copies"])
        self.assertEqual(snapshot(self.target), before)
        self.assertFalse(self.state().exists())
        # A complete old-version plan still reaches the value-drift comparison under the lock.
        value = json.loads(self.plan().read_text(encoding="utf-8"))
        value["installer_version"] = "1.0.0"
        plan.write_text(json.dumps(value), encoding="utf-8")
        result = self.apply(plan)
        self.assert_refused(result, 1)
        self.assertEqual(json.loads(result.stderr)["details"], ["installer_version"])
        self.assertEqual(snapshot(self.target), before)
        self.assertFalse((self.state() / "CURRENT").exists())

    # Swap failure injection and recovery.
    def test_rename_failures_restore_the_original(self):
        for fault in ("apply:park", "apply:activate:permission", "apply:activate"):
            with self.subTest(fault=fault):
                shutil.rmtree(self.home)
                self.home.mkdir()
                before = self.v52_layout()
                result = self.apply(self.plan(), env={"DEV_HARNESS_INSTALL_TEST_FAULT": fault})
                self.assert_refused(result, 1)
                self.assertIn("restored and verified", json.loads(result.stderr)["error"])
                self.assertEqual(snapshot(self.target), before)
                self.assertEqual(len(visible_copies(*self.roots)), 1)
                self.assertFalse((self.state() / "CURRENT").exists())

    def test_incomplete_restoration_is_reported_and_recoverable(self):
        before = self.v52_layout()
        result = self.apply(self.plan(), env={"DEV_HARNESS_INSTALL_TEST_FAULT": "apply:activate,apply:undo-park"})
        self.assert_refused(result, 3)
        self.assertIn("run `recover`", json.loads(result.stderr)["error"])
        self.assertIsNone(snapshot(self.target))
        self.assertTrue((self.state() / "CURRENT").exists())
        blocked = self.apply(self.plan())
        self.assert_refused(blocked, 2)
        recover = self.run_installer("recover", "--runtime", "claude", "--home", self.home, "--maintenance-confirmed")
        self.assertEqual(recover.returncode, 0, recover.stderr)
        self.assertEqual(snapshot(self.target), before)
        self.assertFalse((self.state() / "CURRENT").exists())

    @unittest.skipIf(os.name == "nt", "POSIX mode drift; Windows DACL trust is qualified natively")
    def test_apply_lost_state_protection_after_renames_reports_incomplete(self):
        for trigger in ("apply:park", "apply:post-rename-discovery", "apply:receipt", "apply:committed"):
            with self.subTest(trigger=trigger):
                self.home = self.base / trigger.replace(":", "-")
                self.home.mkdir()
                before = self.v52_layout()
                plan = self.plan()
                witness = self.home / "witness.txt"
                witness.write_bytes(b"outside recovery data\n")
                result = self.run_with_state_drift(trigger, "apply", "--plan", plan, "--checksums", self.checksums,
                                                   "--maintenance-confirmed")
                self.assert_refused(result, 3)
                report = json.loads(result.stderr)
                self.assertIn("CURRENT", report["error"])
                self.assertIn("recover", report["error"])
                current = self.state() / "CURRENT"
                self.assertTrue(current.is_file())
                journal = self.state() / current.read_text(encoding="utf-8").strip()
                transaction = journal.parent
                self.assertTrue((transaction / "backup" / "github-workflow").is_dir())
                self.assertTrue(stat.S_IMODE(self.state().stat().st_mode) & stat.S_IWGRP)
                self.assertEqual(witness.read_bytes(), b"outside recovery data\n")
                if trigger == "apply:park":
                    self.assertIsNone(snapshot(self.target))
                else:
                    self.assertNotEqual(snapshot(self.target), before)
                self.state().chmod(stat.S_IMODE(self.state().stat().st_mode) & ~stat.S_IWGRP)
                recovered = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed")
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                self.assertFalse(current.exists())
                if trigger == "apply:committed":
                    self.assertEqual(json.loads(recovered.stdout)["result"], "already committed")
                    self.assertNotEqual(snapshot(self.target), before)
                else:
                    self.assertEqual(json.loads(recovered.stdout)["result"], "restored")
                    self.assertEqual(snapshot(self.target), before)

    @unittest.skipIf(os.name == "nt", "POSIX mode drift; Windows DACL trust is qualified natively")
    def test_rollback_lost_state_protection_during_finalization_reports_incomplete(self):
        for trigger in ("rollback:committed", "rollback:receipt"):
            with self.subTest(trigger=trigger):
                self.home = self.base / trigger.replace(":", "-")
                self.home.mkdir()
                before = self.v52_layout()
                receipt = self.installed()
                result = self.run_with_state_drift(trigger, "rollback", "--receipt", receipt,
                                                   "--maintenance-confirmed")
                self.assert_refused(result, 3)
                self.assertIn("recover", json.loads(result.stderr)["error"])
                current = self.state() / "CURRENT"
                self.assertTrue(current.is_file())
                self.assertEqual(snapshot(self.target), before)
                self.assertTrue(stat.S_IMODE(self.state().stat().st_mode) & stat.S_IWGRP)
                self.state().chmod(stat.S_IMODE(self.state().stat().st_mode) & ~stat.S_IWGRP)
                recovered = self.run_installer("recover", "--receipt", receipt, "--maintenance-confirmed")
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                self.assertEqual(json.loads(recovered.stdout)["result"], "already committed")
                self.assertFalse(current.exists())
                self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["state"], "rolled back")
                self.assertEqual(snapshot(self.target), before)

    @unittest.skipIf(os.name == "nt", "POSIX mode drift; Windows DACL trust is qualified natively")
    def test_recover_lost_state_protection_after_restoration_reports_incomplete(self):
        before = self.v52_layout()
        plan = self.plan()
        crashed = self.apply(plan, env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:parked"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        current = self.state() / "CURRENT"
        self.assertTrue(current.is_file())
        result = self.run_with_state_drift("apply:undo-park", "recover", "--plan", plan,
                                           "--maintenance-confirmed")
        self.assert_refused(result, 3)
        self.assertIn("recover", json.loads(result.stderr)["error"])
        self.assertTrue(current.is_file())
        self.assertEqual(snapshot(self.target), before)
        self.assertTrue(stat.S_IMODE(self.state().stat().st_mode) & stat.S_IWGRP)
        self.state().chmod(stat.S_IMODE(self.state().stat().st_mode) & ~stat.S_IWGRP)
        recovered = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(recovered.stdout)["result"], "restored")
        self.assertFalse(current.exists())
        self.assertEqual(snapshot(self.target), before)

    @unittest.skipIf(os.name == "nt", "POSIX mode drift; Windows DACL trust is qualified natively")
    def test_journal_error_recording_does_not_mask_incomplete_restoration(self):
        before = self.v52_layout()
        plan = self.plan()
        result = self.run_with_state_drift("apply:undo-park:fault", "apply", "--plan", plan,
                                           "--checksums", self.checksums, "--maintenance-confirmed",
                                           env={"DEV_HARNESS_INSTALL_TEST_FAULT": "apply:activate,apply:undo-park"})
        self.assert_refused(result, 3)
        report = json.loads(result.stderr)
        self.assertIn("journal update refused", report["error"])
        self.assertIn("recover", report["error"])
        current = self.state() / "CURRENT"
        self.assertTrue(current.is_file())
        self.assertIsNone(snapshot(self.target))
        self.assertTrue(stat.S_IMODE(self.state().stat().st_mode) & stat.S_IWGRP)
        self.state().chmod(stat.S_IMODE(self.state().stat().st_mode) & ~stat.S_IWGRP)
        recovered = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertFalse(current.exists())
        self.assertEqual(snapshot(self.target), before)

    def checkpoints(self, *arguments):
        trace = self.base / "trace.txt"
        result = self.run_installer(*arguments, env={"DEV_HARNESS_INSTALL_TEST_TRACE": str(trace)})
        self.assertEqual(result.returncode, 0, result.stderr)
        names = trace.read_text(encoding="utf-8").split()
        trace.unlink()
        return names

    def test_interruption_at_every_apply_checkpoint_recovers_the_before_state(self):
        before = self.v52_layout()
        plan = self.plan()
        pristine = self.base / "pristine"
        shutil.copytree(self.home, pristine, symlinks=True)
        names = self.checkpoints("apply", "--plan", plan, "--checksums", self.checksums, "--maintenance-confirmed")
        for expected in ("apply:staged", "apply:prepared", "apply:parked", "apply:activated", "apply:committed"):
            self.assertIn(expected, names)
        for name in names:
            with self.subTest(checkpoint=name):
                shutil.rmtree(self.home)
                shutil.copytree(pristine, self.home, symlinks=True)
                crashed = self.apply(plan, env={"DEV_HARNESS_INSTALL_TEST_CRASH": name})
                self.assertEqual(crashed.returncode, 70, crashed.stderr)
                self.assertLessEqual(len(visible_copies(*self.roots)), 1)
                self.assertLessEqual(set(os.listdir(self.roots[0])), {"github-workflow"})
                self.assertTrue((self.state() / "CURRENT").exists())
                recover = self.run_installer("recover", "--runtime", "claude", "--home", self.home, "--maintenance-confirmed")
                self.assertEqual(recover.returncode, 0, recover.stderr)
                self.assertFalse((self.state() / "CURRENT").exists())
                if name == "apply:committed":
                    self.assertEqual(json.loads(recover.stdout)["result"], "already committed")
                    self.assertNotEqual(snapshot(self.target), before)
                else:
                    self.assertEqual(snapshot(self.target), before)
                self.assertEqual(len(visible_copies(*self.roots)), 1)

    def test_interruption_at_every_rollback_checkpoint_recovers(self):
        before = self.v52_layout()
        receipt_path = self.installed()
        after = snapshot(self.target)
        pristine = self.base / "pristine"
        shutil.copytree(self.home, pristine, symlinks=True)
        names = self.checkpoints("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertIn("rollback:activated", names)
        for name in names:
            with self.subTest(checkpoint=name):
                shutil.rmtree(self.home)
                shutil.copytree(pristine, self.home, symlinks=True)
                crashed = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed",
                                             env={"DEV_HARNESS_INSTALL_TEST_CRASH": name})
                self.assertEqual(crashed.returncode, 70, crashed.stderr)
                self.assertLessEqual(len(visible_copies(*self.roots)), 1)
                self.assertLessEqual(set(os.listdir(self.roots[0])), {"github-workflow"})
                recover = self.run_installer("recover", "--runtime", "claude", "--home", self.home, "--maintenance-confirmed")
                self.assertEqual(recover.returncode, 0, recover.stderr)
                state = json.loads(receipt_path.read_text(encoding="utf-8"))["state"]
                if name == "rollback:committed":
                    self.assertEqual(snapshot(self.target), before)
                    self.assertEqual(state, "rolled back")
                else:
                    self.assertEqual(snapshot(self.target), after)
                    self.assertEqual(state, "installed")
                self.assertEqual(len(visible_copies(*self.roots)), 1)

    def crash_after_parking(self):
        before = self.v52_layout()
        crashed = self.apply(self.plan(), env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:parked"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        self.assertIsNone(snapshot(self.target))
        parked = next(self.state().glob("*/retired/github-workflow"))
        return before, parked

    def recover(self, runtime="claude"):
        result = self.run_installer("recover", "--runtime", runtime, "--home", self.home, "--maintenance-confirmed")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_recover_rejects_untrusted_current_before_touching_any_journal(self):
        before, parked = self.crash_after_parking()
        current = self.state() / "CURRENT"
        original = current.read_bytes()
        journal = self.state() / original.decode("utf-8").strip()
        journal_bytes = journal.read_bytes()
        foreign = self.base / "foreign"
        foreign.mkdir()
        (foreign / "journal.json").write_bytes(journal_bytes)
        foreign_before = snapshot(foreign)
        pristine = self.base / "pristine-home"
        shutil.copytree(self.home, pristine)
        cases = (str(foreign / "journal.json").encode() + b"\n", b"../foreign/journal.json\n",
                 b"not-a-transaction/journal.json\n", original.rstrip(b"\n"), b"\xff\n", b"x" * 1024)
        for selection in cases:
            with self.subTest(selection=selection[:48]):
                shutil.rmtree(self.home)
                shutil.copytree(pristine, self.home)
                (foreign / "journal.json").write_bytes(journal_bytes)
                current.write_bytes(selection)
                refused = self.run_installer("recover", "--runtime", "claude", "--home", self.home,
                                             "--maintenance-confirmed")
                self.assert_refused(refused, 1)
                self.assertEqual(current.read_bytes(), selection)
                self.assertEqual(journal.read_bytes(), journal_bytes)
                self.assertEqual(snapshot(foreign), foreign_before)
                self.assertIsNone(snapshot(self.target))
                self.assertIsNotNone(snapshot(parked))
        shutil.rmtree(self.home)
        shutil.copytree(pristine, self.home)
        current.write_bytes(original)
        journal.unlink()
        uncertain = self.run_installer("recover", "--runtime", "claude", "--home", self.home,
                                       "--maintenance-confirmed")
        self.assert_refused(uncertain, 3)
        self.assertIn("journal", json.loads(uncertain.stderr)["error"].lower())
        self.assertEqual(current.read_bytes(), original)
        self.assertIsNone(snapshot(self.target))
        journal.write_bytes(journal_bytes)
        self.recover()
        self.assertEqual(snapshot(self.target), before)

    def test_recover_rejects_forged_journal_paths_and_state_before_a_write(self):
        self.crash_after_parking()
        current = self.state() / "CURRENT"
        journal = self.state() / current.read_text(encoding="utf-8").strip()
        original = json.loads(journal.read_text(encoding="utf-8"))
        foreign = self.base / "foreign"
        foreign.mkdir()
        (foreign / "marker").write_bytes(b"untouched\n")
        foreign_before = snapshot(foreign)
        parked_before = snapshot(Path(original["parked"]))
        pristine = self.base / "pristine-home"
        shutil.copytree(self.home, pristine)
        changes = (
            ("target", lambda value: value.__setitem__("target", str(foreign))),
            ("parked", lambda value: value.__setitem__("parked", str(foreign / "parked"))),
            ("backup", lambda value: value.__setitem__("origin_backup", str(foreign / "backup"))),
            ("incoming", lambda value: value.__setitem__("incoming_path", str(foreign / "incoming"))),
            ("moves type", lambda value: value.__setitem__("moves", "invalid")),
            ("origin type", lambda value: value.__setitem__("origin", "invalid")),
            ("false commit", lambda value: value.__setitem__("state", "committed")),
        )
        for label, change in changes:
            with self.subTest(field=label):
                shutil.rmtree(self.home)
                shutil.copytree(pristine, self.home)
                forged = json.loads(json.dumps(original))
                change(forged)
                journal.write_text(json.dumps(forged), encoding="utf-8")
                recorded = journal.read_bytes()
                refused = self.run_installer("recover", "--runtime", "claude", "--home", self.home,
                                             "--maintenance-confirmed")
                self.assert_refused(refused, 1)
                self.assertEqual(journal.read_bytes(), recorded)
                self.assertTrue(current.exists())
                self.assertEqual(snapshot(foreign), foreign_before)
                self.assertEqual(snapshot(Path(original["parked"])), parked_before)
                self.assertIsNone(snapshot(self.target))

    def test_rollback_rejects_forged_receipt_paths_before_creating_a_transaction(self):
        self.v52_layout()
        receipt_path = self.installed()
        original = json.loads(receipt_path.read_text(encoding="utf-8"))
        installed = snapshot(self.target)
        foreign = self.base / "foreign"
        foreign.mkdir()
        (foreign / "marker").write_bytes(b"untouched\n")
        foreign_before = snapshot(foreign)
        pristine = self.base / "pristine-home"
        shutil.copytree(self.home, pristine)
        changes = (
            ("home type", lambda value: value.__setitem__("home", [])),
            ("config root type", lambda value: value.__setitem__("config_root", {})),
            ("transaction", lambda value: value.__setitem__("transaction", str(foreign))),
            ("journal", lambda value: value.__setitem__("journal", str(foreign / "journal.json"))),
            ("backup", lambda value: value.__setitem__("backup", str(foreign / "backup"))),
            ("retired", lambda value: value.__setitem__("retired", str(foreign / "retired"))),
            ("duplicate path", lambda value: value.__setitem__("duplicates", [{"path": str(foreign / "duplicate"),
                                                                                "retired_to": str(foreign / "retired"),
                                                                                "backup": str(foreign / "backup"),
                                                                                "inventory": {}}])),
            ("retirement copy", lambda value: value["retirements"][0].__setitem__("retired_copy", str(foreign / "copy"))),
            ("duplicate schema", lambda value: value.__setitem__("duplicates", "invalid")),
        )
        for label, change in changes:
            with self.subTest(field=label):
                shutil.rmtree(self.home)
                shutil.copytree(pristine, self.home)
                forged = json.loads(json.dumps(original))
                change(forged)
                receipt_path.write_text(json.dumps(forged), encoding="utf-8")
                recorded = receipt_path.read_bytes()
                refused = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
                self.assert_refused(refused, 1)
                self.assertEqual(receipt_path.read_bytes(), recorded)
                self.assertEqual(snapshot(self.target), installed)
                self.assertEqual(snapshot(foreign), foreign_before)
                self.assertFalse((self.state() / "CURRENT").exists())
                self.assertFalse(list(self.state().glob("*/rollback-*")))

    def test_rollback_binds_operative_receipt_fields_to_its_committed_apply_journal(self):
        for variant in ("omitted_duplicate", "sibling_duplicate", "missing_before", "missing_journal", "uncommitted_journal"):
            with self.subTest(variant=variant):
                self.home = self.base / variant
                self.home.mkdir()
                duplicate = variant in ("omitted_duplicate", "sibling_duplicate")
                if duplicate:
                    target = self.home / ".agents" / "skills" / "github-workflow"
                    legacy = self.home / ".codex" / "skills" / "github-workflow"
                    self.v52_layout(target)
                    self.v52_layout(legacy)
                    installed = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
                    self.assertEqual(installed.returncode, 0, installed.stderr)
                    receipt_path = Path(json.loads(installed.stdout)["receipt"])
                else:
                    target = self.target
                    self.v52_layout()
                    receipt_path = self.installed()
                original = json.loads(receipt_path.read_text(encoding="utf-8"))
                receipt = json.loads(json.dumps(original))
                journal = Path(original["journal"])
                journal_bytes = journal.read_bytes()
                held_journal = journal.with_name("held-journal.json")
                observed = [target, Path(original["retired"]), Path(original["backup"])]
                for entry in original["duplicates"]:
                    observed.extend(Path(entry[key]) for key in ("path", "retired_to", "backup"))
                if variant == "omitted_duplicate":
                    receipt["duplicates"] = []
                elif variant == "sibling_duplicate":
                    sibling = legacy.parent / "sibling" / legacy.name
                    receipt["duplicates"][0]["path"] = str(sibling)
                    receipt["duplicates"][0]["resolved"] = os.path.realpath(str(sibling))
                    observed.append(sibling)
                elif variant == "missing_before":
                    receipt.update(before=None, backup=None, retired=None, retirements=[])
                elif variant == "missing_journal":
                    journal.rename(held_journal)
                else:
                    record = json.loads(journal_bytes)
                    record["state"] = "activated"
                    journal.write_text(json.dumps(record), encoding="utf-8")
                journal_after = journal.read_bytes() if journal.exists() else None
                receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
                receipt_bytes = receipt_path.read_bytes()
                trees_before = {str(path): snapshot(path) for path in observed}
                refused = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
                self.assert_refused(refused, 1)
                self.assertEqual({str(path): snapshot(path) for path in observed}, trees_before)
                self.assertEqual(receipt_path.read_bytes(), receipt_bytes)
                self.assertFalse((self.state() / "CURRENT").exists())
                self.assertFalse(list(receipt_path.parent.glob("rollback-*")))
                if variant == "missing_journal":
                    self.assertFalse(journal.exists())
                    self.assertEqual(held_journal.read_bytes(), journal_bytes)
                else:
                    self.assertEqual(journal.read_bytes(), journal_after)

    def test_recover_committed_rollback_rejects_foreign_receipt_before_marking_it(self):
        before = self.v52_layout()
        receipt_path = self.installed()
        crashed = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed",
                                     env={"DEV_HARNESS_INSTALL_TEST_CRASH": "rollback:committed"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        current = self.state() / "CURRENT"
        journal = self.state() / current.read_text(encoding="utf-8").strip()
        original = json.loads(journal.read_text(encoding="utf-8"))
        foreign = self.base / "foreign"
        foreign.mkdir()
        (foreign / "receipt.json").write_bytes(receipt_path.read_bytes())
        foreign_before = snapshot(foreign)
        pristine = self.base / "pristine-home"
        shutil.copytree(self.home, pristine)
        for field, replacement in (("receipt", foreign / "receipt.json"), ("journal", foreign / "journal.json"),
                                   ("parked", foreign / "parked")):
            with self.subTest(field=field):
                shutil.rmtree(self.home)
                shutil.copytree(pristine, self.home)
                forged = dict(original, **{field: str(replacement)})
                journal.write_text(json.dumps(forged), encoding="utf-8")
                recorded = journal.read_bytes()
                receipt_before = receipt_path.read_bytes()
                refused = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
                self.assert_refused(refused, 1)
                self.assertEqual(journal.read_bytes(), recorded)
                self.assertEqual(receipt_path.read_bytes(), receipt_before)
                self.assertEqual(snapshot(foreign), foreign_before)
                self.assertTrue(current.exists())
                self.assertEqual(snapshot(self.target), before)
        shutil.rmtree(self.home)
        shutil.copytree(pristine, self.home)
        recovered = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8"))["state"], "rolled back")

    def test_recover_refuses_malformed_journal_history_and_evidence_before_mutation(self):
        for label in ("history-map", "history-item", "evidence-list", "evidence-authority-paths",
                      "evidence-boundary-missing", "evidence-hooks-list"):
            with self.subTest(label=label):
                self.home = self.base / label
                self.home.mkdir()
                before = self.v52_layout()
                if label.startswith("history-"):
                    plan = self.plan()
                    crashed = self.apply(plan, env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:parked"})
                    receipt_path = None
                else:
                    receipt_path = self.installed()
                    crashed = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed",
                                                 env={"DEV_HARNESS_INSTALL_TEST_CRASH": "rollback:committed"})
                self.assertEqual(crashed.returncode, 70, crashed.stderr)
                current = self.state() / "CURRENT"
                current_bytes = current.read_bytes()
                journal = self.state() / current_bytes.decode("utf-8").strip()
                original = json.loads(journal.read_text(encoding="utf-8"))
                foreign = self.base / (label + "-foreign")
                foreign.mkdir()
                (foreign / "witness").write_bytes(b"outside recovery state\n")
                foreign_before = snapshot(foreign)
                if label == "history-map":
                    forged = {**original, "history": {}}
                elif label == "history-item":
                    forged = {**original, "history": [{}]}
                elif label == "evidence-list":
                    forged = {**original, "rollback_evidence": []}
                elif label == "evidence-authority-paths":
                    forged = {**original, "rollback_evidence": {
                        "journal": str(foreign / "journal.json"), "parked": str(foreign / "parked")}}
                elif label == "evidence-boundary-missing":
                    forged = {**original, "rollback_evidence": {
                        "maintenance_boundary": {}, "test_hooks": original["rollback_evidence"]["test_hooks"]}}
                else:
                    forged = {**original, "rollback_evidence": {
                        "maintenance_boundary": original["rollback_evidence"]["maintenance_boundary"],
                        "test_hooks": []}}
                journal.write_text(json.dumps(forged), encoding="utf-8")
                state_before = snapshot(self.state())
                target_before = snapshot(self.target)
                receipt_bytes = receipt_path.read_bytes() if receipt_path else None
                refused = self.run_installer("recover", "--runtime", "claude", "--home", self.home,
                                             "--maintenance-confirmed")
                self.assert_refused(refused, 1)
                self.assertEqual(snapshot(self.state()), state_before)
                self.assertEqual(snapshot(self.target), target_before)
                self.assertEqual(current.read_bytes(), current_bytes)
                self.assertEqual(journal.read_text(encoding="utf-8"), json.dumps(forged))
                if receipt_path:
                    self.assertEqual(receipt_path.read_bytes(), receipt_bytes)
                self.assertEqual(snapshot(foreign), foreign_before)

                # Missing historical audit fields remain recoverable; the current writer's evidence is retained.
                honest = dict(original)
                if label.startswith("history-"):
                    honest.pop("history")
                elif label != "evidence-authority-paths":
                    honest.pop("rollback_evidence")
                journal.write_text(json.dumps(honest), encoding="utf-8")
                recovered = self.run_installer("recover", "--runtime", "claude", "--home", self.home,
                                               "--maintenance-confirmed")
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                self.assertFalse(current.exists())
                self.assertEqual(snapshot(self.target), before)
                if receipt_path:
                    rollback = json.loads(receipt_path.read_text(encoding="utf-8"))["rollback"]
                    self.assertEqual(rollback["journal"], str(journal))
                    self.assertEqual(rollback["parked"], original["parked"])
                    self.assertEqual("maintenance_boundary" in rollback, label == "evidence-authority-paths")
                    self.assertEqual("test_hooks" in rollback, label == "evidence-authority-paths")

    def test_recover_rejects_a_linked_transaction_selector(self):
        if os.name != "nt" and not symlinks_supported():
            self.skipTest("symlinks unavailable")
        before, parked = self.crash_after_parking()
        current = self.state() / "CURRENT"
        selection = current.read_bytes()
        transaction = parked.parents[1]
        held = self.base / "held-transaction"
        transaction.rename(held)
        link_directory(transaction, held)
        journal_before = (held / "journal.json").read_bytes()
        refused = self.run_installer("recover", "--runtime", "claude", "--home", self.home,
                                     "--maintenance-confirmed")
        self.assert_refused(refused, 1)
        self.assertEqual(current.read_bytes(), selection)
        self.assertEqual((held / "journal.json").read_bytes(), journal_before)
        self.assertIsNone(snapshot(self.target))
        unlink_directory(transaction)
        held.rename(transaction)
        self.recover()
        self.assertEqual(snapshot(self.target), before)

    def test_rollback_rejects_a_linked_transaction_backup_component(self):
        if os.name != "nt" and not symlinks_supported():
            self.skipTest("symlinks unavailable")
        self.v52_layout()
        receipt_path = self.installed()
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        active = snapshot(self.target)
        backup_dir = Path(receipt["backup"]).parent
        held = self.base / "held-backup"
        backup_dir.rename(held)
        link_directory(backup_dir, held)
        foreign_before = snapshot(held)
        recorded = receipt_path.read_bytes()
        refused = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assert_refused(refused, 1)
        self.assertEqual(receipt_path.read_bytes(), recorded)
        self.assertEqual(snapshot(held), foreign_before)
        self.assertEqual(snapshot(self.target), active)
        self.assertFalse((self.state() / "CURRENT").exists())
        unlink_directory(backup_dir)
        held.rename(backup_dir)
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)

    def test_recover_rejects_a_linked_derived_restore_directory(self):
        if os.name != "nt" and not symlinks_supported():
            self.skipTest("symlinks unavailable")
        before, parked = self.crash_after_parking()
        current = self.state() / "CURRENT"
        journal = self.state() / current.read_text(encoding="utf-8").strip()
        journal_before = journal.read_bytes()
        shutil.rmtree(parked)
        foreign = self.base / "foreign-restore"
        foreign.mkdir()
        (foreign / "marker").write_bytes(b"untouched\n")
        foreign_before = snapshot(foreign)
        restore_dir = parked.parent / "restore"
        link_directory(restore_dir, foreign)
        refused = self.run_installer("recover", "--runtime", "claude", "--home", self.home,
                                     "--maintenance-confirmed")
        self.assert_refused(refused, 1)
        self.assertEqual(journal.read_bytes(), journal_before)
        self.assertEqual(snapshot(foreign), foreign_before)
        self.assertTrue(current.exists())
        self.assertIsNone(snapshot(self.target))
        unlink_directory(restore_dir)
        self.recover()
        self.assertEqual(snapshot(self.target), before)

    def test_duplicate_metadata_is_bound_to_the_retired_copy_and_skill_roots(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        self.v52_layout(target)
        self.v52_layout(legacy)
        plan = self.plan("codex")
        crashed = self.apply(plan, "--retire-duplicate", legacy,
                             env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:parked"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        state = self.home / ".agents" / "dev-harness-install"
        current = state / "CURRENT"
        journal = state / current.read_text(encoding="utf-8").strip()
        original = json.loads(journal.read_text(encoding="utf-8"))
        foreign = self.base / "foreign"
        foreign.mkdir()
        (foreign / "marker").write_bytes(b"untouched\n")
        foreign_before = snapshot(foreign)
        original_bytes = journal.read_bytes()
        for field in ("from", "to", "backup"):
            with self.subTest(field=field):
                forged = json.loads(json.dumps(original))
                forged["moves"][0][field] = str(foreign / field)
                journal.write_text(json.dumps(forged), encoding="utf-8")
                recorded = journal.read_bytes()
                refused = self.run_installer("recover", "--runtime", "codex", "--home", self.home,
                                             "--maintenance-confirmed")
                self.assert_refused(refused, 1)
                self.assertEqual(journal.read_bytes(), recorded)
                self.assertEqual(snapshot(foreign), foreign_before)
                self.assertEqual(current.read_text(encoding="utf-8").strip(), str(journal.relative_to(state)))
        journal.write_bytes(original_bytes)
        self.recover("codex")

        installed = self.apply(plan, "--retire-duplicate", legacy)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        receipt_path = Path(json.loads(installed.stdout)["receipt"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        active = snapshot(target)
        receipt_bytes = receipt_path.read_bytes()
        for field in ("path", "retired_to", "backup"):
            with self.subTest(receipt_field=field):
                forged = json.loads(json.dumps(receipt))
                forged["duplicates"][0][field] = str(foreign / field)
                receipt_path.write_text(json.dumps(forged), encoding="utf-8")
                recorded = receipt_path.read_bytes()
                refused = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
                self.assert_refused(refused, 1)
                self.assertEqual(receipt_path.read_bytes(), recorded)
                self.assertEqual(snapshot(target), active)
                self.assertEqual(snapshot(foreign), foreign_before)
                self.assertFalse((state / "CURRENT").exists())
        receipt_path.write_bytes(receipt_bytes)

    def test_recover_refuses_a_duplicate_root_retargeted_after_retirement(self):
        if os.name != "nt" and not symlinks_supported():
            self.skipTest("symlinks unavailable")
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        before, legacy_before = self.v52_layout(target), self.v52_layout(legacy)
        crashed = self.apply(self.plan("codex"), "--retire-duplicate", legacy,
                             env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:parked"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        state = self.home / ".agents" / "dev-harness-install"
        current = state / "CURRENT"
        current_bytes = current.read_bytes()
        journal = state / current_bytes.decode("utf-8").strip()
        journal_bytes = journal.read_bytes()
        move = json.loads(journal_bytes)["moves"][0]
        parked = Path(json.loads(journal_bytes)["parked"])
        parked_before, moved_before = snapshot(parked), snapshot(Path(move["to"]))
        root = legacy.parent
        held = self.base / "held-skills"
        foreign = self.base / "foreign-skills"
        foreign.mkdir()
        (foreign / "marker").write_bytes(b"untouched\n")
        shutil.move(str(root), str(held))
        link_directory(root, foreign)
        foreign_before = snapshot(foreign)
        try:
            refused = self.run_installer("recover", "--runtime", "codex", "--home", self.home,
                                         "--maintenance-confirmed")
            self.assert_refused(refused, 2)
            self.assertEqual(current.read_bytes(), current_bytes)
            self.assertEqual(journal.read_bytes(), journal_bytes)
            self.assertEqual(snapshot(parked), parked_before)
            self.assertEqual(snapshot(Path(move["to"])), moved_before)
            self.assertEqual(snapshot(foreign), foreign_before)
            self.assertIsNone(snapshot(target))
        finally:
            unlink_directory(root)
            shutil.move(str(held), str(root))
        self.recover("codex")
        self.assertEqual((snapshot(target), snapshot(legacy)), (before, legacy_before))

    def test_recover_accepts_an_unchanged_linked_duplicate_root(self):
        if os.name != "nt" and not symlinks_supported():
            self.skipTest("symlinks unavailable")
        target = self.home / ".agents" / "skills" / "github-workflow"
        physical_root = self.base / "dotfiles" / "skills"
        legacy_source = physical_root / "github-workflow"
        before, legacy_before = self.v52_layout(target), self.v52_layout(legacy_source)
        (self.home / ".codex").mkdir()
        link_directory(self.home / ".codex" / "skills", physical_root)
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        try:
            crashed = self.apply(self.plan("codex"), "--retire-duplicate", legacy,
                                 env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:parked"})
            self.assertEqual(crashed.returncode, 70, crashed.stderr)
            self.recover("codex")
            self.assertEqual((snapshot(target), snapshot(legacy_source)), (before, legacy_before))
        finally:
            unlink_directory(self.home / ".codex" / "skills")

    def test_recover_refuses_a_rollback_duplicate_root_retargeted_after_restoration(self):
        if os.name != "nt" and not symlinks_supported():
            self.skipTest("symlinks unavailable")
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        before, legacy_before = self.v52_layout(target), self.v52_layout(legacy)
        installed = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        receipt_path = Path(json.loads(installed.stdout)["receipt"])
        active = snapshot(target)
        crashed = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed",
                                     env={"DEV_HARNESS_INSTALL_TEST_CRASH": "rollback:moved-0"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        state = self.home / ".agents" / "dev-harness-install"
        current = state / "CURRENT"
        current_bytes = current.read_bytes()
        journal = state / current_bytes.decode("utf-8").strip()
        journal_bytes = journal.read_bytes()
        receipt_bytes = receipt_path.read_bytes()
        self.assertEqual((snapshot(target), snapshot(legacy)), (before, legacy_before))
        root = legacy.parent
        held = self.base / "held-skills"
        foreign = self.base / "foreign-skills"
        foreign.mkdir()
        shutil.copytree(legacy, foreign / legacy.name, symlinks=True)
        (foreign / "marker").write_bytes(b"untouched\n")
        shutil.move(str(root), str(held))
        link_directory(root, foreign)
        foreign_before, held_before = snapshot(foreign), snapshot(held)
        try:
            refused = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
            self.assert_refused(refused, 2)
            self.assertEqual(current.read_bytes(), current_bytes)
            self.assertEqual(journal.read_bytes(), journal_bytes)
            self.assertEqual(receipt_path.read_bytes(), receipt_bytes)
            self.assertEqual((snapshot(foreign), snapshot(held)), (foreign_before, held_before))
            self.assertEqual(snapshot(target), before)
        finally:
            unlink_directory(root)
            shutil.move(str(held), str(root))
        recovered = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual((snapshot(target), snapshot(legacy)), (active, None))

    def test_recover_rollback_accepts_an_unchanged_linked_duplicate_root(self):
        if os.name != "nt" and not symlinks_supported():
            self.skipTest("symlinks unavailable")
        target = self.home / ".agents" / "skills" / "github-workflow"
        physical_root = self.base / "dotfiles" / "skills"
        legacy_source = physical_root / "github-workflow"
        self.v52_layout(target)
        self.v52_layout(legacy_source)
        (self.home / ".codex").mkdir()
        root = self.home / ".codex" / "skills"
        link_directory(root, physical_root)
        try:
            legacy = root / "github-workflow"
            installed = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            receipt_path = Path(json.loads(installed.stdout)["receipt"])
            active = snapshot(target)
            crashed = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed",
                                         env={"DEV_HARNESS_INSTALL_TEST_CRASH": "rollback:moved-0"})
            self.assertEqual(crashed.returncode, 70, crashed.stderr)
            recovered = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertEqual((snapshot(target), snapshot(legacy_source)), (active, None))
        finally:
            unlink_directory(root)

    def test_recover_rollback_binds_changed_receipt_resolution_to_the_apply_journal(self):
        if os.name != "nt" and not symlinks_supported():
            self.skipTest("symlinks unavailable")
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        before, legacy_before = self.v52_layout(target), self.v52_layout(legacy)
        installed = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        receipt_path = Path(json.loads(installed.stdout)["receipt"])
        active = snapshot(target)
        crashed = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed",
                                     env={"DEV_HARNESS_INSTALL_TEST_CRASH": "rollback:moved-0"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        self.assertEqual((snapshot(target), snapshot(legacy)), (before, legacy_before))
        state = self.home / ".agents" / "dev-harness-install"
        current = state / "CURRENT"
        current_bytes = current.read_bytes()
        journal = state / current_bytes.decode("utf-8").strip()
        journal_bytes = journal.read_bytes()
        receipt_bytes = receipt_path.read_bytes()
        record = json.loads(journal_bytes)
        parked = Path(record["parked"])
        parked_before = snapshot(parked)
        retired_duplicate = Path(json.loads(receipt_bytes)["duplicates"][0]["retired_to"])
        retired_before = snapshot(retired_duplicate)
        root = legacy.parent
        held = self.base / "held-skills"
        foreign = self.base / "foreign-skills"
        foreign.mkdir()
        shutil.copytree(legacy, foreign / legacy.name, symlinks=True)
        self.assertEqual(snapshot(foreign / legacy.name), legacy_before)
        (foreign / "marker").write_bytes(b"untouched\n")
        shutil.move(str(root), str(held))
        link_directory(root, foreign)
        receipt = json.loads(receipt_bytes)
        receipt["duplicates"][0]["resolved"] = os.path.realpath(str(legacy))
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        forged_bytes = receipt_path.read_bytes()
        foreign_before, held_before = snapshot(foreign), snapshot(held)
        try:
            refused = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
            self.assert_refused(refused, 1)
            self.assertEqual((current.read_bytes(), journal.read_bytes(), receipt_path.read_bytes()),
                             (current_bytes, journal_bytes, forged_bytes))
            self.assertEqual((snapshot(target), snapshot(parked), snapshot(retired_duplicate)),
                             (before, parked_before, retired_before))
            self.assertEqual((snapshot(foreign), snapshot(held)), (foreign_before, held_before))
        finally:
            receipt_path.write_bytes(receipt_bytes)
            unlink_directory(root)
            shutil.move(str(held), str(root))
        recovered = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual((snapshot(target), snapshot(legacy)), (active, None))
        self.assertEqual(snapshot(foreign), foreign_before)
        self.assertFalse(current.exists())

    def test_recover_rollback_rejects_receipt_duplicate_and_before_divergence(self):
        for variant in ("omitted_duplicate", "sibling_duplicate", "changed_before"):
            with self.subTest(variant=variant):
                self.home = self.base / variant
                self.home.mkdir()
                target = self.home / ".agents" / "skills" / "github-workflow"
                legacy = self.home / ".codex" / "skills" / "github-workflow"
                self.v52_layout(target)
                self.v52_layout(legacy)
                installed = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
                self.assertEqual(installed.returncode, 0, installed.stderr)
                receipt_path = Path(json.loads(installed.stdout)["receipt"])
                active = snapshot(target)
                crashed = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed",
                                             env={"DEV_HARNESS_INSTALL_TEST_CRASH": "rollback:moved-0"})
                self.assertEqual(crashed.returncode, 70, crashed.stderr)
                state = self.home / ".agents" / "dev-harness-install"
                current = state / "CURRENT"
                current_bytes = current.read_bytes()
                journal = state / current_bytes.decode("utf-8").strip()
                journal_bytes = journal.read_bytes()
                record = json.loads(journal_bytes)
                receipt_bytes = receipt_path.read_bytes()
                receipt = json.loads(receipt_bytes)
                sibling = legacy.parent / "sibling" / legacy.name
                if variant == "omitted_duplicate":
                    receipt["duplicates"] = []
                elif variant == "sibling_duplicate":
                    receipt["duplicates"][0]["path"] = str(sibling)
                    receipt["duplicates"][0]["resolved"] = os.path.realpath(str(sibling))
                else:
                    receipt["before"]["SKILL.md"]["sha256"] = "0" * 64
                receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
                forged_bytes = receipt_path.read_bytes()
                observed = (target, legacy, sibling, Path(record["parked"]),
                            Path(json.loads(receipt_bytes)["duplicates"][0]["retired_to"]))
                trees_before = tuple(snapshot(path) for path in observed)
                refused = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
                self.assert_refused(refused, 1)
                self.assertEqual((current.read_bytes(), journal.read_bytes(), receipt_path.read_bytes()),
                                 (current_bytes, journal_bytes, forged_bytes))
                self.assertEqual(tuple(snapshot(path) for path in observed), trees_before)
                receipt_path.write_bytes(receipt_bytes)
                recovered = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                self.assertEqual((snapshot(target), snapshot(legacy)), (active, None))
                self.assertFalse(current.exists())

    def test_recover_accepts_a_committed_rollback_receipt_with_a_retired_duplicate(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        before, legacy_before = self.v52_layout(target), self.v52_layout(legacy)
        installed = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        receipt_path = Path(json.loads(installed.stdout)["receipt"])
        rolled_back = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        journal = Path(receipt["rollback"]["journal"])
        journal_bytes, receipt_bytes = journal.read_bytes(), receipt_path.read_bytes()
        current = self.home / ".agents" / "dev-harness-install" / "CURRENT"
        current.write_bytes((str(journal.relative_to(current.parent)) + "\n").encode("utf-8"))
        recovered = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(recovered.stdout)["result"], "already committed")
        self.assertEqual((snapshot(target), snapshot(legacy)), (before, legacy_before))
        self.assertEqual(journal.read_bytes(), journal_bytes)
        self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8")), json.loads(receipt_bytes))
        self.assertFalse(current.exists())

    def test_recover_rollback_refuses_a_forged_duplicate_through_an_in_root_link(self):
        if os.name != "nt" and not symlinks_supported():
            self.skipTest("symlinks unavailable")
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        self.v52_layout(target)
        self.v52_layout(legacy)
        installed = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        receipt_path = Path(json.loads(installed.stdout)["receipt"])
        active = snapshot(target)
        crashed = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed",
                                     env={"DEV_HARNESS_INSTALL_TEST_CRASH": "rollback:moved-0"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        before_target = snapshot(target)
        state = self.home / ".agents" / "dev-harness-install"
        current = state / "CURRENT"
        current_bytes = current.read_bytes()
        journal = state / current_bytes.decode("utf-8").strip()
        original_journal = journal.read_bytes()
        original_receipt = receipt_path.read_bytes()
        foreign = self.base / "foreign-skills"
        foreign.mkdir()
        shutil.copytree(legacy, foreign / legacy.name, symlinks=True)
        alias = legacy.parent / "alias"
        link_directory(alias, foreign)
        try:
            forged = alias / "github-workflow"
            receipt = json.loads(original_receipt)
            receipt["duplicates"][0]["path"] = str(forged)
            receipt["duplicates"][0]["resolved"] = os.path.realpath(str(forged))
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            journal_record = json.loads(original_journal)
            journal_record["moves"][0]["to"] = str(forged)
            journal.write_text(json.dumps(journal_record), encoding="utf-8")
            journal_bytes, receipt_bytes = journal.read_bytes(), receipt_path.read_bytes()
            foreign_before = snapshot(foreign)
            refused = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
            self.assert_refused(refused, 1)
            self.assertEqual(current.read_bytes(), current_bytes)
            self.assertEqual((journal.read_bytes(), receipt_path.read_bytes()), (journal_bytes, receipt_bytes))
            self.assertEqual(snapshot(foreign), foreign_before)
            self.assertEqual(snapshot(target), before_target)
        finally:
            journal.write_bytes(original_journal)
            receipt_path.write_bytes(original_receipt)
            unlink_directory(alias)
        recovered = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual((snapshot(target), snapshot(legacy)), (active, None))

    def test_recover_refuses_a_parked_tree_without_a_recorded_origin(self):
        crashed = self.apply(self.plan(), env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:staged"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        current = self.state() / "CURRENT"
        current_bytes = current.read_bytes()
        journal = self.state() / current_bytes.decode("utf-8").strip()
        journal_bytes = journal.read_bytes()
        record = json.loads(journal_bytes)
        self.assertIsNone(record["origin"])
        self.assertIsNone(record["origin_backup"])
        parked = Path(record["parked"])
        parked.mkdir(parents=True)
        (parked / "marker").write_bytes(b"unrecorded origin\n")
        parked_before = snapshot(parked)
        staged_before = snapshot(Path(record["incoming_path"]))
        refused = self.run_installer("recover", "--runtime", "claude", "--home", self.home,
                                     "--maintenance-confirmed")
        self.assert_refused(refused, 1)
        self.assertEqual(current.read_bytes(), current_bytes)
        self.assertEqual(journal.read_bytes(), journal_bytes)
        self.assertEqual(snapshot(parked), parked_before)
        self.assertEqual(snapshot(Path(record["incoming_path"])), staged_before)
        self.assertIsNone(snapshot(self.target))
        shutil.rmtree(parked)
        self.recover()
        self.assertIsNone(snapshot(self.target))

    def test_recover_refuses_a_committed_new_install_with_an_unrecorded_parked_tree(self):
        crashed = self.apply(self.plan(), env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:committed"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        current = self.state() / "CURRENT"
        current_bytes = current.read_bytes()
        journal = self.state() / current_bytes.decode("utf-8").strip()
        journal_bytes = journal.read_bytes()
        record = json.loads(journal_bytes)
        self.assertIsNone(record["origin"])
        parked = Path(record["parked"])
        parked.mkdir(parents=True)
        (parked / "marker").write_bytes(b"unrecorded origin\n")
        parked_before, active_before = snapshot(parked), snapshot(self.target)
        receipt = journal.parent / "receipt.json"
        receipt_bytes = receipt.read_bytes()
        refused = self.run_installer("recover", "--runtime", "claude", "--home", self.home,
                                     "--maintenance-confirmed")
        self.assert_refused(refused, 1)
        self.assertEqual(current.read_bytes(), current_bytes)
        self.assertEqual(journal.read_bytes(), journal_bytes)
        self.assertEqual(receipt.read_bytes(), receipt_bytes)
        self.assertEqual((snapshot(parked), snapshot(self.target)), (parked_before, active_before))
        shutil.rmtree(parked)
        self.assertEqual(self.recover()["result"], "already committed")

    def test_recover_committed_apply_binds_receipt_to_journal(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        self.v52_layout(target)
        self.v52_layout(legacy)
        crashed = self.apply(self.plan("codex"), "--retire-duplicate", legacy,
                             env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:committed"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        state = self.home / ".agents" / "dev-harness-install"
        current = state / "CURRENT"
        current_bytes = current.read_bytes()
        journal = state / current_bytes.decode("utf-8").strip()
        journal_bytes = journal.read_bytes()
        receipt_path = journal.parent / "receipt.json"
        original = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt_bytes = receipt_path.read_bytes()
        active, retired = snapshot(target), snapshot(Path(original["duplicates"][0]["retired_to"]))
        changes = (
            ("before", lambda value: value["before"]["SKILL.md"].__setitem__("sha256", "0" * 64)),
            ("after", lambda value: value["after"]["SKILL.md"].__setitem__("sha256", "0" * 64)),
            ("duplicate inventory", lambda value: value["duplicates"][0]["inventory"]["SKILL.md"].__setitem__("sha256", "0" * 64)),
            ("duplicate path", lambda value: value["duplicates"][0].__setitem__("path", str(legacy.parent / "vendor" / "github-workflow"))),
            ("duplicate resolved", lambda value: value["duplicates"][0].__setitem__("resolved", str(self.base / "foreign"))),
            ("missing duplicate", lambda value: value.__setitem__("duplicates", [])),
        )
        for label, change in changes:
            with self.subTest(field=label):
                forged = json.loads(json.dumps(original))
                change(forged)
                receipt_path.write_text(json.dumps(forged), encoding="utf-8")
                forged_bytes = receipt_path.read_bytes()
                try:
                    refused = self.run_installer("recover", "--runtime", "codex", "--home", self.home,
                                                 "--maintenance-confirmed")
                    self.assert_refused(refused, 1)
                    self.assertEqual(current.read_bytes(), current_bytes)
                    self.assertEqual(journal.read_bytes(), journal_bytes)
                    self.assertEqual(receipt_path.read_bytes(), forged_bytes)
                    self.assertEqual((snapshot(target), snapshot(Path(original["duplicates"][0]["retired_to"]))),
                                     (active, retired))
                finally:
                    # Keep the same transaction and directories for every variant, even on a broken installer.
                    if not current.exists():
                        current.write_bytes(current_bytes)
                    receipt_path.write_bytes(receipt_bytes)
        self.assertEqual(self.recover("codex")["result"], "already committed")

    def test_recover_committed_apply_accepts_legacy_duplicate_without_resolution(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        self.v52_layout(target)
        self.v52_layout(legacy)
        crashed = self.apply(self.plan("codex"), "--retire-duplicate", legacy,
                             env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:committed"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        state = self.home / ".agents" / "dev-harness-install"
        current = state / "CURRENT"
        journal = state / current.read_text(encoding="utf-8").strip()
        receipt_path = journal.parent / "receipt.json"
        record = json.loads(journal.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        del record["moves"][0]["resolved"]
        del receipt["duplicates"][0]["resolved"]
        journal.write_text(json.dumps(record), encoding="utf-8")
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        self.assertEqual(self.recover("codex")["result"], "already committed")
        self.assertFalse(current.exists())

    def test_recover_validates_a_precommit_apply_receipt_before_undo(self):
        before = self.v52_layout()
        crashed = self.apply(self.plan(), env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:committed"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        current = self.state() / "CURRENT"
        current_bytes = current.read_bytes()
        journal = self.state() / current_bytes.decode("utf-8").strip()
        record = json.loads(journal.read_text(encoding="utf-8"))
        # A receipt is written immediately before the committed journal state.
        record["state"] = "activated"
        journal.write_text(json.dumps(record), encoding="utf-8")
        journal_bytes = journal.read_bytes()
        receipt_path = journal.parent / "receipt.json"
        original = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt_bytes = receipt_path.read_bytes()
        parked_before, active = snapshot(Path(record["parked"])), snapshot(self.target)
        changes = (
            ("transaction", lambda value: value.__setitem__("transaction", str(self.base / "foreign"))),
            ("after", lambda value: value["after"]["SKILL.md"].__setitem__("sha256", "0" * 64)),
        )
        for label, change in changes:
            with self.subTest(field=label):
                forged = json.loads(json.dumps(original))
                change(forged)
                receipt_path.write_text(json.dumps(forged), encoding="utf-8")
                forged_bytes = receipt_path.read_bytes()
                try:
                    refused = self.run_installer("recover", "--runtime", "claude", "--home", self.home,
                                                 "--maintenance-confirmed")
                    self.assert_refused(refused, 1)
                    self.assertEqual(current.read_bytes(), current_bytes)
                    self.assertEqual(journal.read_bytes(), journal_bytes)
                    self.assertEqual(receipt_path.read_bytes(), forged_bytes)
                    self.assertEqual((snapshot(Path(record["parked"])), snapshot(self.target)), (parked_before, active))
                finally:
                    if not current.exists():
                        current.write_bytes(current_bytes)
                    journal.write_bytes(journal_bytes)
                    receipt_path.write_bytes(receipt_bytes)
        self.assertEqual(self.recover()["result"], "restored")
        self.assertEqual(snapshot(self.target), before)
        self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8"))["state"],
                         "recovered to the before-state; not installed")
        self.assertFalse(current.exists())

    def test_recover_validates_rollback_origin_against_its_receipt_before_undo(self):
        self.v52_layout()
        receipt_path = self.installed()
        active = snapshot(self.target)
        crashed = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed",
                                     env={"DEV_HARNESS_INSTALL_TEST_CRASH": "rollback:parked"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        current = self.state() / "CURRENT"
        current_bytes = current.read_bytes()
        journal = self.state() / current_bytes.decode("utf-8").strip()
        journal_bytes = journal.read_bytes()
        record = json.loads(journal_bytes)
        parked_before = snapshot(Path(record["parked"]))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["after"]["SKILL.md"]["sha256"] = "0" * 64
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        forged_bytes = receipt_path.read_bytes()
        refused = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assert_refused(refused, 1)
        self.assertEqual(current.read_bytes(), current_bytes)
        self.assertEqual(journal.read_bytes(), journal_bytes)
        self.assertEqual(receipt_path.read_bytes(), forged_bytes)
        self.assertEqual(snapshot(Path(record["parked"])), parked_before)
        self.assertIsNone(snapshot(self.target))
        receipt["after"] = record["origin"]
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        recovered = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(snapshot(self.target), active)
        self.assertFalse(current.exists())

    def test_recover_binds_rollback_move_inventory_to_receipt_before_undo_or_finish(self):
        for checkpoint in ("rollback:moved-0", "rollback:committed"):
            with self.subTest(checkpoint=checkpoint):
                self.home = self.base / checkpoint.replace(":", "-")
                self.home.mkdir()
                target = self.home / ".agents" / "skills" / "github-workflow"
                legacy = self.home / ".codex" / "skills" / "github-workflow"
                before, legacy_before = self.v52_layout(target), self.v52_layout(legacy)
                installed = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
                self.assertEqual(installed.returncode, 0, installed.stderr)
                receipt_path = Path(json.loads(installed.stdout)["receipt"])
                active = snapshot(target)
                crashed = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed",
                                             env={"DEV_HARNESS_INSTALL_TEST_CRASH": checkpoint})
                self.assertEqual(crashed.returncode, 70, crashed.stderr)
                state = self.home / ".agents" / "dev-harness-install"
                current = state / "CURRENT"
                current_bytes = current.read_bytes()
                journal = state / current_bytes.decode("utf-8").strip()
                original_journal = journal.read_bytes()
                receipt_bytes = receipt_path.read_bytes()
                original_skill = (legacy / "SKILL.md").read_bytes()
                changed = b"---\nname: github-workflow\n---\nDifferent duplicate bytes\n"
                (legacy / "SKILL.md").write_bytes(changed)
                record = json.loads(original_journal)
                entry = record["moves"][0]["inventory"]["SKILL.md"]
                entry.update(sha256=hashlib.sha256(changed).hexdigest(), bytes=len(changed))
                journal.write_text(json.dumps(record), encoding="utf-8")
                forged_bytes = journal.read_bytes()
                target_before, legacy_changed = snapshot(target), snapshot(legacy)
                refused = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
                self.assert_refused(refused, 1)
                self.assertEqual(current.read_bytes(), current_bytes)
                self.assertEqual((journal.read_bytes(), receipt_path.read_bytes()), (forged_bytes, receipt_bytes))
                self.assertEqual((snapshot(target), snapshot(legacy)), (target_before, legacy_changed))
                journal.write_bytes(original_journal)
                (legacy / "SKILL.md").write_bytes(original_skill)
                recovered = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                expected = (before, legacy_before) if checkpoint == "rollback:committed" else (active, None)
                self.assertEqual((snapshot(target), snapshot(legacy)), expected)
                self.assertFalse(current.exists())

    def test_recover_binds_receipt_identity_to_both_journal_kinds(self):
        for operation in ("apply", "rollback"):
            with self.subTest(operation=operation):
                self.home = self.base / operation
                self.home.mkdir()
                self.v52_layout()
                if operation == "apply":
                    crashed = self.apply(self.plan(), env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:committed"})
                else:
                    receipt_path = self.installed()
                    crashed = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed",
                                                 env={"DEV_HARNESS_INSTALL_TEST_CRASH": "rollback:parked"})
                self.assertEqual(crashed.returncode, 70, crashed.stderr)
                current = self.state() / "CURRENT"
                current_bytes = current.read_bytes()
                journal = self.state() / current_bytes.decode("utf-8").strip()
                journal_bytes = journal.read_bytes()
                record = json.loads(journal_bytes)
                if operation == "apply":
                    receipt_path = journal.parent / "receipt.json"
                receipt_bytes = receipt_path.read_bytes()
                foreign = receipt_path.parent.parent / "20000101T000000Z-deadbeef"
                forged = relocated_transaction(json.loads(receipt_bytes), receipt_path.parent, foreign)
                receipt_path.write_text(json.dumps(forged), encoding="utf-8")
                forged_bytes = receipt_path.read_bytes()
                target_before, parked_before = snapshot(self.target), snapshot(Path(record["parked"]))
                refused = self.run_installer("recover", "--runtime", "claude", "--home", self.home,
                                             "--maintenance-confirmed")
                self.assert_refused(refused, 1)
                self.assertEqual(current.read_bytes(), current_bytes)
                self.assertEqual((journal.read_bytes(), receipt_path.read_bytes()), (journal_bytes, forged_bytes))
                self.assertEqual((snapshot(self.target), snapshot(Path(record["parked"]))),
                                 (target_before, parked_before))
                receipt_path.write_bytes(receipt_bytes)
                recovered = self.run_installer("recover", "--runtime", "claude", "--home", self.home,
                                               "--maintenance-confirmed")
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                self.assertFalse(current.exists())

    def test_recover_rejects_an_unrecorded_rollback_move_backup(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        self.v52_layout(target)
        self.v52_layout(legacy)
        installed = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        receipt_path = Path(json.loads(installed.stdout)["receipt"])
        active = snapshot(target)
        crashed = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed",
                                     env={"DEV_HARNESS_INSTALL_TEST_CRASH": "rollback:moved-0"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        state = self.home / ".agents" / "dev-harness-install"
        current = state / "CURRENT"
        current_bytes = current.read_bytes()
        journal = state / current_bytes.decode("utf-8").strip()
        original_journal = journal.read_bytes()
        receipt_bytes = receipt_path.read_bytes()
        record = json.loads(original_journal)
        record["moves"][0]["backup"] = str(self.base / "foreign-backup")
        journal.write_text(json.dumps(record), encoding="utf-8")
        forged_bytes = journal.read_bytes()
        target_before, legacy_before = snapshot(target), snapshot(legacy)
        refused = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assert_refused(refused, 1)
        self.assertEqual(current.read_bytes(), current_bytes)
        self.assertEqual((journal.read_bytes(), receipt_path.read_bytes()), (forged_bytes, receipt_bytes))
        self.assertEqual((snapshot(target), snapshot(legacy)), (target_before, legacy_before))
        journal.write_bytes(original_journal)
        recovered = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual((snapshot(target), snapshot(legacy)), (active, None))

    def test_recover_refuses_a_missing_origin_backup_without_parked_drift(self):
        for operation in ("apply", "rollback"):
            with self.subTest(operation=operation):
                self.home = self.base / operation
                self.home.mkdir()
                expected = self.v52_layout()
                if operation == "apply":
                    crashed = self.apply(self.plan(), env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:parked"})
                else:
                    receipt_path = self.installed()
                    expected = snapshot(self.target)
                    crashed = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed",
                                                 env={"DEV_HARNESS_INSTALL_TEST_CRASH": "rollback:parked"})
                self.assertEqual(crashed.returncode, 70, crashed.stderr)
                current = self.state() / "CURRENT"
                current_bytes = current.read_bytes()
                journal = self.state() / current_bytes.decode("utf-8").strip()
                original_journal = journal.read_bytes()
                record = json.loads(original_journal)
                self.assertIsNotNone(record["origin"])
                self.assertIsNotNone(record["origin_backup"])
                parked_before = snapshot(Path(record["parked"]))
                record["origin_backup"] = None
                journal.write_text(json.dumps(record), encoding="utf-8")
                forged_bytes = journal.read_bytes()
                refused = self.run_installer("recover", "--runtime", "claude", "--home", self.home,
                                             "--maintenance-confirmed")
                self.assert_refused(refused, 1)
                self.assertEqual(current.read_bytes(), current_bytes)
                self.assertEqual(journal.read_bytes(), forged_bytes)
                self.assertEqual(snapshot(Path(record["parked"])), parked_before)
                self.assertIsNone(snapshot(self.target))
                journal.write_bytes(original_journal)
                recovered = self.run_installer("recover", "--runtime", "claude", "--home", self.home,
                                               "--maintenance-confirmed")
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                self.assertEqual(snapshot(self.target), expected)
                self.assertFalse(current.exists())

    def test_recover_accepts_a_rollback_origin_changed_after_parking(self):
        self.v52_layout()
        receipt_path = self.installed()
        crashed = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed",
                                     env={"DEV_HARNESS_INSTALL_TEST_CRASH": "rollback:parked"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        current = self.state() / "CURRENT"
        journal = self.state() / current.read_text(encoding="utf-8").strip()
        record = json.loads(journal.read_text(encoding="utf-8"))
        parked = Path(record["parked"])
        changed = b"---\nname: github-workflow\n---\nChanged after parking\n"
        (parked / "SKILL.md").write_bytes(changed)
        record["planned_origin"] = record["origin"]
        record["origin"] = json.loads(json.dumps(record["origin"]))
        record["origin"]["SKILL.md"].update(sha256=hashlib.sha256(changed).hexdigest(), bytes=len(changed))
        record["origin_backup"] = None
        record["state"] = "parked-drift"
        journal.write_text(json.dumps(record), encoding="utf-8")
        parked_before = snapshot(parked)
        restored = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(snapshot(self.target), parked_before)
        self.assertFalse(parked.exists())
        self.assertEqual((self.target / "SKILL.md").read_bytes(), changed)
        self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8"))["state"], "installed")
        self.assertFalse(current.exists())

    def test_recover_accepts_a_rollback_receipt_already_marked_after_commit(self):
        before = self.v52_layout()
        receipt_path = self.installed()
        rolled_back = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        journal = Path(receipt["rollback"]["journal"])
        current = self.state() / "CURRENT"
        current.write_bytes((str(journal.relative_to(self.state())) + "\n").encode("utf-8"))
        recovered = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(recovered.stdout)["result"], "already committed")
        self.assertEqual(snapshot(self.target), before)
        self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8"))["state"], "rolled back")
        self.assertFalse(current.exists())

    def test_rollback_rejects_a_forged_duplicate_through_an_in_root_link(self):
        if os.name != "nt" and not symlinks_supported():
            self.skipTest("symlinks unavailable")
        receipt_path = self.installed("codex")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        active = snapshot(self.home / ".agents" / "skills" / "github-workflow")
        root = self.home / ".codex" / "skills"
        root.mkdir(parents=True)
        foreign = self.base / "foreign-skills"
        foreign.mkdir()
        (foreign / "marker").write_bytes(b"untouched\n")
        alias = root / "alias"
        link_directory(alias, foreign)
        try:
            forged = alias / "github-workflow"
            source = self.home / ".agents" / "skills" / "github-workflow"
            transaction = receipt_path.parent
            retired = transaction / "duplicates" / "0" / forged.name
            backup = transaction / "backup" / "duplicate-0" / forged.name
            shutil.copytree(source, retired, symlinks=True)
            shutil.copytree(source, backup, symlinks=True)
            # The forged metadata is schema-valid, and its retired tree is available to move.
            receipt["duplicates"].append({"path": str(forged), "retired_to": str(retired),
                                          "backup": str(backup), "inventory": receipt["after"],
                                          "resolved": os.path.realpath(str(forged))})
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            receipt_bytes = receipt_path.read_bytes()
            foreign_before = snapshot(foreign)
            refused = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
            self.assert_refused(refused, 1)
            self.assertEqual(receipt_path.read_bytes(), receipt_bytes)
            self.assertEqual(snapshot(foreign), foreign_before)
            self.assertEqual(snapshot(self.home / ".agents" / "skills" / "github-workflow"), active)
            self.assertFalse((self.home / ".agents" / "dev-harness-install" / "CURRENT").exists())
        finally:
            unlink_directory(alias)

    def test_recover_restores_from_backup_when_parked_copy_drifted(self):
        before, parked = self.crash_after_parking()
        (parked / "local-notes.md").write_bytes(b"changed while parked\n")
        drifted = snapshot(parked)
        report = self.recover()
        self.assertEqual(report["parked_drift"], str(parked))
        self.assertEqual(snapshot(self.target), before)
        self.assertEqual(snapshot(parked), drifted)
        self.assertEqual(len(visible_copies(*self.roots)), 1)

    def test_recover_restores_from_backup_when_parked_copy_is_lost(self):
        before, parked = self.crash_after_parking()
        shutil.rmtree(parked)
        self.recover()
        self.assertEqual(snapshot(self.target), before)

    def test_rerun_recover_after_a_failed_duplicate_restoration(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        before, legacy_before = self.v52_layout(target), self.v52_layout(legacy)
        plan = self.plan("codex")
        crashed = self.apply(plan, "--retire-duplicate", legacy, env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:parked"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        parked = next((self.home / ".agents" / "dev-harness-install").glob("*/retired/github-workflow"))
        (parked / "local-notes.md").write_bytes(b"changed while parked\n")
        failed = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed",
                                    env={"DEV_HARNESS_INSTALL_TEST_FAULT": "apply:undo-move-0"})
        self.assert_refused(failed, 3)
        self.assertEqual(snapshot(target), before)
        report = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed")
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertEqual(snapshot(target), before)
        self.assertEqual(snapshot(legacy), legacy_before)

    def test_recover_restores_a_lost_or_changed_retired_duplicate_from_its_backup(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        for label in ("lost", "changed"):
            with self.subTest(label):
                for path in (self.home / ".agents", self.home / ".codex"):
                    shutil.rmtree(path, ignore_errors=True)
                before, legacy_before = self.v52_layout(target), self.v52_layout(legacy)
                plan = self.plan("codex")
                crashed = self.apply(plan, "--retire-duplicate", legacy, env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:moved-0"})
                self.assertEqual(crashed.returncode, 70, crashed.stderr)
                retired = next((self.home / ".agents" / "dev-harness-install").glob("*/duplicates/0/github-workflow"))
                if label == "lost":
                    shutil.rmtree(retired)
                else:
                    (retired / "local-notes.md").write_bytes(b"changed while retired\n")
                for attempt in range(2):
                    report = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed")
                    self.assertEqual(report.returncode, 0, report.stderr)
                self.assertEqual((snapshot(target), snapshot(legacy)), (before, legacy_before))
                if label == "changed":
                    self.assertTrue((retired / "local-notes.md").is_file())

    def test_recover_killed_after_restoring_a_duplicate_can_be_rerun(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        before, legacy_before = self.v52_layout(target), self.v52_layout(legacy)
        plan = self.plan("codex")
        crashed = self.apply(plan, "--retire-duplicate", legacy, env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:activated"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        retired = next((self.home / ".agents" / "dev-harness-install").glob("*/duplicates/0/github-workflow"))
        (retired / "local-notes.md").write_bytes(b"changed while retired\n")
        killed = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed",
                                    env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:undo-move-restore-0:done"})
        self.assertEqual(killed.returncode, 70, killed.stderr)
        report = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed")
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertEqual(json.loads(report.stdout)["moved_drift"], [str(retired)])
        self.assertEqual((snapshot(target), snapshot(legacy)), (before, legacy_before))
        self.assertTrue((retired / "local-notes.md").is_file())

    @unittest.skipIf(os.name == "nt", "POSIX directory modes")
    def test_kept_directories_keep_their_mode(self):
        self.v52_layout()
        private = self.target / "private-notes"
        (private / "drafts").mkdir(parents=True)
        (private / "drafts" / "idea.md").write_bytes(b"private\n")
        (private / "drafts").chmod(0o750)
        private.chmod(0o700)
        self.target.chmod(0o700)
        before = snapshot(self.target)
        receipt_path = self.installed()
        self.assertEqual(stat.S_IMODE(os.stat(self.target).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(private).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(private / "drafts").st_mode), 0o750)
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertEqual(snapshot(self.target), before)

    def test_rollback_restores_a_lost_or_changed_retired_duplicate_from_its_backup(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        for label in ("lost", "changed"):
            with self.subTest(label):
                for path in (self.home / ".agents", self.home / ".codex"):
                    shutil.rmtree(path, ignore_errors=True)
                before, legacy_before = self.v52_layout(target), self.v52_layout(legacy)
                result = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
                self.assertEqual(result.returncode, 0, result.stderr)
                receipt_path = Path(json.loads(result.stdout)["receipt"])
                retired = Path(json.loads(receipt_path.read_text(encoding="utf-8"))["duplicates"][0]["retired_to"])
                if label == "lost":
                    shutil.rmtree(retired)
                else:
                    (retired / "local-notes.md").write_bytes(b"changed while retired\n")
                rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
                self.assertEqual(rollback.returncode, 0, rollback.stderr)
                self.assertEqual((snapshot(target), snapshot(legacy)), (before, legacy_before))
                if label == "changed":
                    self.assertTrue((retired / "local-notes.md").is_file())

    @unittest.skipIf(os.name == "nt", "POSIX directory modes")
    def test_a_changed_root_mode_is_drift(self):
        self.v52_layout()
        plan = self.plan()
        self.target.chmod(0o700)
        self.assert_refused(self.apply(plan), 1)
        self.assertEqual(stat.S_IMODE(os.stat(self.target).st_mode), 0o700)
        self.target.chmod(0o755)
        receipt_path = self.installed()
        self.target.chmod(0o700)
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assert_refused(rollback, 1)
        self.assertIn(".", [item["path"] for item in json.loads(rollback.stderr)["details"]])
        self.assertEqual(stat.S_IMODE(os.stat(self.target).st_mode), 0o700)

    @unittest.skipIf(os.name == "nt", "POSIX directory modes")
    def test_plan_blocks_a_directory_without_owner_write_permission(self):
        self.v52_layout()
        for path in (self.target, self.target.parent):
            with self.subTest(path=path.name):
                path.chmod(0o555)
                self.addCleanup(path.chmod, 0o755)
                before = snapshot(self.target)
                output = self.base / "plan.json"
                result = self.run_installer("plan", "--runtime", "claude", "--home", self.home, "--checksums", self.checksums,
                                            "--output", output)
                self.assert_refused(result, 2)
                error = json.loads(result.stderr)
                self.assertIn(f"add owner write permission to {path}, then plan again", error["error"])
                self.assertEqual(error["details"], {"path": str(path), "mode": "0555"})
                self.assertFalse(output.exists())
                self.assertEqual(snapshot(self.target), before)
                path.chmod(0o755)
        plan = self.plan()
        self.target.chmod(0o555)
        # apply plans again, so it blocks before the park rename instead of failing there.
        self.assert_refused(self.apply(plan), 2)
        self.assertFalse((self.state() / "CURRENT").exists())

    @unittest.skipIf(os.name == "nt", "POSIX directory modes")
    def test_rollback_blocks_a_target_without_owner_write_permission_before_any_rename(self):
        self.v52_layout()
        # An explicit mode, so the recorded mode does not depend on the umask.
        self.target.chmod(0o750)
        receipt_path = self.installed()
        after = snapshot(self.target)
        recorded = json.loads(receipt_path.read_text(encoding="utf-8"))["after"]["."]["mode"]
        self.assertEqual(recorded, 0o750)
        self.target.chmod(0o555)
        self.addCleanup(self.target.chmod, recorded)
        result = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assert_refused(result, 2)
        self.assertIn(f"add owner write permission to {self.target} (the receipt records mode 0750), then run rollback again",
                      json.loads(result.stderr)["error"])
        self.assertEqual(snapshot(self.target), after)
        self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8"))["state"], "installed")
        self.assertFalse((self.state() / "CURRENT").exists())
        self.target.chmod(recorded)
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)

    def legacy_owner_write_receipt(self):
        """A receipt recording root mode 0550, as an older installer run by root could write, for a root at 0750."""
        self.v52_layout()
        self.target.chmod(0o750)
        before = snapshot(self.target)
        receipt_path = self.installed()
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["after"]["."]["mode"] = 0o550
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        self.addCleanup(lambda: self.target.exists() and self.target.chmod(0o750))
        return before, receipt_path

    @unittest.skipIf(os.name == "nt", "POSIX directory modes")
    def test_rollback_of_a_receipt_recording_a_root_without_owner_write_accepts_the_owner_write_fix(self):
        before, receipt_path = self.legacy_owner_write_receipt()
        self.target.chmod(0o550)
        blocked = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assert_refused(blocked, 2)
        self.assertIn(f"add owner write permission to {self.target} (the receipt records mode 0550; rollback also accepts "
                      "0750), then run rollback again", json.loads(blocked.stderr)["error"])
        self.target.chmod(0o750)
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertEqual(snapshot(self.target), before)
        self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8"))["state"], "rolled back")

    @unittest.skipIf(os.name == "nt", "POSIX directory modes")
    def test_owner_write_fix_still_refuses_any_other_drift(self):
        _, receipt_path = self.legacy_owner_write_receipt()
        pristine = self.base / "pristine"
        shutil.copytree(self.home, pristine, symlinks=True)

        def edit_with_owner_write():
            (self.target / "SKILL.md").chmod(0o644)
            (self.target / "SKILL.md").write_bytes(b"edited\n")

        for name, change in (("another root mode", lambda: self.target.chmod(0o700)),
                             ("owner write and an edited file", edit_with_owner_write)):
            with self.subTest(drift=name):
                shutil.rmtree(self.home)
                shutil.copytree(pristine, self.home, symlinks=True)
                change()
                drifted = snapshot(self.target)
                rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
                self.assert_refused(rollback, 1)
                self.assertEqual(snapshot(self.target), drifted)
                self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8"))["state"], "installed")
                self.assertFalse((self.state() / "CURRENT").exists())

    @unittest.skipIf(os.name == "nt", "POSIX directory modes")
    def test_interrupted_rollback_after_the_owner_write_fix_recovers(self):
        before, receipt_path = self.legacy_owner_write_receipt()
        after = snapshot(self.target)
        pristine = self.base / "pristine"
        shutil.copytree(self.home, pristine, symlinks=True)
        names = self.checkpoints("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertIn("rollback:activated", names)
        for name in names:
            with self.subTest(checkpoint=name):
                shutil.rmtree(self.home)
                shutil.copytree(pristine, self.home, symlinks=True)
                crashed = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed",
                                             env={"DEV_HARNESS_INSTALL_TEST_CRASH": name})
                self.assertEqual(crashed.returncode, 70, crashed.stderr)
                recover = self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
                self.assertEqual(recover.returncode, 0, recover.stderr)
                self.assertFalse((self.state() / "CURRENT").exists())
                state = json.loads(receipt_path.read_text(encoding="utf-8"))["state"]
                # Until the commit, recovery puts back the tree as found: the owner-write fix, not the recorded mode.
                self.assertEqual((snapshot(self.target), state),
                                 (before, "rolled back") if name == "rollback:committed" else (after, "installed"))

    @unittest.skipIf(os.name == "nt", "POSIX directory modes")
    def test_rollback_refusal_names_the_recorded_root_mode(self):
        self.v52_layout()
        # An explicit mode, so the recorded mode does not depend on the umask.
        self.target.chmod(0o755)
        receipt_path = self.installed()
        self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8"))["after"]["."]["mode"], 0o755)
        self.target.chmod(0o700)
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assert_refused(rollback, 1)
        self.assertIn(f"restore mode 0755 on {self.target} (now 0700)", json.loads(rollback.stderr)["error"])

    @unittest.skipIf(os.name == "nt", "POSIX directory modes")
    def test_a_duplicate_or_its_root_without_owner_write_blocks_before_any_rename(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        before, legacy_before = self.v52_layout(target), self.v52_layout(legacy)
        plan = self.plan("codex")
        for path, reason in ((legacy, "so this duplicate cannot be moved to another directory"),
                             (legacy.parent, f"so the duplicate {legacy} cannot be moved out of it")):
            with self.subTest(path=str(path)):
                mode = stat.S_IMODE(os.stat(path).st_mode)
                path.chmod(0o555)
                # Restored here, not by addCleanup: the final apply moves the duplicate away.
                try:
                    output = self.base / "blocked-plan.json"
                    result = self.run_installer("plan", "--runtime", "codex", "--home", self.home, "--checksums",
                                                self.checksums, "--output", output)
                    self.assert_refused(result, 2)
                    error = json.loads(result.stderr)
                    self.assertIn(f"{path} has no owner write permission (mode 0555), {reason}; "
                                  f"add owner write permission to {path}, then plan again", error["error"])
                    self.assertEqual(error["details"], {"path": str(path), "mode": "0555"})
                    self.assertFalse(output.exists())
                    # apply plans again, so it blocks before retiring the duplicate instead of failing at that rename.
                    self.assert_refused(self.apply(plan, "--retire-duplicate", legacy), 2)
                    self.assertFalse((self.home / ".agents" / "dev-harness-install" / "CURRENT").exists())
                finally:
                    path.chmod(mode)
                self.assertEqual((snapshot(target), snapshot(legacy)), (before, legacy_before))
        result = self.apply(plan, "--retire-duplicate", legacy)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX directory modes")
    def test_rollback_blocks_a_duplicate_root_without_owner_write_before_any_rename(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        before, legacy_before = self.v52_layout(target), self.v52_layout(legacy)
        result = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt_path = Path(json.loads(result.stdout)["receipt"])
        receipt = receipt_path.read_bytes()
        after = snapshot(target)
        mode = stat.S_IMODE(os.stat(legacy.parent).st_mode)
        legacy.parent.chmod(0o555)
        self.addCleanup(legacy.parent.chmod, mode)
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assert_refused(rollback, 2)
        self.assertIn(f"{legacy.parent} has no owner write permission (mode 0555), so the retired duplicate {legacy} "
                      f"cannot be moved back into it; add owner write permission to {legacy.parent}, then run rollback again",
                      json.loads(rollback.stderr)["error"])
        self.assertEqual(receipt_path.read_bytes(), receipt)
        self.assertFalse((self.home / ".agents" / "dev-harness-install" / "CURRENT").exists())
        self.assertEqual(snapshot(target), after)
        self.assertFalse(legacy.exists())
        legacy.parent.chmod(mode)
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertEqual((snapshot(target), snapshot(legacy)), (before, legacy_before))

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "POSIX directory modes for an unprivileged user")
    def test_rollback_blocks_a_linked_duplicate_root_whose_target_has_no_owner_write(self):
        if not symlinks_supported():
            self.skipTest("symlinks unavailable")
        # The secondary root is a link unchanged since apply; its own mode (0777) is not where the rename writes.
        target = self.home / ".agents" / "skills" / "github-workflow"
        physical = Path(os.path.realpath(self.base / "codex-skills"))
        before, legacy_before = self.v52_layout(target), self.v52_layout(physical / "github-workflow")
        (self.home / ".codex").mkdir()
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        link_directory(legacy.parent, physical)
        # plan, and the plan apply repeats, check the same physical parent before the duplicate leaves it.
        physical.chmod(0o555)
        try:
            output = self.base / "blocked-plan.json"
            result = self.run_installer("plan", "--runtime", "codex", "--home", self.home, "--checksums",
                                        self.checksums, "--output", output)
            self.assert_refused(result, 2)
            self.assertIn(f"{legacy.parent} leads to {physical}, which has no owner write permission (mode 0555), so the "
                          f"duplicate {legacy} cannot be moved out of it; add owner write permission to {physical}, then "
                          "plan again", json.loads(result.stderr)["error"])
            self.assertFalse(output.exists())
        finally:
            physical.chmod(0o755)
        result = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt_path = Path(json.loads(result.stdout)["receipt"])
        receipt, after = receipt_path.read_bytes(), snapshot(target)
        physical.chmod(0o555)
        self.addCleanup(physical.chmod, 0o755)
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assert_refused(rollback, 2)
        error = json.loads(rollback.stderr)
        self.assertIn(f"{legacy.parent} leads to {physical}, which has no owner write permission (mode 0555), so the "
                      f"retired duplicate {legacy} cannot be moved back into it; add owner write permission to {physical}, "
                      "then run rollback again", error["error"])
        self.assertEqual(error["details"], {"path": str(legacy.parent), "mode": "0555", "physical": str(physical)})
        self.assertEqual(receipt_path.read_bytes(), receipt)
        self.assertFalse((self.home / ".agents" / "dev-harness-install" / "CURRENT").exists())
        self.assertEqual(snapshot(target), after)
        self.assertFalse((physical / "github-workflow").exists())
        physical.chmod(0o755)
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertEqual((snapshot(target), snapshot(physical / "github-workflow")), (before, legacy_before))

    # The OS enforces these modes only for an unprivileged user; root reads and searches any directory.
    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "POSIX directory modes for an unprivileged user")
    def test_a_directory_that_cannot_be_searched_or_listed_blocks_with_its_fix(self):
        before = self.v52_layout()
        skills, config = self.target.parent, self.target.parent.parent
        nested = skills / "other-skill" / "nested"
        nested.mkdir(parents=True)
        (nested / "notes.md").write_bytes(b"notes\n")
        cases = ((skills, 0o444, "searched"), (skills, 0o400, "searched"), (skills, 0o300, "listed"), (config, 0o444, "searched"),
                 (nested, 0o444, "searched"), (nested, 0o300, "listed"), (nested, 0o000, "listed"))
        for path, mode, action in cases:
            with self.subTest(path=str(path), mode=f"{mode:04o}"):
                path.chmod(mode)
                # Restored before the next case even when an assertion fails, so one failure does not cascade.
                try:
                    output = self.base / "blocked-plan.json"
                    result = self.run_installer("plan", "--runtime", "claude", "--home", self.home, "--checksums",
                                                self.checksums, "--output", output)
                    self.assert_refused(result, 2)
                    error = json.loads(result.stderr)
                    self.assertIn(f"{path} cannot be {action} (mode {mode:04o}), so the installer cannot inspect what it "
                                  f"holds; add owner read and search (execute) permission to {path}, then plan again",
                                  error["error"])
                    self.assertEqual(error["details"], {"path": str(path), "mode": f"{mode:04o}"})
                    self.assertFalse(output.exists())
                finally:
                    path.chmod(0o755)
                self.assertEqual(snapshot(self.target), before)
        plan = self.plan()
        skills.chmod(0o444)
        self.addCleanup(skills.chmod, 0o755)
        self.assert_refused(self.apply(plan), 2)
        skills.chmod(0o755)
        self.assertFalse((self.state() / "CURRENT").exists())
        self.assertEqual(snapshot(self.target), before)
        receipt_path = self.installed()
        after = snapshot(self.target)
        skills.chmod(0o444)
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assert_refused(rollback, 2)
        self.assertIn(f"add owner read and search (execute) permission to {skills}, then run rollback again",
                      json.loads(rollback.stderr)["error"])
        recover = self.run_installer("recover", "--runtime", "claude", "--home", self.home, "--maintenance-confirmed")
        self.assert_refused(recover, 2)
        self.assertIn(f"add owner read and search (execute) permission to {skills}, then run recover again",
                      json.loads(recover.stderr)["error"])
        skills.chmod(0o755)
        self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8"))["state"], "installed")
        self.assertEqual(snapshot(self.target), after)

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "POSIX directory modes for an unprivileged user")
    def test_the_selected_skill_directory_or_one_inside_it_that_cannot_be_searched_or_listed_blocks(self):
        before = self.v52_layout()

        def blocked(path, command, retry):
            # Each mode keeps owner write, so only the search or list check can block.
            for mode, action in ((0o600, "searched"), (0o300, "listed")):
                with self.subTest(command=command[0], path=str(path), mode=f"{mode:04o}"):
                    path.chmod(mode)
                    try:
                        result = self.run_installer(*command)
                        self.assert_refused(result, 2)
                        error = json.loads(result.stderr)
                        self.assertIn(f"{path} cannot be {action} (mode {mode:04o}), so the installer cannot inspect what "
                                      f"it holds; add owner read and search (execute) permission to {path}, then {retry}",
                                      error["error"])
                        self.assertEqual(error["details"], {"path": str(path), "mode": f"{mode:04o}"})
                    finally:
                        path.chmod(0o755)

        output = self.base / "blocked-plan.json"
        for path in (self.target, self.target / "templates"):
            blocked(path, ("plan", "--runtime", "claude", "--home", self.home, "--checksums", self.checksums,
                           "--output", output), "plan again")
            self.assertFalse(output.exists())
            self.assertEqual(snapshot(self.target), before)
        receipt_path = self.installed()
        after = snapshot(self.target)
        inside = self.target / "agents"
        for path in (self.target, inside):
            blocked(path, ("rollback", "--receipt", receipt_path, "--maintenance-confirmed"), "run rollback again")
            self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8"))["state"], "installed")
            self.assertFalse((self.state() / "CURRENT").exists())
            self.assertEqual(snapshot(self.target), after)

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "POSIX directory modes for an unprivileged user")
    def test_a_directory_above_the_receipt_that_cannot_be_searched_blocks_rollback_and_recover(self):
        self.v52_layout()
        receipt_path = self.installed()
        receipt, after = receipt_path.read_bytes(), snapshot(self.target)
        commands = ((("rollback", "--receipt", receipt_path, "--maintenance-confirmed"), "run rollback again"),
                    (("recover", "--receipt", receipt_path, "--maintenance-confirmed"), "run recover again"))
        # The configuration directory holds both the skills and the installer state; the receipt is read first.
        for path in (self.target.parent.parent, self.state()):
            mode = path.stat().st_mode & 0o7777
            for command, retry in commands:
                with self.subTest(path=str(path), command=command[0]):
                    path.chmod(0o600)
                    try:
                        result = self.run_installer(*command)
                        self.assert_refused(result, 2)
                        error = json.loads(result.stderr)
                        self.assertIn(f"{path} cannot be searched (mode 0600), so the installer cannot inspect what it "
                                      f"holds; add owner read and search (execute) permission to {path}, then {retry}",
                                      error["error"])
                        self.assertEqual(error["details"], {"path": str(path), "mode": "0600"})
                    finally:
                        path.chmod(mode)
                    self.assertEqual(receipt_path.read_bytes(), receipt)
                    self.assertFalse((self.state() / "CURRENT").exists())
                    self.assertEqual(snapshot(self.target), after)

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "POSIX directory modes for an unprivileged user")
    def test_a_directory_above_the_plan_that_cannot_be_searched_blocks_apply(self):
        before = self.v52_layout()
        plans = self.base / "plans"
        plans.mkdir()
        plan = self.plan().replace(plans / "plan.json")
        plans.chmod(0o600)
        try:
            result = self.apply(plan)
            self.assert_refused(result, 2)
            error = json.loads(result.stderr)
            self.assertIn(f"{plans} cannot be searched (mode 0600), so the installer cannot inspect what it holds; "
                          f"add owner read and search (execute) permission to {plans}, then run apply again", error["error"])
            self.assertEqual(error["details"], {"path": str(plans), "mode": "0600"})
        finally:
            plans.chmod(0o755)
        self.assertFalse((self.state() / "CURRENT").exists())
        self.assertEqual(snapshot(self.target), before)

    @unittest.skipIf(os.name == "nt", "POSIX owners and modes")
    def test_the_fix_for_an_inaccessible_directory_follows_its_owner_and_mode(self):
        spec = importlib.util.spec_from_file_location("harness_installer_hints", self.installer)
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        folder = self.base / "denied"
        folder.mkdir(mode=0o700)
        folder.chmod(0o700)
        # The owner already has read and search permission, so its mode is not what denies access.
        blocked = installer.inaccessible(folder, "listed")
        self.assertIn(f"{folder} cannot be listed (mode 0700), so the installer cannot inspect what it holds; its owner "
                      "already has read and search permission, so an access control list or system privacy setting "
                      f"denies access: allow this process to read and search {folder}, then plan again", str(blocked))
        # A directory another user owns: only that owner or an administrator can change its mode.
        owner = folder.stat().st_uid
        with mock.patch.object(installer.os, "geteuid", return_value=owner + 1):
            blocked = installer.inaccessible(folder, "searched", "run rollback again")
        self.assertIn(f"{folder} belongs to another user (uid {owner}): have its owner or an administrator give you "
                      "read and search (execute) access to it, or its ownership, then run rollback again", str(blocked))
        self.assertEqual(blocked.details, {"path": str(folder), "mode": "0700"})

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "POSIX directory modes for an unprivileged user")
    def test_a_second_codex_root_that_cannot_be_searched_or_listed_blocks_plan(self):
        self.v52_layout(self.home / ".agents" / "skills" / "github-workflow")
        second = self.home / ".codex" / "skills"
        (second / "other-skill").mkdir(parents=True)
        output = self.base / "blocked-plan.json"
        for mode, action in ((0o444, "searched"), (0o300, "listed")):
            with self.subTest(mode=f"{mode:04o}"):
                second.chmod(mode)
                try:
                    result = self.run_installer("plan", "--runtime", "codex", "--home", self.home, "--checksums",
                                                self.checksums, "--output", output)
                    self.assert_refused(result, 2)
                    self.assertIn(f"{second} cannot be {action} (mode {mode:04o})", json.loads(result.stderr)["error"])
                    self.assertFalse(output.exists())
                finally:
                    second.chmod(0o755)

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "POSIX directory modes for an unprivileged user")
    def test_rollback_blocks_a_folder_its_retired_duplicate_cannot_be_searched_back_into(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        vendor = self.home / ".codex" / "skills" / "vendor"
        legacy = vendor / "github-workflow"
        before, legacy_before = self.v52_layout(target), self.v52_layout(legacy)
        result = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt_path = Path(json.loads(result.stdout)["receipt"])
        receipt = receipt_path.read_bytes()
        after = snapshot(target)
        # Owner write without search: a write-only check would let the renames start and fail at the move back.
        vendor.chmod(0o644)
        try:
            rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
            self.assert_refused(rollback, 2)
            self.assertIn(f"{vendor} cannot be searched (mode 0644), so the installer cannot inspect what it holds; "
                          f"add owner read and search (execute) permission to {vendor}, then run rollback again",
                          json.loads(rollback.stderr)["error"])
        finally:
            vendor.chmod(0o755)
        self.assertEqual(receipt_path.read_bytes(), receipt)
        self.assertFalse((self.home / ".agents" / "dev-harness-install" / "CURRENT").exists())
        self.assertEqual(snapshot(target), after)
        self.assertFalse(legacy.exists())
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertEqual((snapshot(target), snapshot(legacy)), (before, legacy_before))

    def test_rollback_blocks_a_folder_replaced_by_a_link_its_retired_duplicate_would_return_through(self):
        if os.name != "nt" and not symlinks_supported():
            self.skipTest("symlinks unavailable")
        target = self.home / ".agents" / "skills" / "github-workflow"
        vendor = self.home / ".codex" / "skills" / "vendor"
        legacy = vendor / "github-workflow"
        before, legacy_before = self.v52_layout(target), self.v52_layout(legacy)
        result = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt_path = Path(json.loads(result.stdout)["receipt"])
        receipt = receipt_path.read_bytes()
        retired_from = json.loads(receipt)["duplicates"][0]["resolved"]
        after = snapshot(target)
        # The emptied folder is replaced by a link to a directory outside every skill root.
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        vendor.rmdir()
        link_directory(vendor, elsewhere)
        try:
            for older in (False, True):
                with self.subTest(older_receipt=older):
                    if older:
                        # A receipt without the resolved path: the link below the root still blocks.
                        recorded = json.loads(receipt)
                        del recorded["duplicates"][0]["resolved"]
                        receipt_path.write_text(json.dumps(recorded), encoding="utf-8")
                    content = receipt_path.read_bytes()
                    rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
                    self.assert_refused(rollback, 2)
                    error = json.loads(rollback.stderr)
                    self.assertIn(f"The retired duplicate {legacy} would be restored to ", error["error"])
                    self.assertIn("a link or junction on the way was added or retargeted after apply; restore that "
                                  "folder, then run rollback again", error["error"])
                    self.assertEqual(error["details"]["resolves_to"], str(Path(os.path.realpath(elsewhere)) / legacy.name))
                    self.assertEqual(error["details"]["retired_from"], retired_from)
                    self.assertEqual(list(elsewhere.iterdir()), [])
                    self.assertEqual(receipt_path.read_bytes(), content)
                    self.assertFalse((self.home / ".agents" / "dev-harness-install" / "CURRENT").exists())
                    self.assertEqual(snapshot(target), after)
        finally:
            unlink_directory(vendor)
        vendor.mkdir()
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertEqual((snapshot(target), snapshot(legacy)), (before, legacy_before))

    def test_rollback_blocks_a_secondary_root_or_its_config_root_replaced_by_a_link(self):
        if os.name != "nt" and not symlinks_supported():
            self.skipTest("symlinks unavailable")
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        before, legacy_before = self.v52_layout(target), self.v52_layout(legacy)
        for folder in (legacy.parent, legacy.parent.parent):
            with self.subTest(folder=folder.name):
                result = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
                self.assertEqual(result.returncode, 0, result.stderr)
                receipt_path = Path(json.loads(result.stdout)["receipt"])
                receipt = receipt_path.read_bytes()
                older_receipt = json.loads(receipt)
                del older_receipt["duplicates"][0]["resolved"]
                after = snapshot(target)
                # The folder moves outside the home and is replaced by a link to it there.
                elsewhere = self.base / ("elsewhere" + folder.name)
                shutil.move(str(folder), str(elsewhere))
                link_directory(folder, elsewhere)
                try:
                    for older in (False, True):
                        if older:
                            # Nothing in a receipt without the resolved path shows where the link pointed.
                            receipt_path.write_text(json.dumps(older_receipt), encoding="utf-8")
                        content = receipt_path.read_bytes()
                        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
                        self.assert_refused(rollback, 2)
                        error = json.loads(rollback.stderr)
                        if older:
                            self.assertIn(f"{folder} is a link or junction, and this receipt predates the record of "
                                          "where each retired duplicate was", error["error"])
                            self.assertEqual(error["details"]["link"], str(folder))
                        else:
                            self.assertIn(f"The retired duplicate {legacy} would be restored to ", error["error"])
                            self.assertEqual(error["details"]["retired_from"],
                                             json.loads(receipt)["duplicates"][0]["resolved"])
                        self.assertFalse(legacy.exists())
                        self.assertEqual(receipt_path.read_bytes(), content)
                        self.assertFalse((self.home / ".agents" / "dev-harness-install" / "CURRENT").exists())
                        self.assertEqual(snapshot(target), after)
                finally:
                    unlink_directory(folder)
                    shutil.move(str(elsewhere), str(folder))
                # Once the folder is back, either receipt rolls back: the older one for skills, the current one
                # for .codex.
                if folder == legacy.parent.parent:
                    receipt_path.write_bytes(receipt)
                rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
                self.assertEqual(rollback.returncode, 0, rollback.stderr)
                self.assertEqual((snapshot(target), snapshot(legacy)), (before, legacy_before))

    def test_rollback_restores_a_retired_duplicate_through_a_config_root_link_unchanged_since_apply(self):
        if os.name != "nt" and not symlinks_supported():
            self.skipTest("symlinks unavailable")
        # A config root kept elsewhere and linked into the home, as dotfile managers do.
        target = self.home / ".agents" / "skills" / "github-workflow"
        dotfiles = self.base / "dotfiles" / "codex"
        legacy_source = dotfiles / "skills" / "github-workflow"
        before, legacy_before = self.v52_layout(target), self.v52_layout(legacy_source)
        link_directory(self.home / ".codex", dotfiles)
        try:
            legacy = self.home / ".codex" / "skills" / "github-workflow"
            result = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(legacy_source.exists())
            receipt_path = Path(json.loads(result.stdout)["receipt"])
            receipt = receipt_path.read_bytes()
            # A recorded location that is not a path is refused, not read as an older receipt.
            malformed = json.loads(receipt)
            malformed["duplicates"][0]["resolved"] = None
            receipt_path.write_text(json.dumps(malformed), encoding="utf-8")
            rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
            self.assert_refused(rollback, 1)
            self.assertIn(f"no valid location for the retired duplicate {legacy}", json.loads(rollback.stderr)["error"])
            # A receipt without the resolved path cannot show that the link is unchanged, so it blocks.
            older_receipt = json.loads(receipt)
            del older_receipt["duplicates"][0]["resolved"]
            receipt_path.write_text(json.dumps(older_receipt), encoding="utf-8")
            content = receipt_path.read_bytes()
            rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
            self.assert_refused(rollback, 2)
            error = json.loads(rollback.stderr)
            self.assertEqual(error["details"]["link"], str(self.home / ".codex"))
            self.assertIn("or, if it still points there, roll back with the installer that wrote this receipt",
                          error["error"])
            self.assertEqual(receipt_path.read_bytes(), content)
            self.assertFalse(legacy_source.exists())
            receipt_path.write_bytes(receipt)
            rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
            self.assertEqual(rollback.returncode, 0, rollback.stderr)
            self.assertEqual((snapshot(target), snapshot(legacy_source)), (before, legacy_before))
        finally:
            unlink_directory(self.home / ".codex")

    def test_rollback_restores_a_retired_duplicate_for_a_receipt_recording_a_root_without_owner_write(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "vendor" / "github-workflow"
        before, legacy_before = self.v52_layout(target), self.v52_layout(legacy)
        result = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt_path = Path(json.loads(result.stdout)["receipt"])
        # As an older installer run by root could record it; rollback accepts the root with owner write added.
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["after"]["."]["mode"] &= ~stat.S_IWUSR
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertEqual((snapshot(target), snapshot(legacy)), (before, legacy_before))

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "POSIX directory modes for an unprivileged user")
    def test_a_failed_recover_the_journal_cannot_record_still_reports_its_cause_and_retry(self):
        before, parked = self.crash_after_parking()
        transaction = parked.parent.parent
        # The journal and the undo both write in the transaction directory, so both fail.
        transaction.chmod(0o500)
        try:
            failed = self.run_installer("recover", "--runtime", "claude", "--home", self.home, "--maintenance-confirmed")
            self.assert_refused(failed, 3)
            error = json.loads(failed.stderr)["error"]
            self.assertRegex(error, r"^Restoration incomplete: .*Permission denied.* \(the journal could not record it: "
                                    r".*Permission denied.*\)\. Recovery data kept in ")
            self.assertTrue(error.endswith(f"{transaction}; fix the cause and run `recover` again"), error)
        finally:
            transaction.chmod(0o755)
        self.assertTrue((self.state() / "CURRENT").exists())
        self.recover()
        self.assertEqual(snapshot(self.target), before)

    def test_a_failed_undo_the_journal_cannot_record_still_reports_its_cause_and_recover(self):
        spec = importlib.util.spec_from_file_location("harness_installer_undo", self.installer)
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        journal = self.base / "transaction" / "journal.json"
        operation = mock.Mock(path=journal, record={})
        operation.undo.side_effect = OSError("disk full while undoing")
        # The cause that stopped the undo (a full disk, an unwritable state directory) also stops the journal.
        with mock.patch.object(installer, "write_json", side_effect=OSError("disk full while journaling")):
            with self.assertRaises(installer.Incomplete) as caught:
                installer.undo_or_report(operation, installer.Blocked("The original blocker"))
        self.assertEqual(str(caught.exception),
                         "The original blocker; restoration incomplete: disk full while undoing (the journal could not "
                         f"record it: disk full while journaling). Recovery data kept in {journal.parent}; run `recover` "
                         "with the same --plan or --receipt before any other step")
        self.assertEqual(operation.record, {"undo_error": "disk full while undoing"})

    def test_rerun_recover_sets_a_stale_restoration_copy_aside(self):
        before, parked = self.crash_after_parking()
        shutil.rmtree(parked)
        failed = self.run_installer("recover", "--runtime", "claude", "--home", self.home, "--maintenance-confirmed",
                                    env={"DEV_HARNESS_INSTALL_TEST_FAULT": "apply:undo-restore"})
        self.assert_refused(failed, 3)
        report = self.recover()
        self.assertEqual(len(report["stale_restore_copies"]), 1)
        self.assertTrue(Path(report["stale_restore_copies"][0]).is_dir())
        self.assertEqual(snapshot(self.target), before)

    def test_recover_without_a_transaction_names_the_codex_home_as_the_cause(self):
        # An existing state directory without CURRENT, as left by an earlier completed transaction.
        state = self.home / ".agents" / "dev-harness-install"
        state.mkdir(parents=True)
        report = self.recover("codex")
        self.assertEqual(report["result"], "nothing to recover")
        self.assertEqual(report["state_root"], str(state))
        self.assertIn("another home directory (--home)", report["hint"])
        self.assertIn("--plan", report["hint"])
        self.assertIn("--receipt", report["hint"])
        self.assertNotIn("CODEX_HOME", report["hint"])

    def test_recover_killed_after_setting_a_stale_copy_aside_still_reports_it(self):
        before, parked = self.crash_after_parking()
        shutil.rmtree(parked)
        failed = self.run_installer("recover", "--runtime", "claude", "--home", self.home, "--maintenance-confirmed",
                                    env={"DEV_HARNESS_INSTALL_TEST_FAULT": "apply:undo-restore"})
        self.assert_refused(failed, 3)
        killed = self.run_installer("recover", "--runtime", "claude", "--home", self.home, "--maintenance-confirmed",
                                    env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:undo-restore:set-aside:done"})
        self.assertEqual(killed.returncode, 70, killed.stderr)
        report = self.recover()
        self.assertEqual(len(report["stale_restore_copies"]), 1)
        self.assertTrue(Path(report["stale_restore_copies"][0]).is_dir())
        self.assertEqual(snapshot(self.target), before)

    def test_stale_copy_report_omits_an_aside_path_that_was_never_used(self):
        before, parked = self.crash_after_parking()
        shutil.rmtree(parked)
        failed = self.run_installer("recover", "--runtime", "claude", "--home", self.home, "--maintenance-confirmed",
                                    env={"DEV_HARNESS_INSTALL_TEST_FAULT": "apply:undo-restore"})
        self.assert_refused(failed, 3)
        refused = self.run_installer("recover", "--runtime", "claude", "--home", self.home, "--maintenance-confirmed",
                                     env={"DEV_HARNESS_INSTALL_TEST_FAULT": "apply:undo-restore:set-aside"})
        self.assert_refused(refused, 3)
        report = self.recover()
        self.assertEqual(len(report["stale_restore_copies"]), 1)
        self.assertTrue(Path(report["stale_restore_copies"][0]).is_dir())
        self.assertEqual(snapshot(self.target), before)

    @unittest.skipIf(os.name == "nt", "pauses the installer with a named pipe")
    def test_target_changed_during_apply_is_put_back_as_found(self):
        before = self.v52_layout()
        plan = self.plan()
        pipe = self.base / "trace.fifo"
        os.mkfifo(pipe)
        process = subprocess.Popen([sys.executable, "-B", str(self.installer), "apply", "--plan", str(plan), "--checksums",
                                    str(self.checksums), "--maintenance-confirmed"], cwd=self.base,
                                   env=self.environment({"DEV_HARNESS_INSTALL_TEST_TRACE": str(pipe)}),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        reader = None
        try:
            # The first trace write, at apply:staged, blocks on the pipe until a reader opens it. Once the
            # staged tree exists, the drift check and backup are done and no rename has happened yet.
            for _ in range(600):
                if list(self.state().glob("*/staged/github-workflow")) or process.poll() is not None:
                    break
                time.sleep(0.05)
            self.assertIsNone(process.poll(), "the installer did not pause before its first rename")
            (self.target / "SKILL.md").write_bytes(b"edited during apply\n")
            edited = snapshot(self.target)
            reader = os.open(str(pipe), os.O_RDONLY | os.O_NONBLOCK)
            stdout, stderr = process.communicate(timeout=120)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            if reader is not None:
                os.close(reader)
        self.assertEqual(process.returncode, 1, stdout + stderr)
        self.assertIn("put back as found", json.loads(stderr)["error"])
        self.assertEqual(snapshot(self.target), edited)
        self.assertNotEqual(edited, before)
        self.assertFalse((self.state() / "CURRENT").exists())

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "pauses the installer with a named pipe; POSIX modes")
    def test_a_directory_made_unlistable_during_apply_restores_the_original_state(self):
        before = self.v52_layout()
        other = self.home / ".claude" / "skills" / "other-skill"
        other.mkdir()
        plan = self.plan()

        def paused_apply(name, fault=None):
            pipe = self.base / name
            os.mkfifo(pipe)
            env = {"DEV_HARNESS_INSTALL_TEST_TRACE": str(pipe)}
            if fault:
                env["DEV_HARNESS_INSTALL_TEST_FAULT"] = fault
            earlier = set(self.state().glob("*/staged/github-workflow"))
            process = subprocess.Popen([sys.executable, "-B", str(self.installer), "apply", "--plan", str(plan),
                                        "--checksums", str(self.checksums), "--maintenance-confirmed"], cwd=self.base,
                                       env=self.environment(env), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, encoding="utf-8")
            reader = None
            try:
                # Paused at apply:staged, after every check and before the first rename: only the discovery that
                # follows the renames can meet the unlistable directory. A copy staged by an earlier run is not
                # this run's pause.
                for _ in range(600):
                    staged = [path for path in self.state().glob("*/staged/github-workflow") if path not in earlier]
                    if staged or process.poll() is not None:
                        break
                    time.sleep(0.05)
                self.assertIsNone(process.poll(), "the installer did not pause before its first rename")
                self.assertTrue(staged, "the installer did not stage the new copy within 30 seconds")
                other.chmod(0o300)
                try:
                    reader = os.open(str(pipe), os.O_RDONLY | os.O_NONBLOCK)
                    stdout, stderr = process.communicate(timeout=120)
                finally:
                    other.chmod(0o755)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
                if reader is not None:
                    os.close(reader)
            return process.returncode, stdout + stderr, stderr

        code, output, stderr = paused_apply("trace.fifo")
        self.assertEqual(code, 1, output)
        self.assertIn(f"{other} cannot be listed (mode 0300)", json.loads(stderr)["error"])
        self.assertIn("the original state was restored", json.loads(stderr)["error"])
        self.assertEqual(snapshot(self.target), before)
        self.assertFalse((self.state() / "CURRENT").exists())
        # An undo that fails after that discovery names the unlistable directory and the recovery command.
        code, output, stderr = paused_apply("trace-undo.fifo", "apply:undo-park")
        self.assertEqual(code, 3, output)
        error = json.loads(stderr)["error"]
        self.assertIn(f"{other} cannot be listed (mode 0300)", error)
        self.assertIn("restoration incomplete", error)
        self.assertIn("run `recover`", error)
        journals = [json.loads(path.read_text(encoding="utf-8")) for path in self.state().glob("*/journal.json")]
        self.assertTrue(any("undo_error" in journal for journal in journals), journals)
        recover = self.run_installer("recover", "--runtime", "claude", "--home", self.home, "--maintenance-confirmed")
        self.assertEqual(recover.returncode, 0, recover.stderr)
        self.assertEqual(snapshot(self.target), before)
        self.assertFalse((self.state() / "CURRENT").exists())

    def test_recover_uses_the_recorded_config_root(self):
        config = self.base / "claude-config"
        environment = {"HOME": str(self.home), "USERPROFILE": str(self.home), "CLAUDE_CONFIG_DIR": str(config)}
        before = self.v52_layout(config / "skills" / "github-workflow")
        output = self.base / "plan.json"
        result = self.run_installer("plan", "--runtime", "claude", "--checksums", self.checksums, "--output", output, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        crashed = self.apply(output, env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:parked"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        default = self.recover()
        self.assertEqual(default["result"], "nothing to recover")
        self.assertFalse((self.home / ".claude").exists())
        # "Nothing to recover" must not read as all clear: it names what was checked and the recovery route.
        self.assertEqual(default["config_root"], str(self.home / ".claude"))
        self.assertEqual(default["state_root"], str(self.home / ".claude" / "dev-harness-install"))
        self.assertIn("--plan", default["hint"])
        self.assertIn("--receipt", default["hint"])
        self.assertIn("CLAUDE_CONFIG_DIR", default["hint"])
        report = self.run_installer("recover", "--plan", output, "--maintenance-confirmed")
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertEqual(json.loads(report.stdout)["result"], "restored")
        self.assertEqual(snapshot(config / "skills" / "github-workflow"), before)

    def test_test_hooks_block_a_plan_whose_config_root_is_outside_the_temporary_directory(self):
        plan = self.plan()
        value = json.loads(plan.read_text(encoding="utf-8"))
        value["config_root"] = os.path.abspath(os.sep + "nonexistent-harness-config")
        plan.write_text(json.dumps(value), encoding="utf-8")
        result = self.apply(plan)
        self.assert_refused(result, 2)
        self.assertIn(value["config_root"], json.loads(result.stderr)["details"]["outside"])
        self.assertFalse(os.path.lexists(value["config_root"]))

    def test_trace_hook_outside_the_temporary_directory_blocks(self):
        before = self.v52_layout()
        trace = os.path.abspath(os.sep + "nonexistent-harness-trace")
        result = self.apply(self.plan(), env={"DEV_HARNESS_INSTALL_TEST_TRACE": trace})
        self.assert_refused(result, 2)
        self.assertIn(trace, json.loads(result.stderr)["details"]["outside"])
        self.assertFalse(os.path.lexists(trace))
        self.assertEqual(snapshot(self.target), before)

    def test_rollback_preparation_failure_is_a_refusal(self):
        self.v52_layout()
        receipt_path = self.installed()
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        after = snapshot(self.target)
        shutil.rmtree(receipt["retired"])
        (Path(receipt["backup"]) / "local-notes.md").write_bytes(b"damaged backup\n")
        result = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assert_refused(result, 1)
        self.assertIn("Neither the retired copy nor the backup", json.loads(result.stderr)["error"])
        self.assertEqual(snapshot(self.target), after)
        self.assertFalse((self.state() / "CURRENT").exists())

    def test_interrupted_rollback_with_a_retired_duplicate_recovers(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        before, legacy_before = self.v52_layout(target), self.v52_layout(legacy)
        result = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt_path = Path(json.loads(result.stdout)["receipt"])
        after = snapshot(target)
        pristine = self.base / "pristine"
        shutil.copytree(self.home, pristine, symlinks=True)
        names = self.checkpoints("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertLess(names.index("rollback:activated"), names.index("rollback:moved-0"))
        for name in names:
            with self.subTest(checkpoint=name):
                shutil.rmtree(self.home)
                shutil.copytree(pristine, self.home, symlinks=True)
                crashed = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed",
                                             env={"DEV_HARNESS_INSTALL_TEST_CRASH": name})
                self.assertEqual(crashed.returncode, 70, crashed.stderr)
                # Up to the duplicate's rename, the rollback has not yet reached the two-copy before-state.
                if names.index(name) < names.index("rollback:move-0:done"):
                    self.assertLessEqual(len(visible_copies(*self.roots)), 1)
                self.run_installer("recover", "--receipt", receipt_path, "--maintenance-confirmed")
                if name == "rollback:committed":
                    self.assertEqual((snapshot(target), snapshot(legacy)), (before, legacy_before))
                else:
                    self.assertEqual((snapshot(target), snapshot(legacy)), (after, None))

    def test_interrupted_codex_duplicate_retirement_recovers_both_copies(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        before, legacy_before = self.v52_layout(target), self.v52_layout(legacy)
        plan = self.plan("codex")
        pristine = self.base / "pristine"
        shutil.copytree(self.home, pristine, symlinks=True)
        names = self.checkpoints("apply", "--plan", plan, "--checksums", self.checksums, "--maintenance-confirmed",
                                 "--retire-duplicate", legacy)
        self.assertIn("apply:moved-0", names)
        for name in names:
            if name == "apply:committed":
                continue
            with self.subTest(checkpoint=name):
                shutil.rmtree(self.home)
                shutil.copytree(pristine, self.home, symlinks=True)
                crashed = self.apply(plan, "--retire-duplicate", legacy, env={"DEV_HARNESS_INSTALL_TEST_CRASH": name})
                self.assertEqual(crashed.returncode, 70, crashed.stderr)
                self.assertLessEqual(len(visible_copies(*self.roots)), 2)
                self.recover("codex")
                self.assertEqual(snapshot(target), before)
                self.assertEqual(snapshot(legacy), legacy_before)

    def test_rollback_refuses_a_receipt_without_an_after_inventory(self):
        self.v52_layout()
        receipt_path = self.installed()
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt_path.write_text(json.dumps(dict(receipt, after=None)), encoding="utf-8")
        result = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assert_refused(result, 1)
        self.assertIn("after-inventory", json.loads(result.stderr)["error"])

    def test_rollback_refuses_a_receipt_with_a_non_integer_root_mode(self):
        self.v52_layout()
        receipt_path = self.installed()
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["after"]["."]["mode"] = "0755"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        result = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assert_refused(result, 1)
        error = json.loads(result.stderr)["error"]
        self.assertIn("after-inventory", error)
        self.assertNotIn("restore mode", error)

    def test_rollback_refuses_a_receipt_copy_that_differs(self):
        self.v52_layout()
        receipt_path = self.installed()
        copy = self.base / "receipt-copy.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        copy.write_text(json.dumps(dict(receipt, after={})), encoding="utf-8")
        self.assert_refused(self.run_installer("rollback", "--receipt", copy, "--maintenance-confirmed"), 1)
        copy.write_text(json.dumps(receipt), encoding="utf-8")
        result = self.run_installer("rollback", "--receipt", copy, "--maintenance-confirmed")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8"))["state"], "rolled back")

    def test_config_directories_from_the_environment_are_recorded_and_reused(self):
        config = self.base / "claude-config"
        environment = {"HOME": str(self.home), "USERPROFILE": str(self.home), "CLAUDE_CONFIG_DIR": str(config)}
        output = self.base / "plan.json"
        result = self.run_installer("plan", "--runtime", "claude", "--checksums", self.checksums, "--output", output, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["config_root"], str(config))
        # Apply and rollback run without the variable: the recorded root decides.
        result = self.apply(output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(visible_copies(config / "skills"), [config / "skills" / "github-workflow" / "SKILL.md"])
        self.assertFalse((self.home / ".claude").exists())
        receipt_path = Path(json.loads(result.stdout)["receipt"])
        self.assertTrue(receipt_path.is_relative_to(config / "dev-harness-install"))
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertIsNone(snapshot(config / "skills" / "github-workflow"))
        codex_home = self.base / "codex-home"
        legacy = codex_home / "skills" / "github-workflow"
        self.v52_layout(legacy)
        result = self.run_installer("plan", "--runtime", "codex", "--checksums", self.checksums, "--output", output,
                                    env=dict(environment, CODEX_HOME=str(codex_home)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["duplicates"], [str(legacy)])

    def test_test_hooks_outside_a_temporary_home_block(self):
        home = Path(os.path.abspath(os.sep + "nonexistent-harness-home"))
        result = self.run_installer("plan", "--runtime", "claude", "--home", home, "--checksums", self.checksums,
                                    env={"DEV_HARNESS_INSTALL_TEST_TRACE": str(self.base / "trace.txt")})
        self.assert_refused(result, 2)
        self.assertIn("test hooks", json.loads(result.stderr)["error"])
        self.assertFalse(os.path.lexists(home))
        result = self.run_installer("recover", "--runtime", "claude", "--home", home, "--maintenance-confirmed")
        self.assert_refused(result, 2)
        self.assertFalse(os.path.lexists(home))

    # Blocking conditions.
    def test_link_target_blocks_without_change(self):
        if not symlinks_supported():
            self.skipTest("symlinks unavailable")
        real = self.base / "elsewhere" / "github-workflow"
        before = self.v52_layout(real)
        self.target.parent.mkdir(parents=True)
        os.symlink(real, self.target, target_is_directory=True)
        home = snapshot(self.home)
        result = self.run_installer("plan", "--runtime", "claude", "--home", self.home, "--checksums", self.checksums)
        self.assert_refused(result, 2)
        self.assertEqual(snapshot(self.home), home)
        self.assertEqual(snapshot(real), before)

    def test_linked_duplicate_blocks(self):
        if not symlinks_supported():
            self.skipTest("symlinks unavailable")
        real = self.base / "elsewhere" / "copy"
        real.mkdir(parents=True)
        (real / "SKILL.md").write_bytes(b"---\nname: github-workflow\n---\n")
        link = self.home / ".claude" / "skills" / "linked-copy"
        link.parent.mkdir(parents=True)
        os.symlink(real, link, target_is_directory=True)
        result = self.run_installer("plan", "--runtime", "claude", "--home", self.home, "--checksums", self.checksums)
        self.assert_refused(result, 2)
        self.assertEqual(json.loads(result.stderr)["details"], [str(link)])

    def test_nested_duplicate_blocks(self):
        self.v52_layout()
        nested = self.target / "examples" / "github-workflow"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_bytes(b"---\nname: github-workflow\n---\n")
        result = self.run_installer("plan", "--runtime", "claude", "--home", self.home, "--checksums", self.checksums)
        self.assert_refused(result, 2)
        self.assertEqual(json.loads(result.stderr)["details"], [str(nested)])

    def test_target_under_another_spelling_blocks(self):
        alias = self.home / ".claude" / "skills" / "GitHub-Workflow"
        before = self.v52_layout(alias)
        if not self.target.exists():
            self.skipTest("case-sensitive file system: the spelling is a separate directory")
        result = self.run_installer("plan", "--runtime", "claude", "--home", self.home, "--checksums", self.checksums)
        self.assert_refused(result, 2)
        self.assertIn("another spelling", json.loads(result.stderr)["error"])
        self.assertEqual(snapshot(alias), before)

    def test_link_inside_target_blocks(self):
        if not symlinks_supported():
            self.skipTest("symlinks unavailable")
        self.v52_layout()
        os.symlink(self.base, self.target / "outside", target_is_directory=True)
        result = self.run_installer("plan", "--runtime", "claude", "--home", self.home, "--checksums", self.checksums)
        self.assert_refused(result, 2)

    def test_consumer_state_blocks(self):
        before = self.v52_layout()
        plan = self.plan()
        cases = {
            "active claude": "/usr/local/bin/claude --resume\n",
            "active codex via node": "node /opt/lib/node_modules/codex-cli/bin/codex.js\n",
            "active windows codex": '"C:\\Tools\\codex.exe" exec\n',
            "claude code via node": "node /opt/lib/node_modules/anthropic-ai/claude-code/cli.js --print\n",
            "quoted windows node": '"C:\\Program Files\\nodejs\\node.exe"  "C:\\npm\\node_modules\\anthropic-ai\\claude-code\\cli.js"\n',
            "desktop app": "/Applications/Claude.app/Contents/MacOS/Claude\n",
        }
        for label, listing in cases.items():
            with self.subTest(label):
                self.processes.write_text(listing, encoding="utf-8")
                self.assert_refused(self.apply(plan), 2)
        self.processes.unlink()
        with self.subTest("unverifiable"):
            self.assert_refused(self.apply(plan), 2)
        with self.subTest("unconfirmed"):
            result = self.run_installer("apply", "--plan", plan, "--checksums", self.checksums)
            self.assert_refused(result, 2)
        self.assertEqual(snapshot(self.target), before)
        self.assertFalse(os.path.lexists(self.state() / "CURRENT"))

    @unittest.skipIf(os.name == "nt", "the fake ps is a POSIX shell script")
    def test_real_process_probe_uses_ps(self):
        self.v52_layout()
        plan = self.plan()
        bin_directory = self.base / "bin"
        bin_directory.mkdir()
        fake = bin_directory / "ps"
        path = str(bin_directory) + os.pathsep + os.environ.get("PATH", "")
        # The fake ps runs as a child of the installer, so $PPID is the installer's own process ID.
        # Lines are `PID PPID COMMAND`; 5000 stands for the launcher above the installer.
        own = 'echo "$PPID 5000 python3 install.py recover --runtime codex"; echo "1 0 /sbin/init"; '
        cases = (("active", own + "echo '5000 1 /bin/zsh'; echo '42 1 /opt/homebrew/bin/claude'", 2),
                 ("launched by a consumer", own + "echo '5000 1 /opt/homebrew/bin/claude'", 2),
                 ("launched by a consumer prompt naming the script", own + "echo '5000 1 /opt/homebrew/bin/claude -p run install.py apply'", 2),
                 ("launched by codex exec", own + "echo '5000 1 codex exec python3 install.py apply'", 2),
                 ("launched by a node consumer", own + "echo '5000 1 node /opt/lib/claude-code/cli.js -p install.py'", 2),
                 ("another installer naming codex", own + "echo '5000 1 /bin/zsh'; echo '77 1 python3 install.py recover --runtime codex'", 2),
                 ("failing", "exit 1", 2), ("empty", "exit 0", 2), ("without this process", "echo '1 0 /sbin/init'", 2),
                 ("idle behind a wrapper", own + "echo '5000 5001 /bin/sh -c python3 install.py recover --runtime codex'; "
                  "echo '5001 1 /bin/zsh -l'; echo '   7   1   /usr/sbin/sshd -D'", 0))
        for label, script, code in cases:
            with self.subTest(label):
                fake.write_text("#!/bin/sh\n" + script + "\n", encoding="utf-8")
                fake.chmod(0o755)
                result = self.run_installer("apply", "--plan", plan, "--checksums", self.checksums, "--maintenance-confirmed",
                                            env={"PATH": path}, processes=False)
                self.assertEqual(result.returncode, code, result.stderr)

    # Codex and duplicates.
    def test_codex_legacy_duplicate_blocks_until_retired_under_receipt(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        before = self.v52_layout(target)
        legacy_before = self.v52_layout(legacy)
        plan = self.plan("codex")
        self.assertEqual(json.loads(plan.read_text(encoding="utf-8"))["duplicates"], [str(legacy)])
        blocked = self.apply(plan)
        self.assert_refused(blocked, 2)
        self.assertEqual(json.loads(blocked.stderr)["details"], [str(legacy)])
        self.assertEqual(len(visible_copies(*self.roots)), 2)
        result = self.apply(plan, "--retire-duplicate", legacy)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(visible_copies(*self.roots), [target / "SKILL.md"])
        receipt_path = Path(json.loads(result.stdout)["receipt"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual([item["path"] for item in receipt["duplicates"]], [str(legacy)])
        self.assertEqual(sorted(receipt["instruction_files"]), sorted(str(self.home / ".codex" / name) for name in ("AGENTS.md", "AGENTS.override.md")))
        rollback = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertEqual(snapshot(target), before)
        self.assertEqual(snapshot(legacy), legacy_before)

    def test_renamed_claude_duplicate_blocks(self):
        self.v52_layout()
        copy = self.home / ".claude" / "skills" / "workflow-backup"
        copy.mkdir(parents=True)
        for label, data in (("plain", b"---\nname: github-workflow\n---\n"), ("byte order mark", b"\xef\xbb\xbf---\nname: github-workflow\n---\n")):
            with self.subTest(label):
                (copy / "SKILL.md").write_bytes(data)
                result = self.apply(self.plan())
                self.assert_refused(result, 2)
                self.assertEqual(json.loads(result.stderr)["details"], [str(copy)])

    def test_stray_skill_file_in_a_skill_root_blocks(self):
        for runtime, root in (("claude", self.home / ".claude" / "skills"), ("codex", self.home / ".codex" / "skills")):
            with self.subTest(runtime):
                (root / "other-skill").mkdir(parents=True)
                (root / "other-skill" / "SKILL.md").write_bytes(b"---\nname: other-skill\n---\n")
                (root / "SKILL.md").write_bytes(b"---\nname: github-workflow\n---\n")
                before = snapshot(root)
                result = self.run_installer("plan", "--runtime", runtime, "--home", self.home, "--checksums", self.checksums)
                self.assert_refused(result, 2)
                self.assertEqual(json.loads(result.stderr)["details"], [str(root)])
                self.assertEqual(snapshot(root), before)

    def test_linked_codex_root_is_walked_once(self):
        if not symlinks_supported():
            self.skipTest("symlinks unavailable")
        target = self.home / ".agents" / "skills" / "github-workflow"
        before = self.v52_layout(target)
        (self.home / ".codex").mkdir()
        os.symlink(target.parent, self.home / ".codex" / "skills", target_is_directory=True)
        plan = self.plan("codex")
        self.assertEqual(json.loads(plan.read_text(encoding="utf-8"))["duplicates"], [])
        result = self.apply(plan)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(visible_copies(target.parent)), 1)
        rollback = self.run_installer("rollback", "--receipt", json.loads(result.stdout)["receipt"], "--maintenance-confirmed")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertEqual(snapshot(target), before)

    def test_duplicate_changed_after_planning_is_drift(self):
        self.v52_layout(self.home / ".agents" / "skills" / "github-workflow")
        legacy = self.home / ".codex" / "skills" / "github-workflow"
        self.v52_layout(legacy)
        plan = self.plan("codex")
        (legacy / "local-notes.md").write_bytes(b"changed after planning\n")
        changed = snapshot(legacy)
        result = self.apply(plan, "--retire-duplicate", legacy)
        self.assert_refused(result, 1)
        self.assertIn("duplicate_inventories", json.loads(result.stderr)["details"])
        self.assertEqual(snapshot(legacy), changed)

    def test_state_directory_inside_git_work_tree_blocks(self):
        (self.home / ".git").mkdir()
        result = self.run_installer("plan", "--runtime", "claude", "--home", self.home, "--checksums", self.checksums)
        self.assert_refused(result, 2)


if __name__ == "__main__":
    unittest.main()
