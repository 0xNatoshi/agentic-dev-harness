# Changelog

## Unreleased

- Reject ambiguous package destinations, generated-manifest collisions and Windows-reserved path components before writing output. The v6.4.0 installation payload and published tag are unchanged.

## 6.4.0

- Establish the private versioned repository from the v6.3.1 distribution.
- Keep one neutral common profile source and generate runtime copies during packaging.
- Add a deterministic standard-library ZIP build, manifest and SHA-256 checksums.
- Port merge/cleanup regression fixtures into repository tests and add a Linux CI gate.
- Add issue/PR templates and explicit ownership, review and cross-computer handoff guidance.
- Preserve existing runtime guards, license texts, Codex roles and the active v6.3 project template.

## Earlier distribution history

v6.3.1 supplied English documentation and profiles after v6.3's routing, suspension and resource-lifecycle corrections. These earlier packages were produced before this repository existed. Their authentic template snapshots and import provenance are retained; this repository does not fabricate Git commits or tags for them.
