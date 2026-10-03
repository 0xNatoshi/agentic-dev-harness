# Agentic development harness

Versioned development workflow, agent profiles and safe delivery tooling for Codex, Claude Code and Hermes

This private repository is the shared source for harness changes, review and handoff between coding agents. It started from the verified v6.3.1 distribution; v6.4.0 established repository maintenance and reproducible packaging. **v6.5.0** adds proactive technical initiative, written collaboration, review convergence, verified agent attribution and observed model/provider provenance. The current project template is v6.4.

## Work with another agent

1. Open an issue with the desired outcome, acceptance criteria and relevant evidence.
2. The implementing session records its ownership and branch in the issue. Check existing comments and linked PRs first.
3. Open a draft PR early, link the issue, and keep the evidence and decisions in that PR.
4. Another agent reviews the actual diff and posts actionable findings. The implementation owner fixes and validates it, then follows the ready-PR merge rules.
5. On handoff, record the exact revision, completed checks, remaining work and next owner. On delivery, verify the published result and account for temporary resources.

See [CONTRIBUTING.md](CONTRIBUTING.md). A GitHub assignee alone cannot identify two sessions using the same account. Read the recorded session owner; do not take over another active session's branch or worktree.

## Build an installation package

Contributors need **Python 3.11+, Git and Bash**. The build uses only the Python standard library. Runtime workflow guards separately require Python 3.8+, Git, gh and Bash/Git Bash.

```bash
git clone https://github.com/0xNatoshi/agentic-dev-harness.git
cd agentic-dev-harness
python3 scripts/check.py
python3 -m unittest discover -s tests -v
python3 scripts/build.py
```

The build writes a deterministic ZIP, SHA-256 manifest and checksum file to `dist/`. These generated files stay outside Git. Give the ZIP to the agent on the destination computer; the package includes separate Codex and Claude installation guides plus the Hermes adaptation. On Windows, use `py -3` for the Python build commands when needed; guard fixtures need a Bash-compatible environment and are not Windows runtime qualification.

## Source map

| Path | Responsibility |
|---|---|
| [configurations/](configurations/) | Shared policy source and per-target adaptations for Codex, Claude desktop and Hermes, without personal identifiers |
| [skills/github-workflow/](skills/github-workflow/) | Workflow, read-only guards, reference material, license/project templates and authentic history |
| [configurations/codex/](configurations/codex/) | Sanitized Codex role registration and all five role files |
| [docs/INSTALL-CODEX.md](docs/INSTALL-CODEX.md), [docs/INSTALL-CLAUDE.md](docs/INSTALL-CLAUDE.md) | Destination installation and verified rollback procedures |
| [scripts/](scripts/), [tests/](tests/) | Reproducible packaging, source checks and isolated behavioral fixtures |
| [AGENTS.md](AGENTS.md) | This repository's development instructions; Claude imports them through root CLAUDE.md |

The common profile has one source. Packaging generates the runtime-specific copies instead of maintaining duplicate files. [package-files.json](package-files.json) explicitly lists every exportable source and destination; undeclared files in configuration/skill/role directories block the build. Profile/template instructions are installable data; they do not replace this repository's root instructions. No installer runs automatically, and no live profile, provider or permission is changed by building the package.

The build rejects file/directory collisions after Unicode normalization, uppercase mapping and case folding, exports that overlap the generated manifest, and reserved path components from the [Windows naming conventions](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file). These conservative source checks do not emulate every filesystem or replace installation and extraction checks on the destination computer.

## Versions and evidence

- `VERSION` is the package version; [CHANGELOG.md](CHANGELOG.md) records changes.
- Tags identify published revisions. Never move a published tag or replace its assets; make a new version.
- Template snapshots remain authentic and immutable. Preserve destination v5.1/v5.2 captures; missing global snapshots are not invented.
- [Provenance](docs/PROVENANCE.md) records the imported archive hashes and exclusions.
- [Status](docs/STATUS.md) distinguishes automated checks from Windows and interactive application loading that require destination checks.

CI runs source checks, isolated tests and packaging on Linux for PRs and main. It does not deploy, publish releases or change repository settings. Local success is reported separately from CI success; existing protection and authorization gates remain binding.

## License

This repository uses the [proprietary development permission](LICENSE). Authorized contributors may access, modify, test and run it to develop this project. Other uses require the applicable authorization. Included downstream license templates retain their own text; see [third-party notices](THIRD_PARTY_NOTICES.md).
