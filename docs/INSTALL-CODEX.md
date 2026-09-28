# Personal Codex Desktop installation on Windows — v6.5.0

Use these steps from the extracted ZIP produced by `python3 scripts/build.py`. Paths below are relative to that package root, not this guide's source location in the repository.

Apply only when **Codex is installed and the user has targeted it on this computer**. Otherwise record that Codex is not targeted and leave its files intact. Install personal instructions/skill outside repositories; this does not run project adoption, integration or cleanup. First check MANIFEST.json version, every file hash and STATUS.md. Historical checks do not qualify this computer.

## Locate destinations and capabilities

In the Windows process running Codex, inspect effective paths without changing environment variables:

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$skillsHome = Join-Path $env:USERPROFILE '.agents\skills'
$codexHome
$skillsHome
```

Check Desktop version or `codex --version`, active config.toml, custom-role support, available models/efforts and any local/managed subagent disablement. No model call is needed for discovery. WSL has separate Linux paths; Windows installation does not configure it. A nonempty personal AGENTS.override.md takes precedence over AGENTS.md; establish which is loaded before merging. Project instructions remain applicable. [Instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md), [configuration](https://learn.chatgpt.com/docs/config-file/config-reference), [subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents).

The preflight requires Git, gh, Git Bash/Bash with awk/grep/mktemp and **Python 3.8+ standard library**. Verify a real working python3, py -3 or python, in that detection order. If absent, installation remains unqualified until Python is installed from the [official Windows distribution](https://www.python.org/downloads/windows/) and checked again. No external jq is needed.

## Update check — read-only, before any backup or edit

Run this first. It answers "is this computer already current, and what must change?" and modifies nothing. Record one outcome per checkpoint, using these four words:

- **current** — matches the package.
- **personalized** — matches, plus deliberate local additions or preferences.
- **outdated** — a package rule is absent, older or altered.
- **blocked** — unreadable, or not safely ownable.

Report the outcome per checkpoint; do not collapse them into one verdict.

1. **Package** — verify the extracted ZIP against `SHA256SUMS.txt` and every entry in `MANIFEST.json`, and record the package version. A mismatch is `blocked`, and no later checkpoint is trustworthy.
2. **Active instruction file** — establish which file Codex loads: a nonempty `$codexHome\AGENTS.override.md` takes precedence over `$codexHome\AGENTS.md`. Compare the loaded one with `configurations/codex/AGENTS.md`; compare the masked one only to keep the pair consistent. Merge into the loaded file, never into a neighbor copy.
3. **`github-workflow` skill** — resolve `$skillsHome\github-workflow`. A real directory is compared file by file with `skills/github-workflow/`. A link or junction is **not** replaced: resolve its canonical directory, record the link target and its owner, and compare there. One canonical directory can serve several applications at once and is then checked once for all of them. Also resolve `$codexHome\skills\github-workflow` as a possible older separate copy. Evidence is the file count, every SHA-256 and the three license templates.
4. **Roles and configuration** — compare the `[agents]` keys in the active `config.toml` with `configurations/codex/agents-config.toml`, and the five role TOMLs with `configurations/codex/agents/`. Preserve other tables, providers, permissions and deliberate disablement. An unavailable model or effort is `personalized`, not `outdated`.
5. **Loading** — in a new read-only Codex session, confirm the loaded instruction source, the skill and the roles actually exposed. File presence or TOML syntax does not prove loading.

When every checkpoint is `current`, the installation already matches the package: report that, change nothing and skip the remaining sections. Otherwise the `personalized` and `outdated` checkpoints are the exact edit list for those sections, and the backup below covers exactly those targets.

## Back up and compare versions

Before editing, create a new private `%LOCALAPPDATA%\dev-harness\backups\v6.5.0-codex-<timestamp>` directory and record its absolute path:

```powershell
$backupParent = Join-Path $env:LOCALAPPDATA 'dev-harness\backups'
New-Item -ItemType Directory -Path $backupParent -Force -ErrorAction Stop | Out-Null
$backupRoot = Join-Path $backupParent ('v6.5.0-codex-' + (Get-Date -Format 'yyyyMMdd-HHmmssfff'))
if (Test-Path -LiteralPath $backupRoot) { throw 'Backup path already exists' }
New-Item -ItemType Directory -Path $backupRoot -ErrorAction Stop | Out-Null
$backupRoot
```

Copy every file/link that will change, preserving type/tree: active instructions, adjacent AGENTS.md, config.toml, targeted role files and the complete skill/history. Inventory paths/types/link targets/SHA-256/file counts and verify the backup by readback. The local receipt records absolute paths, before/after hashes, recognized provenance/version, created/retired files and merge diffs. Keep authentication, secrets and unrelated profile content out of the export. Preserve earlier backups.

The **personal package is v6.5.0; the active repository template is v6.4**. This package includes initiative, written collaboration, review convergence, verified agent attribution and observed model/provider provenance in the shared profiles/skill; project adoption remains a separate PR after its current task. Compare marker and actual bytes against authentic sources; a version number alone is insufficient. Preserve newer/unknown local policy and merge only compatible additions. For v6.4.0 or older with a verified base, use a three-way comparison: exact historical base / local file / package file, individually for instructions, skill and roles. Preserve local customizations, restrictions and imports. Without an authentic base, capture local files and compare manually; invent no history. Keep authentic local v5.1/v5.2 snapshots, which are not shipped here.

## Install targeted files

1. Merge configurations/codex/AGENTS.md into the actual active personal instruction file. If an override masks AGENTS.md, merge there without deleting it or assuming a neighbor copy will load. Resolve authorization conflicts using current user direction or a concrete decision.
2. Merge the complete skills/github-workflow contents into `$skillsHome\github-workflow\`: all three scripts, references, templates, licenses, history and metadata. Inventory before/after and handle proven obsolete installer-owned files from the prior receipt without leaving an active stale copy. Preserve authentic history/customizations; avoid blind directory replacement. Verify the actual discovered skill path.
3. Only if the runtime supports custom roles and the supplied models/efforts, merge the `[agents]` keys from configurations/codex/agents-config.toml into active config.toml and the five explorer/worker/reviewer/architect/operator TOMLs into `$codexHome\agents\`. The fragment contains source descriptions, relative paths, concurrency/default settings; role files contain model/effort/permission/instruction layers. It is not a complete config.toml. Preserve other tables, providers, permissions, authentication and deliberate local differences, including subagent disablement. If a model/effort is unavailable, preserve the existing model and report the affected role rather than changing provider or selecting an unauthorized substitute.

The fragment's `[agents.<name>].config_file` refers to each role layer. Keep relative paths consistent. The source configuration does not require adding a features flag: verify installed-version defaults and managed settings before any activation, and preserve deliberate disablement. Effective child permissions also depend on the parent session. [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference), [roles/inheritance](https://learn.chatgpt.com/docs/agent-configuration/subagents).

## Named inventory before retiring files

| Exact destination path | Treatment |
|---|---|
| `$codexHome\AGENTS.override.md`, `$codexHome\AGENTS.md` | Preserve/merge active or adjacent instructions; no automatic removal. |
| `$skillsHome\github-workflow\` | Target skill; inventory/preserve/merge. |
| `$codexHome\agents\architect.toml` | Preserve/merge targeted role. |
| `$codexHome\agents\explorer.toml` | Preserve/merge targeted role. |
| `$codexHome\agents\operator.toml` | Preserve/merge targeted role. |
| `$codexHome\agents\reviewer.toml` | Preserve/merge targeted role. |
| `$codexHome\agents\worker.toml` | Preserve/merge targeted role. |
| `$codexHome\skills\github-workflow\` | Possible older separate copy. Retire only with prior installer ownership, complete unchanged inventory and hashes for every file/link, verified backup and proof no session loads it. Otherwise retain. |

Record any other obsolete candidate by exact path in the receipt. No wildcard deletion, recursive user-directory deletion, removal of an active link or loss of authentic history. Unknown ownership/use means preserve and name the owner/reevaluation trigger.

## Verify and roll back

- Check package/copy hashes or intentional merge diffs, TOML syntax, five relative role references, instruction imports and three license templates. In a new read-only Codex session confirm loaded sources, skill and actually exposed roles. File presence alone does not prove loading. State an unavailable/unstarted runtime explicitly.
- Conversation headings follow the actually loaded personal preference. Put verified progress and the next action together. Record executed checks, runtime version, exposed roles and exact backup path.
- Compare each target with its installed hash before rollback. Restore a backup/reverse a diff only without losing later edits; otherwise merge. Remove a newly created file only if unchanged and unused. Recheck instruction/skill/TOML loading. Restoring an entire config.toml blindly is not a rollback plan.
