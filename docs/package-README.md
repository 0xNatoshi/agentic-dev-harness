# Development harness — v6.4.0

Versioned development workflow, agent profiles and safe delivery tooling for Codex, Claude Code and Hermes

## Install

Extract outside project repositories. Ask the agent in each targeted application to verify MANIFEST.json, read STATUS.md and follow its installation guide. Back up targeted instructions and skills, preserve existing preferences/authentic history, compare versions and verify actual loading in a new session. Install Codex only when present and targeted. Do not modify unrelated projects, providers, permissions or settings.

| Application | Guide and source |
|---|---|
| Claude Desktop local Code tab / Claude Code | [INSTALL-CLAUDE.md](INSTALL-CLAUDE.md), [Claude profile](configurations/claude-desktop/CLAUDE.md) and adjacent AGENTS.md |
| Codex Desktop/CLI | [INSTALL-CODEX.md](INSTALL-CODEX.md), [common profile](configurations/codex/AGENTS.md), [agent registration](configurations/codex/agents-config.toml) and five role files |
| Hermes | [Development adaptation](adaptations/hermes-development.md); replace only the development section after backup and preserve private SOUL/settings |

The [workflow skill](skills/github-workflow/SKILL.md) includes scripts, references, license templates and authentic project-template history. Runtime guards require Git, gh, Bash/Git Bash and Python 3.8+; no external jq is required. Conversation follows personal preferences; maintained files use English. Legacy suspension strings remain exact compatibility data.

## Version and recovery

The package is v6.4.0; the project template remains v6.3. Authentic template snapshots are retained, including instructions to preserve destination-only v5.1/v5.2 history. The common policy is neutral; merge personal preferences during installation instead of overwriting them.

MANIFEST.json hashes every payload except itself. The ZIP is reproducibly generated from the versioned source. Read [STATUS.md](STATUS.md) for actual scope and outstanding destination checks. The package contains no private backup, provider credentials or full Hermes SOUL.

Installation guides record backups under `%LOCALAPPDATA%\dev-harness\backups`. Restore only files still matching their installed hashes or merge later edits. The [root license](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md) remain applicable.

Maintain changes, reviews and handoffs in the private [source repository](https://github.com/0xNatoshi/dev-harness). Building this package does not publish a release or install a cleanup automation.
