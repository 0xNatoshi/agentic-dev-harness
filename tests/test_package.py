"""Exercise reproducible distribution output and source-boundary failures."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

from tests._fixture_support import symlinks_supported

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
                self.assertEqual(archive.read(prefix + "configurations/hermes/development.md"),
                                 (ROOT / "configurations/hermes/development.md").read_bytes())
                self.assertTrue(all(".." not in Path(name).parts and not name.startswith("/") for name in names))
                self.assertNotIn(prefix + "TASKS.md", names)
                self.assertNotIn(prefix + ".git/config", names)
                self.assertNotIn(prefix + "audit/local-installation.json", names)

    def copied_source(self, directory):
        source = Path(directory) / "source"
        shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", "dist", "__pycache__", "TASKS.md"))
        return source

    def assert_invalid_destinations(self, destinations):
        with tempfile.TemporaryDirectory(prefix="harness-paths-") as directory:
            source = self.copied_source(directory)
            mapping_path = source / "package-files.json"
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
            mapping["docs/package-README.md"].extend(destinations)
            mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
            output = Path(directory) / "output"
            with self.assertRaises(ValueError):
                BUILD.build(output, root=source)
            self.assertFalse(output.exists())

    def test_rejects_generated_manifest_destinations(self):
        for name in ["MANIFEST.json", "manifest.JSON", "MANIFEST.JSON/readme.md"]:
            with self.subTest(destination=name):
                self.assert_invalid_destinations([name])

    def test_rejects_destination_collisions(self):
        for names in [
            ["extra.txt", "extra.txt"],
            ["extra/Guide.md", "extra/guide.md"],
            ["extra", "extra/readme.md"],
            ["extra/readme.md", "extra"],
            ["extra/notes.md", "EXTRA/NOTES.MD/child.txt"],
            ["extra/notes.md/child.txt", "EXTRA/NOTES.MD"],
            ["extra/caf\u00e9.md", "extra/cafe\u0301.md"],
            ["extra/caf\u00e9.md", "extra/CAFE\u0301.MD/child.txt"],
            ["extra/cafe\u0301.md/child.txt", "extra/CAF\u00c9.md"],
            ["extra/I.txt", "extra/\u0131.txt"],
            ["extra/I.txt", "extra/\u0131.txt/child.md"],
            ["extra/\u0131.txt/child.md", "extra/I.txt"],
        ]:
            with self.subTest(destinations=names):
                self.assert_invalid_destinations(names)

    def test_rejects_nonportable_destination_components(self):
        components = ["notes" + char + "draft" for char in '<>:"\\|?*\x00\x1f']
        components += ["notes.", "notes ", "CON", "prn.txt", "AUX", "NUL.tar.gz", "NUL .txt", "conin$.txt", "CONOUT$", "CoM1.md", "LPT9", "COM\u00b9.log", "LPT\u00b2", "COM\u00b3"]
        for component in components:
            for name in ["extra/" + component, "extra/" + component + "/readme.md"]:
                with self.subTest(destination=name):
                    self.assert_invalid_destinations([name])

    def test_accepts_safe_neighboring_destinations(self):
        destinations = ["extra/.hidden", "extra/COM0.txt", "extra/COM10.txt", "extra/NUL-marker.md", "extra/notes..md", "extra/MANIFEST.json", "extra/cafe.md", "extra/caf\u00e9.md"]
        with tempfile.TemporaryDirectory(prefix="harness-safe-paths-") as directory:
            source = self.copied_source(directory)
            mapping_path = source / "package-files.json"
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
            mapping["docs/package-README.md"].extend(destinations)
            mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
            output = Path(directory) / "output"
            result = BUILD.build(output, root=source)
            with zipfile.ZipFile(output / result["archive"]) as archive:
                archive.extractall(Path(directory) / "extracted")
                prefix = "dev-harness-v" + result["version"] + "/"
                manifest = json.loads(archive.read(prefix + "MANIFEST.json"))
                for name in destinations:
                    data = archive.read(prefix + name)
                    self.assertEqual(hashlib.sha256(data).hexdigest(), manifest["files"][name]["sha256"])

    @unittest.skipUnless(symlinks_supported(), "creating symlinks needs Developer Mode or elevation on Windows")
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
