#!/usr/bin/env python3
"""Read-only origin binding and conservative suspension checks (Python 3.8+)."""
import collections
import datetime
import functools
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


# Only an unspaced em dash can stand for a hyphen, so only text with one, or with a dash opening a line,
# gets the hyphen reading: the spaced dash of the canonical marker costs no extra scan.
UNSPACED_EM_DASH = re.compile('\\S[\u2014\u2015]|[\u2014\u2015]\\S')
# A dash opening a line may be a list marker, as on main, or open a wrapped continuation, so it gets
# both readings: as ' - ' alone it would indent the line under its '- ' siblings and split their list.
LINE_EM_DASH = re.compile('(?m)^[ \t>]*[\u2014\u2015]')


def readings(text):
    """Each distinct casefolded reading of text that scan_text must check, dashes read as hyphens.

    Invisible characters may split a word or separate two words: text with one is read with them
    removed and with them spaced. An em dash or horizontal bar usually separates words even unspaced,
    unlike a hyphen that joins them, so it reads as ' - '; an unspaced one may also stand for a hyphen
    ('Laisse—moi fusionner'), and one opening a line for a list marker, and that reading comes first.
    Text without either has one reading.
    """
    # A spaced soft hyphen is read as a dash; elsewhere it is invisible.
    text = unicodedata.normalize('NFKC', re.sub('(?<=\\s)\u00ad|\u00ad(?=\\s)', '-', text))
    chars = set(text)
    hidden = [c for c in chars if invisible(c)]
    table = {ord(c): '-' for c in chars
             if c not in '\u2014\u2015' and (unicodedata.category(c) == 'Pd' or c == '\u2212')}
    folded = []
    for joiner in ('', ' ') if hidden else ('',):
        table.update(dict.fromkeys(map(ord, hidden), joiner))
        folded.append(text.translate(table).replace('\r\n', '\n').replace('\r', '\n').casefold())
    # Translation, line endings and casefolding leave em dashes in place, so the NFKC pass and each
    # translation serve both dash readings. Line starts are found in the folded readings, where every
    # line ending is a newline and no invisible character stands before the dash.
    hyphen = ('\u2014' in text or '\u2015' in text) and (
        UNSPACED_EM_DASH.search(text) or any(LINE_EM_DASH.search(reading) for reading in folded))
    em_dashes = ('-', ' - ') if hyphen else (' - ',)
    return list(dict.fromkeys(reading.replace('\u2014', em_dash).replace('\u2015', em_dash)
                              for em_dash in em_dashes for reading in folded))


START = re.compile(r'^\s*<!--\s*github-workflow:start\b(?:(?!-->).)*-->\s*$')
END = re.compile(r'^\s*<!--\s*github-workflow:end\s*-->\s*$')
PREFIX = r'(?:autonomous\s+merge\s+suspended\s*-\s*(?:request\s+dated|requested\s+on|asked\s+on)|merge\s+autonome\s+suspendu\s*-\s*(?:demande\s+du|demandé\s+le))'
EXAMPLE = re.compile(PREFIX + r'\s*<date>(?!\s*\d)')
DATED = re.compile(PREFIX + r'\s*(\d{4}-\d{2}-\d{2})(?![\w-])')
MERGE_WORD = r'\b(?:merges?|merged|merging|merger|mergez|automerg\w*|fusions?|fusionn\w*)\b'
# A lead-in naming a merged state ('- Not until the release PR is merged') still introduces its items.
LEAD_MERGE_WORD = r'\b(?:merges?|merging|merger|mergez|fusions?|fusionner|fusionnement|fusionnez)\b'
PAUSE_WORD = r'\b(?:suspend\w*|paused?|on\s+hold|disabled|forbidden|blocked|en\s+attente|interdit\w*|interdic\w*|bloqu\w*|désactiv\w*|différ\w*)\b'
# Common rule wording that PAUSE_WORD lacks. Read within one clause only, as in 'Direct
# pushes are not allowed; merge through PRs' it restricts something else.
BAN_WORD = (r'(?:\b(?:prohibit\w*|disallow\w*|banned|défendu\w*)\b|(?:\bnot|n[’\x27]t)\s+(?:allowed|permitted|authori[sz]ed)\b'
            r'|\b(?:ne\s+|n[’\x27])(?:\w+\s+){0,2}?pas\s+(?:autoris\w*|permis\w*))')
FREEZE_STEM = r'\b(?:frozen|freeze|gel(?:é|ée|és|ées)?)\b'
# 'A frozen lockfile' and 'Freeze the lockfile' are package-manager settings, but
# 'Freeze dependency merges' and 'freeze versions and merges' still freeze merges.
FREEZE_WORD = (FREEZE_STEM + r'(?![\s-]+(?:(?:the|your|our|a|all|any)\s+)?(?:lock[\s-]*files?|dependenc\w*|deps|versions?|pins?)\b'
               r'(?!(?:[\s,&/+-]+(?!(?:before|after|until|unless|once|when|while|then|prior|during|avant|après)\b)[\w’\x27-]+){0,3}?'
               r'[\s,&/+-]+(?:merg|fusion)))')
FIRST_PERSON = r'\bi\b(?!\.e\b)|\bj(?=[’\x27])|\b(?:me|my|mine|we|us|our|je|moi|mon|ma|mes|nous|notre|nos)\b'
# 'review' and 'ok' only when said by someone: 'my review', 'until I say ok'. A bare 'go' or
# 'go-ahead' is a signal, as on the release before; 'then go to the next task' is a verb.
APPROVAL_WORD = (r'\b(?:approv\w*|agreement|consent\w*|permission|authori[sz]\w*|sign(?:s|ed)?[ -]?off|green\s+light'
                 r'|feu\s+vert|accord|confirmation|autoris\w*|approbation'
                 # 'go to', 'go home' and 'go back' are the verb unless a determiner makes a noun:
                 # 'then go back to work', but 'my go to proceed' and 'the go on Slack'.
                 r'|(?:(?:my|our|your|their|his|her|the|a|mon|ma|ton|notre|votre|le|un)\s+|[’\x27]s\s+)go'
                 r'|go(?![\s-]+(?:to|home|back)\b)'
                 r'|(?:my|our|mon|ma|mes|notre|nos)\s+(?:own\s+)?(?:reviews?|ok(?:ay)?|relecture|revue)'
                 # 'until my return', but not 'our return codes' or 'our returns policy'. Units are
                 # whitespace-collapsed, so one space before the pronoun is enough. The lookahead
                 # first keeps the lookbehinds off every other position.
                 r'|(?=(?:my|our|mon|notre)\s+re)(?:(?<=\buntil\s)|(?<=\btill\s)|(?<=\bafter\s)|(?<=\bpending\s)|(?<=\bbefore\s)|(?<=\bupon\s)'
                 r'|(?<=jusqu’à\s)|(?<=jusqu\x27à\s)|(?<=\bavant\s)|(?<=\baprès\s)|(?<=\bdès\s))'
                 r'(?:my|our|mon|notre)\s+(?:return|retour)(?![\s-]*(?:codes?|values?|types?|polic\w*|statements?|modules?)\b)'
                 r'|(?:i|we)\s+(?:say|give)\s+(?:so|ok(?:ay)?))\b')
# Someone who decides: 'until I say go', 'until you hear from me', 'until we approve'.
# 'Wait for our CI' and 'We hold PRs that fail CI' name no decision.
FIRST_ACTOR = (r'\bi\b(?!\.e\b)|\bj(?=[’\x27])|\b(?:me|us|je|moi)\b'
               r'|\b(?:we|nous)\s+(?:\w+\s+)?(?:say|approv|give|sign|confirm|review|decid|agree|valid|dis|donn|approuv|décid)\w*')
APPROVAL_OR_FIRST = r'(?:' + APPROVAL_WORD + r'|' + FIRST_ACTOR + r')'
# Landing or shipping a pull request integrates it; 'Do not land broken code' does not name one.
INTEGRATION_WORD = re.compile(r'\b(?:integrat\w*|lands?|landed|landing|ships?|shipped|shipping|intègr\w*|intégr\w*)\b')
# The scope must sit in the same clause, near the verb: 'integrate any PR', 'landing on main',
# 'PRs are shipped', 'Hold all PRs'. A PR named in an earlier clause does not make 'its own
# integration' a merge, nor 'no approval needed when they hold' a hold on it.
# Who decides a reserved merge: the speaker, or a named human role. The base lists are shared
# with POSSESSOR so that 'the owner decides' and 'the owner's call' name the same people.
DECIDER_BASE = r'(?:owner|maintainer|admin|human)'
ROLE_BASE = r'(?:release\s+manager|reviewer|(?:tech|team)\s+lead|code\s+owner)'
# A role word naming a document, tool or place ('Consult the reviewer guide', 'Merge via the
# admin panel', 'Merges go through the reviewer queue') names no one who decides.
TOOL_WORD = (r'(?:guide\w*|checklists?|docs?|documentation|notes?|templates?|bots?|tools?|files?|panels?|queues?'
             r'|channels?|dashboards?|ui|pages?|settings?|buttons?|lists?)')
TOOL_NOUN = r'(?!\s+' + TOOL_WORD + r'\b)'
DECIDER_PREFIX = r'(?:(?:repo(?:sitory)?|project)(?:[’\x27]s)?\s+)?'
DECIDER = (r'(?:me|us|moi|nous|(?:the\s+|a\s+|an\s+|your\s+)?' + DECIDER_PREFIX + DECIDER_BASE + r's?'
           r'|(?:le\s+|la\s+|les\s+|un\s+|l[’\x27])?(?:propriétaires?|mainteneu(?:r|rs|se|ses)|humains?))\b(?![’\x27]s\b)')
# Roles that decide when a merge is reserved or asked for: 'Merging is reserved for the release
# manager', 'Ask a reviewer before merging'. Kept out of DECIDER so 'Merge once a reviewer approves'
# stays an ordinary review gate. A plural possessive ('the reviewers’ guide') names a thing.
ROLE = (r'(?:(?:the|a|an|your|le|la|les|un|une)\s+)?(?:' + ROLE_BASE + r's?'
        r'|relecteu(?:r|rs|se|ses)|relectrices?|responsables?\s+des?\s+(?:versions?|releases?|livraisons?))\b'
        r'(?![’\x27](?:s\b|\s))' + TOOL_NOUN)
DECIDER_ROLE = r'(?:' + DECIDER + r'|' + ROLE + r')'
# Who merges a PR in the passive voice: 'PRs are merged by the owner'. 'Merged by a maintainer
# after review' or 'by the release manager on Fridays' describes an ordinary process instead.
OWNER_BY = (r'(?:me|us|moi|nous|(?:the\s+|your\s+)?(?:(?:repo(?:sitory)?|project)(?:[’\x27]s)?\s+)?owners?'
            r'|(?:le\s+|la\s+|les\s+|l[’\x27])?propriétaires?)\b(?![’\x27]s\b)' + TOOL_NOUN)
# Who approves or is asked: 'Merge once the owner approves', 'Ping me before merging'.
DECIDER_OR_I = r'(?:i|we|' + DECIDER + r')'
# Who may be the only one to merge: 'Only I may merge', 'Nobody but the owner merges'.
# '(?!-)' leaves 'Only human-written PRs' to its noun.
RESERVER = r'(?:(?:i|we)\b|' + DECIDER_ROLE + r')(?!-)'
# Whose call a merge is: 'my call', 'the owner's decision', 'the tech lead's call'.
POSSESSOR = (r'(?:my|our|mon|ma|notre|(?:the\s+)?(?:(?:repo(?:sitory)?|project)(?:[’\x27]s)?\s+)?'
             r'(?:' + DECIDER_BASE + r'|' + ROLE_BASE + r')s?[’\x27](?:s\b)?)')
DECIDER_ACT = r'(?:approv\w*|say\s+(?:so|go|ok)|gives?\s+(?:the\s+)?(?:go|ok|green\s+light)|signs?\s+off|confirms?)\b'
ASK_DECIDER = (r'(?:\b(?:ask|consult|check\s+with|confirm\s+with|ping|talk\s+to|consulte[rz]?)\s+' + DECIDER_ROLE
               + r'|\b(?:demande[rz]?|consulte[rz]?|préviens|prévenez)-(?:moi|nous)\b|\bme\s+(?:demander|consulter|prévenir)\b'
               + r'|\b(?:demande[rz]?|consulte[rz]?)\s+(?:au|aux|à\s+l[’\x27]?|à\s+la|à\s+un)\s*' + DECIDER_ROLE + r')')
# 'ask me first'; a bare 'before' only when it ends the clause or cell ('| ask me before |').
FIRST_WORD = r'(?:first|beforehand|d[’\x27]abord|avant|before(?=\s*(?:$|[|,;.)])))\b'
SCOPE_NEAR = 40
NOT_BEFORE = re.compile(r'\b(?:not|never|n[’\x27]t|no\s+need\s+to|pas|jamais)\s+(?:\w+\s+)?$')
CHECK_NOUN = r'(?:ci|checks?|tests?|builds?|lint\w*|status\s+checks?)'
# 'PRs are held to the same standard' names no hold, but 'held in the queue until I approve',
# 'held up' and 'held to my approval' do: only the standard idiom (and 'held accountable' or
# 'held responsible') is excluded, and only when
# no condition or speaker follows in its sentence ('held to a high bar until I approve').
# The window is bounded so the lookahead stays linear.
HELD_IDIOM = (r'(?:\s+(?:accountable|responsible)\b|\s+to\s+(?:(?:the|a|an|our|same|high\w*|strict\w*)\s+){0,3}'
              r'(?:standards?|bar|rules?|conventions?|quality|expectations?)\b)'
              r'(?![^.!?]{0,120}?(?:\b(?:until|till|pending|unless|before|wait\w*|jusqu\w*)\b|' + FIRST_PERSON + r'))')
HOLD_VERB = re.compile(r'\b(?:hold\w*|paus\w*|freez\w*|frozen|wait\w*|suspend\w*|attend\w*|gel\w*|block\w*|bloqu\w*'
                       r'|park\w*|held(?!' + HELD_IDIOM + r')|unmerged)\b')
# A held pull request or frozen branch needs no merge word or approver: 'All PRs are on hold.',
# 'Hold PRs.', 'Every PR needs my approval.', 'The main branch is frozen.'
PR_NOUN = r'\b(?:prs?|mrs?|pull[\s-]+requests?|merge[\s-]+requests?|demandes?\s+de\s+fusion)\b'
# 'PRs are not on hold' and 'The main branch is not frozen' lift the state instead.
NOT_HELD = r'(?!(?:not|no|never|pas|plus|jamais)\b)'
PR_STATE = (r'(?:on\s+hold|on\s+pause|en\s+pause|paused|suspended|frozen|parked|held(?!\s+(?:to|in|against|accountable|responsible|up)\b)'
            r'|en\s+attente|suspendue?s?|gelée?s?)')
# 'All PRs are blocked' holds them; 'Draft PRs are blocked', 'PRs are blocked by failing checks'
# or '... until CI passes' describe a gate. So the PRs open the clause, with at most a
# quantifier, and the state ends it, a human or freeze blocks it, or a time or event
# condition that is not a check follows.
# After punctuation, only a failure explanation that ends the sentence describes a gate:
# 'PRs are blocked: failing checks.' but not 'All PRs are blocked: CI is down.'
FAILURE_EXPLANATION = (r'\s*(?:the\s+)?(?:(?:failing|failed|red)\s+' + CHECK_NOUN + r'|' + CHECK_NOUN
                       + r'\s+(?:fail|fails|failed|(?:are|is)\s+(?:red|failing)))\s*(?:\)\s*)?$')
# A named human or explicit freeze is a hold; a role modifying a gate or thing is not.
# A possessive decision still names the human; a tool noun after it ('review queue') does not.
# 'lead time' names a delay, not a lead.
PR_BLOCKER_PERSON = (r'(?:(?:la\s+)?(?:décision|validation|approbation|relecture)\s+'
                     r'(?:du|de\s+la|de\s+l[’\x27]|des)\s*)?'
                     + DECIDER_PREFIX + r'(?:(?:core|security)\s+)?'
                     r'(?:me|us|moi|nous|' + DECIDER_BASE + r's?|' + ROLE_BASE + r's?|(?:tech|team)-leads?|leads?(?!\s+times?\b)|teams?'
                     r'|mainteneu(?:r|rs|se|ses)|propriétaires?|responsables?|administrat(?:eur|rice)s?'
                     r'|relecteu(?:r|rs|se|ses)|relectrices?|équipes?|chefs?)')
# The speaker's own decision: 'blocked by my decision', not 'blocked by our CI'.
PR_BLOCKER_MINE = r'(?:my|our|mon|ma|notre|nos|mes)\s+(?:decisions?|call|say-so|word|go-ahead|approval|décisions?|validation|accord)\b'
PR_BLOCKER_GATE = (r'(?:ci|checks?|checkers?|tests?|builds?|lint\w*|pipelines?|jobs?|workflows?|polic(?:y|ies)|gates?'
                   r'|failures?(?!\s+to\b)|outages?|runners?|timeouts?|scans?|coverage|(?<=github\s)actions|rules?|status|runs?|vérifications?|règles?|protection'
                   r'|requirements?|enforce\w*|counts?|notifications?)')
# Words that end the noun phrase after a person: a condition, relative, preposition,
# concession, coordination, determiner, verb or time adverb. 'the reviewer until checks pass' and
# 'the owner although (albeit, notwithstanding) CI is green' never reach the gate word.
PR_PHRASE_END = (r'(?:until|till|unless|if|while|when\w*|although|though|albeit|notwithstanding|however|regardless|even|despite|whereas|except|once|yet|wh(?:o|om|ose|ich)|that|pending|for|since|because|and|or|but|nor|so'
                 r'|as|on|in|at|to|with\w*|after|before|via|per|from|of|about|than|by|is|are|was|were|be|been|has|have|had'
                 r'|will|may|can|must|should|would|could|the|a|an|this|these|those|some|any|all|every|each|no'
                 r'|today|tonight|now|again|still|right|currently'
                 r'|jusqu\w*|tant|pendant|sauf|si|qui|que|et|ou|mais|avec|sans|après|avant|pour|sur|dans|du|de|des|d'
                 r'|le|la|les|l|un|une|ce|cet|cette|ces)\b')
FREEZE_NOUN = r'(?:freezes?|holds?|embargo(?:es)?|gels?)'
# A modifier is one word; a hyphenated compound ('end-to-end') is one word, so a phrase end inside
# it ends nothing. A freeze, hold or human-decision word, alone or in a compound, is never a
# modifier: 'the team's freeze rules', 'the owner's code-freeze gate' and 'the owner's final
# decision gate' name the freeze or the person, not a gate. So does a failure to act: 'the
# owner's failure to review'. 'Manual' vetoes only a manual gate, review or approval ('the
# maintainer's manual gate'); 'the team's manual QA tests' are an ordinary gate.
HUMAN_DECISION = r'(?:decisions?|approvals?|manual(?=[\s-]+(?:gates?|reviews?|approvals?|sign-?offs?)\b)|consent|permissions?)'
PR_VETO = r'(?!(?:' + FREEZE_NOUN + r'|' + HUMAN_DECISION + r')\b)'
PR_MODIFIER = r'(?!' + PR_PHRASE_END + r'(?!-))' + PR_VETO + r'\w+(?:-' + PR_VETO + r'\w+)*'
# The phrase's head (its last word before a phrase end or punctuation) decides: a gate or tool
# head names a thing ('team unit tests', 'reviewer assignment queue', 'the owner's CI'); any other
# head names the person ('the owner's final decision'). An unclassified phrase stays a hold.
# There is no word cap: each modifier is one whitespace-separated word, so matching stays linear.
# A hyphen joins words; a comma ends the phrase, and so does an em dash in the reading where readings() spaces it.
PR_GATE_HEAD = (r'(?:\s+|-)(?:' + PR_MODIFIER + r'\s+)*(?:' + PR_VETO + r'\w+-)*(?:' + PR_BLOCKER_GATE + r'|' + TOOL_WORD + r')\b'
                r'(?![’\x27-])(?=\s*(?:$|[^\w\s])|\s+' + PR_PHRASE_END + r')')
# Determiners, including quantifiers ('both maintainers', 'the other maintainers').
PR_DETERMINER = (r'(?:(?:the|a|an|my|our|your|their|his|her|all|both|any|some|either|each|other|two|three'
                 r'|le|la|les|un|une|du|des|mon|ma|mes|ton|ta|tes|notre|nos|votre|vos|leur|leurs|son|sa|ses)\s+|l[’\x27])')
# 'Code owner review' is GitHub's branch-protection gate, not a person's decision.
PR_BLOCKER = (PR_DETERMINER + r'{0,2}'
              r'(?:' + PR_BLOCKER_MINE + r'|(?!code\s+owners?\s+reviews?\b)(?:' + PR_BLOCKER_PERSON + r')\b'
              r'(?:[’\x27]s?(?=\s+\w))?(?![’\x27])(?!' + PR_GATE_HEAD + r')'
              # Modifiers up to the freeze word name the freeze ('the current release freeze', 'the end-of-year
              # code freeze'); a hyphenated compound is one modifier.
              r'|(?:(?!' + PR_PHRASE_END + r'(?!-))\w+(?:-\w+)*\s+)*(?:\w+-)*'
              + FREEZE_NOUN + r'\b(?![’\x27](?:s\b|\s)|-(?!(?:period|window)\b))' + TOOL_NOUN + r')')
PR_BLOCKED = (r'^\W*(?:(?:all|every|any|the|open|pending|toutes|tous|les)\s+){0,2}' + PR_NOUN
              + r'\s+(?:are|is|remain|stay|restent|reste|sont|est)\s+'
              r'(?:(?:now|currently|temporarily|all|still|again|actuellement|désormais|encore|toujours)\s+)?'
              r'(?:blocked|bloquée?s?)\b(?=\s*$|\s*[,;:)](?!' + FAILURE_EXPLANATION + r')'
              r'|\s+(?:until|till|while|jusqu\w*|tant|pendant|for\s+now|today|pour\s+le\s+moment)\b'
              r'(?!\s+(?:the\s+|a\s+|all\s+|la\s+|le\s+|les\s+)?(?:(?:failing|green)\s+)?' + CHECK_NOUN + r'\b)'
              r'|\s+(?:(?:again|still|now|currently|encore|toujours)\s+)?(?:by|pending|par)\s+'
              r'(?:(?:(?:a|an|code)\s+)?(?:review|revue|relecture)\s+(?:by|par)\s+)?' + PR_BLOCKER + r')')
# A PR state that PR_HELD reads as a gate, not a hold: 'held to the same standard', 'blocked by failing checks'.
PR_GATE = re.compile(r'(?P<gate_prefix>' + PR_NOUN + r'[^.!?]{0,80}?)\b(?:held|blocked|bloquée?s?)\b')
# Only a gate's own words, so the rest of its sentence is still read for a hold: 'All PRs are
# blocked by the maintainer until further notice.' keeps 'maintainer until further notice'.
# The prefix may itself contain an owner hold, so the substitution must preserve it.
GATE_WORDS = re.compile(PR_GATE.pattern + r'(?:\s+(?:until|till|by|for|on)\s+(?:the\s+)?'
                        r'(?:(?:failing|failed|red|required)\s+)?' + CHECK_NOUN
                        + r'(?:\s+(?:pass\w*|(?:are|is)\s+green))?)?'
                        r'|\b(?:the\s+)?' + CHECK_NOUN + r'\s+(?:fail\w*|(?:are|is)\s+(?:red|failing))')
# A lifted state: 'PRs are not on hold', 'PRs aren't on hold', 'PRs are no longer on hold'.
LIFTED = r'(?:(?:are|is|remain|stay|restent|reste|sont|est)\s+(?:not|no\s+longer|pas|plus)|(?:aren|isn)[’\x27]t)\s+'
# Imperatives open their clause, after an optional softener: 'Please freeze main.'
IMPERATIVE = r'^\W*(?:(?:please|now|kindly|temporarily)\s+)?'
PR_HELD = re.compile(
    PR_NOUN + r'(?:\s+\w+){0,2}?\s+'
    # Also a state after a lifted one: 'PRs are not on hold but frozen', 'PRs aren't on hold
    # anymore, but they are frozen'.
    + r'(?:' + LIFTED + PR_STATE + r'(?:\s+(?:anymore|any\s+longer))?,?\s+(?:but|mais)\s+'
    r'(?:(?:they\s+are|they[’\x27]re|ils\s+sont|elles\s+sont|now|still|rather|instead|are|is|sont|plutôt|désormais)\s+){0,2}'
    r'|(?:are|is|remain|stay|restent|reste|sont|est)\s+(?:' + NOT_HELD + r'\w+\s+)?)' + PR_STATE + r'\b'
    + r'|' + PR_BLOCKED
    + r'|' + IMPERATIVE + r'(?:hold|pause|freeze|suspend)\s+(?:all\s+|every\s+|the\s+|any\s+)?(?:open\s+|pending\s+)?' + PR_NOUN
    # Bare 'main' as the object: 'Freeze main until I approve', 'Hold main.'; 'Freeze the main
    # lockfile' names something else, so a condition word or the end of the clause must follow.
    + r'|' + IMPERATIVE + r'(?:hold|pause|freeze|suspend|gèle|gelez|bloque|bloquez|suspends|suspendez)\s+'
    r'(?:the\s+|la\s+branche\s+)?(?:main|master|trunk)(?:\s+branch)?'
    r'(?=\s*$|\s*[,;:)]|\s+(?:until|unless|till|pending|for|while|during|now|today|temporarily'
    r'|jusqu\w*|tant|pendant|maintenant|aujourd\w*)\b)'
    # 'Lock main' is also branch protection ('Lock the main branch for force pushes', 'Lock
    # main: require signed commits'), so only a time or condition makes it a hold.
    + r'|' + IMPERATIVE + r'lock\s+(?:the\s+)?(?:main|master|trunk)(?:\s+branch)?'
    r'(?=\s*$|\s*[,;)]|\s+(?:until|unless|till|pending|while|during|now|today|temporarily|for\s+(?:now|the\s+duration))\b)'
    + r'|\bkeep\s+(?:all\s+|the\s+)?(?:open\s+)?' + PR_NOUN + r'\s+on\s+hold\b'
    + r'|' + PR_NOUN + r'[^.!?;:]{0,30}\b(?:needs?|requires?|nécessitent|nécessite)\b[^.!?;:]{0,40}' + APPROVAL_WORD
    # Only a negation right before the state lifts it: 'Main not develop is frozen' holds.
    + r'|\b(?:branch\w*|branche\w*|main|master|trunk)\b(?:\s+\w+){0,2}?\s+(?:(?:is|are|est|sont)\s+)?'
    r'(?<!\bnot\s)(?<!\bno\s)(?<!\bnever\s)(?<!\bpas\s)(?<!\bplus\s)(?<!\bjamais\s)(?:frozen|gel(?:é|ée|és|ées))\b'
    # 'The main branch is paused', 'Main is on hold'. Pausing is common debugger and CI wording,
    # so the branch itself must open the clause and be paused: not 'Branch protection is paused',
    # 'Builds on main are paused' or 'When the debugger stops, main is paused'.
    + r'|^\W*(?:the\s+|la\s+|le\s+)?(?:(?:(?:main|master|trunk|default)\s+)?(?:branch(?:es)?|branche)(?:\s+(?:main|master|trunk))?'
    r'|main|master|trunk)\s+(?:is|are|remains?|stays?|ha(?:s|ve)\s+been|est|sont|reste|restent|a\s+été)\s+'
    r'(?:(?:now|currently|temporarily|actuellement|désormais)\s+)?'
    r'(?:paused|suspended|on\s+hold|on\s+pause|en\s+pause|en\s+attente|suspendue?s?)\b')
# Restrictions that carry pause, approval or wait wording. Unlike a bare negation
# ('- Do not add dependencies' beside '- Merge requests use squash'), they are
# also read across the items of one list or the rows of one table.
# Each branch lists the word classes of RUN_WORDS it needs.
HOLD_BRANCHES = (
    (MERGE_WORD + r'[^.!?]{0,120}' + PAUSE_WORD, 'mp'),
    (PAUSE_WORD + r'[^.!?]{0,120}' + MERGE_WORD, 'mp'),
    # Also a label: 'Merge PRs: only after my approval'.
    (MERGE_WORD + r'(?:\s+[^\s.!?;:]+){0,4}?(?:\s*:[\s*_`]*|\s+)(?:only|seulement|uniquement|après|after|requires?|needs?|nécessite\w*|exige\w*'
     r'|requiert|(?:is|are)\s+subject\s+to)\b[^.!?]{0,90}' + APPROVAL_WORD, 'mqa'),
    (r'\bonly\s+' + MERGE_WORD + r'[^.!?;:]{0,60}\b(?:after|once|when|if)\b[^.!?;:]{0,60}' + APPROVAL_OR_FIRST, 'mx'),
    (APPROVAL_WORD + r'[^.!?]{0,90}\b(?:before|avant)\s+(?:de\s+|toute?\s+|any\s+)?(?:[^\s.!?;:]+\s+){0,3}?' + MERGE_WORD, 'mab'),
    (r'\bbefore\s+' + MERGE_WORD + r'[^.!?]{0,90}\b(?:obtain|get|seek|receive|wait\s+for)\b[^.!?]{0,90}' + APPROVAL_WORD, 'mboa'),
    (MERGE_WORD + r'[^.!?]{0,90}\b(?:wait\w*|attend\w*)\b[^.!?]{0,90}' + APPROVAL_WORD, 'mwa'),
    (r'\b(?:stop|hold|wait|attend\w*)\b[^.!?]{0,90}' + MERGE_WORD, 'mh'),
    # 'without' is left to the negated forms: 'merged without another ritual go' grants autonomy.
    (MERGE_WORD + r'[^.!?;:]{0,90}\b(?:until|unless|jusqu\w*|tant\s+que)\b[^.!?;:]{0,90}' + APPROVAL_OR_FIRST, 'mux'),
    # A label and its condition: 'Merging: until I say go.'
    (MERGE_WORD + r'[^.!?;:]{0,40}:[\s*_`]*(?:not\s+)?(?:until|unless|jusqu\w*|tant\s+que)\b[^.!?;:]{0,90}' + APPROVAL_OR_FIRST, 'mux'),
    (MERGE_WORD + r'[^.!?;:]{0,60}' + BAN_WORD + r'|' + BAN_WORD + r'[^.!?;:,]{0,60}' + MERGE_WORD, 'mn'),
    # A label and its rule: '**Merging:** not allowed', 'Prohibited: merging PRs'.
    (MERGE_WORD + r'[^.!?;:]{0,40}:[\s*_`]*' + BAN_WORD + r'|' + BAN_WORD + r'[\s*_`]*:[^.!?;]{0,60}' + MERGE_WORD, 'mn'),
    (MERGE_WORD + r'[^.!?;]{0,40}' + FREEZE_WORD + r'|' + FREEZE_WORD + r'[^.!?;]{0,30}' + MERGE_WORD, 'mz'),
    # 'PRs must be approved before merging', 'Merges must be approved by me'.
    (r'\b(?:must|shall|needs?\s+to|ha(?:s|ve)\s+to)\s+be\s+(?:approved|authori[sz]ed|signed\s+off)\b[^.!?]{0,60}' + MERGE_WORD
     + r'|' + MERGE_WORD + r'[^.!?]{0,60}\b(?:must|shall|needs?\s+to|ha(?:s|ve)\s+to)\s+be\s+(?:approved|authori[sz]ed|signed\s+off)\b', 'mae'),
    # 'Merge once the owner approves', 'PRs get merged when I say so'.
    (MERGE_WORD + r'[^.!?]{0,60}\b(?:once|when|after)\s+' + DECIDER_OR_I + r'\s+(?:\w+\s+)?' + DECIDER_ACT
     + r'|\b(?:once|when|after)\s+' + DECIDER_OR_I + r'\s+(?:\w+\s+)?' + DECIDER_ACT + r'[^.!?]{0,60}' + MERGE_WORD, 'md'),
    # 'Nothing is merged without my consent', 'Aucune fusion sans mon feu vert'; not 'without CI passing'
    # unless an approver follows ('without CI and my approval').
    (r'\b(?:nothing|nobody|no\s+one|aucune?|rien|personne)\b[^.!?;:]{0,40}(?:' + MERGE_WORD + r'|' + INTEGRATION_WORD.pattern + r')'
     + r'[^.!?;:]{0,60}\b(?:without|sans|until|unless|jusqu\w*|before|avant)\b'
     + r'(?:(?!\s+(?:the\s+|a\s+|all\s+|la\s+|le\s+|les\s+)?(?:green\s+)?(?:ci|checks?|tests?|builds?|lint\w*|status\s+checks?)\b)'
     + r'|[^.!?;:]{0,60}(?:' + APPROVAL_OR_FIRST + r'|\b' + DECIDER + r'))', 'mgc'),
    # 'Approval required for merges'; 'Approval is not required to merge' grants autonomy.
    (APPROVAL_WORD + r'\s+(?:(?:is|are|est|sont)\s+)?(?:required|needed|mandatory|requise?s?|obligatoires?|nécessaires?)\b'
     r'[^.!?]{0,90}' + MERGE_WORD, 'arm'),
    # Asking someone who decides, not a bot or CI: 'Ask me before merging'.
    (ASK_DECIDER + r'[^.!?]{0,60}\b(?:before|avant)\b[^.!?]{0,90}' + MERGE_WORD
     + r'|\b(?:before|avant)\s+(?:de\s+|any\s+|toute?\s+|you\s+|we\s+)?' + MERGE_WORD + r'[^.!?]{0,60}' + ASK_DECIDER, 'kbm'),
    # 'Merge PRs, ask me first', also across list items and table cells. The merge and the ask
    # sit side by side, so '- Keep your branch up to date by merging main' above '- For large
    # changes, ask a maintainer first', or 'Merge conflicts: ask the reviewer first', stay apart.
    (MERGE_WORD + r'(?:\s+(?:all\s+|the\s+|any\s+|a\s+|every\s+)?' + PR_NOUN + r')?[\s,:;|-]{1,6}'
     + ASK_DECIDER + r'\s+(?:\w+\s+)?' + FIRST_WORD
     + r'|' + ASK_DECIDER + r'\s+(?:\w+\s+)?' + FIRST_WORD + r'[\s,]*(?:(?:and|then|puis|et|before|avant\s+de)\s+){1,2}' + MERGE_WORD, 'mkf'),
    # 'Leave merging to me', 'Merging is reserved for the owner' or 'for the release manager';
    # 'Merge it for me' delegates instead. 'Up to' and 'left to' name a decider only: 'Squash
    # vs. merge commit is up to the reviewer' leaves a method choice, not the merge. The merge
    # word is read once, and each lookahead skips positions where no ending can start.
    (r'\bleav\w*\b[^.!?]{0,30}' + MERGE_WORD + r'[^.!?]{0,30}\bto\s+' + DECIDER_ROLE
     + r'|' + MERGE_WORD + r'(?:[^.!?]{0,60}(?=[ulr])\b(?:(?:up\s+to|left\s+to)\s+' + DECIDER
     + r'|(?:reserved\s+(?:to|for)|restricted\s+to|limited\s+to'
     r'|(?:réservée?s?|restreinte?s?|limitée?s?)\s+(?:à|aux?))\s+' + DECIDER_ROLE + r')'
     + r'|[^.!?]{0,20}(?=[mn])\b(?:me\s+|nous\s+|m[’\x27])(?:est|sont)\s+réservée?s?\b)', 'mtl'),
    # The branches below have no word classes (None): list and table runs never try them, so
    # their forms split over two items or rows stay unread ('- Only the owner' above '- merges
    # PRs'). Each unit is still read with its context, so a label item, parent item or heading
    # before it counts. One MERGE_WORD prefix is shared so each merge word is read once here.
    (MERGE_WORD + r'(?:'
     # 'Merges go through me', 'Les fusions passent par moi'; not 'through the merge queue',
     # 'via the admin panel' or 'through the maintainer bot'.
     r'(?:\s+(?:(?:always|toujours)\s+)?(?:(?:must|should|doivent|doit)\s+|ha(?:s|ve)\s+to\s+)?(?:(?:always|toujours)\s+)?'
     r'(?:go(?:es)?|pass(?:es)?|run|runs))?\s+(?:only\s+|exclusively\s+|uniquement\s+|seulement\s+)?(?:through|via)\s+'
     + DECIDER_ROLE + TOOL_NOUN
     + r'|\s+(?:(?:toujours|always)\s+)?(?:passe|passent|doivent\s+passer|doit\s+passer)\s+(?:(?:uniquement|seulement|toujours)\s+)?par\s+'
     + DECIDER_ROLE + TOOL_NOUN
     # 'PRs are merged by the owner', 'Merges are done by me', 'Les PR sont fusionnées par moi';
     # not 'merged by a maintainer after review', an ordinary process, nor 'PRs opened by me'.
     + r'|(?:\s+(?:is|are|get|gets|sont|est)\s+(?:done|handled|performed|made|faite?s?|gérée?s?|effectuée?s?))?'
     r'\s+(?:only\s+|exclusively\s+|uniquement\s+|seulement\s+)?(?:by|par)\s+' + OWNER_BY
     # 'Merging is my call', 'Merging a PR is the owner's decision'; not 'my favourite part of the
     # job', 'Merged PRs are my responsibility to monitor' or 'the reviewer's call between squash
     # and rebase'. Only a PR noun may sit between, so a label item ('- Merge PRs') cannot lend
     # its merge to the next item ('- Releases are my call').
     + r'|(?<!\bmerged)(?<!fusionné)(?<!fusionnée)(?<!fusionnés)(?<!fusionnées)'
     r'(?:\s+(?:all\s+|the\s+|a\s+|an\s+|les\s+|des\s+|une?\s+)?' + PR_NOUN + r')?'
     r'(?:\s+(?:is|are|remains?|stays?|reste|restent|est|sont)\s+(?:(?:always|solely|entirely|still|toujours)\s+)?|\s*:\s*)'
     + POSSESSOR
     + r'\s+(?:own\s+)?(?:call|decision|choice|prerogative|responsibility|décision|choix|responsabilité)\b'
     r'(?!\s+(?:to\s+(?!make\b|take\b)\w+|between|entre|of\s+(?:squash|rebase|merge|method)\w*)))'
     + r'|\blaiss\w*[\s-](?:moi|nous)\s+(?:(?:faire|gérer)\s+)?(?:les\s+|la\s+)?' + MERGE_WORD
     + r'|\blet\s+(?:me|us)\s+(?:(?:do|handle)\s+(?:all\s+)?(?:the\s+)?)?' + MERGE_WORD
     + r'|\bc[’\x27]est\s+(?:moi|nous)\s+qui\s+' + MERGE_WORD, None),
    # 'Only the owner merges PRs', 'Only I may merge', 'Only the owner is allowed to merge PRs',
    # 'Seul le propriétaire fusionne les PR'. Only a modal or permission may sit between, and the
    # merge word must not name a thing: 'Only admins can bypass merge checks', 'Only code
    # owners can approve merge requests'.
    (r'\b(?:only|seule?s?)\s+' + RESERVER + r'\s+(?:(?:can|may|must|should|shall|will|could|gets?\s+to|peut|peuvent|doit|doivent'
     r'|(?:is|am|are|be)\s+(?:allowed|permitted|authori[sz]ed)\s+to|ha(?:s|ve)\s+(?:the\s+)?(?:permission|right)\s+to'
     r'|(?:est|sont)\s+autorisée?s?\s+à|(?:a|ont)\s+le\s+droit\s+de)\s+)?' + MERGE_WORD
     + r'(?![\s-]+(?:requests?|checks?|conflicts?|commits?|queues?|settings?|methods?|strateg\w*|buttons?|messages?|markers?)\b)', None),
    # 'Nobody but me merges PRs', 'Nobody merges PRs except the owner'; 'Nobody merges without
    # CI' is read by the 'without' branch.
    (r'\b(?:nobody|no\s+one|personne)\s+(?:but|except|other\s+than|besides|apart\s+from|save|sauf|à\s+part)\s+' + RESERVER
     + r'[^.!?;:]{0,30}' + MERGE_WORD
     + r'|\b(?:nobody|no\s+one|personne)\s+(?:ne\s+)?' + MERGE_WORD
     + r'[^.!?;:]{0,30}\b(?:but|except|other\s+than|besides|apart\s+from|save|sauf|à\s+part)\s+' + RESERVER, None),
    # 'Merge nothing before my approval'; 'Merge nothing before CI passes' names no approver.
    (MERGE_WORD + r'\s+(?:nothing|rien|aucune?\s+\w+)\s+(?:before|avant)\b[^.!?;:]{0,60}' + APPROVAL_OR_FIRST, None),
    # 'I handle all merges myself', 'I'll merge PRs myself'; the speaker comes first, so '- Merge
    # PRs' above '- I write the docs myself' does not read as one rule. 'I merge my own PRs
    # myself' describes self-merging instead. Word steps, not a character span, keep a long run
    # of 'I' cheap.
    (r'\b(?:i|we|je|nous)(?:[’\x27](?:ll|m|d|ve|re))?(?:\s+[\w’\x27-]+){0,5}?\s+' + MERGE_WORD
     + r'(?!\s+(?:[\w’\x27-]+\s+)?own\b)(?:\s+[\w’\x27-]+){0,4}?\s+(?:myself|ourselves|moi-même|nous-mêmes)\b', None))
HOLD_FREE = '|'.join(pattern for pattern, _ in HOLD_BRANCHES)
# Word classes of HOLD_BRANCHES. A phrase such as 'on hold' may be split across
# two items, so its last word counts on its own.
RUN_WORDS = {
    'm': re.compile(MERGE_WORD),
    'p': re.compile(PAUSE_WORD + r'|\b(?:hold|attente)\b'),
    # Supersets of the words each class stands for: 'my' and 'go to' may sit in two items.
    'a': re.compile(APPROVAL_WORD + r'|\b(?:off|light|go|returns?|retour)\b'),
    'q': re.compile(r'\b(?:only|seulement|uniquement|après|after|requires?|needs?|subject|nécessite\w*|exige\w*|requiert)\b'),
    'b': re.compile(r'\b(?:before|avant)\b'),
    'o': re.compile(r'\b(?:obtain|get|seek|receive|wait)\b'),
    'w': re.compile(r'\b(?:wait\w*|attend\w*)\b'),
    'h': re.compile(r'\b(?:stop|hold|wait|attend\w*)\b'),
    'u': re.compile(r'\b(?:until|unless|jusqu\w*|que)\b'),
    'x': re.compile(APPROVAL_OR_FIRST + r'|\b(?:off|light|vert|go|returns?|retour)\b'),
    'n': re.compile(BAN_WORD + r'|\b(?:allowed|permitted|autoris\w*|permis\w*)\b'),
    'z': re.compile(FREEZE_STEM),
    'r': re.compile(r'\b(?:required|needed|mandatory|requise?s?|obligatoires?|nécessaires?)\b'),
    'k': re.compile(r'\b(?:ask|consult\w*|check|confirm|ping|talk|demande\w*|préven\w*|préviens)\b'),
    't': re.compile(r'\b' + DECIDER_ROLE),
    'l': re.compile(r'\b(?:leav\w*|up|left|reserved|restricted|limited|réservée?s?|restreinte?s?|limitée?s?)\b'),
    'f': re.compile(r'\b' + FIRST_WORD),
    'd': re.compile(r'\b' + DECIDER_ACT),
    'g': re.compile(r'\b(?:nothing|nobody|no\s+one|aucune?|rien|personne)\b'),
    # The connective 'Nothing is merged without my consent' needs, so '- Merge nothing' above a
    # long list of '- first' items is not read again on each of them.
    'c': re.compile(r'\b(?:without|sans|until|unless|jusqu\w*|before|avant)\b'),
    # A bare 'go' is an approval word, so 'must be approved' needs its own class.
    'e': re.compile(r'\b(?:approved|authori[sz]ed|signed)\b')}
# Word classes a branch can end on. Every branch can end on one of RUN_ENDS_ANY. An item with
# none of them, only a decider or 'first', can end only the branches below, and only on the
# listed classes: 'mkf' on 'first', 'kbm' and 'mtl' on who decides, 'mgc' on either. 'mtl' then
# ends on 'to the owner', 'reserved for me' or 'm'est réservée', so the run must hold one.
RUN_ENDS_ANY = frozenset('mpaxnz')
RUN_ENDS_DECIDER = {'mkf': (frozenset('f'), None), 'kbm': (frozenset('t'), None), 'mgc': (frozenset('ft'), None),
                    'mtl': (frozenset('t'), re.compile(r'\b(?:to|for|à|aux?)\s+' + DECIDER_ROLE
                                                       + r'|\b(?:me\s+|nous\s+|m[’\x27])(?:est|sont)\s+réservée?s?\b'))}
RUN_ENDS = RUN_ENDS_ANY | frozenset('ft')
# Each branch: pattern, needed word classes, the classes it ends on in an item with only a
# decider or 'first' and what the run must then hold, and whether it is read only in the new
# item and the one before it ('mkf' reads a merge and an ask side by side).
RUN_BRANCHES = tuple((re.compile(pattern), frozenset(words), *RUN_ENDS_DECIDER.get(words, (frozenset(), None)), words == 'mkf')
                     for pattern, words in HOLD_BRANCHES if words is not None)
# A HOLD_BRANCHES match has no sentence punctuation and, for ordinary word
# lengths, starts fewer than RUN_WINDOW characters before its last item, so a
# list or table is read in bounded windows.
RUN_WINDOW = 300
FREE = re.compile(
    HOLD_FREE
    + r'|\b(?:do\s+not|don[’\x27]t|no|never|ne\s+pas|pas\s+de|ne)\b[^.!?]{0,90}' + MERGE_WORD
    # Newer negators stay in one clause: 'PR titles must not exceed 72 characters; merge with squash'.
    # 'You should not need to merge main' only says it is unnecessary.
    + r'|\b(?:n(?=[’\x27])|jamais|(?:must|may|should|shall)\s+not|not\s+to|not\s+supposed\s+to|refrain\w*\s+from|cannot|can[’\x27]t|mustn[’\x27]t|shouldn[’\x27]t)\b'
    r'(?!\s+(?:need|have)\s+to\b)'
    r'[^.!?;:]{0,60}' + MERGE_WORD
    + r'|\bavoid\w*\s+(?:any\s+|all\s+)?(?:merging|merges?(?![\s-]+(?:conflicts?|commits?|markers?|bubbles?))|fusionner)\b'
    + r'|\b[ée]vit\w*\s+(?:de\s+fusionner|toute?\s+fusions?|les\s+fusions)\b'
    # One long rule sentence, read once per unit rather than per list window.
    + r'|' + MERGE_WORD + r'[^.!?;:]{0,120}' + BAN_WORD)
# Every FREE branch needs a merge word, or an integration word in the 'nothing is landed' form.
# Most units name neither, and this check costs far less than FREE's many branches.
FREE_NEEDS = re.compile(MERGE_WORD + '|' + INTEGRATION_WORD.pattern)

# A flagged unit that only states git mechanics is cleared when none of these apply.
HOLD_CONTEXT = re.compile(
    PAUSE_WORD + r'|' + APPROVAL_WORD
    + r'|' + FIRST_PERSON
    + r'|\b(?:until|unless|without|before|wait\w*|stop\w*'
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
NEGATIVE = {'no', 'not', 'never', 'don', 'avoid', 'nor', 'pas', 'jamais', 'ne', 'n', 'ni', 'cannot', 'mustn', 'shouldn',
            'isn', 'aren', 'prohibit', 'prohibits', 'prohibited', 'disallowed', 'banned'}
# A method clause clears only when every word is method vocabulary or plain grammar:
# a condition such as 'once the owner signs off' is never read as a permitted method.
METHOD_TOKENS = POSITIVE | NEGATIVE | {
    'merge', 'merges', 'merging', 'fusion', 'fusions', 'squash', 'rebase', 'rebases', 'fast', 'forward', 'ff',
    'noff', 'three', 'way', '3', 'octopus', 'recursive', 'ort', 'resolve', 'subtree', 'commit', 'commits', 'git', 'method',
    'methods', 'strategy', 'strategies', 'do', 'does', 't', 'the', 'a', 'an', 'and', 'or', 'but', 'instead',
    'rather', 'than', 'of', 'over', 'with', 'via', 'de', 'des', 'les', 'le', 'la', 'du', 'd', 'l', 'ou', 'et',
    'mais', 'plutôt', 'que', 'qu', 'au', 'lieu', 'par', 'ie', 'by', 'default', 'défaut', 'create', 'creates',
    'creating', 'keep', 'make', 'faire', 'faites', 'fais', 'créer', 'créez', 'crée',
    # 'Squash merges are not allowed; use merge commits.'
    'is', 'are', 'be', 'allowed', 'permitted', 'you', 'must', 'may', 'should', 'shall'}
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
# The rest of a cleared rule's section, subsections included, is read as a class, not a
# word list: a later unit before a heading of the same or a higher level returns 2 when it refers back to the rule or names who decides
# or checks ('A human must review each one.', 'Each one is manual.', 'Check with me first.').
NEXT_HOLD = re.compile(
    r'\b(?:each|every|them|they|it|its|these|those|this|that|such|one|ones'
    r'|humans?|people|person|someone|somebody|anyone|nobody|me|my|mine|i|we|us|our'
    r'|review\w*|confirm\w*|valid\w*|vet(?:s|ted|ting)?|allow\w*|permit\w*|green[\s-]?light\w*|lgtm'
    r'|clearance|bless\w*|okay\w*|thumbs?[\s-]+up|manual\w*|decid\w*|trigger\w*|check\w*|sign\w*'
    r'|chaque|chacune?|les|elles?|ils?|ceux|celles|cela|ça|humains?|personne|moi|je|nous|mon|ma|mes'
    r'|relu\w*|relecture|vérifi\w*|valid\w*|manuel\w*)\b')
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
UPSTREAM_BEFORE = re.compile(
    r'(?:[\s,]|\b(?:please|never|ever|do|not|don[’\x27]t|avoid|ne|n[’\x27]|jamais|pas|or|and|et|ou|rebase|onto'
    r'|cherry-pick\w*|pull|fetch|sync|copy|import)\b)*')
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


LEAD_LIMIT = 120
NEGATION = r'\b(?:not|never|no|avoid|don[’\x27]t|jamais|pas|ne|aucune?)\b'
# A negation whose next word is an adverb, preposition, determiner or idiom noun
# qualifies what follows instead of naming what it forbids: '- Never, ever again',
# '- Never under any circumstances', '- Do not do such things', '- En aucun cas'.
# A content word ('- Do not add dependencies', '- No secrets in commits') makes the
# item a complete rule of its own.
LEAD_START = (r'(?:again|ever|even|any|anymore|at|all|under|in|on|for|by|with|without|'
              r'from|during|before|after|until|unless|while|when|whatsoever|regardless|really|simply|'
              r'just|now|yet|today|once|whatever|whenever|except|outside|beyond|within|'
              r'the|this|that|these|those|such|circumstances?|conditions?|cases?|account|means|'
              r'times?|point|event|ways?|matter|reason|pretext|excuse|'
              r'au|aux|à|en|sous|dans|pour|sans|avant|après|pendant|aucune?|cas|prétexte|'
              r'circonstances?|raison|moment|façon|manière|sorte|du|tout|encore|même|maintenant|'
              r'le|la|les|ce|cet|cette|ces|cela|ça|ceci)')
LEAD_GAP = r'[\s;,:()\-–—>]'
BARE_LEAD = re.compile(r'.*' + NEGATION + r'(?:\s+(?:do|faire|faites|fais))?'
                       r'(?:' + LEAD_GAP + r'+' + LEAD_START + r'\b.*|' + LEAD_GAP + r'*)')
POINTER_LEAD = re.compile(
    NEGATION + r'.*\b(?:following|below|these|those|this|suivante?s?|ci[\s-]dessous|ces|ceci)\b')
# A preposition before the negation marks an idiom: '- Under no circumstances',
# '- At no point', '- By no means', '- D’aucune façon'.
IDIOM_LEAD = re.compile(r'(?:under|at|in|by|on|for|en|à|sous|dans|pour|de|d[’\x27])\s*(?:no|aucune?)\b')
FUNCTION_WORD = re.compile(LEAD_START + r'|of|it|them|to|a|an|do|if|as|so|long|other|than|possible|'
                           r'faire|faites|fais|de|d|l|un|une|des|sur|vers|si')


def negated_lead(text):
    """A negated list item that introduces its later siblings.

    It is a bare or qualified negation ('- Never,', '- Never under any
    circumstances'), points forward ('- Do not do the following for now' above
    '- merge pull requests') or carries hold wording ('- Do not, until I
    approve'); a complete rule such as '- Do not add dependencies' does not.
    """
    plain = text.strip(' \t*_`:')
    # Lead-ins are short; the bound also keeps the backtracking patterns cheap.
    if len(plain) > LEAD_LIMIT or terminated(text) or re.search(LEAD_MERGE_WORD, plain):
        return False
    negations = list(re.finditer(NEGATION, plain))
    if not negations:
        return False
    # A complete rule names at least two content words after its negation
    # ('- Do not add dependencies', '- No secrets in commits'); with fewer it
    # names nothing it forbids on its own ('- Do not proceed', '- No exceptions
    # whatsoever', '- Do not do such things').
    words = re.findall(r'[^\W_]+', plain[negations[-1].end():])
    content = [word for word in words if not FUNCTION_WORD.fullmatch(word)]
    return bool(len(content) <= 1 or BARE_LEAD.fullmatch(plain)
                or IDIOM_LEAD.match(plain) or POINTER_LEAD.search(plain) or HOLD_CONTEXT.search(plain))


def terminated(block):
    return block.rstrip(' \t*_`"\'»”’)]').endswith(('.', '!', '?'))


# Markdown emphasis and code-span markers at word edges: '**Merging**:', 'Only **I** may merge',
# 'Freeze `main`'. Inner underscores ('merge_queue') are part of the word and stay.
EMPHASIS = re.compile(r'(?<!\w)[*_`]+|[*_`]+(?!\w)')


def strip_markers(value):
    return DATED.sub('', EXAMPLE.sub('', EMPHASIS.sub('', re.sub(r'\s+', ' ', value))))


def run_class(text):
    """The text the run patterns read and its RUN_WORDS classes."""
    text = strip_markers(text)
    return text, ''.join(key for key, pattern in RUN_WORDS.items() if pattern.search(text))


def run_match(joined, last_two, candidates):
    """Whether a candidate branch matches the run's last sentence or its last two items."""
    return any(pattern.search(last_two if pair else joined) for pattern, pair, tail in candidates
               if tail is None or tail.search(joined))


def units(lines, hold_found, heading_found=lambda level: None):
    """Yield (context, unit) for each Markdown block and its wrapped lines.

    Context is the heading path, unterminated lead-in blocks, parent list items
    and a table's header row. Sibling list items and table data rows never give
    each other context. A fenced block is one unit.

    hold_found is called when a pause or approval restriction spreads over
    adjacent list items and table rows, or when a merge item follows a negated
    lead-in item of the same list or a negated lead-in row of the same table.
    """
    headings = []  # (level, text) of the current heading path
    lead = []      # unterminated blocks that introduce what follows
    items = []     # (indent, text) of the open list item chain
    intros = set()  # indents whose list holds a negated lead-in item so far
    header = None  # first row of the current table
    row_intro = False  # the current table holds a negated lead-in row so far
    block, kind, indent = [], None, 0
    fence = None   # (marker, context, lines) of an open fenced block
    run = collections.deque()  # texts of recent adjacent list items and table rows
    run_classes = collections.deque()  # their RUN_WORDS classes
    run_words = collections.Counter()  # RUN_WORDS classes across the run
    run_size, run_held = 0, False
    # Pure per-text work, so repeated items and run windows are read once.
    classify = functools.lru_cache(maxsize=128)(run_class)
    held = functools.lru_cache(maxsize=128)(run_match)

    def context(extra=(), near=()):
        """Heading path, then lead-ins and extra cut to CONTEXT_LIMIT, then near items uncut."""
        path = ' '.join(t for _, t in headings)[-CONTEXT_LIMIT:]
        parts, size = [], 0
        for part in reversed(lead + list(extra)):
            if size >= CONTEXT_LIMIT:
                break
            parts.append(part)
            size += len(part) + 1
        return ' '.join([path, ' '.join(reversed(parts))[-CONTEXT_LIMIT:], *near]).strip()

    def extend_run(text):
        nonlocal run_size, run_held
        if run_held:
            return
        # Sized on the text the patterns read, so padding or example markers
        # cannot push an earlier item out of the window.
        text, words = classify(text)
        # Keep at least RUN_WINDOW characters of earlier items before the new one.
        while len(run) > 1 and run_size - len(run[0]) - 1 >= RUN_WINDOW:
            run_size -= len(run.popleft()) + 1
            run_words.subtract(run_classes.popleft())
        run.append(text)
        run_classes.append(words)
        run_size += len(text) + 1
        run_words.update(words)
        # Only a restriction that ends in the new item is new; earlier ones were
        # read before. It cannot cross sentence punctuation, and each branch is
        # tried only when the run holds every word class it needs. An item naming
        # only who decides or 'first' ('- to the owner', '- ask a reviewer', '- avant')
        # can end only a branch that ends on those words, so only those are tried then.
        if len(run) == 1 or not RUN_ENDS.intersection(words):
            return
        present = {key for key, count in run_words.items() if count}
        decider_only = not RUN_ENDS_ANY.intersection(words)
        candidates = tuple((pattern, pair, tail if decider_only else None) for pattern, needed, ends, tail, pair in RUN_BRANCHES
                           if needed <= present and not (decider_only and ends.isdisjoint(words)))
        if not candidates:
            return
        joined = ' '.join(run)
        cut = len(joined) - len(text) - 1  # end of the earlier items
        start = max(joined.rfind('.', 0, cut), joined.rfind('!', 0, cut), joined.rfind('?', 0, cut)) + 1
        last_two = joined[max(start, cut - len(run[-2])):]
        joined = joined[start:]
        if held(joined, last_two, candidates):
            run_held = True
            hold_found()

    def end_run():
        nonlocal run_size
        run.clear()
        run_classes.clear()
        run_words.clear()
        run_size = 0

    def flush():
        nonlocal block, kind, lead, items, header, row_intro
        if not block:
            return None
        text = ' '.join(part.strip() for part in block)
        current, block, kind = kind, [], None
        if current == 'item':
            text = LIST_ITEM.sub('', text, 1)
            sibling = None
            while items and items[-1][0] >= indent:
                sibling = items.pop()
            intros.difference_update([i for i in intros if i > indent])
            parents = [t for _, t in items]
            # Added after the cut, so a long parent item cannot push it out.
            near = []
            if sibling and sibling[0] == indent and label(sibling[1]):
                near.append(sibling[1][-CONTEXT_LIMIT:])  # '- **Merges:**' labels the next sibling
            if indent in intros and re.search(MERGE_WORD, text):
                # '- Do not do the following' may govern every later sibling, whatever
                # comes between, so a merge item after it is ambiguous.
                hold_found()
            pair = context(parents, near), text
            items.append((indent, text))
            if negated_lead(text):
                intros.add(indent)
            extend_run(text)
            return pair
        if items and indent:
            # Indented content continues the list item above it.
            extend_run(text)
            return context(t for i, t in items if i < indent), text
        if items:
            # Unterminated open items still introduce the block after the list.
            lead = trim(lead + [t for _, t in items if not terminated(t)])
            items = []
            intros.clear()
        if current == 'row':
            if row_intro and re.search(MERGE_WORD, text):
                hold_found()  # '| Do not do the following |' above '| merge PRs |'
            pair = context([header] if header else []), text
            if header and negated_lead(text.replace('|', ' ')):
                row_intro = True
            # Adjacent lists and tables share one run, as a whole-file reading would.
            extend_run(text)
            header = header or text
            return pair
        header, row_intro = None, False
        end_run()
        pair = context(), text
        body = re.sub(r'<!--|-->', ' ', text).strip() if current == 'comment' else text
        lead = [] if terminated(body) else trim(lead + [body])
        return pair

    def enter_heading(level, text):
        nonlocal lead, items, header, row_intro
        while headings and headings[-1][0] >= level:
            headings.pop()
        pair = context(), text
        headings.append((level, text[:CONTEXT_LIMIT]))
        lead, items, header, row_intro = [], [], None, False
        intros.clear()
        end_run()
        heading_found(level)
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
    # Only a direct directive ('Never merge, rebase onto or cherry-pick from upstream')
    # is sync mechanics; 'No contributor may merge from upstream' restricts who merges.
    if not UPSTREAM_BEFORE.fullmatch(before):
        return False
    sources = list(UPSTREAM_SOURCE.finditer(after))
    if len(sources) != 1 or len(SOURCE_WORD.findall(after)) > 1 or WIDEN.search(before + after):
        return False
    between, rest = after[:sources[0].start()], after[sources[0].end():]
    if ',' in between and (not re.search(r'\b(?:or|and|et|ou)\b[^,]*\Z', between)
                           or re.match(r'[^,]*,\s*(?:or|and|et|ou)\b', between)):
        # 'Never merge, fetch from upstream' and 'Never merge, and fetch from
        # upstream' are two instructions; only a list of two or more verbs
        # closed by 'or' or 'and' coordinates them with the merge.
        return False
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


# 'Ship to production' or 'the main bundle' names no pull request; 'land on main' merges one.
PR_SCOPE = re.compile(
    r'\b(?:prs?|mrs?|pull[\s-]+requests?|merge[\s-]+requests?|demandes?\s+de\s+fusion|branch\w*|branche\w*)\b'
    r'|\b(?:into|onto|to|on|sur|dans)\s+(?:the\s+)?(?:main|master|trunk|default[\s-]+branch)\b')


def near_scope(text, match):
    # A label stays with its rule: 'PRs: hold until I approve'.
    before = re.split(r'[.!?;]', text[max(0, match.start() - SCOPE_NEAR):match.start()])[-1]
    after = re.split(r'[.!?;]', text[match.end():match.end() + SCOPE_NEAR])[0]
    return bool(PR_SCOPE.search(before) or PR_SCOPE.search(after))


# 'Integration:' opening a unit is a rule label for the merge step itself; 'Continuous
# integration:' and 'Integration tests:' name something else. The label also needs a speaker
# or a PR in its unit: 'Integration: needs admin consent in Entra ID' is an app integration.
# A speaker's approval of an app ('needs my approval to connect the app') still holds: no
# reviewed wording separates it from a merge approval in every context.
INTEGRATION_LABEL = re.compile(r'[\W_]*(integration|intégration)[*_`]*\s*:')
FIRST_PERSON_WORD = re.compile(FIRST_PERSON)


def integrations_as_merges(text):
    return INTEGRATION_WORD.sub(lambda match: 'merge' if near_scope(text, match) else match.group(), text)


def free_restriction(context, unit):
    """True for a restriction, 'cleared' for an exempted git-mechanics rule, 'gate' for a PR gate, else False."""
    text = (context + ' ' + unit).strip()
    if any(PR_HELD.search(part) for part in re.split(r'[.!?]', unit)):
        return True
    if PR_SCOPE.search(unit):
        # 'Hold all PRs until I say go.' holds integration without a merge word.
        # Read per sentence, as HOLD_BRANCHES never cross sentence punctuation.
        # 'Do not hold PRs for approval' lifts a hold instead.
        if any(re.search(APPROVAL_OR_FIRST, part) and any(
                near_scope(part, verb) and not NOT_BEFORE.search(part[max(0, verb.start() - SCOPE_NEAR):verb.start()]) for verb in HOLD_VERB.finditer(part))
               for part in re.split(r'[.!?]', unit)):
            return True
    label = INTEGRATION_LABEL.match(unit)
    if label and (FIRST_PERSON_WORD.search(unit) or PR_SCOPE.search(unit)):
        unit = unit[:label.start(1)] + 'merge' + unit[label.end(1):]
        text = (context + ' ' + unit).strip()
    if PR_SCOPE.search(unit):
        text = integrations_as_merges(text)
    if not FREE_NEEDS.search(text) or not FREE.search(text):
        # 'PRs are held to the same standard.' or 'PRs are blocked: failing checks.' describes a
        # gate, but a heading, lead-in, later sentence or later unit can still hold it
        # ('## Freeze until I approve'), so it is read like a cleared rule. Only sentences without
        # the gate are read here; scan_normalized reads the gate sentence without its GATE_WORDS.
        if PR_GATE.search(unit):
            rest = [part for part in re.split(r'[.!?]', unit) if not PR_GATE.search(part)]
            return True if any(FOLLOW_HOLD.search(part) or HOLD_CONTEXT.search(part) for part in rest) else 'gate'
        return False
    if HOLD_CONTEXT.search(text) or SCOPE.search(text) or NEVER_EXEMPT.search(text):
        return True
    # The exemption reads only the unit, so it cannot tell a negation inherited from
    # context ('Do not:' above 'Use squash merges.') from an affirmative method rule.
    if not FREE.search(unit):
        return True
    return 'cleared' if mechanics_only(unit) else True


def scan_normalized(text, restriction, strip):
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
    cleared, follows, held, headings = False, False, [], []
    mechanics, gate_follows = False, False  # a git-mechanics rule; a hold beside a gate's own words
    level, section = 0, None  # current heading level; level of the cleared rule's section
    for context, unit in units(outside, lambda: held.append(True), headings.append):
        if headings:
            level = headings.pop()
            if section is not None and level <= section:
                section = None  # a sibling or parent heading ends the cleared rule's section
        unit = strip(unit)
        context = strip(context)
        result = restriction(context, unit)
        if result is True:
            return 2
        if result in ('cleared', 'gate') and FOLLOW_HOLD.search(context):
            return 2  # a heading, lead-in or parent item conditions the rule
        if section is not None and NEXT_HOLD.search(unit):
            return 2  # a later unit of the section qualifies the cleared rule
        if result in ('cleared', 'gate') and section is None:
            section = level
        cleared = cleared or result in ('cleared', 'gate')
        # A gate's own words ('blocked', 'until CI passes') are not a hold, so they would clear
        # a gate beside a method rule; the rest of its unit still qualifies that rule.
        follows = follows or (result != 'gate' and bool(FOLLOW_HOLD.search(unit)))
        mechanics = mechanics or result == 'cleared'
        gate_follows = gate_follows or (result == 'gate' and bool(FOLLOW_HOLD.search(GATE_WORDS.sub(r'\g<gate_prefix> ', unit))))
    if held:
        return 2  # a list or table carries a pause or approval restriction across items
    # A hold or approval sentence anywhere in the file may qualify a cleared
    # rule without naming a merge, even from a sibling section under the
    # same parent heading.
    if cleared and follows or mechanics and gate_follows:
        return 2
    return 1 if found else 0


def outside_managed(text, choices):
    """Raw lines outside managed blocks under any of the readings in choices."""
    raw = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    keep = [False] * len(raw)
    for reading in choices:
        lines = reading.split('\n')
        if len(lines) != len(raw):
            return text  # lines do not align: check the whole file
        managed = False
        for index, line in enumerate(lines):
            if START.match(line):
                managed = True
            elif END.match(line):
                managed = False
            elif not managed:
                keep[index] = True
    return '\n'.join(line for line, kept in zip(raw, keep) if kept)


def hidden_splits(text):
    """Count in-word invisible characters, up to two, in text holding merge or pause vocabulary.

    Splits count file-wide: padding (visible or invisible) between two splits
    that need different readings must not hide one of them.
    """
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
        if following and following.group().isalnum():
            count += 1
            if count > 1:
                break
    return count


def scan_text(text):
    """0 clear, 1 dated canonical veto, 2 ambiguous/malformed evidence."""
    if len(text) > MAX_SCAN_CHARS:
        return 2
    # A hold in any reading is a hold, so each distinct reading is scanned in full.
    choices = readings(text)
    # Cache only pure classification; every unit still updates the scan state. The readings of one
    # file share most of their units, so they share caches sized for the distinct units of a file.
    restriction = functools.lru_cache(maxsize=4096)(free_restriction)
    strip = functools.lru_cache(maxsize=4096)(strip_markers)
    results = []
    for reading in choices:
        results.append(scan_normalized(reading, restriction, strip))
        if results[-1] == 2:
            return 2
    if hidden_splits(outside_managed(text, choices)) > 1:
        # Two readings cannot cover several invisible splits that need different choices.
        return 2
    return 1 if 1 in results else 0


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
