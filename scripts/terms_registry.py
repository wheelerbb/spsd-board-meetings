import json
import threading

from sourcing import drive as drive_mod
import name_matching

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
    prior document only named in part shouldn't become a second, disconnected entity."""
    term_tokens = name_matching.strip_title(term).split()
    if not term_tokens:
        return None
    matches = []
    for c in candidates:
        c_tokens = c['term'].split()
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
            same_type = entities_of_type(registry, term_type)
            match = _person_match(term, same_type) if term_type == 'person' else None
            if match is None:
                match = name_matching.fuzzy_match(term, same_type, name_key='term')
            if match:
                existing = registry.pop(_registry_key(term_type, match))
                if len(term) > len(existing['term']):
                    existing['term'] = term  # fuller spelling observed — upgrade
                key = _registry_key(term_type, existing['term'])
                mutated = True

        if existing is None:
            existing = {
                'term': term, 'type': term_type, 'context': context,
                'sources': [], 'first_seen': meeting_slug, 'last_seen': meeting_slug,
            }
            mutated = True

        registry[key] = existing

        source = {'file_id': file_id, 'meeting': meeting_slug, 'doc_type': doc_type}
        if source not in existing['sources']:
            existing['sources'].append(source)
            mutated = True
        if context and context not in (existing.get('context') or ''):
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
