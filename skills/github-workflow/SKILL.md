---
name: github-workflow
description: "Apply the development workflow and adopt it by default in owned repositories: init, init all, update, license [all], readme, start, finish, merge and cleanup. Forks and other owners require an explicit request. Preserve local rules, evidence and authorization boundaries."
argument-hint: "[init [all] | update | license [all] | readme | start <issue> | finish | merge <pr> | cleanup]"
---

# GitHub development workflow

The Git lifecycle serves an observable outcome. For implementation/fixes (`start`/`finish`), read the [development loop](references/development-loop.md): framing, cause, architecture, neighboring behavior, delegation and delivery. The repository template carries its essential invariants; actual commands/contracts belong in the project.

**Initiative and written collaboration**: improve justified technical choices within the entrusted outcome. Verify and deduplicate useful incidental findings, open/update issues without another prompt in owned or explicitly entrusted repositories, and report their links and next actions. Leave decisions and review dispositions in issue/PR comments and explain non-obvious code constraints. Follow the [initiative procedure](references/development-loop.md#initiative-and-incidental-findings); preserve scope, sensitive-data and authorization boundaries, without inventing extra work.

**Documentation-only path**: at any diff size, use self-review and automatic checks without subagents or workflow orchestration, including `ultracode`. Group a task's documentation fixes in one PR. This alone does not trigger `init`/`update`, orchestration or new CI; existing publication gates apply. Changes to executable rules, authorization, CI, hooks or gate configuration remain behavior changes even in Markdown, reviewed by risk.

**Tools**: Bash/Git Bash, Git, gh and Python 3.8+ (standard library for Unicode/origin checks); Git Bash supplies awk/grep/mktemp. No external jq: gh has built-in `--jq`. Avoid the unsupported gh 2.93 combination `--paginate --slurp --jq`; supplied checks process `--paginate --jq` page by page. Resolve missing tools before the gate. On Windows, install Python 3 from python.org and reopen Git Bash; python3, py -3 or python is detected. An error is not green evidence.

The source command checker verifies the documented command forms, not arbitrary Bash semantics. Coprocesses and ambiguous here-document headers fail explicitly. In shell fences, recognized here-documents use a standalone `cat` or the selected Python array with stdin, one literal quoted delimiter and no pipeline or compound chain. Review their bodies in the recipient's language; they are not shell commands. Dynamic evaluation, aliases/functions, indirect executables and substitutions inside quoted tokens still require manual review. Keep examples within this boundary instead of extending the checker for speculative shell syntax.

In each Bash/Git Bash shell, select a working interpreter before Python examples. These probes match `merge-preflight.sh`; an executable name alone does not establish that a Windows Store alias works. Keep the array in the same shell as its uses.

```bash
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; assert sys.version_info >= (3,8)' 2>/dev/null; then
  python_cmd=(python3)
elif command -v py >/dev/null 2>&1 && py -3 -c 'import sys; assert sys.version_info >= (3,8)' 2>/dev/null; then
  python_cmd=(py -3)
elif command -v python >/dev/null 2>&1 && python -c 'import sys; assert sys.version_info >= (3,8)' 2>/dev/null; then
  python_cmd=(python)
else
  printf '%s\n' 'Python 3.8+ unavailable; check not run. Install Python 3 and reopen Git Bash.' >&2
  exit 2
fi
```

## Modes and scope

- **`init`** (default): install project AGENTS.md, CLAUDE.md, README and applicable CI.
- **`init all`**: inventory/adopt/update owned repositories in the verified scope, one delivery at a time; see [adoption and licenses](references/adoption-and-licenses.md).
- **`license` / `license all`**: apply policy to owned unlicensed repositories, one dedicated PR at a time. Preserve existing licenses; see the same reference.
- **`readme`**: maintain README/About using the [guide](references/readme-guide.md).
- **`update`**: migrate an installed AGENTS.md block and refresh relevant README content.
- **`start <n>` / `finish` / `merge <pr>` / `cleanup`**: use the task lifecycle below.

Invoke `$github-workflow <mode>` in Codex, `/github-workflow <mode>` in Claude Code, or load through `skill_view` in Hermes. Verify the loaded path. Prefer one canonical shared source through native mechanisms on the same machine. For start/closure/cleanup also read [resource lifecycle](references/workspace-lifecycle.md).

Project instructions remain applicable. Personal policy delegates adoption/update in owned repositories without another technical go, under the reference's sequence. Preserve deliberate restrictions, including applicable holds. Forks/other owners require an explicit request.

License selection is automatic. A public grant is irreversible: prepare its text and obtain specific authorization before first publication/integration, including the first public bootstrap commit. Existing specific authorization counts; general development/autonomous delivery does not. Publishing the license in a public PR also requires that authorization. Other reversible work keeps its normal gates.

Before any Git/GitHub operation in any mode, read all applicable instructions, project AGENTS.md and CLAUDE.md imports. Discovery comes afterward.

## Bind GitHub operations to origin

**Pre-origin bootstrap** applies only to an explicitly requested new repository without history. Prepare content locally, verify account/host/exact target name and create that explicitly named repository within the authorized scope. Before origin exists, do not run an existing-repository preflight or use an implicit target. Verify remote/origin identity as soon as they exist. If the remote is empty, compare fetch/push, implicit gh selection and explicit `nameWithOwner,url`; a missing default branch permits only the authorized first bootstrap push. After publication/default-branch creation, run the complete preflight before any PR/merge/cleanup. Public-license authorization remains separate.

With multiple remotes gh may default to upstream. In the relevant checkout, run the following check. It compares origin fetch/push URLs, implicit `gh repo view --json nameWithOwner,url,defaultBranchRef` and an explicit repository read. A mismatch/unreadable identity exits 2. After independently verifying and assigning the origin host/repository variables, repair local selection with `gh repo set-default "$workflow_host/$workflow_repo"` and retry; changing remotes or unsetting the selection is not a way to pass the guard.

```bash
workflow_origin=$(bash <skill-dir>/scripts/merge-preflight.sh origin) || exit 2
IFS="$(printf '\t')" read -r workflow_host workflow_repo workflow_default <<< "$workflow_origin"
export workflow_host workflow_repo workflow_default
```

Every `gh pr`/`gh run`/`gh issue` uses `--repo "$workflow_host/$workflow_repo"`; `gh repo view/edit` takes the positional `"$workflow_host/$workflow_repo"`. Only the implicit comparison described above and the explicit `set-default` repair are exceptions. `gh api` has no `--repo`: use `--hostname "$workflow_host"` plus an explicit `repos/$workflow_repo/...` path, or GraphQL owner/name from that verified identity. Avoid implicit `{owner}/{repo}` resolution. Pass the verified owner/repository components to `reviews`/`pages`. Ambiguous URLs/unresolved SSH aliases block; do not guess the host. Repeat before merge/cleanup and after checkout/remote changes.

### Select one PR explicitly

`--repo` does not permit relying on the current-branch PR fallback. Capture the URL when creating the first draft after its branch push:

```bash
workflow_pr=$(gh pr create --repo "$workflow_host/$workflow_repo" --draft --base "$workflow_default" --title "<type>(scope): summary" --body-file <file>) || exit 2
export workflow_pr
```

On resume, rerun origin binding and resolve the existing PR before any PR-specific command. Require an attached branch and exactly one open PR from the verified origin owner, against the verified default branch:

```bash
workflow_branch=$(git branch --show-current) || exit 2
[ -n "$workflow_branch" ] || { printf '%s\n' 'Detached checkout: establish the entrusted branch/PR first.' >&2; exit 2; }
workflow_pr=$(gh pr list --repo "$workflow_host/$workflow_repo" --base "$workflow_default" --head "$workflow_branch" --state open --limit 1000 --json url,headRepositoryOwner,isCrossRepository --jq "[.[] | select(.isCrossRepository == false and .headRepositoryOwner.login == \"${workflow_repo%%/*}\")] | if length == 1 then .[0].url else error(\"Expected exactly one origin-owned open PR\") end") || exit 2
export workflow_pr
```

No match, multiple matches or an API error blocks this lookup; do not pick the first result or create a duplicate. Establish whether a first draft is needed or an explicit entrusted PR was supplied. Validate any supplied URL against the verified host/repository before using it. A matching head/owner does not establish session ownership: read the claim and preserve other sessions' PRs. Use `"$workflow_pr"` or an explicit `<pr>` selector for every subsequent PR-specific command. After merge, retain its URL for publication/cleanup checks; an open-PR lookup no longer applies.

**English files and repository privacy**: write skills, instructions, package documentation, reports, filenames, repository docs, comments, commits and PRs in English. French is reserved for conversation. Exact historical identifiers/test inputs remain data. Personal global instructions stay outside repositories. Repository content/evidence excludes personal names/email, secrets and machine paths. Before each commit verify author/committer: actual organization or verified GitHub handle, with approved professional or confirmed noreply address. Repair personal identity only in this repository: obtain the handle with `gh api --hostname "$workflow_host" user --jq .login`, confirm noreply in account settings/verified evidence, then `git config --local user.name "<verified-handle>"` and `git config --local user.email "<confirmed-noreply>"`. Check `git var GIT_AUTHOR_IDENT`, `git var GIT_COMMITTER_IDENT` and task-scoped overrides without publishing personal data. This local evidence never covers the server-generated squash/merge commit: GitHub takes its author name from the merging account's profile display name (no supported option sets it), its author email from `--author-email` or the account default, and records GitHub as committer; `merge <pr>` checks it with the `identity` and `published` modes for the verified handle. If noreply cannot be confirmed, prepare the diff and request that indispensable information before commit. Preserve global configuration/history and invent no identity. License holders follow the same organization/handle rule.

## `init` — adopt the workflow

### 1. Read-only discovery

For an existing repository, run the origin preflight first. For a new bootstrap, follow its narrow exception and omit nonexistent remote reads while continuing local inspection.

```bash
git rev-parse --show-toplevel
git remote -v
gh repo view "$workflow_host/$workflow_repo" --json nameWithOwner,defaultBranchRef,squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed,deleteBranchOnMerge
git status --short
ls AGENTS.md CLAUDE.md README.md .github/workflows/ 2>/dev/null
```

- Include methodology/license in the new repository's first commit before authorized publication. The known visibility/type determines the template without a second choice. Existing repositories use the adoption PR policy.
- Without authenticated gh, identify the missing `gh auth login` user action and continue useful Git-only work.
- Preserve dirty/occupied checkouts and use a suitable free checkout or real isolation. Do not commit/stash/push another session's work to free it; ask only when no safe isolation is available.

Detect real stack commands:

| Evidence | Where to read commands |
|---|---|
| package.json | scripts for lint/test/build, package manager from lockfile |
| pyproject.toml / requirements | configured ruff, flake8, pytest, tox, uv, poetry |
| Cargo.toml | cargo clippy/test/build |
| go.mod | go vet/test/build |
| .csproj / .sln | dotnet format --verify-no-changes, test, build |
| Makefile / justfile / CI | reuse existing targets |

If a command is absent, record `—`, remove its corresponding CI step and report the gap; `run: —` is invalid. Detect typecheck/type-check scripts, configured TypeScript/mypy/pyright, and avoid duplicate checks already included in lint/build. Use the package manager for nonscripted commands (`npx`, pnpm/yarn exec, uv run, poetry run); CI cannot assume node_modules/.bin or a venv is on PATH.

Check these pitfalls:

- Multiple remotes: verified explicit targets, pushes only to origin; implicit mismatch blocks even with explicit later endpoints.
- Conflicting existing rules (never push, no-verify, direct-main commits): identify every copy, show conflicts and obtain an unresolved policy decision before replacement. Search AGENTS.md, CLAUDE.md, CODEX.md, Copilot instructions and playbooks.
- Broken hooks: execute their commands and fix the cause (including linting managed worktrees), without bypassing hooks.
- Unavailable runners: use [CI/local gate](references/ci-local-gate.md) after about 120 seconds with no check/job started or runner assigned. Running/red checks and explicit approval/concurrency waits are distinct; preserve server protections.
- Missing token workflow scope: pushes touching .github/workflows may fail. The user can `gh auth refresh -s workflow`; otherwise preserve workflows and use a local gate when no CI exists.
- Git Bash path conversion: write `gh api --hostname "$workflow_host" repos/...` without an initial slash, or use `MSYS_NO_PATHCONV=1`. Multiline PR bodies use `--body-file`, not interpolated shell strings.
- Worktrees: a default branch checked out in the primary checkout cannot be switched into elsewhere. Start from origin/default and preserve other sessions.
- Long hooks: measure duration. Above two minutes, a tool timeout can masquerade as hook failure; document measured timing in CLAUDE.md and Codex Known pitfalls, then wait for the result.
- Secrets: check ignored .env, keys and credentials; propose missing exclusions.
- Existing claims/TODO/playbooks: follow their protocol during adoption itself.
- Thresholds/baselines: inventory coverage, warnings, size and deterministic benchmark counters for RATCHETS.
- Existing feature flags: record their mechanism; do not add a flag framework when none exists.

Create the adoption branch in an available checkout before generating files. For managed worktrees use the native tool and returned paths. New-repository bootstrap remains the sole initial direct-commit exception.

```bash
git fetch origin && git switch --no-track -c chore/agent-workflow origin/<default>
```

### 2. Generate AGENTS.md

Use [templates/AGENTS.md](templates/AGENTS.md) as the canonical project instructions and fill:

- DEFAULT_BRANCH from defaultBranchRef.name, otherwise main.
- MERGE_METHOD: squash when allowed, otherwise merge, otherwise rebase.
- INSTALL_CMD / BUILD_CMD / LINT_CMD / TYPECHECK_CMD / TEST_CMD from actual commands.
- CI_FILE from existing/applicable CI.
- RATCHETS from existing/measured/authorized thresholds, otherwise `None configured yet`.
- KNOWN_PITFALLS: English bullets for confirmed pitfalls, without personal data, otherwise `- None identified yet`.

Preserve start/end managed markers. Project-specific content belongs after end, outside the block. Existing AGENTS.md is merged carefully, never blindly overwritten; show the diff.

**Local gate mode**: use the shared [procedure](references/ci-local-gate.md). Preserve existing workflows; create no fake workflow to simulate CI. State actual commands, coverage and proof in the template/PR, consistently updating CI-following, merge, DoD and post-merge checks. Required remote checks stay binding until an authorized configuration decision.

An optional PR template uses Why / What / How to test / Evidence / Rollback / Local gate, plus Closes/Refs only for an existing issue. Evidence includes a visible `- Independent review: TO FILL` line; examples can be in comments, but a ready PR must replace that placeholder.

### 3. Generate CLAUDE.md

Claude Code loads CLAUDE.md/imports; native AGENTS.md support depends on version/configuration. Use one canonical source and verify actual loading:

```markdown
@AGENTS.md

<!-- Optional Claude Code-specific instructions below -->
```

For hooks measured above two minutes add:

```markdown
- Git hooks take ~<duration>: run `git commit` / `git push` with a Bash timeout of 600000 ms (or in the background). A timeout is not a hook failure: wait for the result, never re-run with `--no-verify`.
```

This timeout advice is Claude-specific. Codex uses its own tool limits and a Known pitfalls entry. For existing CLAUDE.md add the import without removing content; removing duplicated authored rules requires applicable user authorization.

### 4. README, About and license

Apply the [README/About guide](references/readme-guide.md) using real project commands/examples, without global personal instructions or machine paths. Apply [license policy](references/adoption-and-licenses.md#licenses) with the MIT, proprietary or PolyForm templates after visibility/type/provenance checks. New repositories include the license in their first commit; existing unlicensed repositories use a separate PR after the current task. Existing license/visibility changes remain user decisions and public grants retain their specific authorization.

### 5. GitHub Actions CI

If no workflow runs lint/tests on pull_request and local-gate mode does not apply, adapt [ci.yml](templates/ci.yml) to the real stack: replace SETUP_STEP with the appropriate setup action, use the project's runtime version/cache, and install from the lockfile (`npm ci`, pnpm frozen-lockfile, yarn immutable, uv locked). Preserve existing CI and report missing lint/PR/default-branch push triggers; the latter matters for post-merge verification.

GitHub `${{ ... }}` expressions are syntax, not placeholders. Replace only uppercase `{{...}}` fields, then run this Bash check even if no CI directory exists:

```bash
(
  shopt -s nullglob
  workflow_files=(AGENTS.md CLAUDE.md README.md LICENSE* .github/workflows/*.yml .github/workflows/*.yaml)
  grep -nE '\{\{[A-Z_]+\}\}' "${workflow_files[@]}"
)
```

Exit 1 means no unresolved placeholder; 0 means fill it; any other code means resolve an error. Empty output alone proves nothing. Execute CI commands locally before committing their configuration.

**Optional ratchets**: propose, do not impose, deterministic thresholds that can tighten over time. Wall-clock timing is too noisy for a blocking ratchet. Examples: coverageThreshold/cov-fail-under/fail_under, eslint max-warnings or committed ruff/clippy counters, size-limit/bundle budgets, benchmark operation/render/request counts. Set the current measured value, prove failure under deliberate degradation and connect it to CI/local gate if authorized. Record it in RATCHETS. Tighten manually in the improving PR; scheduled automatic tightening requires a request.

### 6. Repository settings

Propose settings and obtain missing specific authorization. A README/About request only authorizes its description change, as the guide explains.

```bash
gh api --hostname "$workflow_host" --method PATCH "repos/$workflow_repo" -F allow_squash_merge=true -f squash_merge_commit_title=PR_TITLE
gh repo edit "$workflow_host/$workflow_repo" --description "…"
```

The first command uses the documented [repository update API](https://docs.github.com/en/rest/repos/repos#update-a-repository) to allow squash merges and default their titles to the PR title. Inspect the prior values for rollback and reread the authorized settings after success.

About uses the guide's sentence, length and rollback checks. Archived repositories stay read-only. Suggest Secret scanning, Push protection and Dependabot alerts when supported; this is not permission to enable them.

At init/update inspect effective default-branch protection and availability for the account plan/visibility, then propose missing protections. The user configures Settings → Rules or authorizes the precise API change. For a solo developer: PR required with zero required approvals (self-approval is unavailable), protected from force-push/deletion, applying to administrators if intended. Require only a named check that actually runs on PRs and has succeeded; without working CI retain a local gate with no invented required check. Reread authorized settings and document rollback. A feature unavailable on the current plan does not authorize visibility changes.

### 7. Deliver adoption through its own workflow

For an explicitly requested new repository without history, validate project files/license and proportionate review/gate before the bootstrap commit. Publish only within existing authorization, then verify remote SHA and initial validation. Do not create a PR comparing the default branch to itself or pretend a distinct base exists. Preserve evidence and close temporary resources; later changes use PRs.

For existing repositories, deliver the adoption branch below; license work has its own PR:

```bash
git add <reviewed-files>
git commit -m "chore: formalise the git/github workflow and CI (AGENTS.md, CLAUDE.md, README)"
git push -u origin HEAD
workflow_pr=$(gh pr create --repo "$workflow_host/$workflow_repo" --draft --base "$workflow_default" --title "chore: formalise the git/github workflow" --body-file <file>) || exit 2
export workflow_pr
```

Apply finish, resolve findings and validate the final head before ready. Continue with merge when ready; repair technical gaps and request only a genuinely missing authorization.

## `update` — migrate existing project instructions

Use chore/agent-workflow-update and the same PR delivery. The policy applicable before this PR governs its delivery: changing policy cannot authorize the PR itself. Preserve applicable explicit restrictions at every version. Current user authorization is outside this PR and needs no repeat request.

1. With a managed block, read its version/authentic snapshot and compare old template, installed block and new template. Use `git diff --no-index --word-diff=plain <historical-rendered-block> <installed-block>` for reformatted content (1 means differences, >1 error). Follow [migration/history](references/adoption-and-licenses.md#migration-and-history). Reapply real commands/branch/method/gates and local customizations. If history is missing, compare manually and preserve ambiguous rules. Preserve outside-block content except authorized targeted Known pitfalls updates.
2. Without markers, compare section by section and add only missing rules. Distinguish old defaults from deliberate restrictions; preserve explicit holds and all copies, moving them outside the managed block when needed. Preserve ambiguous provenance. Propose markers around methodology and replace review rules without leaving competing procedures.
3. Apply relevant CLAUDE.md/CI/pitfall changes and check README usage. Preserve stronger local gates and compare contracts before/after. New content is English; a full existing-content translation is a distinct task. Inspect the license under the policy. Read known instruction files/imports and search changed-rule copies with `rg --hidden -n -i 'merge|merger|fusionn|approval|approbation|accord|validation' -g '*.md' -g '!.git/**' .`. Search supports reading, not proof that no restriction exists.
4. Run the init placeholder check, covering README.md, LICENSE*, .yml/.yaml and absent CI, distinguishing exit 1 from errors.
5. Show the diff and summarize added/modified rules.

**Package v6.5.0; active repository template v6.4.** This package adds technical initiative, incidental issue capture, written collaboration, review convergence, verified agent attribution and observed model/provider provenance in the shared profiles/skill. Deliver a missing/older project-block update through its separate PR after the current task. Older versions migrate using authentic history/cmp and preserved local rules, not blind replacement.

## What's new

- **v6.5.0 / template v6.4**: proactive technical judgment, deduplicated incidental issues, useful next-action reporting and durable issue/PR/code reasoning across Codex, Claude Code and Hermes. Shared profiles record verified agent attribution and observed model/provider provenance. Shared profiles and the skill also require review convergence: targeted rechecks, a root-design checkpoint after repeated unresolved cycles and delivery once ready. Existing delivery and authorization safeguards remain applicable.
- **v6.4.0**: versioned source repository, neutral profile source, reproducible packages, portable regression checks and issue/PR handoff guidance. Runtime guards and the active repository template retain their behavior.
- **v6.3.1**: English personal instructions, skill, references and distribution files; README.md and English installer names. Runtime guards, licenses, role files and authentic template history retain their bytes. French remains the conversation language; historical aliases/test inputs remain exact data.
- **v6.3**: origin binding, detached rebase/bisect guards, Unicode/published holds, inherited reporting preferences, qualified Claude installation/status, public-license authorization, historical word comparisons and lockfile synchronization.
- **v6.2**: documentation-only path, high-stakes CI/hooks/gates, suspension aliases, blocking Pages errors, no external jq, local Git identity, license/manifest alignment, README/About checks, precise Claude worktree lifecycle, recoverable branch deletion, five Codex role exports and conditional installation.
- **v6.1**: default license/adoption policies, init all/readme, useful Computer Use, proportionate review, about-120-second CI fallback, Claude session protection, English private-data-free repository content, harmonized review/hold labels and autonomous ready-PR closure.
- **v6**: observable development loop, autonomy, shared contracts/neighbors, maintainability, verified publication and recoverable closure.
- **v5.1/v5.2**: retain authentic available snapshots/customizations; version numbers do not establish their content.

`init all` follows [adoption scope/sequence](references/adoption-and-licenses.md#init-all). `readme` follows its [guide](references/readme-guide.md) without automatically migrating workflow/license/permissions. Check README/About together and group each task's documentation fixes.

## Task lifecycle

Apply by default in owned repositories, and on request in forks/third parties. Track missing/old adoption initially and finish the current task before its separate PR. Preserve newer, unknown or customized blocks.

### `start <n>`

```bash
gh issue view <n> --repo "$workflow_host/$workflow_repo"
git status --short
git ls-remote --heads origin "*/<n>-*"
gh pr list --repo "$workflow_host/$workflow_repo" --state open --search "<n>"
gh issue develop <n> --repo "$workflow_host/$workflow_repo" --name <type>/<n>-<slug> --base <default> --checkout
```

- For nontrivial entrusted work without an issue, check for duplicates and create one (`gh issue create --repo "$workflow_host/$workflow_repo"`) without another prompt. Otherwise use `git fetch origin && git switch --no-track -c <type>/<slug> origin/<default>`; no-track prevents accidentally tracking origin/default.
- Inventory ownership/activity and reuse a free checkout before creating isolation. Revisit this task's retained resources, preserve other sessions and current work, and push only owned/entrusted branches.
- For substantial API/schema/security/dependency work, state approach, invariants and validation, then continue authorized work. Technical difficulty alone is not an approval gate. Prepare options for a missing product decision; actual migrations/publication retain their boundary.
- Open a draft PR on first push and capture `workflow_pr` using [Select one PR explicitly](#select-one-pr-explicitly), so other agents can see the claim. On resume, resolve that existing PR instead of creating another.

### Commit and push meaningful increments

An increment is coherent and verified: working feature slice, fixed bug, completed refactoring or meaningful tests.

1. Relevant lint/tests pass using project commands.
2. Stage reviewed files; inspect status/staged diff and exclude secrets/.env/artifacts. Blind git add -A is inappropriate.
3. Use a Conventional Commit, for example `fix(scope): summary`, adding an issue reference when present.
4. Push, using `git push -u origin HEAD` initially.

Keep hooks/tests/CI intact; diagnose and fix failures or state the blocker. Bypassing checks is not an execution path.

### `finish`

```bash
git fetch origin && git merge origin/<default>
<applicable-gate-commands>
git diff origin/<default>...HEAD
# Resolve workflow_pr using the selector contract above; the first push already opened its draft.
gh pr checks --repo "$workflow_host/$workflow_repo" "$workflow_pr"
gh pr checks --repo "$workflow_host/$workflow_repo" "$workflow_pr" --watch
```

- After base synchronization, regenerate a changed lockfile through the configured manager/version, then verify frozen installation and diff; avoid manual lockfile edits.
- Follow project §3 for conflict resolution and lockfile resync. If uncertain, git merge --abort and request the concrete missing decision.
- Self-review scope, secrets, neighbors and maintenance cost. Apply the documentation/behavior review scale above and in the [loop](references/development-loop.md). Ordinary behavior uses one fresh reviewer; high-risk/large behavior uses multiple passes plus a skeptic without a quota. Initial reviewers do not get author rationale or cross-findings. The skeptic gets final diff, validation, findings and evidence. Recheck significant deltas. Every PR has `- Independent review: not required (<reason>)` or `- Independent review: done on <sha>; findings: <none/fixed/justified>`. Inspect and validate final head and review delta.
- Prove a fix using the same final test without/with the fix after required rebuilds, and attach the results.
- Update README in the same branch when commands/config/API/screens/usage change.
- PR body: Why / What / How to test / Evidence (before/after visuals or measured gains/conditions) / Rollback (revert, flag, manual step, or already-authorized irreversible action) / gate / optional Closes/Refs.
- Conventional Commit PR title becomes the squash commit message.
- For red CI, first read the current SHA with `workflow_sha=$(gh pr view --repo "$workflow_host/$workflow_repo" "$workflow_pr" --json headRefOid --jq .headRefOid)`. List runs with `gh run list --repo "$workflow_host/$workflow_repo" --commit "$workflow_sha" --json databaseId,headSha,workflowName,status,conclusion`. Set `workflow_run` to the actual failing run's databaseId after inspecting its workflow/head; never assume the first run is the relevant one. Then use `gh run view --repo "$workflow_host/$workflow_repo" "$workflow_run" --log-failed`, fix, commit/push and observe again. Read errors or an empty/changed SHA require new valid evidence before proceeding.
- For pending CI, use the [shared procedure](references/ci-local-gate.md): configured fallback only after about 120 seconds with no started check/assigned runner on that SHA. Wait for running work, block on red, and reread remote state before merge. gh pr checks exit 8 means pending for the documented CLI behavior.
- After review and available gates pass on the final head, use `gh pr ready --repo "$workflow_host/$workflow_repo" "$workflow_pr"` and inspect triggered checks. If ready triggers CI, finish review/local preparation first, then follow the procedure. Effective protection and required checks or authorized fallback remain mandatory.
- Complete the DoD and continue to merge. Repair technical gaps; ask only for an unresolved decision/authorization after checking the exact applicable rule/current request. Conversation reports follow personal preference; put progress and the next action together and continue authorized work.

### `merge <pr>`

A truly ready entrusted PR must be integrated, verified and closed in this turn. Outstanding decisions mean it is not ready for autonomous delivery. Honor applicable explicit waits. Before citing an approval rule, read/quote it and check current authorization; an older generic go requirement does not revoke present delegation.

Fetch the final-head PR body into a temporary file. The first grep must succeed; the second must find no match. Then read the review line in context: visible outside comments/examples, real justification, checked revision and addressed findings. Reject unresolved placeholders. Shape matching does not prove visibility/truth; semantic checking is mandatory. Read/grep errors block.

```bash
gh pr view --repo "$workflow_host/$workflow_repo" <pr> --json body --jq .body > <pr-body-file>
grep -nE '^[[:space:]]*-[[:space:]]*Independent review:[[:space:]]*(not required[[:space:]]*\([^[:space:]()<>][^()<>]*\)|done on[[:space:]]+[0-9a-f]{7,40};[[:space:]]*findings:[[:space:]]*(none|fixed|justified)([[:space:]]+[^[:space:]].*)?)[[:space:]]*$' <pr-body-file>
grep -nF 'TO FILL' <pr-body-file>
```

After successful git fetch origin, resolve all applicable instructions/imports and run `bash <skill-dir>/scripts/merge-preflight.sh suspension <files...>`: 0 means no known pattern, 1 dated veto, 2 blocking ambiguity/error. It normalizes case, Unicode dashes, NBSP, CRLF and wrapped lines, reads invisible format characters such as BOM, zero-width, bidirectional and soft-hyphen characters both as removed and as spaces, and reads free-form restrictions per Markdown block with heading, lead-in, parent-item and table-header context, keeps 2 for a merge item after a negated lead-in item or row of the same list or table (`- Do not do the following`, `- Never under any circumstances`) and for pause, approval or wait wording across the items of adjacent lists and tables; without hold, approval, PR or branch words it clears only statements whose every merge word is git mechanics, a merge method beside a permitted alternative, or a plain clause whose single source is an upstream remote, and keeps 2 when hold or approval wording appears anywhere in the file or when a later block of the rule's section, subsections included, refers back to it or names who decides or checks (`A human must review each one.`); returns 2 for inputs over 131,072 characters; ignores only full-line managed blocks and literal date examples; recognizes the v5 and French aliases below; and reads published origin/default AGENTS.md after verifying its tree. Refresh origin first. Zero is not semantic proof of absence. Read all instructions: an applicable free-form restriction missed by the scanner still makes the gate indeterminate (state 2) until context resolves it; current specific authorization may already do so. A scan result is not authorization. Normalize historical French review labels in the current PR to Independent review.

```bash
gh pr view --repo "$workflow_host/$workflow_repo" <pr> --json state,isDraft,mergeable,reviewDecision,reviewRequests,statusCheckRollup,headRefOid,title
gh pr view --repo "$workflow_host/$workflow_repo" <pr> --comments
bash <skill-dir>/scripts/merge-preflight.sh reviews <owner> <repo> <pr>
gh api --hostname "$workflow_host" repos/$workflow_repo/deployments --jq length
bash <skill-dir>/scripts/merge-preflight.sh pages <owner> <repo>
git fetch origin && git merge-base --is-ancestor origin/<default> <headRefOid>
workflow_author_email=$(bash <skill-dir>/scripts/merge-preflight.sh identity <owner> <repo> <pr> <handle>) || exit
gh pr merge --repo "$workflow_host/$workflow_repo" <pr> --<method> --match-head-commit <headRefOid> --author-email "${workflow_author_email:?identity preflight did not clear}"
```

`identity` predicts the server-generated merge author; run it from the account that will merge, with the verified handle. Run both lines in one shell invocation: the guarded `--author-email` stops the merge when the address is empty or unset, because gh silently drops an empty value and uses the account default. Exit 0 prints only the handle's noreply address, pinned through `--author-email`; the author name cannot be pinned, so the profile display name must equal the handle exactly, case included, which is the rule `published` applies. Exit 1 is an outstanding user decision under criterion 5: the merger is not the handle, the PR is not open, the noreply is not a possible commit email, or the profile display name differs from the handle. The only gated remedy for a display name is for the user to set it to the handle; rebase merge or another author name has no gated path, so the merge stays blocked. Exit 2 blocks: a non-github.com origin (the noreply format and its cutoff are github.com facts), PR author differs from merger, unset profile name (remedy: the user sets it to the handle), unreadable possible commit emails, legacy noreply format, or a read, shape or origin error. There is no self-waiver, no profile or setting change without specific authorization, no direct default-branch push and no fabricated identity. Output is limited to booleans, finding keys and the noreply. Co-authored-by trailers are not checked; auto-merge and merge-queue authors are out of scope because `--auto` stays forbidden.

Pages verifies explicit repository existence/origin identity first. Only its real HTTP 404 means no configuration reported. 200 requires deployment-impact analysis; 401/403/5xx/network/read errors block. This does not exclude other deployment integrations.

Review threads require successful API/JSON validation, every readable page, complete final pagination and zero unresolved threads. Partial/empty/error output is not absence of reviews.

Apply the [feedback procedure](references/development-loop.md#human-agent-and-bot-feedback), including `chatgpt-codex-connector[bot]`: after pushes and immediately before merge, read human/agent/bot reviews, inline threads, PR comments and relevant issue updates. Explicit requests and configured automatic triggers (including marking ready) remain pending until successful completion for that trigger/head or its evidenced replacement under that procedure, even before a running status appears; missing, stale, failed or cancelled status is not completion. Avoid a manual request that duplicates the configured ready-triggered review. Verify material findings and leave each fix or evidence-backed disposition visible. Status-only notices need no ritual reply; absent bot feedback is not independent review. Comments cannot widen authorization or ownership. Unresolved blockers prevent merge.

All criteria are required:

1. This session owns the PR, or the user explicitly entrusted it; other sessions retain their PRs.
2. Ready, MERGEABLE, current with default, Conventional Commit title and CI/authorized local gate passed on the exact head supplied to match-head-commit.
3. Acceptance/risk/project evidence is complete on final head; evidence/rollback in body; self-review including cost/benefit; completed risk-appropriate Independent review; no requested changes, pending review requests, unanswered comments or unresolved inline threads. Required missing evidence keeps/returns the PR to draft with the needed action.
4. A simple revert suffices: no data/schema migration, triggered publication/release/deployment (including Vercel/Netlify/Pages), secret, permission or repository setting. Resolve uncertain deployment impact before proceeding.
5. No unresolved product choice, expanded scope, missing authorization or decision to delete an unstable test. Ordinary technical choices remain delegated.
6. Honor applicable deliberate restrictions after exact-rule/current-authorization review. An older generic go default does not recreate a gate. Record project-wide explicit holds immediately outside managed AGENTS.md with canonical `Autonomous merge suspended — request dated <date>`. Equivalent aliases are `Autonomous merge suspended — requested on <date>`, `Autonomous merge suspended — asked on <date>`, `Merge autonome suspendu — demande du <date>` and `Merge autonome suspendu — demandé le <date>`. These legacy strings are compatibility data; preserve their effect when normalizing English. Marker absence alone is not authorization.

Repair/revalidate technical gaps. Wait for running checks. Use the documented 120-second fallback only for an eligible unstarted run. Prepare evidence/rollback for a real hold/decision/unauthorized action and ask only the necessary question. Authorization does not turn a red check green or permit destroying unintegrated work.

- Conflicting/outdated branches return to finish/synchronization. Use no admin override, auto merge, fabricated status or weakened protection.
- Correct the title via `gh pr edit --repo "$workflow_host/$workflow_repo" <pr> --title ...` when needed.
- match-head-commit rejects a changed head; revalidate rather than bypassing it.
- Verify publication then cleanup before reporting. On verification failure use fix/revert and retain useful diagnostic resources.

### `cleanup`

Read the [resource lifecycle](references/workspace-lifecycle.md). Safe cleanup of this task's/entrusted resources is already delegated; unrelated global cleanup requires explicit scope. Names and integrated PRs do not prove ownership.

```bash
gh pr view --repo "$workflow_host/$workflow_repo" <pr> --json state,headRefOid,mergeCommit
git fetch --prune origin
git merge-base --is-ancestor <mergeCommit> origin/<default>
git rev-parse refs/heads/<branch>
git ls-remote --heads origin refs/heads/<branch>
git worktree list --porcelain
git diff --quiet <headRefOid> <mergeCommit>
```

Tree equality establishes only a simple squash case; otherwise prove actual inclusion. A new tip is not covered by an old PR. Preserve dirty/divergent local default branches and verify publication through origin/default.

Release a branch only after safe reuse/detachment, native archival or owner-appropriate removal of dependent worktrees. Absent refs need no deletion; read failure is not absence. Delete a remote ref still at its expected head using `git push --force-with-lease=refs/heads/<branch>:<headRefOid> origin :refs/heads/<branch>`; reread after refusal. For the local ref, establish recovery then run `bash <skill-dir>/scripts/check-branch-unused.sh <branch>` immediately before `git update-ref -d refs/heads/<branch> <headRefOid>`, requiring exit 0. Attached/rebasing/bisecting or unreadable state blocks. The atomic tip comparison is not a substitute for ownership/activity/inclusion/recovery/protection checks. No forced branch/worktree deletion.

Re-inventory branches/worktrees and report removed/reused/retained, with reason/owner/trigger for each remainder. Verified recovery removes the need for just-in-case retention. Keep deletion separate from merge and perform it after publication checks.

### Verify after merge

```bash
gh pr view --repo "$workflow_host/$workflow_repo" <pr> --json headRefOid,mergeCommit -q '.headRefOid + " " + .mergeCommit.oid'
gh run list --repo "$workflow_host/$workflow_repo" --branch <default> --commit <mergeCommit> --json databaseId,status,conclusion,workflowName
gh run watch --repo "$workflow_host/$workflow_repo" <id>
bash <skill-dir>/scripts/merge-preflight.sh published <owner> <repo> <pr> <handle>
```

- `published` reads the PR's merge commit and compares its author name, email, login and id with the handle, its committer with GitHub (web-flow) and reports its signature state, which never blocks, as booleans only. It reads only the merge commit, so it covers squash and merge commits, not the commits a rebase merge re-creates. Exit 1 is a published mismatch: record it on the PR or issue without personal data; never rewrite history, and a revert is no remedy because it carries the same server author. Exit 2 means the published identity is unverified.

- With no run for the merge commit, inspect triggers/state and apply the same 120-second unstarted-run rule before eligible fallback. If no post-merge trigger exists, use the relevant local gate and label the limit. Earlier green runs prove nothing about this commit.
- For applicable local validation, use `git fetch origin && git diff --quiet <headRefOid> <mergeCommit>`: 0 means the validated tree is identical; 1 requires the gate on the merged tree in an available isolated checkout; other codes need diagnosis. A local check is not remote CI.
- Report a red result promptly, diagnose and create a fix/revert PR. `git revert <mergeCommit>` uses -m 1 for a merge commit where appropriate. Deliver under the same criteria; never push a fix directly to default.
- Report actual results in the task closure.

## Non-negotiable boundaries

- Project instructions and applicable current user authorization govern; read them before Git.
- Default-branch changes use PRs except the authorized first bootstrap of an explicitly requested new repository. Preserve the default branch from force-push.
- Verify MERGED and recovery evidence before deleting refs.
- Creating/publishing repositories, changing settings and deleting unintegrated work retain their authorization boundary. Opening a draft PR from an owned branch is part of the delegated workflow. Fix technical prerequisites before asking for a genuinely missing decision; red checks remain blockers.
- Be ambitious on reversible in-scope work, change hypothesis after two uninformative failures, and prepare evidence/rollback before an unauthorized irreversible action. Ready PR delivery under all gates stays autonomous.
- Exclude secrets from commits. If a secret was pushed, notify the user promptly: rotation is needed; rewriting history is insufficient.
