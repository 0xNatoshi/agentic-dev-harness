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
2. **Active instruction file** — establish which file Codex loads: a nonempty `$codexHome\AGENTS.override.md` takes precedence over `$codexHome\AGENTS.md`. Compare the loaded one with `configurations/codex/AGENTS.md`; compare the masked one only to keep the pair consistent. Record the loaded file as the target for any later required merge; do not edit either file during this check.
3. **`github-workflow` skill** — resolve `$skillsHome\github-workflow`. A real directory is compared file by file with `skills/github-workflow/`. A link or junction is **not** replaced: resolve its canonical directory, record the link target and its owner, and compare there. One canonical directory can serve several applications at once and is then checked once for all of them. Also resolve `$codexHome\skills\github-workflow` as a possible older separate copy. Evidence is the file count, every SHA-256 and the three license templates.
4. **Roles and configuration** — compare the `[agents]` keys in the active `config.toml` with `configurations/codex/agents-config.toml`, and the five role TOMLs with `configurations/codex/agents/`. Preserve other tables, providers, permissions and deliberate disablement. An unavailable model or effort is `personalized`, not `outdated`.
5. **Loading** — in a new read-only Codex session, confirm the loaded instruction source, the skill and the roles actually exposed. File presence or TOML syntax does not prove loading.

When every checkpoint is `current` or `personalized`, no package update is needed: report any deliberate local differences, change nothing and skip the remaining sections. Otherwise record only the `outdated` checkpoints as update candidates, preserving local customizations. A `blocked` checkpoint needs its missing evidence or ownership resolved before changing that target; a blocked package check stops the whole update. Back up the confirmed edit targets before using the remaining sections.

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
2. Prepare a skill plan using the installer below only if that checkpoint is outdated. The target is `$skillsHome\github-workflow\`; `$codexHome\skills\github-workflow\` is a possible duplicate. Apply replaces verified package paths, preserves classified history/customizations, and records named retirements. It does not merge package-path edits in place.
3. Only if the runtime supports custom roles and the supplied models/efforts, merge the `[agents]` keys from configurations/codex/agents-config.toml into active config.toml and the five explorer/worker/reviewer/architect/operator TOMLs into `$codexHome\agents\`. The fragment contains source descriptions, relative paths, concurrency/default settings; role files contain model/effort/permission/instruction layers. It is not a complete config.toml. Preserve other tables, providers, permissions, authentication and deliberate local differences, including subagent disablement. If a model/effort is unavailable, preserve the existing model and report the affected role rather than changing provider or selecting an unauthorized substitute.

The fragment's `[agents.<name>].config_file` refers to each role layer. Keep relative paths consistent. The source configuration does not require adding a features flag: verify installed-version defaults and managed settings before any activation, and preserve deliberate disablement. Effective child permissions also depend on the parent session. [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference), [roles/inheritance](https://learn.chatgpt.com/docs/agent-configuration/subagents).

## Plan the skill update

Use this section only when the skill checkpoint needs an update. A current or deliberately personalized skill does not need replacement merely because an instruction or role file needs a merge. After the read-only check and the backups above, save the plan in the private backup directory:

```powershell
$planPath = Join-Path $backupRoot 'skill-plan.json'
& $installerPython @installerPythonArgs .\install.py plan --runtime codex --package $packageZip --checksums $checksums --output $planPath
if ($LASTEXITCODE -ne 0) { throw 'Planning failed; do not apply' }
```

Planning does not modify the installed profile; `--output` writes only the requested plan file. Without `--home`, it uses the current user and effective runtime configuration. `--home DIR` selects an isolated home and ignores runtime configuration environment overrides; it is useful for a disposable test home, not a substitute for locating the active profile. There is no `--state-dir` option.

Review the plan's exact target, all `skill_roots`, package identity, complete `before` inventory (including the `.` root mode), `classification`, `retirements` and `duplicates`. `package` paths are replaced with verified package bytes; they are not merged. Paths classified as `customization` or `authentic history` are preserved. If a required customization occupies a package path, stop the skill update and reconcile it with its owner; the installer cannot preserve that edit automatically. Do not treat an old version marker as proof that a local file is unmodified.

State lives at `$env:USERPROFILE\.agents\dev-harness-install\`, a sibling of the discovery root, on the target volume and outside every skill root and repository. The installer creates the transaction, backup, staging and retired copies there. Do not create staging or retired siblings under `skills/`. A link or junction target or checked ancestor is blocked, even if the read-only comparison identified its canonical source: this CLI does not accept an arbitrary canonical destination. Preserve the link and record its owner and the action needed to establish a supported destination.

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

Rollback and rollback recovery require the retained committed apply journal to agree with the receipt before any restoration. Keep that journal with the original receipt and recovery copies. Missing or inconsistent older records cause a refusal; preserve them for reconciliation instead of regenerating evidence or deleting CURRENT.

For an interrupted apply, retain the original plan and run `& $installerPython @installerPythonArgs .\install.py recover --plan $planPath --maintenance-confirmed` from the verified package. For an interrupted rollback, use `recover --receipt $receiptPath --maintenance-confirmed` with that verified installer. Reestablish the maintenance boundary first. Prefer the exact plan or receipt over `recover --runtime codex`: the recorded directories are reused even when `CODEX_HOME` or `--home` changes. Codex state is under the selected home, independently of `CODEX_HOME`; the runtime form checks only that currently resolved state directory, and `nothing to recover` is limited to the reported root. Read the reported result: recovery may restore the before-state or acknowledge an already committed transaction. Never delete `CURRENT`, the journal or recovery copies to clear an error.

Success is exit 0 with JSON on stdout. Refusals such as drift use exit 1; blocked preconditions such as active consumers or links use exit 2. Exit 3 can indicate an incomplete restoration or an unexpected filesystem error: inspect the JSON and retained state before deciding the recovery action. Failed commands write JSON to stderr. A nonzero result is not a completed installation.

### Legacy receipt with a root lacking owner write

On POSIX, an older receipt can record a selected skill directory without owner write permission. Its retained installer is frozen at installation time; the matching old package used when no installer copy was retained is also unchanged by extracting a newer package. Both historical routes expect the recorded mode. If only owner write was added to that directory, returning it to the mode recorded by the receipt allows the historical route to run in the original permission context. Preserve all later content edits and reconcile other inventory drift first; never edit the receipt or its inventories to make a check pass.

If the verified current installer asks for owner write and explicitly supports that single change on a legacy receipt, use that installer for this exceptional rollback. Keep consumers stopped and preserve the canonical receipt, its original retained installer and hash, backups and journals. Verify the corrected package ZIP and extracted files with the package-check commands above and its separately delivered checksums; the version number alone does not identify the corrected build. Use its extracted directory as the current directory and keep the original canonical receipt path in `$receiptPath`.

Add only the owner-write permission requested for the selected skill directory. The corrected installer accepts exactly the recorded root mode with owner write added; it still refuses other mode, content or inventory changes. Run it in a permission context that can perform the original directory moves and restoration. The mode exception does not supply missing operating-system permissions, and these steps do not authorize switching accounts or elevating privileges. Then run:

```powershell
& $installerPython @installerPythonArgs .\install.py rollback --receipt $receiptPath --maintenance-confirmed
if ($LASTEXITCODE -ne 0) { throw 'Legacy rollback failed; inspect the JSON error and preserve recovery state' }
```

Do not replace the historical installer copy, change its recorded hash, regenerate the receipt or reapply the package to bypass a refusal. If this exceptional rollback is interrupted, use the same verified corrected installer for `recover --receipt $receiptPath --maintenance-confirmed`, with consumers still stopped. Preserve `CURRENT` and all recovery data until the reported restoration is verified.

## Verify and roll back

- Check package/copy hashes or intentional merge diffs, TOML syntax, five relative role references, instruction imports and three license templates. In a new read-only Codex session confirm loaded sources, skill and actually exposed roles. File presence alone does not prove loading. State an unavailable/unstarted runtime explicitly.
- Conversation headings follow the actually loaded personal preference. Put verified progress and the next action together. Record executed checks, runtime version, exposed roles and exact backup path.
- Skill rollback uses the receipt procedure above. For separately merged instructions, config.toml and role files, compare each target with its recorded installed hash before restoring a private backup or reversing a diff. Preserve later edits and deliberate disablement; reconcile changed files instead of overwriting them. Remove a new file only if unchanged and unused, then recheck instruction/skill/TOML loading. Restoring an entire config.toml blindly is not a rollback plan.
