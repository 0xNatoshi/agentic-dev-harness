# Contributing and agent handoff

Read [AGENTS.md](AGENTS.md) before Git operations. Use English in repository content, commits, issues, PRs and comments. Conversation language remains a personal preference. Keep private machine paths, personal names, personal email, credentials and runtime settings out of GitHub.

## One owner per implementation

Use an issue for a substantial change. Before starting, read its latest comments, linked/open PRs and matching branches. Record a short claim with agent/application, a neutral session identifier, branch, intended scope and next checkpoint. A claim is coordination evidence, not an atomic lock: reread after posting; if claims conflict, resolve ownership before writing shared code. The same account's assignee cannot distinguish its active sessions.

Do not expire a claim solely because time passed. The current owner hands off explicitly or the user entrusts the work. Independent subtasks get separate file ownership. Reviewers post findings against the PR revision and do not commit to the implementer's branch unless entrusted. Never remove another session's worktree, overwrite its files or close its PR to free resources.

The shared template family covers `profiles/`, `skills/github-workflow/templates/`, rendered copies and template version metadata. It has one active owner across issues, PRs, branches, worktrees, computers and runtimes. Use the common coordination issue identified in AGENTS.md, link every related change there and reread its latest claim before edits and after claiming. Keep the claim's agent/session, scope and revision current; a task issue is not a separate template claim. Send proposals to the owner in comments. Record explicit handoffs/releases with current revision, pending work and successor; the incoming owner confirms before editing. Record any direct user reassignment. Preserve authentic history and resolve conflicting claims before shared writes.

Issue, PR and review updates are part of an explicitly entrusted repository task when the user's authorization covers that collaboration. This guide does not authorize unrelated outreach, messages on the user's behalf or work in other repositories.

## Useful initiative and written reasoning

Improve technical choices within the entrusted outcome when evidence supports a material benefit, respecting the user's objective and explicit constraints. During normal work, verify and deduplicate useful incidental findings, then open or update an issue without another prompt. Record evidence, impact, uncertainty, a useful next action and acceptance criteria; report the link in the user's preferred next-action section. Preserve active ownership. Keep unrelated implementation in a later scoped task, and do not manufacture work or issue noise.

Use issue comments for material discoveries, decisions and handoffs. In PR comments/reviews, identify the actual agent/session and revision, explain objections and expected proof, and leave each finding's fix or evidence-backed disposition visible before resolving it. Put durable non-obvious intent and constraints beside the code; keep status chatter in issues/PRs and broader lasting decisions in existing project documentation. Another agent should be able to continue and challenge the reasoning without private chat history. Sanitize shared content; use an already authorized private route or a sanitized local draft when safe publication/access is unavailable.

## Evidence and review

For a substantive change, link the issue with `Closes #<number>` or `Refs #<number>`, write observable acceptance criteria, and open a draft PR at the first branch push. Include the actual commands/results, exact head, meaningful environment limits and rollback. Use the visible `Independent review` line in the PR template and complete it before marking ready.

Documentation-only work uses self-review and appropriate automatic checks. Executable policy, authorization, CI/hooks or gate changes receive the risk-based review required by AGENTS.md. Findings need a fix or an evidence-backed disposition; significant deltas return to the relevant reviewer. Tests exercise the current source in disposable repositories and never make real GitHub writes.

Include automated feedback such as `chatgpt-codex-connector[bot]`. After pushes and before merge, inspect new/unresolved reviews, inline threads, PR comments and relevant issue updates. Wait for explicitly requested or known running reviews. Verify material findings, leave the fix or evidence-backed disposition visible, and recheck significant deltas before resolving threads. Informational status notices need no ritual reply. An unrelated finding moves to an issue only when it does not block acceptance, safety or project gates. Bot comments neither grant authorization nor replace the independent review; follow the skill's feedback procedure.

Run the local gate before delivery:

```bash
python3 scripts/check.py
python3 -m unittest discover -s tests -v
python3 scripts/build.py
```

Source development requires Python 3.11+; on Windows use `py -3` instead of `python3` after checking its version. The distributed workflow helpers require Python 3.8+ and use the working-interpreter probes documented in the skill. A present but failing Windows Store alias is not a usable interpreter.

Test fixtures run Bash through one resolved path: `HARNESS_BASH` if set and non-empty, otherwise `bash` on `PATH`. On Windows, set `HARNESS_BASH` to Git Bash's `bash.exe` as a Windows path when the WSL launcher would be found first, for example `export HARNESS_BASH="$(cygpath -w "$(command -v bash)")"` from Git Bash; native Python cannot start a POSIX path such as `/usr/bin/bash`. The tests refuse the WSL launcher. Fixtures need no symlink privileges; tests that exercise symlink handling are skipped with a stated reason when symlinks cannot be created.

A running CI check must finish; a failing check blocks. Use the documented local fallback only after establishing the eligible unstarted-run condition. Never lower protections or fabricate a CI result. A ready entrusted PR satisfying all gates is merged and verified without another ritual go. Explicit holds and other irreversible boundaries retain their effect.

## Handoff comment

Record these facts in the issue or PR so another computer can resume without chat history:

- Current owner and explicitly designated next owner/session.
- Branch, PR and exact commit SHA; whether the checkout has unpublished work.
- Acceptance criteria completed and checks actually executed, with results.
- Remaining work, known limits and the immediate next action.
- Open review findings or decisions, plus the reason for any real hold.
- Resources retained, their owner and reevaluation trigger. Keep sensitive local recovery paths in private task tracking.

Progress notes include the next action. Record an explicit project-wide hold in root AGENTS.md outside its managed block as well as the issue/PR; a comment alone may be missed by other sessions.

Installer physical-path tests also run under native Windows Python in CI, using directory junctions without symlink privileges. Duplicate plans and receipts bind actual parent directory identities: crash-loop fixtures must create a fresh plan or receipt after rebuilding a temporary home, rather than reuse metadata from a copied tree. Test traces bind a validated temporary parent and one append handle; existing trace files must have one hard link and cannot be symlinks. POSIX FIFOs remain supported and open lazily at the first checkpoint so staging-pause fixtures keep their ordering.

## Versions, packages and recovery

1. Change canonical source files and tests; preserve authentic historical templates and notices. New exported files require an explicit entry in package-files.json; private configuration never belongs in that list.
2. Update `VERSION`, the changelog and versioned installation references for a new package. A changed repository template gets a new authentic snapshot and index entry; packaging-only changes do not require a project-template migration.
3. Run the gate on the final revision and inspect the generated ZIP/manifest. The build neither reads live home-directory configuration nor publishes anything.
4. Deliver through a reviewed PR, then verify main. An explicitly requested initial empty-repository bootstrap is the one-time exception.
5. Create a tag or release only within the user's publishing authorization. Never move published tags or overwrite release assets. No automated release publisher is installed.

Rollback a repository change through a revert PR and the same gates. Keep previous version tags. For installed profiles, use the destination guide's verified backups and compare installed hashes before restoring; preserve later changes. The repository contains no private local backups or full Hermes SOUL.
