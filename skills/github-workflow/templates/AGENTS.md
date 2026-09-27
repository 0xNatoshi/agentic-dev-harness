# AGENTS.md

<!-- github-workflow:start v6.3 — managed block from the github-workflow skill; preserve local safeguards and explicit approval requirements on update -->

Working instructions for AI agents (Claude Code, Codex, Copilot, Cursor…) and humans on this repository.
Expected stance: **a senior developer working in project mode** — understand before coding, ship in small verified increments, leave the repository cleaner than you found it.

## Repository content

Use English for all repository documentation, README, AGENTS.md, CLAUDE.md, code comments, commits and PRs. Do not publish real personal names, personal email addresses, passwords or machine-specific paths. Use neutral examples and the verified organization or GitHub identifier as license holder. For Git author and committer, use a verified organization or GitHub identifier with an approved professional address or confirmed GitHub noreply address; never a real personal name or personal email. If user.name or user.email exposes a real personal name or personal address, set a repository-local identity with `git config --local user.name "<verified-handle>"` and `git config --local user.email "<confirmed-noreply>"`; verify author/committer and environment overrides before committing. Confirm the noreply address from the account settings or established evidence. Never change global Git configuration, invent an identity or rewrite existing history for this repair. Personal global instructions belong outside the repository.

## Project commands

| Role      | Command             |
|-----------|---------------------|
| Install   | `{{INSTALL_CMD}}`   |
| Build     | `{{BUILD_CMD}}`     |
| Lint      | `{{LINT_CMD}}`      |
| Typecheck | `{{TYPECHECK_CMD}}` |
| Tests     | `{{TEST_CMD}}`      |

CI (`{{CI_FILE}}`) is a separate verification environment. Match its relevant configuration and dependencies; local success alone does not establish CI success. Define path-specific gates for surfaces excluded by CI.

## Working method

1. **Own an observable result.** Define acceptance criteria and scope in the existing issue, PR or task record. For non-trivial work, create an issue when project policy permits it; preserve any approval rule for audits or external writes. A technical plan informs the user and does not itself require another approval.
2. **Understand before coding.** Follow the affected path to its owner; read callers, contracts, state and tests. Reproduce a bug or establish the relevant baseline. Map acceptance criteria and exposed neighboring behavior to checks before implementation. Make technical decisions within the delegated scope; ask only for a missing product decision, indispensable access or an action whose authorization is still required. After two attempts on the same hypothesis without new evidence, diagnose and change approach or seek independent analysis; do not repeat blindly or bypass a gate.
3. **Small increments.** One PR = one topic. Split risky work from mechanical changes when useful, but group documentation corrections for the same task in one PR; do not create one PR per documentation finding.
4. **Respect what exists.** Follow the project's style, structure and patterns; no opportunistic refactor outside scope (open an issue instead).
5. **No shortcuts.** Never disable a test, bypass a hook (`--no-verify`), ignore a lint error, or lower a coverage threshold to "make it pass". Fix the root cause.
6. **Bounded audacity.** Distinguish **reversible** from **irreversible**.
   - **Reversible** (your branch, draft PR, worktree, spike, code behind a flag): aim high within the delegated scope. If the existing structure causes the problem, consider a focused redesign as well as a local fix, then choose the simplest adequate solution. A complex technical choice alone is not an approval gate.
   - **Before anything irreversible** (merge, data migration, publication, deletion beyond merged branches, repository settings): bring the proof (green tests, including a proven test for a fix; before/after measurements if a gain is claimed; cost/benefit self-review), identify the **rollback path** (revert of the merged commit, flag turned off), then get the user's approval. Exception: a PR meeting **all** the *autonomous merge* criteria (§4) merges without waiting for approval; proof and rollback path are still required.
   - **No way back** (destructive migration, data, publication): careful mode — prepare the reversible work and plan, obtain approval, then perform the irreversible action.
   - Meeting the criteria does not stop thinking but does not grow the PR: follow-up leads become proposed issues or PRs. The report also says what was tried and abandoned, and why.

## Implementation, maintainability and integration

- Correct the cause in the component that owns the behavior. Keep one owner for each rule and state; reuse existing contracts. Justify new layers, dependencies, duplicated rules or compatibility paths with a present need.
- Refactor the affected area when necessary for the fix. Remove replaced code, temporary workarounds, obsolete flags and documentation in the same coherent slice. A broader redesign is split into compatible, verifiable increments; unrelated improvements remain outside the current change.
- Before final review, inspect callers and consumers, error paths and relevant shared state. Verify the changed path and neighboring behavior exposed by the change. For concurrency, persistence or retries, exercise the actual risk: duplication, ordering, restart, cancellation or partial failure.
- Except for documentation-only work, delegate only when the expected time or quality gain exceeds coordination and context cost. Delegate bounded independent work with an owner, file scope, contracts and expected proof. Use available explorer/operator roles for focused work, workers for implementation, reviewers for independent checks, and the most capable architecture role for difficult invariants. Avoid concurrent writes to shared files or Git state. The parent integrates and validates the combined result against the original criteria.
- Keep durable architecture decisions in the existing project documentation when they change contracts or ownership. A temporary compromise needs a removal condition and follow-up under project policy; do not add a new tracking system for it.

## Git / GitHub workflow

Bind GitHub operations to the verified `origin` repository before starting and again before merge/cleanup. Run the skill’s `merge-preflight.sh origin` to populate `workflow_host`, `workflow_repo` and `workflow_default`. Every `gh pr`/`gh run` command uses `--repo "$workflow_host/$workflow_repo"`. `gh api` has no `--repo` option: use `--hostname "$workflow_host"` and an explicit `repos/$workflow_repo/...` endpoint or origin-derived GraphQL owner/name. A mismatch between gh’s implicit repository and origin, or unreadable identity, blocks.

For an explicitly requested new repository before origin exists, use only the skill's narrowly scoped bootstrap path with a verified, explicitly named host and repository. Verify origin and repository identity as soon as they exist; if the remote is still empty, the missing default branch permits only the authorized first bootstrap push. Run the complete preflight after that push and before any PR/merge/cleanup operation.

Default branch: `{{DEFAULT_BRANCH}}` — **all ordinary changes go through a PR**. For an explicitly requested new repository with no commit history, include these project instructions and the policy-selected license in the first bootstrap commit, before its authorized first publication. This one-time bootstrap is the only direct-commit exception; existing repositories and all subsequent changes use PRs.

A public license grant is irreversible: prepare the policy-selected text, then obtain explicit approval of that grant before publishing it (including in a public PR) or merging it. Prior specific approval counts; general development or autonomous-merge authorization does not. This also applies to the first public bootstrap commit.

### 1. One branch per implementation / issue

- Check the issue is not already taken (another agent, another session): `git ls-remote --heads origin "*/<n>-*"` and `gh pr list --repo "$workflow_host/$workflow_repo" --state open --search "<n>"`. Reuse a branch only when it is yours or explicitly entrusted. Preserve existing work. Inventory ownership and active use, and reuse a suitable free checkout before creating a worktree for a real isolation need. Revisit retained resources from your previous tasks at this transition; do not touch another session’s resources.
- One issue / implementation per branch, created from the up-to-date **remote** default branch (works in a worktree too):
  - Existing issue: `gh issue develop <n> --name <type>/<n>-<slug> --base {{DEFAULT_BRANCH}} --checkout`
  - No issue: `git fetch origin && git switch --no-track -c <type>/<slug> origin/{{DEFAULT_BRANCH}}`
- Naming: `<type>/<issue-number>-<short-slug>` — e.g. `feat/42-login-oauth`, `fix/57-export-crash`.
  Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `ci`.

### 2. Commit at every significant step

- Commit as soon as a coherent step is reached (partial feature that works, fix, finished refactor, added tests…) — no big catch-all commit at the end of a session.
- **Before every commit**: lint + relevant tests green. Every commit leaves the project in a state that builds and passes tests.
- [Conventional Commits](https://www.conventionalcommits.org/) format; include an issue reference when one exists:
  ```
  feat(auth): add OAuth login (#42)
  ```
- Never commit secrets, `.env`, build artifacts, generated files or debug files.
- **Interruption**: the only exception to "every commit passes tests" — a `chore(wip): …` commit (with an issue reference when one exists) is allowed on your branch (hooks green, never `--no-verify` and no stash as a backup), pushed, remaining work noted in the draft PR. **Resuming**: `gh pr view --repo "$workflow_host/$workflow_repo"`, `git log origin/{{DEFAULT_BRANCH}}..HEAD`, then sync (§3).

### 3. Push on every commit and after every merge

- Push each verified commit on your own or explicitly entrusted branch:
  ```bash
  git push -u origin HEAD   # first push of the branch
  git push                  # subsequent pushes
  ```
- Merges into `{{DEFAULT_BRANCH}}` go through the PR on GitHub, never via a local merge pushed to it; then resync the local default branch (§5).
- **Sync** your branch before the PR and whenever `{{DEFAULT_BRANCH}}` moved: `git fetch origin && git merge origin/{{DEFAULT_BRANCH}}` (never rebase an already-pushed branch). Conflicts: resolve by hand, never with `-X ours/theirs` or by overwriting someone else's work; lockfile → take `{{DEFAULT_BRANCH}}`'s then re-run the install; tracking files (claims, CHANGELOG) → keep both entries. Re-run lint + tests. When in doubt: `git merge --abort` and ask.
- `--force-with-lease` only on your own working branch, **never** force-push `{{DEFAULT_BRANCH}}`.

### 4. Pull request

- Open a PR against `{{DEFAULT_BRANCH}}` at the first push, as a draft (visibility, avoids duplicates), then mark it ready (`gh pr ready --repo "$workflow_host/$workflow_repo"`) when the work is done:
  ```bash
  gh pr create --repo "$workflow_host/$workflow_repo" --draft --base {{DEFAULT_BRANCH}} --title "<type>(scope): summary" --body-file <file>
  ```
- The title follows Conventional Commits (under squash it becomes the final commit message).
- The description contains: the **why**, the **what**, **how to test**, the **evidence**, the **rollback**, and, when an issue exists, `Closes #<n>` (or `Refs #<n>` for partial delivery).
  - **Evidence**: proven final regression tests for fixes; before/after captures for visible changes; reproducible measurements for claimed gains. Include exactly the English line `- Independent review: not required (<reason>)` or `- Independent review: done on <sha>; findings: <none/fixed/justified>`. The head and any delta after review must be inspected and validated; substantiate the review level, findings and resolutions.
  - **Rollback**: how to undo (revert of the merged commit, flag to turn off, possible manual step); "irreversible" if so, with the approval obtained beforehand.
- **Self-review** before merge: read `git diff origin/{{DEFAULT_BRANCH}}...HEAD` in full (out of scope, debug, secrets, generated files, missing tests) and ask whether **the gain is worth the code to maintain** (a marginal gain doesn't justify an extra abstraction, plugin or dependency).
- **Review scale**: Documentation only, regardless of diff size: self-review and automatic checks, without subagents or a workflow, even with `ultracode`. Group documentation corrections for the same task in one PR. Do not trigger workflow adoption, orchestration or a new CI workflow for this task; keep existing publication gates. Mechanical low-risk changes also need only self-review and relevant checks. Changes to executable rules, permissions, CI, hooks or gate configuration change behavior even when stored in Markdown; review them according to that risk. Ordinary behavioral changes need one fresh-context reviewer. Large behavioral PRs or high-stakes changes (security, data, agent autonomy, CI, hooks or gate configuration, or difficult contracts) need multiple review passes and a fresh-context skeptical agent, without a fixed agent quota. Choose angles by risk. First analyses receive the request, rules and diff without author reasoning or other review conclusions; the skeptic receives the final diff, findings, corrections and validations. Recheck significant deltas with the relevant reviewers. The parent remains responsible for integrated final validation.
- Follow CI on the exact PR head. If no check has started and no runner has taken a job after about 120 seconds, treat runners as unavailable for this run: execute the configured local gate, put its commands/results and head SHA in the PR, then merge when the remaining criteria hold. Wait for checks already running; any failed check blocks. Verify the actual jobs, not only a queued workflow label: approval, concurrency/dependency waits, API errors or incomplete evidence do not prove runner unavailability. Re-read status immediately before merge. Keep remote protections; never use admin override or mark CI green from local results. The skill's `ci-local-gate.md` supplies the complete evidence procedure.
- **Autonomous merge.** Merge **without waiting for approval** when **all** these criteria hold. Repair technical gaps and wait for running checks; ask only when a decision or authorization boundary remains:
  - it is **your** PR (opened in this session, or explicitly entrusted by the user), never another agent's or another session's;
  - it is ready: not a draft, `MERGEABLE`, up to date with `{{DEFAULT_BRANCH}}` (`git fetch origin && git merge-base --is-ancestor origin/{{DEFAULT_BRANCH}} <headRefOid>`), Conventional Commits title, CI (or local gate) green **on the final head**;
  - acceptance criteria and checks required by the risk and project rules are satisfied on the final head, evidence and rollback are in the PR, the self-review (including cost/benefit) is done, the *Independent review* line is filled (risk-appropriate review complete, findings addressed), no change requests, no pending review request, no unanswered comment and no unresolved inline review thread (`gh pr view --repo "$workflow_host/$workflow_repo" <n> --json reviewDecision,reviewRequests`, `gh pr view --repo "$workflow_host/$workflow_repo" <n> --comments`, and `reviewThreads.isResolved` via `gh api --hostname "$workflow_host" graphql`, see the `github-workflow` skill). An unavailable environment documented in the PR does not replace required evidence: keep or return the PR to draft and identify the needed access or action;
  - a simple revert undoes it: no data or schema migration, no publication, release or deployment triggered by the merge (including Vercel / Netlify / Pages integrations, `gh api --hostname "$workflow_host" repos/$workflow_repo/deployments --jq length` non-zero; only an HTTP 404 from the Pages endpoint means no Pages configuration; all other API/network errors block, and HTTP 200 requires deployment-impact analysis; when in doubt, ask), no secret, permission or repository setting;
  - no decision is waiting on the user: an unresolved product choice, widened scope, required authorization or flaky test to remove;
  - the user has not asked to wait for their approval (for this PR or this project); a project-wide request is recorded immediately in this file, outside the managed block ("Autonomous merge suspended — request dated <date>"), so other sessions see it.

  Before merging, save the current PR body and run both checks. The first must match a populated review line; the second must return no match (exit 1, not an error). Inspect the matching line in context: it must be visible outside HTML comments, code examples and quoted templates, with a real reason or reviewed revision and resolved findings. Reject remaining example placeholders. Read the review evidence as well; grep checks the line format, not its visibility or truth.

  ```bash
  gh pr view --repo "$workflow_host/$workflow_repo" <n> --json body --jq .body > <pr-body-file>
  grep -nE '^[[:space:]]*-[[:space:]]*Independent review:[[:space:]]*(not required[[:space:]]*\([^[:space:]()<>][^()<>]*\)|done on[[:space:]]+[0-9a-f]{7,40};[[:space:]]*findings:[[:space:]]*(none|fixed|justified)([[:space:]]+[^[:space:]].*)?)[[:space:]]*$' <pr-body-file>
  grep -nF 'TO FILL' <pr-body-file>   # Expect no match; errors must be resolved.
  ```

  The canonical suspension marker is `Autonomous merge suspended — request dated <date>`. The v5 aliases `Autonomous merge suspended — requested on <date>` and `Autonomous merge suspended — asked on <date>`, and `Merge autonome suspendu — demande du <date>`, are equivalent vetoes. Detection ignores the managed block and example lines containing the literal `<date>`, and blocks on malformed managed markers. Use the skill’s `scripts/merge-preflight.sh suspension` on all applicable files and resolved imports; also read other explicit restrictions. Also accept `Merge autonome suspendu — demandé le <date>`. Normalize case, Unicode dashes, NBSP, BOM and CRLF and join wrapped lines. Anchor managed delimiters to complete lines. A noncanonical free-form suspension requires clarification (exit 2), never implicit permission. After a successful origin fetch, scan the published `origin/<default>:AGENTS.md` as well. Treat applicable historical markers as equivalent vetoes; preserve the restriction when normalizing to English. Inspect all applicable instructions and imports before merge.

  Command: `gh pr merge --repo "$workflow_host/$workflow_repo" <n> --{{MERGE_METHOD}} --match-head-commit <headRefOid>` (never `--auto` or `--admin`). Verify the merge before branch cleanup (§5), then report the PR, merged commit and verification result.

When these criteria are satisfied, merge, verify publication and safely close your branches/worktrees before ending the turn; do not report a merge-ready PR and ask for a ritual go. If a real decision or authorization is still missing, name that concrete blocker. Before claiming that a file requires approval, read and cite its exact applicable rule and check whether the user's current authorization already supersedes it. Preserve explicit wait instructions still in force; an older generic go requirement does not revoke current authorization.

### 5. Verify publication and close local resources

**Verify before cleanup**: confirm the PR is `MERGED`, fetch `origin`, and establish that its merge commit is included in `origin/{{DEFAULT_BRANCH}}`. Check CI on the merged commit, never an earlier green run. Wait for running jobs; failed checks block completion and trigger the fix/revert workflow. Apply the same 120-second no-runner rule when post-merge checks have not started. With the applicable local gate, equal head/merge trees establish reuse of the validated tree; otherwise run the gate on the merged result in an available isolated checkout. Label local verification honestly and retain resources needed for diagnosis.

Safe cleanup of your own or explicitly entrusted task resources is part of completion and requires no additional go. Review it after verified delivery, abandonment and before the next task. A merged PR does not automatically remove local worktrees.

- Establish ownership, app management and absence of dependent sessions/processes; `git worktree list --porcelain` is only inventory. Preserve primary, pinned, shared, locked and active checkouts, default/protected branches and other sessions' resources. Preserve tracked, untracked and useful ignored files; a clean Git status does not cover ignored data. Record any surviving resource in the local task record with its owner, reason and concrete recheck trigger.
- Reuse a suitable free checkout. For `.claude/worktrees/` or a configured Claude app worktree location, never use `git worktree remove` or shell deletion: use `ExitWorktree` only for a worktree entered through `EnterWorktree` in this same session after recovery checks. For app-created worktrees, use the owning session’s app archive; preserve another active session’s worktree. If that action is unavailable or ownership is unknown, retain it and report the owner and required app action. For Codex, use its native archive when available, preserving needed ignored files first; without that app tool retain the worktree. For other app-managed checkouts, use their native mechanism. Manual unmanaged worktrees must be created outside `.claude/worktrees/` and configured app-managed roots; prefer native creation when available. Only ordinary unmanaged Git worktrees may use `git worktree remove <path>` without force after recovery and activity checks; diagnose any refusal. A broken foreign-machine `.git` link requires a separately entrusted recovery/cleanup task.
- Before deleting either branch ref, read the current tips and PR `headRefOid`/`mergeCommit`. Each existing tip must match the PR head; establish inclusion (equal head/merge trees prove a simple squash; differences require actual change analysis), no uncommitted dependent work, and recovery. Preserve exact head/merge SHAs and a surviving reachable ref; after squash/rebase with otherwise unreachable history, keep a verified native archive or Git bundle before deleting the last ref. Record recovery outside the checkout being removed.
- Detach only your free, accounted-for checkout if needed; run the skill’s `check-branch-unused.sh <branch>` immediately before `git update-ref -d` (exit 0 required); it checks all registered worktrees and their `rebase-merge/head-name`, `rebase-apply/head-name` and `BISECT_START` files, including detached worktrees, recheck exclusive ownership and absence of a protected/default branch before ref deletion. An already absent ref needs no action; a read error is not absence. Delete an expected remote ref with `git push --force-with-lease=refs/heads/<branch>:<headRefOid> origin :refs/heads/<branch>`; a rejected lease requires a new read. Delete the expected local ref with `git update-ref -d refs/heads/<branch> <headRefOid>`; its compare-and-delete protects against a changed tip but does not check ownership or worktree use. Never use forced worktree removal or `git branch -D` to bypass these checks.
- Re-read worktree and branch inventories and report **removed / reused / retained**. For each remainder, give its reason, owner and recheck trigger (such as that task's verified PR merge). Resume this follow-up when resuming/closing that task; do not claim a watcher or automatic disappearance exists unless it was actually configured. Keep merged branches only for a concrete remaining need, not merely "just in case" once recovery is assured. Never force a local default branch to match the remote; preserve dirty/divergent state.

## Quality & tests

After syncing the base, resynchronize the lockfile with changed manifests using the configured package manager/version, then verify a frozen install and the resulting diff. Do not hand-edit the lockfile.

- Every new behavior or bug fix comes with **tests** (a bug fix ideally starts with a failing test).
- **For a bug fix, a proven test**: the same final regression test must fail without the fix and pass with it. Use isolated states with the test present in both and rebuild before each run when required; do not restore files over uncommitted work. If the test changes, repeat the red/green proof. Record both revisions, commands and results in the PR.
- Test observable behavior, exposed consumers and relevant failure paths. Use unit, integration and real-path checks where each risk can occur; avoid tests that only duplicate implementation or mocks. A green test count does not prove unexercised behavior. Record pre-existing failures and unavailable environments, and do not claim full verification when a required surface remains untested.
- Tests are deterministic, fast and independent (no network / order / uncontrolled clock dependency). A flaky test gets fixed, not re-run until green; if impossible, report it: deleting or quarantining it (with an issue) is the user's decision.
- **Measure before optimizing**: a performance change starts from a reproducible measurement (benchmark, counter) showing the problem, then gives before/after numbers. For CI, prefer deterministic counters (number of operations, renders, size) over measured times, which are too noisy.
- **Ratchet thresholds**: {{RATCHETS}}. A ratchet never loosens; when the measurement improves, tighten it to the new value in the same PR (raise a minimum like coverage, lower a maximum like warnings or size).
- **Guardrails over instructions**: a discovered pitfall or bug is locked by a test, lint rule or CI check when possible; otherwise noted under "Known pitfalls".
- **Feature flags** (if the project uses them): a visible and risky change can go behind a short-lived flag, declared as an emergency switch or progressive rollout, with a removal issue.
- No dead code, no debug `console.log` / `print`, no TODO without a linked issue.
- Errors are handled explicitly; no silent `catch`.
- Update the documentation (README, CHANGELOG, docstrings) when visible behavior changes.
- Dependencies: add only with a real justification, versions pinned via the lockfile.

## Definition of Done

A task is done when **all** of this holds:

- [ ] Observable acceptance criteria met; cause and affected contracts understood
- [ ] Applicable lint, typecheck, build and test commands defined by the project pass locally
- [ ] Changed behavior and exposed neighbors checked; for a bug fix, the same final regression test fails without and passes with the fix
- [ ] Documentation updated if needed
- [ ] Replaced paths/workarounds removed; any necessary temporary debt has a removal condition and follow-up under project policy
- [ ] Commits pushed, PR ready (out of draft), issue linked with `Closes #<n>` or `Refs #<n>` when one exists, consolidated diff reviewed for behavior and maintenance cost, risk-appropriate independent review complete
- [ ] Evidence in the PR (before/after screenshots if visible, measurements and conditions if a gain is claimed) and rollback stated (or careful mode applied if irreversible)
- [ ] CI green, or the authorized no-CI/no-runner local gate passed on the final head with evidence in the PR; no active or failed check bypassed and all server protections respected
- [ ] Report the useful result and actual verification. Conversation headings follow the user's preference; repository and PR text uses **Done / Next step / Required input**, without personal names, personal email or local-machine paths. Turn limits into actions with an owner and reason for waiting. Complete necessary authorized work before the report; ask only for a real decision or indispensable manual action.
- [ ] PR merged under §4, published result verified, branches and worktrees accounted for under §5 (removed, reused or retained with owner, reason and recheck trigger); remaining proof gaps actionable

## Recap

| Stage                  | Action                                                                  |
|------------------------|-------------------------------------------------------------------------|
| New task               | Issue free? → branch `<type>/<n>-<slug>` from `origin/{{DEFAULT_BRANCH}}` |
| Exploration            | Acceptance criteria → owner and consumers → baseline → approach and checks; proceed within delegated scope |
| Significant step       | Lint + tests → `git commit` → `git push` (draft PR at the first push)   |
| Work done              | Integrate → validate behavior and exposed neighbors → risk-appropriate review → evidence + rollback → PR ready → CI green |
| §4 criteria met        | Merge → verify published result → safe cleanup → Done / Next step / Required input |

<!-- github-workflow:end -->

## Known pitfalls

<!-- Repository-specific, filled at init and enriched over time: any non-obvious pitfall found during a task is locked by a test / lint / CI check when possible, otherwise added here, in that task's PR. -->

{{KNOWN_PITFALLS}}
