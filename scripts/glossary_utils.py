import json


def load_glossary(path='src/_data/glossary.json'):
    with open(path) as f:
        return json.load(f)


def render_glossary_hints(entries):
    """Render entries into prompt-hint bullet text (consumed by process_transcripts.py)."""
    lines = []
    for e in entries:
        line = f"- {e['correct']}"
        if e.get('aliases'):
            line += f" (NOT {' or '.join(e['aliases'])})"
        if e.get('note'):
            line += f" — {e['note']}"
        lines.append(line)
    return '\n' + '\n'.join(lines)


def to_replacement_map(entries):
    """Derive alias->correct pairs for the find/replace pass (consumed by post_process.py)."""
    return {alias: e['correct'] for e in entries for alias in e.get('aliases', [])}
