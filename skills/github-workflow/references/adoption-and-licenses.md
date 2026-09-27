# Adoption, licenses and versions

Read for `init`, `init all`, `update`, `license [all]`, or an owned repository that is new or has a missing/older managed block. This policy is already delegated; it does not require another technical go. Deliberate project restrictions still govern delivery.

## Scope and sequence

Repository adoption installs the English AGENTS.md block, the CLAUDE.md import and useful project files. The personal skill/global rules stay in agent configuration; do not copy this personal package or its archives into a project.

- **Owned**: the authenticated account's repository or an organization explicitly entrusted in user context. Read/write access alone does not prove ownership. Verify GitHub host, `nameWithOwner`, `isFork`, `isArchived`, visibility, instructions and effective rights.
- **New original repository**: include project instructions/import, useful project files and the policy-selected license in the first commit before authorized first publication. An empty commit added later cannot simulate this. Creation/publication retains its authorization; the bootstrap exception is distinct from subsequent default-branch changes, which use PRs.
- **Existing repository**: identify the need initially, finish/verify the current task, then deliver a separate `chore/agent-workflow` or `chore/agent-workflow-update` PR. Keep this migration separate from code changes. Reuse an entrusted workflow PR rather than duplicating it. A task already devoted to `init`/`update` is that PR, not a reason for another.
- **Missing license in an existing repository**: use a dedicated license PR after the current task, separate from code/workflow migration. Finish one topic before the next; group its coherent documentation fixes in its PR. Complete authorized work that is possible now; a genuine blocker gets an owner and precise action.
- **Fork/other owner**: no automatic adoption/migration. An explicit request defines scope; preserve upstream licenses/contracts/rules. Do not reclassify a fork as an original repository to apply defaults.
- **Archived**: read-only; report/exclude without unarchiving. Preserve other active sessions/worktrees and isolate a PR safely.

## `init all`

1. Inventory owned repositories of already identified accounts/organizations on the authenticated host, read-only. Paginate the appropriate API (`gh api --paginate`) or use a verified local inventory; the first 30/100 results are not a complete list. Exclude forks, third parties and archived repositories unless explicitly included.
2. Show repository, detected version, intended action and exclusions. `init all` authorizes adoption/update PRs within that verified scope, without per-repository confirmations. If organization-wide scope is unclear, proceed on established owned repositories and ask only about the missing boundary.
3. Read each repository's instructions, license, block/version, real commands, CI, README and About. Current versions do not need empty PRs. Compare newer/unknown versions instead of downgrading.
4. Process one repository/coherent delivery at a time. The policy in force before a PR's changes governs that PR; it cannot authorize itself. Preserve explicit holds and repair technical prerequisites. Permissions, visibility, protections, subscriptions and providers are not side effects of adoption.
5. Verify publication and close resources. Report installed/updated/current/excluded/blocked per repository, with PR/evidence or a concrete remaining action. No change to an already conforming repository is a valid result.

## `license [all]`

`license` inspects the current repository; `license all` inventories verified owned originals using the same pagination, exclusions and sequence as `init all`. This does not trigger workflow installation or rewriting existing licenses. Apply the table only to genuinely unlicensed repositories after visibility/type/provenance checks. Use a dedicated PR after the current task for an existing repository, or the first commit for a new original. Forks/other owners require an explicit request. Report already licensed, excluded and blocked repositories without empty PRs.

## Licenses

MIT selection for public repositories is automatic. A new public grant is irreversible: prepare text/evidence, then obtain specific authorization covering the grant before push/publication/integration. General development authorization, automatic selection and a revert do not provide that permission. Existing specific authorization counts. This boundary takes precedence over autonomous delivery and includes a public repository's first commit.

First inspect `LICENSE*`/`LICENCE*`/`COPYING*`, SPDX declarations, metadata and notices. A declared license without its full file is still an existing license. Preserve third-party notices/licenses.

Verify the holder's right to license the content, including provenance, earlier contributions and applicable agreements. GitHub ownership does not transfer other authors' rights. For uncovered third-party contributions, prepare the PR but retain publication until that evidence exists; this needs proof, not another license choice. Established organization rights/agreements need no extra confirmation. Invent no assignment of rights and remove no notice to fit the table.

| Owned original without an existing license | Automatic template |
|---|---|
| Public, any project type | `templates/LICENSE-mit.txt` |
| Private connector or MCP server | `templates/LICENSE-polyform-noncommercial-1.0.0.txt` |
| Other private repository | `templates/LICENSE-proprietary.txt` |

The connector/MCP exception applies only to private repositories. Establish type from the project's actual role, README, API/transport, manifest and code, not a client dependency. Investigate missing information; access failure is not proof of private visibility. Ask only for indispensable information if classification remains uncertain.

The proprietary template reserves rights while letting authorized contributors access, copy, modify, compile, test and run the software to develop/maintain this project. Development may concern a commercial project; publication, external distribution and production operation need separate authorization. It is a custom policy template, not a standard GitHub catalog license. Do not substitute the old no-use template. It imposes no assignment of contributions; respect other authors' agreements/rights.

MIT uses [GitHub's standard text](https://choosealicense.com/licenses/mit/). PolyForm contains the unchanged [official Noncommercial 1.0.0 text](https://polyformproject.org/licenses/noncommercial/1.0.0.txt), preceded only by the added copyright `Required Notice:` line to complete. Keep its clauses intact and do not describe it as unrestricted open source. It permits the uses/distribution specified in its text; private repository access does not add license restrictions. Proprietary-template prohibitions do not apply to PolyForm.

Set `{{YEAR}}` to the relevant year and `{{COPYRIGHT_HOLDER}}` to the established organization (Microsoft Corporation only when that ownership is confirmed), otherwise the appropriate GitHub identifier. Exclude personal names/email. Company examples in a license remain examples. For PolyForm, fill only those two fields in the added notice; keep all other notices and the official body. Use SPDX `MIT` or `PolyForm-Noncommercial-1.0.0`; the custom proprietary template is neither MIT nor a standard GitHub license.

### Manifest alignment

Align metadata with the actual license without inventing rights or confusing GitHub visibility with package publication. Verify package-manager/backend versions and build when applicable. Preserve an existing license/publication configuration; changing it needs a user decision even if it differs from the default.

- **npm (`package.json`)**: proprietary uses `"license": "UNLICENSED"` and `"private": true`; LICENSE remains the source of limited contributor rights. MIT uses `"license": "MIT"`. PolyForm uses `"license": "PolyForm-Noncommercial-1.0.0"`, not `UNLICENSED`; it is an SPDX identifier with defined rights. Set `"private": true` for a PolyForm package not intended for a registry, while preserving an already authorized distribution process. [npm](https://docs.npmjs.com/files/package.json/), [PolyForm SPDX](https://spdx.org/licenses/PolyForm-Noncommercial-1.0.0.html).
- **Python (`pyproject.toml`)**: with PEP 639 backend support, set `[project] license` to `"MIT"`, `"PolyForm-Noncommercial-1.0.0"` or `"LicenseRef-Proprietary"` and add the actual path to `license-files`, preserving other notices. `Private :: Do Not Upload` guards PyPI uploads, not every index. With older backends use their documented supported configuration, verify the build and report limits instead of writing invalid metadata. [PyPA guide](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/), [proprietary example](https://packaging.python.org/en/latest/guides/licensing-examples-and-user-scenarios/#i-have-a-private-package-that-won-t-be-distributed).
- **Rust (`Cargo.toml`)**: use `[package] license = "MIT"` or `"PolyForm-Noncommercial-1.0.0"`. For the custom proprietary template use `license-file = "LICENSE"`. A private crate not intended for a registry uses `publish = false`; preserve an authorized publication process. Check relevant workspace members. [Cargo license](https://doc.rust-lang.org/cargo/reference/manifest.html#the-license-and-license-file-fields), [publish](https://doc.rust-lang.org/cargo/reference/manifest.html#the-publish-field).

### Compare standard texts

After filling legal fields, compare the delivered license byte for byte with the official MIT/PolyForm sources. Remove Choose a License's metadata header for MIT; prefix only the filled notice plus blank line for PolyForm. Set verified `LICENSE_KIND` (`MIT`/`PolyForm`), `LICENSE_PATH`, `LICENSE_YEAR`, `LICENSE_HOLDER`, export them, and run at the repository root:

```bash
python3 - <<'PY'
import difflib
import os
from pathlib import Path
from urllib.request import urlopen

kind = os.environ["LICENSE_KIND"]
path = Path(os.environ["LICENSE_PATH"])
year = os.environ["LICENSE_YEAR"]
holder = os.environ["LICENSE_HOLDER"]
if not (len(year) == 4 and year.isdigit() and holder.strip()) or "{{" in holder:
    raise SystemExit("Set a confirmed year and copyright holder first")
urls = {
    "MIT": "https://raw.githubusercontent.com/github/choosealicense.com/gh-pages/_licenses/mit.txt",
    "PolyForm": "https://polyformproject.org/licenses/noncommercial/1.0.0.txt",
}
source = urlopen(urls[kind], timeout=30).read()
if kind == "MIT":
    header, separator, body = source.partition(b"\n---\n")
    if not header.startswith(b"---\n") or not separator:
        raise SystemExit("Unexpected MIT source format")
    expected = body.lstrip(b"\n").replace(b"[year]", year.encode()).replace(b"[fullname]", holder.encode())
else:
    expected = f"Required Notice: Copyright (c) {year} {holder}\n\n".encode() + source
actual = path.read_bytes()
if actual != expected:
    print("".join(difflib.unified_diff(
        expected.decode().splitlines(keepends=True),
        actual.decode().splitlines(keepends=True),
        fromfile="official-plus-confirmed-notice", tofile=str(path))), end="")
    raise SystemExit(1)
print(f"Exact license text verified: {path}")
PY
```

This requires Python 3; on Windows use `py -3` when appropriate. If unavailable, report that check unavailable and prepare an equivalent exact comparison before claiming conformity.

Network failures or differences block a successful official-comparison claim. Preserve the diff and resolve differences before delivering the standard template. The proprietary text is custom: check its rights against the chosen template without claiming an official external text. Check unresolved `{{...}}`, `[year]`, `[fullname]`, `TO FILL` in every delivered `LICENSE*` and README.md, then reconcile notices/metadata.

Selecting/preparing the default template requires no further decision. Public grants still require the specific authorization above. Replacing/removing an existing license or changing visibility is the user's decision; repository creation is not authorization to remove existing rights. Update the license mention/badge in the same dedicated PR under the publication boundary.

## Migration and history

`templates/AGENTS.md` is active; `templates/history/AGENTS-v<version>.md` stores authentic immutable snapshots, with provenance in INDEX.md. Preserve their exact bytes rather than rewriting history to fit current preferences. They are comparison evidence, not active project instructions.

Before release, verify the active template against its snapshot:

```bash
cmp -s "<skill-root>/templates/AGENTS.md" "<skill-root>/templates/history/AGENTS-v6.4.md"
```

Exit 0 means equal; 1 means different; >1 is a read error. Resolve differences/errors before distribution. PowerShell may compare byte arrays if `cmp` is unavailable; a preview/version label is insufficient. Keep and update the skill's **What's new** section. Package v6.5.0 adds initiative and written collaboration; its active repository template is v6.4. Preserve older snapshots unchanged.

For v5.1/v5.2 or another older repository:

1. Capture complete AGENTS.md, imports, managed block and real project parameters before editing.
2. Read the authentic corresponding snapshot if available. Compare old template, installed block and new template. Render old parameters with repository values before `cmp`; comparing raw placeholders does not establish customization.
3. Reapply project parameters/customizations. Preserve commands/contracts/holds outside the block and localized historical suspension forms. An ambiguous restriction stays in place; a model update does not revoke it.
4. Without an exact v5.1/v5.2 snapshot, preserve the installed block as a **repository-specific capture**, not global template history. For reformatted blocks use `git diff --no-index --word-diff=plain <historical-rendered-block> <installed-block>` (1 means differences; >1 means error) to distinguish formatting from local rules. Compare manually and preserve unknown provenance; a version number cannot reconstruct history.
5. Verify outside-block content, parameters, unresolved placeholders including LICENSE*/README.md, links, English and absence of personal data. Run required gates and deliver the migration PR; its policy changes cannot override the hold governing its own integration.
