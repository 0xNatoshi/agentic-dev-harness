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

After two attempts on one hypothesis without new evidence, diagnose and switch approach. Repair technical failures; wait for started checks; after about 120 seconds without a runner apply the [local gate](ci-local-gate.md); bring a prepared decision to a genuine authorization boundary.

Conversation uses the user's personal reporting format with only useful sections. Put progress and the next action together. Record a project-wide explicit hold in project AGENTS.md outside the managed block with date/scope. Repository/PR reports use **Done / Next step / Required input**, without personal identifiers. Explain outcomes/proof and put detailed, sanitized logs in the PR. Turn limits into actions with owners/conditions. Finish necessary authorized work before reporting; request only a real decision or indispensable manual action with a recommendation.
