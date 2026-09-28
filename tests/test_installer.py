"""Black-box tests of install.py, run from the extracted package against disposable fake homes."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
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
    def run_installer(self, *arguments, env=None, processes=True):
        environment = {key: value for key, value in os.environ.items()
                       if key not in HOOKS and key not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        environment.update(HOME=str(self.guard), USERPROFILE=str(self.guard))
        if processes:
            environment["DEV_HARNESS_INSTALL_TEST_PROCESSES"] = str(self.processes)
        environment.update(env or {})
        return subprocess.run([sys.executable, "-B", str(self.installer), *map(str, arguments)], cwd=self.base,
                              env=environment, capture_output=True, text=True, encoding="utf-8", timeout=120)

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
        home = ROOT / "tests" / "no-such-home"
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
        own = 'echo "$PPID python3 install.py recover --runtime codex"; '
        cases = (("active", own + "echo '42 /opt/homebrew/bin/claude'", 2), ("failing", "exit 1", 2),
                 ("empty", "exit 0", 2), ("without this process", "echo '1 /sbin/init'", 2),
                 ("idle", own + "echo '1 /sbin/init'; echo '   7   /bin/zsh -l'", 0))
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
        (copy / "SKILL.md").write_bytes(b"---\nname: github-workflow\n---\n")
        result = self.apply(self.plan())
        self.assert_refused(result, 2)
        self.assertEqual(json.loads(result.stderr)["details"], [str(copy)])

    def test_state_directory_inside_git_work_tree_blocks(self):
        (self.home / ".git").mkdir()
        result = self.run_installer("plan", "--runtime", "claude", "--home", self.home, "--checksums", self.checksums)
        self.assert_refused(result, 2)


if __name__ == "__main__":
    unittest.main()
