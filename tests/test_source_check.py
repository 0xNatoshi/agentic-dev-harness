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
        for address in ["@".join(["first.last", "personal-mail.net"]), "@".join(["noreply", "vanity-name.me"]),
                        "_" + "@".join(["first.last", "personal-mail.net"]) + "_",
                        "@".join(['"first.last"', "personal-mail.net"]),
                        "@".join(["first!", "personal-mail.net"]), "@".join(["first=", "personal-mail.net"]),
                        "`" + "@".join(["first.last", "personal-mail.net"]) + "`",
                        "@".join(["!", "personal-mail.net"]), "@".join(["{|}", "personal-mail.net"]),
                        "@".join(['"first\\""', "personal-mail.net"]), "@".join(["<me>", "personal-mail.net"]),
                        "_" + "@".join(["noreply", "anthropic.com"]), "@".join(["!git", "github.com"]),
                        "@".join(["first`", "personal-mail.net"]), "@".join(["`first`", "personal-mail.net"]),
                        "@".join(["`", "personal-mail.net"]), "@".join(["*git", "github.com"]),
                        "@".join(["~noreply", "anthropic.com"]), "@".join(["`git", "github.com"]),
                        "@".join(["'git", "github.com"]), "@".join(["{git", "github.com"]),
                        "*" + "@".join(["", "personal-mail.net"]) + "*",
                        "`" + "@".join(["", "personal-mail.net"]) + "`",
                        "@".join(["person", "example.com2"]), "@".join(["noreply", "anthropic.com-evil"]),
                        "@".join(["first", "ex\u00e4mple.de"]), "@".join(["noreply", "anthropic.c\u00f6m"]),
                        "@".join(["user", "123.456.com"]), "@".join(["user", "1.2.3-evil.com"]),
                        "@".join(["first", "\u0909\u0926\u093e\u0939\u0930\u0923.\u092d\u093e\u0930\u0924"]),
                        "@".join(["first", "personal-mail\u3002net"]), "@".join(["first", "example.com.personal-mail.net"]),
                        "@".join(["person", "privatehost"]), "@".join(["person", "[192.168.1.10]"]),
                        "@".join(["person", "[IPv6:2001:db8::1]"]), "@".join(["person", "ci-runner-7"]),
                        "https://" + "@".join(["person", "privatehost"]),
                        "@".join(["codex2", "openai.com"]), "@".join(["x-codex", "openai.com"]),
                        "@".join(["codex", "mail.openai.com"]), "@".join(["codex", "openai.com.evil"]),
                        "@".join(["codex", "openai.co"]), "~" + "@".join(["codex", "openai.com"]),
                        # An @ inside a quoted local part is still a separator, so a
                        # single-label host cannot hide there (conservative).
                        "@".join(['"first', 'privatehost"', "example.com"]),
                        "@".join(["image", "sha256:" + "0" * 63]), "@".join(["image", "sha512:" + "0" * 64]),
                        # URL user info is screened like any local part.
                        "ssh://" + "@".join(["person", "privatehost"]) + "/owner/repo.git",
                        "ssh://" + "@".join(["git", "github.com.evil"]) + "/owner/repo.git",
                        "ssh://" + "@".join(["person", "github.com"]) + "/owner/repo.git",
                        "https://" + "@".join(["person", "github.com"]) + "/owner/repo.git",
                        "ssh://" + "@".join(["//git", "github.com"]) + "/owner/repo.git",
                        "@".join(["//git", "github.com"]) + "/owner/repo.git"]:
            with self.subTest(address=address):
                self.check_rejected(address)

    def check_rejected(self, address):
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
            "Claude <" + "@".join(["noreply", "anthropic.com"]) + ">",
            "Co-authored-by: Codex <" + "@".join(["codex", "openai.com"]) + ">",
            "@".join(["git", "github.com"]) + ":owner/repo.git",
            "`" + "@".join(["noreply", "anthropic.com"]) + "`",
            "**" + "@".join(["git", "github.com"]) + "**",
            "imports `@AGENTS.md` and `@../.codex/AGENTS.md`",
            "_" + "@".join(["noreply", "anthropic.com"]) + "_",
            "~~" + "@".join(["noreply", "anthropic.com"]) + "~~",
            "{'" + "@".join(["noreply", "anthropic.com"]) + "'}",
            "mailto:" + "@".join(["noreply", "anthropic.com"]),
            "actions/checkout@v4.2.2",
            "package@1.2.3",
            "package@v1.2.3-beta.1",
            "package@4.2",
            "actions/checkout@v5",
            "@".join(["actions/checkout", "main"]),
            "@".join(["uses: owner/repo/.github/workflows/ci.yml", "main"]),
            "@".join(["npx package", "latest"]),
            "actions/checkout@" + "0123456789abcdef" * 2 + "01234567",
            "@".join(["fixture", "localhost"]),
            "\"@codex review\" `@codex review` '@codex'",
            "HEAD@{1} and main@{upstream}",
            "@".join(["fixture", "example.net"]),
            "@".join(["fixture", "mail.example.org"]),
            "@".join(["fixture", "host.test"]),
            "@".join(["fixture", "docs.example"]),
            "@".join(["fixture", "machine.localhost"]),
            "Write to " + "@".join(["noreply", "anthropic.com"]) + ".",
            "@".join(["FROM ubuntu", "sha256:" + "0123456789abcdef" * 4]),
            "@".join(["image", "sha512:" + "0123456789abcdef" * 8]),
            "ssh://" + "@".join(["git", "github.com"]) + "/owner/repo.git",
            "`ssh://" + "@".join(["git", "github.com"]) + "/owner/repo.git`",
            "\"ssh://" + "@".join(["git", "github.com"]) + "/owner/repo.git\"",
            "[clone](ssh://" + "@".join(["git", "github.com"]) + "/owner/repo.git)",
            "ssh://" + "@".join(["git", "github.com"]) + ":22/owner/repo.git",
            "git+ssh://" + "@".join(["git", "github.com"]) + "/owner/repo.git",
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
