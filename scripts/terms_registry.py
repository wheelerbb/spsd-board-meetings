import json
import re
import threading

from sourcing import drive as drive_mod
import name_matching

# Gemini's own uncertainty about itself ("likely a board member," "appears to be") is not a role
# fact about the term, and "present at the meeting" / "listed under call to order" just restates
# that the term showed up in this document. Neither is worth accumulating into `context`, which
# otherwise grows into a semicolon-joined wall of near-duplicate hedging across documents instead
# of distinct facts (see todos/019). A prompt-level fix (post_process.py's term-extraction prompt)
# asks Gemini not to produce this phrasing in the first place; this is the backstop for whatever
# still slips through, since that's non-deterministic.
_HEDGE_RE = re.compile(
    r'\b(?:likely|probably|possibly|presumably|appears?\s+to|seems?\s+to)\b'
    r'|present\s+at\s+the\s+meeting'
    r'|listed\s+under\s+call\s+to\s+order',
    re.IGNORECASE,
)


def _is_hedge_fragment(fragment):
    return bool(_HEDGE_RE.search(fragment))

# A single, well-known GCS object — not fragmented across hundreds of per-document blobs like the
# legacy terms_blob pointers were. Not git-tracked: generated, corpus-wide, and too large for a
# useful diff, same reasoning as drive_catalog.json (see docs/pipeline.md).
REGISTRY_BLOB_NAME = 'terms_registry.json'

# post_process.py ingests terms for many meetings concurrently via a ThreadPoolExecutor, all
# mutating the same in-memory registry dict — without this, concurrent ingest_document_terms
# calls race on the same dict (observed in production as "dictionary changed size during
# iteration", see todos/019 work log). Owned here rather than by each caller, since this module is
# the one place that mutates the registry.
_registry_lock = threading.Lock()


def load_registry(bucket_uri):
    """Load the corpus-wide Terms Registry from GCS. Returns {} if no bucket is configured or
    nothing's been built yet."""
    if not bucket_uri:
        return {}
    cached = drive_mod.read_cached_blob(bucket_uri, REGISTRY_BLOB_NAME)
    if not cached:
        return {}
    try:
        return json.loads(cached)
    except Exception:
        return {}


def save_registry(bucket_uri, registry):
    if not bucket_uri:
        return
    drive_mod.write_blob(
        bucket_uri, REGISTRY_BLOB_NAME, json.dumps(registry, indent=2),
        content_type='application/json',
    )


def _registry_key(term_type, canonical):
    return f"{term_type}:{canonical}"


def _find_case_insensitive_key(registry, term_type, term):
    """The existing registry key for `term_type`:`term` when a same-type entity's own `term`
    differs from this one only by case, else None. Registry keys are case-sensitive
    (`_registry_key` performs no normalization), but the real-world entity a term names is not —
    an ALL-CAPS agenda-header extraction and a mixed-case minutes-body extraction of the same
    place/org/program name are the same entity, not two (see todos/021). Checked before any
    fuzzy/surname matching, so casing is purely a *lookup* concern at the one place every caller
    already goes through to find an existing entity — this doesn't touch fuzzy_match's broader
    "exact match means nothing to correct" contract, which stays correct for every other caller."""
    term_norm = term.lower()
    for k, e in registry.items():
        if e.get('type') == term_type and (e.get('term') or '').lower() == term_norm:
            return k
    return None


def entities_of_type(registry, term_type):
    return [e for e in registry.values() if e.get('type') == term_type]


def persons(registry):
    """All person-type entities, as {'term': canonical_name, ...} dicts suitable for
    name_matching.match_unique(..., name_key='term')."""
    return entities_of_type(registry, 'person')


def _person_match(term, candidates):
    """Unique candidate whose canonical term is the same person as `term`, matching trailing
    tokens in either direction — whichever string is shorter anchors the comparison. Unlike
    name_matching.match_unique (which only ever treats the *raw observation* as the potentially
    shorter side), this also lets a newly-extracted fuller name find and upgrade an existing
    shorter/partial registry entry, which matters here because both sides are "official
    extractions" with no inherent size ordering — a later document's richer mention of someone a
    prior document only named in part shouldn't become a second, disconnected entity.

    Both sides are title-stripped before comparing: a registered candidate ("Mr. Feller") is
    itself a raw extraction and may carry a title just as readily as the incoming term does — an
    unstripped candidate's title token was shadowing the trailing-token alignment and splitting
    e.g. "Mr. Feller" / "Mr. Daniel Feller" into two disconnected entities (see todos/019).
    Tokenizing (not just `.split()`) also treats a hyphen as a token separator, so "Atkinson
    Duina" and "Atkinson-Duina" line up as the same two-token surname instead of the hyphenated
    form looking one token short."""
    term_tokens = name_matching.name_tokens(name_matching.strip_title(term))
    if not term_tokens:
        return None
    matches = []
    for c in candidates:
        c_tokens = name_matching.name_tokens(name_matching.strip_title(c['term']))
        if len(term_tokens) <= len(c_tokens):
            shorter, longer = term_tokens, c_tokens
        else:
            shorter, longer = c_tokens, term_tokens
        if [t.lower() for t in longer[-len(shorter):]] == [t.lower() for t in shorter]:
            matches.append(c['term'])
    return matches[0] if len(matches) == 1 else None


def ingest_document_terms(registry, file_id, meeting_slug, doc_type, extracted_terms):
    """Merge one document's freshly-extracted (or cache-loaded) proper nouns into the
    consolidated, corpus-wide registry — using the same consolidation matching used everywhere
    else in the pipeline to fold near-duplicate extractions (e.g. "Kaler" and "Kaler Elementary
    School" from different documents, or "Ishmeel Daniels" vs. "Mr. Daniels") into one entity
    instead of leaving them as disconnected fragments. Mutates `registry` in place; returns True
    if anything changed. Thread-safe: callers may invoke this concurrently across meetings."""
    with _registry_lock:
        return _ingest_document_terms_locked(registry, file_id, meeting_slug, doc_type, extracted_terms)


def _ingest_document_terms_locked(registry, file_id, meeting_slug, doc_type, extracted_terms):
    mutated = False
    for t in extracted_terms or []:
        term = (t.get('term') or '').strip()
        term_type = t.get('type')
        if not term or not term_type:
            continue
        context = t.get('context') or ''

        key = _registry_key(term_type, term)
        existing = registry.get(key)

        if existing is None:
            ci_key = _find_case_insensitive_key(registry, term_type, term)
            if ci_key is not None:
                key = ci_key
                existing = registry[ci_key]

        if existing is None:
            same_type = entities_of_type(registry, term_type)
            match = _person_match(term, same_type) if term_type == 'person' else None
            if match is None:
                # Same title-stripping asymmetry as _person_match above: an unstripped title
                # (e.g. "Mr. ") dilutes the edit-distance ratio enough to miss a real match
                # (e.g. "Mr. Mitchell Ouellet" vs. "Mitch Ouellet" scores 0.79, just under the
                # 0.8 bar, vs. 0.90 once the title's gone) — strip it before falling back to the
                # generic fuzzy matcher, which has no notion of titles for its other callers
                # (place/organization/program).
                fuzzy_term = name_matching.strip_title(term) if term_type == 'person' else term
                match = name_matching.fuzzy_match(fuzzy_term, same_type, name_key='term')
            if match:
                existing = registry.pop(_registry_key(term_type, match))
                if term_type == 'person':
                    # Prefer a complete first+last form over a surname-only or titled fragment
                    # outright, even if it's shorter in characters than the fragment ("Dr. Holman"
                    # is longer than "Claire Holman" but less complete) — a document stating only
                    # "Ms. Holman" never asserted there's no first name, just omitted it (see
                    # todos/019). Between two forms of the same completeness, fall back to length.
                    term_complete = name_matching.is_complete_person_name(term)
                    existing_complete = name_matching.is_complete_person_name(existing['term'])
                    if term_complete and not existing_complete:
                        existing['term'] = term
                    elif term_complete == existing_complete and len(term) > len(existing['term']):
                        existing['term'] = term
                elif len(term) > len(existing['term']):
                    existing['term'] = term  # fuller spelling observed — upgrade
                key = _registry_key(term_type, existing['term'])
                mutated = True

        if existing is None:
            # Gemini's own type classification isn't perfectly consistent call-to-call — the
            # exact same literal string ("Taj", "GoFundMe", "Trump Accounts") has been observed
            # extracted as two different types across documents. Registry keys are `type:term`,
            # so without this check those would silently fork into two disconnected entities
            # instead of one. Exact-string match only (not fuzzy) — this is reconciling a
            # classification slip on an identical string, not a spelling variant — and only when
            # there's a single unique cross-type candidate, same safe-degrade-on-ambiguity
            # posture as every other matcher here. Keeps the first-established type/spelling
            # rather than guessing which type is "more correct".
            term_norm = term.lower()
            cross_type = [
                (k, e) for k, e in registry.items()
                if e.get('type') != term_type and e.get('term', '').lower() == term_norm
            ]
            if len(cross_type) == 1:
                old_key, existing = cross_type[0]
                registry.pop(old_key)
                key = old_key
                mutated = True

        if existing is None:
            existing = {
                'term': term, 'type': term_type,
                'context': context if context and not _is_hedge_fragment(context) else '',
                'sources': [], 'first_seen': meeting_slug, 'last_seen': meeting_slug,
            }
            mutated = True

        registry[key] = existing

        source = {'file_id': file_id, 'meeting': meeting_slug, 'doc_type': doc_type}
        if source not in existing['sources']:
            existing['sources'].append(source)
            mutated = True
        if context and not _is_hedge_fragment(context) and context not in (existing.get('context') or ''):
            existing['context'] = f"{existing['context']}; {context}" if existing.get('context') else context
            mutated = True
        if meeting_slug:
            if not existing.get('first_seen') or meeting_slug < existing['first_seen']:
                existing['first_seen'] = meeting_slug
                mutated = True
            if not existing.get('last_seen') or meeting_slug > existing['last_seen']:
                existing['last_seen'] = meeting_slug
                mutated = True
    return mutated


def canonical_person(raw_name, registry):
    """Resolve a raw extracted attendance/speaker name to its registry canonical spelling, trying
    three matchers in increasing order of fuzziness: an exact full-name/surname-only anchor
    (match_unique); a correct surname with a misheard first name (surname_match — the original
    "Elyse Holman" for "Claire Holman" bug); a misheard surname itself (fuzzy_surname_match —
    "Holeman" for "Claire Holman"). Returns None (leave the raw name as-is) rather than guessing on
    an ambiguous or absent match at any stage — this is the direct replacement for the retired
    board_members_utils.canonicalize_attendance_names(), now reading from the corpus-wide
    registry instead of a separately-maintained, date-scoped roster file."""
    candidates = persons(registry)
    match = name_matching.match_unique(raw_name, candidates, name_key='term')
    if match is None:
        match = name_matching.surname_match(raw_name, candidates, name_key='term')
    if match is None:
        match = name_matching.fuzzy_surname_match(raw_name, candidates, name_key='term')
    return match
