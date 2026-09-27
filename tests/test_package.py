"""Exercise reproducible distribution output and source-boundary failures."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("harness_build", ROOT / "scripts/build.py")
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


class PackageTests(unittest.TestCase):
    def test_reproducible_archive_and_complete_manifest(self):
        with tempfile.TemporaryDirectory(prefix="harness-package-") as directory:
            root = Path(directory)
            first = BUILD.build(root / "first")
            second = BUILD.build(root / "second")
            left = root / "first" / first["archive"]
            right = root / "second" / second["archive"]
            self.assertEqual(left.read_bytes(), right.read_bytes())
            with zipfile.ZipFile(left) as archive:
                names = archive.namelist()
                prefix = "dev-harness-v" + first["version"] + "/"
                manifest = json.loads(archive.read(prefix + "MANIFEST.json"))
                self.assertEqual(set(names), {prefix + name for name in manifest["files"]} | {prefix + "MANIFEST.json"})
                for name, item in manifest["files"].items():
                    with self.subTest(file=name):
                        data = archive.read(prefix + name)
                        self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])
                        self.assertEqual(len(data), item["bytes"])
                common = archive.read(prefix + "AGENTS.md")
                self.assertEqual(common, archive.read(prefix + "configurations/codex/AGENTS.md"))
                self.assertEqual(common, archive.read(prefix + "configurations/claude-desktop/AGENTS.md"))
                self.assertEqual(archive.read(prefix + "CLAUDE.md"), archive.read(prefix + "configurations/claude-desktop/CLAUDE.md"))
                self.assertTrue(all(".." not in Path(name).parts and not name.startswith("/") for name in names))
                self.assertNotIn(prefix + "TASKS.md", names)
                self.assertNotIn(prefix + ".git/config", names)
                self.assertNotIn(prefix + "audit/local-installation.json", names)

    def copied_source(self, directory):
        source = Path(directory) / "source"
        shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", "dist", "__pycache__", "TASKS.md"))
        return source

    def test_rejects_symlinked_payload(self):
        with tempfile.TemporaryDirectory(prefix="harness-link-") as directory:
            source = self.copied_source(directory)
            outside = Path(directory) / "outside.txt"
            outside.write_text("Private fixture content", encoding="utf-8")
            (source / "skills/github-workflow/references/external.md").symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "Symlink"):
                BUILD.build(Path(directory) / "output", root=source)
            self.assertFalse((Path(directory) / "output").exists())

    def test_rejects_unexpected_payload_file(self):
        with tempfile.TemporaryDirectory(prefix="harness-extra-") as directory:
            source = self.copied_source(directory)
            (source / "skills/github-workflow/private.bak").write_text("Private fixture content", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unexpected source file"):
                BUILD.build(Path(directory) / "output", root=source)

    def test_rejects_unlisted_runtime_configuration(self):
        with tempfile.TemporaryDirectory(prefix="harness-private-config-") as directory:
            source = self.copied_source(directory)
            (source / "configurations/codex/private-runtime.toml").write_text(
                'provider = "private-local-account"\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "Undeclared source file"):
                BUILD.build(Path(directory) / "output", root=source)
            self.assertFalse((Path(directory) / "output").exists())

    def test_rejects_unsafe_version(self):
        with tempfile.TemporaryDirectory(prefix="harness-version-") as directory:
            source = self.copied_source(directory)
            (source / "VERSION").write_text("../../unexpected\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "VERSION"):
                BUILD.build(Path(directory) / "output", root=source)


if __name__ == "__main__":
    unittest.main()
