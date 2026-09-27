# Waiting CI and the local gate

Use this shared procedure for discovery, `finish`, integration and post-merge checks. The user authorizes fallback after about two minutes without CI starting. This is an operational judgment for that run, not proof that the whole runner fleet is powered off.

## Decide from current state

| Observation on the relevant head | Action |
|---|---|
| All applicable checks succeeded, or skipped/neutral is justified by project rules | Continue through readiness criteria; an unjustifiably skipped required step proves nothing. |
| A check/job has started or is running | Wait for its conclusion without tight polling. Another queued job does not trigger global fallback. |
| Current failure/error/timed_out/startup failure | Block, diagnose and fix. A local gate cannot turn that failure into success. |
| Cancelled, expired/stale or action required | Neither green nor evidence of no runner starting; diagnose and resolve. |
| Explicit approval, environment protection, concurrency or dependency wait | Address that cause; it is not runner unavailability. |
| About 120 seconds elapsed; no check/job started, no runner assigned, successful reads reveal no other explicit wait | Treat runners as unavailable for this run and execute the configured local gate immediately. |
| Surface has no planned CI or is deliberately filtered | Use its intended local gate without claiming runner failure; separately fix an unintended missing trigger when necessary. |
| Unreadable/incomplete state, changed SHA or contradictory observations | Obtain valid evidence and restart on the current head. API errors are not absent checks. |

Time starts at the expected trigger for the current SHA (push, ready transition or run launch), not at chat start. Use observed timestamps; an already elapsed interval does not require another two minutes. Select the latest attempt of applicable runs and latest state per commit-status context; historical results do not establish current state.

## Collect evidence without changing CI

Read the PR head successfully, then its current checks, runs and jobs. Bind `workflow_host`/`workflow_repo` using the origin preflight and resolve `workflow_pr` through the skill's [PR-selector contract](../SKILL.md#select-one-pr-explicitly). Capture the current head with `workflow_sha=$(gh pr view --repo "$workflow_host/$workflow_repo" "$workflow_pr" --json headRefOid --jq .headRefOid) || exit 2`; require a nonempty commit SHA. Obtain actual run/attempt values from the returned runs/jobs, never from a placeholder or a different head. The Git Bash commands use gh's built-in `--jq`, no external jq and no `--slurp`. PowerShell can use native JSON tools with the same pagination/exit checks.

```bash
(
gh pr view --repo "$workflow_host/$workflow_repo" "$workflow_pr" --json headRefOid,isDraft,statusCheckRollup || exit 2
gh api --hostname "$workflow_host" --method GET "repos/$workflow_repo/commits/$workflow_sha/check-runs" -f filter=latest -F per_page=100 --paginate --jq 'if (.check_runs | type) == "array" and (.total_count | type) == "number" then {total_count, page_count:(.check_runs|length), items:[.check_runs[] | {name,status,conclusion,started_at,completed_at,app:.app.slug}]} else error("Invalid check-runs page") end' || exit 2
gh api --hostname "$workflow_host" --method GET "repos/$workflow_repo/actions/runs" -f head_sha="$workflow_sha" -F per_page=100 --paginate --jq 'if (.workflow_runs | type) == "array" and (.total_count | type) == "number" then {total_count, page_count:(.workflow_runs|length), items:[.workflow_runs[] | {id,workflow_id,run_attempt,head_sha,event,status,conclusion,created_at,run_started_at}]} else error("Invalid runs page") end' || exit 2
# For each applicable current run and attempt:
gh api --hostname "$workflow_host" --method GET "repos/$workflow_repo/actions/runs/$workflow_run/attempts/$workflow_attempt/jobs" -F per_page=100 --paginate --jq 'if (.jobs | type) == "array" and (.total_count | type) == "number" then {total_count, page_count:(.jobs|length), items:[.jobs[] | {name,status,conclusion,started_at,runner_id,runner_name,steps}]} else error("Invalid jobs page") end' || exit 2
gh api --hostname "$workflow_host" --method GET "repos/$workflow_repo/commits/$workflow_sha/statuses" -F per_page=100 --paginate --jq 'if type == "array" then {page_count:length, items:[.[] | {context,state,created_at,updated_at,target_url}]} else error("Invalid statuses page") end' || exit 2
gh pr checks --repo "$workflow_host/$workflow_repo" "$workflow_pr" --required --json name,state,bucket,startedAt,completedAt
)
```

Retain exit codes/stderr without publishing secrets. The parameterized API requests explicitly use GET. Each JSON object represents one page and its `page_count`, not an aggregated array; retain all pages. Empty output, absent/unknown required state or command failure invalidates the observation. For `total_count`, require consistency across pages and equality to the sum of page counts; otherwise reread truncated/moving state. For commit statuses, successful pagination must cover every link. Search endpoints may cap results (notably 1,000 filtered runs); refine/verify the run set rather than treating others as absent. `gh pr checks --repo "$workflow_host/$workflow_repo" "$workflow_pr"` is not pinned to a SHA: reread `headRefOid` afterward and discard changed-head observations. Pending is not a network error; distinguish the version's documented exit code and actual response.

Workflow `run_started_at` may be populated while queued. Inspect status, jobs/steps and runner identifiers together; an absent optional field is not availability evidence. External checks/pending commit statuses need the provider's state. Diagnose `waiting`/`pending`/`requested` before classification.

## Execute authorized fallback

1. Establish real validation commands from repository scripts/workflows/rules. Cover required risk/project surfaces (types, lint, build, tests, relevant integration); a faster, narrower suite cannot replace a required gate. An inaccessible indispensable environment keeps the PR in draft with the needed action.
2. Run the gate on the consolidated final-head tree. Attach fallback reason, SHA, timestamped observations, exact commands/results and coverage/limits in English without personal paths. Use `Validation: local gate passed; remote CI did not start`, not an unproved remote-CI claim.
3. Reread head, checks and protections immediately before integration. Revalidate a changed head. Wait for newly started checks; a red result blocks. Review, rollback, instruction restrictions and all other autonomy criteria still apply.
4. Use ordinary `gh pr merge --repo "$workflow_host/$workflow_repo" "$workflow_pr" --<method> --match-head-commit "$workflow_sha"`, with the project's configured merge method and the final verified SHA, without admin override, false checks, disabling workflows/removing required checks or weakening protection. If GitHub still requires an unstarted check, retain the ready PR, explain the obstacle and prepare the concrete runner/protection decision. This fallback does not alter server rights.
5. Verify publication and inspect the integrated commit's checks with the same procedure. When local validation applies and trees are identical, preserve equality evidence; otherwise rerun the gate on the integrated result. Resolve post-merge failures through fix/revert before removing diagnostic resources.

## Technical sources

- [Workflow runs](https://docs.github.com/en/rest/actions/workflow-runs), [jobs/attempts](https://docs.github.com/en/rest/actions/workflow-jobs), [check runs](https://docs.github.com/en/rest/checks/runs).
- [Check statuses](https://docs.github.com/en/pull-requests/reference/status-checks), [concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency), [environment protections](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/control-deployments).
- [gh api](https://cli.github.com/manual/gh_api), [checks](https://cli.github.com/manual/gh_pr_checks), [merge](https://cli.github.com/manual/gh_pr_merge).
