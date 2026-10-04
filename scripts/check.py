#!/usr/bin/env python3
"""Check source integrity, portable links and syntax, and screen repository privacy.

The privacy screen rejects machine-specific home paths, common credential
formats and email addresses outside example domains, GitHub noreply,
the Claude and Codex co-author trailers and the GitHub SSH user; in Python files
it reads literal source content and comments, excluding Python syntax quotes.
It does not detect personal names or reconstruct addresses through separate
strings, escape decoding or interpolation.
"""
import ast
import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import tokenize
import tomllib
import unicodedata

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build import payloads, version  # noqa: E402
from check_workflow_commands import check_repository  # noqa: E402


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
# A ref or tag such as feature.1 or rc.2.10: one name label, then numeric labels only.
# No top-level domain is all-numeric (RFC 3696), and a real domain before the numbers
# (gmail.com.1) keeps two alphabetic labels, so it stays screened.
REF_NAME = re.compile(r'[a-z][a-z0-9_-]*(?:\.[0-9]+)+\Z')
# Default branch names after owner/repo@; any other dotless ref needs a `uses:` key.
DEFAULT_BRANCHES = {"main", "master", "trunk", "develop", "head"}
# An OCI image digest: the image name, then @sha256: and 64 hex digits (or sha512, 128).
DIGESTS = {"sha256": re.compile(r':[0-9a-f]{64}(?![0-9A-Za-z])'), "sha512": re.compile(r':[0-9a-f]{128}(?![0-9A-Za-z])')}
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
# A URI scheme and // before the local part mark URL user info.
URI_SCHEME = re.compile(r'(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*:\Z')
# In a YAML file the value of a `uses:` key is an action reference by schema, whatever
# its ref; elsewhere the same text is prose and stays screened.
USES_KEY = re.compile(r'\s*(?:-\s+)?uses:\s+["\x27]?\Z')
YAML_SUFFIXES = (".yml", ".yaml")
# pip-style VCS URLs put a revision after a plain path: git+https://host/owner/repo.git@main.
VCS_SCHEME = re.compile(r'(?<![A-Za-z0-9+.-])(?:git|hg|svn|bzr)\+[a-z]+:\Z')
VCS_PATH = re.compile(r'//[^/?#=&]+(?:/[^/?#=&]+)+\Z')
ACTION_PIN = re.compile(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+@[0-9a-f]{40}\Z')
RELEASE_COMMENT = re.compile(r'v[0-9]+(?:\.[0-9]+){0,2}(?![A-Za-z0-9.])')
CI_USES = re.compile(r'^\s*(?:-\s+)?uses\s*:\s*(.*)$')
CI_USES_KEY = re.compile(r'(?<![A-Za-z0-9_-])["\x27]?uses["\x27]?\s*:')
SETUP_EXAMPLE = re.compile(r'(?<![A-Za-z0-9_./])actions/setup-[A-Za-z0-9_-]+(?:@[A-Za-z0-9._-]+)?')


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


def python_text(text):
    """Blank Python syntax while preserving literal content, comments and physical positions.

    Matrix multiplication and decorators are operators, not addresses. Untokenizable
    source is screened whole.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        return text
    rows = [list(row) for row in io.StringIO(text).readlines()]
    for token in tokens:
        (start_row, start), (end_row, end) = token.start, token.end
        if (
            token.type in (tokenize.NAME, tokenize.OP, tokenize.NUMBER)
            or tokenize.tok_name[token.type] in {"FSTRING_START", "FSTRING_END", "TSTRING_START", "TSTRING_END"}
        ) and start_row == end_row:
            rows[start_row - 1][start:end] = " " * (end - start)
        elif token.type == tokenize.STRING:
            # Only token delimiters are syntax. Quotes inside the literal may
            # belong to an address and must remain visible to the privacy screen.
            quote = next(index for index, char in enumerate(token.string) if char in "'\"")
            width = 3 if token.string[quote:].startswith(token.string[quote] * 3) else 1
            opening = quote + width
            rows[start_row - 1][start:start + opening] = " " * opening
            rows[end_row - 1][end - width:end] = " " * width
    return "".join("".join(row) for row in rows)


def ref_shaped(domain):
    """A revision that cannot be a screened mail domain: dotless, numbered, a version or a SHA."""
    return "." not in domain or bool(REF_NAME.match(domain) or VERSION.match(domain) or COMMIT.match(domain))


def check_emails(relative, text):
    yaml = str(relative).endswith(YAML_SUFFIXES)
    # Split on newlines only, as editors and python_text() count lines; splitlines()
    # also breaks on form feeds and Unicode separators and would shift the reported line.
    for number, line in enumerate(text.split("\n"), 1):
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
            if domain in DIGESTS and DIGESTS[domain].match(line, end):
                continue
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
            last = domain.rpartition(".")[2]
            if (VERSION.match(domain) and not last.isalpha() and not last.startswith("xn--")) \
                    or COMMIT.match(domain) or REF_NAME.match(domain):
                continue  # a Punycode label such as xn--p1ai is a top-level domain, not a pin
            if "/" in local and not local.startswith("/") and (
                    yaml and USES_KEY.fullmatch(before) or "." not in domain and domain in DEFAULT_BRANCHES):
                # A repository reference: any ref after a YAML `uses:` key, or a default branch as
                # in actions/checkout@main. Elsewhere a slash is a valid local-part character before
                # an intranet host and .one may be a real top-level domain, so both stay screened;
                # URL user info keeps its leading // in the local part.
                continue
            if "." not in domain and domain in DIST_TAGS:
                continue  # a package dist-tag such as pkg@latest
            if VCS_PATH.match(local) and VCS_SCHEME.search(before) and ref_shaped(domain):
                continue  # a revision after a plain VCS URL path, not user info
            if local.startswith("//") and URI_SCHEME.search(before):
                local = local[2:]  # ssh://git@github.com/owner/repo.git names git@github.com
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


def operative_ci_steps(lines):
    """Return step spans from a workflow, without parsing arbitrary YAML."""
    steps = []
    for index, line in enumerate(lines):
        match = re.fullmatch(r'( *)steps:\s*(?:#.*)?', line)
        if not match:
            continue
        parent_indent = len(match.group(1))
        step_indent = start = None
        end = index + 1
        while end < len(lines):
            candidate = lines[end]
            if candidate.strip() and not candidate.lstrip().startswith('#'):
                indent = len(candidate) - len(candidate.lstrip(' '))
                if indent <= parent_indent:
                    break
                item = re.match(r'( *)-\s+\S', candidate)
                if item and (step_indent is None or len(item.group(1)) == step_indent):
                    if start is not None:
                        steps.append((start, end, step_indent))
                    step_indent = len(item.group(1))
                    start = end
            end += 1
        if start is not None:
            steps.append((start, end, step_indent))
    return steps


def checkout_has_explicit_credentials(lines, start, end, step_indent):
    """Only a direct `with:` input on this checkout step can disable credentials."""
    with_blocks = []
    property_indent = step_indent + 2
    for index in range(start + 1, end):
        line = lines[index]
        if line.lstrip().startswith('#'):
            continue
        indent = len(line) - len(line.lstrip(' '))
        if indent == property_indent and re.fullmatch(r'\s*with:\s*(?:#.*)?', line):
            with_blocks.append(index)
    if len(with_blocks) != 1:
        return False
    first = with_blocks[0] + 1
    last = first
    while last < end:
        line = lines[last]
        if line.strip() and not line.lstrip().startswith('#'):
            indent = len(line) - len(line.lstrip(' '))
            if indent <= property_indent:
                break
        last += 1
    entries = [
        (len(line) - len(line.lstrip(' ')), line.strip())
        for line in lines[first:last]
        if line.strip() and not line.lstrip().startswith('#')
    ]
    if not entries:
        return False
    input_indent = min(indent for indent, _ in entries)
    credentials = []
    for indent, entry in entries:
        if indent == input_indent:
            match = re.fullmatch(r'persist-credentials:\s*(.*)', entry)
            if match:
                credentials.append(match.group(1).partition('#')[0].strip())
    return credentials == ['false']


def check_ci_actions(ci_name):
    """Guard action pins and each checkout's credential input in one workflow."""
    lines = (ROOT / ci_name).read_text(encoding='utf-8').splitlines()
    steps = operative_ci_steps(lines)
    for index, line in enumerate(lines):
        if line.lstrip().startswith('#'):
            continue
        match = CI_USES.match(line)
        if not match:
            require(not CI_USES_KEY.search(line.partition('#')[0]),
                    f'{ci_name}:{index + 1}: unsupported action uses syntax')
            continue
        value, separator, comment = match.group(1).partition('#')
        ref = value.strip()
        if ref.startswith(('"', "'")) and ref.endswith(ref[0]):
            ref = ref[1:-1]
        location = f'{ci_name}:{index + 1}'
        if ref.startswith('./'):
            continue
        require(bool(ACTION_PIN.fullmatch(ref)),
                f'{location}: non-local action requires a full lowercase 40-hex revision')
        require(bool(separator and RELEASE_COMMENT.match(comment.strip())),
                f'{location}: action pin needs a readable version comment')
        if ref.partition('@')[0].lower() == 'actions/checkout':
            step = next((step for step in steps if step[0] <= index < step[1]), None)
            require(step is not None and checkout_has_explicit_credentials(lines, *step),
                    f'{location}: checkout step needs its own persist-credentials: false input')


def check_distributed_actions():
    """Guard active CI, the exported template, and setup examples adopters copy."""
    check_ci_actions('skills/github-workflow/templates/ci.yml')
    workflows = ROOT / '.github/workflows'
    for path in sorted(workflows.iterdir()):
        if path.suffix in YAML_SUFFIXES and path.is_file():
            check_ci_actions(path.relative_to(ROOT).as_posix())

    for name in ['skills/github-workflow/templates/ci.yml', 'skills/github-workflow/SKILL.md']:
        for number, line in enumerate((ROOT / name).read_text(encoding='utf-8').splitlines(), 1):
            for match in SETUP_EXAMPLE.finditer(line):
                location = f'{name}:{number}'
                require(bool(ACTION_PIN.fullmatch(match.group())),
                        f'{location}: setup example requires a full lowercase 40-hex revision')
                require(bool(re.match(r'\s*#\s*' + RELEASE_COMMENT.pattern, line[match.end():])),
                        f'{location}: setup example pin needs a readable version comment')


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
        relative_path = path.relative_to(ROOT)
        if any(part in {".git", "dist", "__pycache__"} for part in relative_path.parts) or path.name == "TASKS.md":
            continue
        relative = relative_path.as_posix()
        require(not path.is_symlink(), f"Unexpected repository symlink: {relative}")
        if not path.is_file():
            continue
        data = path.read_bytes()
        text = data.decode("utf-8")
        require(not re.search(r"/(?:Users|home)/[A-Za-z0-9_.-]+/|[A-Za-z]:\\Users\\", text), f"Machine-specific path: {relative}")
        check_emails(relative, python_text(text) if path.suffix == ".py" else text)
        require(not re.search(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{24,}|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----)", text), f"Possible credential: {relative}")
        if path.suffix == ".py":
            ast.parse(text, filename=relative)
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
    bootstrap_blocks = []
    for name in ["docs/INSTALL-CODEX.md", "docs/INSTALL-CLAUDE.md"]:
        text = (ROOT / name).read_text(encoding="utf-8")
        blocks = re.findall(
            r"<!-- package-bootstrap:start -->\n```powershell\n(.*?)```\n<!-- package-bootstrap:end -->",
            text, re.DOTALL,
        )
        require(len(blocks) == 1, f"Exactly one authenticated package bootstrap required: {name}")
        bootstrap = blocks[0]
        require(".\\install.py" not in text, f"Unbound extracted installer command: {name}")
        require("$packageName = 'dev-harness-v" + release + "'" in bootstrap,
                f"Bootstrap package version mismatch: {name}")
        authentication = bootstrap.find("if ($actualHash -cne $archiveHashes[0]) { throw")
        execution = bootstrap.find("& $candidateCommand.Source")
        require(authentication >= 0 and execution > authentication,
                f"Archive authentication must precede Python discovery: {name}")
        require("Get-FileHash -InputStream $archiveStream" in bootstrap
                and "ZipArchive]::new($archiveStream" in bootstrap,
                f"Hash and extraction must use the captured archive: {name}")
        require("'dev-harness-verified-' + [guid]::NewGuid()" in bootstrap
                and "'-I'" in bootstrap,
                f"Fresh extraction and isolated Python required: {name}")
        bootstrap_blocks.append(bootstrap)
    require(bootstrap_blocks[0] == bootstrap_blocks[1], "Installation bootstrap differs between guides")
    for name in ["profiles/hermes-development.md", "docs/INSTALL-CODEX.md", "docs/INSTALL-CLAUDE.md", "docs/package-README.md"]:
        require("v" + release in (ROOT / name).read_text(encoding="utf-8"), f"Release version missing: {name}")
    check_distributed_actions()
    module = ROOT / "skills/github-workflow/scripts/workflow-context.py"
    spec = importlib.util.spec_from_file_location("workflow_context", module)
    context = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(context)
    # Project holds belong to merge preflight, not to the build/lint gate.
    # Distributed neutral profiles must not propagate a project's dated hold.
    for name in ["profiles/AGENTS.template.md", "profiles/CLAUDE.template.md", "profiles/hermes-development.md"]:
        require(context.scan_text((ROOT / name).read_text(encoding="utf-8")) == 0, f"Unexpected suspension in source profile: {name}")
    workflow_findings = check_repository(ROOT)
    if workflow_findings:
        raise SystemExit("\n".join(str(finding) for finding in workflow_findings))
    print(json.dumps({"source_checks": "passed", "version": release, "package_payload_files": len(package), "runtime_template": project["repository_template_version"]}))


if __name__ == "__main__":
    main()
