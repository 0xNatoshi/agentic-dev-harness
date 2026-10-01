# Qualification and limitations

This repository maintains portable development instructions and build/check tooling. A successful build reads the archive back and verifies its entries and hashes; `tests/test_package.py` proves that two builds are byte-identical. A build does not install or run the profiles on a destination computer.

The local gate consists of `python3 scripts/check.py`, `python3 -m unittest discover -s tests -v` and `python3 scripts/build.py`. Tests use real disposable Git repositories and simulated gh responses. They exercise merge preflight routing/suspension/Pages behavior, attached/rebase/bisect branch guards, the documented license comparison and package integrity. Consult the CI run for the exact commit; static documentation is not proof that a future revision passed.

Exercised platforms and interpreters:

- CI: Linux (`ubuntu-latest`) with Python 3.11 only.
- Local runs: macOS with the Python recorded in each PR's evidence.
- The test suite uses Python 3.10+ syntax (for example `str | None` annotations); contributors need 3.11+, as the README states. Runtime guards claim Python 3.8+, but no run on 3.8 or 3.9 exists, so that compatibility is unverified.
- Windows: local command-fixture and installer runs now exist, with both passing compositions and failed or skipped checks recorded in local proof artifacts. They do not qualify every branch or the destination profile. #12 owns command-fixture portability; native permissions, symlinks, open-handle behavior and exact-revision suite results need their own evidence. Preserve failed runs and platform skips when reporting a later pass.

The source gate (`scripts/check.py`) rejects home-directory paths, email addresses outside a neutral allowlist (reporting file and line only, #10) and three credential formats: GitHub tokens, `sk-` keys and PEM private keys. It does not detect personal names, other secret formats or other personal data; review still owns those.

The imported v6.3.1 distribution was reportedly checked on macOS, including native offline Codex/Hermes loading and Claude import/link resolution. Its receipts are excluded from the source repository (see its `docs/PROVENANCE.md`), so that claim is not reproducible from repository evidence, and it is not destination installation evidence for the current v6.6.0 development package. The new common profile is neutral and omits the prior user's display name; destination preferences must be preserved during installation.

Installer receipts cover the skill transaction and record instruction-file hashes; they do not prove that instruction or role merges loaded in an application. The five named v5.2 paths are retired by pathname, even when edited, while verified before-state copies preserve their bytes. Unknown customizations and authentic history are distinct from replaced package paths. The INSTALL guides describe plan review, maintenance, duplicate selection, recovery and exact rollback. Hash verification against supplied checksums establishes integrity, not independent package provenance.

The source remains an unreleased development version during coordinated work. #1 owns final export and destination qualification; an incremental source change does not itself publish a release, deliver a final distribution or update an installed profile.

The initiative/collaboration rules introduced in v6.5.0 also need fresh-session loading checks for the current v6.6.0 development package. Source integrity and scenario reviews establish the distributed policy and its consistency, not every future model decision. Record current installation receipts separately from these historical facts.

Context budget (sizes on main 618f8bf): the repository template `skills/github-workflow/templates/AGENTS.md` is 32,242 bytes and the source repository's root AGENTS.md is 33,756 bytes. Codex's default `project_doc_max_bytes` is 32 KiB (32,768 bytes). A local codex-cli 0.158.0-alpha.2.1 `debug prompt-input` probe on d29d264 loaded exactly 32,768 bytes of the root AGENTS.md and omitted its final line; a command-line 65,536-byte override loaded it fully ([#15](https://github.com/0xNatoshi/agentic-dev-harness/issues/15#issuecomment-5856984060)). That is one CLI observation, not Windows, Claude, Hermes or desktop qualification, so full project-instruction loading is not qualified. The size threshold is an owner decision; #15 owns condensation.

Still requiring environment-specific verification:

- Windows/Git Bash behavior: #1 (destination qualification) and #12 (local gate).
- Directory replacement: the package ships `install.py`; `tests/test_installer.py` invokes the extracted installer against disposable homes. Its tests cover package verification, inventory drift, staging/retired copies outside discovery roots, duplicate handling, fault-injected swaps, receipts, recovery and rollback. Read the exact-revision logs for executed cases and skips. Simulated process lists and injected rename failures do not prove a real destination's process probe, Windows open-handle behavior or native filesystem permissions. #17 owns the installer/guide contract; #41/#42/#43 track recovery, permission guidance and named-retirement follow-ups; #1 owns real installation and fresh-session qualification.
- A new interactive Claude Code session actually loading the imported AGENTS.md: owning session, after installation.
- Runtime model/role availability and native worktree tools: owning application/session, before use.
- Live GitHub merges and app-managed archival: the session executing that operation, under the documented gates.

The unchanged Claude `argument-hint` skill extension is rejected by an older Codex skill-creator static validator. That validator is not part of this repository gate and is not represented as passing; actual runtime discovery must be checked during destination qualification (#1). Text-based suspension detection never replaces reading applicable instructions.

No repository build changes live profiles, providers, permissions, repository protections or other sessions' resources. No recurring cleanup or automatic release publication is configured.
