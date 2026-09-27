#!/usr/bin/env python3
"""Read-only origin binding and conservative suspension checks (Python 3.8+)."""
import collections
import datetime
import itertools
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


def invisible(c):
    category = unicodedata.category(c)
    return category == 'Cf' or (category == 'Cc' and c not in '\t\n\r') or bool(IGNORABLE.match(c))


def normalize(text, joiner=''):
    """Casefold and map dashes; replace invisible characters with joiner."""
    # A spaced soft hyphen is read as a dash; elsewhere it is invisible.
    text = re.sub('(?<=\\s)\u00ad|\u00ad(?=\\s)', '-', text)
    text = unicodedata.normalize('NFKC', text)
    table = {}
    for c in set(text):
        if invisible(c):
            table[ord(c)] = joiner
        elif unicodedata.category(c) == 'Pd' or c == '\u2212':
            table[ord(c)] = '-'
    return text.translate(table).replace('\r\n', '\n').replace('\r', '\n').casefold()


START = re.compile(r'^\s*<!--\s*github-workflow:start\b(?:(?!-->).)*-->\s*$')
END = re.compile(r'^\s*<!--\s*github-workflow:end\s*-->\s*$')
PREFIX = r'(?:autonomous\s+merge\s+suspended\s*-\s*(?:request\s+dated|requested\s+on|asked\s+on)|merge\s+autonome\s+suspendu\s*-\s*(?:demande\s+du|demandé\s+le))'
EXAMPLE = re.compile(PREFIX + r'\s*<date>(?!\s*\d)')
DATED = re.compile(PREFIX + r'\s*(\d{4}-\d{2}-\d{2})(?![\w-])')
MERGE_WORD = r'\b(?:merges?|merging|merger|mergez|fusions?|fusionner|fusionnement|fusionnez)\b'
PAUSE_WORD = r'\b(?:suspend\w*|paused?|on\s+hold|disabled|forbidden|blocked|en\s+attente|interdit\w*|interdic\w*|bloqu\w*|désactiv\w*|différ\w*)\b'
APPROVAL_WORD = r'\b(?:approv\w*|agreement|consent|permission|authorization|authorisation|sign[ -]?off|green\s+light|accord|go|confirmation|autorisation)\b'
# Restrictions that carry pause or approval wording. Unlike a bare negation
# ('- Do not add dependencies' beside '- Merge requests use squash'), they are
# also read across the items of one list or the rows of one table.
# Each branch lists the word classes of RUN_WORDS it needs.
HOLD_BRANCHES = (
    (MERGE_WORD + r'[^.!?]{0,120}' + PAUSE_WORD, 'mp'),
    (PAUSE_WORD + r'[^.!?]{0,120}' + MERGE_WORD, 'mp'),
    (MERGE_WORD + r'\s+(?:only|seulement|uniquement|après|after|requires?|needs?|(?:is|are)\s+subject\s+to)\b[^.!?]{0,90}' + APPROVAL_WORD, 'mqa'),
    (APPROVAL_WORD + r'[^.!?]{0,90}\b(?:before|avant)\s+(?:de\s+|toute?\s+|any\s+)?' + MERGE_WORD, 'mab'),
    (r'\bbefore\s+' + MERGE_WORD + r'[^.!?]{0,90}\b(?:obtain|get|seek|receive|wait\s+for)\b[^.!?]{0,90}' + APPROVAL_WORD, 'mboa'),
    (MERGE_WORD + r'[^.!?]{0,90}\b(?:wait\w*|attend\w*)\b[^.!?]{0,90}' + APPROVAL_WORD, 'mwa'))
HOLD_FREE = '|'.join(pattern for pattern, _ in HOLD_BRANCHES)
# Word classes of HOLD_BRANCHES. A phrase such as 'on hold' may be split across
# two items, so its last word counts on its own.
RUN_WORDS = {
    'm': re.compile(MERGE_WORD),
    'p': re.compile(PAUSE_WORD + r'|\b(?:hold|attente)\b'),
    'a': re.compile(APPROVAL_WORD + r'|\b(?:off|light)\b'),
    'q': re.compile(r'\b(?:only|seulement|uniquement|après|after|requires?|needs?|subject)\b'),
    'b': re.compile(r'\b(?:before|avant)\b'),
    'o': re.compile(r'\b(?:obtain|get|seek|receive|wait)\b'),
    'w': re.compile(r'\b(?:wait\w*|attend\w*)\b')}
RUN_BRANCHES = tuple((re.compile(pattern), words) for pattern, words in HOLD_BRANCHES)
# A HOLD_BRANCHES match has no sentence punctuation and, for ordinary word
# lengths, starts fewer than RUN_WINDOW characters before its last item, so a
# list or table is read in bounded windows.
RUN_WINDOW = 300
FREE = re.compile(
    HOLD_FREE
    + r'|\b(?:do\s+not|don[’\x27]t|no|never|stop|hold|wait|attend\w*|ne\s+pas|pas\s+de|ne)\b[^.!?]{0,90}' + MERGE_WORD)

# A flagged unit that only states git mechanics is cleared when none of these apply.
HOLD_CONTEXT = re.compile(
    PAUSE_WORD + r'|' + APPROVAL_WORD
    + r'|\bi\b(?!\.e\b)|\bj(?=[’\x27])'
    r'|\b(?:me|my|mine|we|us|our|je|moi|mon|ma|mes|nous|notre|nos|until|unless|without|before|wait\w*|stop\w*'
    r'|hold\w*|ask\w*|review\w*|pending|confirm\w*|decision|decide\w*|notice|further|today|tomorrow|week\w*'
    r'|month\w*|sprint\w*|time\s+being|moment|instant|now|currently|temporar\w*|freez\w*|frozen'
    r'|jusqu\w*|sans|avant|attend\w*|tant|décision|décid\w*|nouvel\w*|ordre|demand\w*|revue|valid\w*'
    r'|semaines?|mois|aujourd\w*|demain|actuellement|provisoire\w*|gel\w*)\b')
SCOPE = re.compile(
    r'\b(?:prs?|mrs?|pull[\s-]+requests?|merge[\s-]+requests?|demandes?\s+de\s+fusion|branch\w*|branche\w*'
    r'|main|master|trunk|default[\s-]+branch\w*|develop|production|release\w*)\b')
NEVER_EXEMPT = re.compile(r'\bauto[\s-]*merg\w*|' + MERGE_WORD + r'[\s-]+(?:buttons?|queues?)\b')
# Merge methods; a negated method is cleared only beside a permitted alternative.
METHOD_PREFIX = re.compile(
    r'\b(?:squash|rebase|fast[\s-]*forward|ff|no-ff|three-way|3-way|octopus|recursive|ort|resolve|subtree)'
    r'(?:\s+and\s+|[\s-]*)$')
METHOD_FLAG = re.compile(r'\s+--?(?:ff-only|no-ff|ff|squash|no-commit)\b')
MECHANICS_NOUN = re.compile(r'[\s-]+(?:conflicts?|markers?|bases?|drivers?|tools?|messages?|parents?)\b')
# 'No merge method is permitted' bans every choice; 'the merge strategy' only names one.
CHOICE_NOUN = re.compile(r'[\s-]+(?:strateg\w*|polic\w*|methods?|settings?|options?)\b')
ANY_BEFORE = re.compile(r'\b(?:any|no|every|all|aucune?|toute?s?)\s*$')
COMMIT_NOUN = re.compile(r'[\s-]+commits?\b')
# 'Never merge commits' uses merge as a verb: it restricts what may be merged.
VERB_BEFORE = re.compile(
    r'\b(?:not|never|n[’\x27]t|to|please|cannot|can|may|must|should|shall|will|would|could|ne|pas|jamais)\s*$')
ARTICLE_BEFORE = re.compile(r'\b(?:the|a|an|this|that|each|every|its|their|your|our|le|la|chaque)\s*$')
# 'Never create a merge commit' still names the method, unlike 'the merge commit SHA'.
CREATE_BEFORE = re.compile(r'\b(?:create|creates|creating|make|makes|making|use|uses|using)\s+(?:a|an|the)\s*$')
POSITIVE = {'use', 'uses', 'using', 'always', 'prefer', 'prefers', 'only', 'utilise', 'utilisez', 'utiliser',
            'toujours', 'privilégier', 'privilégiez', 'uniquement', 'seulement'}
NEGATIVE = {'no', 'not', 'never', 'don', 'avoid', 'nor', 'pas', 'jamais', 'ne', 'n', 'ni'}
# A method clause clears only when every word is method vocabulary or plain grammar:
# a condition such as 'once the owner signs off' is never read as a permitted method.
METHOD_TOKENS = POSITIVE | NEGATIVE | {
    'merge', 'merges', 'merging', 'fusion', 'fusions', 'squash', 'rebase', 'rebases', 'fast', 'forward', 'ff',
    'noff', 'three', 'way', '3', 'octopus', 'recursive', 'ort', 'resolve', 'subtree', 'commit', 'commits', 'git', 'method',
    'methods', 'strategy', 'strategies', 'do', 'does', 't', 'the', 'a', 'an', 'and', 'or', 'but', 'instead',
    'rather', 'than', 'of', 'over', 'with', 'via', 'de', 'des', 'les', 'le', 'la', 'du', 'd', 'l', 'ou', 'et',
    'mais', 'plutôt', 'que', 'qu', 'au', 'lieu', 'par', 'ie', 'by', 'default', 'défaut', 'create', 'creates',
    'creating', 'keep', 'make', 'faire', 'faites', 'fais', 'créer', 'créez', 'crée'}
# A clause with no merge word inside a cleared rule may only restate git mechanics;
# any other clause ('the owner signs off first') may condition the rule.
MECHANICS_CONTENT = {
    'rebase', 'rebases', 'rebasing', 'squash', 'commit', 'commits', 'fast', 'forward', 'ff', 'onto', 'main', 'master',
    'branch', 'branches', 'branche', 'cherry', 'pick', 'upstream', 'remote', 'sync', 'history', 'linear',
    'historique', 'linéaire'}
METHOD_CONTENT = {'rebase', 'rebases', 'rebasing', 'squash', 'commit', 'commits', 'fast', 'forward', 'ff'}
# One identity per merge method, so a rule that bans and permits the same method is caught;
# every merge strategy (three-way, recursive, ort, ...) produces a merge commit.
METHOD_KEYS = {
    'squash': 'squash', 'rebase': 'rebase', 'rebases': 'rebase', 'fast': 'ff', 'forward': 'ff', 'ff': 'ff',
    'noff': 'commit', 'three': 'commit', '3': 'commit', 'octopus': 'commit', 'recursive': 'commit', 'ort': 'commit',
    'resolve': 'commit', 'subtree': 'commit', 'commit': 'commit', 'commits': 'commit'}
BARE_TOKENS = METHOD_TOKENS | MECHANICS_CONTENT | {'them', 'it'}
# A hold sentence right after a cleared method rule may qualify that rule.
FOLLOW_HOLD = re.compile(
    PAUSE_WORD + r'|' + APPROVAL_WORD + r'|\b(?:until|unless|wait\w*|hold\w*|jusqu\w*|attend\w*|tant\s+que'
    r'|ok(?:s|ed)?|first|d\W?abord|final\s+say|dernier\s+mot|feu\s+vert|owner|maintainer|propriétaire|mainteneur)\b')
# Pause wording is also read across Markdown blocks, as sibling headings or items can carry it.
STRONG_PAUSE = r'\b(?:suspend\w*|paused?|on\s+hold|en\s+attente)\b'
CROSS_PAUSE = re.compile(
    MERGE_WORD + r'[^.!?]{0,120}' + STRONG_PAUSE + r'|' + STRONG_PAUSE + r'[^.!?]{0,120}' + MERGE_WORD)
# An upstream-sync rule names an upstream remote as the single source that ends its clause.
UPSTREAM_SOURCE = re.compile(
    r'\b(?:from|depuis|du|de)\s+(?:the\s+|le\s+|la\s+|l[’\x27]\s*)?'
    r'(?:(?:upstream|original|parent|vendor)\s+(?:remote|repository|repo|project)s?'
    r'|(?:dépôt|depot|remote|projet)\s+(?:upstream|amont|d[’\x27]origine|parent)|upstream)\b')
UPSTREAM_END = re.compile(
    r'(?:\s*\b(?:directly|wholesale|as[\s-]is|blindly|directement|en\s+bloc|tel\s+quel)\b)*[\s,)\]"\x27»”’]*')
# Only coordinated git verbs and plain objects may sit between the merge word and the source:
# 'never merge except from upstream' or 'not even from upstream' still ban merging.
UPSTREAM_BETWEEN = re.compile(
    r'(?:[\s,]|\b(?:or|and|et|ou|rebase|onto|cherry-pick\w*|pull|fetch|sync|copy|import|wholesale|directly'
    r'|blindly|jamais|changes?|commits?|code|updates?|work|the|a|an|des|les|le|la|du|de|in|into)\b)*')
WIDEN = re.compile(r'\b(?:anywhere|elsewhere|else|other\w*|including|ailleurs|autres?|y\s+compris|notamment)\b')
SOURCE_WORD = re.compile(r'\b(?:from|depuis)\b')
LIST_ITEM = re.compile(r'^(\s*)(?:[-*+]|\d{1,9}[.)])(?:\s+|$)')
HEADING = re.compile(r'^ {0,3}(#{1,6})(?:\s|$)')
FENCE = re.compile(r'^ {0,3}(`{3,}(?!.*`)|~{3,})(.*)$')
RULE = re.compile(r'^ {0,3}(?:(?:-[ \t]*){3,}|(?:=[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$')
SETEXT = re.compile(r'^ {0,3}(=+|-+)[ \t]*$')
QUOTE = re.compile(r'^ {0,3}>[ \t]?')
# FREE spans a few hundred characters; the hold and scope checks read only this much context.
CONTEXT_LIMIT = 600
# Bound the work per unit; a longer unit stays blocking.
WORD_WINDOW = 250
MAX_MERGE_WORDS = 50
# Instruction files are far smaller; a larger input is not scanned and stays ambiguous,
# which also bounds the scan time on adversarial input.
MAX_SCAN_CHARS = 128 * 1024  # characters, not bytes


def unquote(line):
    """Drop blockquote prefixes and expand tabs so indentation compares in columns."""
    while QUOTE.match(line):
        line = QUOTE.sub('', line, 1)
    return line.expandtabs(4)


def trim(blocks):
    """Keep the newest blocks that fit the context limit, and at least one."""
    kept, size = [], 0
    for block in reversed(blocks):
        if kept and size + len(block) > CONTEXT_LIMIT:
            break
        kept.append(block)
        size += len(block) + 1
    return kept[::-1]


def label(text):
    """A list item such as '**Merges:**' or 'Merging' that titles the items after it."""
    if re.fullmatch(r'(\*\*|__)[^*_]+\1:?', text) or text.rstrip(' *_').endswith(':'):
        return True
    return len(text.split()) <= 2 and bool(re.search(MERGE_WORD, text))


def terminated(block):
    return block.rstrip(' \t*_`"\'»”’)]').endswith(('.', '!', '?'))


def strip_markers(value):
    return DATED.sub('', EXAMPLE.sub('', re.sub(r'\s+', ' ', value)))


def units(lines, hold_found):
    """Yield (context, unit) for each Markdown block and its wrapped lines.

    Context is the heading path, unterminated lead-in blocks, parent list items
    and a table's header row. Sibling list items and table data rows never give
    each other context. A fenced block is one unit.

    hold_found is called once when a pause or approval restriction spreads over
    the items of one list or the rows of one table.
    """
    headings = []  # (level, text) of the current heading path
    lead = []      # unterminated blocks that introduce what follows
    items = []     # (indent, text) of the open list item chain
    header = None  # first row of the current table
    block, kind, indent = [], None, 0
    fence = None   # (marker, context, lines) of an open fenced block
    run = collections.deque()  # (text, words) of recent items of the current list or table
    run_words = collections.Counter()  # RUN_WORDS classes across the run
    run_size, run_table, run_held = 0, False, False

    def context(extra=()):
        path = ' '.join(t for _, t in headings)[-CONTEXT_LIMIT:]
        parts, size = [], 0
        for part in reversed(lead + list(extra)):
            if size >= CONTEXT_LIMIT:
                break
            parts.append(part)
            size += len(part) + 1
        return (path + ' ' + ' '.join(reversed(parts))[-CONTEXT_LIMIT:]).strip()

    def extend_run(text, table):
        nonlocal run_size, run_table, run_held
        if run_table != table:
            end_run()
        run_table = table
        if run_held:
            return
        # Sized on the text the patterns read, so padding or example markers
        # cannot push an earlier item out of the window.
        text = strip_markers(text)
        # Keep at least RUN_WINDOW characters of earlier items before the new one.
        while len(run) > 1 and run_size - len(run[0][0]) - 1 >= RUN_WINDOW:
            old, words = run.popleft()
            run_size -= len(old) + 1
            run_words.subtract(words)
        words = ''.join(key for key, pattern in RUN_WORDS.items() if pattern.search(text))
        run.append((text, words))
        run_size += len(text) + 1
        run_words.update(words)
        # Only a restriction that ends in the new item is new; earlier ones were
        # read before. It cannot cross sentence punctuation, and each branch is
        # tried only when the run holds every word class it needs.
        if len(run) == 1 or not set(words) & set('mpa'):
            return
        earlier = ' '.join(item for item, _ in itertools.islice(run, len(run) - 1))
        start = max(earlier.rfind('.'), earlier.rfind('!'), earlier.rfind('?')) + 1
        joined = (earlier + ' ' + text)[start:]
        if any(all(run_words[key] for key in needed) and pattern.search(joined)
               for pattern, needed in RUN_BRANCHES):
            run_held = True
            hold_found()

    def end_run():
        nonlocal run_size
        run.clear()
        run_words.clear()
        run_size = 0

    def flush():
        nonlocal block, kind, lead, items, header
        if not block:
            return None
        text = ' '.join(part.strip() for part in block)
        current, block, kind = kind, [], None
        if current == 'item':
            text = LIST_ITEM.sub('', text, 1)
            sibling = None
            while items and items[-1][0] >= indent:
                sibling = items.pop()
            parents = [t for _, t in items]
            if sibling and sibling[0] == indent and label(sibling[1]):
                parents.append(sibling[1])  # '- **Merges:**' labels the next sibling
            pair = context(parents), text
            items.append((indent, text))
            extend_run(text, False)
            return pair
        if items and indent:
            # Indented content continues the list item above it.
            extend_run(text, False)
            return context(t for i, t in items if i < indent), text
        if items:
            # Unterminated open items still introduce the block after the list.
            lead = trim(lead + [t for _, t in items if not terminated(t)])
            items = []
        if current == 'row':
            pair = context([header] if header else []), text
            if header is None:
                end_run()  # a new table
            extend_run(text, True)
            header = header or text
            return pair
        header = None
        end_run()
        pair = context(), text
        body = re.sub(r'<!--|-->', ' ', text).strip() if current == 'comment' else text
        lead = [] if terminated(body) else trim(lead + [body])
        return pair

    def enter_heading(level, text):
        nonlocal lead, items, header
        while headings and headings[-1][0] >= level:
            headings.pop()
        pair = context(), text
        headings.append((level, text[:CONTEXT_LIMIT]))
        lead, items, header = [], [], None
        end_run()
        return pair

    for line in lines:
        stripped = line.strip()
        if fence:
            marker, fenced_context, fenced = fence
            if re.fullmatch(r' {0,3}' + re.escape(marker[0]) + '{%d,}\\s*' % len(marker), line):
                yield fenced_context, ' '.join(fenced)
                fence = None
            elif stripped:
                fenced.append(stripped)
            continue
        if kind == 'comment' and not block[-1].rstrip().endswith('-->'):
            block.append(line)
            continue
        setext = kind == 'para' and not (items and indent) and SETEXT.match(line)
        if setext:
            text = ' '.join(part.strip() for part in block)
            block, kind = [], None
            yield enter_heading(1 if setext.group(1)[0] == '=' else 2, text)
            continue
        heading = HEADING.match(line)
        opening = FENCE.match(line)
        if not stripped or RULE.match(line) or heading or opening:
            pair = flush()
            if pair:
                yield pair
            if opening:
                fence = opening.group(1), context(t for _, t in items), [opening.group(2).strip()]
            elif heading:
                yield enter_heading(len(heading.group(1)), stripped)
            continue
        item = LIST_ITEM.match(line)
        starts = 'item' if item else 'row' if stripped.startswith('|') else 'comment' if stripped.startswith('<!--') else None
        if kind is None or starts or kind in ('row', 'comment'):
            pair = flush()
            if pair:
                yield pair
            kind, indent = starts or 'para', len(line) - len(line.lstrip())
        block.append(line)
    if fence:
        yield fence[1], ' '.join(fence[2])
    pair = flush()
    if pair:
        yield pair


def upstream_source(before, after):
    sources = list(UPSTREAM_SOURCE.finditer(after))
    if len(sources) != 1 or len(SOURCE_WORD.findall(after)) > 1 or WIDEN.search(before + after):
        return False
    between, rest = after[:sources[0].start()], after[sources[0].end():]
    return bool(UPSTREAM_BETWEEN.fullmatch(between) and UPSTREAM_END.fullmatch(rest))


def permitted_method(clause):
    """None if the clause is not plain method wording, else its (permitted, banned) method sets.

    Polarity is read per comma or 'but' segment from its start; a segment with
    neither polarity word continues the previous one, so 'never use squash
    merges, rebase merges or merge commits' bans all three. 'and' starts a new
    segment only before a polarity word, so 'do not use squash merges and use
    merge commits' permits one method while GitHub's 'Squash and merge' label
    stays one name even before 'only'. 'no-ff' names a method, not a ban.
    """
    clause = re.sub(r'\bno-ff\b', 'noff', clause, flags=re.IGNORECASE)
    words = re.findall(r'[^\W_]+', clause)
    if not set(words) <= METHOD_TOKENS:
        return None
    segments = []
    labels = re.sub(r'\b(squash|rebase)\s+and\s+(?=merg(?:e|ing))', r'\1 & ', clause, flags=re.IGNORECASE)
    for part in re.split(r',|\b(?:but|mais|instead|plutôt)\b', labels):
        pieces = re.split(r'\b(?:and|et)\b', part)
        segments.append(pieces[0])
        for piece in pieces[1:]:
            if set(re.findall(r'[^\W_]+', piece)) & (NEGATIVE | POSITIVE):
                segments.append(piece)
            else:
                segments[-1] += ' and ' + piece
    permitted, banned, polarity = set(), set(), None
    for segment in segments:
        tokens = set(re.findall(r'[^\W_]+', segment))
        if tokens & NEGATIVE:
            polarity = False
        elif tokens & POSITIVE:
            polarity = True
        keys = {METHOD_KEYS[token] for token in tokens & METHOD_KEYS.keys()}
        if polarity is False:
            banned |= keys or {'merge'}
        elif polarity and re.search(MERGE_WORD + r'|\bcommits?\b', segment):
            permitted |= keys or {'merge'}
    return permitted, banned


def mechanics_only(unit):
    """True when every merge word in the unit is a git-mechanics term or an upstream-sync clause."""
    plain = re.sub(r'[*_`]', ' ', unit)
    methods = False
    permitted, banned = set(), set()
    if len(re.findall(MERGE_WORD, plain)) > MAX_MERGE_WORDS:
        return False
    # 'i.e.' restates the rule inside one clause.
    pieces = re.split(r'([.!?;:])', re.sub(r'\bi\.e\.', ' ie ', plain))
    for index in range(0, len(pieces), 2):
        clause = pieces[index]
        # 'Never merge from upstream; ever.' continues the clause after ';' or ':'.
        final = index + 1 >= len(pieces) or pieces[index + 1] in '.!?' or not re.search(r'\w', ''.join(pieces[index + 2:]))
        method_clause = False
        if not re.search(MERGE_WORD, clause):
            words = set(re.findall(r'[^\W_]+', clause))
            # 'Do not do it.' has no git content and may undo the rule;
            # 'Never squash.' may forbid the method the rule just permitted.
            if (words and not (words <= BARE_TOKENS and words & MECHANICS_CONTENT)) or (
                    words & NEGATIVE and words & METHOD_CONTENT):
                return False
            continue
        for match in re.finditer(MERGE_WORD, clause):
            before = clause[max(0, match.start() - WORD_WINDOW):match.start()]
            after = clause[match.end():match.end() + WORD_WINDOW]
            prefix = METHOD_PREFIX.search(before)
            commit = COMMIT_NOUN.match(after)
            verb = VERB_BEFORE.search(before)
            if final and len(clause) - match.end() <= WORD_WINDOW and upstream_source(before, after):
                continue
            if prefix or METHOD_FLAG.match(after) or (commit and not verb and (
                    not ARTICLE_BEFORE.search(before) or CREATE_BEFORE.search(before))):
                method_clause = True
            elif not verb and (MECHANICS_NOUN.match(after) or commit):
                continue
            elif not verb and CHOICE_NOUN.match(after) and not ANY_BEFORE.search(before):
                continue
            elif match.group() == 'fusion' and re.search(r'\bconflits?\s+de\s*$', before):
                continue
            elif match.group() == 'fusion' and re.search(r'\bcommits?\s+de\s*$', before):
                method_clause = True
            else:
                return False
        if method_clause:
            polarity = permitted_method(clause)
            if polarity is None:
                return False
            methods = True
            permitted |= polarity[0]
            banned |= polarity[1]
    # 'Do not use squash merges; use squash merges.' contradicts itself, and a ban naming
    # no method ('use merge commits, never') may cancel any permission, so both stay blocking.
    return not (permitted & banned) and 'merge' not in banned and (bool(permitted) or not methods)


def free_restriction(context, unit):
    """True for a restriction, 'cleared' for an exempted git-mechanics rule, else False."""
    text = (context + ' ' + unit).strip()
    if not FREE.search(text):
        return False
    if HOLD_CONTEXT.search(text) or SCOPE.search(text) or NEVER_EXEMPT.search(text):
        return True
    return 'cleared' if mechanics_only(unit) else True


def scan_normalized(text):
    outside = []
    managed = False
    # Only newlines end Markdown lines; str.splitlines would also split on U+2028 or \x1e.
    for line in text.split('\n'):
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
    outside = [unquote(line) for line in outside]
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
    if CROSS_PAUSE.search(joined):
        return 2

    # Free-form restrictions are read per Markdown unit so that sibling list
    # items and separate sentences are not joined into one false restriction.
    cleared, follows, held = False, False, []
    for context, unit in units(outside, lambda: held.append(True)):
        unit = strip_markers(unit)
        result = free_restriction(strip_markers(context), unit)
        if result is True:
            return 2
        if result == 'cleared' and FOLLOW_HOLD.search(strip_markers(context)):
            return 2  # a heading, lead-in or parent item conditions the rule
        cleared = cleared or result == 'cleared'
        follows = follows or bool(FOLLOW_HOLD.search(unit))
    if held:
        return 2  # a list or table carries a pause or approval restriction across items
    # A hold or approval sentence anywhere in the file may qualify a cleared
    # rule without naming a merge, even from a sibling section under the
    # same parent heading.
    if cleared and follows:
        return 2
    return 1 if found else 0


def hidden_splits(text):
    """Count in-word invisible characters near merge or pause vocabulary, up to two."""
    hidden = ''.join(filter(invisible, set(text)))
    vocabulary = re.compile(r'merg|fusion|suspen|paus|hold|attente')
    strip = {ord(c): None for c in hidden}
    if not hidden or not vocabulary.search(re.sub(r'\s+', '', text.translate(strip)).casefold()):
        return 0
    count = 0
    visible = re.compile('[^' + re.escape(hidden) + ']')
    for found in re.finditer('[' + re.escape(hidden) + ']', text):
        index = found.start()
        # Check the cheap left neighbour first: a long invisible run stays linear.
        if not (index and text[index - 1].isalnum()):
            continue
        following = visible.search(text, index + 1)
        if not (following and following.group().isalnum()):
            continue
        window = text[max(0, index - 250):index + 250].translate(strip)
        if vocabulary.search(re.sub(r'\s+', '', window).casefold()):
            count += 1
            if count > 1:
                break
    return count


def scan_text(text):
    """0 clear, 1 dated canonical veto, 2 ambiguous/malformed evidence."""
    if len(text) > MAX_SCAN_CHARS:
        return 2
    # Invisible characters may split a word or separate two words: check both readings.
    results = [scan_normalized(normalize(text, joiner)) for joiner in ('', ' ')]
    if hidden_splits(text) > 1:
        # Two readings cannot cover several invisible splits that need different choices.
        results.append(2)
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
