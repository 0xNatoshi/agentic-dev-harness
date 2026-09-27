#!/usr/bin/env python3
"""Check executable workflow examples for explicit targets and interpreters.

This intentionally recognizes the Bash and Markdown forms used by the workflow
guide and active project instructions. It is not a general Bash parser: dynamic
evaluation, aliases/functions, indirect executables and substitutions inside
quoted tokens need manual review. Unsupported coprocesses and ambiguous
here-document headers fail explicitly. Only standalone cat and selected-Python
commands with literal quoted delimiters have recognized body boundaries; their
bodies are not shell commands. Historical snapshots are never current inputs.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import shlex
import sys


GUIDE = Path("skills/github-workflow")
SHELL_FENCES = {"", "bash", "sh", "shell", "zsh", "console"}
# Recognized command options from gh 2.86 help. Unknown commands/options fail
# closed until their documented contract is added. Inherited flags are added
# by _options; the merge-method placeholder is guidance syntax, not a CLI flag.
# Each tuple holds valued flags, Boolean flags and (minimum, maximum) operands.
# Workflow minima require explicit PR/run/repository targets even where gh can
# infer one. None preserves the documented variadic issue-edit form.
GH_OPTIONS = {
    'pr create': (
        '-a --assignee -B --base -b --body -F --body-file -H --head -l --label -m --milestone -p '
        '--project --recover -r --reviewer -T --template -t --title',
        '-d --draft --dry-run -e --editor -f --fill --fill-first --fill-verbose '
        '--no-maintainer-edit -w --web',
        (0, 0),
    ),
    'pr list': (
        '--app -a --assignee -A --author -B --base -H --head -q --jq --json -l --label -L --limit '
        '-S --search -s --state -t --template',
        '-d --draft -w --web',
        (0, 0),
    ),
    'pr status': (
        '-q --jq --json -t --template',
        '-c --conflict-status',
        (0, 0),
    ),
    'pr checkout': (
        '-b --branch',
        '--detach -f --force --recurse-submodules',
        (1, 1),
    ),
    'pr checks': (
        '-i --interval -q --jq --json -t --template',
        '--fail-fast --required --watch -w --web',
        (1, 1),
    ),
    'pr close': (
        '-c --comment',
        '-d --delete-branch',
        (1, 1),
    ),
    'pr comment': (
        '-b --body -F --body-file',
        '--create-if-none --delete-last --edit-last -e --editor -w --web --yes',
        (1, 1),
    ),
    'pr diff': (
        '--color',
        '--name-only --patch -w --web',
        (1, 1),
    ),
    'pr edit': (
        '--add-assignee --add-label --add-project --add-reviewer -B --base -b --body -F '
        '--body-file -m --milestone --remove-assignee --remove-label --remove-project '
        '--remove-reviewer -t --title',
        '--remove-milestone',
        (1, 1),
    ),
    'pr lock': (
        '-r --reason',
        '',
        (1, 1),
    ),
    'pr merge': (
        '-A --author-email -b --body -F --body-file --match-head-commit -t --subject',
        '--admin --auto -d --delete-branch --disable-auto -m --merge -r --rebase -s --squash '
        '--<method>',
        (1, 1),
    ),
    'pr ready': (
        '',
        '--undo',
        (1, 1),
    ),
    'pr reopen': (
        '-c --comment',
        '',
        (1, 1),
    ),
    'pr revert': (
        '-b --body -F --body-file -t --title',
        '-d --draft',
        (1, 1),
    ),
    'pr review': (
        '-b --body -F --body-file',
        '-a --approve -c --comment -r --request-changes',
        (1, 1),
    ),
    'pr unlock': (
        '',
        '',
        (1, 1),
    ),
    'pr update-branch': (
        '',
        '--rebase',
        (1, 1),
    ),
    'pr view': (
        '-q --jq --json -t --template',
        '-c --comments -w --web',
        (1, 1),
    ),
    'run cancel': (
        '',
        '--force',
        (1, 1),
    ),
    'run delete': (
        '',
        '',
        (1, 1),
    ),
    'run download': (
        '-D --dir -n --name -p --pattern',
        '',
        (1, 1),
    ),
    'run list': (
        '-b --branch -c --commit --created -e --event -q --jq --json -L --limit -s --status -t '
        '--template -u --user -w --workflow',
        '-a --all',
        (0, 0),
    ),
    'run rerun': (
        '-j --job',
        '-d --debug --failed',
        (1, 1),
    ),
    'run view': (
        '-a --attempt -j --job -q --jq --json -t --template',
        '--exit-status --log --log-failed -v --verbose -w --web',
        (1, 1),
    ),
    'run watch': (
        '-i --interval',
        '--compact --exit-status',
        (1, 1),
    ),
    'issue close': (
        '-c --comment -r --reason',
        '',
        (1, 1),
    ),
    'issue comment': (
        '-b --body -F --body-file',
        '--create-if-none --delete-last --edit-last -e --editor -w --web --yes',
        (1, 1),
    ),
    'issue create': (
        '-a --assignee -b --body -F --body-file -l --label -m --milestone -p --project --recover '
        '-T --template -t --title',
        '-e --editor -w --web',
        (0, 0),
    ),
    'issue delete': (
        '',
        '--yes',
        (1, 1),
    ),
    'issue develop': (
        '-b --base --branch-repo -n --name',
        '-c --checkout -l --list',
        (1, 1),
    ),
    'issue edit': (
        '--add-assignee --add-label --add-project -b --body -F --body-file -m --milestone '
        '--remove-assignee --remove-label --remove-project -t --title',
        '--remove-milestone',
        (1, None),
    ),
    'issue list': (
        '--app -a --assignee -A --author -q --jq --json -l --label -L --limit --mention -m '
        '--milestone -S --search -s --state -t --template',
        '-w --web',
        (0, 0),
    ),
    'issue lock': (
        '-r --reason',
        '',
        (1, 1),
    ),
    'issue reopen': (
        '-c --comment',
        '',
        (1, 1),
    ),
    'issue status': (
        '-q --jq --json -t --template',
        '',
        (0, 0),
    ),
    'issue transfer': (
        '',
        '',
        (2, 2),
    ),
    'issue unlock': (
        '',
        '',
        (1, 1),
    ),
    'issue view': (
        '-q --jq --json -t --template',
        '-c --comments -w --web',
        (1, 1),
    ),
    'repo view': (
        '-b --branch -q --jq --json -t --template',
        '-w --web',
        (1, 1),
    ),
    'repo edit': (
        '--add-topic --default-branch -d --description -h --homepage --remove-topic --visibility',
        '--accept-visibility-change-consequences --allow-forking --allow-update-branch '
        '--delete-branch-on-merge --enable-advanced-security --enable-auto-merge '
        '--enable-discussions --enable-issues --enable-merge-commit --enable-projects '
        '--enable-rebase-merge --enable-secret-scanning --enable-secret-scanning-push-protection '
        '--enable-squash-merge --enable-wiki --template',
        (1, 1),
    ),
    'repo set-default': (
        '',
        '-u --unset -v --view',
        (1, 1),
    ),
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
PROJECT_COMMAND_ROW = re.compile(r"\s*\|\s*(?:Install|Build|Lint|Typecheck|Tests)\s*\|\s*`([^`]+)`\s*\|\s*")
PLACEHOLDER = r"<[A-Za-z_][A-Za-z_0-9-]*>"
DOCUMENTED_OPERAND = re.compile(
    rf"{PLACEHOLDER}(?:(?:[/-]{PLACEHOLDER})+|\([A-Za-z_0-9-]+\): [^<>]+)?\Z"
)


@dataclass(frozen=True)
class Diagnostic:
    file: str
    line: int
    message: str

    def __str__(self):
        return f"{self.file}:{self.line}: {self.message}"


@dataclass(frozen=True)
class ShellBoundaries:
    visible: str
    has_here_operator: bool
    continuation: bool
    error: str


def _shell_boundaries(command):
    """Own the boundary syntax; leave command tokenization and headers bounded."""
    # Dollar quotes, arithmetic, backticks and execution/quotes inside parameter
    # expansions need a grammar outside this checker. Case arms inside
    # substitutions can close with unmatched ')': even an unquoted literal case argument makes
    # boundary-sensitive forms ambiguous here. Quote that literal to disambiguate.
    # Process substitutions and extended globs also continue a word after ')';
    # reject their active forms instead of treating them as ordinary groups.
    quote = ""
    escaped = False
    word_started = False
    # A substitution resumes its outer word; a shell group ends at control
    # punctuation. Saved quotes also cover substitutions inside double quotes.
    frames = []
    has_here_operator = False
    case_in_substitution = False
    index = 0

    def unsupported(reason):
        return ShellBoundaries(command, has_here_operator, False, reason)

    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            word_started = True
            index += 1
            continue
        if quote == "'":
            if char == "'":
                quote = ""
            index += 1
            continue
        if char == "\\":
            escaped = True
            word_started = True
            index += 1
            continue
        if char == '"' and quote == '"':
            quote = ""
            index += 1
            continue
        if not quote and char in "'\"":
            quote = char
            word_started = True
            index += 1
            continue
        if not quote and command.startswith("$'", index):
            return unsupported("unsupported ANSI-C quote boundary; use ordinary literal quotes")
        if not quote and command.startswith('$"', index):
            return unsupported("unsupported locale quote boundary; use ordinary double quotes")
        if command.startswith("$((", index):
            return unsupported("unsupported arithmetic expansion boundary")
        if command.startswith("$(", index):
            frames.append(("substitution", quote))
            quote = ""
            word_started = False
            index += 2
            continue
        if command.startswith("${", index):
            # Parameter data cannot start comments or redirects. Nested
            # execution and quoting require a grammar outside this checker.
            depth = 1
            end = index + 2
            while end < len(command) and depth:
                if command.startswith("$(", end) or command[end] in ("'", '"', "`"):
                    return unsupported("unsupported complex parameter expansion boundary")
                if command[end] == "\\":
                    end += 2
                    continue
                if command[end] == "{":
                    depth += 1
                elif command[end] == "}":
                    depth -= 1
                end += 1
            if depth:
                return unsupported("unfinished parameter expansion boundary")
            word_started = True
            index = end
            continue
        if char == "`":
            return unsupported("unsupported backtick substitution boundary")
        if quote == '"':
            index += 1
            continue
        if command.startswith(("<(", ">("), index):
            return unsupported("unsupported process substitution boundary; split the example")
        if char in "@!?*+" and command[index + 1:index + 2] == "(":
            return unsupported("unsupported extended glob boundary; use a literal argument")
        if not word_started and command.startswith("case", index) and (
            index + 4 == len(command) or command[index + 4] in " \t" or
            command[index + 4] in ";&|<>()"
        ) and any(kind == "substitution" for kind, _ in frames):
            case_in_substitution = True
        if char == "#":
            if case_in_substitution:
                return unsupported("unsupported case substitution comment boundary; quote literal case arguments or split the example")
            if not word_started:
                if frames:
                    return unsupported("unsupported comment inside unfinished shell group")
                return ShellBoundaries(command[:index], has_here_operator, False, "")
        # Bash blanks are ASCII space/tab; Unicode whitespace is word data.
        if char in " \t":
            word_started = False
        elif char in ";&|<>":
            if char == "<" and command.startswith("<<", index) and (
                index == 0 or command[index - 1] != "<"
            ) and not command.startswith("<<<", index):
                if case_in_substitution:
                    return unsupported("unsupported case substitution here-document boundary; quote literal case arguments or split the example")
                has_here_operator = True
            word_started = False
        elif char == "(":
            frames.append(("group", quote))
            word_started = False
        elif char == ")":
            if frames:
                kind, quote = frames.pop()
                word_started = kind == "substitution"
            else:
                word_started = False
        else:
            word_started = True
        index += 1
    if case_in_substitution and escaped:
        return unsupported("unsupported case substitution continuation boundary; quote literal case arguments or split the example")
    if any(kind == "substitution" for kind, _ in frames) and not escaped:
        return unsupported("unfinished command substitution boundary")
    return ShellBoundaries(command, has_here_operator, escaped, "")


def _tokens(command):
    boundaries = _shell_boundaries(command)
    if boundaries.error:
        raise ValueError(boundaries.error)
    lexer = shlex.shlex(boundaries.visible, posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    raw = []
    for word in lexer:
        # shlex groups adjacent punctuation, such as ');' or ')&&'. Keep
        # parentheses separate so array frames close and command boundaries
        # remain visible to every consumer of this token stream.
        if word and set(word) <= set(";&|()"):
            raw.extend(re.findall(r"[()]|[;&|]+", word))
        else:
            raw.append(word)
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


def _execution_prefix(prefix):
    """Consume supported shell introducers and wrappers only before execution."""
    prefix, incomplete = _without_redirections(prefix)
    if incomplete:
        return False, None

    offset = 0
    while offset < len(prefix):
        word = prefix[offset]
        offset += 1
        if word in {"if", "elif", "else", "then", "while", "until", "!", "do", "{"} or re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*\+?=.*", word):
            continue
        if word not in {"exec", "command", "env", "time"}:
            return False, None
        wrapper = word
        while offset < len(prefix) and prefix[offset].startswith("-"):
            option = prefix[offset]
            offset += 1
            if option == "--":
                break
            if wrapper == "command" and re.fullmatch(r"-[pVv]+", option):
                if "v" in option or "V" in option:
                    return False, None
                continue
            if wrapper == "exec":
                if re.fullmatch(r"-[cl]+", option):
                    continue
                match = re.fullmatch(r"-[cl]*a(.*)", option)
                if match:
                    if not match.group(1):
                        offset += 1
                    continue
            if wrapper == "env":
                if option in {"-", "-i", "--ignore-environment"}:
                    continue
                if option in {"-u", "--unset", "-C", "--chdir"}:
                    offset += 1
                    continue
                if re.fullmatch(r"(?:-[uC].+|--(?:unset|chdir)=.+)", option):
                    continue
            if wrapper == "time" and option == "-p":
                continue
            return False, f"unsupported execution prefix {wrapper} {option}; cannot identify command operands"
        if offset > len(prefix):
            # The candidate itself is a wrapper option's value, not a command.
            return False, None
    return True, None

def _command_start(tokens, index):
    """Return (executable position, unsupported prefix) for a visible word."""
    frames = []
    prefix = []
    array = False
    case_pattern = False
    for word in tokens[:index]:
        if word == "(":
            child_array = bool(prefix and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*\+?=", prefix[-1]))
            pattern_group = case_pattern and not (prefix and prefix[-1].endswith("$"))
            frames.append((prefix, array, child_array, case_pattern, pattern_group))
            prefix, array = [], child_array
            case_pattern = pattern_group
        elif word == ")" and case_pattern:
            # The optional '(' of a case arm delimits a pattern, not a
            # subshell. An unparenthesized arm must not close an outer frame.
            if frames and frames[-1][4]:
                _, array, _, _, _ = frames.pop()
            prefix, case_pattern = [], False
        elif word == ")" and frames:
            prefix, array, child_array, case_pattern, _ = frames.pop()
            # A substitution or array is one word in the enclosing command;
            # its closing parenthesis cannot promote a later argument to a
            # new executable. A subshell also needs a separator before one.
            if prefix and (child_array or prefix[-1].endswith("$")):
                prefix[-1] += "()"
            else:
                prefix.append("()")
        elif word == "|" and case_pattern:
            prefix.append(word)
        elif word in CONTROL:
            # An unmatched ')' remains the boundary of a case-pattern arm.
            prefix = []
            case_pattern = word in {";;", ";&", ";;&"}
        else:
            prefix.append(word)
            if word == "esac":
                case_pattern = False
            elif len(prefix) >= 3 and prefix[-3] == "case" and word == "in" and _execution_prefix(prefix[:-3])[0]:
                case_pattern = True
    if array or case_pattern:
        return False, None
    return _execution_prefix(prefix)


def _arguments(tokens, start):
    result = []
    for token in tokens[start:]:
        if token in CONTROL:
            break
        result.append(token)
    return result


def _redirect(word):
    """Return whether a token is shell redirection and needs a next word."""
    # Accept the documented placeholder, branch-name and commit-title shapes.
    # Paths inside <...> or literal suffixes can be actual shell redirections.
    if DOCUMENTED_OPERAND.fullmatch(word):
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


def _option_value(flag, value):
    return (
        bool(value.strip())
        and (not value.startswith("-") or flag in {"--body-file", "-F"} and value == "-")
        # After '=' a leading redirect leaves the option value empty. A digit
        # there is already part of the value, not a file-descriptor prefix.
        and not (value.startswith(("<", ">")) and _redirect(value)[0])
    )


def _options(args, command, *, extra_boolean_flags=()):
    """Return operands, option values/switches and malformed-input evidence."""
    value_options, boolean_options, _ = GH_OPTIONS[command]
    value_flags = set(value_options.split())
    boolean_flags = set(boolean_options.split()) | {"--help"} | set(extra_boolean_flags)
    if not command.startswith("repo "):
        value_flags.update({"--repo", "-R"})
    positional = []
    valued = {}
    switches = set()
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
        if flag in value_flags:
            if equal:
                if _option_value(flag, attached):
                    valued[flag] = attached
                else:
                    missing.add(flag)
            elif index + 1 < len(args) and _option_value(flag, args[index + 1]) and not _redirect(args[index + 1])[0]:
                index += 1
                valued[flag] = args[index]
            else:
                missing.add(flag)
        elif flag in boolean_flags and not equal:
            switches.add(flag)
        elif word.startswith("-"):
            # An unknown flag may consume the next word. Never let that word
            # silently satisfy a selector/ID check.
            unknown.add(flag)
        elif not word.startswith("-"):
            positional.append(word)
        index += 1
    return positional, valued, switches, missing, unknown, incomplete


def _direct_python(tail, *, windows_launcher=False):
    tail, incomplete = _without_redirections(tail)
    if incomplete:
        return False, True
    index = 0
    while index < len(tail):
        word = tail[index]
        if word in PY_SWITCHES or windows_launcher and word == "-3":
            index += 1
        elif word in PY_VALUE_SWITCHES:
            if index + 1 >= len(tail):
                return False, True
            index += 2
        elif (word.startswith("-X") or word.startswith("-W")) and len(word) > 2:
            index += 1
        elif word in {"-c", "-"} or word.startswith("-m") or word.endswith(".py"):
            return True, False
        elif word in {"-V", "-VV", "--version", "-h", "--help"}:
            return False, False
        elif word.startswith("-"):
            return False, True
        else:
            # Python accepts extensionless scripts, directories and ZIP apps.
            return True, False
    return True, False


def _analyze(command, file, line, *, inline=False, probe=False, origin_comparison=False, project_python=False):
    # Bare executable names in prose are references, like gh family shorthand.
    # Their standalone shell-fence/command-line form is executable and checked.
    if inline and (SHORTHAND.fullmatch(command) or command in {"python3", "python", "py", "py -3", "coproc"}):
        return []
    try:
        words = _tokens(command)
    except ValueError as error:
        return [Diagnostic(file, line, f"invalid shell example: {error}")]

    findings = []
    for index, word in enumerate(words):
        if word not in {"gh", "python3", "python", "py", "coproc"}:
            continue
        executable, prefix_error = _command_start(words, index)
        if prefix_error:
            findings.append(Diagnostic(file, line, prefix_error))
        if not executable:
            continue
        if word == "coproc":
            findings.append(Diagnostic(file, line, "unsupported coprocess execution; use an explicit foreground command"))
            continue
        tail = _arguments(words, index + 1)
        if word != "gh":
            direct, unsupported = _direct_python(tail, windows_launcher=word == "py")
            if direct and not (probe or project_python):
                findings.append(Diagnostic(file, line, f"direct {word} invocation; use the selected python_cmd array"))
            elif unsupported:
                findings.append(Diagnostic(file, line, f"unsupported {word} invocation; cannot verify interpreter usage"))
            continue

        if len(tail) < 2 or tail[0] not in {"pr", "run", "issue", "repo"}:
            continue
        family, subcommand = tail[:2]
        command_name = f"{family} {subcommand}"
        if command_name not in GH_OPTIONS:
            findings.append(Diagnostic(file, line, f"unsupported gh command {command_name}; cannot verify command operands"))
            continue
        template_merge = file == "skills/github-workflow/templates/AGENTS.md" and family == "pr" and subcommand == "merge"
        positional, valued, switches, missing, unknown, incomplete = _options(
            tail[2:], command_name, extra_boolean_flags=("--{{MERGE_METHOD}}",) if template_merge else (),
        )
        minimum, maximum = GH_OPTIONS[command_name][2]
        default_view = command_name == "repo set-default" and bool(switches & {"--view", "-v"})
        default_unset = command_name == "repo set-default" and bool(switches & {"--unset", "-u"})
        if default_view or default_unset:
            minimum, maximum = 0, 0
        if origin_comparison:
            minimum = 0
        if default_unset:
            findings.append(Diagnostic(file, line, "gh repo set-default --unset is outside the verified-origin repair; supply the verified target instead"))
        if len(positional) < minimum:
            detail = {
                "pr": "needs an explicit PR selector",
                "run": "needs an explicit run ID",
                "repo": "needs an explicit repository",
            }.get(family, f"needs at least {minimum} positional operands")
            findings.append(Diagnostic(file, line, f"gh {command_name} {detail}"))
        if maximum is not None and len(positional) > maximum:
            findings.append(Diagnostic(file, line, f"gh {command_name} accepts at most {maximum} positional operands; found {len(positional)}"))
        if incomplete:
            findings.append(Diagnostic(file, line, "incomplete shell redirection in gh command"))
        for flag in sorted(unknown):
            findings.append(Diagnostic(file, line, f"unsupported gh option {flag}; cannot verify command operands"))
        for flag in sorted(missing - {"--repo", "-R", "--match-head-commit"}):
            findings.append(Diagnostic(file, line, f"gh {command_name} {flag} needs a value"))
        if "-R" in valued or "-R" in missing:
            findings.append(Diagnostic(file, line, "gh repository alias -R is unsupported; use only the verified --repo"))
        if any(not value.strip() for value in positional):
            findings.append(Diagnostic(file, line, "empty gh operand cannot select a PR or run"))
        if family in {"pr", "run", "issue"}:
            repository = valued.get("--repo")
            if repository is None or "--repo" in missing:
                findings.append(Diagnostic(file, line, f"gh {family} {subcommand} needs a valued --repo"))
            elif not ORIGIN_REPO.fullmatch(repository):
                findings.append(Diagnostic(file, line, f"gh {family} {subcommand} --repo must target the verified origin"))
        if family == "repo" and positional and not (origin_comparison or default_view or default_unset) and not ORIGIN_REPO.fullmatch(positional[0]):
            findings.append(Diagnostic(file, line, f"gh repo {subcommand} needs an explicit repository matching the verified origin"))
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
    starts = [
        number - offset for offset, command in (
            (0, PROBE), (2, PY_LAUNCHER_PROBE), (4, PYTHON_PROBE),
        ) if number > offset and lines[number - 1].strip() == command
    ]
    if not starts or lines[starts[0] - 1].strip() != PROBE:
        return False
    following = [line.strip() for line in lines[starts[0]:starts[0] + 9]]
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


def _here_document(boundaries):
    """Recognize the bounded body grammar without interpreting its contents."""
    if not boundaries.has_here_operator:
        return None, None
    header = re.fullmatch(
        r'''[ \t]*(?:cat|"\$\{python_cmd\[@\]\}"[ \t]+-)[ \t]+<<(?P<tabs>-)?[ \t]*(?P<quote>['"])(?P<end>[A-Za-z_][A-Za-z_0-9]*)(?P=quote)[ \t]*''',
        boundaries.visible,
    )
    if header is None:
        return None, "unsupported here-document header; use a standalone cat or selected-Python command with one literal quoted delimiter"
    return (header["end"], bool(header["tabs"])), None


def scan_document(file, text):
    """Check a Markdown document and return deterministic file:line findings."""
    # Markdown normalizes CR/LF endings, not Unicode/control word characters.
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    findings = []
    fence_mark = None
    shell_fence = False
    heading = ""
    pending = ""
    pending_line = 0
    here_document = None
    here_line = 0

    for number, source in enumerate(lines, 1):
        match = FENCE.match(source)
        if match:
            marker, info = match.groups()
            if fence_mark is None:
                fence_mark = marker
                language = info.strip().split(maxsplit=1)[0].lower() if info.strip() else ""
                shell_fence = language in SHELL_FENCES
            elif marker[0] == fence_mark[0] and len(marker) >= len(fence_mark) and not info.strip():
                if here_document is not None:
                    findings.append(Diagnostic(file, here_line, "unterminated here-document before the closing Markdown fence"))
                    here_document = None
                if pending_line:
                    findings.extend(_analyze(pending, file, pending_line))
                    pending = ""
                    pending_line = 0
                fence_mark = None
                shell_fence = False
            continue
        if fence_mark is not None:
            if not shell_fence:
                continue
            if here_document is not None:
                delimiter, strip_tabs = here_document
                if (source.lstrip("\t") if strip_tabs else source) == delimiter:
                    here_document = None
                continue
            command_line = source
            if language == "console" and not pending_line and command_line.lstrip().startswith("$ "):
                command_line = command_line.lstrip()[2:]
            if pending_line:
                pending += command_line
            else:
                pending = command_line
                pending_line = number
            boundaries = _shell_boundaries(pending)
            if boundaries.error:
                findings.append(Diagnostic(file, pending_line, boundaries.error))
                pending = ""
                pending_line = 0
                continue
            here_document, here_error = _here_document(boundaries)
            if boundaries.continuation and here_document is None:
                pending = pending[:-1]
                continue
            is_probe = _interpreter_probe(lines, number)
            if here_error:
                findings.append(Diagnostic(file, pending_line, here_error))
            if here_document is not None:
                here_line = pending_line
            findings.extend(_analyze(pending, file, pending_line, probe=is_probe))
            pending = ""
            pending_line = 0
            continue

        heading_match = HEADING.match(source)
        if heading_match:
            heading = heading_match.group(1)
        for inline_match in INLINE.finditer(source):
            snippet = inline_match.group(1).strip()
            # The rendered root table declares this project's verified toolchain.
            # It is not a portable skill recipe. Keep gh checks in these rows.
            row = PROJECT_COMMAND_ROW.fullmatch(source)
            project_python = (
                file == "AGENTS.md" and heading == "Project commands"
                and row is not None and row.group(1).strip() == snippet
            )
            findings.extend(_analyze(
                snippet, file, number, inline=True,
                origin_comparison=_origin_comparison(file, heading, source, snippet),
                project_python=project_python,
            ))
        if re.match(r"^\s*(?:gh|python3|python|py)(?:\s|$)", source):
            findings.extend(_analyze(source, file, number))
    if pending_line:
        findings.extend(_analyze(pending, file, pending_line))
    if here_document is not None:
        findings.append(Diagnostic(file, here_line, "unterminated here-document at the end of the document"))
    return findings


def check_repository(root):
    """Scan guidance and current/root instructions, never historical snapshots."""
    root = Path(root)
    skill = GUIDE / "SKILL.md"
    references = GUIDE / "references"
    if not (root / references).is_dir():
        raise FileNotFoundError(f"workflow references directory missing: {root / references}")
    files = [
        skill, GUIDE / "templates/AGENTS.md", Path("AGENTS.md"),
        *(path.relative_to(root) for path in sorted((root / references).glob("*.md"))),
    ]
    claude = Path("CLAUDE.md")
    try:
        (root / claude).lstat()
    except FileNotFoundError:
        pass
    else:
        # lstat includes dangling symlinks: an existing but unreadable input
        # must fail the check instead of disappearing from its inventory.
        files.append(claude)
    findings = []
    for path in files:
        try:
            text = (root / path).read_text(encoding="utf-8")
        except UnicodeError as error:
            raise OSError(f"{path}: expected UTF-8 guidance") from error
        findings.extend(scan_document(path.as_posix(), text))
    return findings


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
