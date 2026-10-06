# Personal Claude Desktop Code installation on Windows — v6.5.0

Obtain this guide and the release checksum file through an authenticated source outside the ZIP. Start in the download directory, outside repositories and skill discovery roots, with the ZIP and checksum file there. Do not use an existing extraction or its instructions to establish trust. The bootstrap below creates the only package root used by later steps.

Target Claude Desktop's **local Code tab**, and local Claude Code using that profile. Before editing verify package version/all hashes in MANIFEST.json and read STATUS.md. Historical evidence does not qualify the destination. Personal installation stays outside repositories and does not change projects, integrate PRs or run global cleanup.

Chat/Cowork, cloud, SSH and WSL sessions are not assumed to load these same files. [Desktop Code](https://code.claude.com/docs/en/desktop), [instructions](https://code.claude.com/docs/en/memory), [skills](https://code.claude.com/docs/en/skills).

## Identify the active profile

Inspect the proposed destination in PowerShell without changing the environment:

```powershell
$env:CLAUDE_CONFIG_DIR
$claudeHome = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $env:USERPROFILE '.claude' }
$claudeHome
```

Confirm actual loading with `/context` in a local Code session and record the Claude Code version. WSL/SSH have separate profiles. In the effective profile, CLAUDE.md imports adjacent AGENTS.md through @AGENTS.md; the skill lives at `skills\github-workflow\`. Preserve other imports. Keep settings.json, ~/.claude.json, providers, models, permissions and MCP settings intact. [Configuration directories](https://code.claude.com/docs/en/settings).

The preflight requires Git, gh, Git Bash/Bash, awk/grep/mktemp and **Python 3.8+ standard library**. Verify python3, py -3 or python launches that version; the script tries them in this order. Missing Python leaves installation unqualified: use the [official Windows distribution](https://www.python.org/downloads/windows/), then check again. No external jq is required.

## Authenticate and extract the package

The expected SHA-256 must come from a separately authenticated release checksum file. Downloading both files from a replaceable, untrusted source establishes no provenance. Use PowerShell 5.1+ or PowerShell 7 with a trusted local Python installation; start a terminal without profiles (`powershell -NoProfile` or `pwsh -NoProfile`). Stop on any error and do not run later steps with values left by an earlier attempt.

Run this block from the download directory. It captures bounded ZIP/checksum bytes once, authenticates the ZIP before running any bundled code, checks its members with the authenticated installer, and extracts into a new directory. The original ZIP may subsequently change without changing this verified snapshot. Python isolation (`-I`) excludes the current directory, user modules and Python environment variables during discovery and every installer call.

<!-- package-bootstrap:start -->
```powershell
$ErrorActionPreference = 'Stop'
$installerScript = $null
$packageRoot = $null
$packageName = 'dev-harness-v6.5.0'
$archiveName = $packageName + '-codex-claude.zip'
$checksumName = $packageName + '-SHA256SUMS.txt'
$downloadRoot = (Get-Location).ProviderPath
$archiveSource = (Resolve-Path -LiteralPath (Join-Path $downloadRoot $archiveName) -ErrorAction Stop).ProviderPath
$checksumSource = (Resolve-Path -LiteralPath (Join-Path $downloadRoot $checksumName) -ErrorAction Stop).ProviderPath

function Read-BootstrapBytes([string] $Path, [int] $Limit) {
    $inputFile = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        $length = $inputFile.Length
        if ($length -gt $Limit) { throw 'Bootstrap input exceeds its size limit' }
        $bytes = New-Object byte[] ([int] $length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $count = $inputFile.Read($bytes, $offset, $bytes.Length - $offset)
            if ($count -eq 0) { throw 'Bootstrap input changed during capture' }
            $offset += $count
        }
        if ($inputFile.ReadByte() -ne -1) { throw 'Bootstrap input grew during capture' }
        return ,$bytes
    } finally {
        $inputFile.Dispose()
    }
}

$checksumBytes = Read-BootstrapBytes $checksumSource 131072
$checksumText = [Text.UTF8Encoding]::new($false, $true).GetString($checksumBytes)
$archivePattern = '^([0-9a-fA-F]{64})  ' + [regex]::Escape($archiveName) + '$'
$archiveHashes = @(foreach ($line in ($checksumText -split '\r?\n')) {
    if ($line -cmatch $archivePattern) { $Matches[1].ToLowerInvariant() }
})
if ($archiveHashes.Count -ne 1) { throw 'Require exactly one valid checksum for the expected archive' }
$archiveBytes = Read-BootstrapBytes $archiveSource 268435456
$archiveStream = [IO.MemoryStream]::new($archiveBytes, $false)
$bundle = $null
try {
    $actualHash = (Get-FileHash -InputStream $archiveStream -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
    if ($actualHash -cne $archiveHashes[0]) { throw 'Archive checksum mismatch; no bundled code was executed' }

    # Use only this authenticated snapshot, never an existing extracted installer.
    $bootstrapRoot = Join-Path $downloadRoot ('dev-harness-verified-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $bootstrapRoot -ErrorAction Stop | Out-Null
    $packageZip = Join-Path $bootstrapRoot $archiveName
    $checksums = Join-Path $bootstrapRoot $checksumName
    [IO.File]::WriteAllBytes($packageZip, $archiveBytes)
    [IO.File]::WriteAllBytes($checksums, $checksumBytes)
    Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem -ErrorAction Stop
    $archiveStream.Position = 0
    $bundle = [IO.Compression.ZipArchive]::new($archiveStream, [IO.Compression.ZipArchiveMode]::Read, $true)
    $installerEntries = @($bundle.Entries | Where-Object { $_.FullName -ceq ($packageName + '/install.py') })
    if ($installerEntries.Count -ne 1 -or $installerEntries[0].Length -gt 67108864) { throw 'Invalid bootstrap installer entry' }
    $bootstrapInstaller = Join-Path $bootstrapRoot 'bootstrap-install.py'
    [IO.Compression.ZipFileExtensions]::ExtractToFile($installerEntries[0], $bootstrapInstaller)

    $installerPython = $null
    $installerPythonArgs = @()
    $pythonCandidates = @(
        @{ Name = 'python3'; Args = @('-I') },
        @{ Name = 'py'; Args = @('-3', '-I') },
        @{ Name = 'python'; Args = @('-I') }
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
    if (-not $installerPython) { throw 'Python 3.8+ unavailable; install Python and repeat bootstrap' }
    & $installerPython @installerPythonArgs $bootstrapInstaller verify-package $packageZip --checksums $checksums
    if ($LASTEXITCODE -ne 0) { throw 'Archive member verification failed; stop before full extraction' }
    [IO.Compression.ZipFileExtensions]::ExtractToDirectory($bundle, $bootstrapRoot)
    $packageRoot = Join-Path $bootstrapRoot $packageName
    $installerScript = Join-Path $packageRoot 'install.py'
    & $installerPython @installerPythonArgs $installerScript verify-package $packageRoot --checksums $checksums
    if ($LASTEXITCODE -ne 0) { throw 'Extracted package verification failed; stop the update' }
    Set-Location -LiteralPath $packageRoot -ErrorAction Stop
} finally {
    if ($bundle) { $bundle.Dispose() }
    $archiveStream.Dispose()
}
```
<!-- package-bootstrap:end -->

The current directory is now `$packageRoot`; `$installerScript`, `$packageZip` and `$checksums` are absolute paths within the new verified workspace. Keep that workspace intact for planning/apply and recovery evidence. The bootstrap creates package files only; it does not edit an installed profile. Never substitute an old extracted `install.py` in later commands. If the workspace is removed or changes, repeat authentication from the independently trusted release files.

## Update check — read-only, before any backup or edit

After package authentication, run this check before editing the installed profile. It answers "is this computer already current, and what must change?" and modifies nothing. Record one outcome per checkpoint, using these four words:

- **current** — matches the package.
- **personalized** — matches, plus deliberate local additions or preferences.
- **outdated** — a package rule is absent, older or altered.
- **blocked** — unreadable, or not safely ownable.

Report the outcome per checkpoint; do not collapse them into one verdict.

Complete the authenticated bootstrap above before this check. Reuse its verified snapshot and fresh package root; a checksum supplied by the same untrusted archive is not an independent trust source.

The installer is standard-library Python and declares Python 3.8+ support; interpreter versions actually exercised are listed in STATUS.md. Use the same verified package and checksum file for planning and apply. Keep private plans, receipts and backups outside repositories and every skill discovery root.

1. **Package** — use the completed authenticated bootstrap and its `verify-package` results with the independently trusted `dev-harness-v6.5.0-SHA256SUMS.txt`. They check the archive and the extracted files against the same manifest digest, sizes and hashes, and reject extra, missing or unsafe paths. ZIP input also checks the archive digest; extracted-directory input reports that the archive digest was not checked in that invocation. Record the package version and that distinction. Hash agreement proves integrity against the supplied expectations, not independent provenance. A mismatch is `blocked`, and no later checkpoint is trustworthy.
2. **Instruction files** — compare `$claudeHome\CLAUDE.md` with `configurations/claude-desktop/CLAUDE.md`, and `$claudeHome\AGENTS.md` with `configurations/claude-desktop/AGENTS.md`. Keep exactly one relative @AGENTS.md import resolving to its neighbor, plus unrelated imports. Preferences merged on purpose are `personalized`.
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

The **unreleased source retains personal package version v6.5.0 and uses active repository template v6.7**; published release assets remain unchanged, with compact instructions and required policy references. This package includes initiative, written collaboration, review convergence, verified agent attribution and observed model/provider provenance in the shared profiles/skill. Project adoption remains a separate PR after its current task. Compare markers and actual bytes with authentic sources. Preserve newer/unknown versions and merge compatible additions without downgrading. For v6.4.0 or older with an exact base, compare historical base / local file / package file per file. Preserve local customizations, restrictions and imports. Without a verified base, capture and manually compare local content instead of fabricating history. Preserve authentic destination v5.1/v5.2 snapshots absent from this package.

## Merge instructions

1. Merge configurations/claude-desktop/AGENTS.md into the personal AGENTS.md and CLAUDE.md into personal CLAUDE.md. Retain one relative @AGENTS.md import resolving to its neighbor, plus existing unrelated imports/preferences. A copied AGENTS.md alone does not establish loading. An authorization conflict unresolved by current user direction needs a concrete decision.
2. Prepare a skill plan using the installer below only if that checkpoint is outdated. Skill agents/openai.yaml and Codex role TOMLs do not configure Claude subagents.
3. Explorer/operator/worker/reviewer/architect are responsibilities; use Claude's actual tools/models instead of importing GPT model names/efforts. This package requires no provider, permission or Claude subagent-configuration change.

## Plan the skill update

Use this section only when the skill checkpoint needs an update. A current or deliberately personalized skill does not need replacement merely because an instruction or role file needs a merge. After the read-only check and the backups above, save the plan in the private backup directory:

```powershell
$planPath = Join-Path $backupRoot 'skill-plan.json'
& $installerPython @installerPythonArgs $installerScript plan --runtime claude --package $packageZip --checksums $checksums --output $planPath
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
$applyOutput = & $installerPython @installerPythonArgs $installerScript apply --plan $planPath --checksums $checksums --maintenance-confirmed
if ($LASTEXITCODE -ne 0) { throw 'Apply failed; preserve the JSON error and recovery state' }
$installed = $applyOutput | ConvertFrom-Json
$receiptPath = $installed.receipt
$receiptPath
```

If authorized duplicates exist, append one `--retire-duplicate` argument with its exact planned path for each. The installer rechecks package, target and duplicate inventories and refuses drift; regenerate and review the plan after any input change. Keep the saved plan unchanged. The local process probe blocks active or unverifiable consumers, but cannot observe remote, container, WSL or web sessions or prevent a new process starting. The operator owns that part of the maintenance boundary. Never use the installer test-hook environment variables against a real profile.

The installer verifies a full backup and staged inventory, records durable recovery state, then replaces the skill using two renames. This is not an atomic or continuously available replacement. After successful apply it verifies exactly one discoverable copy. A failed swap attempts to restore and verify the original; an incomplete restoration retains recovery data and reports failure. Preserve the JSON result and inspect the receipt before restarting consumers.

## Receipt, recovery and skill rollback

The ownership and recovery checks below describe the corrected source installer. Published `v6.5.0` release files predate these corrections and remain unchanged. Use an independently authenticated package containing the corrected installer; the version string alone does not identify it. Development-version and package reconciliation is tracked in [#63](https://github.com/0xNatoshi/agentic-dev-harness/issues/63).

The private receipt records versions, package and instruction-file hashes, before/after inventories, modes, preserved/dropped/retired paths, duplicates, transaction state and maintenance evidence. It contains no file contents. The instruction hashes do not mean AGENTS.md, CLAUDE.md, config.toml or role files were merged or backed up by this skill installer; those edits use the separate private backups and diffs above.

The receipt also records a historical installer copy and an argument-array `rollback_command`. Keep both as evidence, but do not execute the receipt-directed script for secure rollback: its path and hash are both supplied by the receipt and cannot independently authenticate code. Use `$installerScript` from the separately authenticated package bootstrap above. Keep that verified workspace intact; if it changes or disappears, repeat the bootstrap from independently trusted release files before rollback or recovery. Reestablish the maintenance boundary, then run:

<!-- skill-rollback:start -->
```powershell
& $installerPython @installerPythonArgs $installerScript rollback --receipt $receiptPath --maintenance-confirmed
if ($LASTEXITCODE -ne 0) { throw 'Rollback failed; inspect the JSON error and keep recovery data' }
```
<!-- skill-rollback:end -->

Use the original receipt path reported by apply. The current installer checks native ownership and mutation rights along the state path and on `CURRENT`, `LOCK`, the canonical receipt and journals before trusting them, then rechecks under the lock. It admits the current account and root-owned safe ancestors on POSIX, or the current account, SYSTEM and Administrators on Windows. Windows system ancestors may also be owned or maintained by the exact TrustedInstaller service SID; that exception does not admit it as an owner of control files or containers, nor add it to their private creation permissions. Unsafe or unverifiable state is refused; preserve its contents and reconcile it manually from trusted evidence, without changing its permissions to make it pass. This does not authenticate arbitrary code running as the same account or a deliberately imported same-owner receipt. Older safely owned receipts and their historical installer copies remain readable; do not rewrite or execute the copies to establish provenance. Rollback also refuses if the active tree, including its recorded modes, differs from the receipt's after-inventory; preserve and reconcile later edits before retrying. Otherwise it restores the verified before-state, including edited named files, duplicate copies and caches, or returns a previously absent target to absence.

Rollback and rollback recovery require the retained committed apply journal to agree with the receipt before any restoration. Keep that journal with the original receipt and recovery copies. Missing or inconsistent older records cause a refusal; preserve them for reconciliation instead of regenerating evidence or deleting CURRENT.

For an interrupted apply, retain the original plan and run `& $installerPython @installerPythonArgs $installerScript recover --plan $planPath --maintenance-confirmed` from the verified package. For an interrupted rollback, use `recover --receipt $receiptPath --maintenance-confirmed` with that verified installer. Reestablish the maintenance boundary first. Prefer the exact plan or receipt over `recover --runtime claude`: the recorded configuration root is reused even when `CLAUDE_CONFIG_DIR` or `--home` changes. The runtime form checks only its currently resolved state directory; `nothing to recover` is limited to the reported root. Read the reported result: recovery may restore the before-state or acknowledge an already committed transaction. Never delete `CURRENT`, the journal or recovery copies to clear an error.

Success is exit 0 with JSON on stdout. Refusals such as drift use exit 1; blocked preconditions such as active consumers or links use exit 2. Exit 3 can indicate an incomplete restoration or an unexpected filesystem error: inspect the JSON and retained state before deciding the recovery action. Failed commands write JSON to stderr. A nonzero result is not a completed installation.

The legacy `$claudeHome\commands\github-workflow.md` is reported by the installer but is not removed. Retire it only under a separate verified ownership and backup decision after skill activation; otherwise record its owner and reevaluation trigger.

### Legacy receipt with a root lacking owner write

On POSIX, an older receipt can record a selected skill directory without owner write permission. Historical installers expected the recorded mode, but their retained copies remain evidence rather than a secure execution path. The independently verified current installer supports the exact owner-write exception described below; use that path if this is the only mode change. Preserve all later content edits and reconcile other inventory drift first; never edit the receipt or its inventories to make a check pass.

If the verified current installer asks for owner write and explicitly supports that single change on a legacy receipt, use that installer for this exceptional rollback. Keep consumers stopped and preserve the canonical receipt, its original retained installer and hash, backups and journals. Verify the corrected package ZIP and extracted files with the package-check commands above and its separately delivered checksums; the version number alone does not identify the corrected build. Use its extracted directory as the current directory and keep the original canonical receipt path in `$receiptPath`.

Add only the owner-write permission requested for the selected skill directory. The corrected installer accepts exactly the recorded root mode with owner write added; it still refuses other mode, content or inventory changes. Run it in a permission context that can perform the original directory moves and restoration. The mode exception does not supply missing operating-system permissions, and these steps do not authorize switching accounts or elevating privileges. Then run:

```powershell
& $installerPython @installerPythonArgs $installerScript rollback --receipt $receiptPath --maintenance-confirmed
if ($LASTEXITCODE -ne 0) { throw 'Legacy rollback failed; inspect the JSON error and preserve recovery state' }
```

Do not replace the historical installer copy, change its recorded hash, regenerate the receipt or reapply the package to bypass a refusal. If this exceptional rollback is interrupted, use the same verified corrected installer for `recover --receipt $receiptPath --maintenance-confirmed`, with consumers still stopped. Preserve `CURRENT` and all recovery data until the reported restoration is verified.

## Verify loading and worktree boundaries

- Verify manifest, final inventory/counts, copied hashes and merge diffs. Check @AGENTS.md, other imports, references and three license templates. In a **new local Code session**, `/context` must show the correct CLAUDE.md and `/memory` or context view must establish the imported AGENTS.md. Probe a distinctive rule read-only without opening the file ad hoc, and check `/github-workflow`. File existence/import text does not prove loading. If the session cannot establish the imported source/content, report **loading unverified** and require a new session or diagnosis rather than claiming full qualification. Read merge/review/closure rules.
- App-managed Claude Desktop Code worktrees use their owning session's archive action after preservation checks. CLI ExitWorktree applies only after same-session EnterWorktree and ownership/activity/recovery checks. Retain other/unknown managed worktrees and name their owner/trigger. `git worktree remove` is prohibited under the managed root. [Desktop session management](https://code.claude.com/docs/en/desktop).
- Conversation format follows the loaded personal preference. Give verified progress and the next action together. Record executed checks, version and exact backup path.

## Restore separately merged instructions

Skill rollback uses the receipt procedure above. For separately merged CLAUDE.md and AGENTS.md, compare each file with its recorded installed hash before restoring a private backup or reversing a diff. Preserve later edits; reconcile a changed file instead of overwriting it. Remove a newly created instruction file only if it is unchanged and unused. Reverify imported instructions and skill loading in a fresh session after restoration.
