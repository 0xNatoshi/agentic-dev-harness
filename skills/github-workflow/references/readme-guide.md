# `readme` — maintain README and About

Read for `readme`, during `init`/`update`, and when a task changes project usage. `readme` covers documentation/About, not an automatic workflow migration or license replacement. Adoption/license policies retain their separate-PR sequence.

## Start with facts

Read project instructions, README, manifests/scripts, relevant architecture and supported configuration. Verify repository/host/owner, GitHub description and any URL before writing. Invent no feature, benchmark, Windows compatibility, test result or screenshot. Describe environmental limits honestly.

Extend/correct an existing README while preserving valid authored content. Removing authored material needs explicit authorization, including for shortening/reorganization; prior applicable authorization counts. Prepare the proposed deletion and rollback when needed. Create a missing README. Avoid broad formatting noise. Group one task's documentation fixes in one PR; a feature PR includes its README changes, without a separate PR per remark.

## Content checklist

Adapt to actual needs; unused sections are optional:

- Title/summary: purpose, audience and useful outcome.
- Accurate badges for actual CI/license and working links.
- A real screenshot/example when an interface exists.
- Prerequisites: runtime versions and required external dependencies.
- Exact installation commands matching the lockfile's package manager, verified paths/results.
- Smallest reproducible quick start; distinguish illustrative input from tested commands.
- Supported configuration/files, safe values and real defaults; no credentials/personal data.
- Run/build/lint/test commands; reference AGENTS.md for workflow instead of duplicating it.
- Main architecture boundaries/flows and links to deeper documentation.
- Contribution branch/PR/test instructions and repository rules.
- Existing or authorized license, documentation/site/changelog links.

Use English for repository text, commits, PRs and About. Exclude personal global instructions, personal identity/email and machine paths. Use relative paths/neutral examples and sanitize attached logs/captures.

README.md is the canonical English version. Explicitly requested translations use `README.<bcp47>.md`, with a valid [BCP 47 language tag](https://www.rfc-editor.org/info/rfc5646/) such as README.fr.md or README.pt-BR.md. Translate explanatory prose, not commands, paths, variable/API names or technical assertions. Cross-link versions and verify the same functional revision. If an existing translation becomes stale without an update assignment, report the exact gap/owner rather than silently deleting/rewriting it. An explicit translation request is a narrow language-policy exception.

## GitHub About

Read `gh repo view --json nameWithOwner,description,url,isArchived` after the origin preflight. Prepare an accurate English sentence without a final period, **at most 120 characters**. Correct empty/stale descriptions. Use exactly the same sentence in About, the README's descriptive opening and the applicable project manifest description (package.json, pyproject.toml or equivalent) when present; this is not the harness MANIFEST.json. Count the proposed and read-back values.

An explicit request covering README/About authorizes only that description change: use `gh repo edit "$workflow_host/$workflow_repo" --description <description>` with safe argument quoting, reread the value and retain the old one for rollback. Website/topics/visibility/protections/permissions/unarchiving retain separate authorization. For `readme` triggered implicitly by code changes, prepare the About delta and establish whether publication is already authorized.

Archived repositories remain read-only. On forks/third-party repositories, the explicit request must cover the intended writes. Technical write access alone is not delegated ownership.

## Verify and deliver

- Execute example commands when environment/scope permit and identify unexecuted examples.
- Check relative links, badges, personal identifiers/paths, secrets and factual claims. Search TODO/TBD/TO FILL/`{{...}}`, for example `rg -n 'TODO|TBD|TO FILL|\{\{[^}]+\}\}' -- README.md`, and each delivered translation (1 means no matches; >1 means error). Clearly label intentional example markers. Include every LICENSE* in a license PR.
- Measure proposed and read-back About with `python3 -c 'import sys; s=sys.argv[1]; print(len(s)); raise SystemExit(not (0 < len(s) <= 120))' "$ABOUT_DESCRIPTION"` or a configured equivalent. Validate the actual published value. Windows may use `py -3`, or PowerShell: `if ([string]::IsNullOrWhiteSpace($env:ABOUT_DESCRIPTION) -or $env:ABOUT_DESCRIPTION.Length -gt 120) { throw 'About must contain 1–120 characters' }`.
- Run configured link/Markdown checks. If already available, use `lychee README.md` and `markdownlint-cli2 README.md`, adding changed translations. Avoid a new dependency without demonstrated need. Report an unconfigured/unavailable tool as unavailable, not passed. [Lychee](https://github.com/lycheeverse/lychee#commandline-usage), [markdownlint-cli2](https://github.com/DavidAnson/markdownlint-cli2#use).
- Inspect rendered Markdown when useful/available. Source reading does not prove rendering.
- Documentation-only `readme` work uses self-review/applicable automatic checks regardless of size or effort, including `ultracode`, without subagents, a full development cycle or new workflow/CI adoption. Substantial code, security, license, data or autonomy changes use their own gates. Existing publication gates and irreversible-action boundaries still apply.
- Deliver through existing PR gates, verify authorized About changes and close resources. Repository/PR reports use **Done / Next step / Required input**; conversation follows the user's language/format.
