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

Use the first working Python 3.8+ launcher in the same terminal as the installation commands below. Keep its executable and arguments separate; every command reuses this selection:

```powershell
$installerPython = $null
$installerPythonArgs = @()
$pythonCandidates = @(
    @{ Name = 'python3'; Args = @() },
    @{ Name = 'py'; Args = @('-3') },
    @{ Name = 'python'; Args = @() }
)
foreach ($candidate in $pythonCandidates) {
    $candidateCommand = Get-Command -Name $candidate.Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $candidateCommand) { continue }
    $candidateArgs = $candidate.Args
    & $candidateCommand.Source @candidateArgs -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $installerPython = $candidateCommand.Source
        $installerPythonArgs = $candidateArgs
        break
    }
}
if (-not $installerPython) { throw 'Python 3.8+ unavailable; install Python and repeat discovery' }
```

## Update check — read-only, before any backup or edit

Run this first. It answers "is this computer already current, and what must change?" and modifies nothing. Record one outcome per checkpoint, using these four words:

- **current** — matches the package.
- **personalized** — matches, plus deliberate local additions or preferences.
- **outdated** — a package rule is absent, older or altered.
- **blocked** — unreadable, or not safely ownable.

Report the outcome per checkpoint; do not collapse them into one verdict.

Keep the original ZIP and its separately delivered `dev-harness-v6.5.0-SHA256SUMS.txt` beside the extracted package. From the extracted package root, this read-only example uses those actual sibling files; adjust their locations if necessary:

```powershell
$packageZip = (Resolve-Path '..\dev-harness-v6.5.0-codex-claude.zip' -ErrorAction Stop).Path
$checksums = (Resolve-Path '..\dev-harness-v6.5.0-SHA256SUMS.txt' -ErrorAction Stop).Path
& $installerPython @installerPythonArgs .\install.py verify-package $packageZip --checksums $checksums
if ($LASTEXITCODE -ne 0) { throw 'Package verification failed; stop the update' }
& $installerPython @installerPythonArgs .\install.py verify-package . --checksums $checksums
if ($LASTEXITCODE -ne 0) { throw 'Extracted package verification failed; stop the update' }
```

The installer is standard-library Python and declares Python 3.8+ support; interpreter versions actually exercised are listed in STATUS.md. Use the same verified package and checksum file for planning and apply. Keep private plans, receipts and backups outside repositories and every skill discovery root.

1. **Package** — use the shipped `install.py verify-package` commands above with the separately supplied `dev-harness-v6.5.0-SHA256SUMS.txt`. They check the archive and the extracted files against the same manifest digest, sizes and hashes, and reject extra, missing or unsafe paths. ZIP input also checks the archive digest; extracted-directory input reports that the archive digest was not checked in that invocation. Record the package version and that distinction. Hash agreement proves integrity against the supplied expectations, not independent provenance. A mismatch is `blocked`, and no later checkpoint is trustworthy.
2. **Instruction files** — compare `$claudeHome\CLAUDE.md` with `configurations/claude-desktop/CLAUDE.md`, and `$claudeHome\AGENTS.md` with `configurations/claude-desktop/AGENTS.md`. Keep exactly one relative `@AGENTS.md` import resolving to its neighbor, plus unrelated imports. Preferences merged on purpose are `personalized`.
3. **`github-workflow` skill** — resolve `$claudeHome\skills\github-workflow`. A real directory is compared file by file with `skills/github-workflow/`. A link or junction is **not** replaced: resolve its canonical directory, record the link target and its owner, and compare there. One canonical directory can serve several applications at once and is then checked once for all of them. Evidence is the file count, every SHA-256 and the three license templates; extra `.bak` or history files are retained customizations.
4. **Claude imports and registration** — confirm the imports that load in the effective profile and that `/github-workflow` resolves. `agents/openai.yaml` and the Codex role TOMLs do not configure Claude subagents.
5. **Loading** — in a new local Code session, `/context` must show the effective CLAUDE.md and establish the imported AGENTS.md. File presence or import text does not prove loading.

When every checkpoint is `current` or `personalized`, no package update is needed: report any deliberate local differences, change nothing and skip the remaining sections. Otherwise record only the `outdated` checkpoints as update candidates, preserving local customizations. A `blocked` checkpoint needs its missing evidence or ownership resolved before changing that target; a blocked package check stops the whole update. Back up the confirmed edit targets before using the remaining sections.

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
2. Prepare a skill plan using the installer below only if that checkpoint is outdated. Skill agents/openai.yaml and Codex role TOMLs do not configure Claude subagents.
3. Explorer/operator/worker/reviewer/architect are responsibilities; use Claude's actual tools/models instead of importing GPT model names/efforts. This package requires no provider, permission or Claude subagent-configuration change.

## Plan the skill update

Use this section only when the skill checkpoint needs an update. A current or deliberately personalized skill does not need replacement merely because an instruction or role file needs a merge. After the read-only check and the backups above, save the plan in the private backup directory:

```powershell
$planPath = Join-Path $backupRoot 'skill-plan.json'
& $installerPython @installerPythonArgs .\install.py plan --runtime claude --package $packageZip --checksums $checksums --output $planPath
if ($LASTEXITCODE -ne 0) { throw 'Planning failed; do not apply' }
```

Planning does not modify the installed profile; `--output` writes only the requested plan file. Without `--home`, it uses the current user and effective runtime configuration. `--home DIR` selects an isolated home and ignores runtime configuration environment overrides; it is useful for a disposable test home, not a substitute for locating the active profile. There is no `--state-dir` option.

Review the plan's exact target, all `skill_roots`, package identity, complete `before` inventory (including the `.` root mode), `classification`, `retirements` and `duplicates`. `package` paths are replaced with verified package bytes; they are not merged. Paths classified as `customization` or `authentic history` are preserved. If a required customization occupies a package path, stop the skill update and reconcile it with its owner; the installer cannot preserve that edit automatically. Do not treat an old version marker as proof that a local file is unmodified.

State lives at `$claudeHome\dev-harness-install\`, a sibling of the discovery root, on the target volume and outside every skill root and repository. The installer creates the transaction, backup, staging and retired copies there. Do not create staging or retired siblings under `skills/`. A link or junction target or checked ancestor is blocked, even if the read-only comparison identified its canonical source: this CLI does not accept an arbitrary canonical destination. Preserve the link and record its owner and the action needed to establish a supported destination.

Any other discoverable `github-workflow` copy is listed. Apply blocks unless every planned duplicate is explicitly selected with a repeated `--retire-duplicate PATH` option. Use that option only for an owned copy whose retirement is authorized; its exact inventory is backed up, moved outside the discovery roots, recorded and restored by rollback. Unknown ownership, linked copies or overlapping roots need resolution before applying; do not automatically retire every discovered path.

## Named v5.2 retirement rule

The versioned retirement list uses these exact paths relative to the selected `github-workflow` directory:

| Path | Apply and rollback behavior |
|---|---|
| `templates/LICENSE-MIT` | Retire from the active tree; preserve exact bytes for rollback. |
| `templates/LICENSE-PolyForm-Noncommercial` | Retire from the active tree; preserve exact bytes for rollback. |
| `templates/LICENSE-proprietary` | Retire from the active tree; preserve exact bytes for rollback. |
| `readme-guide.md` | Retire from the active tree; preserve exact bytes for rollback. |
| `templates/README.md` | Retire from the active tree; preserve exact bytes for rollback. |

Retirement is by pathname, including an edited copy, without a prior receipt or historical hash requirement. Review each present file's `path`, `listed_as`, `sha256` and `bytes` in the plan before deciding to apply. The plan gives the state root; its transaction is not allocated yet. Apply output and the receipt give each file's concrete `retired_copy` and `backup` locations and the `rollback_command`. A file absent before installation is not listed as retired. These five names are not a rule for deleting other files.

Unknown files and authentic local history remain active unless they occupy a package path as described above. `__pycache__` and `*.pyc` leave the new active tree and are recorded as dropped there; the private before-state copies retain them for exact rollback. An edited named file may contain useful material even though it is obsolete at that location: its removal from active use is intentional and its preserved locations must be recorded.

## Apply during maintenance

Stop all Claude and Codex sessions that can consume the affected skills, prevent new sessions from starting, and close handles or shells whose working directory is in the target. Run the following from an independent terminal outside every skill root, keeping consumers stopped through verification. An agent running inside a consuming session must hand over the prepared plan rather than run the mutation itself. `--maintenance-confirmed` records the operator's confirmation; it does not stop applications.

```powershell
$applyOutput = & $installerPython @installerPythonArgs .\install.py apply --plan $planPath --checksums $checksums --maintenance-confirmed
if ($LASTEXITCODE -ne 0) { throw 'Apply failed; preserve the JSON error and recovery state' }
$installed = $applyOutput | ConvertFrom-Json
$receiptPath = $installed.receipt
$receiptPath
```

If authorized duplicates exist, append one `--retire-duplicate` argument with its exact planned path for each. The installer rechecks package, target and duplicate inventories and refuses drift; regenerate and review the plan after any input change. Keep the saved plan unchanged. The local process probe blocks active or unverifiable consumers, but cannot observe remote, container, WSL or web sessions or prevent a new process starting. The operator owns that part of the maintenance boundary. Never use the installer test-hook environment variables against a real profile.

The installer verifies a full backup and staged inventory, records durable recovery state, then replaces the skill using two renames. This is not an atomic or continuously available replacement. After successful apply it verifies exactly one discoverable copy. A failed swap attempts to restore and verify the original; an incomplete restoration retains recovery data and reports failure. Preserve the JSON result and inspect the receipt before restarting consumers.

## Receipt, recovery and skill rollback

The private receipt records versions, package and instruction-file hashes, before/after inventories, modes, preserved/dropped/retired paths, duplicates, transaction state and maintenance evidence. It contains no file contents. The instruction hashes do not mean AGENTS.md, CLAUDE.md, config.toml or role files were merged or backed up by this skill installer; those edits use the separate private backups and diffs above.

The receipt also records the verified installer copy retained under the transaction and an argument-array `rollback_command`, so rollback does not depend on the original extracted directory remaining available. The command intentionally omits `--maintenance-confirmed`; add it only after reestablishing the maintenance boundary. Do not interpret the array as a shell string. For a current receipt, an equivalent PowerShell invocation checks the retained script's hash before running it:

```powershell
$receipt = Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
$retainedInstaller = $receipt.installer_copy.path
if (-not $retainedInstaller) { throw 'Use the verified matching package installer for this older receipt' }
$actualHash = (Get-FileHash -LiteralPath $retainedInstaller -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
if ($actualHash -ne $receipt.installer_copy.sha256) { throw 'Retained installer hash mismatch; stop' }
& $installerPython @installerPythonArgs $retainedInstaller rollback --receipt $receiptPath --maintenance-confirmed
if ($LASTEXITCODE -ne 0) { throw 'Rollback failed; inspect the JSON error and keep recovery data' }
```

Use the original receipt path reported by apply. Rollback refuses if the active tree, including its recorded modes, differs from the receipt's after-inventory; preserve and reconcile later edits before retrying. Otherwise it restores the verified before-state, including edited named files, duplicate copies and caches, or returns a previously absent target to absence. A deleted retained installer must be replaced by a separately verified matching package installer; do not reconstruct missing recovery evidence.

For an interrupted apply, retain the original plan and run `& $installerPython @installerPythonArgs .\install.py recover --plan $planPath --maintenance-confirmed` from the verified package. For an interrupted rollback, use `recover --receipt $receiptPath --maintenance-confirmed` with that verified installer. Reestablish the maintenance boundary first. Prefer the exact plan or receipt over `recover --runtime claude`: the recorded configuration root is reused even when `CLAUDE_CONFIG_DIR` or `--home` changes. The runtime form checks only its currently resolved state directory; `nothing to recover` is limited to the reported root. Read the reported result: recovery may restore the before-state or acknowledge an already committed transaction. Never delete `CURRENT`, the journal or recovery copies to clear an error.

Success is exit 0 with JSON on stdout. Refusals such as drift use exit 1; blocked preconditions such as active consumers or links use exit 2. Exit 3 can indicate an incomplete restoration or an unexpected filesystem error: inspect the JSON and retained state before deciding the recovery action. Failed commands write JSON to stderr. A nonzero result is not a completed installation.

The legacy `$claudeHome\commands\github-workflow.md` is reported by the installer but is not removed. Retire it only under a separate verified ownership and backup decision after skill activation; otherwise record its owner and reevaluation trigger.

## Verify loading and worktree boundaries

- Verify manifest, final inventory/counts, copied hashes and merge diffs. Check @AGENTS.md, other imports, references and three license templates. In a **new local Code session**, `/context` must show the correct CLAUDE.md and `/memory` or context view must establish the imported AGENTS.md. Probe a distinctive rule read-only without opening the file ad hoc, and check `/github-workflow`. File existence/import text does not prove loading. If the session cannot establish the imported source/content, report **loading unverified** and require a new session or diagnosis rather than claiming full qualification. Read merge/review/closure rules.
- App-managed Claude Desktop Code worktrees use their owning session's archive action after preservation checks. CLI ExitWorktree applies only after same-session EnterWorktree and ownership/activity/recovery checks. Retain other/unknown managed worktrees and name their owner/trigger. `git worktree remove` is prohibited under the managed root. [Desktop session management](https://code.claude.com/docs/en/desktop).
- Conversation format follows the loaded personal preference. Give verified progress and the next action together. Record executed checks, version and exact backup path.

## Restore separately merged instructions

Skill rollback uses the receipt procedure above. For separately merged CLAUDE.md and AGENTS.md, compare each file with its recorded installed hash before restoring a private backup or reversing a diff. Preserve later edits; reconcile a changed file instead of overwriting it. Remove a newly created instruction file only if it is unchanged and unused. Reverify imported instructions and skill loading in a fresh session after restoration.
