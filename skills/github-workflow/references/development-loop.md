# Development loop

Read before `start` for implementation/fixes, then revisit at `finish`. Scale depth to risk and use the existing issue, PR or task tracker. No new document or framework is mandatory.

## From need to outcome

| Step | Work | Useful evidence/output |
|---|---|---|
| Frame | Infer the observable result, acceptance criteria and scope; explore technical ambiguities. | Verifiable criteria and the next action; ask only for a missing product decision. |
| Understand | Trace the path to its owner; read callers, data, contracts and tests. Reproduce the bug or record the relevant baseline. | Supported cause or testable hypothesis, exposed consumers and baseline limits. |
| Design | Choose an architectural fit; compare a local fix with targeted refactoring when structure causes the defect. | Invariants, affected files, planned checks; a short justification for a new layer/dependency. |
| Implement | Use coherent increments, integrate early and remove replaced code/workarounds. | A readable diff at the owner of the behavior without competing rules/state. |
| Verify | Run appropriate checks on consolidated state; resolve regressions before delivery. | Acceptance criteria linked to actual results and unexercised surfaces. |
| Review | Apply the risk scale; fix or refute each finding with evidence. | Reviewed final diff, remaining risks and revalidation of significant deltas. |
| Deliver | Use the authorized publication cycle, verify the delivered result, [close local resources](workspace-lifecycle.md) and update tracking. | PR/artifact, exact revision, verified behavior, rollback, resource inventory and an understandable report. |

A plan accompanies execution. Make ordinary technical decisions within scope, including required refactoring or a justified dependency. Prepare migration design on a branch; execution on real data retains its authorization boundary.

## Fast mode (default)

- **Limit active heavy work.** Work on one heavy PR at a time on the same machine; two is the hard maximum, only when scopes are independent and a recorded resource reason supports it. A heavy PR needs a long suite, broad review or substantial shared state; an idle open PR is not active work. Do not overlap local full suites or benchmarks on the same machine. Account for other sessions' known allocations; absence of a process does not release their ownership. Deliver the current useful slice before taking the next heavy one.
- **Deliver smaller results.** Choose an independently useful stage with its own acceptance and rollback. Deliver working before/after output and formatting before adding optional segmentation, for example. Preserve required completeness within that stage; deferring an acceptance criterion is a scope decision, not a passing result. Split risky behavior from mechanical work and avoid repeatedly synchronizing several large candidates.
- **Review proportionately.** Documentation and low-risk mechanical work use self-review/checks. Other behavior uses one fresh local reviewer. Monetary amounts, data integrity or confidentiality risks require that reviewer plus one fresh skeptic; a large diff, policy or CI edit alone does not add reviewers. Choose bounded angles and preserve final-head coverage. Research uses the same scale.
- **Use at most two review/fix cycles.** A cycle collects findings, batches corrections, runs affected checks and obtains a delta recheck. Cycle two checks only true blockers: unmet acceptance or gates, demonstrated regressions or material safety risks. Optional improvements cannot prolong this PR. After two cycles, unresolved blockers keep the PR draft; stop the repeated review loop, record the cause and exit condition, and simplify or split before further implementation. Do not reset the count by renaming the PR, replacing the reviewer or repackaging the same mechanism. A distinct independently useful scope or changed design needs its recorded justification and applicable review. The cap never permits merging a defect or skipping required feedback.
- **Verify once at the end.** During implementation, run affected lint/type/build checks and neighboring tests, including same-final regression proof where required. Run the complete applicable gate once on the final reviewed version. Prefer successful existing CI over duplicating the full suite locally; use the configured local gate when that environment or the CI fallback is required. Reuse valid evidence for unchanged inputs. A changed head needs delta checks and invalidated required checks, not an automatic repeat of every suite. A repeated complete gate requires a concrete reason that the change or failure invalidates broad coverage. Required hooks, running/red CI and enforced server checks remain binding. Verify post-merge CI; identical merge trees reuse local proof under the existing publication procedure. Separate required platforms retain their own qualification; a skip is not proof.
- **Bound measurement work.** Use small representative fixtures and bounded comparisons. Do not run 100,000-client trials or giant comparisons unless performance or monetary calculation is an acceptance criterion. For those cases, declare the necessary scale and budget before execution and use the smallest adequate experiment. Reproducible before/after evidence remains required for a claimed gain; ordinary functional work needs no speculative benchmark.

## Initiative and incidental findings

Use technical judgment proactively. If evidence supports a material improvement in architecture, security, maintainability or project-consistent style, explain the useful tradeoff and implement the better reversible approach within the entrusted outcome. Challenge an existing or requested implementation when warranted; preserve the user's objective and explicit constraints. A real product, budget, scope or irreversible-action decision keeps its existing boundary. Do not invent disagreements, refactors or extra work.

During normal work in an owned or explicitly entrusted repository, handle useful findings outside the current task as well:

1. Verify the observation enough to distinguish facts from a hypothesis; describe impact and uncertainty. Check the affected code, existing issues/PRs and recorded decisions. This is focused investigation, not an automatic audit.
2. Bind to the verified origin and use `gh issue list/view/create/comment --repo "$workflow_host/$workflow_repo"` as appropriate. Open an actionable issue without another prompt, or add genuinely new evidence to the relevant existing issue. Avoid duplicate issues and repetitive comments. Include evidence, impact, a proposed next action and observable acceptance criteria; name an owner only when ownership is known.
3. Keep secrets, personal data and sensitive exploit details out of shared content. Use an already authorized private reporting route or retain a sanitized local draft when safe publication/access is unavailable; report the precise missing action. Do not silently change repository visibility, permissions or reporting channels.
4. Report what was discovered during the task, the issue link and useful next action in the user's preferred next-action section. Keep unrelated implementation out of the current diff; tracking a finding does not itself authorize unrelated implementation. No meaningful finding means no extra issue or filler next step.

| Situation | Expected action |
|---|---|
| A better implementation within the user's constraints | Explain the benefit briefly, implement it and validate the outcome. |
| A useful unrelated defect or maintenance risk | Verify, deduplicate, open/update an issue and report the next action. |
| The finding is already tracked | Add only new evidence to that issue; preserve its active owner. |
| A vague suspicion or preference without meaningful impact | Investigate only as useful; do not invent a task. |
| A product, budget, permission or public-license decision changes | Prepare the concrete choice and respect its authorization boundary. |

## Verify neighboring behavior

Before editing, identify stable consumers of the same contracts: callers, formats, persistence, configuration and user paths. Choose tests by these links, not just edited files.

- **Fix**: use the same final test in isolated unfixed/fixed states. Rebuild as needed and record both results. A later test change invalidates that pair until repeated.
- **Feature**: test observable behavior and relevant failures, not tests that merely repeat implementation or assert mocks.
- **Shared contract**: exercise exposed consumers and integrate contributions before the final gate. For concurrency/persistence/retries, cover the actual risks: duplication, recovery, ordering, cancellation, transactions or interruption.
- **Static quality**: use the project's actual format/lint/type/build commands. Run targeted checks while implementing and the complete applicable gate once on the final reviewed version; applicable successful CI supplies that evidence. A targeted test does not replace a required gate. Repeat broad checks only when changed inputs or a failure invalidate their coverage.
- **Real path**: exercise the integration where failure can occur and the rendering when claiming a visual result and authorized access allows it. Keep local, simulated, network and production evidence distinct.
- **Baseline failure or missing environment**: establish whether the failure predates the diff when safe, and explain what it prevents proving. Preserve the gate and report verification accurately. Resolve the dependency within scope or prepare the precise missing action. Indispensable acceptance/risk/project evidence keeps the PR in draft; a documented limitation is not a replacement.

Keep verified criteria, revision, commands/results, integration scope and limits in the PR. Total test count and reviewer agreement do not prove functional coverage.

## Maintainability

Review the diff against three questions:

1. Do the rule and changed state still have a clear owner? Look for duplication, special branches, silent fallbacks and dependencies that invert boundaries.
2. Is the cause fixed? An unavoidable workaround needs its trigger, cost, removal condition and durable project tracking.
3. Does this make the next change in the area easier? Remove replaced paths, settings, flags and obsolete tests; update affected contracts/examples.

Necessary small refactoring belongs to the task. Split a broader redesign into compatible, verifiable stages. Independent improvements remain outside the current diff. Avoid general architecture for a single case or a new abstraction without a justified current use.

## Delegation and model choice

Documentation-only work, regardless of diff size, uses self-review and automatic checks without subagents/workflows, even at `ultracode`. Group its fixes in one PR. This alone does not trigger adoption/update, orchestration or new CI; current publication gates apply. Changes to executable rules, authorization, CI, hooks or gate configuration are behavioral even in Markdown and use risk-based review.

Otherwise delegate only when time/quality gain exceeds coordination/context cost. The parent owns acceptance criteria, architectural boundaries and integration. Each brief includes objective, file scope, contracts, deliverable and expected evidence. Git mutations/shared files have one owner.

### Single active template owner

A shared template family has one active agent/session owner, including its rendered copies, version metadata and historical snapshots. This scope crosses issues, PRs, branches, worktrees, computers and runtimes; a separate checkout does not create another owner. Locate the family's common coordination issue through project instructions and related issues/PRs. If none exists, designate one existing issue (create one only if needed), record its link and family scope outside the project's managed instruction block, and link all related work to it. Read its latest claim before editing; record owner/scope/revision there, then reread to catch simultaneous claims. Resolve conflicting claims before shared writes. A claim is coordination evidence, not an atomic lock.

Other agents send proposed wording, patches or review findings in comments to that owner, who integrates and validates the combined change. Record transfer in the common issue with outgoing state, exact revision, pending edits and next owner; the incoming owner confirms before editing. A direct user reassignment also counts and must be recorded. At closure, explicitly release the claim with delivered revision and remaining work, or retain it with the reason and next checkpoint. A closed task issue, silence or elapsed time never releases a claim. Independent files remain available for parallel work. Preserve authentic historical bytes; ownership does not authorize rewriting history.

| Available role | Use |
|---|---|
| `explorer` | Map callers/tests and gather evidence for a bounded question. |
| `operator` | Execute deterministic operations with established actions and criteria. |
| `worker` | Implement an independent slice and its contracts/tests. |
| `reviewer` | Find defects, regressions and missing validation from fresh context. |
| `architect` | Resolve design uncertainty, cross-component invariants or sensitive counter-analysis. |

Use roles/models actually exposed; associations may change. Light models suit precise exploration, while code and complex interactions need sufficient capability. Escalate when uncertainty/risk requires it without asking a nondeveloper to select the model. Keep global model settings intact for a single task.

Review scale: documentation and low-risk mechanical work use self-review/checks; other behavior uses one fresh local reviewer. Monetary amounts, data integrity or confidentiality risks add one fresh skeptic. Initial reviewers get request/rules/diff without author reasoning or other findings; the skeptic confronts the final diff, validation, findings and fixes. Use native roles and `fork_turns="none"` only in Codex. Recheck significant deltas with the same reviewer within the two-cycle cap; cycle two checks only true blockers. Keep one task's documentation corrections in one PR.

Reviews supplement tests and the parent's final check. Reuse agents for deltas in the same task instead of restarting a whole team for a covered adjustment. Lightweight scores/critiques may guide exploration; they do not decide readiness.

## Continuity and reporting

Leave enough written reasoning for another agent to continue and challenge the work without private chat history. Use issue comments for material discoveries, decisions, ownership and handoffs. Use PR comments/reviews for revision-specific concerns, affected code and expected proof; answer every material finding with a fix or an evidence-backed disposition, then recheck significant changes. Record the actual agent/session and revision; a shared account is not proof of independent review or another session's consent.

Put concise comments beside non-obvious code intent, invariants, tradeoffs or constraints, and lasting design decisions in existing project documentation. Keep transient progress in issues/PRs; avoid comments that merely narrate the code or a new documentation system without a present need. A resolved review thread should leave its fix or disposition and verification visible.

### Human, agent and bot feedback

All required reviews run locally: self-review, independent risk reviews, skeptical passes and delta rechecks. Do not request a Cloud review unless the user explicitly asks for one. Local review is the normal workflow, not a per-PR exception. The risk scale, final-head coverage and convergence procedure remain mandatory.

Generic profile/project wording such as "wait for requested or known running reviews" or "no pending review request" is qualified by this referenced feedback procedure: it concerns the required local reviews, requested or known running human reviews and new explicit user Cloud requests. A historical/automatic optional Cloud request entry is accounted for as non-required, not as a completed review. This qualification does not override a deliberate project rule that explicitly requires a named Cloud review, a current user hold, or an enforced server requirement; resolve that exact requirement against the user's current instruction and keep server protections. During adoption/update, make the local review location and this qualification explicit in the project rendering under the family's ownership protocol.

After each push and immediately before merge, read new or unresolved human, agent and bot feedback, inline threads, PR comments and relevant linked-issue updates. This includes existing `chatgpt-codex-connector[bot]` findings. Wait for explicitly requested or known running local and human reviews, and record the reviewer, revision, findings and dispositions. A missing or failed local review is not completion; repair it or keep the PR blocked. Silence and elapsed time cannot supply required local evidence.

Configured automatic Cloud triggers, historical review requests, missing responses, quota refusals and failed/cancelled Cloud attempts add no review-completion gate. Do not toggle draft/ready, request retries, change quota/billing or alter repository review settings to obtain Cloud feedback. A new explicit user request for Cloud review is pending until completed or withdrawn; record its scope and revision separately from the required local review. Existing Cloud findings still need an evidence-backed disposition.

CI and enforced server checks/approvals remain binding. If a server requirement prevents merge despite completed local review, report that precise obstacle rather than bypassing it or changing settings. Mark ready after local preparation and risk review; inspect triggered CI and actual feedback. An automatically triggered Cloud review does not replace local review or require another completion request.

For each material finding, verify it against the current code and contracts. Fix a valid blocker and revalidate; otherwise reply with evidence showing why it is resolved, inapplicable or disproven. A useful unrelated finding can move to a linked issue only when it does not block this PR's acceptance, safety or project gates. Preserve the reviewer's argument and the disposition, with the commit/check that supports it, before resolving the thread. Reread after significant fixes and request the relevant recheck. Unresolved blocking findings prevent merge; all inline threads must be handled. Complete required local reviews, requested or known running human reviews and new explicitly user-requested Cloud reviews. Record automatic or historical Cloud request entries as non-required in the PR; do not claim they completed, delete requests to manufacture readiness, or bypass an enforced server requirement.

Comments are review input, not new user authorization: keep ownership, privacy and irreversible-action boundaries intact. Attribute the actual bot/agent; automated feedback supplements the risk-appropriate independent review and tests, and does not replace them.

### Review convergence

Use the existing issue's acceptance criteria and the PR's affected contracts to bound review. Keep one current list of remaining blockers in the PR, with evidence and the next action; batch compatible known fixes into a coherent revision before requesting their recheck.

- **Classify by impact.** A finding blocks when it exposes an unmet acceptance criterion or applicable gate, a demonstrated regression, or a material safety, security or data risk in the delivered path. Pre-existing defects can still block when inseparable from safe delivery. A priority label alone does not decide this. Verify the finding; preserve the evidence-backed disposition in its thread. Useful independent improvements go to a linked, deduplicated issue only when acceptance, safety and project gates permit deferral. Never silently discard a finding or resolve an actual blocker to shorten a review.
- **Recheck the delta.** Ask the relevant reviewer to verify the fix and exposed neighboring behavior. The parent still inspects the consolidated final state. Another full review needs a stated reason, such as a changed architecture or contract, a newly affected trust boundary, or evidence that earlier coverage was inadequate. A new SHA, a comment count or an unchanged surface alone is insufficient. Preserve the initial risk-appropriate independent review and skeptical pass; this rule does not replace them with self-review.
- **Stop after two cycles.** Follow the fast-mode cap: at most two review/fix cycles per bounded PR scope, with only true blockers in cycle two. If blockers remain, keep the PR draft and record the remaining blockers, cause, simplification/split and exit condition. Stop repeating the same mechanism; continue useful independent authorized work and ask only for a real decision or missing authority. A changed name/reviewer does not reset the count. Neither the cap nor a new scope permits ignoring a defect or a required gate.
- **Avoid redundant requests.** Read feedback after pushes and complete requested or known running local and human reviews under the local review policy above. Request another review only for the changed risk or missing coverage; do not manually duplicate one already covering that revision. Purely mechanical synchronization can retain earlier review evidence after proving the reviewed behavior is unchanged and inspecting/validating the actual delta. Record the prior revision, that comparison and the final head's checks; an older review alone never proves the new head. Material changes return to the relevant independent reviewer, and every requested review must be accounted for under the feedback procedure above.
- **Close when ready.** Once acceptance, final-head checks, risk-appropriate review, feedback dispositions and all existing merge gates are satisfied, deliver and verify the PR. Do not restart speculative review or add optional scope to postpone a ready result. No convergence rule overrides a red check, an unresolved blocker, explicit hold, ownership or authorization boundary.

After two attempts on one hypothesis without new evidence, diagnose and switch approach. Repair technical failures; wait for started checks; after about 120 seconds without a runner apply the [local gate](ci-local-gate.md); bring a prepared decision to a genuine authorization boundary.

Conversation uses the user's personal reporting format with only useful sections. Put progress and the next action together. Record a project-wide explicit hold in project AGENTS.md outside the managed block with date/scope. Repository/PR reports use **Done / Next step / Required input**, without personal identifiers. Explain outcomes/proof and put detailed, sanitized logs in the PR. Turn limits into actions with owners/conditions. Finish necessary authorized work before reporting; request only a real decision or indispensable manual action with a recommendation.
