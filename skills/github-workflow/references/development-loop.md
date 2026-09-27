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
- **Static quality**: use the project's actual format/lint/type/build commands. A targeted test does not replace a required gate. Broader suites need impact or an existing requirement; repeat only for a concrete reason.
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

| Available role | Use |
|---|---|
| `explorer` | Map callers/tests and gather evidence for a bounded question. |
| `operator` | Execute deterministic operations with established actions and criteria. |
| `worker` | Implement an independent slice and its contracts/tests. |
| `reviewer` | Find defects, regressions and missing validation from fresh context. |
| `architect` | Resolve design uncertainty, cross-component invariants or sensitive counter-analysis. |

Use roles/models actually exposed; associations may change. Light models suit precise exploration, while code and complex interactions need sufficient capability. Escalate when uncertainty/risk requires it without asking a nondeveloper to select the model. Keep global model settings intact for a single task.

Review scale: documentation-only and low-risk mechanical work use self-review/checks; ordinary changes use one fresh reviewer; large behavioral/security/data/autonomy/CI/hooks/gate changes use several passes plus a fresh skeptic, with no quota. Choose angles by risk. Initial reviewers get request/rules/diff without author reasoning or other findings; the skeptic confronts the final diff, validation, findings and fixes. Use native roles and `fork_turns="none"` only in Codex. Recheck significant deltas with the relevant reviewer. Keep one task's documentation corrections in one PR.

Reviews supplement tests and the parent's final check. Reuse agents for deltas in the same task instead of restarting a whole team for a covered adjustment. Lightweight scores/critiques may guide exploration; they do not decide readiness.

## Continuity and reporting

Leave enough written reasoning for another agent to continue and challenge the work without private chat history. Use issue comments for material discoveries, decisions, ownership and handoffs. Use PR comments/reviews for revision-specific concerns, affected code and expected proof; answer every material finding with a fix or an evidence-backed disposition, then recheck significant changes. Record the actual agent/session and revision; a shared account is not proof of independent review or another session's consent.

Put concise comments beside non-obvious code intent, invariants, tradeoffs or constraints, and lasting design decisions in existing project documentation. Keep transient progress in issues/PRs; avoid comments that merely narrate the code or a new documentation system without a present need. A resolved review thread should leave its fix or disposition and verification visible.

After two attempts on one hypothesis without new evidence, diagnose and switch approach. Repair technical failures; wait for started checks; after about 120 seconds without a runner apply the [local gate](ci-local-gate.md); bring a prepared decision to a genuine authorization boundary.

Conversation uses the user's personal reporting format with only useful sections. Put progress and the next action together. Record a project-wide explicit hold in project AGENTS.md outside the managed block with date/scope. Repository/PR reports use **Done / Next step / Required input**, without personal identifiers. Explain outcomes/proof and put detailed, sanitized logs in the PR. Turn limits into actions with owners/conditions. Finish necessary authorized work before reporting; request only a real decision or indispensable manual action with a recommendation.
