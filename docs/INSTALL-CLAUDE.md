# Personal Claude Desktop Code installation on Windows — v6.5.0

Use these steps from the extracted ZIP produced by `python3 scripts/build.py`. Paths below are relative to that package root, not this guide's source location in the repository.

Target Claude Desktop's **local Code tab**, and local Claude Code using that profile. Before editing verify package version/all hashes in MANIFEST.json and read STATUS.md. Historical evidence does not qualify the destination. Personal installation stays outside repositories and does not change projects, integrate PRs or run global cleanup.

Chat/Cowork, cloud, SSH and WSL sessions are not assumed to load these same files. [Desktop Code](https://code.claude.com/docs/en/desktop), [instructions](https://code.claude.com/docs/en/memory), [skills](https://code.claude.com/docs/en/skills).

## Identify the active profile

Inspect the proposed destination in PowerShell without changing the environment:

```powershell
$env:CLAUDE_CONFIG_DIR
$claudeHome = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $env:USERPROFILE '.claude' }
$claudeHome
```

Confirm actual loading with `/context` in a local Code session and record the Claude Code version. WSL/SSH have separate profiles. In the effective profile, CLAUDE.md imports adjacent AGENTS.md through `@AGENTS.md`; the skill lives at `skills\github-workflow\`. Preserve other imports. Keep settings.json, ~/.claude.json, providers, models, permissions and MCP settings intact. [Configuration directories](https://code.claude.com/docs/en/settings).

The preflight requires Git, gh, Git Bash/Bash, awk/grep/mktemp and **Python 3.8+ standard library**. Verify python3, py -3 or python launches that version; the script tries them in this order. Missing Python leaves installation unqualified: use the [official Windows distribution](https://www.python.org/downloads/windows/), then check again. No external jq is required.

## Back up and compare versions

Before editing, create private `%LOCALAPPDATA%\dev-harness\backups\v6.5.0-claude-<timestamp>` and record its absolute path:

```powershell
$backupParent = Join-Path $env:LOCALAPPDATA 'dev-harness\backups'
New-Item -ItemType Directory -Path $backupParent -Force -ErrorAction Stop | Out-Null
$backupRoot = Join-Path $backupParent ('v6.5.0-claude-' + (Get-Date -Format 'yyyyMMdd-HHmmssfff'))
if (Test-Path -LiteralPath $backupRoot) { throw 'Backup path already exists' }
New-Item -ItemType Directory -Path $backupRoot -ErrorAction Stop | Out-Null
$backupRoot
```

Back up every targeted file/link with its type/tree: CLAUDE.md, AGENTS.md, complete skill, canonical-source link and any targeted older command. Inventory relative paths, types, link targets, SHA-256 and count. Verify backup types/links/counts/hashes by readback before editing. A local receipt records absolute paths, before/after hashes, provenance/version, created/retired files and merge diffs. Keep secrets/unrelated profile files out of the export and preserve earlier backups. Report the exact backup path.

The **personal package is v6.5.0; the active repository template is v6.4**. This package includes initiative, written collaboration, review convergence, verified agent attribution and observed model/provider provenance in the shared profiles/skill. Project adoption remains a separate PR after its current task. Compare markers and actual bytes with authentic sources. Preserve newer/unknown versions and merge compatible additions without downgrading. For v6.4.0 or older with an exact base, compare historical base / local file / package file per file. Preserve local customizations, restrictions and imports. Without a verified base, capture and manually compare local content instead of fabricating history. Preserve authentic destination v5.1/v5.2 snapshots absent from this package.

## Merge instructions

1. Merge configurations/claude-desktop/AGENTS.md into the personal AGENTS.md and CLAUDE.md into personal CLAUDE.md. Retain one relative `@AGENTS.md` import resolving to its neighbor, plus existing unrelated imports/preferences. A copied AGENTS.md alone does not establish loading. An authorization conflict unresolved by current user direction needs a concrete decision.
2. Install the full skill using the controlled replacement below, including all scripts, references, license templates and history. Skill agents/openai.yaml and Codex role TOMLs do not configure Claude subagents.
3. Explorer/operator/worker/reviewer/architect are responsibilities; use Claude's actual tools/models instead of importing GPT model names/efforts. This package requires no provider, permission or Claude subagent-configuration change.

## Controlled skill replacement

1. Resolve `$claudeHome\skills\github-workflow`. For a link/junction, record target and owner; preserve the link unchanged. Modify its canonical directory only when that shared source is identified and within authorized installation scope. Otherwise retain the link and mark the skill blocked.
2. Create a uniquely named staging directory next to the ordinary/canonical destination. Copy every manifest-verified skill file. Reintegrate authentic local v5.1/v5.2 snapshots with hashes/provenance and merge INDEX.md entries in version order. Reapply qualified custom files/restrictions after comparison; resolve conflicting same-path content explicitly.
3. Compare the full old inventory with package/staging. Classify each old path absent from the package as retained customization, authentic history, or obsolete file owned by this installer. The last category requires a previous receipt, unchanged hash, verified backup and no active session use; omit those proven obsolete files from staging. Without ownership proof preserve the file or block the conflict, naming the reevaluation owner.
4. Verify staging's exact expected paths/counts: unchanged package files match hashes, intentional merges including INDEX have diffs/final hashes, and retained history/customizations remain exact unless an authorized merge applies. Check imports/references/licenses. Rename the real destination to a unique retired name in the same parent, then staging to the canonical directory name. If the second rename fails, restore the original immediately and report failure. Existing links retain the same path/target. Keep the retired copy privately until post-install verification. Recursive copying over an active directory can leave obsolete files loaded and is not this procedure.

## Named retirement inventory

| Exact path | Treatment |
|---|---|
| `$claudeHome\CLAUDE.md`, `$claudeHome\AGENTS.md` | Preserve/merge active/imported files. |
| `$claudeHome\skills\github-workflow\` | Inventory every path, preserve history/qualified customizations, replace the real directory as above and preserve links. |
| `$claudeHome\commands\github-workflow.md` | Retire this older command only after new skill activation, with prior installer provenance, unchanged hash, verified backup and no session use. Otherwise retain with a reason. |

The receipt lists exact retired paths, including the command if qualified. Avoid keeping a proven obsolete installer file just in case. No wildcard/recursive user-directory deletion, active-link removal or destruction of authentic history. Preserve uncertain ownership/use with owner and reevaluation trigger.

## Verify loading and worktree boundaries

- Verify manifest, final inventory/counts, copied hashes and merge diffs. Check @AGENTS.md, other imports, references and three license templates. In a **new local Code session**, `/context` must show the correct CLAUDE.md and `/memory` or context view must establish the imported AGENTS.md. Probe a distinctive rule read-only without opening the file ad hoc, and check `/github-workflow`. File existence/import text does not prove loading. If the session cannot establish the imported source/content, report **loading unverified** and require a new session or diagnosis rather than claiming full qualification. Read merge/review/closure rules.
- App-managed Claude Desktop Code worktrees use their owning session's archive action after preservation checks. CLI ExitWorktree applies only after same-session EnterWorktree and ownership/activity/recovery checks. Retain other/unknown managed worktrees and name their owner/trigger. `git worktree remove` is prohibited under the managed root. [Desktop session management](https://code.claude.com/docs/en/desktop).
- Conversation format follows the loaded personal preference. Give verified progress and the next action together. Record executed checks, version and exact backup path.

## Rollback

Compare every target against its installed hash. Restore backups or reverse diffs only without overwriting later work; otherwise merge. Remove a newly created file only when it still matches the installed copy and is unused. Reverify imports/instructions/skill after restoration.
