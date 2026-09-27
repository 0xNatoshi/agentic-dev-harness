# Qualification and limitations

This repository maintains portable development instructions and build/check tooling. A successful build reads the archive back and verifies its entries and hashes; `tests/test_package.py` proves that two builds are byte-identical. A build does not install or run the profiles on a destination computer.

The local gate consists of `python3 scripts/check.py`, `python3 -m unittest discover -s tests -v` and `python3 scripts/build.py`. Tests use real disposable Git repositories and simulated gh responses. They exercise merge preflight routing/suspension/Pages behavior, attached/rebase/bisect branch guards, the documented license comparison and package integrity. Consult the CI run for the exact commit; static documentation is not proof that a future revision passed.

Exercised platforms and interpreters:

- CI: Linux (`ubuntu-latest`) with Python 3.11 only.
- Local runs: macOS with the Python recorded in each PR's evidence.
- The test suite uses Python 3.10+ syntax (for example `str | None` annotations); contributors need 3.11+, as the README states. Runtime guards claim Python 3.8+, but no run on 3.8 or 3.9 exists, so that compatibility is unverified.
- Windows: the suite has never run there. It creates symlinks (`tests/_fixture_support.py`, `tests/test_package.py`) and depends on the locale encoding; #12 owns a Windows-runnable gate.

The source gate (`scripts/check.py`) rejects home-directory paths, email addresses outside a neutral allowlist (reporting file and line only, #10) and three credential formats: GitHub tokens, `sk-` keys and PEM private keys. It does not detect personal names, other secret formats or other personal data; review still owns those.

The imported v6.3.1 distribution was reportedly checked on macOS, including native offline Codex/Hermes loading and Claude import/link resolution. Its receipts are excluded from the source repository (see its `docs/PROVENANCE.md`), so that claim is not reproducible from repository evidence, and it is not v6.5.0 destination installation evidence. The new common profile is neutral and omits the prior user's display name; destination preferences must be preserved during installation.

The v6.5.0 initiative/collaboration rules also need fresh-session loading checks. Source integrity and scenario reviews establish the distributed policy and its consistency, not every future model decision. Record current installation receipts separately from these historical facts.

Context budget (sizes on main 618f8bf): the repository template `skills/github-workflow/templates/AGENTS.md` is 32,242 bytes and the source repository's root AGENTS.md is 33,756 bytes. Codex's default `project_doc_max_bytes` is 32 KiB (32,768 bytes). A local codex-cli 0.158.0-alpha.2.1 `debug prompt-input` probe on d29d264 loaded exactly 32,768 bytes of the root AGENTS.md and omitted its final line; a command-line 65,536-byte override loaded it fully ([#15](https://github.com/0xNatoshi/agentic-dev-harness/issues/15#issuecomment-5856984060)). That is one CLI observation, not Windows, Claude, Hermes or desktop qualification, so full project-instruction loading is not qualified. The size threshold is an owner decision; #15 owns condensation.

Still requiring environment-specific verification:

- Windows/Git Bash behavior: #1 (destination qualification) and #12 (local gate).
- Directory replacement: no installer exists yet, and the prose replacement procedure has never been exercised by a test; #17 owns a tested installer and #1 its qualification.
- A new interactive Claude Code session actually loading the imported AGENTS.md: owning session, after installation.
- Runtime model/role availability and native worktree tools: owning application/session, before use.
- Live GitHub merges and app-managed archival: the session executing that operation, under the documented gates.

The unchanged Claude `argument-hint` skill extension is rejected by an older Codex skill-creator static validator. That validator is not part of this repository gate and is not represented as passing; actual runtime discovery must be checked during destination qualification (#1). Text-based suspension detection never replaces reading applicable instructions.

No repository build changes live profiles, providers, permissions, repository protections or other sessions' resources. No recurring cleanup or automatic release publication is configured.
