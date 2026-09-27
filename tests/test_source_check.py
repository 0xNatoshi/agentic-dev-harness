"""Keep project merge authorization separate from distributable profile checks."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from tests._fixture_support import fixture_environment, run
from tests.test_merge_preflight import FAKE_GH

ROOT = Path(__file__).resolve().parents[1]
HOLD = "\nAutonomous merge suspended — request dated 2026-09-27\n"


class SourceCheckTests(unittest.TestCase):
    def copy_source(self, directory):
        source = Path(directory) / "source"
        shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", "dist", "__pycache__", "TASKS.md"))
        return source

    def test_project_hold_allows_source_gate_but_still_blocks_merge(self):
        with tempfile.TemporaryDirectory(prefix="harness-project-hold-") as directory:
            root = Path(directory)
            source = self.copy_source(root)
            instructions = source / "AGENTS.md"
            instructions.write_text(instructions.read_text(encoding="utf-8") + HOLD, encoding="utf-8")
            environment, tools = fixture_environment(root)
            check = run([sys.executable, "scripts/check.py"], source, environment)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            fake_gh = tools / "gh"
            fake_gh.write_text(FAKE_GH, encoding="utf-8")
            fake_gh.chmod(0o700)
            environment["FIXTURE_LOG"] = str(root / "gh.log")
            for args in [
                ["init", "-b", "main"],
                ["remote", "add", "origin", "https://github.example/fixture/repo.git"],
                ["add", "AGENTS.md"],
                ["commit", "-m", "fixture project hold"],
                ["update-ref", "refs/remotes/origin/main", "HEAD"],
            ]:
                result = run(["git", *args], source, environment)
                self.assertEqual(result.returncode, 0, result.stderr)
            preflight = run(
                ["bash", "skills/github-workflow/scripts/merge-preflight.sh", "suspension", "AGENTS.md"],
                source,
                environment,
            )
            self.assertEqual(preflight.returncode, 1, preflight.stdout + preflight.stderr)

    def test_exported_profile_rejects_project_specific_hold(self):
        with tempfile.TemporaryDirectory(prefix="harness-profile-hold-") as directory:
            root = Path(directory)
            source = self.copy_source(root)
            profile = source / "profiles/AGENTS.template.md"
            profile.write_text(profile.read_text(encoding="utf-8") + HOLD, encoding="utf-8")
            environment, _ = fixture_environment(root)
            result = run([sys.executable, "scripts/check.py"], source, environment)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Unexpected suspension in source profile: profiles/AGENTS.template.md", result.stderr)

    def test_source_gate_rejects_personal_email_without_echoing_it(self):
        # Built at runtime so this file itself stays free of the pattern.
        address = "@".join(["first.last", "personal-mail.net"])
        with tempfile.TemporaryDirectory(prefix="harness-profile-email-") as directory:
            root = Path(directory)
            source = self.copy_source(root)
            profile = source / "profiles/AGENTS.template.md"
            text = profile.read_text(encoding="utf-8")
            profile.write_text(text + f"\nContact: {address}\n", encoding="utf-8")
            line = len((text + "\nContact:").splitlines())
            environment, _ = fixture_environment(root)
            result = run([sys.executable, "scripts/check.py"], source, environment)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f"Email address outside the neutral allowlist: profiles/AGENTS.template.md:{line}", result.stderr)
            self.assertNotIn(address, result.stdout + result.stderr)

    def test_source_gate_accepts_neutral_email_forms(self):
        addresses = [
            "@".join(["fixture", "example.invalid"]),
            "@".join(["<id>+<login>", "users.noreply.github.com"]),
            "@".join(["noreply", "service.example.net"]),
        ]
        with tempfile.TemporaryDirectory(prefix="harness-neutral-email-") as directory:
            root = Path(directory)
            source = self.copy_source(root)
            profile = source / "profiles/AGENTS.template.md"
            profile.write_text(profile.read_text(encoding="utf-8") + "\n" + " ".join(addresses) + "\n", encoding="utf-8")
            environment, _ = fixture_environment(root)
            result = run([sys.executable, "scripts/check.py"], source, environment)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
