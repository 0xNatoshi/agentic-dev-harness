#!/usr/bin/env python3
"""Check source integrity, portable links and syntax, and screen repository privacy.

The privacy screen rejects machine-specific home paths, common credential
formats and email addresses outside example domains, GitHub noreply,
the Claude and Codex co-author trailers and the GitHub SSH user. It does not detect
personal names or obfuscated addresses.
"""
import ast
import importlib.util
import json
from pathlib import Path
import re
import sys
import tomllib
import unicodedata

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build import payloads, version  # noqa: E402


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


# The domain is every dotted label after @: letters, combining marks and digits in
# any script, and hyphens, with IDNA's ideographic and full-width dots as separators.
# A neutral prefix such as example.com2 therefore never passes as example.com, and a
# final sentence period is not a label.
DOTS = ".\u3002\uff0e\uff61"
IDNA_DOTS = str.maketrans(DOTS[1:], "...")
# Version pins such as action@v4.2.2 or pkg@1.2.3-beta.1 are semver-shaped, and their
# last label is never an alphabetic top-level domain.
VERSION = re.compile(r'v?\d+(?:\.\d+)*(?:-[0-9a-z-]+(?:\.[0-9a-z-]+)*)?\Z')
# A single-label pin such as actions/checkout@<full commit SHA>.
COMMIT = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
DIST_TAGS = {"latest", "next"}
# RFC 5321 address literals: IPv4, or a tag such as IPv6 followed by dcontent.
ADDRESS_LITERAL = re.compile(r'\[(?:[0-9.]+|[A-Za-z0-9-]*[A-Za-z0-9]:[!-Z^-~]+)\]')
# The local part is the token before @, including RFC quoted strings and
# <placeholder> segments, so punctuation or quoting cannot hide an address;
# a quoted string never spans whitespace, @ or commas, so `"a", "b@c.test"` reads as b.
# Only characters that cannot appear unquoted in a local part end it. The domain
# decides; only exact service addresses are neutral. RFC 5321 caps local parts
# at 64 octets, so a bounded look-back keeps long lines linear.
LOCAL = re.compile(r'(?:"(?:[^"\\\s@,]|\\.)*"|<[^<>@\s]*>|[^\s<>()\[\],;:"@])*\Z')
LOOK_BACK = 256
# Markdown emphasis, code spans, quotes and braces are valid local-part
# characters too; they wrap the address only when mirrored right after it.
WRAPPERS = "_*~`'{"
CLOSING = str.maketrans("{", "}")
# Reserved for documentation and testing (RFC 2606, RFC 6761), subdomains included.
RESERVED_DOMAINS = ("example.com", "example.net", "example.org", "example", "invalid", "test", "localhost")
NEUTRAL_DOMAINS = {"users.noreply.github.com"}
NEUTRAL_ADDRESSES = {"noreply@anthropic.com", "codex@openai.com", "git@github.com"}


def label_char(char):
    return char == "-" or unicodedata.category(char)[0] in "LMN"


def domain_end(line, at):
    """End of the domain or address literal after the @ at index at, or None without one.

    Single-label domains such as intranet hosts are valid mail domains too.
    """
    literal = ADDRESS_LITERAL.match(line, at + 1)
    if literal:
        return literal.end()
    index = end = at + 1
    labels = 0
    while True:
        start = index
        while index < len(line) and label_char(line[index]):
            index += 1
        if index == start:
            break
        labels += 1
        end = index
        if index + 1 < len(line) and line[index] in DOTS and label_char(line[index + 1]):
            index += 1
        else:
            break
    return end if labels else None


def neutral_domain(domain):
    return domain in NEUTRAL_DOMAINS or any(
        domain == reserved or domain.endswith("." + reserved) for reserved in RESERVED_DOMAINS)


def check_emails(relative, text):
    for number, line in enumerate(text.splitlines(), 1):
        for match in re.finditer("@", line):
            end = domain_end(line, match.start())
            if end is None:
                continue
            window = line[max(0, match.start() - LOOK_BACK):match.start()]
            token = LOCAL.search(window).group(0)
            before = window[:len(window) - len(token)]
            local = token.lstrip(WRAPPERS)
            wrapper = token[:len(token) - len(local)]
            domain = line[match.start() + 1:end].translate(IDNA_DOTS).lower()
            if not local and "." not in domain:
                # A single label with no local part is a mention such as `@codex review`.
                continue
            if not line[end:].startswith(wrapper[::-1].translate(CLOSING)):
                local = token
            elif not local and (not before or before[-1] in " \t([") and (
                    not wrapper or wrapper == "`" and domain.endswith(".md")):
                # No local part at all is an @mention or import; a wrapper-only
                # token is a local part too, except a code span around a Markdown
                # import such as `@AGENTS.md`.
                continue
            if (VERSION.match(domain) and not domain.rpartition(".")[2].isalpha()) or COMMIT.match(domain):
                continue
            if "." not in domain and (domain in DIST_TAGS or "/" in local and not local.startswith("/")):
                # A repository or package reference such as actions/checkout@main or pkg@latest;
                # URL user info keeps its leading // in the local part and stays screened.
                continue
            neutral = neutral_domain(domain) or f"{local.lower()}@{domain}" in NEUTRAL_ADDRESSES
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
