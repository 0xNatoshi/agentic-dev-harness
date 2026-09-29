"""The documented merge path covers the server-generated merge author (#21)."""

from pathlib import Path
import re
import unittest


SKILL_DIR = Path(__file__).resolve().parents[1] / "skills/github-workflow"
SKILL = SKILL_DIR / "SKILL.md"
GUARDED_EMAIL = '--author-email "${workflow_author_email:?'
PREFLIGHT = "workflow_author_email=$(bash <skill-dir>/scripts/merge-preflight.sh identity "
# Any documented way to merge a PR or its branch: the gh command, the REST endpoints or the GraphQL mutations.
MERGE_SURFACE = re.compile(r"gh\s+pr\s+merge\b[^`\n]*|pulls/[^/\s`]+/merge\b|repos/[^\s`]+/merges\b"
                           r"|mergePullRequest|mergeBranch|enqueuePullRequest")
IDENTITY_CALL = re.compile(r"identity \S+ \S+ (\S+) \S+ (\S+)\) \|\| exit")
MERGE_PR = re.compile(r"gh pr merge --repo \S+ (\S+) ")


def section(text: str, heading: str) -> str:
    start = text.index(heading)
    following = re.compile(r"^#{1,3} ", re.MULTILINE).search(text, start + len(heading))
    return text[start:following.start() if following else len(text)]


class SkillIdentityContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SKILL.read_text(encoding="utf-8")

    def test_local_identity_is_not_server_author_evidence(self) -> None:
        rule = next(line for line in self.text.splitlines() if "git var GIT_AUTHOR_IDENT" in line)
        self.assertIn("never covers the server-generated squash/merge commit", rule)

    def test_identity_preflight_precedes_pinned_merge(self) -> None:
        merge = section(self.text, "### `merge <pr>`")
        merge_lines = [line for line in merge.splitlines() if line.startswith("gh pr merge ")]
        self.assertEqual(len(merge_lines), 1)
        line = merge_lines[0]
        self.assertIn("--match-head-commit <headRefOid>", line)
        self.assertIn(GUARDED_EMAIL, line)
        for forbidden in ("--auto", "--admin"):
            self.assertNotIn(forbidden, line)
        preflight = merge.index("workflow_author_email=$(bash <skill-dir>/scripts/merge-preflight.sh identity ")
        # Fail closed: an empty address must never reach gh pr merge.
        self.assertTrue(merge[preflight:].splitlines()[0].endswith(") || exit"))
        self.assertLess(preflight, merge.index(line))

    def test_every_skill_merge_command_is_identity_gated(self) -> None:
        # Templates are exported project text owned by the template family (#15), not skill procedure.
        commands = []
        for path in sorted(SKILL_DIR.rglob("*.md")):
            if "templates" in path.relative_to(SKILL_DIR).parts:
                continue
            text = path.read_text(encoding="utf-8")
            for found in MERGE_SURFACE.finditer(text):
                command = found.group()
                commands.append(command)
                with self.subTest(path=path.name, command=command):
                    # The REST and GraphQL merges have no gated form: only gh pr merge may be documented.
                    self.assertTrue(re.match(r"gh pr merge ", command))
                    # An empty address must stop the merge: gh drops it and uses the account default.
                    self.assertIn(GUARDED_EMAIL, command)
                    self.assertIn("--match-head-commit ", command)
                    for forbidden in ("--auto", "--admin"):
                        self.assertNotIn(forbidden, command)
                    # The preflight belongs to the same block, not merely somewhere earlier in the file.
                    preflight = text.rfind(PREFLIGHT, 0, found.start())
                    self.assertNotEqual(preflight, -1)
                    between = text[preflight:found.start()]
                    self.assertNotIn("\n\n", between)
                    self.assertNotIn("```", between)
                    # Both commands name the same PR and merge method, so identity checks the merge that runs.
                    # The repository is bound to origin by the preflight in both, so it is not compared here.
                    identity = IDENTITY_CALL.search(between)
                    self.assertIsNotNone(identity)
                    self.assertEqual(MERGE_PR.match(command).group(1), identity.group(1))
                    self.assertIn(" --" + identity.group(2) + " ", command)
        self.assertGreaterEqual(len(commands), 2)

    def test_identity_exit_codes_block_merge(self) -> None:
        merge = section(self.text, "### `merge <pr>`")
        self.assertIn("Exit 1 is an outstanding user decision", merge)
        self.assertIn("Exit 2 blocks", merge)

    def test_published_identity_is_verified_after_merge(self) -> None:
        verify = section(self.text, "### Verify after merge")
        self.assertIn("bash <skill-dir>/scripts/merge-preflight.sh published <owner> <repo> <pr> <handle>", verify)


if __name__ == "__main__":
    unittest.main()
