import os
import re
import difflib

# Backs is_dictionary_word below — derived once from the macOS/BSD words(5) list
# (/usr/share/dict/words), filtered to lowercase-alphabetic entries, and checked into the repo so
# the check is dependency-free and portable (no new pip package, works identically in CI). Loaded
# lazily, not at import time, since most processes that import this module never call
# is_dictionary_word.
_DICTIONARY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'common_words.txt')
_dictionary = None


def is_dictionary_word(token):
    """True if `token` (case-insensitively) is an ordinary standalone English word, per the
    bundled dictionary — not a name or a proper noun. Scoped deliberately narrow: used only to
    reject a *place/organization/program* fuzzy-match candidate that's merely an unrelated real
    word a short edit-distance away from a registry entity's own spelling (see
    glossary_utils.py::_tokens_match) — a genuine OCR slip or transcription mishearing of such a
    term essentially never happens to also be a different, valid English word (`start`/`stage`/
    `slate` are not mishearings of `State`; they're just other words).

    Deliberately NOT used to guard person-name fuzzy matching (`fuzzy_match`/
    `fuzzy_surname_match` below) — unlike a place/org/program name, a misheard *surname* commonly
    does collide with an ordinary dictionary word (e.g. "Holeman" for "Holman" is itself a real,
    if obscure, dictionary entry) without being any less of a genuine mishearing. Applying this
    guard there would silently break that real, already-tested case.

    Falls back to stripping a common inflectional suffix (-s, -ed, -ing) and checking the stem —
    a dictionary headword list records base forms, not every inflection, so "started" alone
    (confirmed: not a headword, unlike "start") would otherwise slip past this guard and still
    fuzzy-match "State". See todos/024."""
    global _dictionary
    if _dictionary is None:
        try:
            with open(_DICTIONARY_PATH) as f:
                _dictionary = frozenset(line.strip() for line in f if line.strip())
        except FileNotFoundError:
            _dictionary = frozenset()
    t = (token or '').strip().lower()
    if not t:
        return False
    if t in _dictionary:
        return True
    for suffix in ('ing', 'ed', 's'):
        if t.endswith(suffix) and len(t) - len(suffix) >= 3 and t[:-len(suffix)] in _dictionary:
            return True
    return False


# Attendance/timeline names come from transcript roll calls or official documents, which state a
# role/office instead of (or alongside) an honorific — "board member Feller", "Chair DeAngelos",
# "Student rep Philippian" — not just "Mr./Ms. Surname". Left unstripped, those extra leading
# words would make a raw name longer than the matching registry entity's full name, which
# match_unique's suffix comparison requires raw_tokens <= full-name tokens for — so it could never
# match.
_TITLE_RE = re.compile(
    r'^(?:Mr\.?|Mrs\.?|Ms\.?|Dr\.?|Mx\.?|'
    r'Board\s+Vice\s+Chair|Board\s+Chair|Vice\s+Chair|Chair|'
    r'Board\s+members?|Members?|'
    r'Superintendent|'
    r'Student\s+rep(?:resentatives?)?s?)\s+',
    re.IGNORECASE,
)


def strip_title(name):
    """Remove a leading honorific/role title from a raw extracted name."""
    return _TITLE_RE.sub('', name or '').strip()


def name_tokens(name):
    """Split a name into comparison tokens, treating a hyphen the same as whitespace —
    "Atkinson-Duina" and "Atkinson Duina" are the same two-token surname written two different
    ways across different documents/sources (one page hyphenates a married name, another
    doesn't), not a spelling variant for fuzzy_match to catch. A plain `.split()` here would
    treat the hyphenated form as one token and never trailing-match the two-token form."""
    return [t for t in re.split(r'[\s-]+', (name or '').strip()) if t]


def surname(name):
    """Last token of a title-stripped name, lowercased — the stable grouping key for
    observations of the same person across differently-spelled extractions."""
    tokens = name_tokens(strip_title(name))
    return tokens[-1].lower() if tokens else None


def match_unique(raw_name, candidates, name_key='name'):
    """The unique candidate dict from `candidates` whose trailing name tokens exactly match
    raw_name's title-stripped tokens (case-insensitive) — or None if there's no match or more
    than one. Safe-degrade on ambiguity: a genuine collision (two candidates both matching)
    returns None rather than guessing, so callers can leave an unresolved name as-is instead of
    risking a wrong resolution. Only matches when raw_name has no more tokens than the
    candidate — a plain surname ("DeAngelis") anchors against a fuller name ("Rosemarie
    DeAngelis"), not the reverse."""
    raw_tokens = name_tokens(strip_title(raw_name))
    if not raw_tokens:
        return None
    matches = {
        c[name_key] for c in candidates
        if len(raw_tokens) <= len(name_tokens(c[name_key]))
        and [t.lower() for t in name_tokens(c[name_key])[-len(raw_tokens):]] == [t.lower() for t in raw_tokens]
    }
    return next(iter(matches)) if len(matches) == 1 else None


def surname_match(raw_name, candidates, name_key='name'):
    """Unique candidate sharing raw_name's exact surname (last token), regardless of whether the
    rest of the name matches — the direct fix for a correctly-identified person's first name
    being misheard (e.g. "Elyse Holman" for "Claire Holman"). Distinct from match_unique, which
    requires every one of raw_name's tokens to match the candidate's trailing tokens exactly, so
    it fails here precisely because the first name differs — and from fuzzy_surname_match, which
    handles the surname itself being misheard, not just the given name. Safe-degrade on ambiguity,
    same as the other matchers here: two candidates sharing that surname returns None."""
    s = surname(raw_name)
    if not s:
        return None
    matches = {c[name_key] for c in candidates if surname(c[name_key]) == s}
    return next(iter(matches)) if len(matches) == 1 else None


def is_complete_person_name(name):
    """True when a title-stripped name has at least a first and last token ("Claire Holman"), not
    just a surname ("Holman") or a title+surname ("Ms. Holman"). The registry and glossary should
    favor this complete form as the canonical spelling whenever it's genuinely known for a person,
    even over a form that's more frequently or more officially attested (see todos/019) — an
    official document stating only "Ms. Holman" isn't asserting the first name doesn't exist, just
    omitting it."""
    return len(name_tokens(strip_title(name))) >= 2


def is_subset_name(a, b):
    """True when the shorter title-stripped name's tokens are an exact match against the longer's
    trailing tokens (or the two are the same length and equal) — a legitimate shortform/longform
    relationship ("Holman" within "Claire Holman", "Ms. Holman" within "Claire Holman"), not a
    spelling difference. Order-independent, unlike match_unique, since the caller doesn't always
    know which side is the fuller one."""
    a_tokens = name_tokens(strip_title(a))
    b_tokens = name_tokens(strip_title(b))
    if not a_tokens or not b_tokens:
        return False
    shorter, longer = (a_tokens, b_tokens) if len(a_tokens) <= len(b_tokens) else (b_tokens, a_tokens)
    if len(shorter) == len(longer):
        return [t.lower() for t in shorter] == [t.lower() for t in longer]
    return [t.lower() for t in longer[-len(shorter):]] == [t.lower() for t in shorter]


def is_bare_surname(name):
    """True for a raw mention that is just a single surname token with no title and no first
    name ("Smith"), as distinct from a title+surname form ("Ms. Holman") or a complete name.
    Resolving one against a registry candidate doesn't make it worth persisting as a glossary
    alias: there's no misspelling to correct, and the bare surname alone is too generic to safely
    rewrite everywhere — a future meeting mentioning an unrelated same-surname person would get
    blindly renamed to today's candidate. See todos/019."""
    stripped = strip_title(name)
    return stripped == (name or '').strip() and len(name_tokens(stripped)) == 1


def is_nickname_variant(a, b):
    """True when two names (after title-stripping) share every token except the first, and that
    first-name pair is a short/full-name relationship in the common sense of one being an exact
    prefix of the other ("Jo"/"Johanna", "Liz"/"Elizabeth") — a legitimate alternate form, not a
    mishearing ("Johanna Prince" is not a misspelling of "Jo Prince", it's her full given name).
    Declines (returns False) for a nickname that isn't a literal prefix of the full name
    ("Bill"/"William") rather than maintaining a curated nickname dictionary, which would be
    incomplete and culture-specific — those still classify as a misspelling, same as before this
    existed; no regression, just no improvement for that subset yet. See todos/019."""
    a_tokens = name_tokens(strip_title(a))
    b_tokens = name_tokens(strip_title(b))
    if len(a_tokens) != len(b_tokens) or len(a_tokens) < 2:
        return False
    if [t.lower() for t in a_tokens[1:]] != [t.lower() for t in b_tokens[1:]]:
        return False
    first_a, first_b = a_tokens[0].lower(), b_tokens[0].lower()
    if first_a == first_b:
        return False
    shorter, longer = (first_a, first_b) if len(first_a) <= len(first_b) else (first_b, first_a)
    return len(shorter) >= 2 and longer.startswith(shorter)


def is_boundary_variant(a, b):
    """True when one (normalized) string is an exact prefix or suffix of the other — "main" of
    "maine", "maine" of "mainers", "august" of "augusta". This is the hallmark of an unrelated
    word's derivational/inflectional relationship (a demonym, a plural, an ordinary month name),
    not a genuine ASR mishearing — a real single-character mishearing is overwhelmingly a mid-word
    substitution or insertion ("Caler"/"Kaler", "Holeman"/"Holman"), not a boundary edit.
    SequenceMatcher's ratio scores a boundary insertion/deletion far higher than a same-length
    substitution at comparable length (0.89 for "main"/"maine" vs. 0.80 for "caler"/"kaler"), so
    the length-scaled floor in fuzzy_match alone doesn't reject it — this is an independent guard,
    checked before the ratio is ever computed. See todos/019."""
    a, b = (a or '').strip().lower(), (b or '').strip().lower()
    if not a or not b or a == b:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return longer.startswith(shorter) or longer.endswith(shorter)


def is_acronym(term):
    """True for a short, all-uppercase term ("ICE", "DOE", "SPSEA") — these don't get "misspelled"
    the way a name does, so a near-miss against one is almost always a coincidental resemblance to
    an ordinary lowercase word ("ICE" vs "service", "DOE" vs "does") rather than a genuine
    correction. fuzzy_match below requires exact agreement for these rather than any fuzzy
    tolerance. Length-bounded (<=6) so a longer all-caps proper noun, if one ever shows up, isn't
    swept into the same rule without reason to."""
    return term.isupper() and len(term) <= 6


def _token_containment(raw_norm, candidate_norm):
    """Whole-word containment: the shorter side's tokens must appear as a contiguous run inside
    the longer side's tokens, not a raw substring check. "Kaler" inside "Kaler Elementary School"
    is legitimate (the same place extracted with varying specificity across documents); "ice"
    inside "service" is not (it's a false match on letters that happen to appear inside an
    unrelated word, the root cause of a 2026-11-13-class corruption — see todos/019). A raw
    Python `in` check can't tell these apart; comparing token lists can. Each token has its outer
    punctuation stripped before comparing — e.g. "KLGC" and "L.D. 2106" both legitimately appear as
    the leading fragment of a longer extraction that continues straight into a comma ("KLGC,
    Relations With...", "L.D. 2106, An Act..."), and without this the trailing comma on the
    longer side's token would make an otherwise-identical token compare unequal."""
    strip_chars = '.,;:()—-'
    raw_tokens = [t.strip(strip_chars) for t in raw_norm.split()]
    candidate_tokens = [t.strip(strip_chars) for t in candidate_norm.split()]
    shorter, longer = (
        (raw_tokens, candidate_tokens) if len(raw_tokens) <= len(candidate_tokens)
        else (candidate_tokens, raw_tokens)
    )
    n = len(shorter)
    if n == 0:
        return False
    return any(longer[i:i + n] == shorter for i in range(len(longer) - n + 1))


def fuzzy_match(raw_text, candidates, name_key='term', min_ratio=0.8):
    """The unique candidate whose `name_key` value is a close-but-not-exact match for raw_text —
    for proper nouns with no stable "surname" to anchor on (places, organizations, programs), and
    as a fallback for a person whose surname itself was misheard (where match_unique's exact
    anchor structurally cannot help, e.g. "Atkinson Duina" misheard as "Atkinson Dena", or
    "Holman" misheard as "Holeman"). Two independent ways to match:
      - **Containment**: one string's tokens are a contiguous run inside the other's, at the
        whole-word level (`_token_containment` above) — common when the same place/org is
        extracted with varying specificity across different documents, not a spelling error at all.
      - **Edit-distance ratio** (stdlib difflib, no new dependency) — for same-length-ish strings
        that differ by a handful of characters (a mishearing or OCR slip), scored by
        `SequenceMatcher.ratio()` against a floor that scales up with string length, capped at
        0.85, rather than a flat `min_ratio` — a flat threshold is too permissive for medium-length
        strings: "Activitas" vs "activities" scores 0.84, comfortably over a flat 0.8 bar, despite
        being a 2-3 character edit away, not one. The 0.85 cap keeps this from also tightening
        legitimately-longer differences it isn't meant to touch, like a nickname expansion
        ("Mitch Ouellet" vs "Mitchell Ouellet", ratio 0.897) compared via fuzzy_surname_match's
        same-token-count tails.
    A candidate that `is_acronym` is still eligible for containment (an acronym can legitimately
    be the leading fragment of its own fuller extraction, e.g. "KLGC" inside "KLGC, Relations
    With..."), but never for the ratio path — only an exact (pre-normalized) match or whole-token
    containment counts for one, see `is_acronym`'s docstring. Same carve-out for `is_boundary_variant`
    (one full string an exact prefix/suffix of the other, e.g. "main"/"maine", "August"/"Augusta")
    — containment still applies (e.g. "Kaler" inside "Kaler Elementary School"), just not the ratio
    path, since that shape is otherwise indistinguishable from an unrelated word's own
    derivational/inflectional form. Returns None on an exact match (nothing to correct), no
    candidate clearing the bar, or more than one candidate tied for the best score (ambiguous —
    same safe-degrade as match_unique)."""
    raw_norm = raw_text.strip().lower()
    if not raw_norm or len(raw_norm) < 3:
        return None
    scored = []
    for c in candidates:
        raw_term = c[name_key].strip()
        candidate_norm = raw_term.lower()
        if candidate_norm == raw_norm:
            return None  # exact match — nothing to correct
        is_containment = _token_containment(raw_norm, candidate_norm)
        if is_containment:
            scored.append((1.0, c[name_key]))
            continue
        if is_acronym(raw_term) or is_boundary_variant(raw_norm, candidate_norm):
            continue
        ratio = difflib.SequenceMatcher(None, raw_norm, candidate_norm).ratio()
        length = min(len(raw_norm), len(candidate_norm))
        floor = max(min_ratio, min(0.85, (length - 1) / length)) if length else min_ratio
        if ratio >= floor:
            scored.append((ratio, c[name_key]))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None  # tied best match — ambiguous, don't guess
    return scored[0][1]


def fuzzy_surname_match(raw_name, candidates, name_key='name', min_ratio=0.8):
    """Fallback for match_unique when the surname itself was misheard (e.g. "Holeman" for
    "Claire Holman", or "Rocher" for "Susan Rauscher") — compares raw_name's own trailing tokens
    against each candidate's trailing tokens of the same length via fuzzy_match, rather than
    comparing full strings directly. A short surname-only observation scores poorly against a
    candidate's full name by whole-string ratio alone (the length mismatch dominates the score
    even when the surnames themselves are a near-perfect match), so the comparison is narrowed to
    same-length tails on both sides first."""
    raw_tokens = name_tokens(strip_title(raw_name))
    if not raw_tokens:
        return None
    n = len(raw_tokens)
    raw_tail = ' '.join(raw_tokens)
    tails = [
        {name_key: ' '.join(name_tokens(c[name_key])[-n:]), '_full': c[name_key]}
        for c in candidates if len(name_tokens(c[name_key])) >= n
    ]
    match = fuzzy_match(raw_tail, tails, name_key=name_key, min_ratio=min_ratio)
    if match is None:
        return None
    return next(t['_full'] for t in tails if t[name_key] == match)
