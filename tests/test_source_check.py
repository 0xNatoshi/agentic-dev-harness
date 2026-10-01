"""Keep project merge authorization separate from distributable profile checks."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from scripts.check import check_emails

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

    def test_source_gate_reports_invalid_utf8_without_echoing_source(self):
        marker = b"private-content-must-not-appear"
        # Metadata consumers and local module imports must not run before the screen.
        for relative in [
            "VERSION", "package-files.json", "project.json", "README.md",
            "profiles/AGENTS.template.md", "docs/fixture-encoding.txt",
            "scripts/build.py", "scripts/install.py", "scripts/check_workflow_commands.py",
        ]:
            with self.subTest(path=relative), tempfile.TemporaryDirectory(prefix="harness-source-encoding-") as directory:
                root = Path(directory)
                source = self.copy_source(root)
                path = source / relative
                original = path.read_bytes() if path.exists() else b""
                line = original.count(b"\n") + 2
                path.write_bytes(original + b"\n" + marker + b"\xff\n")
                environment, _ = fixture_environment(root)
                result = run([sys.executable, "scripts/check.py"], source, environment)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(result.stderr, f"Source file is not UTF-8: {relative}:{line}\n")
                self.assertEqual(result.stdout, "")
                self.assertNotIn(marker.decode("ascii"), result.stderr)

    def test_source_gate_rejects_address_shaped_version_pins(self):
        for domain in ["1-beta.com", "1-mail.xn--p1ai", "1-beta.mail.com.2",
                       "1-mail.xn--p1ai.2", "1-beta.co1", "1.2.3-alpha.beta.1"]:
            for local in ["person", "git+https://host.test/owner/repo.git"]:
                with self.subTest(domain=domain, local=local), tempfile.TemporaryDirectory(prefix="harness-pin-address-") as directory:
                    root = Path(directory)
                    source = self.copy_source(root)
                    reference = "@".join([local, domain])
                    (source / "fixture-pins.txt").write_text(reference + "\n", encoding="utf-8")
                    environment, _ = fixture_environment(root)
                    result = run([sys.executable, "scripts/check.py"], source, environment)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("Email address outside the neutral allowlist: fixture-pins.txt:1", result.stderr)
                    self.assertNotIn(reference, result.stdout + result.stderr)
                    self.assertNotIn(domain, result.stdout + result.stderr)
                    self.assertNotIn('"source_checks": "passed"', result.stdout)

    def test_source_gate_accepts_version_shaped_vcs_revisions(self):
        pins = ["1.2.3", "v1.2.3-beta.1", "1.2.3-beta", "v1.2.3-alpha-beta.1",
                "feature.1", "0123456789abcdef" * 2 + "01234567"]
        revisions = ["main", "feature", *pins]
        references = ["git+https://host.test/" + "@".join(["owner/repo.git", revision]) for revision in revisions]
        references.extend("@".join(["package", pin]) for pin in pins)
        references.append("@".join(["first/last", "main"]))
        with tempfile.TemporaryDirectory(prefix="harness-vcs-version-") as directory:
            root = Path(directory)
            source = self.copy_source(root)
            (source / "fixture-vcs.txt").write_text("\n".join(references) + "\n", encoding="utf-8")
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
                "cat <<'EOF'" + chr(0xA0), "EOF" + chr(0xA0),
                'gh pr view --repo "$workflow_host/$workflow_repo"', "cat <<'EOF'", "EOF",
                "printf '%s\\n' <(printf x)#word; gh pr view --repo \"$workflow_host/$workflow_repo\"",
                "printf '%s\\n' >(cat)#word; gh pr view --repo \"$workflow_host/$workflow_repo\"",
                "printf '%s\\n' @(x)#word; gh pr view --repo \"$workflow_host/$workflow_repo\"",
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
            self.assertIn(locations[6] + " unsupported here-document header", result.stderr)
            self.assertIn(locations[8] + message, result.stderr)
            self.assertIn(locations[11] + " unsupported process substitution boundary", result.stderr)
            self.assertIn(locations[12] + " unsupported process substitution boundary", result.stderr)
            self.assertIn(locations[13] + " unsupported extended glob boundary", result.stderr)

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
                        # A dotted alphabetic ref stays screened, including uses-looking prose.
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

    def test_documented_email_residuals_keep_their_explicit_boundary(self):
        rows = [
            ("named yaml action ref", "fixture.yml", "uses: " + "@".join(["first/last", "private.mail"]), False),
            ("quoted named yaml action ref", "fixture.yml", 'uses: "' + "@".join(["first/last", "private.mail"]) + '"', False),
            *[("named yaml ref " + ref, "fixture.yml", "uses: " + "@".join(["owner/action", ref]), False) for ref in ["feature", "feature.one", "release.candidate", "release-1.x", "v1.x"]],
            ("YAML full commit", "fixture.yml", "uses: " + "@".join(["owner/action", "0123456789abcdef" * 4]), True),
            ("YAML numeric version", "fixture.yml", "uses: " + "@".join(["owner/action", "v5"]), True),
            ("YAML numbered ref", "fixture.yml", "uses: " + "@".join(["owner/action", "feature.1"]), True),
            ("underscore numbered ref", "fixture.yml", "uses: " + "@".join(["owner/action", "feature_foo.1"]), False),
            ("raw IPv4 version ambiguity", "fixture.txt", "@".join(["person", "10.0.0.1"]), True),
            ("latest dist-tag ambiguity", "fixture.txt", "@".join(["person", "latest"]), True),
            ("next dist-tag ambiguity", "fixture.txt", "@".join(["person", "next"]), True),
            ("default-branch slash ambiguity", "fixture.txt", "@".join(["first/last", "main"]), True),
            ("dotless VCS revision", "fixture.txt", "git+https://host.test/" + "@".join(["owner/repo.git", "feature"]), True),
            *[("other npm tag " + tag, "fixture.txt", "@".join(["pkg", tag]), False) for tag in ["beta", "canary", "rc"]],
            ("scoped stable tag", "fixture.txt", "@".join(["", "scope/pkg", "stable"]), False),
            ("short commit", "fixture.txt", "@".join(["actions/checkout", "1a2b3c4"]), False),
            ("prose release ref", "fixture.txt", "@".join(["owner/repo", "release-1.x"]), False),
            ("markdown uses ref", "fixture.md", "uses: " + "@".join(["owner/action", "feature.one"]), False),
            ("non-allowlisted Git host", "fixture.txt", "@".join(["git", "gitlab.com"]) + ":owner/repo.git", False),
            ("URL user info", "fixture.txt", "https://x-access-token:${GITHUB_TOKEN}" + "@" + "github.com/owner/repo.git", False),
            ("SSH mail host", "fixture.txt", "ssh " + "@".join(["user", "server"]), False),
            ("unlisted bot trailer", "fixture.txt", "Signed-off-by: Bot <" + "@".join(["bot", "private.mail"]) + ">", False),
            *[("dotted VCS ref " + ref, "fixture.txt", "git+https://host.test/" + "@".join(["owner/repo.git", ref]), False) for ref in ["feature.one", "release-1.x"]],
            ("Bash transformation", "fixture.txt", "${value" + "@Q}", False),
        ]
        for label, relative, text, accepted in rows:
            with self.subTest(case=label):
                if accepted:
                    check_emails(relative, text)
                else:
                    with self.assertRaisesRegex(ValueError, "Email address outside the neutral allowlist: " + relative.replace(".", r"\.") + ":1"):
                        check_emails(relative, text)

    def test_source_gate_rejects_named_yaml_refs_in_values_and_scalars(self):
        reference = "@".join(["first/last", "private.mail"])
        cases = [
            ("actual key", "uses: " + reference + "\n", 1),
            ("quoted key value", 'uses: "' + reference + '"\n', 1),
            ("single-quoted key value", "uses: '" + reference + "'\n", 1),
            ("literal scalar", "run: |\n  uses: " + reference + "\n", 2),
            ("indented chomping scalar", "run: |2-\n  uses: " + reference + "\n", 2),
            ("folded scalar", "description: >-\n  uses: " + reference + "\n", 2),
            ("quoted multiline scalar", 'description: "text\n  uses: ' + reference + '\n"\n', 2),
            ("long prefix", "run:" + " " * 300 + "uses: " + reference + "\n", 1),
            ("non-YAML whitespace", "\x0cuses: " + reference + "\n", 1),
        ]
        for label, text, line in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory(prefix="harness-yaml-ref-") as directory:
                root = Path(directory)
                source = self.copy_source(root)
                relative = ".github/workflows/fixture-uses.yml"
                (source / relative).write_text(text, encoding="utf-8", newline="")
                environment, _ = fixture_environment(root)
                result = run([sys.executable, "scripts/check.py"], source, environment)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"Email address outside the neutral allowlist: {relative}:{line}", result.stderr)
                self.assertNotIn(reference, result.stdout + result.stderr)

    def test_source_gate_rejects_backtick_local_parts_before_markdown_domains(self):
        for domain in ["private.md", "mail.private.md", "private\u3002md"]:
            with self.subTest(domain=domain):
                self.check_rejected(chr(96) + "@" + domain + chr(96))

    def test_source_gate_accepts_neutral_email_forms(self):
        addresses = [
            "@".join(["fixture", "example.invalid"]),
            "@".join(["<id>+<login>", "users.noreply.github.com"]),
            "Claude <" + "@".join(["noreply", "anthropic.com"]) + ">",
            "Co-authored-by: Codex <" + "@".join(["codex", "openai.com"]) + ">",
            "@".join(["git", "github.com"]) + ":owner/repo.git",
            "`" + "@".join(["noreply", "anthropic.com"]) + "`",
            "**" + "@".join(["git", "github.com"]) + "**",
            "imports @AGENTS.md and `@./AGENTS.md` and `@../.codex/AGENTS.md`",
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
        # Workflow pins use the same independently verifiable shapes as prose.
        uses = [
            "  - uses: " + "@".join(["owner/action", "0123456789abcdef" * 2 + "01234567"]),
            "        uses: '" + "@".join(["owner/action", "v4.2.2"]) + "'",
            "        uses: \"" + "@".join(["owner/action", "v5"]) + "\"",
            "    uses: " + "@".join(["owner/repo/.github/workflows/ci.yml", "main"]),
            "    uses: " + "@".join(["owner/action", "feature.1"]),
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
