"""Merge routing, suspension and Pages fixtures against the source skill."""

import collections
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import random
import re
from tempfile import TemporaryDirectory
import time
import unicodedata
import unittest

from tests._fixture_support import fixture_environment, run, write_fixture


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "skills/github-workflow/scripts/merge-preflight.sh"

FAKE_GH = """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with Path(os.environ['FIXTURE_LOG']).open('a', encoding='utf-8') as log:
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
    Case("orphan marker with em dash for a hyphen", 2, "<!-—github-workflow:end -->"),
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
    Case("kept review after a cleared method rule", 2,
         "No fast-forward merges; use merge commits.\n\nA human must review each one."),
    Case("kept manual step after a cleared method rule", 2,
         "No fast-forward merges; use merge commits.\n\nEach one is manual."),
    Case("kept first-person check after a cleared method rule", 2,
         "No fast-forward merges; use merge commits.\n\nCheck with me each time."),
    Case("kept qualifier later in a cleared rule's section", 2,
         "No fast-forward merges; use merge commits.\n\nKeep history readable.\n\nA human must review each one."),
    Case("kept qualifier in a cleared rule's subsection", 2,
         "## Git\n\nNo fast-forward merges; use merge commits.\n\n### Review\n\nEach one must be confirmed."),
    Case("cleared method rule followed by unrelated rules", 0,
         "No fast-forward merges; use merge commits.\n\nTests must pass.\n\nKeep commits small."),
    Case("cleared method rule before a sibling section", 0,
         "## Git\n\nNo fast-forward merges; use merge commits.\n\n## Docs\n\nReview docs weekly."),
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
    # Hold vocabulary: integration verbs with PR scope, passive and modal forms, bans and freezes.
    Case("caught integrate PR without approval", 2, "Do not integrate any PR without my approval."),
    Case("caught merge after my review", 2, "Merge only after my review."),
    Case("caught hold PRs until go", 2, "Hold all PRs until I say go."),
    Case("caught PRs wait before landing", 2, "Pull requests must wait for my review before landing on main."),
    Case("caught French integrate PR", 2, "N’intègre aucune PR sans mon accord."),
    Case("caught French fusionne", 2, "Ne fusionne rien sans mon accord."),
    Case("caught passive must not be merged", 2, "PRs must not be merged without my approval."),
    Case("caught passive merged until", 2, "Nothing should be merged until I approve."),
    Case("caught ask me before merged", 2, "Ask me before anything is merged."),
    Case("caught French passive fusionné", 2, "Rien ne doit être fusionné sans mon accord."),
    Case("caught merging not allowed until I approve", 2, "Merging is not allowed until I approve."),
    Case("caught merging not allowed until Friday", 2, "Merging is not allowed until Friday."),
    Case("caught merging prohibited", 2, "Merging is prohibited until I approve."),
    Case("caught merges frozen", 2, "Merges are frozen."),
    Case("caught merge freeze", 2, "Merge freeze until Friday."),
    Case("caught no automerge", 2, "No automerge."),
    Case("caught ask me before merging", 2, "Ask me before merging."),
    Case("caught leave merging to me", 2, "Leave merging to me."),
    Case("caught French jamais de fusion", 2, "Jamais de fusion sans moi."),
    Case("caught approval required for merges", 2, "Approval required for merges."),
    Case("caught must not merge from upstream", 2, "Agents must not merge from upstream."),
    Case("caught may not merge PRs", 2, "Contributors may not merge PRs."),
    Case("caught avoid merging PRs", 2, "Avoid merging pull requests."),
    Case("caught ban across list items", 2, "- Merging PRs to the main branch\n- not allowed"),
    Case("cleared land without PR scope", 0, "Do not land broken code."),
    Case("cleared ship PRs", 0, "Ship small PRs."),
    Case("cleared integrate upstream", 0, "Integrate upstream changes weekly."),
    Case("cleared review without first person", 0, "Merge only after CI and review pass."),
    Case("cleared approval not required", 0, "Approval is not required to merge a ready PR."),
    Case("cleared merge for me", 0, "Merge it for me when ready."),
    Case("cleared ask a bot before merging", 0, "Ask for a Codex review before merging."),
    Case("cleared avoid merge conflicts", 0, "Avoid merge conflicts: rebase often."),
    Case("cleared negated hold on PRs", 0, "Do not hold PRs for approval once gates pass."),
    Case("cleared freeze in another clause", 0, "Feature freeze is over; merge ready PRs."),
    Case("cleared frozen in another clause", 0, "Merge PRs to main; frozen tags stay untouched."),
    Case("cleared must not in another clause", 0, "PR titles must not exceed 72 characters; merge with squash."),
    Case("cleared ban in another clause", 0, "Direct pushes to main are not allowed; merge through PRs."),
    Case("cleared merged without another go", 0, "Ready PRs are merged without another ritual go."),
    Case("cleared criteria hold", 0,
         "When the PR is ready and green, merge it directly - no approval needed when the criteria hold."),
    Case("cleared integration named far from its PR", 0,
         "Deliver the migration PR; its policy changes cannot override the hold governing its own integration."),
    Case("conservative cannot merge a PR", 2, "You cannot merge a PR with failing checks."),
    Case("conservative French do not hesitate", 2, "N’hésite pas à fusionner les PR prêtes."),
    # Review round 1: a negated lead-in naming a merged state, and further hold wording.
    Case("caught lead-in not until merged", 2, "- Not until the release PR is merged\n- merge other PRs"),
    Case("caught lead-in avoid until merged", 2, "- Avoid the following until the fix is merged:\n- merging feature PRs"),
    Case("caught lead-in not while automerge", 2, "- Not while automerge is off\n- merge PRs"),
    Case("caught French lead-in pas avant fusionné", 2, "- Pas avant que le correctif soit fusionné :\n- fusionner les autres PR"),
    Case("caught table lead-in not until merged", 2, "| Rule |\n|---|\n| Not until the fix is merged |\n| merge PRs |"),
    Case("caught merging reserved for the owner", 2, "Merging is reserved for the owner."),
    Case("caught leave merging to the owner", 2, "Leave merging to the owner."),
    Case("caught French reserved to the owner", 2, "La fusion est réservée au propriétaire."),
    Case("caught French reserved to me", 2, "La fusion m’est réservée."),
    Case("caught merge PRs only after my review", 2, "Merge PRs only after my review."),
    Case("caught only merge PRs after I approve", 2, "Only merge PRs after I approve."),
    Case("caught landing requires my approval", 2, "Landing a PR requires my approval."),
    Case("caught merges to main frozen", 2, "Merges to main are frozen."),
    Case("caught freeze all merges", 2, "Freeze all merges."),
    Case("caught French merges frozen", 2, "Les fusions sont gelées."),
    Case("caught main branch frozen", 2, "The main branch is frozen."),
    Case("caught label not allowed", 2, "**Merging:** not allowed."),
    Case("caught prohibited label", 2, "Prohibited: merging PRs, force-pushing."),
    Case("caught contraction not allowed", 2, "Merging isn’t allowed."),
    Case("caught French not authorized", 2, "Les fusions ne sont pas autorisées."),
    Case("caught PRs not to be merged", 2, "PRs are not to be merged."),
    Case("caught refrain from merging", 2, "Please refrain from merging PRs."),
    Case("caught avoid merges", 2, "Avoid merges."),
    Case("caught French avoid merges", 2, "Évitez de fusionner les PR."),
    Case("caught merges must be approved", 2, "Merges must be approved by me."),
    Case("caught approval before they are merged", 2, "PRs require my approval before they are merged."),
    Case("caught before merging ask me", 2, "Before merging, ask me."),
    Case("caught check with me before merging", 2, "Check with me before merging."),
    Case("caught ask a maintainer before merging", 2, "Ask a maintainer before merging."),
    Case("caught French before merging ask me", 2, "Avant de fusionner, demande-moi."),
    Case("caught merge once the owner approves", 2, "Merge once the owner approves."),
    Case("caught nothing merged without consent", 2, "Nothing is merged without my consent."),
    Case("caught French no merge without go", 2, "Aucune fusion sans mon feu vert."),
    Case("caught PRs blocked until I approve", 2, "PRs are blocked until I approve."),
    Case("caught PRs label hold", 2, "PRs: hold until I approve."),
    Case("caught all PRs on hold", 2, "All PRs are on hold."),
    Case("caught hold PRs", 2, "Hold PRs."),
    Case("caught every PR needs my approval", 2, "Every PR needs my approval."),
    Case("caught wait for the go", 2, "Wait for the go before merging."),
    Case("cleared ship to production", 0, "Never ship to production on Fridays."),
    Case("cleared integrate into the release", 0, "Do not integrate secrets into the release."),
    Case("cleared wait for our release", 0, "Wait for our release pipeline to finish."),
    Case("cleared our CI on PRs", 0, "Wait for our CI to pass on your PR."),
    Case("cleared we hold failing PRs", 0, "We hold PRs that fail CI."),
    Case("cleared merge unless our CI fails", 0, "Merge the PR unless our checks fail."),
    Case("cleared tell me afterwards", 0, "Merge unless CI is red; tell me afterwards."),
    Case("cleared squash not allowed with alternative", 0, "Squash merges are not allowed; use merge commits."),
    Case("cleared cannot fast-forward with alternative", 0, "You cannot use fast-forward merges; use merge commits."),
    Case("cleared should not need to merge", 0, "You should not need to merge main into your branch; rebase instead."),
    Case("cleared owner's guide", 0, "Consult the owner’s guide before merging."),
    Case("cleared go to the next task", 0, "Merge PRs after CI passes, then go to the next task."),
    Case("cleared PRs need a description", 0, "PRs need a description and a linked issue."),
    Case("cleared keep PRs small", 0, "Keep PRs small and focused."),
    # Review round 2: a bare 'go' stays an approval signal, as on the release before.
    Case("caught merges only after go-ahead", 2, "Merges only after go-ahead."),
    Case("caught merge after go", 2, "Merge after go."),
    Case("caught merges wait for go", 2, "Merges wait for go."),
    Case("caught before merging wait for go", 2, "Before merging, wait for go."),
    Case("caught explicit go before merging", 2, "Get explicit go before merging."),
    Case("caught possessive go", 2, "Merging requires the maintainer’s go."),
    Case("caught go-ahead required", 2, "Go-ahead required before merging."),
    Case("caught fusion after go", 2, "Fusion uniquement après go."),
    Case("caught go across list items", 2, "- Merge only after\n- explicit go"),
    Case("caught go after cleared method rule", 2, "Never use rebase merges; use squash merges.\n\nNeed explicit go."),
    Case("caught label until approval", 2, "Merging: until I say go."),
    Case("caught label not until approval", 2, "Merge PRs: not until I approve."),
    Case("cleared merge and go home", 0, "Merge the PR and go home."),
    Case("cleared nothing merged without CI", 0, "Nothing is merged without CI passing."),
    Case("cleared nothing lands before CI", 0, "Nothing lands on main before CI passes."),
    Case("cleared frozen lockfile", 0, "Merge PRs with a frozen lockfile."),
    Case("cleared frozen lockfiles before merging", 0, "Use frozen lockfiles in CI before merging."),
    # Codex review on bee23ba: possessive roles, lifted states and lockfile freezes.
    Case("caught reserved for the repository's owner", 2, "Merging is reserved for the repository’s owner."),
    Case("cleared PRs not on hold", 0, "PRs are not on hold."),
    Case("cleared main branch not frozen", 0, "The main branch is not frozen."),
    Case("cleared PRs plus en attente", 0, "Les PR ne sont plus en attente."),
    Case("cleared freeze the lockfile", 0, "Freeze the lockfile before merging."),
    Case("cleared freeze dependencies", 0, "Freeze dependencies before merging."),
    # Delta recheck on bee23ba: 'go' as a noun before on/through/ahead, named deciders, freeze locks.
    Case("caught go on Slack", 2, "Get the go on Slack before merging."),
    Case("caught go-ahead and green CI", 2, "Merge only after the go-ahead and a green CI."),
    Case("caught go through Slack", 2, "Merging requires my go through Slack."),
    Case("caught nothing merged without a named person", 2, "Nothing gets merged without Alice."),
    Case("caught aucune fusion sans validation", 2, "Aucune fusion sans validation humaine."),
    Case("caught merge freeze lockdown", 2, "Merge freeze lockdown in effect."),
    Case("caught freeze locks merges", 2, "A freeze locks all merges until further notice."),
    # Final delta recheck on 4d48804: 'go' as a noun after a determiner, freezes of dependency merges.
    Case("caught my go to proceed", 2, "Merging requires my go to proceed."),
    Case("caught the go to deploy", 2, "Merges require the go to deploy."),
    Case("caught a go back from the owner", 2, "Merging needs a go back from the owner."),
    Case("caught possessive go to proceed", 2, "Merging requires the maintainer’s go to proceed."),
    Case("caught freeze dependency merges", 2, "Freeze dependency merges."),
    Case("caught freeze version bumps and merges", 2, "Freeze version bumps and merges."),
    Case("caught freeze the version-2 merges", 2, "Freeze the version-2 merges."),
    Case("caught main not develop is frozen", 2, "Main not develop is frozen."),
    Case("caught owner's go split across items", 2, "- Merging stays closed until the owner’s\n- go to proceed."),
    Case("caught freeze dependencies lockfiles and merges", 2, "Freeze dependencies, lockfiles and merges."),
    Case("caught freeze dependencies slash merges", 2, "Freeze dependencies/merges."),
    # #13 increment 2: reservations without a reserve verb, labels, ask-first, named roles and PR states.
    Case("caught merge label only after my approval", 2, "- Merge PRs: only after my approval"),
    Case("caught integration label only after my approval", 2, "Integration: only after my approval."),
    Case("caught landing label only after my approval", 2, "Landing a PR: only after my approval."),
    Case("caught PR label hold until my return", 2, "PRs: hold until my return."),
    Case("caught merge nothing until my return", 2, "Merge nothing until my return."),
    Case("caught table row ask me first", 2, "| Action | Rule |\n|---|---|\n| Merge PRs | ask me first |"),
    Case("caught list items ask me first", 2, "- Merge PRs\n- ask me first"),
    Case("caught only the owner merges", 2, "Only the owner merges PRs."),
    Case("caught only I may merge", 2, "Only I may merge."),
    Case("caught nobody but me merges", 2, "Nobody but me merges PRs."),
    Case("caught merging is my call", 2, "Merging is my call."),
    Case("caught merging is the owner's decision", 2, "Merging is the owner's decision."),
    Case("caught I handle all merges myself", 2, "I handle all merges myself."),
    Case("caught merges go through me", 2, "Merges go through me."),
    Case("caught route every merge through me", 2, "Route every merge through me."),
    Case("caught French only the owner merges", 2, "Seul le propriétaire fusionne les PR."),
    Case("caught French PRs merged by me", 2, "Les PR sont fusionnées par moi."),
    Case("caught French let me merge", 2, "Laisse-moi fusionner."),
    Case("caught freeze main until I approve", 2, "Freeze main until I approve."),
    Case("caught hold main until I approve", 2, "Hold main until I approve."),
    Case("caught all PRs are blocked", 2, "All PRs are blocked."),
    Case("caught main branch is paused", 2, "The main branch is paused."),
    Case("caught merging reserved for the release manager", 2, "Merging is reserved for the release manager."),
    Case("caught ask a reviewer before merging", 2, "Ask a reviewer before merging."),
    Case("caught PRs not on hold but frozen", 2, "PRs are not on hold but frozen."),
    Case("caught French PRs not waiting but frozen", 2, "Les PR ne sont pas en attente mais gelées."),
    Case("caught list items only after my return", 2, "- Merge only after my\n- return"),
    Case("caught merges are my responsibility", 2, "Merges are my responsibility."),
    Case("caught PRs are merged by the owner", 2, "PRs are merged by the owner."),
    Case("cleared only squash merges", 0, "Only squash merges are allowed."),
    Case("cleared only the bot merges dependency updates", 0, "Only the bot merges dependency updates after CI."),
    Case("cleared only human-written PRs are merged", 0, "Only human-written PRs are merged."),
    Case("cleared only merge PRs that pass CI", 0, "Only merge PRs that pass CI."),
    Case("cleared merging is my favourite part", 0, "Merging is my favourite part of the job."),
    Case("cleared merging is your call once CI is green", 0, "Merging is your call once CI is green."),
    Case("cleared merging is a team decision", 0, "Merging is a team decision."),
    Case("cleared route build logs through the CI server", 0, "Route build logs through the CI server."),
    Case("cleared merges go through the merge queue", 0, "Merges go through the merge queue."),
    Case("cleared route every merge through the merge queue", 0, "Route every merge through the merge queue."),
    Case("cleared merges go through CI", 0, "Merges go through CI."),
    Case("cleared PRs merged by the bot", 0, "PRs are merged by the bot after CI."),
    Case("cleared merge PRs opened by me", 0, "Merge PRs opened by me after CI."),
    Case("cleared squash merge by default", 0, "Squash merge by default."),
    Case("cleared French only the bot merges", 0, "Seul le bot fusionne les mises à jour."),
    Case("cleared French PRs merged by the bot", 0, "Les PR sont fusionnées par le bot."),
    Case("cleared French leave me a comment", 0, "Laisse-moi un commentaire après la fusion."),
    Case("cleared freeze the lockfile before merging", 0, "Freeze the lockfile before merging."),
    Case("cleared freeze main dependencies", 0, "Freeze main dependencies before the release."),
    Case("cleared pause main menu animations", 0, "Pause main menu animations."),
    Case("cleared hold the main thread lock", 0, "Hold the main thread lock briefly."),
    Case("cleared main branch is not paused", 0, "The main branch is not paused."),
    Case("cleared branch protection is paused", 0, "Branch protection is paused."),
    Case("cleared PRs are not on hold plain", 0, "PRs are not on hold."),
    Case("cleared PRs not on hold but ready", 0, "PRs are not on hold but ready."),
    Case("cleared PRs are not blocked", 0, "PRs are not blocked."),
    Case("cleared PRs blocked by failing checks", 0, "PRs are blocked by failing checks."),
    Case("cleared PRs blocked until CI passes", 0, "PRs are blocked until CI passes."),
    Case("cleared all PRs are squash merged", 0, "All PRs are squash merged."),
    Case("cleared ask a reviewer for style feedback", 0, "Ask a reviewer for feedback on style."),
    Case("cleared merge once a reviewer approves", 0, "Merge once a reviewer approves."),
    Case("cleared merge after CI then ask the bot first", 0, "Merge PRs after CI; ask the bot first."),
    Case("cleared list items ask the bot first", 0, "- Merge PRs\n- ask the bot first"),
    Case("cleared merge label squash only", 0, "Merge PRs: squash only."),
    Case("cleared merge label only after CI", 0, "Merge PRs: only after CI passes."),
    Case("cleared integration tests label", 0, "Integration tests: only after my approval."),
    Case("cleared continuous integration label", 0, "Continuous integration: only after my approval."),
    Case("cleared return value then merge after review", 0, "Document the return value; merge after review."),
    Case("cleared release manager merges on Fridays", 0, "The release manager merges PRs on Fridays."),
    Case("cleared I review them myself afterwards", 0, "Merge PRs; I review them myself afterwards."),
    Case("cleared label item then I write the docs myself", 0, "- Merge PRs\n- I write the docs myself"),
    Case("cleared label item then releases are my call", 0, "- Merge PRs\n- Releases are my call"),
    Case("cleared label item then questions go through me", 0, "- Merges\n- Questions go through me"),
    Case("cleared main branch protection is paused", 0, "Main branch protection is paused."),
    Case("cleared main loop is paused", 0, "The main loop is paused on breakpoints."),
    Case("cleared branch builds are paused", 0, "Branch builds are paused."),
    Case("cleared consult the reviewer guide before merging", 0, "Consult the reviewer guide before merging."),
    # #13 increment 2, review round 1: emphasis, contractions, more states and roles, split items.
    Case("caught emphasized merging label", 2, "- **Merging**: only after my approval."),
    Case("caught underscored merge label", 2, "__Merge PRs__: only after my approval."),
    Case("caught PRs blocked until further notice", 2, "All PRs are blocked until further notice."),
    Case("caught PRs blocked while the release runs", 2, "All PRs are blocked while the release is in progress."),
    Case("caught only the owner is allowed to merge", 2, "Only the owner is allowed to merge PRs."),
    Case("caught only the owner has permission to merge", 2, "Only the owner has permission to merge."),
    Case("caught French only the owner has the right to merge", 2, "Seul le propriétaire a le droit de fusionner les PR."),
    Case("caught I'll handle all merges myself", 2, "I'll handle all merges myself."),
    Case("caught typographic I'll handle all merges myself", 2, "I’ll handle all merges myself."),
    Case("caught we'll merge PRs ourselves", 2, "We'll merge PRs ourselves."),
    Case("caught merging is restricted to me", 2, "Merging is restricted to me."),
    Case("caught merging is limited to maintainers", 2, "Merging is limited to maintainers."),
    Case("caught freeze code-span main", 2, "Freeze `main` until I approve."),
    Case("caught now freeze main", 2, "Now freeze main."),
    Case("caught only emphasized I may merge", 2, "Only **I** may merge."),
    Case("caught merging is emphasized my call", 2, "Merging is **my** call."),
    Case("caught all PRs are emphasized on hold", 2, "All PRs are **on hold**."),
    Case("caught do emphasized not merge", 2, "Do **not** merge."),
    Case("caught all PRs are held", 2, "All PRs are held."),
    Case("caught PRs are on pause", 2, "PRs are on pause."),
    Case("caught all PRs are parked", 2, "All PRs are parked."),
    Case("caught PRs aren't on hold but frozen", 2, "PRs aren't on hold but frozen."),
    Case("caught PRs no longer on hold but frozen", 2, "PRs are no longer on hold but frozen."),
    Case("caught PRs not on hold anymore but frozen", 2, "PRs are not on hold anymore but frozen."),
    Case("caught main branch is on hold", 2, "The main branch is on hold."),
    Case("caught main branch has been paused", 2, "The main branch has been paused."),
    Case("caught French main branch paused", 2, "La branche main est en pause."),
    Case("caught French PRs paused", 2, "Les PR sont en pause."),
    Case("caught French merges go through me", 2, "Les fusions passent par moi."),
    Case("caught French freeze main until my approval", 2, "Gèle main jusqu'à mon accord."),
    Case("caught French it is me who merges", 2, "C'est moi qui fusionne les PR."),
    Case("caught merges always go through me", 2, "Merges always go through me."),
    Case("caught nobody merges except the owner", 2, "Nobody merges PRs except the owner."),
    Case("caught let me merge", 2, "Let me merge."),
    Case("caught French let me merge without hyphen", 2, "Laisse moi fusionner."),
    Case("caught French let me merge with em dash for a hyphen", 2, "Laisse—moi fusionner."),
    Case("caught freeze dependencies lockfiles then em dash and merges", 2, "Freeze dependencies, lockfiles—and merges."),
    Case("caught table row ask me before", 2, "| Action | Rule |\n|---|---|\n| Merge PRs | ask me before |"),
    Case("caught merging is the tech lead's call", 2, "Merging is the tech lead's call."),
    Case("caught merging is the code owner's decision", 2, "Merging is the code owner's decision."),
    Case("caught French list items merge requires my approval", 2, "- Fusion des PR\n- exige mon accord"),
    Case("caught list items merge requires my approval", 2, "- The merge of PRs\n- requiert my approval"),
    Case("caught list items before merging ask a reviewer", 2, "- Before merging any PR\n- ask a reviewer"),
    Case("caught list items before merging ask the owner", 2, "- Before merging any PR, ask\n- the owner"),
    Case("caught list items consult the release manager", 2, "- Before you merge a PR\n- consult the release manager"),
    Case("caught list items merging reserved to the owner", 2, "- Merging PRs is reserved\n- to the owner"),
    Case("caught list items merging is up to the owner", 2, "- Merging is up\n- to the owner"),
    Case("caught list items nothing merged without the owner", 2, "- Nothing is merged\n- without the owner"),
    Case("caught ask your tech lead before merging", 2, "Ask your tech lead before merging."),
    Case("caught only an admin merges", 2, "Only an admin merges."),
    Case("caught French merging reserved to the release manager", 2,
         "La fusion est réservée au responsable des versions."),
    Case("caught merging is handled by the owner", 2, "Merging is handled by the owner."),
    Case("caught merge nothing before my return", 2, "Merge nothing before my return."),
    Case("caught merge nothing before my approval", 2, "Merge nothing before my approval."),
    Case("caught lock main for the migration", 2, "Lock main for the duration of the migration."),
    Case("caught lock main temporarily", 2, "Lock the main branch temporarily while the migration runs."),
    Case("cleared builds on main are paused", 0, "Builds on main are paused."),
    Case("cleared branch protection on main is paused", 0, "Branch protection on main is paused while we migrate."),
    Case("cleared Renovate on the main branch is paused", 0, "Renovate on the main branch is paused."),
    Case("cleared debugger main is paused", 0, "When the debugger hits a breakpoint, main is paused."),
    Case("cleared PRs failing CI are blocked", 0, "PRs failing CI are blocked."),
    Case("cleared draft PRs are blocked", 0, "Draft PRs are blocked."),
    Case("cleared PRs blocked for 24 hours", 0, "PRs are blocked for 24 hours after opening."),
    Case("cleared PRs blocked until checks pass", 0, "PRs are blocked until checks pass."),
    Case("cleared PRs merged by a maintainer after review", 0, "PRs are merged by a maintainer after review."),
    Case("cleared PRs merged by the reviewer once approved", 0, "Once approved, PRs are merged by the reviewer."),
    Case("cleared PRs merged by the release manager on Fridays", 0, "PRs are merged by the release manager on Fridays."),
    Case("cleared ask a maintainer first about a big feature", 0,
         "Before starting work on a big feature, ask a maintainer first; small fixes can be merged directly."),
    Case("cleared list items merging main then ask first", 0,
         "- Fork the repo and create a branch from main\n- Keep your branch up to date by merging main\n"
         "- For large changes, ask a maintainer first"),
    Case("cleared merge conflicts ask the reviewer first", 0, "Merge conflicts: ask the reviewer first."),
    Case("cleared stuck merge queue ping first", 0, "If the merge queue is stuck, ping the release manager first, then retry."),
    Case("cleared integration needs admin consent", 0, "Integration: needs admin consent in Entra ID."),
    Case("cleared integration after the Slack admin approves", 0, "Integration: only after the Slack admin approves the app."),
    Case("cleared consult CODEOWNERS before merging", 0, "Consult CODEOWNERS before merging to see who reviews what."),
    Case("cleared consult the reviewers' guide before merging", 0, "Consult the reviewers' guide before merging."),
    Case("cleared merge via the admin panel", 0, "Merge via the admin panel if the button is greyed out."),
    Case("cleared merges go through the reviewer queue", 0, "Merges go through the reviewer queue."),
    Case("cleared only code owners approve merge requests", 0, "Only code owners can approve merge requests."),
    Case("cleared only reviewers resolve merge conflicts", 0, "Only reviewers should resolve merge conflicts."),
    Case("cleared only we use squash merges", 0, "Only we use squash merges in this repo."),
    Case("cleared merged PRs are my responsibility to monitor", 0, "Merged PRs are my responsibility to monitor."),
    Case("cleared the reviewer's call between squash and rebase", 0, "Merges: the reviewer's call between squash and rebase."),
    Case("cleared merge commit is up to the reviewer", 0, "Squash vs. merge commit is up to the reviewer."),
    Case("cleared merging restricted to squash merges", 0, "Merging is restricted to squash merges."),
    Case("cleared check our return codes before merging", 0, "Check our return codes before merging."),
    Case("cleared our return-code checklist", 0, "Merging requires our return-code checklist."),
    Case("cleared we merge our own PRs ourselves", 0, "We merge our own PRs ourselves once CI is green."),
    Case("cleared I merge my own PRs myself", 0, "I merge my own PRs myself."),
    Case("cleared PRs aren't on hold", 0, "PRs aren't on hold."),
    Case("cleared lock the main branch for force pushes", 0, "Lock the main branch for force pushes."),
    Case("cleared lock main require signed commits", 0, "Lock main: require signed commits."),
    # Independent review of 0816ddf: 'held to' a standard and a check explanation after
    # punctuation name no merge hold; their held neighbors stay caught.
    Case("cleared PRs held to the same standard", 0, "PRs are held to the same standard and approved after CI."),
    Case("cleared PRs blocked colon failing checks", 0, "PRs are blocked: failing checks."),
    Case("cleared PRs blocked parenthesis failing checks", 0, "PRs are blocked (failing checks)."),
    Case("cleared PRs blocked semicolon the tests fail", 0, "PRs are blocked; the tests fail."),
    Case("caught integration needs my approval", 2, "Integration: needs my approval."),
    Case("caught integration PRs need my approval before the app", 2,
         "Integration: PRs need my approval before connecting the app."),
    Case("caught PRs blocked colon waiting for my approval", 2, "PRs are blocked: waiting for my approval."),
    Case("caught PRs blocked comma until I return", 2, "All PRs are blocked, until I return."),
    Case("caught PRs held for my approval", 2, "PRs are held for my approval."),
    Case("caught integration my approval then tokens clause", 2,
         "Integration: needs my approval; rotate tokens monthly."),
    Case("caught integration ask me first then services sentence", 2,
         "Integration: ask me first. Services restart after."),
    # Independent review of 06ae000: each exception clears only its reviewed idiom, so these
    # holds next to it stay caught.
    Case("caught PRs held in the queue until I approve", 2, "PRs are held in the queue until I approve."),
    Case("caught PRs held up until I approve", 2, "All PRs are held up until I approve them."),
    Case("caught PRs held to my approval", 2, "PRs are held to my approval."),
    Case("caught PRs blocked colon CI is down", 2, "All PRs are blocked: CI is down."),
    Case("caught PRs blocked comma CI included", 2, "PRs are blocked, CI included."),
    Case("caught integration my approval even for app changes", 2,
         "Integration: needs my approval, even for app changes."),
    Case("caught integration ask me first then deploy the app", 2,
         "Integration: ask me first, then deploy the app."),
    Case("caught integration app approval then ask me first", 2,
         "Integration: needs my approval for the app. Ask me first."),
    Case("caught PRs held in review pending my approval", 2, "All PRs are held in review pending my approval."),
    Case("caught PR heading integration approval and service checks", 2,
         "## Pull requests\nIntegration: only after my approval and after the service checks pass."),
    Case("caught PRs blocked checks passed awaiting the owner", 2,
         "PRs are blocked: checks passed, awaiting the owner's decision."),
    Case("caught PRs held by the merge queue until I approve", 2, "PRs are held by the merge queue until I approve."),
    Case("caught PRs held by the merge queue then wait for me", 2,
         "PRs are held by the merge queue until checks pass, then wait for me."),
    Case("caught integration PRs need my approval to connect the app", 2,
         "Integration: PRs need my approval to connect the app."),
    Case("caught integration app approval then hold all PRs", 2,
         "Integration: needs my approval for the app. Hold all PRs until I say go."),
    # Independent review of 69e25ee: a PR gate is read like a cleared rule, so a heading,
    # lead-in, later sentence or later unit still holds it; an app approval is no exception.
    Case("caught integration app approval then same for PRs", 2,
         "Integration: needs my approval for the app. Same for PRs."),
    Case("caught integration service OK then no PR without it", 2,
         "Integration: needs my OK for the service. Pas de PR sans cela."),
    Case("caught lead-in until I approve over merge queue gate", 2,
         "Until I approve:\n- PRs are held by the merge queue until checks pass."),
    Case("caught merge queue gate then I merge", 2, "PRs are held by the merge queue until checks pass. Then I merge."),
    Case("caught PRs held to a high bar until I approve", 2, "PRs are held to a high bar until I approve."),
    Case("caught PRs held to the standard below I approve", 2,
         "PRs are held to the standard below: I approve each one."),
    Case("caught PRs held to standards and held until I approve", 2,
         "Pull requests are held to quality standards, and held until I approve."),
    Case("caught integration paused until my OK for the service", 2, "Integration: paused until my OK for the service."),
    Case("caught integration my approval for every service", 2, "Integration: needs my approval for every service."),
    Case("caught freeze heading over integration app approval", 2, "# Freeze\nIntegration: needs my approval for the app."),
    Case("caught freeze heading over PRs blocked failing checks", 2,
         "## Freeze until I approve\nPRs are blocked: failing checks."),
    Case("caught PRs blocked failing checks then wait paragraph", 2,
         "PRs are blocked: failing checks.\n\nWait for my approval."),
    Case("caught PRs held to the standard then wait paragraph", 2,
         "PRs are held to the same standard.\n\nWait for my go before merging."),
    Case("caught PRs blocked failing checks then I merge", 2, "PRs are blocked: failing checks. I merge when ready."),
    Case("cleared CI heading over PRs blocked by failing checks", 0, "## CI\nPRs are blocked by failing checks."),
    # Issue #62: human blockers and freezes hold PRs even without a neighboring merge rule.
    Case("caught PRs blocked by the maintainer until further notice", 2,
         "All PRs are blocked by the maintainer until further notice."),
    Case("caught PRs blocked by the owner", 2, "PRs are blocked by the owner."),
    Case("caught PRs blocked by the maintainer", 2, "PRs are blocked by the maintainer."),
    Case("caught PRs blocked by the maintainer's decision", 2, "PRs are blocked by the maintainer's decision."),
    Case("caught PRs blocked by the owner's review", 2, "PRs are blocked by the owner's review."),
    Case("caught PRs blocked by the owner's curly possessive approval", 2, "PRs are blocked by the owner’s approval."),
    Case("caught PRs blocked by the team-lead", 2, "PRs are blocked by the team-lead."),
    Case("caught PRs blocked by the maintainers", 2, "All PRs are blocked by the maintainers."),
    Case("caught PRs blocked pending review by the owner", 2, "PRs are blocked pending review by the owner."),
    Case("caught PRs blocked by the release freeze", 2, "PRs are blocked by the release freeze."),
    Case("caught PRs blocked by me", 2, "PRs are blocked by me."),
    Case("caught PRs blocked pending maintainer approval", 2, "All PRs are blocked pending maintainer approval."),
    Case("cleared PRs blocked by failing checks standalone", 0, "PRs are blocked by failing checks."),
    Case("cleared PRs blocked by CI", 0, "PRs are blocked by CI."),
    Case("cleared PRs blocked pending CI", 0, "PRs are blocked pending CI."),
    Case("cleared PRs blocked pending checks", 0, "PRs are blocked pending checks."),
    Case("cleared draft PRs blocked by branch protection", 0, "Draft PRs are blocked by branch protection."),
    # A queue blocking PRs stays ambiguous, consistent with the existing held-by-queue cases.
    Case("conservative PRs blocked by the merge queue", 2, "PRs are blocked by the merge queue."),
    Case("caught PRs blocked by owners", 2, "PRs are blocked by the owners."),
    Case("caught PRs blocked by admin", 2, "PRs are blocked by the admin."),
    Case("caught PRs blocked by admins", 2, "PRs are blocked by the admins."),
    Case("caught PRs blocked by lead", 2, "PRs are blocked by the lead."),
    Case("caught PRs blocked by release manager", 2, "PRs are blocked by the release manager."),
    Case("caught PRs blocked by release managers", 2, "PRs are blocked by the release managers."),
    Case("caught PRs blocked by reviewer", 2, "PRs are blocked by the reviewer."),
    Case("caught PRs blocked by reviewers", 2, "PRs are blocked by the reviewers."),
    Case("caught PRs blocked by team", 2, "PRs are blocked by the team."),
    Case("caught PRs blocked by bare maintainer", 2, "PRs are blocked by maintainer."),
    Case("caught PRs blocked by a maintainer", 2, "PRs are blocked by a maintainer."),
    Case("caught PRs blocked by an admin", 2, "PRs are blocked by an admin."),
    Case("caught PRs blocked by our maintainers", 2, "PRs are blocked by our maintainers."),
    Case("caught PRs blocked by their reviewers", 2, "PRs are blocked by their reviewers."),
    Case("caught PRs blocked by my team", 2, "PRs are blocked by my team."),
    Case("caught PRs blocked by your owner", 2, "PRs are blocked by your owner."),
    Case("caught PRs blocked by his lead", 2, "PRs are blocked by his lead."),
    Case("caught PRs blocked by her release manager", 2, "PRs are blocked by her release manager."),
    Case("caught PRs blocked by code owner", 2, "PRs are blocked by the code owner."),
    Case("caught PRs blocked pending owner", 2, "PRs are blocked pending the owner."),
    Case("caught PRs blocked pending their reviewers", 2, "PRs are blocked pending their reviewers."),
    Case("caught French PRs blocked by mainteneur", 2, "Les PR sont bloquées par le mainteneur."),
    Case("caught French PRs blocked by nos mainteneurs", 2, "Les PR sont bloquées par nos mainteneurs."),
    Case("caught French PRs blocked by propriétaire", 2, "Les PR sont bloquées par le propriétaire."),
    Case("caught French PRs blocked by propriétaires", 2, "Les PR sont bloquées par les propriétaires."),
    Case("caught French PRs blocked by responsable", 2, "Les PR sont bloquées par la responsable."),
    Case("caught French PRs blocked by leurs responsables", 2, "Les PR sont bloquées par leurs responsables."),
    Case("caught French PRs blocked by mainteneuse", 2, "Les PR sont bloquées par notre mainteneuse."),
    Case("caught French PRs blocked by relecteurs", 2, "Les PR sont bloquées par les relecteurs."),
    Case("caught French PRs blocked by équipe", 2, "Les PR sont bloquées par l’équipe."),
    Case("caught French PRs blocked by un admin", 2, "Les PR sont bloquées par un admin."),
    Case("caught French PRs blocked pending propriétaire", 2, "Les PR sont bloquées en attente du propriétaire."),
    Case("caught French PRs blocked pending responsable", 2, "Les PR sont bloquées en attente de la responsable."),
    Case("caught French PRs blocked pending review by owner", 2, "Les PR sont bloquées en attente de revue par le propriétaire."),
    Case("caught PRs blocked by freeze", 2, "PRs are blocked by the freeze."),
    Case("caught PRs blocked by code freeze", 2, "PRs are blocked by the code freeze."),
    Case("caught PRs blocked by hold", 2, "PRs are blocked by a hold."),
    Case("caught PRs blocked pending embargo", 2, "PRs are blocked pending the embargo."),
    Case("caught PRs blocked by gel", 2, "PRs are blocked by the gel."),
    Case("caught French PRs blocked by gel", 2, "Les PR sont bloquées par le gel."),
    Case("cleared PRs blocked by red checks", 0, "PRs are blocked by red checks."),
    Case("cleared PRs blocked by tests", 0, "PRs are blocked by tests."),
    Case("cleared PRs blocked by builds", 0, "PRs are blocked by builds."),
    Case("cleared PRs blocked by lint", 0, "PRs are blocked by lint."),
    Case("cleared PRs blocked by required status checks", 0, "PRs are blocked by required status checks."),
    Case("cleared PRs blocked by branch protection", 0, "PRs are blocked by branch protection."),
    Case("cleared PRs blocked by maintainer bot", 0, "PRs are blocked by the maintainer bot."),
    Case("cleared PRs blocked by reviewer queue", 0, "PRs are blocked by the reviewer queue."),
    Case("cleared PRs blocked by lead time gate", 0, "PRs are blocked by lead time."),
    Case("cleared PRs blocked by lead time of checks gate", 0,
         "PRs are blocked by the lead time of the checks."),
    Case("cleared PRs blocked by possessive team CI gate", 0, "PRs are blocked by the team's CI."),
    Case("cleared PRs blocked by possessive team review queue gate", 0, "PRs are blocked by the team's review queue."),
    Case("cleared PRs blocked by plural possessive teams CI gate", 0, "PRs are blocked by the teams’ CI."),
    Case("cleared PRs blocked by plural possessive teams checks gate", 0, "PRs are blocked by the teams' checks."),
    Case("caught PRs blocked by the maintainers' decision", 2, "PRs are blocked by the maintainers' decision."),
    Case("cleared PRs blocked by team policy checks gate", 0, "PRs are blocked by team policy checks."),
    Case("cleared PRs blocked by teams CI pipeline gate", 0, "PRs are blocked by the teams CI pipeline."),
    Case("cleared PRs blocked by hold-the-line tests gate", 0, "PRs are blocked by hold-the-line tests."),
    Case("cleared PRs not blocked by the owner", 0, "PRs are not blocked by the owner."),
    Case("cleared PRs blocked pending tests", 0, "PRs are blocked pending tests."),
    Case("caught French PRs blocked pending propriétaires", 2, "Les PR sont bloquées en attente des propriétaires."),
    Case("caught PRs blocked by us", 2, "PRs are blocked by us."),
    Case("caught French PRs blocked by moi", 2, "Les PR sont bloquées par moi."),
    # Issue #62 round 4: fixed blocker vocabulary; condition words never skip to a gate.
    Case("caught PRs blocked by owner then em dash condition", 2,
         "PRs are blocked by the owner—until further notice."),
    Case("caught PRs blocked by maintainer then em dash reference", 2,
         "PRs are blocked by the maintainer—see issue 12."),
    Case("caught PRs blocked by repository owner", 2, "PRs are blocked by the repository owner."),
    Case("caught PRs blocked by repo owner", 2, "PRs are blocked by the repo owner."),
    Case("caught PRs blocked by project maintainer", 2, "PRs are blocked by the project maintainer."),
    Case("caught PRs blocked by core maintainers", 2, "PRs are blocked by the core maintainers."),
    Case("caught PRs blocked by security team", 2, "PRs are blocked by the security team."),
    Case("caught PRs blocked by core team", 2, "PRs are blocked by the core team."),
    Case("caught PRs blocked by my decision", 2, "PRs are blocked by my decision."),
    Case("caught PRs blocked by our decision", 2, "PRs are blocked by our decision."),
    Case("cleared PRs blocked by code owner review requirement", 0,
         "PRs are blocked by the code owner review requirement."),
    Case("cleared PRs blocked by owner review policy", 0, "PRs are blocked by the owner review policy."),
    Case("cleared PRs blocked by team protection rules", 0, "PRs are blocked by the team protection rules."),
    Case("cleared PRs blocked by admin enforced branch protection", 0,
         "PRs are blocked by admin enforced branch protection."),
    Case("cleared PRs blocked by admin enforcement", 0, "PRs are blocked by admin enforcement."),
    Case("cleared PRs blocked by reviewer requirement", 0, "PRs are blocked by the reviewer requirement."),
    Case("cleared PRs blocked by reviewers count", 0, "PRs are blocked by the reviewers count."),
    Case("cleared PRs blocked by lead times", 0, "PRs are blocked by lead times."),
    Case("cleared PRs blocked by Teams notifications", 0, "PRs are blocked by Teams notifications."),
    Case("cleared PRs blocked by human review check", 0, "PRs are blocked by the human review check."),
    Case("caught PRs blocked by freeze window", 2, "PRs are blocked by the freeze window."),
    Case("caught PRs blocked by hold period", 2, "PRs are blocked by the hold period."),
    Case("caught PRs blocked by reviewer until checks pass", 2,
         "PRs are blocked by the reviewer until checks pass."),
    Case("caught PRs blocked by maintainer if CI fails", 2,
         "PRs are blocked by the maintainer if CI fails."),
    Case("caught PRs blocked by feature freeze", 2, "PRs are blocked by the feature freeze."),
    Case("caught PRs blocked by deploy freeze", 2, "PRs are blocked by the deploy freeze."),
    Case("caught PRs blocked by holiday freeze", 2, "PRs are blocked by the holiday freeze."),
    Case("caught PRs blocked by security embargo", 2, "PRs are blocked by the security embargo."),
    Case("caught PRs blocked by legal hold", 2, "PRs are blocked by a legal hold."),
    Case("caught PRs blocked by holds", 2, "PRs are blocked by holds."),
    Case("caught PRs blocked by freezes", 2, "PRs are blocked by freezes."),
    Case("caught PRs blocked by embargoes", 2, "PRs are blocked by embargoes."),
    Case("caught PRs blocked by hyphenated code freeze", 2, "PRs are blocked by the code-freeze."),
    Case("caught PRs blocked by hyphenated release freeze", 2, "PRs are blocked by the release-freeze."),
    Case("cleared PRs blocked by freeze bot", 0, "PRs are blocked by the freeze bot."),
    Case("caught PRs blocked pending a review by owner", 2, "PRs are blocked pending a review by the owner."),
    Case("caught PRs blocked pending code review by owner", 2, "PRs are blocked pending code review by the owner."),
    Case("caught PRs blocked by owner's request", 2, "PRs are blocked by the owner's request."),
    Case("cleared PRs blocked pending review", 0, "PRs are blocked pending review."),
    Case("caught French PRs blocked by administrateur", 2, "Les PR sont bloquées par l'administrateur."),
    Case("caught French PRs blocked by mainteneur decision", 2,
         "Les PR sont bloquées par la décision du mainteneur."),
    Case("cleared French PRs blocked by CI", 0, "Les PR sont bloquées par la CI."),
    Case("cleared French PRs blocked by tests", 0, "Les PR sont bloquées par les tests."),
    Case("caught French PRs blocked by chef de projet", 2, "Les PR sont bloquées par le chef de projet."),
    Case("cleared PRs blocked by maintainer checks", 0, "PRs are blocked by the maintainer checks."),
    Case("cleared PRs blocked by team builds", 0, "PRs are blocked by the team builds."),
    Case("cleared PRs blocked by admin rules", 0, "PRs are blocked by the admin rules."),
    Case("cleared PRs blocked by owner workflows", 0, "PRs are blocked by the owner workflows."),
    Case("cleared French PRs blocked by équipe vérifications", 0,
         "Les PR sont bloquées par l'équipe vérifications."),
    Case("cleared French PRs blocked by règles de équipe", 0,
         "Les PR sont bloquées par les règles de l'équipe."),
    # Neighboring condition words and hyphenated gate/tool nouns use the same bounded path.
    Case("caught PRs blocked by reviewer unless checks fail", 2,
         "PRs are blocked by the reviewer unless checks fail."),
    Case("caught PRs blocked by reviewer when checks pass", 2,
         "PRs are blocked by the reviewer when checks pass."),
    Case("caught PRs blocked by reviewer after checks pass", 2,
         "PRs are blocked by the reviewer after checks pass."),
    Case("caught PRs blocked by reviewer before checks pass", 2,
         "PRs are blocked by the reviewer before checks pass."),
    Case("caught PRs blocked by reviewer pending checks", 2,
         "PRs are blocked by the reviewer pending checks."),
    Case("caught French PRs blocked by mainteneur jusqu checks", 2,
         "Les PR sont bloquées par le mainteneur jusqu'à la réussite des tests."),
    Case("caught French PRs blocked by mainteneur si CI fails", 2,
         "Les PR sont bloquées par le mainteneur si la CI échoue."),
    Case("caught French PRs blocked by mainteneur tant que tests fail", 2,
         "Les PR sont bloquées par le mainteneur tant que les tests échouent."),
    Case("caught French PRs blocked by mainteneur quand tests pass", 2,
         "Les PR sont bloquées par le mainteneur quand les tests passent."),
    Case("cleared PRs blocked by hyphenated owner checks", 0, "PRs are blocked by the owner-checks."),
    Case("cleared PRs blocked by hyphenated maintainer bot", 0, "PRs are blocked by the maintainer-bot."),
    Case("caught French PRs blocked by administratrice", 2, "Les PR sont bloquées par l'administratrice."),
    Case("cleared PRs blocked by maintainer tests", 0, "PRs are blocked by the maintainer tests."),
    Case("cleared PRs blocked by maintainer lint", 0, "PRs are blocked by the maintainer lint."),
    Case("cleared PRs blocked by maintainer pipelines", 0, "PRs are blocked by the maintainer pipelines."),
    Case("cleared PRs blocked by maintainer jobs", 0, "PRs are blocked by the maintainer jobs."),
    Case("cleared PRs blocked by maintainer status", 0, "PRs are blocked by the maintainer status."),
    Case("cleared PRs blocked by maintainer runs", 0, "PRs are blocked by the maintainer runs."),
    # Issue #62 round 5: the head of the noun phrase after a person decides. A gate or tool head
    # clears whatever modifies it; any other head, a boundary word or punctuation keeps the hold.
    Case("cleared PRs blocked by team unit tests", 0, "PRs are blocked by team unit tests."),
    Case("cleared PRs blocked by maintainer automated checks", 0,
         "PRs are blocked by the maintainer automated checks."),
    Case("cleared PRs blocked by team security checks", 0, "PRs are blocked by the team security checks."),
    Case("cleared PRs blocked by reviewer assignment queue", 0,
         "PRs are blocked by the reviewer assignment queue."),
    Case("cleared PRs blocked by maintainer review bot", 0, "PRs are blocked by the maintainer review bot."),
    Case("cleared PRs blocked by owner's CI", 0, "PRs are blocked by the owner's CI."),
    Case("cleared PRs blocked by maintainers' required checks", 0,
         "PRs are blocked by the maintainers’ required checks."),
    Case("cleared PRs blocked by owner-run checks", 0, "PRs are blocked by the owner-run checks."),
    Case("cleared PRs blocked by team unit tests until they pass", 0,
         "PRs are blocked by team unit tests until they pass."),
    Case("cleared PRs blocked by team unit tests then relative clause", 0,
         "PRs are blocked by team unit tests, which run nightly."),
    Case("cleared PRs blocked by release manager checklist", 0,
         "PRs are blocked by the release manager checklist."),
    Case("caught PRs blocked by owner's final decision", 2, "PRs are blocked by the owner's final decision."),
    Case("caught PRs blocked by maintainer's explicit approval", 2,
         "PRs are blocked by the maintainer's explicit approval."),
    Case("caught PRs blocked by team lead's sign-off", 2, "PRs are blocked by the team lead's sign-off."),
    Case("caught PRs blocked by owner this time", 2, "PRs are blocked by the owner this time."),
    Case("caught PRs blocked by owner and CI", 2, "PRs are blocked by the owner and the CI."),
    Case("caught PRs blocked by owner of CI", 2, "PRs are blocked by the owner of the CI."),
    Case("caught PRs blocked by owner not checks", 2, "PRs are blocked by the owner, not the checks."),
    Case("caught PRs blocked by owner then dash about checks", 2,
         "PRs are blocked by the owner — checks are irrelevant."),
    Case("caught PRs blocked by reviewer until checks", 2, "PRs are blocked by the reviewer until checks."),
    Case("cleared PRs blocked by team four modifiers before tests", 0,
         "PRs are blocked by the team nightly automated security unit tests."),
    Case("caught French PRs blocked by mainteneur du dépôt", 2,
         "Les PR sont bloquées par le mainteneur du dépôt."),
    Case("cleared PRs blocked by our CI", 0, "PRs are blocked by our CI."),
    # Issue #62 round 7: a concession ends the phrase, no word cap before a gate or freeze head,
    # a gate's outage, runner or timeout is still the gate, and a freeze or human-decision word
    # in a modifier, or a failure to act, keeps the person.
    Case("caught PRs blocked by owner although CI green", 2, "PRs are blocked by the owner although CI is green."),
    Case("caught PRs blocked by owner even though CI green", 2,
         "PRs are blocked by the owner even though CI is green."),
    Case("caught PRs blocked by owner though CI green", 2, "PRs are blocked by the owner though CI is green."),
    Case("caught PRs blocked by owner despite green CI", 2, "PRs are blocked by the owner despite green CI."),
    Case("caught PRs blocked by owner once CI green", 2, "PRs are blocked by the owner once CI is green."),
    Case("caught PRs blocked by owner yet CI green", 2, "PRs are blocked by the owner yet CI is green."),
    Case("caught PRs blocked by three modifiers before a code freeze", 2,
         "PRs are blocked by the big annual holiday code freeze."),
    Case("caught PRs blocked by hyphenated end-of-year code freeze", 2,
         "PRs are blocked by the annual end-of-year holiday code freeze."),
    Case("caught PRs blocked by owner's long final release decision", 2,
         "PRs are blocked by the owner's long overdue final release decision."),
    Case("caught PRs blocked by owner until long gate phrase passes", 2,
         "PRs are blocked by the owner until the nightly automated security unit tests pass."),
    Case("cleared PRs blocked by reviewer pull request assignment queue", 0,
         "PRs are blocked by the reviewer pull request assignment queue."),
    Case("cleared PRs blocked by maintainer automated code review bot", 0,
         "PRs are blocked by the maintainer automated code review bot."),
    Case("cleared PRs blocked by team's CI outage", 0, "PRs are blocked by the team's CI outage."),
    Case("cleared PRs blocked by team's CI outages", 0, "PRs are blocked by the team's CI outages."),
    Case("cleared PRs blocked by team's flaky CI runner", 0, "PRs are blocked by the team's flaky CI runner."),
    Case("cleared PRs blocked by team's CI timeouts", 0, "PRs are blocked by the team's CI timeouts."),
    Case("cleared PRs blocked by maintainer's GitHub Actions runner", 0,
         "PRs are blocked by the maintainer's GitHub Actions runner."),
    Case("caught PRs blocked by owner's failure to review", 2, "PRs are blocked by the owner's failure to review."),
    Case("caught PRs blocked by owner's failure to decide", 2, "PRs are blocked by the owner's failure to decide."),
    Case("caught PRs blocked by owner's final decision gate", 2, "PRs are blocked by the owner's final decision gate."),
    Case("caught PRs blocked by owner's manual review gate", 2, "PRs are blocked by the owner's manual review gate."),
    Case("caught PRs blocked by owner's manual approval", 2, "PRs are blocked by the owner's manual approval."),
    Case("caught PRs blocked by maintainer's manual gate", 2, "PRs are blocked by the maintainer's manual gate."),
    Case("cleared PRs blocked by team's manual QA tests", 0, "PRs are blocked by the team's manual QA tests."),
    Case("cleared PRs blocked by team's requirements to pass CI", 0,
         "PRs are blocked by the team's requirements to pass CI."),
    Case("caught PRs blocked by owner's requirement to approve", 2,
         "PRs are blocked by the owner's requirement to approve."),
    Case("caught PRs blocked by owner notwithstanding green CI", 2,
         "PRs are blocked by the owner notwithstanding green CI."),
    Case("caught PRs blocked by owner albeit CI is green", 2, "PRs are blocked by the owner albeit CI is green."),
    Case("caught PRs blocked by owner however CI is green", 2, "PRs are blocked by the owner however CI is green."),
    Case("caught PRs blocked by owner's code-freeze gate", 2, "PRs are blocked by the owner's code-freeze gate."),
    Case("caught PRs blocked by owner's hold-period gate", 2, "PRs are blocked by the owner's hold-period gate."),
    Case("caught PRs blocked by owner's freeze-gate", 2, "PRs are blocked by the owner's freeze-gate."),
    Case("cleared PRs blocked by owner's CI failure", 0, "PRs are blocked by the owner's CI failure."),
    # Issue #62 round 6: any freeze or hold modifier, quantifiers, an unspaced em dash, more
    # adverbs and gate heads; only lead time clears among time words.
    Case("caught PRs blocked by current release freeze", 2, "PRs are blocked by the current release freeze."),
    Case("caught PRs blocked by ongoing freeze", 2, "PRs are blocked by an ongoing freeze."),
    Case("caught PRs blocked by holiday code freeze", 2, "PRs are blocked by the holiday code freeze."),
    Case("caught PRs blocked by year-end freeze", 2, "PRs are blocked by the year-end freeze."),
    Case("caught PRs blocked by Q4 freeze", 2, "PRs are blocked by the Q4 freeze."),
    Case("caught PRs blocked by temporary hold", 2, "PRs are blocked by a temporary hold."),
    Case("caught PRs blocked by hyphenated freeze-period", 2, "PRs are blocked by the freeze-period."),
    Case("caught PRs blocked by both maintainers", 2, "PRs are blocked by both maintainers."),
    Case("caught PRs blocked by all maintainers", 2, "PRs are blocked by all maintainers."),
    Case("caught PRs blocked by any maintainer", 2, "PRs are blocked by any maintainer."),
    Case("caught PRs blocked by two maintainers", 2, "PRs are blocked by two maintainers."),
    Case("caught PRs blocked by the other maintainers", 2, "PRs are blocked by the other maintainers."),
    Case("caught PRs blocked by owner then unspaced em dash before checks", 2,
         "PRs are blocked by the owner—checks are irrelevant."),
    Case("caught PRs blocked by maintainer then unspaced em dash before CI", 2,
         "PRs are blocked by the maintainer—CI is not enough."),
    Case("caught PRs blocked by owner then unspaced em dash before a CI list", 2,
         "PRs are blocked by the owner—CI, tests, nothing else matters."),
    Case("caught PRs blocked by owner then unspaced horizontal bar before CI", 2,
         "PRs are blocked by the owner―CI is irrelevant."),
    Case("caught PRs blocked by maintainer then unspaced em dash before status", 2,
         "PRs are blocked by the maintainer—status: frozen."),
    Case("caught ordered item PRs blocked by owner then unspaced em dash", 2,
         "1) PRs are blocked by the owner—CI is green."),
    Case("caught quoted ordered item PRs blocked by owner then unspaced em dash", 2,
         "> 1) PRs are blocked by the owner—CI is green."),
    Case("caught wrapped quoted ordered item PRs blocked by owner then unspaced em dash", 2,
         "> 1) PRs are blocked by the\n>    owner—CI is green."),
    Case("caught wrapped PRs blocked by owner then unspaced em dash after a heading and blank line", 2,
         "# Rules\n\nPRs are blocked by the\nowner—CI is green."),
    Case("caught wrapped PRs blocked by owner then unspaced em dash right after a heading", 2,
         "# Rules\nPRs are blocked by the\nowner—CI is green."),
    Case("caught wrapped PRs blocked by owner then unspaced em dash after a setext heading", 2,
         "Rules\n=====\nPRs are blocked by the\nowner—CI is green."),
    Case("caught wrapped list item PRs blocked by owner then unspaced em dash after a sibling", 2,
         "- Tests run on CI\n- PRs are blocked by the\n  owner—CI is green."),
    Case("caught wrapped PRs blocked by maintainer then unspaced em dash after an unpunctuated paragraph", 2,
         "Read the guide\n\nPRs are blocked by the\nmaintainer—status: frozen."),
    Case("caught wrapped quoted PRs blocked by owner then unspaced em dash after a quoted note", 2,
         "> Note\n>\n> PRs are blocked by the\n> owner—CI is green."),
    # Round 9b: a dash opening the wrapped line still separates the hold from what follows.
    Case("caught wrapped PRs blocked by owner with em dashes on the next line", 2,
         "PRs are blocked by the\n—owner—CI is green."),
    Case("caught wrapped list item PRs blocked by owner with em dashes on the continuation", 2,
         "- PRs are blocked by the\n  —owner—CI is green."),
    Case("caught wrapped PRs blocked by owner with an em dash opening the last line", 2,
         "PRs are blocked by the\n—owner."),
    # Round 10: a spaced dash opening a line is a list marker, never an indent under its siblings.
    Case("caught em dash item Never then hyphen item merge", 2, "— Never\n- merge PRs"),
    Case("caught horizontal bar item Never then hyphen item merge", 2, "― Never\n- merge PRs"),
    Case("caught em dash item Never then asterisk item merge", 2, "— Never\n* merge PRs"),
    Case("caught em dash item Never then numbered item merge", 2, "— Never\n1. merge PRs"),
    Case("caught em dash item Don't unless I approve then hyphen item merge", 2,
         "— Don't, unless I approve\n- merge"),
    Case("caught em dash item after a heading line then hyphen item merge", 2,
         "Rules\n— Don't, unless I approve\n- merge"),
    Case("caught hyphen items Do not then em dash item merge", 2,
         "- Do not do the following\n- Not now\n— merge"),
    Case("caught quoted em dash item Never then quoted hyphen item merge", 2, "> — Never\n> - merge PRs"),
    Case("caught em dash item Never with a tab then hyphen item merge", 2, "—\tNever\n- merge PRs"),
    # Any indent before the dash counts, as for a list marker, including a lone CR line ending and
    # control characters read as invisible.
    Case("caught Ogham space em dash item Never then hyphen item merge", 2,
         "\N{OGHAM SPACE MARK}— Never\n\N{OGHAM SPACE MARK}- merge PRs"),
    Case("caught line separator em dash item Never then hyphen item merge", 2,
         "\N{LINE SEPARATOR}— Never\n\N{LINE SEPARATOR}- merge PRs"),
    Case("caught paragraph separator horizontal bar item Never then asterisk item merge", 2,
         "\N{PARAGRAPH SEPARATOR}― Never\n\N{PARAGRAPH SEPARATOR}* merge PRs"),
    Case("caught em dash item Never after lone CR line endings then hyphen item merge", 2,
         "x\r— Never\r- merge PRs"),
    Case("caught next line control em dash item Never then hyphen item merge", 2, "\x85— Never\n- merge PRs"),
    Case("caught vertical tab em dash item Never then hyphen item merge", 2, "\x0b— Never\n- merge PRs"),
    # A whitespace control character is also invisible: removed, it leaves the dash unspaced, so the
    # dash is still read as a hyphen, as on main.
    Case("caught French let me merge with vertical tabs around an em dash", 2,
         "Laisse\x0b—\x0bmoi fusionner."),
    Case("caught French let me merge with form feeds around a horizontal bar", 2,
         "Laisse\x0c―\x0cmoi fusionner."),
    Case("caught French ask me first with next line controls around an em dash", 2,
         "Avant de fusionner, demande\x85—\x85moi."),
    Case("caught my sign-off with record separators around an em dash", 2,
         "Merging requires my sign\x1e—\x1eoff."),
    Case("orphan end marker with vertical tabs around an em dash", 2,
         "<!-- github\x0b—\x0bworkflow:end -->"),
    Case("unclosed start marker with vertical tabs around an em dash", 2,
         "<!-- github\x0b—\x0bworkflow:start v6.2 -->\nExample <!-- github-workflow:end -->"),
    Case("caught PRs blocked by release manager's freeze policy", 2,
         "PRs are blocked by the release manager's freeze policy."),
    Case("caught PRs blocked by release manager's code freeze policy", 2,
         "PRs are blocked by the release manager's code freeze policy."),
    Case("caught PRs blocked by team's freeze rules", 2, "PRs are blocked by the team's freeze rules."),
    Case("caught PRs blocked by owner's freeze notifications", 2,
         "PRs are blocked by the owner's freeze notifications."),
    Case("caught PRs blocked by maintainer's hold list", 2, "PRs are blocked by the maintainer's hold list."),
    Case("caught PRs blocked by owner's hold status", 2, "PRs are blocked by the owner's hold status."),
    Case("caught PRs still blocked by maintainer", 2, "PRs are still blocked by the maintainer."),
    Case("caught PRs blocked again by owner", 2, "PRs are blocked again by the owner."),
    Case("caught French PRs encore bloquées par mainteneur", 2, "Les PR sont encore bloquées par le mainteneur."),
    Case("caught French PRs toujours bloquées par mainteneur", 2, "Les PR sont toujours bloquées par le mainteneur."),
    Case("cleared PRs blocked pending code owner review gate", 0, "PRs are blocked pending code owner review."),
    Case("cleared PRs blocked by code owner review gate", 0, "PRs are blocked by code owner review."),
    Case("cleared PRs blocked by team CI failures", 0, "PRs are blocked by team CI failures."),
    Case("cleared PRs blocked by team quality gates", 0, "PRs are blocked by team quality gates."),
    Case("cleared PRs blocked by team linters", 0, "PRs are blocked by the team linters."),
    Case("cleared PRs blocked by team's linting", 0, "PRs are blocked by the team's linting."),
    Case("cleared PRs blocked by team's end-to-end tests", 0, "PRs are blocked by the team's end-to-end tests."),
    Case("cleared PRs blocked by team's GitHub Actions", 0, "PRs are blocked by the team's GitHub Actions."),
    Case("cleared PRs blocked by security team scan", 0, "PRs are blocked by the security team scan."),
    Case("cleared PRs blocked by maintainer's type checker", 0, "PRs are blocked by the maintainer's type checker."),
    Case("cleared PRs blocked by owner's required GitHub status checks", 0,
         "PRs are blocked by the owner's required GitHub status checks."),
    Case("cleared PRs blocked by team's CI today", 0, "PRs are blocked by the team's CI today."),
    Case("cleared PRs blocked by maintainer checks right now", 0, "PRs are blocked by maintainer checks right now."),
    Case("caught PRs blocked by owner today", 2, "PRs are blocked by the owner today."),
    Case("caught PRs blocked by owner's vacation time", 2, "PRs are blocked by the owner's vacation time."),
    Case("caught PRs blocked by owner's busy times", 2, "PRs are blocked by the owner's busy times."),
    Case("caught PRs blocked by maintainer review time", 2, "PRs are blocked by maintainer review time."),
    # Independent review of 337c85b: a gate's own words are not a hold, but the rest of its
    # sentence still qualifies a merge-method rule anywhere in the file, in any layout.
    Case("caught PRs blocked by the maintainer then method rule", 2,
         "All PRs are blocked by the maintainer until further notice.\n\n"
         "Do not use squash merges; use merge commits."),
    Case("caught method rule then PRs blocked by the maintainer", 2,
         "Do not use squash merges; use merge commits.\n\n"
         "All PRs are blocked by the maintainer until further notice."),
    Case("caught PRs blocked by the owner section beside method section", 2,
         "## Pull requests\nAll PRs are blocked by the owner for now.\n\n"
         "## Merge method\nDo not use squash merges; use merge commits."),
    Case("caught PRs held in the queue until Friday item beside method item", 2,
         "- PRs are held in the queue until Friday.\n- Do not use squash merges; use merge commits."),
    Case("caught PRs held in the queue table row beside method row", 2,
         "| Rule | Detail |\n|---|---|\n| Queue | PRs are held in the queue until Friday. |\n"
         "| Method | Do not use squash merges; use merge commits. |"),
    Case("caught nested PRs blocked by the maintainer beside nested method rule", 2,
         "- Pull requests\n  - All PRs are blocked by the maintainer until further notice.\n"
         "- Merging\n  - Do not use squash merges; use merge commits."),
    Case("caught method rule then later PRs held in the queue", 2,
         "## Merging\nDo not use squash merges; use merge commits.\n\nPRs are held in the queue until Friday."),
    Case("caught bold PRs held in the queue then method rule", 2,
         "**PRs are held in the queue until Friday.**\n\nDo not use squash merges; use merge commits."),
    Case("caught until the maintainer is back PRs blocked then method rule", 2,
         "Until the maintainer is back, PRs are blocked.\n\nNever create a merge commit; use squash merges."),
    Case("caught PRs held to the standard and wait then method rule", 2,
         "PRs are held to the same standard, and wait for the maintainer.\n\n"
         "Never create a merge commit; use squash merges."),
    Case("caught PRs blocked by failing checks owner final say then method rule", 2,
         "PRs are blocked by failing checks, and the owner has the final say.\n\n"
         "Never create a merge commit; use squash merges."),
    Case("caught owner holds all PRs they are blocked then method rule", 2,
         "The owner holds all PRs; they are blocked.\n\nDo not use squash merges; use merge commits."),
    Case("caught PRs held up until the owner is back then upstream rule", 2,
         "PRs are held up until the owner is back.\n\n- Never merge from the upstream remote."),
    Case("caught French PRs blocked by the maintainer then method rule", 2,
         "Les PR sont bloquées par le mainteneur jusqu’à nouvel ordre.\n\n"
         "Do not use squash merges; use merge commits."),
    # Codex review of ec5c0bc: the words before a gate verb are the gate's subject, not its
    # mechanics, so an owner's wait or decision there still qualifies a method rule.
    Case("caught PRs wait for the owner's final say blocked by CI then method rule", 2,
         "PRs must wait for the owner's final say and are blocked by CI.\n\n"
         "Do not use squash merges; use merge commits."),
    Case("caught method rule then PRs wait for the owner's final say blocked by CI", 2,
         "Do not use squash merges; use merge commits.\n\n"
         "PRs must wait for the owner's final say and are blocked by CI."),
    Case("caught PRs subject to the owner's decision blocked by checks then method rule", 2,
         "PRs remain subject to the owner's decision and are blocked by failing checks.\n\n"
         "Do not use squash merges; use merge commits."),
    Case("caught method rule then PRs subject to the owner's decision blocked by checks", 2,
         "Do not use squash merges; use merge commits.\n\n"
         "PRs remain subject to the owner's decision and are blocked by failing checks."),
    Case("caught PRs await the owner's final say blocked by CI then method rule", 2,
         "All PRs await the owner's final say and are blocked by CI.\n\n"
         "Do not use squash merges; use merge commits."),
    Case("caught method rule then PRs await the owner's final say blocked by CI", 2,
         "Do not use squash merges; use merge commits.\n\n"
         "All PRs await the owner's final say and are blocked by CI."),
    Case("cleared PRs wait for CI and are blocked by failing checks", 0,
         "PRs wait for CI and are blocked by failing checks."),
    Case("cleared PRs blocked until CI passes then method rule", 0,
         "PRs are blocked until CI passes.\n\nDo not use squash merges; use merge commits."),
    Case("cleared method section then CI section PRs blocked by failing checks", 0,
         "## Merging\nDo not use squash merges; use merge commits.\n\n## CI\nPRs are blocked by failing checks."),
    Case("cleared PRs held to the standard then method rule", 0,
         "PRs are held to the same standard.\n\nDo not use squash merges; use merge commits."),
    Case("cleared PRs blocked failing checks item beside method item", 0,
         "- PRs are blocked: failing checks.\n- Never create a merge commit; use squash merges."),
    # Accepted false 2s: stopping costs one question, a false 0 an unwanted merge. Asking code
    # owners by name reads as asking people; a merge done 'myself' reads as a reservation; a
    # hold until CI passes and a frozen main anywhere in a sentence read as holds. A speaker's
    # approval of an app under an 'Integration:' label and a merge queue that holds PRs until
    # checks pass read as holds: the exceptions tried for them cleared real holds nearby.
    Case("conservative integration my approval to connect the app", 2, "Integration: needs my approval to connect the app."),
    Case("conservative integration my approval for the Slack app", 2, "Integration: needs my approval for the Slack app."),
    Case("conservative PRs held by the merge queue until checks pass", 2,
         "PRs are held by the merge queue until checks pass."),
    Case("conservative integration app approval then PRs merge after CI", 2,
         "Integration: needs my approval to connect the app. PRs may merge after CI."),
    Case("conservative check with the code owners before merging", 2,
         "Before merging, check with the code owners listed in CODEOWNERS."),
    Case("conservative we merge dependency updates ourselves", 2, "We merge dependency updates ourselves on Fridays."),
    Case("conservative I rebase and merge small fixes myself", 2, "I usually rebase and merge small fixes myself."),
    Case("conservative hold PRs until CI passes", 2, "Hold PRs until CI passes."),
    # The rest of a gate sentence beside a method rule is read for a hold, so an approval word
    # there stops, even for CI: the gate words are the only ones set aside.
    Case("conservative PRs held to the standard and approved after CI beside method rule", 2,
         "PRs are held to the same standard and approved after CI.\n\n"
         "Do not use squash merges; use merge commits."),
    # The words before a gate verb stay readable, so a CI wait written there reads like an
    # owner's wait beside a method rule; a paragraph after a gate is read as the gate's section.
    Case("conservative PRs wait for CI blocked by failing checks then method rule", 2,
         "PRs wait for CI and are blocked by failing checks.\n\n"
         "Do not use squash merges; use merge commits."),
    Case("conservative PRs are waiting for CI blocked by failing checks then method rule", 2,
         "PRs are waiting for CI and are blocked by failing checks.\n\n"
         "Do not use squash merges; use merge commits."),
    Case("conservative CI section PRs blocked by failing checks then checks paragraph", 2,
         "## CI\nPRs are blocked by failing checks.\n\nChecks run automatically."),
    Case("conservative main is frozen in a screenshot", 2, "Main is frozen in the screenshot below."),
)

# Issue #13: a list item nests only from its parent's content column (CommonMark), so an item indented less
# than that column is a sibling. That reading only adds holds: the marker-column nesting still gives the
# context, so a jittered item and everything after it keep the parent its author may have meant. The rows
# pin both sides of each boundary: marker width, padding, tabs and quotes.
LIST_COLUMN_CASES = (
    Case("list column required 1", 2, ' - Never\n- merge PRs'),
    Case("list column required 2", 2, " - Don't, unless I approve\n- merge"),
    Case("list column required 3", 2, '  - Do not\n- merge'),
    Case("list column root jitter 1", 2, '   - Never\n  - merge PRs'),
    Case("list column root jitter 2", 2, '  - Never\n - merge PRs'),
    Case("list column complete sibling 1", 0, ' - Do not add dependencies\n- Merge requests use squash'),
    Case("list column jitter reads both ways 1", 2, '- Do not add dependencies\n - Merge requests use squash'),
    Case("list column jitter reads both ways 2", 2, '- No agent may\n - merge PRs'),
    Case("list column jitter reads both ways 3", 2, '1. No agent may\n  - merge PRs'),
    Case("list column jitter later items 1", 2, '- No agent may\n - push to main\n - merge PRs'),
    Case("list column jitter later items 2", 2, '1. No agent may\n  - push tags\n  - merge PRs'),
    Case("list column jitter later items 3", 2, '- Never\n - push to main\n   - merge PRs'),
    Case("list column jitter later items 4", 2, '- No agent may\n - push to main\n\n   merge PRs'),
    Case("list column false nesting 1", 2, '- Notes\n - Never\n- merge PRs'),
    Case("list column real nesting 1", 0, '- Notes\n  - Never\n- merge PRs'),
    Case("list column root lead survives child 1", 2, '- Never\n  - notes\n- merge PRs'),
    Case("list column child lead child sibling 1", 2, '- Notes\n  - Never\n  - merge PRs'),
    Case("list column child lead expires 1", 0, '- Notes\n  - Never\n    - docs\n- merge PRs'),
    Case("list column return to child scope 1", 2, '- Notes\n  - Never\n    - docs\n  - merge PRs'),
    Case("list column ordered width 2 1", 2, '1. Notes\n  - Never\n2. merge PRs'),
    Case("list column ordered width 2 2", 0, '1. Notes\n   - Never\n2. merge PRs'),
    Case("list column ordered width 3 1", 2, '10) Notes\n   - Never\n11) merge PRs'),
    Case("list column ordered width 3 2", 0, '10) Notes\n    - Never\n11) merge PRs'),
    Case("list column ordered max width 1", 2, '123456789. Notes\n          - Never\n- merge PRs'),
    Case("list column ordered max width 2", 0, '123456789. Notes\n           - Never\n- merge PRs'),
    Case("list column four-space padding 1", 2, '-    Notes\n    - Never\n- merge PRs'),
    Case("list column four-space padding 2", 0, '-    Notes\n     - Never\n- merge PRs'),
    Case("list column excess padding 1", 0, '-     Notes\n  - Never\n- merge PRs'),
    Case("list column empty marker 1", 0, '-\n  - Never\n- merge PRs'),
    Case("list column empty marker 2", 0, '-   \n  - Never\n- merge PRs'),
    Case("list column non-ASCII digits 1", 2, '\u0661. Notes\n  - Never\n- merge PRs'),
    Case("list column leading tab 1", 0, '  - Notes\n\t- Never\n- merge PRs'),
    Case("list column marker tab 1", 2, '1.\tNotes\n   - Never\n2. merge PRs'),
    Case("list column marker tab 2", 0, '1.\tNotes\n    - Never\n2. merge PRs'),
    Case("list column quote 1", 2, '>  - Never\n> - merge PRs'),
    Case("list column quote 2", 0, '> - Notes\n>   - Never\n> - merge PRs'),
    Case("list column mixed families 1", 2, ' - Never\n* merge PRs'),
    Case("list column mixed families 2", 2, ' - Never\n1. merge PRs'),
    # The content-column chain resets with the marker-column chain: at a heading, after a paragraph and at a
    # shallower sibling item.
    Case("list column reset heading", 0, '- Never\n# H\n - merge PRs'),
    Case("list column reset paragraph", 0, '- Never\n\nDone.\n\n - merge PRs'),
    Case("list column reset sibling", 0, '- Notes\n  - Never\n- Other\n  - merge PRs'),
)
# Issue #73: a negated lead-in item governs its own children and its direct later siblings, as before. A
# childless dangling one ('- Never', '- Do not do the following', '- No agent may') also governs what is
# nested in its later siblings, including continuations and fenced blocks, until the list ends or an item
# sits left of it ("list column reset sibling" above); a lead-in with its own object or children does not
# ('- Don't:' above '- Do:'). A merge label ('Merges', 'Merging PRs', 'Self-merge', not 'Rebase merging'
# or 'Merged') answered by a bare refusal, or by waiting for the person who decides, is a hold. So is a
# merge item under a parent that names an approval, read as the one sentence 'Merge PRs until I approve'.
# A list item that states a status ('- Not applicable', '- Tests: not applicable') introduces nothing,
# unless it ends in a colon.
LEAD_SCOPE_CASES = (
    Case("lead scope later sibling child", 2, '- Never\n- Rules\n  - merge PRs'),
    Case("lead scope pointer later sibling child", 2, '- Do not do the following\n- Notes\n  - merge PRs'),
    Case("lead scope later sibling continuation", 2, '- No agent may\n- push to main\n\n  merge PRs'),
    Case("lead scope later sibling fence", 2, '- Never\n- x\n  ```\n  merge PRs\n  ```'),
    Case("lead scope nested lead fence", 0, '- Never\n  - x\n- Rules\n  ```\n  git merge main\n  ```'),
    Case("lead scope dangling modal sibling", 2, '- No agent may\n- merge PRs'),
    Case("lead scope parent child", 2, '- Never\n  - merge PRs'),
    Case("lead scope sibling", 2, '- Never\n- merge PRs'),
    Case("lead scope not yet", 2, '- Not yet\n- merge PRs'),
    Case("lead scope not until", 2, '- Not until I approve\n- merge PRs'),
    Case("lead scope ends with list", 0, '- Never\n- Rules\n\nMerge PRs with squash.'),
    Case("lead scope ends at heading", 0, '- Never\n- Rules\n\n## Merging\n\n- merge PRs with squash'),
    Case("lead scope fence without lead", 0, '- Notes\n- x\n  ```\n  git merge main\n  ```'),
    Case("lead scope label child wait for me", 2, '- **Merges:**\n  - wait for me'),
    Case("lead scope label child never", 2, '- **Merges:**\n  - never'),
    Case("lead scope merging child wait for me", 2, '- Merging\n  - wait for me'),
    Case("lead scope merging child wait for owner", 2, '- Merging\n  - wait for the owner'),
    Case("lead scope merging child approval", 2, '- Merging\n  - only after my approval'),
    Case("lead scope label never idiom", 2, '- **Merges:** Never under any circumstances'),
    Case("lead scope label never", 2, '- Merges: never'),
    Case("lead scope label child squash only", 0, '- **Merges:**\n  - squash only'),
    Case("lead scope merging child reviewer", 2, '- Merging\n  - wait for a reviewer'),
    Case("lead scope merge commits label", 0, '- Merge commits: never'),
    Case("lead scope label not applicable", 0, '- Merging: not applicable'),
    Case("lead scope label own object", 0, '- Merges: no direct pushes'),
    Case("lead scope label target", 2, '- Merges to main: not without my approval'),
    Case("lead scope label merge queue", 0, '- Merge queue: no'),
    Case("lead scope heading other label", 0, '## Merging\n\n- Force push: no'),
    Case("lead scope complete rule with modal", 0, '- Avoid long branches when you can\n- Merge PRs with squash'),
    Case("lead scope condition child", 2, '- Until I approve:\n  - merge PRs'),
    Case("lead scope condition negated child", 2, '- Until I approve:\n  - do not merge PRs'),
    Case("lead scope condition continuation", 2, '- Until I approve:\n\n  merge PRs'),
    Case("lead scope requirement child", 2, '- Require human approval for\n  - merging PRs'),
    Case("lead scope reviewer condition", 0, '- Once a reviewer approves:\n  - merge PRs'),
    Case("lead scope condition later sibling", 0, '- Until I approve:\n  - Notes\n- merge PRs'),
    Case("lead scope status not applicable", 0, '- Checklist\n- Not applicable\n- Merge PRs with squash after CI'),
    Case("lead scope status jitter", 0, '- Checklist\n - Not applicable\n- Merge PRs with squash after CI'),
    Case("lead scope status not needed", 0, '- Not needed\n- Merge PRs with squash'),
    Case("lead scope dont do layout", 0, "- Don't:\n  - commit secrets\n  - push to main\n- Do:\n  - open a PR\n  - merge with squash after CI"),
    Case("lead scope bold dont do layout", 0, "- **Don't**\n  - commit secrets\n- **Do**\n  - merge with squash after CI"),
    Case("lead scope never do this always", 0, '- Never do this:\n  - commit secrets\n- Always:\n  - merge with squash after CI'),
    Case("lead scope what not to do", 0, '- What not to do\n  - commit secrets\n- What to do\n  - merge with squash after CI'),
    Case("lead scope never commit then workflow", 0, '- Never commit:\n  - `.env`\n  - build output\n- Workflow\n  - Merge with squash after CI'),
    Case("lead scope do not touch then workflow", 0, '- Do not touch\n  - `vendor/`\n- Workflow\n  - merge with squash after CI'),
    Case("lead scope do not edit then workflow", 0, '- Do NOT edit\n  - generated files\n- Workflow\n  - merge PRs with squash'),
    Case("lead scope no secrets then process", 0, '- No secrets\n- Process\n  - Merge PRs with squash after CI'),
    Case("lead scope no exceptions then process", 0, '- No exceptions\n- Process\n  - Merge PRs with squash after CI'),
    Case("lead scope no todos then process", 0, '- No TODOs\n- Process\n  - Merge PRs with squash after CI'),
    Case("lead scope no print then process", 0, '- No `print`\n- Process\n  - Merge PRs with squash after CI'),
    Case("lead scope dont panic then process", 0, "- Don't panic\n- Process\n  - Merge PRs with squash after CI"),
    Case("lead scope no emojis then process", 0, '- No emojis\n- Process\n  - Merge PRs with squash after CI'),
    Case("lead scope nested no secrets release", 0, '- Security\n  - No secrets\n  - Release\n    - Merge with squash'),
    Case("lead scope no secrets example fence", 0, '- No secrets\n- Example:\n\n  ```bash\n  gh pr merge 12 --squash\n  ```\n'),
    Case("lead scope never with child release fence", 0, '- Never\n  - commit .env\n- Release steps:\n\n  ```bash\n  git merge --ff-only origin/main\n  ```\n'),
    Case("lead scope no exceptions sync paragraph", 0, '- No exceptions\n- Sync your branch\n\n  Merge `origin/main` into it; never rebase.'),
    Case("lead scope approval not required for child", 0, '- Approval is not required for\n  - merging PRs'),
    Case("lead scope my approval not required child", 0, '- My approval is not required:\n  - merge PRs once CI is green'),
    Case("lead scope review not required for child", 0, '- Review is not required for\n  - merging docs-only PRs'),
    Case("lead scope not yet notes child", 0, '- Not yet\n  - Notes\n    - merge PRs'),
    Case("lead scope not supported colon sibling", 2, '- Not supported:\n- merging PRs'),
    Case("lead scope bold not supported colon sibling", 2, '- **Not supported:**\n- merge PRs'),
    Case("lead scope not required colon sibling", 2, '- Not required:\n- merge PRs'),
    Case("lead scope not applicable colon sibling", 2, '- Not applicable:\n- merge PRs'),
    Case("lead scope bold not needed colon sibling", 2, '- **Not needed:**\n- merge PRs'),
    Case("lead scope capabilities not supported colon", 2, '## Agent capabilities\n\n- Supported:\n  - opening PRs\n- Not supported:\n- merging PRs'),
    Case("lead scope table not supported row", 2, '| Rule | |\n|---|---|\n| Not supported | |\n| merge PRs | |'),
    Case("lead scope labelled status then workflow child", 0, '- Tests: not applicable\n- Workflow\n  - Merge PRs after CI'),
    Case("lead scope checkbox status then workflow child", 0, '- [ ] Not applicable\n- [ ] Workflow\n  - [ ] Merge PRs after CI'),
    Case("lead scope labelled status sibling", 0, '- Tests: not applicable\n- Merge PRs after CI'),
    Case("lead scope checkbox status sibling", 0, '- [ ] Not applicable\n- [ ] Merge PRs after CI'),
    Case("lead scope rebase merging no", 0, '- Rebase merging: no'),
    Case("lead scope squash merge no", 0, '- Squash merge: no'),
    Case("lead scope fast-forward merges no", 0, '- Fast-forward merges: no'),
    Case("lead scope rebase merging never", 0, '- Rebase merging: never'),
    Case("lead scope repository settings list", 0, '## Repository settings\n\n- Allow squash merging: yes\n- Allow merge commits: no\n- Allow rebase merging: no\n- Automatically delete head branches: yes'),
    Case("lead scope auto-merge no", 0, '- Auto-merge: no'),
    Case("lead scope allow auto-merge no", 0, '- Allow auto-merge: no'),
    Case("lead scope automerge never", 0, '- Automerge: never'),
    Case("lead scope delete branch on merge no", 0, '- Delete branch on merge: no'),
    Case("lead scope pr merged no", 0, '- PR merged: no'),
    Case("lead scope merged no", 0, '- Merged: no'),
    Case("lead scope status merged no", 0, '- Status\n  - Merged: no'),
    Case("lead scope merge to main never", 2, '- Merge to main: never'),
    Case("lead scope self-merge never", 2, '- Self-merge: never'),
    Case("lead scope merges never by agents", 2, '- Merges: never by agents'),
    Case("lead scope merges not by agents", 2, '- Merges: not by agents'),
    Case("lead scope merges never ask me first", 2, '- Merges: never, ask me first'),
    Case("lead scope merges never ask me parens", 2, '- Merges: never (ask me)'),
    Case("lead scope bold merges not yet child", 2, '- **Merges:**\n  - not yet'),
    Case("lead scope merge policy never", 2, '- Merge policy: never'),
    Case("lead scope merges never paragraph", 2, 'Merges: never'),
    Case("lead scope merge autonomously dont wait", 0, "- Merge ready PRs autonomously; don't wait for me"),
    Case("lead scope merge autonomously do not wait owner", 0, 'Merge autonomously when gates pass; do not wait for the owner.'),
    Case("lead scope merge no need to wait owner", 0, '- Merge once CI is green - no need to wait for the owner'),
    Case("lead scope merge directly no need to wait", 0, '- Merge directly; no need to wait for me'),
    Case("lead scope merge when ready do not wait", 0, '- Merge when ready, do not wait for me'),
    Case("lead scope merge without waiting owner", 0, 'Merge PRs without waiting for the owner.'),
    Case("lead scope never wait for a maintainer", 0, 'Merge PRs when CI is green; never wait for a maintainer.'),
    Case("lead scope merging child do not wait", 0, '- Merging\n  - do not wait for me'),
    Case("lead scope after merging wait to publish", 0, 'After merging, wait for a maintainer to publish the release.'),
    Case("lead scope once merged wait to verify", 0, 'Once merged, wait for the human to verify the preview deploy.'),
    Case("lead scope merge then wait to publish", 0, '- Merge the release PR\n- Wait for the maintainers to publish the release'),
    Case("lead scope release steps wait to publish", 0, '- Release\n  1. Merge the release PR\n  2. Wait for the owner to publish the GitHub release'),
    Case("lead scope bold merges child must wait for me", 2, '- **Merges:**\n  - must wait for me'),
    Case("lead scope until i say otherwise child", 0, '- Until I say otherwise:\n  - merge ready PRs'),
    Case("lead scope requires one approval then merge", 0, '- Requires one approval\n  - then merge with squash'),
    Case("lead scope flat requires one approving review", 0, '- Merge with squash; requires 1 approving review'),
    Case("lead scope after approval child", 2, '- After approval:\n  - merge with squash'),
    Case("lead scope unclosed fence under dangling sibling", 2, '- Never\n- x\n  ```\n  merge PRs'),
    Case("lead scope shallowest lead-in governs", 2, '  * Never\n       1. Never\n  * merge PRs'),
    # Round-2 review of #78: dropped holds against main, waived approvals, ordering and punctuation.
    Case("lead scope status not supported", 2, '- Not supported\n- merging PRs'),
    Case("lead scope status not supported bold", 2, '- **Not supported**\n- merging PRs'),
    Case("lead scope status not supported checkbox", 2, '- [ ] Not supported\n- [ ] merging PRs'),
    Case("lead scope status not available", 2, '- Not available\n- merging PRs'),
    Case("lead scope capabilities not supported", 2,
         '## Agent capabilities\n\n- Supported\n  - opening PRs\n- Not supported\n- merging PRs'),
    Case("lead scope actor label not supported", 2, '- Agents: not supported\n- merging PRs'),
    Case("lead scope actor label for agents", 2, '- For agents: not supported\n- merging PRs\n- force pushes'),
    Case("lead scope checkbox status not needed", 0, '- [ ] Not needed\n- Merge PRs with squash after CI'),
    Case("lead scope pointer with object", 0, '- Do not edit these files\n- Workflow\n  - Merge PRs with squash after CI'),
    Case("lead scope this repo rule", 0, '- No secrets in this repo\n- Process\n  - Merge PRs with squash after CI'),
    Case("lead scope trailing pointer", 2, '- Never do these\n- Rules\n  - merge PRs'),
    Case("lead scope merging child wait to publish", 0, '- Merging\n  - wait for a maintainer to publish the release'),
    Case("lead scope merges child then wait to tag", 0, '- Merges\n  - then wait for the owner to tag a release'),
    Case("lead scope merging sibling wait to update", 0, '- Merging\n- wait for me to update the changelog'),
    Case("lead scope merging child wait to approve", 2, '- Merging\n  - wait for the owner to approve'),
    Case("lead scope merging child release manager", 2, '- Merging\n  - wait for the release manager'),
    Case("lead scope inline label wait for me", 2, '- Merging: wait for me'),
    Case("lead scope inline label paragraph wait", 2, 'Merging: wait for the owner.'),
    Case("lead scope inline bold label wait", 2, '- **Merging:** wait for the owner'),
    Case("lead scope nested labelling sibling", 2, '- Rules\n  - **Merges:**\n  - never'),
    Case("lead scope label never without green ci", 0, '- Merges: never without green CI'),
    Case("lead scope label not without passing checks", 0, '- Merges: not without passing checks'),
    Case("lead scope label never without approval", 2, '- Merges: never without my approval'),
    Case("lead scope without waiting parent", 0, '- Without waiting for approval:\n  - merge PRs once CI is green'),
    Case("lead scope waiting unnecessary parent", 0, '- Waiting for approval is unnecessary:\n  - merge PRs'),
    Case("lead scope approvals child waived", 0, '- Approvals\n  - merge PRs without waiting for them'),
    Case("lead scope approval child needs none", 0, '- Approval:\n  - merging PRs needs none'),
    Case("lead scope condition then child", 2, '- Until I approve:\n  - then merge PRs'),
    Case("lead scope my approval then merge", 2, '- Requires my approval\n  - then merge with squash'),
    Case("lead scope reviewer then child", 0, '- Once a reviewer approves:\n  - then merge PRs'),
    Case("lead scope until i confirm child", 2, '- Until I confirm:\n  - merge PRs'),
    Case("lead scope until we agree child", 2, '- Until we agree:\n  - merge PRs'),
    Case("lead scope until you hear from me child", 2, '- Until you hear from me:\n  - merge PRs'),
    Case("lead scope condition child period", 2, '- Until I approve:\n  - Merge PRs.'),
    Case("lead scope condition child semicolon", 2, '- Until I approve:\n  - merge PRs;'),
    Case("lead scope requirement child period", 2, '- Require human approval for\n  - merging PRs.'),
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
ALL_CASES = TEXT_CASES + LIST_COLUMN_CASES + LEAD_SCOPE_CASES + DASH_CASES + SOURCE_CASES + ROUTING_CASES
if len(ALL_CASES) != 1182 or len({case.name for case in ALL_CASES}) != 1182:
    raise RuntimeError("Merge fixture inventory must contain 1182 unique cases")


class MergePreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory(prefix="merge-preflight-fixture-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.environment, tools = fixture_environment(self.root)
        fake_gh = tools / "gh"
        write_fixture(fake_gh, FAKE_GH)
        fake_gh.chmod(0o700)
        self.log = self.root / "gh.log"
        self.environment["FIXTURE_LOG"] = str(self.log)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.instructions = self.repo / "AGENTS.md"
        write_fixture(self.instructions, "Project instructions.\n")
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
            write_fixture(self.instructions, (ROOT / case.source).read_bytes().decode("utf-8"))
        elif case.text is not None:
            write_fixture(self.instructions, case.text)
        if case.setup == "push_mismatch":
            self.git("remote", "set-url", "--push", "origin", "https://github.example/other/repo.git")
        elif case.setup == "published_wait":
            write_fixture(self.instructions, "Autonomous merge suspended — asked on 2026-09-27\n")
            self.git("add", "AGENTS.md")
            self.git("commit", "-qm", "published wait")
            self.git("update-ref", "refs/remotes/origin/main", "HEAD")
            write_fixture(self.instructions, "Local clear.\n")
        elif case.setup == "missing_published_ref":
            self.git("update-ref", "-d", "refs/remotes/origin/main")
        write_fixture(self.log, "")
        environment = self.environment | dict(case.environment)
        arguments = [str(self.instructions)] if case.mode == "suspension" else ["fixture", "repo"]
        if case.mode == "reviews":
            arguments.append("1")
        result = run(["bash", str(PREFLIGHT), case.mode, *arguments], self.repo, environment)
        calls = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
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
    def test_repeated_classification_work_is_bounded_per_scan(self) -> None:
        spec = importlib.util.spec_from_file_location("workflow_context", PREFLIGHT.with_name("workflow-context.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        searches = []
        free = module.FREE

        class Counted:
            def search(self, text):
                searches.append(text)
                return free.search(text)

        module.FREE = Counted()
        # Count real regex work, including False results, with a fresh cache per scan.
        for scan in range(2):
            with self.subTest(scan=scan):
                searches.clear()
                self.assertEqual(module.scan_text("- merge\n- go\n" * 40), 0)
                self.assertEqual(len(searches), 2)

    def test_repeated_run_items_add_no_per_item_work(self) -> None:
        # test_alternating_list_run_stays_bounded repeats these two items 10000 times;
        # per-item stripping and run matching made it the slowest gated scan (#13).
        item = "- merge\n- go\n"
        work = []
        for factor in (1, 2):
            spec = importlib.util.spec_from_file_location("workflow_context", PREFLIGHT.with_name("workflow-context.py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            # Every item adds at least one character, so both scans fill the run
            # window and the counts compare full windows.
            repeats = factor * (module.RUN_WINDOW + 1)
            calls = []
            strip_markers = module.strip_markers

            class Counted:
                def __init__(self, pattern):
                    self.pattern = pattern

                def search(self, text, *args):
                    calls.append(text)
                    return self.pattern.search(text, *args)

            module.strip_markers = lambda value: calls.append(value) or strip_markers(value)
            module.RUN_WORDS = {key: Counted(pattern) for key, pattern in module.RUN_WORDS.items()}
            module.RUN_BRANCHES = tuple((Counted(pattern), *rest) for pattern, *rest in module.RUN_BRANCHES)
            self.assertEqual(module.scan_text(item * repeats), 0)
            work.append(len(calls))
        self.assertEqual(work[0], work[1])

    def test_lead_in_blocks_and_context_match_a_walk_over_every_block(self) -> None:
        # Many short lead-in blocks made a walk over the kept blocks cost up to CONTEXT_LIMIT
        # steps per unit (#62). Lead keeps running sizes instead and must keep and cut exactly
        # what the walk did, including sizes that land on the limit.
        spec = importlib.util.spec_from_file_location("workflow_context", PREFLIGHT.with_name("workflow-context.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        limit = module.CONTEXT_LIMIT

        def trim(blocks):
            kept, size = [], 0
            for block in reversed(blocks):
                if kept and size + len(block) > limit:
                    break
                kept.append(block)
                size += len(block) + 1
            return kept[::-1]

        def tail(blocks, extra):
            parts, size = [], 0
            for part in reversed(blocks + extra):
                if size >= limit:
                    break
                parts.append(part)
                size += len(part) + 1
            return " ".join(reversed(parts))[-limit:]

        # Most sizes plus a separator divide the limit, so running sizes land on it exactly.
        sizes = (0, 1, 2, 5, 9, 19, 29, 59, 99, 199, 299, limit - 2, limit - 1, limit, limit + 1, 2 * limit)
        rng = random.Random(62)
        lead, kept = module.Lead(), []
        for step in range(3000):
            choice = rng.random()
            if choice < 0.05:
                lead.clear()
                kept = []
            elif choice < 0.55:
                blocks = [str(step % 10) * rng.choice(sizes) for _ in range(rng.choice((1, 1, 1, 2, 3)))]
                lead.add(blocks)
                kept = trim(kept + blocks)
            extra = [chr(97 + step % 26) * rng.choice(sizes) for _ in range(rng.choice((0, 0, 1, 2)))]
            with self.subTest(step=step):
                self.assertEqual(list(lead.blocks), kept)
                self.assertEqual(lead.tail(extra), tail(kept, extra))

    def test_pause_cue_keeps_every_cross_pause_match(self) -> None:
        # scan_normalized reads CROSS_PAUSE only when a STRONG_PAUSE word, which every match holds,
        # is present (#62). The guarded search must match exactly when CROSS_PAUSE does, so a cue
        # that misses one pause word fails here.
        spec = importlib.util.spec_from_file_location("workflow_context", PREFLIGHT.with_name("workflow-context.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        merges = ("merge", "merges", "merged", "merging", "fusionne", "automerge", "integrate", "")
        pauses = ("", "suspended", "suspendu", "paused", "pause", "on hold", "on\nhold", "en  attente", "attente", "hold")
        separators = (" ", "  ", "\n", ", ", ". ", "; ", "! ")
        rng = random.Random(62)
        found = collections.Counter()
        for step in range(4000):
            # The 140-character filler or a sentence end keeps the words apart; shorter fillers stay
            # within CROSS_PAUSE's 120-character span.
            words = [rng.choice(merges), "x " * rng.choice((0, 5, 40, 70)), rng.choice(pauses)]
            if rng.random() < 0.5:
                words.reverse()
            text = "".join(word + rng.choice(separators) for word in words)
            cross = bool(module.CROSS_PAUSE.search(text))
            found[cross] += 1
            with self.subTest(step=step, text=text):
                self.assertEqual(bool(module.STRONG_PAUSE_CUE.search(text) and module.CROSS_PAUSE.search(text)), cross)
        # The guarded search both matches and fails often enough for the comparison to mean something;
        # a missing outcome counts as zero.
        self.assertGreater(min(found[True], found[False]), 300, found)

    def test_repeated_classifications_keep_context_and_state(self) -> None:
        rule = "No rebase merges; use squash merges."
        cases = (
            ("different context", "- merge\n- go\n" * 8 + "\n## Never\n\n- merge\n"),
            ("same joined text", rule + "\n\nNo rebase\n\nmerges; use squash merges."),
            ("repeated section", "## Rules\n\n" + rule + "\n\n## Rules\n\n" + rule
             + "\n\nEach one is manual."),
            ("many distinct units", "\n\n".join("Entry %d." % n for n in range(160))
             + "\n\nDo not merge pull requests."),
        )
        for name, text in cases:
            with self.subTest(case=name):
                spec = importlib.util.spec_from_file_location("workflow_context", PREFLIGHT.with_name("workflow-context.py"))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.assertEqual(module.scan_text(text), 2)

    def test_each_distinct_normalized_reading_is_scanned_once(self) -> None:
        cases = (
            ("- merge\n- go\n", 0, 1),
            ("Autonomous merge sus\u200bpended — request dated 2026-09-27", 1, 2),
            ("Autonomous\u200bmerge suspended — request dated 2026-09-27", 1, 2),
            ("Autonomous\u200bmerge sus\u200bpended — request dated 2026-09-27", 2, 2),
            # Only an unspaced em dash adds the hyphen reading; the marker's spaced dash does not.
            # The hyphen reading is scanned first and a hold ends the scan.
            ("Laisse—moi fusionner.", 2, 1),
            # Otherwise the spaced reading is scanned too, wherever the dash is.
            ("- merge\n- go\n" * 40 + "a—b", 0, 2),
            ("PRs are blocked by the owner—CI is green.", 2, 2),
            # A spaced dash opening a line also adds it, since it may be a list marker; one inside a line does not.
            ("- merge\n- go\n" * 40 + "— go\n", 0, 2),
            ("> — Squash merges only.\n", 0, 2),
            # Line starts are found in the folded readings, so a lone CR, which git show output keeps, ends a line.
            ("x\r— go\r", 0, 2),
            ("- merge\n- go\n" * 40 + "a — b\n", 0, 1),
            # Two invisible-character readings times two dash readings.
            ("a\N{ZERO WIDTH SPACE}b—c", 0, 4),
        )
        for text, expected, reading_count in cases:
            with self.subTest(text=text):
                spec = importlib.util.spec_from_file_location("workflow_context", PREFLIGHT.with_name("workflow-context.py"))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                calls = []
                scan_normalized = module.scan_normalized

                def counted(reading, *caches):
                    calls.append(reading)
                    return scan_normalized(reading, *caches)

                module.scan_normalized = counted
                self.assertEqual(module.scan_text(text), expected)
                self.assertEqual(len(calls), reading_count)

    def test_each_scan_normalizes_the_text_once(self) -> None:
        # NFKC dominated the cost of a scan when every reading and the managed-block check each
        # normalized the whole text again; one pass serves every reading.
        for text in ("- merge" + "\n- go" * 200, "a\N{ZERO WIDTH SPACE}b", "Laisse—moi fusionner.",
                     "a\N{ZERO WIDTH SPACE}b—c\n"):
            with self.subTest(text=text[:20]):
                spec = importlib.util.spec_from_file_location("workflow_context", PREFLIGHT.with_name("workflow-context.py"))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                calls = []
                unicodedata = module.unicodedata

                class Counted:
                    def __getattr__(self, name):
                        return getattr(unicodedata, name)

                    def normalize(self, form, value):
                        calls.append(form)
                        return unicodedata.normalize(form, value)

                module.unicodedata = Counted()
                module.scan_text(text)
                self.assertEqual(calls, ["NFKC"])

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

    def test_list_without_connective_tries_no_run_branch(self) -> None:
        # Reading 'Nothing is merged without ...' on every '- first' item made this list 1.9x
        # slower than before. Without its connective no branch can match, so none is tried.
        spec = importlib.util.spec_from_file_location("workflow_context", PREFLIGHT.with_name("workflow-context.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        searches = []

        class Counted:
            def __init__(self, pattern):
                self.pattern = pattern

            def search(self, text):
                searches.append(self.pattern.pattern)
                return self.pattern.search(text)

        module.RUN_BRANCHES = tuple((Counted(pattern), *rest) for pattern, *rest in module.RUN_BRANCHES)
        self.assertEqual(module.scan_text("- merge no one\n- first\n- first\n" * 200), 0)
        self.assertEqual(len(searches), 0)
