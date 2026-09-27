#!/usr/bin/env python3
"""Check source integrity, portable links and syntax, and screen repository privacy.

The privacy screen rejects machine-specific home paths, common credential
formats and email addresses outside example domains, GitHub noreply,
the Claude co-author trailer and the GitHub SSH user. It does not detect
personal names or obfuscated addresses.
"""
import ast
import importlib.util
import json
from pathlib import Path
import re
import sys
import tomllib

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build import payloads, version  # noqa: E402


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


# The domain is every dotted label after @, letters in any script included, so a
# neutral prefix such as example.com2 or anthropic.com-evil cannot pass; a final
# sentence period is not a label. Version pins such as action@v4.2.2 start with
# two numeric labels; real address domains rarely do, so they are skipped.
DOMAIN = re.compile(r'@((?:[^\W_]|-)+(?:\.(?:[^\W_]|-)+)+)')
VERSION = re.compile(r'[vV]?\d+\.\d+(?:\Z|[.-])')
# The local part is the token before @, including RFC quoted strings and
# <placeholder> segments, so punctuation or quoting cannot hide an address;
# a quoted string never spans whitespace, @ or commas, so `"a", "b@c"` reads as b.
# Only characters that cannot appear unquoted in a local part end it. The domain
# decides; only exact service addresses are neutral. RFC 5321 caps local parts
# at 64 octets, so a bounded look-back keeps long lines linear.
LOCAL = re.compile(r'(?:"(?:[^"\\\s@,]|\\.)*"|<[^<>@\s]*>|[^\s<>()\[\],;:"@])*\Z')
LOOK_BACK = 256
# Markdown emphasis, code spans, quotes and braces are valid local-part
# characters too; they wrap the address only when mirrored right after it.
WRAPPERS = "_*~`'{"
CLOSING = str.maketrans("{", "}")
NEUTRAL_DOMAINS = {"example.invalid", "example.com", "example.org", "users.noreply.github.com"}
NEUTRAL_ADDRESSES = {"noreply@anthropic.com", "git@github.com"}


def check_emails(relative, text):
    for number, line in enumerate(text.splitlines(), 1):
        for match in DOMAIN.finditer(line):
            window = line[max(0, match.start() - LOOK_BACK):match.start()]
            token = LOCAL.search(window).group(0)
            before = window[:len(window) - len(token)]
            local = token.lstrip(WRAPPERS)
            wrapper = token[:len(token) - len(local)]
            if not line[match.end():].startswith(wrapper[::-1].translate(CLOSING)):
                local = token
            elif not local and (not before or before[-1] in " \t([") and (
                    not wrapper or wrapper == "`" and match.group(1).endswith(".md")):
                # No local part at all is an @mention or import; a wrapper-only
                # token is a local part too, except a code span around a Markdown
                # import such as `@AGENTS.md`.
                continue
            domain = match.group(1).lower()
            if VERSION.match(domain):
                continue
            neutral = domain in NEUTRAL_DOMAINS or f"{local.lower()}@{domain}" in NEUTRAL_ADDRESSES
            # Report the location only, so the gate never echoes an address.
            require(neutral, f"Email address outside the neutral allowlist: {relative}:{number}")


def check_links(name, text, available):
    for target in re.findall(r"\]\(([^)]+)\)", text):
        if "://" in target or target.startswith(("#", "<", "mailto:")):
            continue
        path = target.partition("#")[0]
        normalized = Path(name).parent / path
        parts = []
        for part in normalized.parts:
            if part == "..":
                require(bool(parts), f"Link escapes package: {name}: {target}")
                parts.pop()
            elif part != ".":
                parts.append(part)
        resolved = "/".join(parts)
        require(resolved in available or any(n.startswith(resolved.rstrip("/") + "/") for n in available), f"Broken link: {name}: {target}")


def main():
    release = version()
    package = payloads()
    project = json.loads((ROOT / "project.json").read_text(encoding="utf-8"))
    require(len(project["description"]) <= 120, "About description exceeds 120 characters")
    require((ROOT / "README.md").read_text(encoding="utf-8").splitlines()[2] == project["description"], "README/project description mismatch")
    require(project["private"] is True and project["license"] == "LicenseRef-Proprietary", "Private repository metadata must match its license")
    model = ROOT / "skills/github-workflow/templates/AGENTS.md"
    snapshot = model.parent / "history" / ("AGENTS-v" + project["repository_template_version"] + ".md")
    require(model.read_bytes() == snapshot.read_bytes(), "Active template differs from its declared historical snapshot")
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in {".git", "dist", "__pycache__"} for part in relative.parts) or path.name == "TASKS.md":
            continue
        require(not path.is_symlink(), f"Unexpected repository symlink: {relative}")
        if not path.is_file():
            continue
        data = path.read_bytes()
        text = data.decode("utf-8")
        require(not re.search(r"/(?:Users|home)/[A-Za-z0-9_.-]+/|[A-Za-z]:\\Users\\", text), f"Machine-specific path: {relative}")
        check_emails(relative, text)
        require(not re.search(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{24,}|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----)", text), f"Possible credential: {relative}")
        if path.suffix == ".py":
            ast.parse(text, filename=str(relative))
        elif path.suffix == ".toml":
            tomllib.loads(text)
        elif path.suffix == ".json":
            json.loads(text)
    for name, data in package.items():
        if name.endswith(".md") and "/templates/" not in name:
            check_links(name, data.decode("utf-8"), package)
    for name in ["AGENTS.md", "CLAUDE.md", "README.md", "LICENSE"]:
        text = (ROOT / name).read_text(encoding="utf-8")
        if name == "AGENTS.md":
            # The managed policy documents this exact check as an instruction.
            text = text.replace("grep -nF 'TO FILL' <pr-body-file>", "the PR placeholder check")
        require(not re.search(r"\{\{[A-Z_]+\}\}|TO FILL|\[year\]|\[fullname\]", text), f"Unresolved project field: {name}")
    for name in ["profiles/hermes-development.md", "docs/INSTALL-CODEX.md", "docs/INSTALL-CLAUDE.md", "docs/package-README.md"]:
        require("v" + release in (ROOT / name).read_text(encoding="utf-8"), f"Release version missing: {name}")
    module = ROOT / "skills/github-workflow/scripts/workflow-context.py"
    spec = importlib.util.spec_from_file_location("workflow_context", module)
    context = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(context)
    # Project holds belong to merge preflight, not to the build/lint gate.
    # Distributed neutral profiles must not propagate a project's dated hold.
    for name in ["profiles/AGENTS.template.md", "profiles/CLAUDE.template.md", "profiles/hermes-development.md"]:
        require(context.scan_text((ROOT / name).read_text(encoding="utf-8")) == 0, f"Unexpected suspension in source profile: {name}")
    print(json.dumps({"source_checks": "passed", "version": release, "package_payload_files": len(package), "runtime_template": project["repository_template_version"]}))


if __name__ == "__main__":
    main()
