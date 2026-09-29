"""The documented merge path covers the server-generated merge author (#21)."""

from pathlib import Path
import re
import unittest


SKILL = Path(__file__).resolve().parents[1] / "skills/github-workflow/SKILL.md"


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
        self.assertIn('--author-email "$workflow_author_email"', line)
        for forbidden in ("--auto", "--admin"):
            self.assertNotIn(forbidden, line)
        preflight = merge.index("workflow_author_email=$(bash <skill-dir>/scripts/merge-preflight.sh identity ")
        # Fail closed: an empty address must never reach gh pr merge.
        self.assertTrue(merge[preflight:].splitlines()[0].endswith(") || exit"))
        self.assertLess(preflight, merge.index(line))

    def test_identity_exit_codes_block_merge(self) -> None:
        merge = section(self.text, "### `merge <pr>`")
        self.assertIn("Exit 1 is an outstanding user decision", merge)
        self.assertIn("Exit 2 blocks", merge)

    def test_published_identity_is_verified_after_merge(self) -> None:
        verify = section(self.text, "### Verify after merge")
        self.assertIn("bash <skill-dir>/scripts/merge-preflight.sh published <owner> <repo> <pr> <handle>", verify)


if __name__ == "__main__":
    unittest.main()
