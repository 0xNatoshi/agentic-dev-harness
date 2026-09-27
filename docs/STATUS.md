# Qualification and limitations

This repository maintains portable development instructions and build/check tooling. A successful build verifies deterministic archive contents and their hashes; it does not install or run the profiles on a destination computer.

The local gate consists of `python3 scripts/check.py`, `python3 -m unittest discover -s tests -v` and `python3 scripts/build.py`. Tests use real disposable Git repositories and simulated gh responses. They exercise merge preflight routing/suspension/Pages behavior, attached/rebase/bisect branch guards and package integrity. CI runs the gate on Linux. Consult the run for the exact commit; static documentation is not proof that a future revision passed.

The imported v6.3.1 distribution was checked on macOS, including native offline Codex/Hermes loading and Claude import/link resolution. Those historical checks are not v6.5.0 destination installation evidence. The new common profile is neutral and omits the prior user's display name; destination preferences must be preserved during installation.

The v6.5.0 initiative/collaboration rules also need fresh-session loading checks. Source integrity and scenario reviews establish the distributed policy and its consistency, not every future model decision. Record current installation receipts separately from these historical facts.

Still requiring environment-specific verification:

- Windows/Git Bash behavior and directory replacement: destination installer, during installation.
- A new interactive Claude Code session actually loading the imported AGENTS.md: owning session, after installation.
- Runtime model/role availability and native worktree tools: owning application/session, before use.
- Live GitHub merges and app-managed archival: the session executing that operation, under the documented gates.

The unchanged Claude `argument-hint` skill extension is rejected by an older Codex skill-creator static validator. That validator is not part of this repository gate and is not represented as passing; actual runtime discovery must be checked by the destination installer. Text-based suspension detection never replaces reading applicable instructions.

No repository build changes live profiles, providers, permissions, repository protections or other sessions' resources. No recurring cleanup or automatic release publication is configured.
