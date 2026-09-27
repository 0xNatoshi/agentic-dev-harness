"""Merge routing, suspension and Pages fixtures against the source skill."""

from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import time
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
    Case("kept upstream note then bare ban", 2, "Keep the upstream remote read-only. Do not merge anything."),
    Case("kept bare ban then upstream pull", 2, "Do not merge. Pull from upstream."),
    Case("kept upstream or anywhere else", 2, "- Never merge from the upstream remote or from anywhere else."),
    Case("kept French upstream note then ban", 2, "Le dépôt upstream est en lecture seule. Ne fusionnez rien."),
    Case("kept ban then upstream clause", 2, "- Never merge anything; only pull from upstream."),
    Case("kept ban including upstream", 2, "Do not merge anything, including from upstream."),
    Case("kept verb merge commits from bots", 2, "Never merge commits authored by bots."),
    Case("kept verb merge commits", 2, "Never merge commits."),
    Case("kept every merge method banned", 2, "No squash merges, rebase merges or merge commits."),
    Case("kept method ban without alternative", 2, "Do not squash-merge anything."),
    Case("kept method ban pending review", 2, "No squash merges pending review."),
    Case("kept method ban then ask", 2, "Do not squash-merge anything; ask first."),
    Case("kept French method ban today", 2, "Pas de squash merge aujourd’hui."),
    Case("kept method ban at the moment", 2, "No rebase merges at the moment."),
    Case("kept upstream for the time being", 2, "Do not merge from the upstream remote for the time being."),
    Case("kept French upstream this week", 2, "Ne fusionnez rien depuis le dépôt upstream cette semaine."),
    Case("kept method ban owner reviews", 2, "No squash merges; the owner reviews everything first."),
    Case("kept French method ban ask first", 2, "Ne pas faire de squash merge ; demandez d’abord."),
    Case("kept table header gives context", 2, "| Change type | Merge policy |\n| --- | --- |\n| Any | On hold until I approve |"),
    Case("kept table paused cell", 2, "| Merges | Deploys |\n|---|---|\n| Paused | Allowed |"),
    Case("kept item lead after list", 2, "- Merges:\n\nOn hold until further notice."),
    Case("kept paragraph lead after list", 2, "Merges:\n- use squash\n\nOn hold until further notice."),
    Case("kept label item after list", 2, "- Merging PRs\n\nPaused until I approve."),
    Case("kept wrapped hold in fence", 2, "```text\nAutonomous merges are\npaused until I approve.\n```"),
    Case("caught fence under item", 2, "- Merging:\n  ```text\n  wait for my approval\n  ```"),
    Case("kept indented backticks are not a fence", 2, "Example:\n\n    ```\n\nDo not squash-merge anything\nuntil I confirm."),
    Case("kept unclosed fence", 2, "~~~\nx\n```\n\nDo not squash-merge anything\nuntil I confirm."),
    Case("kept inline backticks are not a fence", 2, "```merge``` is paused until I approve."),
    Case("kept comment label", 2, "<!-- Merge policy -->\nPaused until I approve."),
    Case("caught numbered lead-in list", 2, "Do not do the following until I approve:\n1. merge pull requests"),
    Case("caught numbered parent item", 2, "1. Merging:\n   1. wait for my approval"),
    Case("caught setext heading", 2, "Merge policy\n------------\n\nWe use squash merges.\n\nOn hold until further notice."),
    Case("caught mixed invisible splits", 2, "Do\u200bnot mer\u200bge until I approve."),
    Case("kept parent method ban child hold", 2, "- Never squash-merge\n  - until I approve"),
    Case("kept blockquote lead then item", 2, "> Merges\n> - on hold"),
    Case("cleared upstream variant original", 0, "- Never merge from the original repository."),
    Case("cleared French upstream mechanics", 0, "- Ne fusionnez jamais depuis le dépôt upstream."),
    Case("cleared code span method", 0, "- No `squash` merges; use merge commits."),
    Case("cleared GitHub method labels", 0, "- Use Squash and merge; never Rebase and merge."),
    Case("cleared i.e. is not first person", 0, "- No fast-forward merges, i.e. always use merge commits."),
    Case("cleared blockquote sibling items", 0, "> - Do not add dependencies\n> - Merge requests use squash"),
    Case("clear numbered sibling items", 0, "1. Do not add dependencies\n2. Merge requests use squash"),
    Case("kept ban except upstream", 2, "Never merge except from upstream."),
    Case("kept ban even from upstream", 2, "Never merge anything, even from upstream."),
    Case("kept ban then only upstream sync", 2, "Do not merge anything, only sync from upstream."),
    Case("kept French only from upstream", 2, "Ne fusionnez que depuis le dépôt amont."),
    Case("kept French ban even from upstream", 2, "Pas de fusion, même depuis le dépôt amont."),
    Case("kept no merge method permitted", 2, "No merge method is permitted."),
    Case("kept label sibling item", 2, "- **Merges:**\n- paused until I approve"),
    Case("kept distant label lead", 2, "Merging\n\nKeep history linear\n\nSign commits\n\nUse squash\n\nOn hold until further notice."),
    Case("cleared upstream changes source", 0, "- Never merge changes from the upstream remote."),
    Case("caught wrapped blockquote veto", 1, "> Autonomous merge\n> suspended — request dated 2026-09-27"),
    Case("French short date alias", 1, "Merge autonome suspendu — demande du 2026-09-27"),
    Case("kept ban not even from upstream", 2, "Never merge, not even from upstream."),
    Case("kept no merge strategy allowed", 2, "No merge strategy is allowed."),
    Case("kept short label sibling", 2, "- Merges\n- on hold"),
    Case("kept tab-indented child", 2, "  - Merging\n\t- paused"),
    Case("kept hold lead-in over blocks", 2, "Until I approve:\n\nA\n\nB\n\nC\n\nNo fast-forward merges; use merge commits."),
    Case("caught distant invisible splits", 2, "Do​not touch anything in this repository at all, or mer​ge anything, until I approve."),
    Case("caught invisible splits across an invisible run", 2,
         "Do\u200bnot" + "\u2060" * 249 + "mer\u200bge until approval."),
    Case("caught invisible splits across visible padding", 2,
         "Do\u200bnot " + "x " * 200 + "mer\u200bge until approval."),
    Case("conservative method ban alone", 2, "- No merge commits."),
    Case("kept method rule with sign-off condition", 2, "Never squash-merge; use rebase merges once the owner signs off."),
    Case("kept method rule once approved", 2, "No squash merges; merge commits only once approved."),
    Case("kept French method rule after green light", 2,
         "Pas de squash merge ; utiliser des merge commits après le feu vert du mainteneur."),
    Case("kept French method rule once approved", 2,
         "Pas de squash merge ; merge commits uniquement une fois approuvés par le propriétaire."),
    Case("kept all methods banned", 2, "Never use squash merges, rebase merges or merge commits."),
    Case("kept all methods banned with serial comma", 2, "Do not use squash merges, rebase merges, or merge commits."),
    Case("kept hold after cleared method rule", 2, "No fast-forward merges; use merge commits.\n\nWait for the owner."),
    Case("kept hold after paragraph separator", 2, "No fast-forward merges; use merge commits.\u2029\u2029Wait for the owner."),
    Case("kept hold after record separator", 2, "No fast-forward merges; use merge commits.\x1e\x1eWait for the owner."),
    Case("kept sibling heading pause", 2, "## Merging\n\n## Status\n\nPaused until I approve."),
    Case("kept sibling item pause", 2, "- Merging pull requests\n- Status: paused until I approve"),
    Case("kept sibling task pause", 2, "- [ ] Merging\n- [x] paused until I approve"),
    Case("kept upstream ban continued after semicolon", 2, "Never merge from upstream; ever."),
    Case("cleared method rule then unrelated rule", 0,
         "No fast-forward merges; use merge commits.\n\nRun the tests before pushing."),
    Case("cleared method ban then permitted method", 0, "Do not use squash merges; use merge commits."),
    Case("oversized input is not scanned", 2, "Use squash merges.\n" * 7000),
    Case("kept condition clause after method rule", 2, "No rebase merges; use squash merges; the owner signs off first."),
    Case("kept condition sentence after method rule", 2, "No squash merges. Use merge commits. The owner OKs each one first."),
    Case("kept colon condition after method rule", 2, "No fast-forward merges; use merge commits: owner OK required."),
    Case("kept final-say sentence after method rule", 2, "No rebase merges; use squash merges. The owner has the final say."),
    Case("kept French condition clause after method rule", 2,
         "Pas de rebase merge ; utilisez des squash merges ; le propriétaire tranche."),
    Case("kept condition sentence after upstream rule", 2,
         "- Never merge, rebase onto or cherry-pick wholesale from the upstream remote. The owner OKs each sync."),
    Case("kept hold later in section of method rule", 2,
         "No fast-forward merges; use merge commits.\n\nKeep history linear.\n\nWait until I approve."),
    Case("kept hold before method rule", 2, "Wait until I approve.\n\nNo fast-forward merges; use merge commits."),
    Case("kept owner OK after method rule", 2, "No fast-forward merges; use merge commits.\n\nThe owner must OK each one first."),
    Case("cleared create merge commits", 0, "Never squash-merge; always create merge commits."),
    Case("cleared GitHub create label", 0, "Use `Squash and merge`, never `Create a merge commit`."),
    Case("cleared French faites method rule", 0, "Ne faites pas de merge commit ; utilisez le squash merge."),
    Case("kept hold in another section", 2,
         "## Merging\n\nUse squash merges; never rebase merges.\n\n## Releases\n\nWait for CI before tagging."),
    Case("kept hold in sibling subsection", 2,
         "## Merging\n\n### Method\n\nNo fast-forward merges; use merge commits.\n\n### Process\n\nAsk the owner first."),
    Case("kept hold in earlier sibling subsection", 2,
         "## Merging\n\n### Process\n\nAsk the owner first.\n\n### Method\n\nNo fast-forward merges; use merge commits."),
    Case("kept hold in sibling top-level section", 2,
         "## Process\n\nWait until I say so.\n\n## Method\n\nNo fast-forward merges; use merge commits."),
    Case("kept parent heading hold on method rule", 2,
         "## Merges need the owner's OK\n\n### Method\n\nNo fast-forward merges; use merge commits."),
    Case("kept French parent heading hold", 2,
         "## Fusions : le propriétaire dit oui d'abord\n\n### Méthode\n\n"
         "Pas de fast-forward merge ; utilisez des merge commits."),
    Case("kept subsection hold after method rule", 2,
         "## Merging\n\nNo fast-forward merges; use merge commits.\n\n### Process\n\nAsk me first."),
    Case("kept subsection owner OK after method rule", 2,
         "## Merging\n\nNo fast-forward merges; use merge commits.\n\n### Process\n\nThe owner OKs each one."),
    Case("kept later section hold after unheaded rule", 2,
         "No fast-forward merges; use merge commits.\n\n---\n\n## Rules\n\nHuman sign-off first."),
    Case("kept method rule undone by bare clause", 2, "No rebase merges; squash merges only. But not this."),
    Case("kept method rule retracted", 2, "No rebase merges; squash merges only. Or rather, don't."),
    Case("kept method rule negated by bare clause", 2, "Use merge commits; never squash-merge. Do not do it."),
    Case("kept parent section hold above method rule", 2,
         "## Merging\n\nWait until I say so.\n\n### Method\n\nNo fast-forward merges; use merge commits."),
    Case("kept parent section owner OK above method rule", 2,
         "## Merging\n\nThe owner OKs each one first.\n\n### Method\n\nNo fast-forward merges; use merge commits."),
    Case("kept grandparent section hold above method rule", 2,
         "# Rules\n\nNothing lands without the owner's OK.\n\n## Git\n\n### Merges\n\n"
         "No fast-forward merges; use merge commits."),
    Case("kept French parent section hold above method rule", 2,
         "## Fusions\n\nLe propriétaire valide chaque fusion d'abord.\n\n### Méthode\n\n"
         "Pas de fast-forward merge ; utilisez des merge commits."),
    Case("kept method rule contradicted by bare ban", 2, "No rebase merges; squash merges only. Never rebase, never squash."),
    Case("kept method rule contradicted by nor", 2, "No squash merges; use merge commits. Nor commits."),
    Case("kept method rule contradicted by retraction", 2,
         "Use merge commits; never squash-merge. Or rather, do not commit."),
    Case("kept method banned then permitted", 2, "Do not use squash merges; use squash merges."),
    Case("kept method banned then permitted in one clause", 2, "Never use squash merges, use squash merges."),
    Case("kept method permitted then banned", 2, "Use rebase merges. Never use rebase merges."),
    Case("kept merge commits banned then permitted", 2, "No merge commits; use merge commits."),
    Case("kept French method banned then permitted", 2, "Pas de squash merge ; utilisez le squash merge."),
    Case("kept create-a-merge-commit ban", 2, "Never create a merge commit."),
    Case("kept create-a-merge-commit banned then permitted", 2, "Never create a merge commit; create a merge commit."),
    Case("kept GitHub create label banned then permitted", 2, "Never use `Create a merge commit`; use merge commits."),
    Case("kept merge commits banned then three-way permitted", 2, "No merge commits; use three-way merges."),
    Case("cleared create-a-merge-commit ban then permitted method", 0, "Never create a merge commit; use squash merges."),
    Case("cleared method ban and permitted method", 0, "Do not use squash merges and use merge commits instead."),
    Case("cleared French method ban and permitted method", 0, "Pas de squash merges et utilisez des merge commits."),
    Case("kept method list banned with and", 2, "Do not use squash merges and rebase merges."),
    Case("kept method permitted and bare merge banned", 2, "Use squash merges and never merge."),
    Case("kept method permitted and same method banned", 2, "Use squash merges and never use squash merges."),
    Case("cleared GitHub label ban and permitted label", 0, "Do not use squash and merge and use rebase and merge."),
    Case("kept method swap then bare merge ban", 2, "Do not use squash merges and use merge commits and never merge."),
    Case("kept GitHub label banned before only", 2, "Never use Squash and merge only."),
    Case("kept GitHub label banned then permitted only", 2, "Never use Squash and merge; use Squash and merge only."),
    Case("kept merging label banned before only", 2, "Never use rebase and merging only."),
    Case("kept task label sibling pause", 2, "- [ ] Merging\n- [x] blocked"),
    Case("kept multiword label sibling pause", 2, "- Pull request merges\n- blocked"),
    Case("kept label titling later siblings", 2, "- Merging\n- CI green\n- blocked"),
    Case("kept method item then pause item", 2, "- Use squash merges\n- blocked"),
    Case("kept pause item then merge item", 2, "- blocked\n- Merging"),
    Case("cleared fast-forward ban with no-ff", 0, "Do not use fast-forward merges; use no-ff merges."),
    Case("kept no-ff banned", 2, "Do not use no-ff merges."),
    Case("kept hold under repeated heading", 2,
         "## Rules\n\nDo not use squash merges; use merge commits.\n\n## Rules\n\nWait for CI before tagging."),
    Case("kept hold in rule subsection", 2,
         "## Rules\n\nDo not use squash merges; use merge commits.\n\n### CI\n\nWait for CI before tagging."),
    Case("kept approval item after method item", 2, "- Merges only\n- after approval"),
    Case("kept French approval item after method item", 2, "- Fusions uniquement\n- après accord"),
    Case("kept ordered approval items", 2, "1. Merges only\n2. after approval"),
    Case("kept pause two items away", 2, "- Merging pull requests into main from feature branches\n- CI green\n- blocked"),
    Case("kept pause past another label", 2, "- Merging\n- **Docs:**\n- blocked"),
    Case("kept table rows pause", 2, "| Item |\n|---|\n| Merging |\n| blocked |"),
    Case("kept padded table rows pause", 2,
         "| Topic | Status |\n|---|---|\n" + "".join(
             f"| {left:<150} | {right:<150} |\n" for left, right in [("Merging", ""), ("Owner", ""), ("", "blocked")])),
    Case("kept pause past padded item", 2, "- Merging\n- Owner:" + " " * 320 + "Thib\n- blocked"),
    Case("kept pause past marker examples", 2,
         "- Merging\n- " + "Autonomous merge suspended — request dated <date> " * 7 + "\n- blocked"),
    Case("kept pause phrase split across items", 2, "- Merging\n- on\n- hold"),
    Case("kept pause from table row to list item", 2, "| a |\n|---|\n| Merging |\n- blocked"),
    Case("kept pause from list item to table row", 2, "- Merging\n\n| a |\n|---|\n| blocked |"),
    Case("kept pause across adjacent tables", 2, "| Merging |\n|---|\n\n| x |\n|---|\n| blocked |"),
    Case("kept negated lead-in item", 2, "- Do not do the following\n- merge pull requests"),
    Case("kept bare negated lead-in item", 2, "- Do not\n- merge"),
    Case("kept negated lead-in over later siblings", 2, "- Do not do the following\n- push to main\n- merge pull requests"),
    Case("kept French negated lead-in item", 2, "- Ne faites pas ceci\n- fusionner les PR"),
    Case("kept merge ban before separate upstream action", 2, "Never merge, fetch directly from upstream."),
    Case("kept merge ban before comma list of upstream verbs", 2, "Never merge, rebase, cherry-pick from upstream."),
    Case("cleared upstream verb list closed by or", 0, "Never merge, rebase or cherry-pick from upstream."),
    Case("kept merge ban before comma and upstream action", 2, "Never merge, and fetch directly from upstream."),
    Case("kept merge ban before comma or upstream action", 2, "Do not merge, or pull the changes from upstream."),
    Case("cleared upstream verb list with serial comma", 0, "Never merge, rebase, or fetch from upstream."),
    Case("kept negated lead-in with trailing hold", 2, "- Do not do the following until I approve\n- merge PRs"),
    Case("kept bare negated lead-in with hold clause", 2, "- Do not, until I approve\n- merge PRs"),
    Case("kept negated pointer lead-in with trailing words", 2, "- Do not do the following for now\n- merge PRs"),
    Case("kept negated lead-in with hold and no pointer", 2, "- Don't, unless I approve\n- merge"),
    Case("kept French negated lead-in with hold", 2, "- Ne faites pas ceci sans mon accord\n- fusionner les PR"),
    Case("kept negated lead-in beside a long parent", 2,
         "- " + ("Without me the notes" + " the notes cover tags and docs for every contributor" * 20)[:590] + "\n  - Never do the following\n  - no rebase merges, use squash merges"),
    Case("kept lead-in over a later hold-bearing sibling", 2,
         "- Do not do the following\n- force-push to main, not even for me\n- merge pull requests"),
    Case("kept lead-in over a later dated sibling", 2, "- Do not do the following\n- push to main, not today\n- merge PRs"),
    Case("kept lead-in over a later review sibling", 2,
         "- Do not do the following\n- avoid force pushes without my review\n- merge pull requests"),
    Case("kept lead-in over a later bare negation", 2, "- Do not do the following\n- Not now\n- merge"),
    Case("kept French lead-in over a later sibling", 2,
         "- Ne faites pas ceci\n- pousser sur main, jamais sans moi\n- fusionner les PR"),
    Case("kept qualified negated lead-in", 2, "- Never under any circumstances\n- merge pull requests"),
    Case("kept intensified negated lead-in", 2, "- Never, ever\n- merge PRs"),
    Case("kept bracketed intensifier lead-in", 2, "- Never (ever)\n- merge PRs"),
    Case("kept arrow negated lead-in", 2, "- Never ->\n- merge PRs"),
    Case("kept lead-in qualified by an unlisted adverb", 2, "- Never, ever again\n- merge pull requests"),
    Case("kept lead-in with a second negation", 2, "- Never, not even once\n- merge PRs"),
    Case("kept French aucun lead-in", 2, "- En aucun cas\n- fusionner"),
    Case("kept French pretext lead-in", 2, "- Sous aucun prétexte\n- fusionner les PR"),
    Case("conservative one-word negated item beside merge method", 2,
         "- Tests must pass\n- No exceptions\n- Merge with squash"),
    Case("cleared negated noun item beside merge method", 0, "- Tests must pass\n- No exceptions for docs\n- Merge with squash"),
    Case("kept idiom negated lead-in", 2, "- Under no circumstances\n- merge pull requests"),
    Case("kept at-no-time lead-in", 2, "- At no time\n- merge pull requests"),
    Case("kept by-no-means lead-in", 2, "- By no means\n- merge pull requests"),
    Case("kept qualified idiom lead-in", 2, "- At no point in time\n- merge PRs"),
    Case("kept one-word negated lead-in", 2, "- Do not proceed\n- merge pull requests"),
    Case("kept determiner negated lead-in", 2, "- Do not do such things\n- merge PRs"),
    Case("kept French moment lead-in", 2, "- À aucun moment\n- fusionner les PR"),
    Case("kept conditional negated lead-in", 2, "- Never, if possible\n- merge PRs"),
    Case("kept conjunction negated lead-in", 2, "- Never so long as\n- merge PRs"),
    Case("kept French preposition negated lead-in", 2, "- Ne pas vers main\n- fusionner les PR"),
    Case("cleared negated branch rule beside merge item", 0, "- Do not touch main or release branches\n- Merges use squash"),
    Case("kept method rule under a negated lead-in paragraph", 2, "Do not:\n\nUse squash merges."),
    Case("kept upstream rule under a negated parent item", 2, "- Do not:\n  - merge from upstream"),
    Case("kept method rule under a negated lead-in item", 2, "Do not:\n- use squash merges"),
    Case("kept authorize after a cleared method rule", 2,
         "No fast-forward merges; use merge commits.\n\nA human must authorize each one."),
    Case("kept authorized after a cleared method rule", 2,
         "No fast-forward merges; use merge commits.\n\nEach one must be authorized."),
    Case("kept upstream rule with a restricted subject", 2, "No contributor may merge changes from upstream."),
    Case("kept negated lead-in between table rows", 2, "| Rule |\n|---|\n| Do not do the following |\n| merge PRs |"),
    Case("kept idiom lead-in between table rows", 2, "| Rule |\n|---|\n| Under no circumstances |\n| merge PRs |"),
    Case("cleared French negated rule beside merge method", 0, "- Ne pas ajouter de dépendances\n- Merges use squash"),
    Case("kept wait directive across list items", 2, "- Wait before\n- merging pull requests"),
    Case("kept hold directive across list items", 2, "- Hold off on\n- merging anything"),
    Case("kept stop directive across table rows", 2, "| Rule |\n| --- |\n| Stop |\n| merging |"),
    Case("cleared hidden splits inside managed block", 0,
         "<!-- github-workflow:start v6.2 -->\nDo\u200bnot mer\u200bge until approval.\n<!-- github-workflow:end -->"),
    Case("kept hidden splits outside managed block", 2,
         "<!-- github-workflow:start v6.2 -->\nText\n<!-- github-workflow:end -->\nDo\u200bnot mer\u200bge until approval."),
    Case("cleared method item beside CI wait item", 0, "- Merge requests use squash\n- Wait for CI before tagging"),
    Case("cleared marker example beside list item", 0,
         "- Use the `Autonomous merge suspended — request dated <date>` marker\n- Read prose restrictions too"),
    Case("kept permitted method cancelled by bare never", 2, "Never squash-merge; use merge commits, never."),
    Case("kept permitted method cancelled by bare not", 2, "No fast-forward merges; use merge commits, not."),
    Case("kept only method cancelled by but not", 2, "No rebase merges; squash merges only, but not."),
    Case("kept French permitted method cancelled by jamais", 2,
         "Pas de squash merge ; utilisez des merge commits, jamais."),
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
if len(ALL_CASES) != 323 or len({case.name for case in ALL_CASES}) != 323:
    raise RuntimeError("Merge fixture inventory must contain 323 unique cases")


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


class ScanTimeTests(unittest.TestCase):
    def test_long_invisible_run_stays_linear(self) -> None:
        # A quadratic hidden-split scan took over 10 s on this input; linear takes milliseconds.
        spec = importlib.util.spec_from_file_location("workflow_context", PREFLIGHT.with_name("workflow-context.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        start = time.monotonic()
        self.assertEqual(module.scan_text("merge" + "\u200b" * 80000 + " x"), 0)
        self.assertLess(time.monotonic() - start, 3)

    def test_alternating_list_run_stays_bounded(self) -> None:
        # Reading every window of a merge / approval list with the full pattern took 7 s here.
        spec = importlib.util.spec_from_file_location("workflow_context", PREFLIGHT.with_name("workflow-context.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        start = time.monotonic()
        self.assertEqual(module.scan_text("- merge\n- go\n" * 10000), 0)
        self.assertLess(time.monotonic() - start, 3)
