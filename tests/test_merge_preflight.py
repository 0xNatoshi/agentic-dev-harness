"""Merge routing, suspension and Pages fixtures against the source skill."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unicodedata
import unittest

from tests._fixture_support import fixture_environment, run


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "skills/github-workflow/scripts/merge-preflight.sh"

FAKE_GH = """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with Path(os.environ['FIXTURE_LOG']).open('a') as log:
    log.write(json.dumps(args) + '\\n')
if args[:2] == ['repo', 'view']:
    explicit = len(args) > 2 and args[2] != '--json'
    if explicit and os.getenv('FIXTURE_MISSING') == '1':
        sys.exit(1)
    full = 'fixture/repo' if explicit else os.getenv('FIXTURE_SELECTED', 'fixture/repo')
    print(json.dumps({
        'nameWithOwner': full,
        'url': 'https://github.example/' + full,
        'defaultBranchRef': {'name': 'main'},
    }))
    sys.exit(0)
if not args or args[0] != 'api' or '--repo' in args:
    sys.exit(90)
if '--hostname' not in args:
    sys.exit(91)
index = args.index('--hostname')
if args[index + 1] != 'github.example':
    sys.exit(92)
del args[index:index + 2]
if args[1] == 'graphql':
    print('false\\t0')
    sys.exit(0)
if args[1] != 'repos/fixture/repo/pages':
    sys.exit(93)
status = int(os.getenv('FIXTURE_HTTP', '404'))
print('HTTP/2.0 ' + str(status) + ' fixture\\n\\n{}')
sys.exit(0 if status == 200 else 1)
"""


@dataclass(frozen=True)
class Case:
    name: str
    expected: int
    text: str | None = None
    source: str | None = None
    mode: str = "suspension"
    environment: tuple[tuple[str, str], ...] = ()
    setup: str | None = None
    api_calls: int = 0


TEXT_CASES = (
    Case("clear", 0, "No suspension."),
    Case("canonical", 1, "Autonomous merge suspended — request dated 2026-09-27"),
    Case("uppercase ASCII dash", 1, "AUTONOMOUS MERGE SUSPENDED - REQUESTED ON 2026-09-27"),
    Case("BOM NBSP CRLF wrapped dashes", 1, "\ufeffAUTONOMOUS\u00a0MERGE\r\nSUSPENDED ‑ ASKED\r\nON 2026−09−27\r\n"),
    Case("French requested date", 1, "Merge autonome suspendu — DEMANDÉ LE 2026-09-27"),
    Case("canonical example", 0, "Autonomous merge suspended — request dated <date>"),
    Case("mixed example and real veto", 1, "Autonomous merge suspended — asked on <date>. Merge autonome suspendu — demandé le 2026-09-27"),
    Case("managed example", 0, "<!-- github-workflow:start v6.2 -->\nAutonomous merge suspended — asked on 2026-09-27\n<!-- github-workflow:end -->"),
    Case("inline start cannot hide veto", 1, "Example <!-- github-workflow:start v6.2 -->\nAutonomous merge suspended — asked on 2026-09-27"),
    Case("inline end cannot close block", 2, "<!-- github-workflow:start v6.2 -->\nExample <!-- github-workflow:end -->"),
    Case("free suspension", 2, "Autonomous merge suspended until a decision."),
    Case("free English wait", 2, "Do not merge until I confirm."),
    Case("free French wait", 2, "Pas de fusion avant mon accord."),
    Case("free hold", 2, "Merge is on hold."),
    Case("free plural approval", 2, "Merges require my approval."),
    Case("free no merge", 2, "No autonomous merge until I approve."),
    Case("free approval before merging", 2, "Please obtain my agreement before merging any PR."),
    Case("free disabled merges", 2, "Autonomous merges are disabled."),
    Case("free plural French suspension", 2, "Les merges sont suspendus."),
    Case("free approval first", 2, "Approval required before merging."),
    Case("free subject to consent", 2, "Merging is subject to my explicit consent."),
    Case("free French plural fusion", 2, "Fusions interdites jusqu’à mon accord."),
    Case("free French prior agreement", 2, "Mon accord est requis avant toute fusion."),
    Case("free before merge consent", 2, "Before merging, obtain my consent."),
    Case("free sign-off", 2, "Merging requires my sign-off."),
    Case("free green light", 2, "The agent must get my green light before merging."),
    Case("invalid date", 2, "Autonomous merge suspended — asked on 2026-99-99"),
    Case("start cannot swallow adjacent comments and veto", 2, "<!-- github-workflow:start v6.3 --> Autonomous merge suspended — request dated 2026-09-27 <!-- owner note -->\n<!-- github-workflow:end -->"),
    Case("start must be one complete comment", 2, "<!-- github-workflow:start v6.3 --> <!-- owner note -->\n<!-- github-workflow:end -->"),
    Case("orphan marker", 2, "<!-- github-workflow:end -->"),
    # Free-form restrictions per Markdown unit and invisible characters.
    Case("kept hold until I approve", 2, "Do not merge anything until I approve."),
    Case("kept bare never merge", 2, "Never merge."),
    Case("kept never without approval", 2, "Never merge without my approval."),
    Case("kept branch-scoped no merges", 2, "- No merges to main this week."),
    Case("kept wrapped bullet hold", 2, "- Do not merge\n  until I approve."),
    Case("kept upstream with hold", 2, "- Never merge from the upstream remote until I approve."),
    Case("kept upstream fork PRs", 2, "- Never merge PRs from the upstream fork."),
    Case("kept upstream contributors", 2, "Never merge anything from upstream contributors."),
    Case("kept squash-merge hold", 2, "Do not squash-merge anything until I confirm."),
    Case("kept rebase merges then wait", 2, "Never use rebase merges; wait for my approval."),
    Case("kept merge button", 2, "Never click the merge button."),
    Case("kept heading then hold", 2, "## Merge policy\n\nOn hold until further notice."),
    Case("kept bold lead then paused", 2, "**Merging**\n\nPaused until I approve."),
    Case("kept paragraph lead then item", 2, "Merges\n- on hold"),
    Case("kept parent item then child", 2, "- **Merging**\n  - paused"),
    Case("kept colon lead-in list", 2, "Do not do the following until I approve:\n- merge pull requests\n- deploy"),
    Case("kept indented paragraph under item", 2, "- **Merging**\n\n  Paused until I approve."),
    Case("kept heading path", 2, "## Merging\n\n### Details\n\nPaused."),
    Case("caught heading then wait for approval", 2, "## Merging\nWait for my approval."),
    Case("caught item then wait for approval", 2, "- Merging:\n  - wait for my approval"),
    Case("caught zero-width split veto", 1, "Autonomous merge sus\u200bpended — request dated 2026-09-27"),
    Case("caught soft hyphen split veto", 1, "Autonomous merge sus\u00adpended — request dated 2026-09-27"),
    Case("caught bidi override veto", 1, "Autonomous merge \u202esuspended\u202c — request dated 2026-09-27"),
    Case("caught zero-width split hold", 2, "Do not mer\u200bge until I approve."),
    Case("caught zero-width joined words veto", 1, "Autonomous\u200bmerge suspended — request dated 2026-09-27"),
    Case("caught example followed by date", 2, "Autonomous merge suspended — request dated <date> 2026-09-27"),
    Case("cleared upstream mechanics", 0, "- Never merge, rebase onto or cherry-pick wholesale from the upstream remote."),
    Case("cleared sibling items", 0, "- Do not add dependencies\n- Merge requests use squash"),
    Case("cleared lead with sibling items", 0, "Notes:\n- Do not add dependencies\n- Merge requests use squash"),
    Case("cleared conflict markers", 0, "Do not leave merge conflict markers in files."),
    Case("cleared merge strategies", 0, "No fast-forward merges; use merge commits."),
    Case("cleared squash not rebase", 0, "Always squash-merge; never use rebase merges."),
    Case("cleared merge commit run", 0, "With no run for the merge commit, inspect triggers."),
    Case("cleared sibling secrets and squash", 0, "- No secrets in commits\n- Merges use the squash method"),
    Case("cleared table rows", 0, "| Rule | Value |\n| --- | --- |\n| No secrets | always |\n| Merges | squash |"),
    Case("conservative blank-separated", 2, "Do not add dependencies\n\nMerge requests use squash"),
    Case("conservative heading", 2, "## Never commit secrets\nMerge PRs with squash."),
    Case("conservative comment", 2, "<!-- do not merge this block -->"),
)

# Frozen v6.3.1 fixture inputs. Python 3.11's Unicode database does not yet
# classify U+10D6E as Pd, so its conservative exit is 2 on that interpreter.
DASH_CODEPOINTS = (
    0x002D, 0x058A, 0x05BE, 0x1400, 0x1806, 0x2010, 0x2011, 0x2012,
    0x2013, 0x2014, 0x2015, 0x2E17, 0x2E1A, 0x2E3A, 0x2E3B, 0x2E40,
    0x2E5D, 0x301C, 0x3030, 0x30A0, 0xFE31, 0xFE32, 0xFE58, 0xFE63,
    0xFF0D, 0x10D6E, 0x10EAD,
)
DASH_CASES = tuple(
    Case(
        f"dash U+{point:04X}",
        1 if unicodedata.category(chr(point)) == "Pd" or point in (0x2212, 0x00AD) else 2,
        f"Autonomous merge suspended {chr(point)} asked on 2026-09-27",
    )
    for point in (*DASH_CODEPOINTS, 0x2212, 0x00AD)
)
SOURCE_CASES = tuple(
    Case("non-veto source " + source, 0, source=source)
    for source in (
        "profiles/AGENTS.template.md",
        "profiles/CLAUDE.template.md",
        "skills/github-workflow/templates/AGENTS.md",
    )
)
ROUTING_CASES = (
    Case("upstream selection blocks Pages", 2, mode="pages", environment=(("FIXTURE_SELECTED", "upstream/repo"),)),
    Case("missing repository blocks Pages", 2, mode="pages", environment=(("FIXTURE_MISSING", "1"),)),
    *(Case(f"Pages HTTP {status}", expected, mode="pages", environment=(("FIXTURE_HTTP", str(status)),), api_calls=1)
      for status, expected in ((404, 0), (200, 1), (403, 2), (500, 2))),
    Case("review routing", 0, mode="reviews", api_calls=1),
    Case("push/fetch mismatch", 2, mode="pages", setup="push_mismatch"),
    Case("published-only veto", 1, setup="published_wait"),
    Case("unreadable published ref", 2, setup="missing_published_ref"),
)
ALL_CASES = TEXT_CASES + DASH_CASES + SOURCE_CASES + ROUTING_CASES
if len(ALL_CASES) != 110 or len({case.name for case in ALL_CASES}) != 110:
    raise RuntimeError("Merge fixture inventory must contain 110 unique cases")


class MergePreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory(prefix="merge-preflight-fixture-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.environment, tools = fixture_environment(self.root)
        fake_gh = tools / "gh"
        fake_gh.write_text(FAKE_GH)
        fake_gh.chmod(0o700)
        self.log = self.root / "gh.log"
        self.environment["FIXTURE_LOG"] = str(self.log)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.instructions = self.repo / "AGENTS.md"
        self.instructions.write_text("Project instructions.\n")
        self.git("init", "-q", "-b", "main")
        self.git("remote", "add", "origin", "https://github.example/fixture/repo.git")
        self.git("add", "AGENTS.md")
        self.git("commit", "-qm", "base")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def git(self, *arguments: str) -> str:
        result = run(["git", *arguments], self.repo, self.environment)
        self.assertEqual(result.returncode, 0, f"git {arguments}: {result.stderr}")
        return result.stdout.strip()

    def check_case(self, case: Case) -> None:
        if case.source is not None:
            self.instructions.write_text((ROOT / case.source).read_text(encoding="utf-8"))
        elif case.text is not None:
            self.instructions.write_text(case.text)
        if case.setup == "push_mismatch":
            self.git("remote", "set-url", "--push", "origin", "https://github.example/other/repo.git")
        elif case.setup == "published_wait":
            self.instructions.write_text("Autonomous merge suspended — asked on 2026-09-27\n")
            self.git("add", "AGENTS.md")
            self.git("commit", "-qm", "published wait")
            self.git("update-ref", "refs/remotes/origin/main", "HEAD")
            self.instructions.write_text("Local clear.\n")
        elif case.setup == "missing_published_ref":
            self.git("update-ref", "-d", "refs/remotes/origin/main")
        self.log.write_text("")
        environment = self.environment | dict(case.environment)
        arguments = [str(self.instructions)] if case.mode == "suspension" else ["fixture", "repo"]
        if case.mode == "reviews":
            arguments.append("1")
        result = run(["bash", str(PREFLIGHT), case.mode, *arguments], self.repo, environment)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        api_calls = sum(call[0] == "api" for call in calls)
        self.assertEqual(
            (result.returncode, api_calls), (case.expected, case.api_calls),
            f"{case.name}: stdout={result.stdout!r}, stderr={result.stderr!r}, gh={calls!r}",
        )


def make_test(case: Case):
    def test(self: MergePreflightTests) -> None:
        self.check_case(case)

    return test


for number, fixture in enumerate(ALL_CASES, 1):
    slug = re.sub(r"[^a-z0-9]+", "_", fixture.name.lower()).strip("_")
    setattr(MergePreflightTests, f"test_{number:02d}_{slug}", make_test(fixture))
