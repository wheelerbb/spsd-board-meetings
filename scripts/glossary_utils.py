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
        if e.get('aliases'):
            line += f" (NOT {' or '.join(e['aliases'])})"
        lines.append(line)
    return '\n' + '\n'.join(lines)


def to_replacement_map(entries):
    """Derive alias->correct pairs for the find/replace pass, unfiltered by scope. Used as the
    building block for replacements_for_meeting() below; call that directly for the actual
    per-file enforcement pass."""
    return {alias: e['correct'] for e in entries for alias in e.get('aliases', [])}


def replacements_for_meeting(entries, slug):
    """alias->correct pairs applicable to one meeting file: entries with no `scope` (global —
    hand-curated or already-promoted, no distinction between the two) always apply; entries still
    carrying `scope` apply only to the meeting(s) that produced their evidence. This bounds the
    blast radius of a freshly-discovered, not-yet-corroborated correction to the specific
    meeting(s) it came from, instead of a blind sitewide rewrite."""
    applicable = [e for e in entries if not e.get('scope') or slug in e['scope']]
    return to_replacement_map(applicable)


def _speaker_names_in_meeting(meeting_data):
    """Raw person names in this meeting's own generated content: timeline child speakers, and
    topic titles' trailing parenthetical attribution (the "Description (Speaker)" convention from
    process_transcripts.py's extraction prompt)."""
    names = []
    for item in meeting_data.get('timeline') or []:
        m = re.search(r'\(([^()]+)\)\s*$', item.get('topic') or '')
        if m:
            names.append(m.group(1).strip())
        for child in item.get('children') or []:
            if child.get('speaker'):
                names.append(child['speaker'].strip())
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


def discover_term_candidates(meeting_data, registry):
    """Compares this meeting's own generated content against the ENTIRE Terms Registry — not just
    this meeting's own documents. This is a real capability gain over a meeting-scoped design: an
    entity extracted from a different meeting's official documents can still correct a mishearing
    here, since the registry is corpus-wide. Dispatches by registry entity type: person names via
    surname-anchored matching (fuzzy fallback for a surname-level mishearing, e.g. Atkinson Duina
    -> Atkinson Dena); place/organization/program via fuzzy/containment matching only, since
    there's no "surname" to anchor on. Returns observation dicts:
    {'correct', 'alias', 'note', 'type', 'slug'}."""
    slug = meeting_data.get('slug')
    persons = [e for e in registry.values() if e.get('type') == 'person']
    other = [e for e in registry.values() if e.get('type') in ('place', 'organization', 'program')]

    observations = []
    for raw in _speaker_names_in_meeting(meeting_data):
        if not raw:
            continue
        match = name_matching.match_unique(raw, persons, name_key='term')
        if match is None:
            match = name_matching.surname_match(raw, persons, name_key='term')
        if match is None:
            match = name_matching.fuzzy_surname_match(raw, persons, name_key='term')
        if match and match.lower() != raw.lower():
            entity = next((e for e in persons if e['term'] == match), {})
            observations.append({
                'correct': match, 'alias': raw, 'type': 'person',
                'note': entity.get('context', ''), 'slug': slug,
            })

    for text in _text_fields(meeting_data):
        words = text.split()
        for entity in other:
            n = len(entity['term'].split())
            for i in range(len(words) - n + 1):
                candidate = ' '.join(words[i:i + n]).strip('.,()—-:;')
                match = name_matching.fuzzy_match(candidate, [entity], name_key='term')
                if match:
                    observations.append({
                        'correct': match, 'alias': candidate, 'type': entity.get('type'),
                        'note': entity.get('context', ''), 'slug': slug,
                    })
    return observations


def discover_recurring_speaker_candidates(meetings_data):
    """Cross-meeting fallback for names with no official-doc/registry backing at all (e.g. a
    recurring public commenter never named in any agenda — the Ishmeel Daniels case). Groups
    every generated-content name by surname across the whole corpus; when one spelling dominates
    over a strictly-less-frequent alternate spelling of the same surname, flags the minority
    spelling as a candidate."""
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
        ranked = sorted(spellings.items(), key=lambda kv: -len(kv[1]))
        (majority, majority_slugs), *rest = ranked
        if len(majority_slugs) < PROMOTION_THRESHOLD:
            continue
        for minority, minority_slugs in rest:
            if len(minority_slugs) >= len(majority_slugs):
                continue
            out.append({
                'correct': majority, 'alias': minority, 'type': 'person',
                'note': f"recurring speaker; {len(majority_slugs)}x vs {len(minority_slugs)}x",
                'slug': sorted(minority_slugs)[0],
            })
    return out


def _collision_candidates(glossary_entries, obs_type):
    """Entries safe to compare a new observation against for a surname/fuzzy collision — exclude
    only entries whose own type is known and differs from the observation's; an entry with no
    recorded type (every entry present before this mechanism existed) is never excluded."""
    return [e for e in glossary_entries if not obs_type or not e.get('type') or e.get('type') == obs_type]


def apply_discovered_terms(glossary_entries, observations):
    """Mutates glossary_entries in place using {'correct','alias','note','type','slug'}
    observations:
      - An entry whose `correct` already matches: add the alias if new, widen `scope` with this
        slug if the entry still carries one (an already-global entry has none to widen — it just
        gets the new alias). Promote to global once `scope` reaches PROMOTION_THRESHOLD distinct
        meetings (the `scope` key is removed entirely).
      - No entry with that `correct`, but a surname/fuzzy collision with an existing entry
        (hand-curated or auto-discovered — no distinction, see todos/019 "remove the protection,
        not the data"): merge into it the same way.
      - Otherwise: append a new entry, `auto_discovered: true`, `scope: [slug]`.
    Returns True if mutated."""
    mutated = False
    for obs in observations:
        correct = obs['correct']
        alias = obs['alias']
        slug = obs.get('slug')
        obs_type = obs.get('type')

        existing = next((e for e in glossary_entries if e['correct'].lower() == correct.lower()), None)
        if existing is None:
            candidates = _collision_candidates(glossary_entries, obs_type)
            match = name_matching.match_unique(correct, candidates, name_key='correct')
            if match is None:
                match = name_matching.fuzzy_match(correct, candidates, name_key='correct')
            if match:
                existing = next((e for e in glossary_entries if e['correct'] == match), None)

        if existing:
            aliases = existing.setdefault('aliases', [])
            if alias not in aliases and alias.lower() != existing['correct'].lower():
                aliases.append(alias)
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
                'correct': correct, 'aliases': [alias], 'note': obs.get('note', ''),
                'type': obs_type, 'auto_discovered': True, 'scope': [slug] if slug else [],
            })
            mutated = True
    return mutated
