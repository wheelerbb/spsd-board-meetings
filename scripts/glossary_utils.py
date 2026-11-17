import difflib
import json
import re

import name_matching

# How many independent meetings must confirm a correction before it's promoted from "applies only
# to the meeting(s) that produced the evidence" (scope: [slug, ...]) to "applies everywhere"
# (scope key removed). Tunable — not yet validated against the real corpus, see todos/019.
PROMOTION_THRESHOLD = 2


def load_glossary(path='src/_data/glossary.json'):
    with open(path) as f:
        return json.load(f)


def _compact_entry(e):
    """Render one glossary entry as a single compact line — matches the file's existing
    hand-authored style, so automated writes don't produce reformatting-noise diffs."""
    return '{ ' + ', '.join(f'{json.dumps(k)}: {json.dumps(v)}' for k, v in e.items()) + ' }'


def save_glossary(entries, path='src/_data/glossary.json'):
    """Writes entries in the file's existing hand-authored style (one compact object per line).
    Iterates each entry's own keys rather than a fixed list, so it round-trips whatever fields an
    entry has (including new auto-discovery fields) and preserves existing entries' key order."""
    lines = ['[']
    for i, e in enumerate(entries):
        comma = ',' if i < len(entries) - 1 else ''
        lines.append(f'  {_compact_entry(e)}{comma}')
    lines.append(']')
    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')


def render_glossary_hints(entries):
    """Render entries into prompt-hint bullet text (consumed by process_transcripts.py and
    post_process.py). `note` is deliberately never included here — it stays in the file as
    human-readable metadata only. Injecting it into every meeting's prompt regardless of date is
    the exact date-blind role-fact shape that caused the original Superintendent misattribution
    bug (todos/013) — a pure spelling hint is timeless-safe; a role/context note is not."""
    lines = []
    for e in entries:
        line = f"- {e['correct']}"
        variants = _variants(e)
        if variants:
            line += f" (NOT {' or '.join(variants)})"
        lines.append(line)
    return '\n' + '\n'.join(lines)


def _variants(entry):
    """Every alternate spelling an entry corrects from — legitimate alternate forms (`aliases`)
    and genuine mishearings/typos (`misspellings`) alike. The two are split for readability in the
    glossary file itself (see todos/019), not because one is correctable and the other isn't."""
    return (entry.get('aliases') or []) + (entry.get('misspellings') or [])


def to_replacement_map(entries):
    """Derive alias->correct pairs for the find/replace pass, unfiltered by scope. Used as the
    building block for replacements_for_meeting() below; call that directly for the actual
    per-file enforcement pass."""
    return {alias: e['correct'] for e in entries for alias in _variants(e)}


def replacements_for_meeting(entries, slug):
    """alias->correct pairs applicable to one meeting file: entries with no `scope` (global —
    hand-curated or already-promoted, no distinction between the two) always apply; entries still
    carrying `scope` apply only to the meeting(s) that produced their evidence. This bounds the
    blast radius of a freshly-discovered, not-yet-corroborated correction to the specific
    meeting(s) it came from, instead of a blind sitewide rewrite."""
    applicable = [e for e in entries if not e.get('scope') or slug in e['scope']]
    return to_replacement_map(applicable)


def apply_replacements(content, replacements):
    """Applies alias->correct find/replace pairs (from replacements_for_meeting) to raw file
    content, word-boundary-safe (`\\b`) rather than a raw substring `content.replace()` — a short
    alias must never corrupt a substring inside an unrelated longer word (e.g. a `"main"` ->
    `"UMaine"` alias rewriting `domain`/`remain`/`maintenance` along with it). `\\b` is anchored on
    regex word-char boundaries, which covers every alias on file today — single words and
    multi-word phrases alike, since the boundary only needs to hold at the two outer edges. See
    todos/024 for the real, confirmed exposure this closes."""
    for alias, correct in replacements.items():
        content = re.sub(r'\b' + re.escape(alias) + r'\b', correct, content)
    return content


def _split_speaker_list(raw):
    """A topic's trailing parenthetical, or a timeline child's own `speaker` field, can jointly
    attribute the item to more than one person when they co-presented ("Sarah Lupien, Lizeth Rios
    Blas", "Gretchen McCloy & Bryna Latham") — split those apart instead of treating the whole
    joined blob as one person's name. Left unsplit, a multi-name string like that has more tokens
    than any single registry candidate, so match_unique can never fire; surname_match falls
    through instead, anchoring purely on the LAST token and silently absorbing every other name
    packed in front of it as if it were part of the same person's name — the 2026-11-16 "Sarah
    Lupien, Lizeth Rios Blas" conflation, and the same-shaped "&"-joined conflation found during
    Stage 3 of the registry-rebuild validation plan (todos/024). Splits on both "," and "&" since
    either shows up as the joiner depending on the source phrasing."""
    names = []
    for piece in re.split(r',|&', raw):
        piece = re.sub(r'^\s*and\s+', '', piece.strip(), flags=re.IGNORECASE).strip()
        if piece:
            names.append(piece)
    return names


def _speaker_names_in_meeting(meeting_data):
    """Raw person names in this meeting's own generated content: timeline child speakers, and
    topic titles' trailing parenthetical attribution (the "Description (Speaker)" convention from
    process_transcripts.py's extraction prompt). Both go through `_split_speaker_list` — a
    `speaker` field is just as capable of holding a joined co-presenter string as the
    parenthetical is."""
    names = []
    for item in meeting_data.get('timeline') or []:
        m = re.search(r'\(([^()]+)\)\s*$', item.get('topic') or '')
        if m:
            names.extend(_split_speaker_list(m.group(1)))
        for child in item.get('children') or []:
            if child.get('speaker'):
                names.extend(_split_speaker_list(child['speaker']))
    return names


def _text_fields(meeting_data):
    """Free-text fields worth scanning for place/organization/program mentions — these entity
    types have no structured "speaker"-style field to anchor on, unlike persons."""
    texts = []
    for item in meeting_data.get('timeline') or []:
        if item.get('topic'):
            texts.append(item['topic'])
        if item.get('desc'):
            texts.append(item['desc'])
        for child in item.get('children') or []:
            if child.get('text'):
                texts.append(child['text'])
    for s in meeting_data.get('summary') or []:
        if s.get('text'):
            texts.append(s['text'])
    return texts


_STRIP_CHARS = '.,()—-:;\'"'


def _norm_token(tok):
    """Lowercase a single token for comparison, stripping outer punctuation and normalizing the
    two punctuation variants that aren't real spelling differences: "&" reads the same as "and"
    (e.g. "Buildings & Grounds Committee" vs "...and Grounds..."), and a trailing possessive "'s"
    is dropped so "Portland's"/"Committee's" compare equal to the entity's own bare token instead
    of registering as a near-miss alias that needs "correcting" — which would mangle the sentence
    by eating the possessive when applied as a find/replace."""
    t = tok.strip(_STRIP_CHARS).lower()
    if t == '&':
        return 'and'
    if t.endswith("'s"):
        t = t[:-2]
    return t


def _tokens_match(window_tok, entity_tok):
    """One free-text word against the entity token at the SAME position — the fix for the
    2026-11-14 false-alias explosion, where a shared leading phrase let an unrelated trailing word
    slip through a whole-string ratio check (e.g. "Elementary Reconfiguration plan" scored >=0.8
    against "...Committee" purely off the shared first two words). Every position must agree, not
    just a leading anchor. A short token (<4 chars) or an acronym entity token
    (`name_matching.is_acronym`) requires exact agreement; a longer, non-acronym token tolerates a
    near-miss via the same length-scaled fuzzy floor as `name_matching.fuzzy_match`, which is what
    still lets "Caler" correct to "Kaler" here without reopening the door to a moderate-length
    coincidental resemblance to a common word ("Activitas" vs "activities"). Also rejects a
    boundary-variant pair (`name_matching.is_boundary_variant` — one token an exact prefix/suffix
    of the other, e.g. "main"/"maine", "August"/"Augusta") regardless of length, since that shape
    scores high via ratio for an unrelated word's own derivational form, not a mishearing — the
    2026-11-16 false aliases this caused on the real `glossary.json`, see todos/019.

    Also rejects a window token that's itself a recognized standalone English word
    (`name_matching.is_dictionary_word`) — the boundary-variant guard above only catches an exact
    prefix/suffix relationship ("main"/"maine"), not a same-length-ish edit-distance coincidence
    between two otherwise-unrelated real words ("State" vs "start"/"stage"/"taste"/"slate",
    "Maine" vs "machine"/"marine", "Brown" vs "grown"/"blown"). Confirmed at full-corpus scale
    during Stage 3 of the registry-rebuild validation plan: 326 of 1781 preview replacement
    occurrences, across 58 of 99 meetings, traced to exactly this gap — see todos/024. Checking
    only the window token (not the entity token) is deliberate: a registry entity that happens to
    also be an ordinary word (a U.S. state, "State") can still be legitimately corrected from a
    genuine OCR/mishearing artifact, which is essentially never itself a different real word."""
    entity_norm = _norm_token(entity_tok)
    window_norm = _norm_token(window_tok)
    if window_norm == entity_norm:
        return True
    if (name_matching.is_acronym(entity_tok.strip(_STRIP_CHARS))
            or name_matching.is_boundary_variant(window_norm, entity_norm)
            or name_matching.is_dictionary_word(window_norm)
            or len(window_norm) < 4 or len(entity_norm) < 4):
        return False
    ratio = difflib.SequenceMatcher(None, window_norm, entity_norm).ratio()
    length = min(len(window_norm), len(entity_norm))
    return ratio >= max(0.8, min(0.85, (length - 1) / length))


def _primary_note(context):
    """A single representative fragment from the registry's context string for the glossary's
    `note` field (human-readable metadata only — never shown to the model, see
    `render_glossary_hints`). The registry's own context can be several semicolon-joined
    fragments accumulated across documents; joining all of them into `note` verbatim reads as
    repetitive restatement of the same underlying fact rather than useful detail. Picks the
    longest surviving fragment as a cheap proxy for "most specific" rather than the first —
    earlier fragments tend to be Gemini's least-settled read of a term, with specificity arriving
    in later documents, not before."""
    fragments = [f.strip() for f in (context or '').split(';') if f.strip()]
    return max(fragments, key=len) if fragments else ''


def discover_term_candidates(meeting_data, registry):
    """Compares this meeting's own generated content against the ENTIRE Terms Registry — not just
    this meeting's own documents. This is a real capability gain over a meeting-scoped design: an
    entity extracted from a different meeting's official documents can still correct a mishearing
    here, since the registry is corpus-wide. Dispatches by registry entity type: person names via
    surname-anchored matching (fuzzy fallback for a surname-level mishearing, e.g. Atkinson Duina
    -> Atkinson Dena); place/organization/program via a per-token comparator (`_tokens_match`)
    across a same-length sliding window, since there's no "surname" to anchor on. Deduplicates
    repeated identical (type, correct, alias) observations within this one call — the same speaker
    or phrase mentioned several times in one meeting's content is one piece of evidence, not
    several. Returns observation dicts: {'correct', 'alias', 'note', 'type', 'slug'}."""
    slug = meeting_data.get('slug')
    persons = [e for e in registry.values() if e.get('type') == 'person']
    other = [e for e in registry.values() if e.get('type') in ('place', 'organization', 'program')]

    observations = []
    seen = set()
    for raw in _speaker_names_in_meeting(meeting_data):
        if not raw:
            continue
        match = name_matching.match_unique(raw, persons, name_key='term')
        via = 'exact' if match else None
        if match is None:
            match = name_matching.surname_match(raw, persons, name_key='term')
            via = 'surname' if match else None
        if match is None:
            match = name_matching.fuzzy_surname_match(raw, persons, name_key='term')
            via = 'fuzzy_surname' if match else None
        if match and match.lower() != raw.lower():
            correct, alias = match, raw
            # exact/surname hits are a shortform/longform containment relationship unless the two
            # sides assert different first names ("Elyse Holman" vs "Claire Holman") — that's a
            # genuine mishearing, not a legitimate alternate form. A surname-level mishearing
            # (fuzzy_surname) is never a legitimate alternate form either way.
            if via in ('exact', 'surname') and name_matching.is_subset_name(raw, match):
                kind = 'alias'
                # Favor whichever side is the complete first+last form as `correct`, even when
                # that's the raw mention rather than the registry's own (possibly title-only,
                # first-name-less) term — see is_complete_person_name.
                if name_matching.is_complete_person_name(raw) and not name_matching.is_complete_person_name(match):
                    correct, alias = name_matching.strip_title(raw), match
            # A differing first name is still a legitimate alternate form, not a mishearing, when
            # it's a nickname/full-name pair ("Jo"/"Johanna") rather than a genuine conflict
            # ("Elyse"/"Claire") — see is_nickname_variant. `correct` stays the registry's own
            # (official-doc-backed) spelling either way; this only affects which list it's filed
            # under, see apply_discovered_terms.
            elif via in ('exact', 'surname') and name_matching.is_nickname_variant(raw, match):
                kind = 'alias'
            else:
                kind = 'misspelling'
            # A bare surname alone ("Smith") isn't a misspelling to correct, and it's too generic
            # to persist as an alias — see is_bare_surname.
            if kind == 'alias' and name_matching.is_bare_surname(alias):
                continue
            key = ('person', correct, alias)
            if key in seen:
                continue
            seen.add(key)
            entity = next((e for e in persons if e['term'] == match), {})
            observations.append({
                'correct': correct, 'alias': alias, 'type': 'person', 'kind': kind,
                'note': _primary_note(entity.get('context', '')), 'slug': slug,
            })

    for text in _text_fields(meeting_data):
        words = text.split()
        for entity in other:
            entity_tokens = entity['term'].split()
            if not entity_tokens:
                continue
            n = len(entity_tokens)
            for i in range(len(words) - n + 1):
                window = words[i:i + n]
                if not all(_tokens_match(w, e) for w, e in zip(window, entity_tokens)):
                    continue
                if [_norm_token(w) for w in window] == [_norm_token(e) for e in entity_tokens]:
                    continue  # same word(s), only a punctuation/possessive difference
                candidate = ' '.join(window).strip(_STRIP_CHARS)
                key = (entity.get('type'), entity['term'], candidate)
                if key in seen:
                    continue
                seen.add(key)
                observations.append({
                    'correct': entity['term'], 'alias': candidate, 'type': entity.get('type'),
                    'kind': 'misspelling',  # same-length token-position mismatch, never containment
                    'note': _primary_note(entity.get('context', '')), 'slug': slug,
                })
    return observations


def dedupe_observations(observations):
    """Collapse exact-duplicate (type, correct, alias, slug) observations surfacing from
    independent discovery mechanisms — e.g. a registry match in `discover_term_candidates` and
    the cross-meeting fallback in `discover_recurring_speaker_candidates` both flagging the same
    alias for the same meeting. `apply_discovered_terms` is already idempotent against this (a
    repeat alias/scope update is a no-op), but the combined observations list itself is worth
    keeping duplicate-free for review/debugging."""
    seen = set()
    out = []
    for o in observations:
        key = (o.get('type'), o['correct'], o['alias'], o.get('slug'))
        if key in seen:
            continue
        seen.add(key)
        out.append(o)
    return out


def discover_recurring_speaker_candidates(meetings_data):
    """Cross-meeting fallback for names with no official-doc/registry backing at all (e.g. a
    recurring public commenter never named in any agenda). Groups every generated-content name by
    surname across the whole corpus; when a complete first+last spelling dominates over a
    strictly-less-frequent bare-surname/titled spelling of the same surname, flags the latter as a
    shortform alias of the former.

    Deliberately does NOT resolve two distinct complete spellings of the same surname against each
    other (e.g. "Melinda Hall" vs. "Angelica Hall"), even when one is far more frequent. Within one
    surname group, two spellings can only differ by a missing first name/title (safe — see
    is_subset_name) or a conflicting first name — and a conflicting first name is, from text alone,
    indistinguishable between "the same person's first name was misheard" and "two different real
    people happen to share a surname." This mechanism exists precisely because it has no
    registry/official-doc corroboration to tell those apart, and the first real-corpus run of it
    merged exactly such a pair (a parent/community organizer and an unrelated teacher, both named
    Hall — see todos/019). A genuine first-name mishearing of a person who IS backed by an official
    document is still caught by discover_term_candidates, which has that corroboration to act on."""
    by_surname = {}
    for m in meetings_data:
        for raw in _speaker_names_in_meeting(m):
            s = name_matching.surname(raw)
            if s:
                by_surname.setdefault(s, {}).setdefault(raw, set()).add(m.get('slug'))

    out = []
    for spellings in by_surname.values():
        if len(spellings) < 2:
            continue
        # A complete first+last spelling always outranks a bare-surname/titled one, even when the
        # latter is mentioned more often — see is_complete_person_name. Frequency is the
        # tiebreaker among spellings of the same completeness.
        ranked = sorted(
            spellings.items(),
            key=lambda kv: (not name_matching.is_complete_person_name(kv[0]), -len(kv[1])),
        )
        (majority, majority_slugs), *rest = ranked
        if len(majority_slugs) < PROMOTION_THRESHOLD:
            continue
        for minority, minority_slugs in rest:
            if not name_matching.is_subset_name(minority, majority):
                continue  # conflicting first name — an identity claim this fallback can't verify
            # A bare surname alone ("Smith") isn't worth persisting as an alias — see
            # is_bare_surname.
            if name_matching.is_bare_surname(minority):
                continue
            out.append({
                'correct': majority, 'alias': minority, 'type': 'person', 'kind': 'alias',
                'note': f"recurring speaker; {len(majority_slugs)}x vs {len(minority_slugs)}x",
                'slug': sorted(minority_slugs)[0],
            })
    return out


def _collision_candidates(glossary_entries, obs_type):
    """Entries safe to compare a new observation against for a surname/fuzzy collision — exclude
    only entries whose own type is known and differs from the observation's; an entry with no
    recorded type (every entry present before this mechanism existed) is never excluded."""
    return [e for e in glossary_entries if not obs_type or not e.get('type') or e.get('type') == obs_type]


def _plausible_correct_spelling(text):
    """Reject text that is almost certainly a missing-space text-extraction artifact (e.g.
    "SouthPortlandWineCompany" from a PDF that lost its spaces) rather than a genuine single-word
    proper noun ("Kaler", "Forecaster") — such artifacts must never become a glossary `correct`
    value, since render_glossary_hints would then tell Gemini that garbled spelling IS correct.
    A normal single word is short; a long run with no spaces anywhere is not."""
    return ' ' in text or len(text) <= 15


def apply_discovered_terms(glossary_entries, observations):
    """Mutates glossary_entries in place using {'correct','alias','note','type','slug','kind'}
    observations, where `kind` is 'alias' (a legitimate alternate form — shortform, title,
    role-reference) or 'misspelling' (a genuine transcription error) and selects which of the
    entry's two variant lists the alias is filed under; both lists are corrected from the same
    way, see _variants:
      - An entry whose `correct` already matches: add the alias (to whichever list `kind` picks,
        unless it's already filed in the other one) if new, widen `scope` with this slug if the
        entry still carries one (an already-global entry has none to widen — it just gets the new
        alias). Promote to global once `scope` reaches PROMOTION_THRESHOLD distinct meetings (the
        `scope` key is removed entirely).
      - No entry with that `correct`, but a surname/fuzzy collision with an existing entry: merge
        into it the same way. Every entry in this file is discovered the same way, so there's no
        "hand-curated vs. auto-discovered" category left to distinguish (see todos/019).
      - Otherwise: append a new entry, `scope: [slug]`.
    Returns True if mutated."""
    mutated = False
    for obs in observations:
        correct = obs['correct']
        if not _plausible_correct_spelling(correct):
            continue
        alias = obs['alias']
        slug = obs.get('slug')
        obs_type = obs.get('type')
        field = 'misspellings' if obs.get('kind') == 'misspelling' else 'aliases'
        other_field = 'aliases' if field == 'misspellings' else 'misspellings'

        existing = next((e for e in glossary_entries if e['correct'].lower() == correct.lower()), None)
        if existing is None:
            candidates = _collision_candidates(glossary_entries, obs_type)
            match = name_matching.match_unique(correct, candidates, name_key='correct')
            if match is None:
                match = name_matching.fuzzy_match(correct, candidates, name_key='correct')
            if match:
                existing = next((e for e in glossary_entries if e['correct'] == match), None)

        if existing:
            variants = existing.get(field) or []
            already_elsewhere = alias in (existing.get(other_field) or [])
            if alias not in variants and not already_elsewhere and alias.lower() != existing['correct'].lower():
                existing.setdefault(field, []).append(alias)
                mutated = True
            if 'scope' in existing:
                if slug and slug not in existing['scope']:
                    existing['scope'].append(slug)
                    mutated = True
                if len(existing['scope']) >= PROMOTION_THRESHOLD:
                    del existing['scope']
                    mutated = True
        else:
            glossary_entries.append({
                'correct': correct, field: [alias], 'note': obs.get('note', ''),
                'type': obs_type, 'scope': [slug] if slug else [],
            })
            mutated = True
    return mutated
