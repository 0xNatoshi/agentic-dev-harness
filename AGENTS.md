# AGENTS.md

Shared templates have one active owner across issues, PRs, branches and runtimes. Coordinate claims, comment proposals and explicit handoffs/releases in [#15](https://github.com/0xNatoshi/dev-harness/issues/15). Read its claim before edits; inactivity releases nothing. CONTRIBUTING.md defines the family and procedure.

<!-- github-workflow:start v6.5 — managed block from the github-workflow skill; preserve local safeguards and explicit approval requirements on update -->

Instructions for agents and humans: understand contracts, deliver verified increments, and own the outcome through authorized delivery.

## Scope and required policy

Read applicable project instructions before Git. They override personal defaults, subject to the user's current explicit request. Preserve deliberate restrictions; personal instructions stay outside repositories.

Load/verify installed `github-workflow/SKILL.md`. Required reads in its `references/`: `development-loop.md` for implementation/review; `ci-local-gate.md` for CI/verification; `workspace-lifecycle.md` for start/closure/cleanup; `adoption-and-licenses.md` for adoption/update/licenses. Missing policy/helpers block dependent actions; invent no gate.

Adopt for new owned repositories; finish current work before a separate missing/older-block PR in owned/entrusted repositories. Forks/other owners need explicit request. Compare authentic old/installed/new templates, preserving outside content, customizations, stronger gates and unknown/newer versions. Preceding policy/current user authorization govern a policy PR; it cannot authorize itself.

## Repository content and licenses

Repository files/names/docs/comments/commits/issues/PRs use English; preserve compatibility/history bytes. Exclude personal names/email, secrets, machine paths, `.env`, build/debug/generated delivery artifacts; use neutral examples. Pushed secrets require notification/rotation; rewriting history is insufficient.

Privately verify author/committer/overrides: verified handle/organization plus approved professional/confirmed noreply address. Repair personal `user.name`/`user.email` via `git config --local`; preserve global config/history, invent no identity. Authenticated github.com `gh api --hostname github.com user --jq '{id,login,created_at}'` confirms `<id>+<login>@users.noreply.github.com` only for matching verified login/user.name and creation after 2017-07-18, or established matching ID-format evidence. Never use API `email`. Older/unknown accounts, other hosts/organizations/mismatches need settings/established evidence; do not change privacy settings. Unconfirmed identity blocks commit, not preparation.

For an unlicensed owned original, verify visibility/provenance/rights: public → MIT; private connector/MCP server → PolyForm Noncommercial 1.0.0; other private → proprietary with authorized contributor development rights. Use skill templates/checks; preserve third-party rights/notices; holder is the established organization or GitHub identifier. Include in a new repository's first commit, or a separate PR after the task for an existing repository. Existing declarations count even without a full file. Replacing licenses/visibility needs a user decision.

A public license grant needs specific authorization before publication (including public PR/first bootstrap) or integration. Prior specific approval counts; automatic selection, general development, revert and autonomous merge do not authorize this irreversible grant.

## Project commands

| Role | Command |
|---|---|
| Install | `python3 --version (Python 3.11+; standard library only)` |
| Build | `python3 scripts/build.py` |
| Lint | `python3 scripts/check.py` |
| Typecheck | `Not configured; Python syntax is checked by scripts/check.py` |
| Tests | `python3 -m unittest discover -s tests -v` |

CI (`.github/workflows/ci.yml`) is a separate environment. Match its relevant dependencies/configuration and define gates for excluded surfaces. Local success is not CI success. Ratchet thresholds: None configured yet. Never loosen a ratchet; tighten it to the new measured value in the improving PR.

## Work and coordination

- Define scope/acceptance in existing tracking; deduplicate/create issues for nontrivial entrusted work without another prompt, respecting restrictions/safe publication. Trace callers/state/contracts/tests, reproduce/baseline and map criteria/neighbors to checks.
- Own decisions through review/fixes/delivery; plans/complexity need no go. Be ambitious on reversible work; consider focused redesign of structural faults, explain tradeoffs and implement the simplest adequate in-scope improvement. After two uninformative attempts, change hypothesis/seek independent analysis; bypass no gate.
- Undelegated irreversible actions (shared/default writes, publication/deployment, migration, unrecoverable deletion, permissions/settings, external messages) need evidence/result/rollback then specific authorization; precise existing requests count. §4 is the ready-PR exception. Without rollback, obtain authorization for the cautious plan first. Ask only for unresolved decisions, indispensable access or missing authorization.
- One topic per PR; separate risky/mechanical changes. Follow conventions, fix the cause at its owner, reuse contracts/remove replaced paths. Marginal gains justify no new layer; debt needs removal condition/follow-up.
- Verify/deduplicate incidental findings against issues/PRs/decisions, then open/update an issue without another prompt: evidence, impact, uncertainty, next action, criteria. Preserve ownership; tracking does not authorize unrelated implementation. Sanitize content; if safe sharing/access is unavailable, keep a sanitized draft and name the missing action. No useful finding means no filler.
- Record discoveries/decisions/handoffs in issues, revision-specific fixes/dispositions in PR comments with actual agent/session/revision. Lasting design goes in existing docs, invariants beside code; status in issues/PRs. No unrelated outreach authorization.
- Delegate independent scopes only when gain exceeds cost; specify owner/contracts/proof, avoid dependent/shared writes, use available specialist roles. Parent resolves contradictions/validates combined acceptance. Review rules below apply.
- One owner per template family (renderings/versions/history included) across issues/branches/worktrees/computers/runtimes. Link one coordination issue outside this block/from related work; reread before edits/after claim. Others propose in comments; resolve conflicts before writes, claims are not locks. Record handoff/release revision/pending work/successor; successor confirms. Record user reassignment. Silence/age/issue closure releases nothing; preserve history.

Long work: progress/discoveries/resources in `TASKS.md`, excluded via `.git/info/exclude`, never `.gitignore`. Recovery evidence stays private/outside removable checkouts.

## Quality and evidence

- Run applicable lint/types/build/tests on consolidated state. Never disable tests, bypass hooks (`--no-verify`), ignore lint, fabricate CI or weaken thresholds/protections. New behavior/fixes need observable-contract/failure-path tests, not implementation/mock mirrors.
- Prove regressions with the same final test present in isolated unfixed/fixed states: fail without, pass with the fix after necessary rebuilds. Record revisions/commands/results; repeat if the test changes. Preserve uncommitted work.
- Exercise actual paths, exposed consumers, errors and shared state; cover relevant duplication/ordering/restart/cancellation/partial failure. Tests must be deterministic/independent, without network/order/uncontrolled clock dependencies. Fix flakes; never retry until green. Deletion/quarantine needs the user's decision and a linked issue.
- Measure before optimizing: reproducible before/after environment/data/trial counts for claimed gains; deterministic counters for CI. Inspect visible changes and attach before/after captures. Distinguish local/simulated/remote/live proof; counts/reviewer agreement do not prove untested paths. Record baseline failures/environment limits; missing required proof keeps draft.
- Enforce traps with tests/lint/CI, otherwise Known pitfalls. Update docs/contracts. No silent catch, dead/debug code or TODO without linked issue. Justify/pin dependencies. Existing flags need purpose/removal issue; temporary debt needs follow-up.

## Git / GitHub workflow

Default: `main`; ordinary changes use PRs, never direct merges/fixes. Sole exception: explicitly requested new repository without history, instructions/license in first commit, publication authorized. Use skill's verified host/name bootstrap before origin exists. Missing default on empty remote permits only authorized first push; full preflight before PR/merge/cleanup.

At start/merge/cleanup and after checkout/remote changes, verify `origin`: readable/matching fetch/push URLs and implicit/explicit gh identities. Use only the skill's `gh repo set-default "$workflow_host/$workflow_repo"`; never change remotes to pass.

```bash
workflow_origin=$(bash <skill-dir>/scripts/merge-preflight.sh origin) || exit 2
IFS=$'\t' read -r workflow_host workflow_repo workflow_default <<< "$workflow_origin"
export workflow_host workflow_repo workflow_default
```

Every `gh pr`/`gh run`/`gh issue` uses `--repo "$workflow_host/$workflow_repo"`; `gh repo view/edit` takes positional `"$workflow_host/$workflow_repo"`. Only the preflight's implicit comparison and explicit `set-default` repair are exceptions. `gh api`: `--hostname "$workflow_host"` plus explicit `repos/$workflow_repo/...` or origin-derived GraphQL owner/name. No implicit `{owner}/{repo}` or guessed hosts/SSH aliases. Replace `<...>` with verified values; `<skill-dir>` is the installed skill.

### 1. One branch per implementation / issue

Follow the loaded skill's `start` claim/PR/branch checks. Only use owned/entrusted branches; record agent/session/scope/checkpoint and resolve conflicting claims. Inventory ownership/activity/attachments, reuse free checkout before isolation, revisit retained resources. Never commit/stash/push another session's work to free it.

Start from current remote default:

```bash
gh issue develop <n> --repo "$workflow_host/$workflow_repo" --name <type>/<n>-<slug> --base main --checkout
# Without an issue:
git fetch origin && git switch --no-track -c <type>/<slug> origin/main
```

Types: `feat|fix|docs|refactor|test|chore|perf|ci`.

### 2. Commit coherent increments

Commit coherent verified steps with lint/relevant tests/build passing. Stage reviewed files, inspect staged diff/status/identity; no blind `git add -A`. Use Conventional Commits with the issue when present.

Interruption allows owned-branch `chore(wip): ...` with hooks green, pushed and work recorded in its draft; no stash backup/hook bypass. Resume: bind origin, resolve PR (§4), read `gh pr view --repo "$workflow_host/$workflow_repo" "$workflow_pr"` and `git log origin/main..HEAD`, then sync (§3).

### 3. Push and synchronize

Push each verified owned/entrusted commit: `git push -u origin HEAD`, then `git push`; draft at first push. Before PR/when default moves: `git fetch origin && git merge origin/main`. Never rebase pushed branches. Resolve conflicts manually, no `-X ours/theirs`/overwriting others; preserve both tracking entries. Take default's lockfile and regenerate with configured manager/version against merged manifests; verify frozen install/diff, never hand-edit. Rerun gates; if uncertain abort and resolve the concrete decision. Lease force-push only your working branch, never default.

### 4. Pull request, review and integration

Capture the draft URL, which is the PR selector:

```bash
workflow_pr=$(gh pr create --repo "$workflow_host/$workflow_repo" --draft --base "$workflow_default" --title "<type>(scope): summary" --body-file <file>) || exit 2
export workflow_pr
```

On resume, bind origin and execute the loaded skill's **Select one PR explicitly** procedure: require an attached branch; resolve exactly one open PR for its head, verified origin owner and default base, excluding cross-repository PRs. No/multiple matches or API errors block lookup; establish first-draft need or an explicit entrusted PR without choosing the first result/duplicating. Validate supplied URLs against host/repository and verify session ownership separately. Every PR-specific command receives `"$workflow_pr"` or `<pr>`. Retain its URL after merge; open-PR lookup no longer applies.

Use a Conventional Commit title; body: Why / What / How to test / Evidence / Rollback / gate and applicable `Closes #<n>`/`Refs #<n>`. Include revisions/checks/results/limits, required visuals/measurements and revert/flag/manual rollback (prior authorization if irreversible). Populate visible `- Independent review: not required (<reason>)` or `- Independent review: done on <sha>; findings: <none/fixed/justified>`.

Self-review full `git diff origin/main...HEAD`: scope/contracts/secrets/obsolete content/proof/maintenance cost. At every effort level:

- Documentation only at any size/low-risk mechanical work: self-review/checks, no subagents/orchestration. Group related documentation; accompanying docs stay in their behavior PR. Documentation alone triggers no adoption/update/new CI.
- Ordinary behavior: one fresh-context reviewer.
- High stakes/large/difficult behavior (security/data/autonomy/CI/hooks/gates): multiple passes plus fresh skeptic, no quota. Executable Markdown rules are behavior. Initial reviewers get request/rules/diff without author rationale/other conclusions; skeptic gets final diff/findings/fixes/validation. Recheck significant deltas. Research follows this scale; explain multiple-agent review. Parent validates final combined state.

After pushes/before merge, read human/agent/bot feedback (including `chatgpt-codex-connector[bot]`), inline threads, PR comments and relevant issues. Wait for requested/known running reviews; silence is not completion. Verify findings and leave fixes/evidence-backed dispositions visible before resolving; recheck significant changes. Only nonblocking unrelated findings move to issues. Status notices need no ritual reply; bots do not replace independent review/tests or widen authorization/ownership.

#### CI and readiness

Follow required CI procedure on final head: wait for started checks; red blocks. About 120 seconds after its trigger, complete successful reads showing no started check/job/assigned runner/other wait allow configured local gate. Approval/concurrency/dependency waits, cancelled/stale/action-required states/API errors are not outages. Filtered/no-CI surfaces use intended gate. Attach reason/SHA/observations/commands/results/coverage; preserve protections, fabricate no success.

Select actual run IDs from checks or `gh run list --repo "$workflow_host/$workflow_repo" --commit <sha>`; verify workflow/head, never assume the first. All run diagnostics/watch commands need that ID. After review/available gates pass: `gh pr ready --repo "$workflow_host/$workflow_repo" "$workflow_pr"`; inspect new checks. Required missing evidence keeps/returns draft; a documented limit is no substitute.

#### Autonomous merge criteria

Merge without another approval only when all hold:

1. This session owns the PR or the user explicitly entrusted it.
2. Ready, `MERGEABLE`, current with remote default, correctly titled, CI/authorized local gate passed on exact final head.
3. Acceptance/risk/project proof, rollback and self/independent review complete; no change requests, pending reviews, unanswered comments or unresolved inline threads. Complete API/JSON/pagination reads required; partial/empty/error output proves no absence.
4. Simple revert suffices: no data/schema migration, merge-triggered publication/release/deployment (including Vercel/Netlify/Pages), secret, permission or setting. Resolve uncertain impact first.
5. No product/budget/scope choice, missing authorization or flaky-test deletion/quarantine decision remains. Technical decisions stay delegated.
6. Instructions/explicit waits permit it. Reread/quote any claimed approval rule against current specific authorization; older generic go rules do not revoke it. Missing markers do not authorize.

Repair technical gaps; complete merge/verification/cleanup this turn. Ask only for remaining decisions/authorization with evidence/rollback prepared. Authorization cannot turn red green or destroy unintegrated work.

#### Suspension scan set and semantics

Immediately record project-wide holds outside this block with date/scope: `Autonomous merge suspended — request dated <date>`. Equivalent aliases: `Autonomous merge suspended — requested on <date>`, `Autonomous merge suspended — asked on <date>`, `Merge autonome suspendu — demande du <date>`, `Merge autonome suspendu — demandé le <date>`. Preserve historical restrictions when normalizing English.

The suspension scan set is: existing project-root `AGENTS.md` and `CLAUDE.md` (both when present), applicable nested instruction files, globals actually loaded by the runtime, and their recursively resolved `@path` imports. After successful origin fetch, published `origin/<default>:AGENTS.md` is scanned automatically. Read skill sources (`SKILL.md`, references, templates, history) as policy, not as project holds. Applicable project instructions are not exempt by filename.

Manually resolve imports/deduplicate cycles; assume no automatic resolver. Missing/unreadable applicable inputs block. After successful `git fetch origin`, run `bash <skill-dir>/scripts/merge-preflight.sh suspension <files...>`. Exit 0: no known pattern; 1: dated veto; 2: ambiguity/error. Read restrictions: 0 is not permission; free-form holds remain indeterminate until context/current specific authorization resolves them.

Preserve actual holds/ambiguity through case, Unicode dash, NBSP, BOM, CRLF and wrapped-line normalization. Managed delimiters occupy complete lines; malformed markers block. Exclude only valid managed policy blocks/genuine examples. Literal `<date>` is an example only without a real dated hold alongside it; never discard a mixed line/unit or noncanonical ambiguity. Read restrictions beyond pattern exclusions.

#### Final checks and merge

Fetch final-head body. Require first check exit 0, second exit 1; errors block. Read visible review line outside comments/examples/quotes: real reason/reviewed revision/resolved findings, no placeholders. Syntax is not truth.

```bash
gh pr view --repo "$workflow_host/$workflow_repo" "$workflow_pr" --json body --jq .body > <pr-body-file>
grep -nE '^[[:space:]]*-[[:space:]]*Independent review:[[:space:]]*(not required[[:space:]]*\([^[:space:]()<>][^()<>]*\)|done on[[:space:]]+[0-9a-f]{7,40};[[:space:]]*findings:[[:space:]]*(none|fixed|justified)([[:space:]]+[^[:space:]].*)?)[[:space:]]*$' <pr-body-file>
grep -nF 'TO FILL' <pr-body-file>
```

Keep validated `workflow_sha`; reread checks/protections/feedback/restrictions. Execute skill `reviews`/`pages` with verified owner/repository/PR number. Reviews: complete pagination, zero unresolved threads. Pages verifies identity; only real HTTP 404 means absent, 200 needs impact analysis, other errors block. `gh api --hostname "$workflow_host" repos/$workflow_repo/deployments --jq length`: nonzero needs analysis. Inspect other integrations too.

```bash
[[ ${workflow_sha:-} =~ ^[0-9a-f]{40}$ ]] || exit 2
workflow_observed_sha=$(gh pr view --repo "$workflow_host/$workflow_repo" "$workflow_pr" --json headRefOid --jq .headRefOid) || exit 2
[ "$workflow_observed_sha" = "$workflow_sha" ] || exit 2
git fetch origin && git merge-base --is-ancestor origin/main "$workflow_sha" || exit 2
gh pr merge --repo "$workflow_host/$workflow_repo" "$workflow_pr" --squash --match-head-commit "$workflow_sha"
```

Require every prerequisite before proceeding. Changed head needs revalidation; no `--auto`/`--admin`/weakened protection. Delete branches separately after verification.

### 5. Verify publication and close resources

Retain `workflow_pr`; confirm `MERGED`, exact `headRefOid`/`mergeCommit`, fetch and prove merge inclusion in `origin/main`. Execute skill **Verify after merge** on that commit, never an earlier run. Wait for started checks; same eligible 120-second fallback, or intended local gate for absent trigger with limit stated. Red blocks closure: report/deliver fix or revert PR under the same gates, retain diagnostic resources; no direct-default repair.

For local validation, `git diff --quiet <headRefOid> <mergeCommit>`: 0 reuses identical-tree proof; 1 requires the gate on merged result in a free isolated checkout; errors need diagnosis. Preserve dirty/divergent default; never force-match origin.

At verified delivery, abandonment or next task, cleanup covers only owned/entrusted resources, not global cleanup/unintegrated deletion. Merge does not remove worktrees. Apply the required lifecycle procedure:

- **Ownership/data:** verify app owner, exclusive control, no dependent sessions/processes. Protect primary/pinned/shared/locked/active checkouts, default/protected branches and other sessions. Inspect tracked/untracked/useful ignored data; clean status is insufficient. Privately preserve/verify recovery outside the removed checkout.
- **Mechanism:** reuse free checkouts/prefer native creation; manual unmanaged creation needs unavailable native tool/explicit request, outside app roots. App checkouts require native archive/app restrictions/ignored-data preservation. Claude roots (`.claude/worktrees/` or configured) forbid Git removal/unlock/shell deletion; `ExitWorktree` needs same-session `EnterWorktree`/recovery. App-created worktrees need owning-session archive. Unknown/active owner or missing tool: retain/name owner/action. Only ordinary unmanaged worktrees allow `git worktree remove <path>` without force after checks; diagnose refusals, never delete files to bypass. Foreign `.git` needs entrusted recovery.
- **Recovery:** read both tips/PR head/merge; each existing tip must equal PR head. Prove inclusion (equal trees suffice for simple squash; differences need analysis), no uncommitted dependent work, durable recovery. Record SHAs/surviving reachable ref; otherwise unreachable squash/rebase history needs verified native archive/bundle of exact head before last-ref deletion. Execute lifecycle bundle/prerequisite checks; SHA notes/reflog hope are insufficient.
- **Final guards:** release dependent worktrees safely; detach only your free preserved checkout. Recheck exclusive ownership/activity/protection. Immediately before local deletion require exit 0 from `bash <skill-dir>/scripts/check-branch-unused.sh <branch>`: every registered worktree, including detached `rebase-merge/head-name`, `rebase-apply/head-name`, `BISECT_START`; use/unreadable state blocks. Observation is not atomic, so exclusive ownership remains required. Expected remote deletion: `git push --force-with-lease=refs/heads/<branch>:<headRefOid> origin :refs/heads/<branch>`; local: `git update-ref -d refs/heads/<branch> <headRefOid>`. Compare-and-delete replaces no other guard. Refusal needs fresh reads/diagnosis; absence needs no deletion, read errors are not absence. Never `git branch -D`, forced removal or default/protected deletion.

Re-inventory/report removed / reused / retained. Record each remainder's owner/reason/action/recheck trigger; resuming/closing agent owns follow-up. Assured recovery leaves no just-in-case retention reason. Claim automatic disappearance/watchers only with verified mechanisms.

## Reporting

Finish necessary authorized work before reporting. Put progress/next action together, then act.

Conversation follows personal reporting preferences. Repository/PR reports use **Done / Next step / Required input** in English: outcomes/actual verification, detailed sanitized commands in PR, useful issue links, abandoned approaches/reasons, gaps as owned actions/reasons for waiting. Indispensable input needs recommendation/consequence. Small tasks: one or two sentences; simple answers: no report.

<!-- github-workflow:end -->

## Known pitfalls

Lock new pitfalls in automatic checks when feasible; otherwise record them here.

- Profile files and skill templates are distribution source, not replacements for these repository instructions.
- Keep generated archives and private installation receipts out of Git.
- Runtime guards require Python 3.8+; contributor/build checks require Python 3.11+.
- Legacy non-English suspension literals are compatibility data.
- Never alter a published version tag or replace its release files; publish a new version.

## Source and delivery contracts

- Edit canonical policy in profiles/ and skills/; build generates the runtime copies.
- Root AGENTS.md/CLAUDE.md govern this repository; exported templates are payload data.
- Local gate: python3 scripts/check.py, python3 -m unittest discover -s tests -v, python3 scripts/build.py. CI runs the same gate on Linux; it makes no Windows/interactive-app qualification claim.
- Issues define acceptance criteria and agent/session ownership. Read latest comments and the linked PR before claiming; handoffs are explicit, never based on age.
- One implementation owner and one branch/PR per issue. Reviewers comment on the PR; parallel agents own independent files. Never commit or clean another session's resources.
- Follow CONTRIBUTING.md for claims, evidence, handoff and releases. Release publication needs specific authorization.
- First bootstrap of this explicitly requested new repository is authorized. All later default-branch changes use reviewed PRs.
