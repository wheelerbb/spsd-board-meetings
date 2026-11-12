import re
import difflib

# Attendance/timeline names come from transcript roll calls or official documents, which state a
# role/office instead of (or alongside) an honorific — "board member Feller", "Chair DeAngelos",
# "Student rep Philippian" — not just "Mr./Ms. Surname". Left unstripped, those extra leading
# words would make a raw name longer than the matching registry entity's full name, which
# match_unique's suffix comparison requires raw_tokens <= full-name tokens for — so it could never
# match.
_TITLE_RE = re.compile(
    r'^(?:Mr\.?|Mrs\.?|Ms\.?|Dr\.?|Mx\.?|'
    r'Board\s+Vice\s+Chair|Board\s+Chair|Vice\s+Chair|Chair|'
    r'Board\s+members?|'
    r'Student\s+rep(?:resentatives?)?s?)\s+',
    re.IGNORECASE,
)


def strip_title(name):
    """Remove a leading honorific/role title from a raw extracted name."""
    return _TITLE_RE.sub('', name or '').strip()


def surname(name):
    """Last whitespace token of a title-stripped name, lowercased — the stable grouping key for
    observations of the same person across differently-spelled extractions."""
    tokens = strip_title(name).split()
    return tokens[-1].lower() if tokens else None


def match_unique(raw_name, candidates, name_key='name'):
    """The unique candidate dict from `candidates` whose trailing name tokens exactly match
    raw_name's title-stripped tokens (case-insensitive) — or None if there's no match or more
    than one. Safe-degrade on ambiguity: a genuine collision (two candidates both matching)
    returns None rather than guessing, so callers can leave an unresolved name as-is instead of
    risking a wrong resolution. Only matches when raw_name has no more tokens than the
    candidate — a plain surname ("DeAngelis") anchors against a fuller name ("Rosemarie
    DeAngelis"), not the reverse."""
    raw_tokens = strip_title(raw_name).split()
    if not raw_tokens:
        return None
    matches = {
        c[name_key] for c in candidates
        if len(raw_tokens) <= len(c[name_key].split())
        and [t.lower() for t in c[name_key].split()[-len(raw_tokens):]] == [t.lower() for t in raw_tokens]
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


def fuzzy_match(raw_text, candidates, name_key='term', min_ratio=0.8):
    """The unique candidate whose `name_key` value is a close-but-not-exact match for raw_text —
    for proper nouns with no stable "surname" to anchor on (places, organizations, programs), and
    as a fallback for a person whose surname itself was misheard (where match_unique's exact
    anchor structurally cannot help, e.g. "Atkinson Duina" misheard as "Atkinson Dena", or
    "Holman" misheard as "Holeman"). Two independent ways to match:
      - **Containment**: one string is a substring of the other at the whole-word level (e.g.
        "Kaler" inside "Kaler Elementary School") — common when the same place/org is extracted
        with varying specificity across different documents, not a spelling error at all.
      - **Edit-distance ratio** (stdlib difflib, no new dependency) — for same-length-ish strings
        that differ by a handful of characters (a mishearing or OCR slip), scored by
        `SequenceMatcher.ratio()` against `min_ratio` (tunable; not yet validated against the
        real corpus — see todos/019).
    Returns None on an exact match (nothing to correct), no candidate clearing the bar, or more
    than one candidate tied for the best score (ambiguous — same safe-degrade as match_unique)."""
    raw_norm = raw_text.strip().lower()
    if not raw_norm or len(raw_norm) < 3:
        return None
    scored = []
    for c in candidates:
        candidate_norm = c[name_key].strip().lower()
        if candidate_norm == raw_norm:
            return None  # exact match — nothing to correct
        is_containment = raw_norm in candidate_norm or candidate_norm in raw_norm
        ratio = difflib.SequenceMatcher(None, raw_norm, candidate_norm).ratio()
        if is_containment or ratio >= min_ratio:
            scored.append((1.0 if is_containment else ratio, c[name_key]))
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
    raw_tokens = strip_title(raw_name).split()
    if not raw_tokens:
        return None
    n = len(raw_tokens)
    raw_tail = ' '.join(raw_tokens)
    tails = [
        {name_key: ' '.join(c[name_key].split()[-n:]), '_full': c[name_key]}
        for c in candidates if len(c[name_key].split()) >= n
    ]
    match = fuzzy_match(raw_tail, tails, name_key=name_key, min_ratio=min_ratio)
    if match is None:
        return None
    return next(t['_full'] for t in tails if t[name_key] == match)
