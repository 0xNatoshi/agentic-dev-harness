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


def normalize(text):
    text = unicodedata.normalize('NFKC', text).replace('\ufeff', '')
    text = ''.join('-' if unicodedata.category(c) == 'Pd' or c in '\u2212\u00ad' else c for c in text)
    return text.replace('\r\n', '\n').replace('\r', '\n').casefold()


START = re.compile(r'^\s*<!--\s*github-workflow:start\b(?:(?!-->).)*-->\s*$')
END = re.compile(r'^\s*<!--\s*github-workflow:end\s*-->\s*$')
PREFIX = r'(?:autonomous\s+merge\s+suspended\s*-\s*(?:request\s+dated|requested\s+on|asked\s+on)|merge\s+autonome\s+suspendu\s*-\s*(?:demande\s+du|demandé\s+le))'
EXAMPLE = re.compile(PREFIX + r'\s*<date>')
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
    + r'|\bbefore\s+' + MERGE_WORD + r'[^.!?]{0,90}\b(?:obtain|get|seek|receive|wait\s+for)\b[^.!?]{0,90}' + APPROVAL_WORD)



def scan_text(text):
    """0 clear, 1 dated canonical veto, 2 ambiguous/malformed evidence."""
    outside = []
    managed = False
    for line in normalize(text).splitlines():
        if START.match(line):
            if managed:
                return 2
            managed = True
        elif END.match(line):
            if not managed:
                return 2
            managed = False
        elif not managed:
            outside.append(line)
    if managed:
        return 2
    # Whitespace joining recognizes a marker or date wrapped over several lines.
    text = re.sub(r'\s+', ' ', '\n'.join(outside))
    text = EXAMPLE.sub('', text)
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
    text = DATED.sub(take_date, text)
    if invalid_date or FREE.search(text) or re.search(r'autonomous\s+merge\s+suspended|merge\s+autonome\s+suspendu', text):
        return 2
    return 1 if found else 0


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
