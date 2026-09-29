"""The documented merge path covers the server-generated merge author (#21)."""

from pathlib import Path
import re
import unittest


SKILL_DIR = Path(__file__).resolve().parents[1] / "skills/github-workflow"
SKILL = SKILL_DIR / "SKILL.md"
GUARDED_EMAIL = '--author-email "${workflow_author_email:?'
PREFLIGHT = "workflow_author_email=$(bash <skill-dir>/scripts/merge-preflight.sh identity "


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
            for found in re.finditer(r"gh pr merge [^`\n]*", text):
                commands.append(found.group())
                with self.subTest(path=path.name, command=found.group()):
                    # An empty address must stop the merge: gh drops it and uses the account default.
                    self.assertIn(GUARDED_EMAIL, found.group())
                    self.assertIn("--match-head-commit ", found.group())
                    for forbidden in ("--auto", "--admin"):
                        self.assertNotIn(forbidden, found.group())
                    self.assertLess(text.find(PREFLIGHT), found.start())
                    self.assertNotEqual(text.find(PREFLIGHT), -1)
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
