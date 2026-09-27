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

    def append_workflow_examples(self, source, relative, commands):
        path = source / relative
        original = path.read_text(encoding="utf-8")
        opening = "\n```bash\n"
        first_line = len((original + opening).splitlines()) + 1
        path.write_text(original + opening + "\n".join(commands) + "\n```\n", encoding="utf-8")
        return [f"{relative}:{first_line + offset}:" for offset in range(len(commands))]

    def test_source_gate_accepts_healthy_workflow_guidance(self):
        with tempfile.TemporaryDirectory(prefix="harness-workflow-healthy-") as directory:
            root = Path(directory)
            source = self.copy_source(root)
            environment, _ = fixture_environment(root)
            result = run([sys.executable, "scripts/check.py"], source, environment)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('"source_checks": "passed"', result.stdout)

    def test_source_gate_rejects_each_unsafe_workflow_command(self):
        cases = [
            ('gh pr view --repo "$workflow_host/$workflow_repo" --json body', "gh pr view needs an explicit PR selector"),
            ('gh run view --repo "$workflow_host/$workflow_repo" --log-failed', "gh run view needs an explicit run ID"),
            ('gh issue view <n>', "gh issue view needs a valued --repo"),
            ('gh repo view --json nameWithOwner', "gh repo view needs an explicit repository"),
            ('gh repo edit --description text', "gh repo edit needs an explicit repository"),
            ('gh pr merge --repo "$workflow_host/$workflow_repo" "$workflow_pr" --match-head-commit', "--match-head-commit needs a commit value"),
            ("python3 -c 'print(1)'", "direct python3 invocation"),
            ("python3 - <<'PY'", "direct python3 invocation"),
            ("python3 scripts/check.py", "direct python3 invocation"),
        ]
        relative = "skills/github-workflow/references/development-loop.md"
        for command, message in cases:
            with self.subTest(command=command), tempfile.TemporaryDirectory(prefix="harness-workflow-command-") as directory:
                root = Path(directory)
                source = self.copy_source(root)
                location, = self.append_workflow_examples(source, relative, [command])
                environment, _ = fixture_environment(root)
                result = run([sys.executable, "scripts/check.py"], source, environment)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(location + " " + message, result.stderr)
                self.assertNotIn('"source_checks": "passed"', result.stdout + result.stderr)

    def test_source_gate_rejects_commands_after_literal_hash_and_comment_newline(self):
        relative = "skills/github-workflow/references/development-loop.md"
        with tempfile.TemporaryDirectory(prefix="harness-workflow-comment-boundary-") as directory:
            root = Path(directory)
            source = self.copy_source(root)
            locations = self.append_workflow_examples(source, relative, [
                "printf '%s\\n' x#word; gh pr view --repo \"$workflow_host/$workflow_repo\"",
                "printf '%s\\n' x # comment \\",
                'gh pr view --repo "$workflow_host/$workflow_repo"',
                "printf $'x\\' # text'; gh pr view --repo \"$workflow_host/$workflow_repo\"",
                "printf '%s\\n' x" + chr(0xA0) + '#word; gh pr view --repo "$workflow_host/$workflow_repo"',
                "printf '%s\\n' x" + chr(0x2028) + '#word; gh pr view --repo "$workflow_host/$workflow_repo"',
            ])
            environment, _ = fixture_environment(root)
            result = run([sys.executable, "scripts/check.py"], source, environment)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            message = " gh pr view needs an explicit PR selector"
            self.assertIn(locations[0] + message, result.stderr)
            self.assertIn(locations[2] + message, result.stderr)
            self.assertIn(locations[3] + " unsupported ANSI-C quote boundary", result.stderr)
            self.assertIn(locations[4] + message, result.stderr)
            self.assertIn(locations[5] + message, result.stderr)

    def test_source_gate_collects_workflow_findings_across_active_files(self):
        with tempfile.TemporaryDirectory(prefix="harness-workflow-collected-") as directory:
            root = Path(directory)
            source = self.copy_source(root)
            skill_location, = self.append_workflow_examples(
                source, "skills/github-workflow/SKILL.md", ["gh issue view <n>"],
            )
            reference_locations = self.append_workflow_examples(
                source, "skills/github-workflow/references/readme-guide.md",
                ["gh repo view --json nameWithOwner", "python3 scripts/check.py"],
            )
            environment, _ = fixture_environment(root)
            result = run([sys.executable, "scripts/check.py"], source, environment)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(skill_location + " gh issue view needs a valued --repo", result.stderr)
            self.assertIn(reference_locations[0] + " gh repo view needs an explicit repository", result.stderr)
            self.assertIn(reference_locations[1] + " direct python3 invocation", result.stderr)
            self.assertNotIn('"source_checks": "passed"', result.stdout + result.stderr)

    def test_source_gate_excludes_authentic_workflow_history(self):
        with tempfile.TemporaryDirectory(prefix="harness-workflow-history-") as directory:
            root = Path(directory)
            source = self.copy_source(root)
            self.append_workflow_examples(
                source, "skills/github-workflow/templates/history/AGENTS-v6.1.md", ["gh issue view <n>"],
            )
            environment, _ = fixture_environment(root)
            result = run([sys.executable, "scripts/check.py"], source, environment)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('"source_checks": "passed"', result.stdout)

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
                        "@".join(["//git", "github.com"]) + "/owner/repo.git",
                        # Only an all-numeric last label reads as a ref or tag.
                        "@".join(["owner/action", "feature.1a"]), "@".join(["person", "mail.example-host.co1"]),
                        "@".join(["person", "host.\u0661"]),
                        # A dotted alphabetic ref passes only as the value of a `uses:` key.
                        "@".join(["owner/action", "feature.one"]),
                        "@".join(["see uses: owner/action", "feature.one"]),
                        # A slash local part is a repository reference only before a default branch.
                        "@".join(["first/last", "privatehost"]), "@".join(["owner/action", "feature"]),
                        # Only a VCS URL path takes a revision; other URL paths and user info stay screened.
                        "https://github.com/" + "@".join(["owner/repo.git", "feature"]),
                        "https://lists.host.test/archive/" + "@".join(["person", "mail-host.co"]),
                        "git+https://" + "@".join(["person", "github.com"]) + "/owner/repo.git",
                        # A revision must be ref-shaped and follow a plain path.
                        "git+https://host.test/" + "@".join(["first.last", "personal-mail.net"]),
                        "git+https://host.test/owner/repo/issues?author=" + "@".join(["first.last", "personal-mail.net"]),
                        # A real domain followed by numbers, and a Punycode top-level domain.
                        "@".join(["first.last", "personal-mail.net.1"]), "@".join(["first", "sub.personal-mail.co.2"]),
                        "@".join(["person", "1-mail.xn--p1ai"]),
                        # Outside YAML a `uses:` line is prose.
                        "uses: " + "@".join(["owner/action", "feature.one"]),
                        # A form feed is not a line break, so the reported line stays exact.
                        "\x0c" + "@".join(["first.last", "personal-mail.net"])]:
            with self.subTest(address=address):
                self.check_rejected(address)

    def check_rejected(self, address):
        with tempfile.TemporaryDirectory(prefix="harness-profile-email-") as directory:
            root = Path(directory)
            source = self.copy_source(root)
            profile = source / "profiles/AGENTS.template.md"
            text = profile.read_text(encoding="utf-8")
            profile.write_text(text + f"\nContact: {address}\n", encoding="utf-8")
            line = len((text + "\nContact:").split("\n"))
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
            "@".join(["owner/action", "feature.1"]),
            "@".join(["npm install pkg", "beta.1"]),
            "@".join(["pkg", "rc.2.10"]),
        ]
        lines = [
            "git+https://github.com/" + "@".join(["owner/repo.git", "feature"]),
            "git+ssh://" + "@".join(["git", "github.com/owner/repo.git", "main"]),
        ]
        # Workflow lines: in YAML the whole value of a `uses:` key is an action reference.
        uses = [
            "  - uses: " + "@".join(["owner/action", "feature.one"]),
            "        uses: '" + "@".join(["owner/action", "release.candidate"]) + "'",
            "        uses: \"" + "@".join(["owner/action", "feature.one"]) + "\"",
            "    uses: " + "@".join(["owner/repo/.github/workflows/ci.yml", "feature.one"]),
            "    uses: " + "@".join(["owner/action", "feature"]),
        ]
        with tempfile.TemporaryDirectory(prefix="harness-neutral-email-") as directory:
            root = Path(directory)
            source = self.copy_source(root)
            profile = source / "profiles/AGENTS.template.md"
            profile.write_text(profile.read_text(encoding="utf-8") + "\n" + " ".join(addresses) + "\n" + "\n".join(lines) + "\n",
                               encoding="utf-8")
            (source / ".github/workflows/fixture-uses.yml").write_text("jobs:\n  steps:\n" + "\n".join(uses) + "\n", encoding="utf-8")
            environment, _ = fixture_environment(root)
            result = run([sys.executable, "scripts/check.py"], source, environment)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_source_gate_accepts_python_import_content_without_syntax_quotes(self):
        imported = "@" + "AGENTS.md"
        prefixes = ("", "r", "u", "b", "br", "rb", "f", "fr", "rf")
        if sys.version_info >= (3, 14):
            prefixes += ("t", "tr", "rt", "T", "Tr", "rT")
        snippets = [
            "value = " + prefix + quote + imported + quote
            for prefix in prefixes
            for quote in ("'", '"', "'''", '"""')
        ]
        snippets.extend("value = " + quote + imported + "\ntext\n" + quote for quote in ("'''", '"""'))
        snippets.append('target.write_text(f"' + imported + r'\n\n```bash\n{command}\n```\n", encoding="utf-8")')
        with tempfile.TemporaryDirectory(prefix="harness-python-import-") as directory:
            root = Path(directory)
            source = self.copy_source(root)
            (source / "tests/fixture_imports.py").write_text("\n".join(snippets) + "\n", encoding="utf-8")
            environment, _ = fixture_environment(root)
            result = run([sys.executable, "scripts/check.py"], source, environment)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('"source_checks": "passed"', result.stdout)

    def test_python_literal_content_remains_screened_with_physical_lines(self):
        address = "@".join(["first.last", "personal-mail.net"])
        quoted = "@".join(['"first.last"', "personal-mail.net"])
        imported = "@" + "AGENTS.md"
        cases = [
            ("value = " + repr(address) + "\n", 2),
            ("value = r" + repr(address) + "\n", 2),
            ("value = " + repr(quoted) + "\n", 2),
            ("value = f" + repr("{name} " + address) + "\n", 2),
            ("# " + address + "\n", 2),
            ('value = """' + imported + "\n" + address + '\n"""\n', 3),
            ('value = """Documentation\n' + address, 3),
        ]
        if sys.version_info >= (3, 14):
            cases.extend([
                ("value = t" + repr(address) + "\n", 2),
                ("value = tr" + repr(quoted) + "\n", 2),
                ("value = t" + repr("{name} " + address) + "\n", 2),
                ('value = t"""' + imported + "\n" + address + '\n"""\n', 3),
            ])
        for snippet, line in cases:
            with self.subTest(snippet=snippet), tempfile.TemporaryDirectory(prefix="harness-python-content-") as directory:
                root = Path(directory)
                source = self.copy_source(root)
                (source / "tests/fixture_content.py").write_text("import os\n" + snippet, encoding="utf-8")
                environment, _ = fixture_environment(root)
                result = run([sys.executable, "scripts/check.py"], source, environment)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"Email address outside the neutral allowlist: tests/fixture_content.py:{line}", result.stderr)
                self.assertNotIn(address, result.stdout + result.stderr)
                self.assertNotIn(quoted, result.stdout + result.stderr)
                self.assertNotIn('"source_checks": "passed"', result.stdout)

    def test_source_gate_reads_python_strings_and_comments_only(self):
        # Matrix multiplication and decorators are operators; strings and comments are screened.
        clean = "@".join(["result = matrix", "vector"]) + "\n\n\n" + "@".join(["", "decorator"]) + "\ndef f():\n    pass\n"
        with tempfile.TemporaryDirectory(prefix="harness-python-email-") as directory:
            root = Path(directory)
            source = self.copy_source(root)
            (source / "tests/fixture_operators.py").write_text(clean, encoding="utf-8")
            environment, _ = fixture_environment(root)
            result = run([sys.executable, "scripts/check.py"], source, environment)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        address = "@".join(["first.last", "personal-mail.net"])
        for text in [f"CONTACT = '{address}'\n", f"x = 1  # {address}\n", f'"""Docs\n{address}\n"""\n',
                     f"value = f'{{x}} {address}'\n"]:
            with self.subTest(text=text), tempfile.TemporaryDirectory(prefix="harness-python-email-") as directory:
                root = Path(directory)
                source = self.copy_source(root)
                (source / "tests/fixture_operators.py").write_text("import os\n" + text, encoding="utf-8")
                environment, _ = fixture_environment(root)
                result = run([sys.executable, "scripts/check.py"], source, environment)
                self.assertNotEqual(result.returncode, 0)
                line = 3 if text.startswith('"""') else 2
                self.assertIn(f"Email address outside the neutral allowlist: tests/fixture_operators.py:{line}", result.stderr)
                self.assertNotIn(address, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
