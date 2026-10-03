"""Black-box tests of install.py, run from the extracted package against disposable fake homes."""
from __future__ import annotations

import ast
import contextlib
import io
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
import time
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
        self.assertEqual(json.loads(result.stderr)["details"], ["installer_version", "retirements", "retirement_copies"])
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
        original_vendor = self.base / "original-vendor"
        vendor.rename(original_vendor)
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
        original_vendor.rename(vendor)
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
        pristine = self.base / "pristine"
        shutil.copytree(self.home, pristine, symlinks=True)
        result = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt_path = Path(json.loads(result.stdout)["receipt"])
        after = snapshot(target)
        names = self.checkpoints("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertLess(names.index("rollback:activated"), names.index("rollback:moved-0"))
        for name in names:
            with self.subTest(checkpoint=name):
                shutil.rmtree(self.home)
                shutil.copytree(pristine, self.home, symlinks=True)
                # A fresh receipt belongs to these physical directories, unlike a copied transaction.
                result = self.apply(self.plan("codex"), "--retire-duplicate", legacy)
                self.assertEqual(result.returncode, 0, result.stderr)
                receipt_path = Path(json.loads(result.stdout)["receipt"])
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
                plan = self.plan("codex")
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
    def invoke_at_checkpoint(self, arguments, point, action, before=False, env=None):
        """Run the extracted installer with one deterministic filesystem change at an existing checkpoint."""
        spec = importlib.util.spec_from_file_location("physical_parent_installer", self.installer)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        observed = []
        original = module.checkpoint

        def checkpoint(name):
            selected = name == point and not observed
            if selected:
                observed.append(name)
                if before:
                    action()
            original(name)
            if selected and not before:
                action()

        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self.environment(env), clear=True), mock.patch.object(module, "checkpoint", checkpoint):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = module.main(list(map(str, arguments)))
        self.assertEqual(observed, [point])
        return subprocess.CompletedProcess(arguments, code, output.getvalue(), errors.getvalue())

    def linked_duplicate_fixture(self, config_link=False):
        if os.name != "nt" and not symlinks_supported():
            self.skipTest("symlinks unavailable")
        target = self.home / ".agents" / "skills" / "github-workflow"
        before = self.v52_layout(target)
        first, second = self.base / "first", self.base / "second"
        if config_link:
            first, second = first / "skills", second / "skills"
            alias = self.home / ".codex"
            source, replacement = first.parent, second.parent
        else:
            alias = self.home / ".codex" / "skills"
            source, replacement = first, second
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        duplicate = first / "github-workflow"
        original = self.v52_layout(duplicate)
        alias.parent.mkdir(parents=True, exist_ok=True)
        link_directory(alias, source)
        self.addCleanup(lambda: unlink_directory(alias) if os.path.lexists(alias) else None)

        def select(path):
            unlink_directory(alias)
            link_directory(alias, path)

        return target, before, duplicate, original, second, source, replacement, select

    def test_apply_keeps_duplicate_moves_on_the_bound_parent_when_an_alias_changes(self):
        for config_link in (False, True):
            with self.subTest(config_link=config_link):
                if config_link:
                    # Each case is a fresh transaction and home, not copied persisted identities.
                    shutil.rmtree(self.home)
                    self.home.mkdir()
                    for name in ("first", "second"):
                        shutil.rmtree(self.base / name)
                target, before, duplicate, original, second, source, replacement, select = self.linked_duplicate_fixture(config_link)
                self.v52_layout(second / "github-workflow")
                alternate = snapshot(second)
                lexical = self.home / ".codex" / "skills" / "github-workflow"
                plan = self.plan("codex")
                result = self.invoke_at_checkpoint(
                    ["apply", "--plan", plan, "--checksums", self.checksums, "--maintenance-confirmed",
                     "--retire-duplicate", lexical], "apply:moving-0", lambda: select(replacement))
                self.assertEqual(snapshot(second), alternate)
                self.assertIn(result.returncode, (1, 3), result.stdout + result.stderr)
                select(source)
                recovered = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed")
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                self.assertEqual((snapshot(target), snapshot(duplicate)), (before, original))
                # Remove this case's alias before replacing its home in the next case.
                unlink_directory(self.home / ".codex" if config_link else self.home / ".codex" / "skills")

    def test_rollback_restores_into_the_bound_parent_when_a_secondary_alias_changes(self):
        target, before, duplicate, original, second, source, replacement, select = self.linked_duplicate_fixture(True)
        lexical = self.home / ".codex" / "skills" / "github-workflow"
        applied = self.apply(self.plan("codex"), "--retire-duplicate", lexical)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        receipt = Path(json.loads(applied.stdout)["receipt"])
        alternate = snapshot(second)
        result = self.invoke_at_checkpoint(
            ["rollback", "--receipt", receipt, "--maintenance-confirmed"],
            "rollback:moving-0", lambda: select(replacement))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(snapshot(second), alternate)
        self.assertEqual((snapshot(target), snapshot(duplicate)), (before, original))

    def test_recovery_refuses_a_retargeted_duplicate_alias_and_succeeds_when_restored(self):
        target, before, duplicate, original, second, source, replacement, select = self.linked_duplicate_fixture(True)
        lexical = self.home / ".codex" / "skills" / "github-workflow"
        plan = self.plan("codex")
        crashed = self.apply(plan, "--retire-duplicate", lexical, env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:moved-0"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        alternate = snapshot(second)
        current = self.home / ".agents" / "dev-harness-install" / "CURRENT"
        pointer = current.read_bytes()
        select(replacement)
        recovered = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed")
        self.assert_refused(recovered, 3)
        self.assertEqual(current.read_bytes(), pointer)
        self.assertEqual(snapshot(second), alternate)
        self.assertFalse(duplicate.exists())
        select(source)
        recovered = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual((snapshot(target), snapshot(duplicate)), (before, original))
        self.assertFalse(current.exists())

    def test_rollback_recovery_keeps_a_retargeted_alias_untouched(self):
        target, before, duplicate, original, second, source, replacement, select = self.linked_duplicate_fixture()
        lexical = self.home / ".codex" / "skills" / "github-workflow"
        applied = self.apply(self.plan("codex"), "--retire-duplicate", lexical)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        receipt = Path(json.loads(applied.stdout)["receipt"])
        installed = snapshot(target)
        crashed = self.run_installer("rollback", "--receipt", receipt, "--maintenance-confirmed",
                                     env={"DEV_HARNESS_INSTALL_TEST_CRASH": "rollback:moved-0"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        self.v52_layout(second / "github-workflow")
        alternate = snapshot(second)
        select(replacement)
        recovered = self.run_installer("recover", "--receipt", receipt, "--maintenance-confirmed")
        self.assert_refused(recovered, 3)
        self.assertEqual(snapshot(second), alternate)
        self.assertEqual(snapshot(duplicate), original)
        select(source)
        recovered = self.run_installer("recover", "--receipt", receipt, "--maintenance-confirmed")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(snapshot(target), installed)
        self.assertFalse(duplicate.exists())

    def test_duplicate_parent_handles_survive_or_block_physical_parent_replacement(self):
        target, before, duplicate, original, second, source, replacement, select = self.linked_duplicate_fixture()
        lexical = self.home / ".codex" / "skills" / "github-workflow"
        plan = self.plan("codex")
        moved = self.base / "moved-parent"
        attempted = []

        def replace_parent():
            try:
                duplicate.parent.rename(moved)
            except PermissionError:
                self.assertEqual(os.name, "nt")
                attempted.append("blocked by live handle")
                return
            attempted.append("renamed")
            shutil.copytree(moved, duplicate.parent)

        result = self.invoke_at_checkpoint(
            ["apply", "--plan", plan, "--checksums", self.checksums, "--maintenance-confirmed",
             "--retire-duplicate", lexical], "apply:moving-0", replace_parent)
        self.assertEqual(len(attempted), 1)
        if os.name == "nt":
            self.assertEqual(attempted, ["blocked by live handle"])
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertEqual(snapshot(duplicate), original, "the replacement directory was changed")
            self.assertFalse((moved / "github-workflow").exists(), "the move did not use its original directory descriptor")
            replacement_tree = self.base / "preserved-replacement"
            duplicate.parent.rename(replacement_tree)
            moved.rename(duplicate.parent)
            recovered = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed")
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertEqual((snapshot(target), snapshot(duplicate)), (before, original))

    def test_trace_parent_retarget_after_validation_keeps_the_other_file_unchanged(self):
        if os.name != "nt" and not symlinks_supported():
            self.skipTest("symlinks unavailable")
        self.v52_layout()
        plan = self.plan()
        approved, alternate = self.base / "trace-approved", self.base / "trace-alternate"
        approved.mkdir()
        alternate.mkdir()
        sentinel = alternate / "trace.txt"
        sentinel.write_bytes(b"unchanged sentinel\n")
        (approved / "trace.txt").write_bytes(b"initial trace\n")
        alias = self.base / "trace-alias"
        link_directory(alias, approved)
        self.addCleanup(lambda: unlink_directory(alias))

        def retarget():
            unlink_directory(alias)
            link_directory(alias, alternate)

        result = self.invoke_at_checkpoint(
            ["apply", "--plan", plan, "--checksums", self.checksums, "--maintenance-confirmed"],
            "apply:staged", retarget, before=True, env={"DEV_HARNESS_INSTALL_TEST_TRACE": str(alias / "trace.txt")})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sentinel.read_bytes(), b"unchanged sentinel\n")
        self.assertIn("apply:committed", (approved / "trace.txt").read_text(encoding="utf-8"))

    def test_trace_leaf_replacement_between_checkpoints_keeps_the_other_file_unchanged(self):
        self.v52_layout()
        plan = self.plan()
        trace, saved, sentinel = self.base / "trace.txt", self.base / "saved-trace.txt", self.base / "sentinel.txt"
        trace.write_bytes(b"initial trace\n")
        sentinel.write_bytes(b"unchanged sentinel\n")
        replacement = []

        def replace_leaf():
            try:
                trace.rename(saved)
            except PermissionError:
                self.assertEqual(os.name, "nt")
                replacement.append("blocked")
                return
            os.link(sentinel, trace)
            replacement.append("replaced")

        result = self.invoke_at_checkpoint(
            ["apply", "--plan", plan, "--checksums", self.checksums, "--maintenance-confirmed"],
            "apply:staged", replace_leaf, env={"DEV_HARNESS_INSTALL_TEST_TRACE": str(trace)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sentinel.read_bytes(), b"unchanged sentinel\n")
        self.assertEqual(replacement, ["blocked" if os.name == "nt" else "replaced"])
        original = trace if os.name == "nt" else saved
        self.assertIn("apply:committed", original.read_text(encoding="utf-8"))

    def test_trace_leaf_replacement_before_first_checkpoint_is_refused_without_writing(self):
        before = self.v52_layout()
        plan = self.plan()
        trace, sentinel = self.base / "trace.txt", self.base / "sentinel.txt"
        trace.write_bytes(b"initial trace\n")
        sentinel.write_bytes(b"unchanged sentinel\n")

        def replace_leaf():
            trace.unlink()
            os.link(sentinel, trace)

        result = self.invoke_at_checkpoint(
            ["apply", "--plan", plan, "--checksums", self.checksums, "--maintenance-confirmed"],
            "apply:staged", replace_leaf, before=True, env={"DEV_HARNESS_INSTALL_TEST_TRACE": str(trace)})
        self.assert_refused(result, 2)
        self.assertEqual(sentinel.read_bytes(), b"unchanged sentinel\n")
        self.assertEqual(snapshot(self.target), before)
        self.assertFalse((self.state() / "CURRENT").exists())

    def test_trace_hard_links_and_file_links_are_refused_during_validation(self):
        self.v52_layout()
        plan = self.plan()
        trace, sentinel = self.base / "trace.txt", self.base / "sentinel.txt"
        sentinel.write_bytes(b"unchanged sentinel\n")
        os.link(sentinel, trace)
        result = self.apply(plan, env={"DEV_HARNESS_INSTALL_TEST_TRACE": str(trace)})
        self.assert_refused(result, 2)
        self.assertEqual(sentinel.read_bytes(), b"unchanged sentinel\n")
        trace.unlink()
        if symlinks_supported():
            trace.symlink_to(sentinel)
            result = self.apply(plan, env={"DEV_HARNESS_INSTALL_TEST_TRACE": str(trace)})
            self.assert_refused(result, 2)
            self.assertEqual(sentinel.read_bytes(), b"unchanged sentinel\n")

    def test_legacy_duplicate_receipt_without_physical_metadata_still_rolls_back(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        duplicate = self.home / ".codex" / "skills" / "github-workflow"
        before, original = self.v52_layout(target), self.v52_layout(duplicate)
        applied = self.apply(self.plan("codex"), "--retire-duplicate", duplicate)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        receipt_path = Path(json.loads(applied.stdout)["receipt"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        del receipt["duplicates"][0]["physical"]
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        rolled_back = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
        self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
        self.assertEqual((snapshot(target), snapshot(duplicate)), (before, original))

    def test_legacy_duplicate_journal_needs_a_recorded_original_location(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        duplicate = self.home / ".codex" / "skills" / "github-workflow"
        before, original = self.v52_layout(target), self.v52_layout(duplicate)
        plan = self.plan("codex")
        crashed = self.apply(plan, "--retire-duplicate", duplicate,
                             env={"DEV_HARNESS_INSTALL_TEST_CRASH": "apply:moved-0"})
        self.assertEqual(crashed.returncode, 70, crashed.stderr)
        state = self.home / ".agents" / "dev-harness-install"
        current = state / "CURRENT"
        journal = state / current.read_text(encoding="utf-8").strip()
        record = json.loads(journal.read_text(encoding="utf-8"))
        del record["moves"][0]["physical"]
        location = record["moves"][0].pop("resolved")
        journal.write_text(json.dumps(record), encoding="utf-8")
        recovered = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed")
        self.assert_refused(recovered, 3)
        self.assertTrue(current.exists())
        self.assertFalse(duplicate.exists())
        record["moves"][0]["resolved"] = location
        journal.write_text(json.dumps(record), encoding="utf-8")
        recovered = self.run_installer("recover", "--plan", plan, "--maintenance-confirmed")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual((snapshot(target), snapshot(duplicate)), (before, original))

    def test_a_malformed_physical_binding_is_not_treated_as_legacy_metadata(self):
        target = self.home / ".agents" / "skills" / "github-workflow"
        duplicate = self.home / ".codex" / "skills" / "github-workflow"
        self.v52_layout(target)
        self.v52_layout(duplicate)
        applied = self.apply(self.plan("codex"), "--retire-duplicate", duplicate)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        receipt_path = Path(json.loads(applied.stdout)["receipt"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        installed = snapshot(target)
        for malformed in (None, [], {"path": str(duplicate.parent), "identity": [True, 1]}):
            with self.subTest(binding=malformed):
                receipt["duplicates"][0]["physical"] = malformed
                receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
                result = self.run_installer("rollback", "--receipt", receipt_path, "--maintenance-confirmed")
                self.assert_refused(result, 1)
                self.assertIn("Invalid physical", json.loads(result.stderr)["error"])
                self.assertEqual(snapshot(target), installed)
                self.assertFalse(duplicate.exists())
                self.assertFalse((self.home / ".agents" / "dev-harness-install" / "CURRENT").exists())

    @unittest.skipUnless(os.name == "nt", "native Windows directory sharing and reparse controls")
    def test_windows_directory_binding_blocks_in_place_junction_conversion(self):
        import ctypes
        from ctypes import wintypes
        import struct

        spec = importlib.util.spec_from_file_location("physical_parent_installer", self.installer)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        parent, other = self.base / "bound-empty-parent", self.base / "other-empty-parent"
        parent.mkdir()
        other.mkdir()
        changed = False
        failure = None
        try:
            with module.PhysicalDirectory(parent) as bound:
                kernel = bound.kernel
                kernel.DeviceIoControl.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                                  wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID)
                kernel.DeviceIoControl.restype = wintypes.BOOL
                def convert(candidate):
                    handle = kernel.CreateFileW(str(candidate), 0x40000000, 7, None, 3, 0x02000000 | 0x00200000, None)
                    if handle == wintypes.HANDLE(-1).value:
                        return False, ctypes.get_last_error()
                    try:
                        # A mount-point record for two empty, disposable directories. No existing profile is used.
                        substitute = ("\\??\\" + str(other)).encode("utf-16-le")
                        display = str(other).encode("utf-16-le")
                        paths = substitute + b"\0\0" + display + b"\0\0"
                        data = struct.pack("<LHHHHHH", 0xA0000003, 8 + len(paths), 0, 0, len(substitute),
                                           len(substitute) + 2, len(display)) + paths
                        buffer = ctypes.create_string_buffer(data)
                        returned = wintypes.DWORD()
                        success = bool(kernel.DeviceIoControl(handle, 0x000900A4, buffer, len(data), None, 0,
                                                              ctypes.byref(returned), None))
                        return success, None if success else ctypes.get_last_error()
                    finally:
                        kernel.CloseHandle(handle)

                control = self.base / "unbound-control"
                control.mkdir()
                allowed, control_error = convert(control)
                try:
                    self.assertTrue(allowed, f"the unbound control could not become a junction: {control_error}")
                finally:
                    if allowed:
                        unlink_directory(control)
                changed, failure = convert(parent)
        finally:
            if changed:
                unlink_directory(parent)
        self.assertFalse(changed, "a bound directory was converted to a junction")
        self.assertIn(failure, (5, 32), "the denial must be an access/sharing barrier, not an invalid fixture")
        self.assertEqual(list(other.iterdir()), [])

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
