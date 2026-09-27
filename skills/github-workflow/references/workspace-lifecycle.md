# Branch and worktree lifecycle

Read for `start`, `cleanup` and closure. Handle temporary resources as tasks progress, with verified recovery. This procedure implies no global disk scan, periodic deletion or remote-setting change.

## When and within what scope

- **Start**: inventory repository worktrees and task attachments through available tools. Establish ownership/activity from app metadata, task tracking and processes. A name, clean status or worktree list does not prove availability. Reuse a suitable free checkout; create one only for actual isolation. Its name need not match the new task.
- Prefer the owning app's native worktree operation. Manual Git worktree creation is for an unavailable native tool or an explicit user request. Place manual/unmanaged worktrees outside `.claude/worktrees/` and every configured app-managed root.
- **Delivery**: verify publication and post-merge gates, then close unused resources before reporting. **Abandonment**: preserve unintegrated work before archival; abandonment does not authorize destroying it or deleting an unintegrated branch. **Next task**: revisit resources retained from earlier tasks, without launching a separate autonomous cleanup project.
- Scope covers this task's resources or explicitly entrusted resources released by their owner. Safe cleanup within that scope is already authorized. Preserve other/unknown/active sessions. Historical global cleanup needs an explicit scope; integrated PRs alone do not establish one.
- Protect primary, pinned, shared, locked and active checkouts, and resources needed by a process, open PR or useful data. Default/protected branches stay protected.

## Evidence before removal

Record repository, exact path, owning app, branch, HEAD, PR, observed activity, decision and recovery in existing local tracking. Keep needed evidence outside the removed checkout. Git-excluded TASKS.md is not covered by an archive that omits ignored files.

1. **Complete state**: inspect tracked changes, untracked and ignored files (`git status --short --untracked-files=all`, `git ls-files --others --ignored --exclude-standard`). Inspect useful categories without exposing secrets. Reproducible caches/builds differ from local databases, .env, evidence and ignored work. Preserve necessary data privately outside the checkout and verify recovery; do not add it to Git merely to enable cleanup.
2. **Publication**: for an integrated branch, require PR MERGED, exact head, merge commit still reachable from the fetched remote default branch, and actual post-merge validation. Clean status or an old PR with the same branch name is insufficient. A different local/remote tip requires diagnosis without deletion.
3. **Inclusion**: ancestry of the head establishes historical inclusion; equal head/merge trees establish a simple squash. If trees differ (rebase, conflict resolution, new base), inspect actual content and PR. A title, number or empty `--merged` listing proves nothing. Preserve until inclusion is established.
4. **Recovery**: recorded SHAs are insufficient if objects can be pruned. A verified retained ref/native archive reaching the head provides recovery. If squash/rebase leaves unique commits otherwise unreachable, create a verified Git bundle before removing the final ref. Limit it to commits unreachable from a retained published base, record that base and run `git bundle verify <file>`. Otherwise retain the reference with the precise reason. Future reflog recovery is not a durable backup.
5. **Final check**: immediately before each mutation recheck expected tip, exclusive ownership, activity and protection. Changed state means retain/diagnose. Git checks do not prove absence of external use.

## Recovery bundle before removing the final reference

After inclusion/ownership proof, set `branch` to the verified name, `head` to the expected SHA and `base` to the retained published SHA providing prerequisites. Select a new private `bundle` path outside the worktree; never overwrite an archive. If all commits are already reachable from the retained base, that base supplies recovery and no empty bundle is needed.

```bash
branch='<verified-branch>'
head='<expected-head-sha>'
base='<retained-published-base-sha>'
bundle='<private-backup-dir>/<unique-name>.bundle'
[ ! -e "$bundle" ] || exit 2
[ "$(git rev-parse "refs/heads/$branch")" = "$head" ] || exit 2
git bundle create "$bundle" "refs/heads/$branch" "^$base" || exit 2
git bundle verify "$bundle" || exit 2
git bundle list-heads "$bundle" "refs/heads/$branch"   # Must name the exact expected head.
```

Explicitly verify the listed head, record the prerequisite/base SHA and bundle hash/path, and preserve the base. This differential bundle needs those prerequisites at restoration; `git bundle verify` checks them in the current repository. Recover into a free branch name with `git fetch "$bundle" "refs/heads/$branch:refs/heads/<unused-recovery-branch>"`. Any failure blocks removal of the final reference.

## Use the owner's mechanism

| Resource | Closure action |
|---|---|
| Suitable free checkout with an identified next use | Reuse; change branch/base after preserving state. Keep multiple worktrees only for concrete needs. |
| Codex-managed worktree | Inspect attachments and archive through Codex when no longer useful. Native archival preserves commits, local changes and non-ignored untracked files; preserve useful ignored data first. Respect primary/pinned/shared, submodule and embedded-repository restrictions. An integrated PR alone does not require archiving a useful checkout. |
| Claude worktree in `.claude/worktrees/` or configured root | Owned by an app session: no Git removal/unlock or shell deletion. Use ExitWorktree only after same-session EnterWorktree and inclusion/recovery/ignored-file checks. App-created worktrees require session archival in the owning app. Retain other active/unknown sessions and resources without the native tool; identify owner/action. Do not force an option that discards work. |
| Another app's worktree | Use its native mechanism after verifying guarantees. From Claude/Hermes without Codex archival, retain and name the Codex action. Git/folder deletion is not a substitute. |
| Ordinary unmanaged Git worktree outside all app roots | Manual management requires an unavailable native operation or explicit manual-Git request. Preserve useful content, release dependent processes and leave the directory, then `git worktree remove <path>` without force. Diagnose refusals; do not delete files to make it removable. A path inside an app root stays managed even if manually created. |
| Unused integrated branch | After dependent worktrees and inclusion/recovery checks, use the skill's guarded ref deletion. Unintegrated branches retain their own authorization boundaries. |
| Broken .git, another computer's path or unknown owner | Preserve and establish content/owner/recovery within an explicitly entrusted cleanup task. It is not automatically disposable. |

Remote deletion compares the expected head through a lease. Local `git update-ref -d refs/heads/<branch> <expected-head>` is an atomic tip comparison, not inclusion/protection/worktree proof. It is not an escape from an unexplained refusal. Detach only this task's free preserved checkout and recheck all worktrees. Reading/mutation errors remain explicit.

## Final worktree check before `git update-ref -d`

After exclusive ownership, inactivity, protection, inclusion and recovery checks, run the read-only guard immediately before compare-and-delete. It retains `git worktree list --porcelain` plus `grep -Fx "branch refs/heads/<branch>"`, and inspects `rebase-merge/head-name`, `rebase-apply/head-name` and `BISECT_START` in every worktree gitdir, including detached worktrees. A branch listed in `rebase-merge/update-refs` (a rebase with `--update-refs`) is in use. Git's exact `detached HEAD` rebase head-name names no branch. A `git am` leaves `rebase-apply` with an `applying` marker and no head-name; the guard then checks that worktree's own HEAD. Any other missing, empty or invalid state stays untrusted. Exit 0 means no observed use; 1 means in use; 2 means inventory/gitdir/operation state cannot be read/trusted. Both nonzero outcomes retain the branch.

```bash
(
  bash "$skill_dir/scripts/check-branch-unused.sh" "$branch" || exit $?
  git update-ref -d "refs/heads/$branch" "$head" || exit 2
)
```

Set skill_dir, branch and head to their verified values. Preserve default/protected branches and ensure no other session can reuse the resource during the operation. Matching or unreadable state blocks deletion. The read-only observation cannot be atomic with update-ref; exclusive ownership/activity checks remain necessary. Tip comparison does not establish session ownership.

## Required closure report

Re-inventory afterward and report **removed / reused / retained**. For each remainder give concrete reason, owner, next action and trigger, such as an agent revisiting a worktree with an open WIP PR after verified delivery. Record it in task tracking and resume this follow-up at continuation/closure.

Claim automatic post-merge disappearance only with a configured, verified mechanism. This procedure creates neither watcher nor scheduled task; the agent acts at lifecycle transitions. Propose separate follow-up when nobody will resume the task. Finish safe authorized work that is possible now. Freed-storage claims require an actual measurement.
