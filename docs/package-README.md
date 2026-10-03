# Agentic development harness — v6.5.0

Versioned development workflow, agent profiles and safe delivery tooling for Codex, Claude Code and Hermes

## Install

Extract outside project repositories. Ask the agent in each targeted application to verify MANIFEST.json, read STATUS.md and follow its installation guide. Back up targeted instructions and skills, preserve existing preferences/authentic history, compare versions and verify actual loading in a new session. Install Codex only when present and targeted. Do not modify unrelated projects, providers, permissions or settings.

| Application | Guide and source |
|---|---|
| Claude Desktop local Code tab / Claude Code | [INSTALL-CLAUDE.md](INSTALL-CLAUDE.md), [Claude profile](configurations/claude-desktop/CLAUDE.md) and adjacent AGENTS.md |
| Codex Desktop/CLI | [INSTALL-CODEX.md](INSTALL-CODEX.md), [common profile](configurations/codex/AGENTS.md), [agent registration](configurations/codex/agents-config.toml) and five role files |
| Hermes | [Hermes adaptation](configurations/hermes/development.md); replace only the development section after backup and preserve private SOUL/settings |

The [workflow skill](skills/github-workflow/SKILL.md) includes scripts, references, license templates and authentic project-template history. Runtime guards require Git, gh, Bash/Git Bash and Python 3.8+; no external jq is required. Conversation follows personal preferences; maintained files use English. Legacy suspension strings remain exact compatibility data.

### Update check — read-only, before any backup or edit

Ask the agent in each targeted application to run this first. It answers "is this computer already current, and what must change?" and modifies nothing. Record one outcome per checkpoint: **current** (matches the package), **personalized** (matches, plus deliberate local additions), **outdated** (a package rule is absent, older or altered) or **blocked** (unreadable, or not safely ownable). Report the outcome per checkpoint.

1. **Package** — the archive verifies against `SHA256SUMS.txt` and every entry in `MANIFEST.json`; record the version. A mismatch is `blocked`, and no later checkpoint is trustworthy.
2. **The instruction file the application loads** — Claude Desktop: `CLAUDE.md` plus the adjacent `AGENTS.md` it imports; Codex: the active `AGENTS.override.md`, or `AGENTS.md` when the override is empty; Hermes: the development section of the private `SOUL.md`.
3. **`github-workflow` skill** — resolve the path actually loaded and compare it file by file. A link or junction is **not** replaced: compare its canonical directory instead. One canonical directory can serve several applications at once and is then checked once for all of them.
4. **Runtime extras** — Codex `[agents]` keys, role TOMLs and `config.toml`; Claude imports and `/github-workflow`; Hermes settings and SOUL sections outside the development section.
5. **Loading** — a new session in each targeted application. File presence does not prove loading.

When every checkpoint is `current` or `personalized`, no package update is needed for that application: report any deliberate local differences and change nothing there. Only `outdated` checkpoints become update candidates; resolve a `blocked` checkpoint before changing its target, and stop the whole update if package verification is blocked. [INSTALL-CLAUDE.md](INSTALL-CLAUDE.md) and [INSTALL-CODEX.md](INSTALL-CODEX.md) state the per-checkpoint evidence, destinations and rollback for their application.

## Version and recovery

The package is v6.5.0; the project template is v6.4. Package policy includes proactive technical judgment, incidental issue capture, written collaboration, review convergence, verified agent attribution and observed model/provider provenance in the shared configuration and skill files. Authentic template snapshots are retained, including instructions to preserve destination-only v5.1/v5.2 history. The common policy is neutral; merge personal preferences during installation instead of overwriting them.

MANIFEST.json hashes every payload except itself. The ZIP is reproducibly generated from the versioned source. Read [STATUS.md](STATUS.md) for actual scope and outstanding destination checks. The package contains no private backup, provider credentials or full Hermes SOUL.

Installation guides record backups under `%LOCALAPPDATA%\dev-harness\backups`. Restore only files still matching their installed hashes or merge later edits. The [root license](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md) remain applicable.

Maintain changes, reviews and handoffs in the private [source repository](https://github.com/0xNatoshi/agentic-dev-harness). Building this package does not publish a release or install a cleanup automation.
