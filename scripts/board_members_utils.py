import json
import re
from datetime import date

BOARD_MEMBERS_PATH = 'src/_data/board_members.json'

_TITLE_RE = re.compile(r'^(Mr\.?|Mrs\.?|Ms\.?|Dr\.?|Mx\.?)\s+', re.IGNORECASE)


def load_board_members(path=BOARD_MEMBERS_PATH):
    with open(path) as f:
        return json.load(f)


def _compact(d):
    """Render a small flat dict on one line, e.g. { "start": "2024-01-01", "end": null } —
    matches the hand-authored style of board_members.json's term/role entries, so automated
    writes don't reformat every pre-existing line into a noisy diff."""
    return '{ ' + ', '.join(f'{json.dumps(k)}: {json.dumps(v)}' for k, v in d.items()) + ' }'


def save_board_members(board_members, path=BOARD_MEMBERS_PATH):
    """Writes board_members in the file's existing hand-authored style (member objects
    expanded, each term/role entry compact on one line) so automated updates from
    apply_discovered_members() don't produce reformatting noise in the diff."""
    lines = ['[']
    for i, m in enumerate(board_members):
        lines.append('  {')
        keys = list(m.keys())
        for j, k in enumerate(keys):
            v = m[k]
            comma = ',' if j < len(keys) - 1 else ''
            if k in ('terms', 'roles') and isinstance(v, list):
                if not v:
                    lines.append(f'    {json.dumps(k)}: []{comma}')
                else:
                    lines.append(f'    {json.dumps(k)}: [')
                    for idx, item in enumerate(v):
                        item_comma = ',' if idx < len(v) - 1 else ''
                        lines.append(f'      {_compact(item)}{item_comma}')
                    lines.append(f'    ]{comma}')
            else:
                lines.append(f'    {json.dumps(k)}: {json.dumps(v)}{comma}')
        member_comma = ',' if i < len(board_members) - 1 else ''
        lines.append(f'  }}{member_comma}')
    lines.append(']')
    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')


def active_members(board_members, meeting_date):
    """Members whose term range covers meeting_date (a date object).
    Returns [{"name": full_name, "role": "Board"|"Student Rep"}, ...]."""
    result = []
    for m in board_members:
        for term in m.get("terms", []):
            start = date.fromisoformat(term["start"]) if term.get("start") else date.min
            end = date.fromisoformat(term["end"]) if term.get("end") else date.max
            if start <= meeting_date <= end:
                role = "Student Rep" if m.get("seat") == "Student Representative" else "Board"
                result.append({"name": m["name"], "role": role})
                break
    return result


def _strip_title(name):
    return _TITLE_RE.sub('', name or '').strip()


def _match_active(raw_name, active):
    """The unique active-roster full name whose trailing words match raw_name
    (title-stripped), or None if there's no match or more than one."""
    raw_tokens = _strip_title(raw_name).split()
    if not raw_tokens:
        return None
    matches = {
        m['name'] for m in active
        if len(raw_tokens) <= len(m['name'].split())
        and [t.lower() for t in m['name'].split()[-len(raw_tokens):]] == [t.lower() for t in raw_tokens]
    }
    return next(iter(matches)) if len(matches) == 1 else None


def canonicalize_attendance_names(attendance, active):
    """Resolve each attendance entry's name to a full name from `active` (the meeting-date-
    scoped roster from active_members()) when there's exactly one unique trailing-word match;
    otherwise leaves the transcript/document-extracted name unchanged. Never adds, removes, or
    reorders attendance entries and never touches status/role — who attended and their status
    stays sourced only from the transcript/official document, this only canonicalizes spelling."""
    canonical = []
    for entry in attendance:
        match = _match_active(entry.get('name', ''), active)
        new_entry = dict(entry)
        if match:
            new_entry['name'] = match
        canonical.append(new_entry)
    return canonical


def unresolved_attendance(attendance, active):
    """Attendance entries with no unique active-roster match — candidates for the
    board_members.json auto-discovery/convergence pass (see apply_discovered_members)."""
    return [
        {'name': e.get('name', ''), 'role': e.get('role', 'Board')}
        for e in attendance if _match_active(e.get('name', ''), active) is None
    ]


def apply_discovered_members(board_members, observations):
    """Mutates board_members in place using (name, role, meeting_date) observations that
    canonicalize_attendance_names() could not resolve to a unique active member, so the
    file converges toward full coverage over time instead of staying static:
      - If an existing entry we previously auto-discovered (marked "auto_discovered": true)
        shares the same surname (last name token, case-insensitive, title/first-name-
        insensitive), widen that entry's single term span to include the observed date, and
        upgrade its name to the fuller of the two spellings (e.g. a "Ms. Kinney" placeholder
        becomes "Jennifer Kinney" once a fuller name is observed).
      - Hand-curated entries (no "auto_discovered" flag) are never modified here — their term
        data is authoritative; a coverage gap there is for a human to fix, not to auto-widen.
        If an unresolved observation's surname matches a curated entry, it's skipped rather
        than risking a confusing duplicate.
      - Otherwise (no entry at all with that surname), append a new placeholder entry (title +
        surname, exactly as observed), marked "auto_discovered": true for easy human review.
    Returns True if board_members was mutated."""
    mutated = False
    for obs in observations:
        raw = obs['name'].strip()
        raw_stripped_tokens = _strip_title(raw).split()
        if not raw_stripped_tokens:
            continue
        raw_last = raw_stripped_tokens[-1].lower()
        meeting_date = obs['date']
        date_str = meeting_date.isoformat()

        same_surname = [m for m in board_members if m['name'].split()[-1].lower() == raw_last]
        existing = next((m for m in same_surname if m.get('auto_discovered')), None)

        if existing:
            existing_core_tokens = _strip_title(existing['name']).split()
            if len(raw_stripped_tokens) > len(existing_core_tokens):
                existing['name'] = _strip_title(raw)
                mutated = True
            terms = existing.setdefault('terms', [])
            if terms:
                # Widen the one running span rather than appending disjoint single-day terms,
                # so meetings between two observed dates also match on reprocessing.
                term = terms[0]
                start = date.fromisoformat(term['start']) if term.get('start') else meeting_date
                end = date.fromisoformat(term['end']) if term.get('end') else meeting_date
                new_start, new_end = min(start, meeting_date), max(end, meeting_date)
                if new_start != start or new_end != end:
                    term['start'], term['end'] = new_start.isoformat(), new_end.isoformat()
                    mutated = True
            else:
                terms.append({'start': date_str, 'end': date_str})
                mutated = True
        elif not same_surname:
            board_members.append({
                'name': raw,
                'seat': 'Student Representative' if obs.get('role') == 'Student Rep' else None,
                'terms': [{'start': date_str, 'end': date_str}],
                'roles': [],
                'auto_discovered': True,
            })
            mutated = True
        # else: a hand-curated entry already has this surname — leave it for human review.
    return mutated
