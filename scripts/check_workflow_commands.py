#!/usr/bin/env python3
"""Check executable workflow examples for explicit targets and interpreters.

This intentionally recognizes the Bash and Markdown forms used by the workflow
guide. It is not a general Bash parser. Active templates may be added by their
own check; historical snapshots are never current guidance inputs.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import shlex
import sys


GUIDE = Path("skills/github-workflow")
SHELL_FENCES = {"", "bash", "sh", "shell", "zsh", "console"}
PR_SELECTORS = {"view", "checks", "ready", "merge", "edit"}
RUN_IDS = {"view", "watch", "rerun", "cancel"}
REPO_POSITIONALS = {"view", "edit"}
VALUE_FLAGS = {
    "--repo", "-R", "--json", "--jq", "-q", "--template", "-t",
    "--title", "--body", "-b", "--body-file", "-F", "--head", "-H",
    "--base", "-B", "--search", "-S", "--state", "--limit", "-L",
    "--commit", "--branch", "--match-head-commit", "--job", "-j",
    "--attempt", "--description", "--hostname", "--method", "--interval",
    "--name", "--branch-repo", "--label", "--reviewer", "--assignee",
    "--project", "--milestone", "--author", "--author-email", "--subject",
    "--add-assignee", "--add-label", "--add-project", "--add-reviewer",
    "--remove-assignee", "--remove-label", "--remove-project", "--recover",
    "--default-branch", "--visibility",
    "--add-topic", "--remove-topic", "--homepage", "--created", "--event",
    "--status", "--user", "--workflow", "--app", "--template-file",
}
BOOLEAN_FLAGS = {
    "--draft", "--watch", "--required", "--log-failed", "--squash",
    "--comments", "--enable-squash-merge", "--checkout", "--web",
    "--failed", "--force", "--delete-branch", "--admin", "--auto",
    "--disable-auto", "--merge", "--rebase", "--undo", "--log",
    "--verbose", "--exit-status", "--compact", "--debug", "--fail-fast",
    "--dry-run", "--editor", "--fill", "--fill-first", "--fill-verbose",
    "--no-maintainer-edit", "--remove-milestone", "--all", "--help",
    "--enable-auto-merge", "--enable-discussions", "--enable-issues",
    "--enable-merge-commit", "--enable-projects", "--enable-rebase-merge",
    "--enable-secret-scanning", "--enable-secret-scanning-push-protection",
    "--enable-wiki", "--allow-forking", "--allow-update-branch",
    "--delete-branch-on-merge", "--accept-visibility-change-consequences",
    "--<method>",
}
CONTROL = {";", ";;", ";&", ";;&", "&&", "||", "|", "|&", "&", "(", ")"}
ORIGIN_REPO = re.compile(r"\$(?:workflow_host|\{workflow_host\})/\$(?:workflow_repo|\{workflow_repo\})\Z")
PY_SWITCHES = {"-B", "-u", "-I", "-E", "-s", "-S", "-O", "-OO", "-b", "-bb", "-q", "-v", "-x", "-P", "-i", "-R", "-d", "-t"}
PY_VALUE_SWITCHES = {"-W", "-X"}
PROBE = "if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; assert sys.version_info >= (3,8)' 2>/dev/null; then"
PY_LAUNCHER_PROBE = "elif command -v py >/dev/null 2>&1 && py -3 -c 'import sys; assert sys.version_info >= (3,8)' 2>/dev/null; then"
PYTHON_PROBE = "elif command -v python >/dev/null 2>&1 && python -c 'import sys; assert sys.version_info >= (3,8)' 2>/dev/null; then"
INLINE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")
SHORTHAND = re.compile(r'gh\s+(?:pr|run|issue|repo)\s+\w+(?:[|/]\w+)+(?:\s+--repo\s+"\$workflow_host/\$workflow_repo")?')
REDIRECTION = re.compile(r"^(?:&(?:>>|>)|\d*(?:<<<|<<-|<<|<>|<&|>>|>\||>&|<|>))(.*)$")


@dataclass(frozen=True)
class Diagnostic:
    file: str
    line: int
    message: str

    def __str__(self):
        return f"{self.file}:{self.line}: {self.message}"


def _tokens(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    raw = list(lexer)
    tokens = []
    index = 0
    while index < len(raw):
        word = raw[index]
        # shlex separates Bash's & and | punctuation. Rejoin only known
        # redirection operators, preserving adjacent && and pipelines.
        if word == "&" and index + 1 < len(raw) and raw[index + 1].startswith(">"):
            tokens.append("&" + raw[index + 1])
            index += 2
        elif re.fullmatch(r"\d*[<>]", word) and index + 1 < len(raw) and raw[index + 1] == "&":
            if index + 2 < len(raw) and raw[index + 2] not in CONTROL:
                tokens.append(word + "&" + raw[index + 2])
                index += 3
            else:
                tokens.append(word + "&")
                index += 2
        elif re.fullmatch(r"\d*>", word) and index + 1 < len(raw) and raw[index + 1] == "|":
            tokens.append(word + "|")
            index += 2
        else:
            tokens.append(word)
            index += 1
    return tokens


def _command_start(tokens, index):
    prefix, incomplete = _without_redirections(tokens[:index])
    if incomplete:
        return False
    if not prefix or prefix[-1] in CONTROL | {"if", "elif", "else", "then", "while", "until", "!", "do", "env", "time", "command", "$"}:
        return True
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*=.*", prefix[-1]))


def _arguments(tokens, start):
    result = []
    for token in tokens[start:]:
        if token in CONTROL:
            break
        result.append(token)
    return result


def _redirect(word):
    """Return whether a token is shell redirection and needs a next word."""
    if re.fullmatch(r"<[^<>\s]+>", word):  # documented <pr> placeholders
        return False, False
    match = REDIRECTION.match(word)
    if match is None:
        return False, False
    return True, match.group(1) == ""


def _without_redirections(args):
    """Remove shell redirections before classifying actual program operands."""
    operands = []
    incomplete = False
    index = 0
    while index < len(args):
        redirection, next_word = _redirect(args[index])
        if redirection:
            if next_word and (index + 1 >= len(args) or args[index + 1] in CONTROL or _redirect(args[index + 1])[0]):
                incomplete = True
            index += 2 if next_word else 1
        else:
            operands.append(args[index])
            index += 1
    return operands, incomplete


def _options(args):
    """Return positional words, valued flags, missing values and unknown flags."""
    positional = []
    valued = {}
    missing = set()
    unknown = set()
    args, incomplete = _without_redirections(args)
    options_done = False
    index = 0
    while index < len(args):
        word = args[index]
        if word == "--" and not options_done:
            options_done = True
            index += 1
            continue
        if options_done:
            positional.append(word)
            index += 1
            continue
        flag, equal, attached = word.partition("=")
        if flag in VALUE_FLAGS:
            if equal:
                if attached.strip() and not attached.startswith("-"):
                    valued[flag] = attached
                else:
                    missing.add(flag)
            elif index + 1 < len(args) and args[index + 1].strip() and not _redirect(args[index + 1])[0] and not args[index + 1].startswith("-"):
                index += 1
                valued[flag] = args[index]
            else:
                missing.add(flag)
        elif flag in BOOLEAN_FLAGS and not equal:
            pass
        elif word.startswith("-"):
            # An unknown flag may consume the next word. Never let that word
            # silently satisfy a selector/ID check.
            unknown.add(flag)
        elif not word.startswith("-"):
            positional.append(word)
        index += 1
    return positional, valued, missing, unknown, incomplete


def _direct_python(tail):
    tail, incomplete = _without_redirections(tail)
    if incomplete:
        return False, True
    index = 0
    while index < len(tail):
        word = tail[index]
        if word in PY_SWITCHES:
            index += 1
        elif word in PY_VALUE_SWITCHES:
            index += 2
        elif (word.startswith("-X") or word.startswith("-W")) and len(word) > 2:
            index += 1
        elif word in {"-c", "-"} or word.endswith(".py"):
            return True, False
        elif word in {"-m", "-V", "-VV", "--version", "-h", "--help"}:
            return False, False
        elif word.startswith("-"):
            return False, True
        else:
            return False, False
    return False, False


def _analyze(command, file, line, *, inline=False, probe=False, origin_comparison=False):
    if inline and SHORTHAND.fullmatch(command):
        return []
    try:
        words = _tokens(command)
    except ValueError as error:
        return [Diagnostic(file, line, f"invalid shell example: {error}")]

    findings = []
    for index, word in enumerate(words):
        if word not in {"gh", "python3"} or not _command_start(words, index):
            continue
        tail = _arguments(words, index + 1)
        if word == "python3":
            direct, unsupported = _direct_python(tail)
            if direct and not probe:
                findings.append(Diagnostic(file, line, "direct python3 invocation; use the selected python_cmd array"))
            elif unsupported:
                findings.append(Diagnostic(file, line, "unsupported python3 invocation; cannot verify interpreter usage"))
            continue

        if len(tail) < 2 or tail[0] not in {"pr", "run", "issue", "repo"}:
            continue
        family, subcommand = tail[:2]
        if "/" in subcommand or "|" in subcommand:
            continue
        positional, valued, missing, unknown, incomplete = _options(tail[2:])
        if incomplete:
            findings.append(Diagnostic(file, line, "incomplete shell redirection in gh command"))
        for flag in sorted(unknown):
            findings.append(Diagnostic(file, line, f"unsupported gh option {flag}; cannot verify command operands"))
        if "-R" in valued or "-R" in missing:
            findings.append(Diagnostic(file, line, "gh repository alias -R is unsupported; use only the verified --repo"))
        if any(not value.strip() for value in positional):
            findings.append(Diagnostic(file, line, "empty gh operand cannot select a PR or run"))
        if family in {"pr", "run", "issue"}:
            repository = valued.get("--repo")
            if repository is None:
                findings.append(Diagnostic(file, line, f"gh {family} {subcommand} needs a valued --repo"))
            elif not ORIGIN_REPO.fullmatch(repository):
                findings.append(Diagnostic(file, line, f"gh {family} {subcommand} --repo must target the verified origin"))
        if family == "pr" and subcommand in PR_SELECTORS and not positional:
            findings.append(Diagnostic(file, line, f"gh pr {subcommand} needs an explicit PR selector"))
        if family == "run" and subcommand in RUN_IDS and not positional:
            findings.append(Diagnostic(file, line, f"gh run {subcommand} needs an explicit run ID"))
        if family == "repo" and subcommand in REPO_POSITIONALS and not any(ORIGIN_REPO.fullmatch(value) for value in positional) and not origin_comparison:
            findings.append(Diagnostic(file, line, f"gh repo {subcommand} needs an explicit repository"))
        if "--match-head-commit" in missing:
            findings.append(Diagnostic(file, line, "--match-head-commit needs a commit value"))
    return findings


def _origin_comparison(file, heading, line, snippet):
    return (
        file == "skills/github-workflow/SKILL.md"
        and heading == "Bind GitHub operations to origin"
        and snippet == "gh repo view --json nameWithOwner,url,defaultBranchRef"
        and "It compares origin fetch/push URLs, implicit `" in line
        and "` and an explicit repository read" in line
    )


def _interpreter_probe(lines, number):
    """Recognize the actual three-way probe, not its section or a loose -c."""
    following = [line.strip() for line in lines[number:number + 9]]
    return (
        len(following) >= 9
        and following[:6] == [
            "python_cmd=(python3)", PY_LAUNCHER_PROBE,
            "python_cmd=(py -3)", PYTHON_PROBE,
            "python_cmd=(python)", "else",
        ]
        and "exit 2" in following[6:8]
        and following[8] == "fi"
    )


def scan_document(file, text):
    """Check a Markdown document and return deterministic file:line findings."""
    lines = text.splitlines()
    findings = []
    fence_mark = None
    shell_fence = False
    heading = ""
    pending = ""
    pending_line = 0

    for number, source in enumerate(lines, 1):
        match = FENCE.match(source)
        if match:
            marker, info = match.groups()
            if fence_mark is None:
                fence_mark = marker
                language = info.strip().split(maxsplit=1)[0].lower() if info.strip() else ""
                shell_fence = language in SHELL_FENCES
            elif marker[0] == fence_mark[0] and len(marker) >= len(fence_mark) and not info.strip():
                if pending:
                    findings.extend(_analyze(pending, file, pending_line))
                    pending = ""
                fence_mark = None
                shell_fence = False
            continue
        if fence_mark is not None:
            if not shell_fence:
                continue
            stripped = source.rstrip()
            if pending:
                pending += stripped.lstrip()
            else:
                pending = stripped
                pending_line = number
            if pending.endswith("\\"):
                pending = pending[:-1] + " "
                continue
            is_probe = source.strip() == PROBE and _interpreter_probe(lines, number)
            findings.extend(_analyze(pending, file, pending_line, probe=is_probe))
            pending = ""
            continue

        heading_match = HEADING.match(source)
        if heading_match:
            heading = heading_match.group(1)
        for inline_match in INLINE.finditer(source):
            snippet = inline_match.group(1).strip()
            findings.extend(_analyze(
                snippet, file, number, inline=True,
                origin_comparison=_origin_comparison(file, heading, source, snippet),
            ))
        if re.match(r"^\s*(?:gh|python3)\s+", source):
            findings.extend(_analyze(source, file, number))
    if pending:
        findings.extend(_analyze(pending, file, pending_line))
    return findings


def check_repository(root):
    """Scan only active workflow guidance, never templates or history."""
    root = Path(root)
    skill = GUIDE / "SKILL.md"
    references = GUIDE / "references"
    if not (root / references).is_dir():
        raise FileNotFoundError(f"workflow references directory missing: {root / references}")
    files = [skill, *(path.relative_to(root) for path in sorted((root / references).glob("*.md")))]
    return [finding for path in files for finding in scan_document(path.as_posix(), (root / path).read_text(encoding="utf-8"))]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1], help="repository root")
    args = parser.parse_args(argv)
    try:
        findings = check_repository(args.root)
    except OSError as error:
        print(f"workflow command check could not read guidance: {error}", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        return 1
    print("workflow command examples: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
