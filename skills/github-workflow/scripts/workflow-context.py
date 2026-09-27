#!/usr/bin/env python3
"""Read-only origin binding and conservative suspension checks (Python 3.8+)."""
import datetime
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit


class EvidenceError(Exception):
    pass


def command(*args):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        # Do not echo credentials, remote URLs or complete instructions.
        raise EvidenceError('Command failed: ' + ' '.join(args[:2]))
    try:
        return result.stdout.decode('utf-8')
    except UnicodeDecodeError as error:
        raise EvidenceError('Non-UTF-8 evidence') from error


def remote_identity(value):
    value = value.strip()
    if '://' in value:
        u = urlsplit(value)
        if u.scheme not in ('https', 'ssh') or not u.hostname or u.query or u.fragment:
            raise EvidenceError('Unsupported origin URL; resolve its GitHub host explicitly')
        if u.password or (u.scheme == 'https' and u.username):
            raise EvidenceError('Do not use an origin URL containing credentials')
        if u.port not in (None, 443 if u.scheme == 'https' else 22):
            raise EvidenceError('Nonstandard origin port requires explicit host resolution')
        host, path = u.hostname, u.path.lstrip('/')
    else:
        m = re.fullmatch(r'(?:[^@/:\s]+@)?([A-Za-z0-9.-]+):([^\s]+)', value)
        if not m:
            raise EvidenceError('Origin must identify a GitHub host and owner/repository')
        host, path = m.groups()
    path = path.rstrip('/')
    if path.lower().endswith('.git'):
        path = path[:-4]
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', path):
        raise EvidenceError('Ambiguous origin owner/repository')
    if any(part in ('.', '..') for part in path.split('/')):
        raise EvidenceError('Invalid origin path segments')
    return host.lower(), path


def repo_info(selector=None):
    args = ['gh', 'repo', 'view'] + ([selector] if selector else [])
    try:
        value = json.loads(command(*args, '--json', 'nameWithOwner,url,defaultBranchRef'))
        host, full = remote_identity(value['url'])
        if full.casefold() != value['nameWithOwner'].casefold():
            raise EvidenceError('Contradictory GitHub repository identity')
        return host, full, value
    except (KeyError, TypeError, ValueError) as error:
        raise EvidenceError('Unreadable GitHub repository identity') from error


def origin_context(expected=None):
    fetch = command('git', 'remote', 'get-url', '--all', 'origin').splitlines()
    push = command('git', 'remote', 'get-url', '--push', '--all', 'origin').splitlines()
    if len(fetch) != 1 or len(push) != 1:
        raise EvidenceError('Origin must have one verified fetch and push destination')
    host, full = remote_identity(fetch[0])
    identity = host, full.casefold()
    p_host, p_full = remote_identity(push[0])
    if identity != (p_host, p_full.casefold()):
        raise EvidenceError('Origin fetch and push destinations differ')
    if expected and expected.casefold() != full.casefold():
        raise EvidenceError('Requested repository differs from origin')
    selected_host, selected_full, _ = repo_info()
    if identity != (selected_host, selected_full.casefold()):
        raise EvidenceError('gh default repository differs from origin; correct the local selection and retry')
    actual_host, actual_full, info = repo_info(host + '/' + full)
    if identity != (actual_host, actual_full.casefold()):
        raise EvidenceError('Explicit repository lookup does not match origin')
    branch = (info.get('defaultBranchRef') or {}).get('name')
    if not isinstance(branch, str) or not branch:
        raise EvidenceError('Missing default branch')
    command('git', 'check-ref-format', 'refs/heads/' + branch)
    return host, actual_full, branch


# Default-ignorable code points outside category Cf (Unicode DerivedCoreProperties).
IGNORABLE = re.compile('[\u034f\u115f\u1160\u17b4\u17b5\u180b-\u180f\u3164\ufe00-\ufe0f\uffa0\ufff0-\ufff8'
                       '\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0000-\U000e0fff]')


def normalize(text, joiner=''):
    """Casefold and map dashes; replace invisible characters with joiner."""
    # A spaced soft hyphen is read as a dash; elsewhere it is invisible.
    text = re.sub('(?<=\\s)\u00ad|\u00ad(?=\\s)', '-', text)
    text = unicodedata.normalize('NFKC', text)
    text = IGNORABLE.sub(joiner, ''.join(joiner if unicodedata.category(c) == 'Cf' else c for c in text))
    text = ''.join('-' if unicodedata.category(c) == 'Pd' or c == '\u2212' else c for c in text)
    return text.replace('\r\n', '\n').replace('\r', '\n').casefold()


START = re.compile(r'^\s*<!--\s*github-workflow:start\b(?:(?!-->).)*-->\s*$')
END = re.compile(r'^\s*<!--\s*github-workflow:end\s*-->\s*$')
PREFIX = r'(?:autonomous\s+merge\s+suspended\s*-\s*(?:request\s+dated|requested\s+on|asked\s+on)|merge\s+autonome\s+suspendu\s*-\s*(?:demande\s+du|demandé\s+le))'
EXAMPLE = re.compile(PREFIX + r'\s*<date>(?!\s*\d)')
DATED = re.compile(PREFIX + r'\s*(\d{4}-\d{2}-\d{2})(?![\w-])')
MERGE_WORD = r'\b(?:merges?|merging|merger|mergez|fusions?|fusionner|fusionnement|fusionnez)\b'
PAUSE_WORD = r'\b(?:suspend\w*|paused?|on\s+hold|disabled|forbidden|blocked|en\s+attente|interdit\w*|interdic\w*|bloqu\w*|désactiv\w*|différ\w*)\b'
APPROVAL_WORD = r'\b(?:approval|approve|agreement|consent|permission|authorization|authorisation|sign[ -]?off|green\s+light|accord|go|confirmation|autorisation)\b'
FREE = re.compile(
    MERGE_WORD + r'[^.!?]{0,120}' + PAUSE_WORD
    + r'|' + PAUSE_WORD + r'[^.!?]{0,120}' + MERGE_WORD
    + r'|\b(?:do\s+not|don[’\x27]t|no|never|stop|hold|wait|attend\w*|ne\s+pas|pas\s+de|ne)\b[^.!?]{0,90}' + MERGE_WORD
    + r'|' + MERGE_WORD + r'\s+(?:only|seulement|uniquement|après|after|requires?|needs?|(?:is|are)\s+subject\s+to)\b[^.!?]{0,90}' + APPROVAL_WORD
    + r'|' + APPROVAL_WORD + r'[^.!?]{0,90}\b(?:before|avant)\s+(?:de\s+|toute?\s+|any\s+)?' + MERGE_WORD
    + r'|\bbefore\s+' + MERGE_WORD + r'[^.!?]{0,90}\b(?:obtain|get|seek|receive|wait\s+for)\b[^.!?]{0,90}' + APPROVAL_WORD
    + r'|' + MERGE_WORD + r'[^.!?]{0,90}\b(?:wait\w*|attend\w*)\b[^.!?]{0,90}' + APPROVAL_WORD)

# A flagged unit that only states git mechanics is cleared when none of these apply.
HOLD_CONTEXT = re.compile(
    PAUSE_WORD + r'|' + APPROVAL_WORD
    + r'|\b(?:i|me|my|mine|we|us|our|je|j|moi|mon|ma|mes|nous|notre|nos|until|unless|without|before|wait\w*|stop\w*|hold\w*'
    r'|confirm\w*|decision|decide\w*|notice|further|today|tomorrow|week|month|now|currently|temporar\w*'
    r'|jusqu\w*|sans|avant|attend\w*|tant|décision|nouvel\w*|ordre)\b')
SCOPE = re.compile(
    r'\b(?:prs?|mrs?|pull[\s-]+requests?|merge[\s-]+requests?|demandes?\s+de\s+fusion|branch\w*|branche\w*'
    r'|main|master|trunk|default|develop|production|release\w*)\b')
NEVER_EXEMPT = re.compile(r'\bauto[\s-]*merg\w*|\bautomerg\w*|' + MERGE_WORD + r'[\s-]+(?:buttons?|queues?)\b')
QUALIFIED = re.compile(
    r'\b(?:squash|rebase|fast-forward|ff|no-ff|three-way|3-way|octopus|recursive|ort|resolve|subtree)[\s-]*merg\w*'
    r'|' + MERGE_WORD + r'[\s-]+(?:conflicts?|commits?|bases?|markers?|drivers?|tools?|strateg\w*|messages?|parents?)\b'
    r'|\b(?:conflits?|commits?)\s+de\s+fusion\b')
UPSTREAM = re.compile(
    r'\bupstream\s+(?:remote|repository|repo)\b|\b(?:remote|dépôt|depot)\s+upstream\b'
    r'|\bfrom\s+(?:the\s+)?upstream\s*(?:[,.;:)]|$)')
LIST_ITEM = re.compile(r'^(\s*)(?:[-*+]|\d{1,9}[.)])(?:\s|$)')
HEADING = re.compile(r'^\s{0,3}(#{1,6})(?:\s|$)')
FENCE = re.compile(r'^\s*(`{3,}|~{3,})')
RULE = re.compile(r'^\s*(?:(?:-\s*){3,}|(?:=\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,})$')


def terminated(block):
    return block.rstrip(' \t*_`"\'\u00bb\u201d\u2019)]').endswith(('.', '!', '?'))


def units(lines):
    """Yield (context, unit) for each Markdown block and its wrapped lines.

    Context is the heading path, unterminated lead-in blocks and parent list
    items. Sibling list items and table rows never give each other context.
    """
    headings = []  # (level, text) of the current heading path
    lead = []      # unterminated blocks that introduce what follows
    items = []     # (indent, text) of open list items
    listed = False
    block, kind, indent, fence = [], None, 0, None

    def flush():
        nonlocal block, kind, lead, items, listed
        if not block:
            return None
        text = ' '.join(part.strip() for part in block)
        current, block, kind = kind, [], None
        path = [t for _, t in headings]
        if current == 'item':
            items = [x for x in items if x[0] < indent]
            pair = (' '.join(path + lead + [t for _, t in items]), text)
            items.append((indent, text))
            listed = True
            return pair
        if items and indent:
            # Indented content continues the list item above it.
            return ' '.join(path + lead + [t for i, t in items if i < indent]), text
        items = []
        if listed and current != 'row':
            lead, listed = [], False
        pair = (' '.join(path + lead), text)
        if current == 'row':
            listed = True
        elif current == 'para':
            lead = [] if terminated(text) else lead + [text]
        return pair

    for line in lines:
        stripped = line.strip()
        if fence:
            if re.fullmatch(re.escape(fence[0]) + '{%d,}' % len(fence), stripped):
                fence = None
            elif stripped:
                yield ' '.join([t for _, t in headings] + lead), stripped
            continue
        if kind == 'comment' and not block[-1].rstrip().endswith('-->'):
            block.append(line)
            continue
        heading = HEADING.match(line)
        opening = FENCE.match(line)
        if not stripped or RULE.match(line) or heading or opening:
            pair = flush()
            if pair:
                yield pair
            if opening:
                fence = opening.group(1)
            elif heading:
                level = len(heading.group(1))
                while headings and headings[-1][0] >= level:
                    headings.pop()
                yield ' '.join(t for _, t in headings), stripped
                headings.append((level, stripped))
                lead, items, listed = [], [], False
            continue
        item = LIST_ITEM.match(line)
        starts = 'item' if item else 'row' if stripped.startswith('|') else 'comment' if stripped.startswith('<!--') else None
        if kind is None or starts or kind in ('row', 'comment'):
            pair = flush()
            if pair:
                yield pair
            kind, indent = starts or 'para', len(line) - len(line.lstrip())
        block.append(line)
    pair = flush()
    if pair:
        yield pair


def free_restriction(context, unit):
    text = (context + ' ' + unit).strip()
    if not FREE.search(text):
        return False
    if HOLD_CONTEXT.search(text) or SCOPE.search(text) or NEVER_EXEMPT.search(text):
        return True
    return bool(re.search(MERGE_WORD, QUALIFIED.sub(' ', unit))) and not UPSTREAM.search(unit)


def scan_normalized(text):
    outside = []
    managed = False
    for line in text.splitlines():
        if START.match(line):
            if managed:
                return 2
            managed = True
            outside.append('')
        elif END.match(line):
            if not managed:
                return 2
            managed = False
            outside.append('')
        elif not managed:
            outside.append(line)
    if managed:
        return 2
    # Whitespace joining recognizes a marker or date wrapped over several lines.
    joined = re.sub(r'\s+', ' ', '\n'.join(outside))
    joined = EXAMPLE.sub('', joined)
    found = False
    invalid_date = False

    def take_date(match):
        nonlocal found, invalid_date
        try:
            datetime.date.fromisoformat(match.group(1))
            found = True
        except ValueError:
            invalid_date = True
        return ''
    joined = DATED.sub(take_date, joined)
    if invalid_date or re.search(r'autonomous\s+merge\s+suspended|merge\s+autonome\s+suspendu', joined):
        return 2

    def strip_markers(value):
        return DATED.sub('', EXAMPLE.sub('', re.sub(r'\s+', ' ', value)))
    # Free-form restrictions are read per Markdown unit so that sibling list
    # items and separate sentences are not joined into one false restriction.
    for context, unit in units(outside):
        if free_restriction(strip_markers(context), strip_markers(unit)):
            return 2
    return 1 if found else 0


def scan_text(text):
    """0 clear, 1 dated canonical veto, 2 ambiguous/malformed evidence."""
    # Invisible characters may split a word or separate two words: check both readings.
    results = [scan_normalized(normalize(text, joiner)) for joiner in ('', ' ')]
    return 2 if 2 in results else 1 if 1 in results else 0


def published_instructions(branch):
    ref = 'refs/remotes/origin/' + branch
    command('git', 'rev-parse', '--verify', ref + '^{tree}')
    entry = command('git', 'ls-tree', '-z', ref, '--', 'AGENTS.md')
    if not entry:
        return None  # Confirmed absent in an existing, readable tree.
    if not re.fullmatch(r'100(?:644|755) blob [0-9a-f]+\tAGENTS\.md\x00', entry):
        raise EvidenceError('Published AGENTS.md is not a regular readable blob')
    return command('git', 'show', ref + ':AGENTS.md')


def main():
    mode, *args = sys.argv[1:]
    if mode == 'origin':
        if len(args) > 1:
            raise EvidenceError('origin expects at most owner/repository')
        print('\t'.join(origin_context(args[0] if args else None)))
        return 0
    if mode != 'suspension' or not args:
        raise EvidenceError('Expected origin or suspension <all applicable instruction files>')
    _, _, branch = origin_context()
    inputs = []
    for name in args:
        inputs.append((name, Path(name).read_text(encoding='utf-8-sig')))
    published = published_instructions(branch)
    if published is not None:
        inputs.append(('origin/' + branch + ':AGENTS.md', published))
    results = []
    for name, text in inputs:
        code = scan_text(text)
        results.append(code)
        if code:
            print(name + ': ' + ('dated suspension' if code == 1 else 'ambiguous suspension or malformed managed block'), file=sys.stderr)
    return 2 if 2 in results else 1 if 1 in results else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (EvidenceError, OSError, UnicodeError, ValueError) as error:
        print('Preflight blocked: ' + str(error), file=sys.stderr)
        sys.exit(2)
