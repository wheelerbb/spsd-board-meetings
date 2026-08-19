import os
import sys
import json
import re
import yaml
import hashlib
import queue as _stdlib_queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from multiprocessing import Process, Queue as MpQueue
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

import socket
socket.setdefaulttimeout(120)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
from sourcing.auth import get_credentials
from sourcing import drive as drive_mod

credentials, project_id = get_credentials()
client = genai.Client(credentials=credentials, project=project_id, location='us-central1', vertexai=True)
from model_config import DEFAULT_MODEL, BACKUP_MODEL, call_with_fallback
MAX_WORKERS = 4
# Safety ceiling on how many meetings' evidence feed one topic's synthesis call — not a real
# token-budget constraint at current corpus/model scale (evidence chunks are short bullet lists,
# well within the model's context window), just a guard against unbounded future corpus growth.
MAX_EVIDENCE_MEETINGS = 60
TAGGING_TIMEOUT = 120  # seconds; enforced via subprocess SIGTERM

def _tagging_subprocess(model_name, prompt_text, result_queue):
    """Run a tagging Gemini call in a separate process so SIGTERM can hard-kill it on timeout."""
    local_credentials, local_project_id = get_credentials()
    local_client = genai.Client(
        credentials=local_credentials, project=local_project_id, location='us-central1', vertexai=True,
    )
    try:
        response = local_client.models.generate_content(
            model=model_name,
            contents=prompt_text,
            config={'response_mime_type': 'application/json', 'temperature': 0.1},
        )
        result_queue.put(('ok', response.text))
    except Exception as e:
        result_queue.put(('err', str(e)))

_MATCH_STOP = {'and', 'the', 'in', 'of', 'for', 'a', 'an', 'on', 'at', 'to', 'by', 'or', 'its', 'is', 'are', 'was', 'were', 'with', 'from'}

def _sig_words(s):
    """Significant words: 4+ chars, not stopwords."""
    words = re.sub(r'[^\w]', ' ', s.lower()).split()
    return {w for w in words if len(w) >= 4 and w not in _MATCH_STOP}

def _fy_norm(s):
    """Normalize FY20XX to FYXX for matching (e.g. FY2024 → FY24)."""
    return re.sub(r'\bfy20(\d{2})\b', r'fy\1', s.lower())

def _evidence_match(topic, bullet_topic, bullet_text):
    """True if this bullet is evidence for the given topic tag."""
    t_n = _fy_norm(topic)
    bt_n = _fy_norm(bullet_topic)
    # Existing substring checks with FY normalization
    if t_n in bt_n or bt_n in t_n or t_n in _fy_norm(bullet_text):
        return True
    # 2-word significant overlap with bullet topic name
    if len(_sig_words(topic) & _sig_words(bullet_topic)) >= 2:
        return True
    # 3-word significant overlap with bullet text body (only triggers for 3+ word tags)
    if len(_sig_words(topic) & _sig_words(bullet_text)) >= 3:
        return True
    return False

_TAG_GENERIC_EXAMPLES = "Policy Review, Community Engagement, School Operations, Board Governance, Student Recognition"

# Single source of truth shared with src/_data/meetings.js — do not hardcode this list in either
# place (see todos/005). Exact-match only: "Board Governance" is dropped, but a more specific tag
# like "Board Governance Ethics Policy" is untouched.
TOPIC_BLACKLIST_PATH = 'src/_data/topic_blacklist.json'
TOPIC_BLACKLIST = set(json.load(open(TOPIC_BLACKLIST_PATH))) if os.path.exists(TOPIC_BLACKLIST_PATH) else set()

def _drop_blacklisted(tag, slug):
    """Deterministic backstop for the generic-noun prompt rule — batch-scale tagging has
    repeatedly drifted toward exactly these bare category words despite explicit instructions
    not to, so this actually removes them rather than just warning."""
    if tag in TOPIC_BLACKLIST:
        print(f"    Dropped blacklisted tag '{tag}' for {slug} (see src/_data/topic_blacklist.json)")
        return None
    return tag

def _normalize_fy(tag):
    """Normalize FY20YY → FYYYY in a tag string (e.g. FY2027 → FY27)."""
    return re.sub(r'\bFY20(\d{2})\b', lambda m: f'FY{m.group(1)}', tag)

# Generic, subject-agnostic words describing what STAGE of board deliberation something is at
# ("X Development" / "X Discussion" could describe any topic at any point) — safe to strip from
# the end of any tag. Deliberately excludes words naming a substantive OUTCOME (Closure, Adoption,
# Referendum, Resignation, Appointment, ...) — those carry real meaning and must never be stripped.
_MODIFIER_SUFFIXES = {
    'Development', 'Presentation', 'Discussion', 'Update', 'Updates', 'Overview', 'Review', 'Reviews',
    'Consideration', 'Process', 'Session', 'Report', 'Reports', 'Debate', 'Announcement',
    'Revision', 'Revisions', 'Projection', 'Projections',
}
# Only stripped from cyclical (FY-prefixed) tags, where "one tag per fiscal year" already forces
# every phase — including the approval/vote moment — under a single tag regardless of wording.
_CYCLICAL_ONLY_SUFFIXES = {'Approval', 'Vote', 'Votes'}
_FY_PREFIX_RE = re.compile(r'^FY\d{2}\b')

def _strip_modifiers(tag):
    """Strip trailing generic process-stage words from a tag, e.g. 'FY26 Budget Development' ->
    'FY26 Budget', 'Cell Phone Policy Development' -> 'Cell Phone Policy'. Repeats until no more
    strippable trailing word remains."""
    words = tag.split()
    strip_set = _MODIFIER_SUFFIXES | _CYCLICAL_ONLY_SUFFIXES if _FY_PREFIX_RE.match(tag) else _MODIFIER_SUFFIXES
    while len(words) > 1 and words[-1] in strip_set:
        words = words[:-1]
    return ' '.join(words)

# Budget-cycle synonyms that sometimes get generated WITHOUT an FY prefix, which lets them slip
# past the cyclical "one tag per fiscal year" rule entirely (that rule only fires on tags that
# already start with "FY\d\d"). Deficits/shortfalls are always part of that year's budget story,
# not a separate topic — but which FY they belong to can't be reliably guessed from the meeting
# date alone: the district's actual budget calendar drifts year to year (e.g. 2025-11-03 is still
# "FY26 Budget" while 2025-12-17 is already "FY27 Budget" — a fixed month cutoff would mis-tag
# either the deficit talk in between, or meetings elsewhere in the corpus). So this only flags the
# gap for human visibility rather than guessing a fiscal year and risking silently wrong data —
# the actual fix is the prompt instruction above, which has full context to get the FY right.
_BUDGET_SYNONYM_KEYWORDS = ('deficit', 'shortfall')

def _warn_if_budget_synonym(tag, slug):
    if not _FY_PREFIX_RE.match(tag) and any(kw in tag.lower() for kw in _BUDGET_SYNONYM_KEYWORDS):
        print(f"    Warning: '{tag}' ({slug}) looks budget-related but has no FY prefix — "
              f"review whether it should be folded into that meeting's FY Budget tag.")
    return tag

def _update_topics_lib(new_tags, path='src/_data/all_topics.json'):
    existing = json.load(open(path)) if os.path.exists(path) else []
    existing_set = set(existing)
    added = [t for t in new_tags if t not in existing_set]
    if added:
        with open(path, 'w') as f:
            json.dump(added + existing, f, indent=2)

# Shared tag-quality rule text, interpolated into both generate_tags()'s (single-meeting) and
# batch_tag_all_meetings()'s (batch) prompts. Kept as single constants — not copy-pasted prose in
# each prompt — specifically so a future fix to one of these rules can't drift out of sync between
# the two tagging paths the way earlier iterations of this file had to be hand-synced.
_RULE_TIME_BINDING = """**Time-binding — 3 categories.** Whether (and how granularly) a tag carries a year/FY depends on what kind of issue it is:
   - **Cyclical** (budget cycles, audits, bargaining rounds, calendar approval): time-bound, but **one tag per fiscal year, period**. If a tag already exists for this fiscal year's instance of the cycle (e.g. an existing "FY27 Budget"), reuse it — do NOT create a new tag for a different phase or sub-event within the same cycle (no separate "FY27 Budget Approval" / "FY27 Budget Development" / "FY27 Budget Challenges"). Use a small, fixed vocabulary for the theme name itself — "Budget", "Audit", "School Calendar", "Collective Bargaining" — never invent a new theme word like "Financial Deficit" or "Staffing Adjustments" for what is really just that year's budget cycle. This includes deficits, shortfalls, and funding gaps — those are always part of that year's budget story, never a standalone tag; always keep the FY prefix on them ("FY27 Budget", never bare "Financial Deficit"). This district's fiscal year is named for its ending year and runs July 1 - June 30 (e.g. FY27 = July 2026-June 2027). Budget deliberation for a fiscal year typically happens in the spring before it starts (roughly January-June) — a budget-related tag from that window usually belongs to the UPCOMING fiscal year, not the one about to end. Occasionally a district starts budget planning for a later fiscal year unusually early (e.g. discussing next year's budget in December instead of the following spring) — when the content clearly indicates that, tag it with the fiscal year actually being discussed, not the one implied by a rigid calendar cutoff.
   - **Discrete initiatives** (a specific search, closure, or policy rollout): bind to a year/era only when the same type of event could plausibly recur later and future disambiguation will matter (e.g. "Superintendent Search", "Student Cell Phone Policy 2026").
   - **Evergreen** (standing, systemic domains with no natural end — special ed, transportation, facilities, equity, governance): never time-bound."""

_RULE_SUBJECT_NOT_PROCESS = """**Subject, not process stage.** A tag names the SUBJECT being discussed, never what stage of board deliberation it's at. Words like "Development", "Presentation", "Discussion", "Update", "Overview", "Review", "Consideration", "Process", "Session", "Report", "Debate", "Announcement", "Revision(s)", "Projection" describe *where something is in the meeting cycle*, not *what the topic is* — never end a tag with one of these (e.g. "Cell Phone Policy Development" → "Cell Phone Policy"). Words naming a specific, substantive outcome — "Closure", "Adoption", "Referendum", "Resignation", "Appointment" — are fine to keep; they're not process-stage words."""

_RULE_GENERIC_NOUN = (
    f'Generic category nouns ({_TAG_GENERIC_EXAMPLES}) are never valid tags — this includes exact matches '
    f'for the blacklisted words ({", ".join(sorted(TOPIC_BLACKLIST))}), which are dropped automatically even '
    f'if generated, so naming one just wastes the tag slot. This rule does not relax just because you\'re also '
    f'citing evidence bullets — pick the specific tag first, exactly as you would without an evidence '
    f'requirement, THEN find which bullets support it, never let ease of citation pull you toward a broader tag.'
)

_RULE_EVIDENCE_RELEVANCE = (
    "**Evidence relevance.** When citing evidence_bullets for a tag, cite only bullets where THIS "
    "topic is the actual subject of that bullet — not bullets that mention it only in passing while "
    "primarily discussing something else. If a topic genuinely has no bullet substantially about it, "
    "don't tag it at all."
)


def generate_tags(slug, summary_bullets, allowed_tags, topic_context=None, recent_topics=None, agenda_text=None):
    """Call Gemini to generate topic tags from a meeting's summary bullets.

    topic_context: optional {tag: current_status} — gives the model a description of what each
    existing tag actually covers, not just its bare name, so it can recognize a continuing thread.
    recent_topics: optional list of tags used at the board's most recent prior meeting.
    agenda_text: optional excerpt of the official agenda (or agenda items isolated from a packet)
    for this meeting — district-authored item framing, independent of the Gemini-generated summary.

    Returns (tags, evidence) — evidence is {tag: [summary_bullet_index, ...]}, populated by the
    model itself alongside the tags so synthesis doesn't have to reverse-engineer which bullets
    support which tag via fuzzy text matching (see _evidence_match / todos/012).
    """
    topic_context = topic_context or {}
    summary_text = '\n'.join(f'{i}. {b["topic"]}: {b["text"]}' for i, b in enumerate(summary_bullets))

    allowed_lines = []
    for t in allowed_tags:
        status = topic_context.get(t, '')
        allowed_lines.append(f"- {t}: {status}" if status else f"- {t}")
    allowed_text = '\n'.join(allowed_lines) if allowed_lines else '(none yet)'

    recent_text = ', '.join(recent_topics) if recent_topics else '(none — this is the first tagged meeting)'
    agenda_section = f"\n**Official agenda excerpt for this meeting:**\n{agenda_text}\n" if agenda_text else ""

    prompt = f"""You are tagging a school board meeting. Given the meeting summary below, identify 3-5 topic tags for the PRIMARY issues discussed.

**First-order rule:** Only tag a topic if it received substantial, independent discussion — not just a passing mention or as context within another topic. Ask: "Would a reader coming to this meeting specifically for this topic find meaningful content?" If not, omit the tag.

**Existing tags** (name: what it currently covers, when known):
{allowed_text}

**Topics discussed at the board's most recent prior meeting:** {recent_text}
If this meeting's content continues one of those threads, that's a strong signal to reuse the same tag rather than coin a new one — even if the wording of this meeting's bullets doesn't closely match the tag's name. Use the "what it currently covers" descriptions above, not just name-matching, to judge whether an existing tag fits.
{agenda_section}
**Tag selection rules, in priority order:**
1. **Reuse an existing tag** when it accurately describes the topic — judge this by what the tag covers (its description above and the recent-meeting context), not by superficial wording overlap with the tag's name.
2. {_RULE_TIME_BINDING}
3. {_RULE_SUBJECT_NOT_PROCESS}
4. **Create a new specific tag** when no existing tag fits. Name the specific issue, not a category — "SPESPA Contract 2025" not "Union Contracts"; "FY26 Staff Reductions" not "Property Taxes". {_RULE_GENERIC_NOUN}
5. {_RULE_EVIDENCE_RELEVANCE}

**Format rules:** Fiscal years must use FYYY format (FY27, FY26) — never FY2027 or FY2026. No parentheses or acronyms. No concatenated words. No symbols (&, /, etc.).

Meeting: {slug}
Summary (numbered):
{summary_text}

Respond with a JSON array of objects, one per tag: [{{"tag": "...", "evidence_bullets": [0, 2]}}, ...]
"evidence_bullets" are the 0-indexed numbers from the Summary list above that support this tag —
every tag must cite at least one. Example:
[{{"tag": "Elementary School Reconfiguration", "evidence_bullets": [0]}}, {{"tag": "FY27 Budget", "evidence_bullets": [1, 3]}}]"""

    def _attempt(model):
        q = MpQueue()
        p = Process(target=_tagging_subprocess, args=(model, prompt, q), daemon=True)
        p.start()
        p.join(timeout=TAGGING_TIMEOUT)
        if p.is_alive():
            p.terminate()
            p.join()
            raise TimeoutError(f"Tagging timed out after {TAGGING_TIMEOUT}s for {slug}")
        try:
            kind, val = q.get_nowait()
        except _stdlib_queue.Empty:
            raise Exception("Tagging subprocess exited with no result")
        if kind == 'err':
            raise Exception(val)
        return val

    val = call_with_fallback(_attempt)
    if val is None:
        raise Exception(f"All models rate-limited for tagging {slug}")
    raw = json.loads(val)

    evidence = {}
    tags = []
    n_bullets = len(summary_bullets)
    for entry in raw:
        tag = _drop_blacklisted(_warn_if_budget_synonym(_strip_modifiers(_normalize_fy(entry['tag'])), slug), slug)
        if tag is None:
            continue
        bullets = [i for i in entry.get('evidence_bullets', []) if isinstance(i, int) and 0 <= i < n_bullets]
        if tag not in evidence:
            tags.append(tag)
            evidence[tag] = []
        evidence[tag] = sorted(set(evidence[tag]) | set(bullets))
    return tags, evidence


from glossary_utils import load_glossary, to_replacement_map

# Single source of truth shared with process_transcripts.py — do not hardcode spelling
# corrections in either place (see todos/013).
GLOSSARY_PATH = 'src/_data/glossary.json'
GLOSSARY_REPLACEMENTS = to_replacement_map(load_glossary(GLOSSARY_PATH)) if os.path.exists(GLOSSARY_PATH) else {}


def _enforce_glossary_pass(meeting_dir, replacements=None):
    """Deterministic find/replace pass over every .njk file's raw content, using
    alias->correct pairs derived from src/_data/glossary.json. Writes back only
    changed files; returns parsed meetings_data list."""
    if replacements is None:
        replacements = GLOSSARY_REPLACEMENTS
    meetings_data = []
    for filename in sorted(os.listdir(meeting_dir)):
        if not filename.endswith('.njk'):
            continue
        filepath = os.path.join(meeting_dir, filename)

        with open(filepath, 'r') as f:
            content = f.read()

        orig = content
        for w, r in replacements.items():
            content = content.replace(w, r)
        if content != orig:
            with open(filepath, 'w') as f:
                f.write(content)

        m = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)', content, re.DOTALL)
        if m:
            try:
                data = yaml.safe_load(m.group(1)) or {}
                data['slug'] = filename.replace('.njk', '')
                meetings_data.append(data)
            except Exception as e:
                print(f"  Warning: could not parse {filename}: {e}")
    return meetings_data


def _read_njk(path):
    with open(path, 'r') as f:
        content = f.read()
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)', content, re.DOTALL)
    if not m:
        raise ValueError(f"Malformed front matter: {path}")
    return yaml.safe_load(m.group(1)) or {}, m.group(2)


def _write_njk(path, data, body):
    fm = yaml.dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)
    with open(path, 'w') as f:
        f.write(f'---\n{fm}---\n{body}')


def _extract_official_terms(meeting_data, bucket_uri, catalog):
    """Extract canonical proper nouns from agenda/packet/minutes docs; raw text and terms cached
    in GCS via the shared Drive catalog (file_id-keyed, not slug-keyed — see
    docs/pipeline.md#the-drive-catalog-drive_catalogjson)."""
    docs = meeting_data.get('docs') or []
    qualifying = [d for d in docs if d.get('type') in ('agenda', 'packet', 'minutes', 'min')]
    if not qualifying:
        return []

    slug = meeting_data['slug']
    svc = drive_mod.get_drive_service()

    all_terms = []
    for doc in qualifying:
        url = doc.get('url', '')
        fid_match = re.search(r'/file/d/([^/?]+)', url)
        if not fid_match:
            continue
        file_id = fid_match.group(1)
        doc_type = 'minutes' if doc.get('type') == 'min' else doc.get('type', 'doc')

        try:
            meta = svc.files().get(fileId=file_id, fields='modifiedTime,mimeType,createdTime').execute()
            current_modified = meta.get('modifiedTime')
        except Exception as e:
            print(f"    Warning: could not get Drive metadata for {file_id}: {e}")
            continue

        # Drive's own folder scan should already have catalogued this file; fill in a minimal
        # entry only for the rare case of a doc found solely via the SPSD site.
        entry = catalog.setdefault(file_id, {})
        entry.setdefault('doc_type', doc_type)
        entry.setdefault('mime_type', meta.get('mimeType'))
        entry.setdefault('created_time', meta.get('createdTime'))

        if entry.get('terms_blob') and entry.get('modified_time') == current_modified:
            cached = drive_mod.read_cached_blob(bucket_uri, entry['terms_blob'])
            if cached is not None:
                try:
                    terms = json.loads(cached)
                    all_terms.extend(terms)
                    print(f"    Loaded {len(terms)} terms for {slug}/{doc_type} from cache.", flush=True)
                    continue
                except Exception:
                    pass

        print(f"    Extracting terms for {slug}/{doc_type} (not in cache)...", flush=True)
        text, updates = drive_mod.get_or_extract_text(bucket_uri, svc, catalog, file_id)
        if updates:
            entry.update(updates)
        if not text:
            continue

        prompt = (
            "Extract all proper nouns from this school board meeting document. Include:\n"
            "- People with official roles (board members, administrators, staff)\n"
            "- Named places (schools, buildings, streets, districts)\n"
            "- Organizations and associations (unions, parent groups, state agencies)\n"
            "- Named programs, policies, or initiatives\n\n"
            "Return ONLY a JSON array: "
            "[{\"term\": \"...\", \"type\": \"person|place|organization|program\", "
            "\"context\": \"short description or role\"}]\n"
            "Use the exact spelling from the document. Return [] if nothing qualifies.\n\n"
            f"Document type: {doc_type}\n"
            f"Document text:\n{text}"
        )
        try:
            response = call_with_fallback(
                lambda model: client.models.generate_content(
                    model=model, contents=prompt, config={'temperature': 0.0}
                )
            )
            if response is None:
                raise Exception("All models rate-limited")
            raw = response.text.strip()
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            terms = json.loads(raw)
            entry['terms_blob'] = drive_mod.cache_terms(bucket_uri, file_id, entry, json.dumps(terms))
            entry['modified_time'] = current_modified
            print(f"    Extracted and cached {len(terms)} terms for {slug}/{doc_type}.", flush=True)
            all_terms.extend(terms)
        except Exception as e:
            print(f"    Warning: could not extract terms for {slug}/{doc_type}: {e}", flush=True)

    return all_terms


def _load_cached_agenda_text(docs, bucket_uri, catalog, max_chars=2500):
    """Read cached agenda-doc text for a meeting from GCS, looked up directly by each doc's
    file_id in the shared Drive `catalog` (no bucket listing, no slug-prefixed folder — see
    docs/pipeline.md#the-drive-catalog-drive_catalogjson). Falls back to isolating the agenda/order-of-
    business items out of a cached packet, via a Gemini call cached back to GCS so it only runs
    once per packet. Read-only otherwise — no new Drive fetches. Returns None if nothing is
    cached for any of this meeting's docs."""
    if not bucket_uri:
        return None

    def _file_id(url):
        m = re.search(r'/file/d/([^/?]+)', url or '')
        return m.group(1) if m else None

    def _cached_entries(doc_type):
        return [
            catalog[fid] for fid in (_file_id(d.get('url')) for d in (docs or []) if d.get('type') == doc_type)
            if fid and catalog.get(fid, {}).get('text_blob')
        ]

    agenda_entries = _cached_entries('agenda')
    if agenda_entries:
        texts = [t for t in (drive_mod.read_cached_blob(bucket_uri, e['text_blob']) for e in agenda_entries) if t]
        if texts:
            return '\n\n'.join(texts)[:max_chars]

    packet_entries = _cached_entries('packet')
    if not packet_entries:
        return None

    packet_entry = packet_entries[0]
    isolated_blob_name = packet_entry['text_blob'][:-4] + '.agenda-extract.txt'
    isolated = drive_mod.read_cached_blob(bucket_uri, isolated_blob_name)
    if isolated is not None:
        return isolated[:max_chars]

    packet_text = drive_mod.read_cached_blob(bucket_uri, packet_entry['text_blob'])
    if not packet_text:
        return None

    print("    Isolating agenda items from packet (not cached)...", flush=True)
    try:
        response = call_with_fallback(
            lambda model: client.models.generate_content(
                model=model,
                contents=(
                    "This is a school board meeting packet, which bundles the agenda with supporting "
                    "materials. Extract ONLY the agenda / order-of-business item list (the itemized "
                    "list of what the board will discuss or vote on) — not the attached supporting "
                    "documents, reports, or exhibits. Return the isolated agenda text only, no "
                    "commentary or markdown fences.\n\n"
                    f"{packet_text}"
                ),
                config={'temperature': 0.0},
            )
        )
        if response is None:
            raise Exception("All models rate-limited")
        isolated_text = response.text.strip()
        drive_mod.write_blob(bucket_uri, isolated_blob_name, isolated_text)
        return isolated_text[:max_chars]
    except Exception as e:
        print(f"    Warning: could not isolate agenda from packet: {e}")
        return None


def generate_agenda_preview(meeting_data, meeting_dir, bucket_uri, catalog):
    """Generate and write a topic-structured agenda_preview for an upcoming stub."""
    docs = meeting_data.get('docs') or []
    agenda_doc = next((d for d in docs if d.get('type') in ('agenda', 'packet')), None)
    if not agenda_doc:
        return
    slug = meeting_data['slug']
    print(f"  Generating agenda preview for {slug}...")
    text = _load_cached_agenda_text(docs, bucket_uri, catalog)
    if not text:
        print(f"    Skipping {slug}: could not read doc content.")
        return
    prompt = (
        'You are summarizing a school board meeting agenda for a public web archive.\n'
        'Output ONLY valid HTML — no prose, no markdown, no code fences.\n\n'
        'Format:\n'
        '<ul class="agenda-preview-list">\n'
        '<li><strong>Topic:</strong> Brief detail (one sentence).</li>\n'
        '</ul>\n\n'
        'Rules:\n'
        '- 3-6 items only\n'
        '- Skip boilerplate: Call to Order, Pledge of Allegiance, Opening Statement, '
        'Public Comment, Adjournment, generic committee report headers with no named item\n'
        '- Include: substantive votes, named policy items, key personnel changes, '
        'grants/donations, notable field trips or events, workshops with a stated topic\n\n'
        f'Agenda text:\n{text}'
    )
    try:
        response = call_with_fallback(
            lambda model: client.models.generate_content(model=model, contents=prompt, config={'temperature': 0.1})
        )
        if response is None:
            raise Exception("All models rate-limited")
        raw = response.text.strip()
        raw = re.sub(r'^```(?:html)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        njk_path = os.path.join(meeting_dir, slug + '.njk')
        data, body = _read_njk(njk_path)
        data['agenda_preview'] = raw
        _write_njk(njk_path, data, body)
        print(f"    Written agenda preview for {slug}.")
    except Exception as e:
        print(f"    Error generating agenda preview for {slug}: {e}")


def generate_blurb(local, meeting_dir):
    print(f"  Generating blurb for {local['slug']}...")
    prompt = "Write an extremely concise 1-2 sentence objective summary (a 'blurb') of this school board meeting based on these notes. Do not use quotes or introductory filler:\n"
    prompt += "\n".join([f"- {s.get('text', '')}" for s in local.get('summary', [])])
    try:
        response = call_with_fallback(
            lambda model: client.models.generate_content(model=model, contents=prompt, config={'temperature': 0.1})
        )
        if response is None:
            raise Exception("All models rate-limited")
        blurb = response.text.strip().replace('\n', ' ')
        njk_path = os.path.join(meeting_dir, local['slug'] + '.njk')
        data, body = _read_njk(njk_path)
        data['blurb'] = blurb
        _write_njk(njk_path, data, body)
        return local['slug'], blurb
    except Exception as e:
        print(f"  Error generating blurb for {local['slug']}: {e}")
        return local['slug'], None


def synthesize_topic(topic, evidence, display_date):
    print(f"  Synthesizing: {topic}...")
    prompt = f"""
    You are a policy analyst for the SPSD Board Meeting Archive.
    Synthesize the following chronological notes (NEWEST FIRST) regarding the topic: '{topic}'.

    Return ONLY a JSON object with these exact keys — no markdown fences, no commentary:
    {{
      "current_status": "1-2 plain sentences (no markdown) summarizing where things stand right now. Card-ready.",
      "overview": ["Array of 3-6 bullet points, newest-first. Each bullet is a single plain sentence (no markdown) citing a meeting date naturally (e.g. 'On {display_date}, the board decided...'). The first bullet MUST cover the most recent developments. If a prior decision was reversed or modified, say so explicitly in its own bullet."],
      "perspectives": {{
        "Board": ["Array of 1-4 short bullets on elected members' stance and questions. Omit key entirely if no data."],
        "Administration": ["Array of 1-4 short bullets on Superintendent/Directors' recommendations. Omit key entirely if no data."],
        "Staff": ["Array of 1-4 short bullets on staff and union rep viewpoints. Omit key entirely if no data."],
        "Citizens": ["Array of 1-4 short bullets on public comment and parent viewpoints. Omit key entirely if no data."]
      }}
    }}

    RULES:
    - Omit any perspective key where there is genuinely no evidence in the notes.
    - Use "Staff" (not "Teachers") for the staff/union group.
    - SPELING: Kaler (NOT Caler), Skillin (NOT Skillen).

    Evidence (Newest First):
    {evidence}
    """
    try:
        response = call_with_fallback(
            lambda model: client.models.generate_content(model=model, contents=prompt, config={'temperature': 0.1})
        )
        if response is None:
            raise Exception("All models rate-limited")
        raw = response.text.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        result = json.loads(raw)
        return topic, result
    except json.JSONDecodeError as e:
        print(f"  JSON parse error for {topic}: {e}\n  Raw: {raw[:200]}")
        return topic, None
    except Exception as e:
        print(f"  Error synthesizing {topic}: {e}")
        return topic, None


class TaggedTopic(BaseModel):
    tag: str
    evidence_bullets: list[int]  # 0-indexed positions into that meeting's own numbered summary


class MeetingTagSet(BaseModel):
    slug: str
    tags: list[TaggedTopic]


class BatchTagResponse(BaseModel):
    results: list[MeetingTagSet]


BATCH_TAGGING_TIMEOUT = 600  # seconds; enforced via subprocess SIGTERM


def _batch_tagging_subprocess(model_name, prompt_text, result_queue):
    """Runs the batch tagging Gemini call in a separate process so SIGTERM can hard-kill it on timeout."""
    local_credentials, local_project_id = get_credentials()
    local_client = genai.Client(
        credentials=local_credentials, project=local_project_id, location='us-central1', vertexai=True,
    )
    try:
        response = local_client.models.generate_content(
            model=model_name,
            contents=prompt_text,
            config={'response_mime_type': 'application/json', 'response_schema': BatchTagResponse, 'temperature': 0.0},
        )
        result_queue.put(('ok', response.text))
    except Exception as e:
        result_queue.put(('err', str(e)))


def batch_tag_all_meetings(meetings_data, bucket_uri, meeting_dir, topics_lib_path, catalog):
    """Tag the entire meeting corpus in a single Gemini call, using each meeting's transcript summary
    plus cached agenda text at once — lets the model assign self-consistent tags across a narrative
    that spans many meetings (e.g. a school closure) instead of reconstructing continuity one meeting
    at a time. Used for periodic full recalibration (--retag), not the daily incremental path."""
    candidates = sorted(
        [m for m in meetings_data if not m.get('stub') and m.get('summary')],
        key=lambda m: m['slug']
    )
    if not candidates:
        print("  No meetings to tag.")
        return

    print(f"  Gathering context for {len(candidates)} meetings (agenda text from cache)...", flush=True)
    meeting_blocks = []
    for m in candidates:
        slug = m['slug']
        summary_text = '\n'.join(f'  {i}. {b["topic"]}: {b["text"]}' for i, b in enumerate(m['summary']))
        agenda_text = _load_cached_agenda_text(m.get('docs'), bucket_uri, catalog)
        block = f"### Meeting: {slug}\nSummary (numbered):\n{summary_text}"
        if agenda_text:
            block += f"\nOfficial agenda excerpt:\n{agenda_text}"
        meeting_blocks.append(block)
    meetings_text = '\n\n'.join(meeting_blocks)

    prompt = f"""You are tagging the full history of a school board's meetings with topic tags, all at once.
For EACH meeting below, identify 3-5 topic tags for the PRIMARY issues discussed.

**First-order rule:** Only tag a topic if it received substantial, independent discussion in that meeting — not just a passing mention or as context within another topic.

**Consistency is the whole point of tagging everything at once:** if the same underlying story spans multiple meetings (e.g. a school closure that gets recommended, voted on, and then has its logistics worked out over several later meetings), use the SAME tag across all of them, even if the specific wording of each meeting's summary differs. You are the single source of truth for the entire tag set here — there is no external tag list to consult, decide it directly from the evidence across the whole corpus.

**Tag selection rules:**
1. **Reuse the same tag** for the same underlying story wherever it recurs across meetings — this is the most important rule for this pass.
2. {_RULE_TIME_BINDING} This rule is the one most often violated when tagging many meetings at once — before finalizing, scan your own output for every budget-adjacent tag (including ones without an FY prefix that should have one) and collapse any that share a fiscal year and theme into one.
3. {_RULE_SUBJECT_NOT_PROCESS}
4. **Name the specific issue, not a category** — "SPESPA Contract 2025" not "Union Contracts". {_RULE_GENERIC_NOUN}
5. {_RULE_EVIDENCE_RELEVANCE}

**Format rules:** Fiscal years must use FYYY format (FY27, FY26) — never FY2027 or FY2026. No parentheses or acronyms. No concatenated words. No symbols (&, /, etc.).

Meetings (chronological):
{meetings_text}

Respond with tags for every meeting listed above, keyed by its slug. For each tag, cite which
0-indexed bullet number(s) from THAT meeting's own numbered Summary support it — every tag must
cite at least one bullet from its own meeting. The citation is bookkeeping for a tag you've already
chosen on its merits, not a factor in choosing it."""

    def _attempt(model):
        q = MpQueue()
        p = Process(target=_batch_tagging_subprocess, args=(model, prompt, q), daemon=True)
        p.start()
        p.join(timeout=BATCH_TAGGING_TIMEOUT)
        if p.is_alive():
            p.terminate()
            p.join()
            raise TimeoutError(f"Batch tagging timed out after {BATCH_TAGGING_TIMEOUT}s")
        try:
            kind, val = q.get_nowait()
        except _stdlib_queue.Empty:
            raise Exception("Batch tagging subprocess exited with no result")
        if kind == 'err':
            raise Exception(val)
        return val

    val = call_with_fallback(_attempt)
    if val is None:
        raise Exception("All models rate-limited for batch tagging")

    parsed = json.loads(val)
    n_bullets_by_slug = {m['slug']: len(m['summary']) for m in candidates}

    results = {}
    for r in parsed['results']:
        slug = r['slug']
        n_bullets = n_bullets_by_slug.get(slug, 0)
        tags, evidence = [], {}
        for entry in r['tags']:
            tag = _drop_blacklisted(_warn_if_budget_synonym(_strip_modifiers(_normalize_fy(entry['tag'])), slug), slug)
            if tag is None:
                continue
            bullets = [i for i in entry['evidence_bullets'] if 0 <= i < n_bullets]
            if tag not in evidence:
                tags.append(tag)
                evidence[tag] = []
            evidence[tag] = sorted(set(evidence[tag]) | set(bullets))
        results[slug] = (tags, evidence)

    last_seen = {}  # tag -> most recent slug mentioning it
    tagged_count = 0
    for m in candidates:  # candidates is chronological (oldest first), so later writes win
        slug = m['slug']
        result = results.get(slug)
        if result is None:
            print(f"  Warning: no tags returned for {slug}")
            continue
        tags, evidence = result
        njk_path = os.path.join(meeting_dir, f"{slug}.njk")
        data, body = _read_njk(njk_path)
        data['topics'] = tags
        data['topic_evidence'] = evidence
        _write_njk(njk_path, data, body)
        m['topics'] = tags
        m['topic_evidence'] = evidence
        for t in tags:
            last_seen[t] = slug
        tagged_count += 1
        print(f"  {slug} → {tags}")

    # Recency order (most recently active topic first) — matches the incremental path's
    # prepend-on-first-seen behavior (_update_topics_lib) and the /topics index's display order,
    # which reads all_topics.json array order directly with no re-sorting of its own.
    ordered_tags = sorted(last_seen, key=lambda t: last_seen[t], reverse=True)
    with open(topics_lib_path, 'w') as f:
        json.dump(ordered_tags, f, indent=2)
    print(f"  Tagged {tagged_count}/{len(candidates)} meetings; {len(ordered_tags)} unique tags.")


def dry_run_tag(slugs, bucket_uri, meeting_dir='src/meetings/',
                 topics_lib_path='src/_data/all_topics.json',
                 summary_lib_path='src/_data/topic_summaries.json'):
    """Test generate_tags() against real current context for given meeting slug(s). Writes nothing —
    for cheaply iterating on the tagging prompt without a full --retag."""
    allowed_tags = json.load(open(topics_lib_path)) if os.path.exists(topics_lib_path) else []
    topic_context = {
        t: s.get('current_status', '')
        for t, s in (json.load(open(summary_lib_path)) if os.path.exists(summary_lib_path) else {}).items()
    }
    all_slugs = sorted(f[:-4] for f in os.listdir(meeting_dir) if f.endswith('.njk'))
    catalog = drive_mod.load_catalog(bucket_uri)

    for slug in slugs:
        njk_path = os.path.join(meeting_dir, f"{slug}.njk")
        if not os.path.exists(njk_path):
            print(f"  {slug}: not found")
            continue
        data, _ = _read_njk(njk_path)
        if not data.get('summary'):
            print(f"  {slug}: no summary bullets to tag from")
            continue

        prior_slug = next((s for s in reversed(all_slugs) if s < slug), None)
        recent_topics = None
        if prior_slug:
            prior_data, _ = _read_njk(os.path.join(meeting_dir, f"{prior_slug}.njk"))
            recent_topics = prior_data.get('topics')

        agenda_text = _load_cached_agenda_text(data.get('docs'), bucket_uri, catalog)

        print(f"  Dry-run tagging {slug} (prior meeting: {prior_slug or 'none'}, agenda context: {'yes' if agenda_text else 'no'})...")
        try:
            tags, evidence = generate_tags(slug, data['summary'], allowed_tags, topic_context, recent_topics, agenda_text)
            print(f"    → {tags}")
            for t in tags:
                print(f"      {t}: bullets {evidence.get(t, [])}")
        except Exception as e:
            print(f"  Warning: dry-run tagging failed for {slug}: {e}")


def post_process():
    meeting_dir = 'src/meetings/'
    topics_lib_path = 'src/_data/all_topics.json'
    summary_lib_path = 'src/_data/topic_summaries.json'
    hashes_lib_path = 'scripts/topic_hashes.json'

    bucket_uri = None
    if '--bucket' in sys.argv:
        idx = sys.argv.index('--bucket')
        if idx + 1 < len(sys.argv):
            bucket_uri = sys.argv[idx + 1]
    bucket_uri = bucket_uri or os.getenv('GCS_BUCKET_URI') or None
    catalog = drive_mod.load_catalog(bucket_uri)

    if '--dry-run-tag' in sys.argv:
        idx = sys.argv.index('--dry-run-tag')
        if idx + 1 >= len(sys.argv):
            print("Usage: --dry-run-tag SLUG[,SLUG...] [--bucket gs://...]")
            return
        slugs = [s.strip() for s in sys.argv[idx + 1].split(',') if s.strip()]
        dry_run_tag(slugs, bucket_uri, meeting_dir, topics_lib_path, summary_lib_path)
        return

    retag = '--retag' in sys.argv

    if retag:
        with open(topics_lib_path, 'w') as f:
            json.dump([], f)
        with open(hashes_lib_path, 'w') as f:
            json.dump({}, f)
        with open(summary_lib_path, 'w') as f:
            json.dump({}, f)
        print("Retag mode: cleared all_topics.json, topic_hashes.json, and topic_summaries.json", flush=True)

    # 1. Enforce Glossary & Extract Data
    print("Enforcing Glossary and scanning meetings...", flush=True)
    meetings_data = _enforce_glossary_pass(meeting_dir)

    # 2. Extract canonical names from official docs (agenda/packet/minutes) → cache in GCS
    if bucket_uri:
        print("Extracting canonical names from official meeting documents...", flush=True)
        term_targets = [
            m for m in meetings_data
            if any(d.get('type') in ('agenda', 'packet', 'minutes', 'min') for d in (m.get('docs') or []))
        ]
        def _extract_terms_task(m):
            try:
                _extract_official_terms(m, bucket_uri, catalog)
            except Exception as e:
                print(f"  Warning: term extraction failed for {m.get('slug')}: {e}", flush=True)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(_extract_terms_task, m) for m in term_targets]
            for future in as_completed(futures):
                future.result()

    # 3. Generate topic tags from summary bullets
    if retag:
        print("Batch-tagging the full meeting corpus in a single call...", flush=True)
        try:
            batch_tag_all_meetings(meetings_data, bucket_uri, meeting_dir, topics_lib_path, catalog)
        except Exception as e:
            print(f"  Warning: batch tagging failed: {e}", flush=True)
    else:
        print("Generating topic tags from meeting summaries...", flush=True)
        allowed_tags = json.load(open(topics_lib_path)) if os.path.exists(topics_lib_path) else []
        topic_context = {
            t: s.get('current_status', '')
            for t, s in (json.load(open(summary_lib_path)) if os.path.exists(summary_lib_path) else {}).items()
        }
        tag_candidates = sorted(
            [m for m in meetings_data if not m.get('stub') and m.get('summary')],
            key=lambda m: m['slug']
        )
        for i, m in enumerate(tag_candidates):
            if m.get('topics'):
                continue
            slug = m['slug']
            recent_topics = tag_candidates[i - 1].get('topics') if i > 0 else None
            agenda_text = _load_cached_agenda_text(m.get('docs'), bucket_uri, catalog)
            print(f"  Tagging {slug}...", flush=True)
            try:
                tags, evidence = generate_tags(slug, m['summary'], allowed_tags, topic_context, recent_topics, agenda_text)
                njk_path = os.path.join(meeting_dir, f"{slug}.njk")
                data, body = _read_njk(njk_path)
                data['topics'] = tags
                data['topic_evidence'] = evidence
                _write_njk(njk_path, data, body)
                m['topics'] = tags  # keep in-memory data in sync for synthesis step
                m['topic_evidence'] = evidence
                _update_topics_lib(tags, topics_lib_path)
                allowed_tags = json.load(open(topics_lib_path))
                print(f"    → {tags}")
            except Exception as e:
                print(f"  Warning: tagging failed for {slug}: {e}", flush=True)

    # 4. Generate Agenda Previews for upcoming stubs with a packet/agenda doc
    print("Generating agenda previews for upcoming meetings...")
    import datetime as _dt
    today_slug = _dt.date.today().isoformat()
    preview_tasks = [
        m for m in meetings_data
        if m.get('stub') and m['slug'] >= today_slug and not m.get('agenda_preview')
        and any(d.get('type') in ('agenda', 'packet') for d in (m.get('docs') or []))
    ]
    for m in preview_tasks:
        generate_agenda_preview(m, meeting_dir, bucket_uri, catalog)

    # 5. Generate missing blurbs
    print("Generating blurbs for unprocessed meetings...")
    blurb_tasks = [m for m in meetings_data
                   if not m.get('stub') and not m.get('blurb') and m.get('summary')]
    if blurb_tasks:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(generate_blurb, local, meeting_dir): local for local in blurb_tasks}
            for future in as_completed(futures):
                future.result()

    # 6. Generate synthesized topic summaries (concurrent + hash caching)
    print("Generating high-level topic summaries...")
    topics_lib = []
    if os.path.exists(topics_lib_path):
        with open(topics_lib_path, 'r') as f:
            topics_lib = json.load(f)

    summaries = {}
    if os.path.exists(summary_lib_path):
        with open(summary_lib_path, 'r') as f:
            summaries = json.load(f)

    hashes = {}
    if os.path.exists(hashes_lib_path):
        with open(hashes_lib_path, 'r') as f:
            hashes = json.load(f)

    # Build inverted index {topic: [meeting, ...]} newest-first — avoids O(topics × meetings) scan
    sorted_m = sorted(meetings_data, key=lambda x: str(x.get('date', x.get('slug', ''))), reverse=True)
    topic_meetings = {}
    for m in sorted_m:
        for t in m.get('topics', []):
            topic_meetings.setdefault(t, []).append(m)

    topic_tasks = []
    for topic in topics_lib:
        topic_sorted = topic_meetings.get(topic, [])
        display_date = topic_sorted[0].get('display_date', 'recent dates') if topic_sorted else 'recent dates'
        evidence_list = []

        for m in topic_sorted:
            m_date = m.get('display_date', m.get('slug', ''))
            m_url = f"/meetings/{m['slug']}/"
            summary = m.get('summary', [])
            # Prefer the evidence linkage the tagging call reported for itself (todos/012) — falls
            # back to fuzzy text matching only for meetings tagged before that field existed.
            evidence_indices = (m.get('topic_evidence') or {}).get(topic)
            if evidence_indices is not None:
                topic_bullets = [summary[i]['text'] for i in evidence_indices if 0 <= i < len(summary)]
            else:
                topic_bullets = [
                    b['text'] for b in summary
                    if _evidence_match(topic, b.get('topic', ''), b.get('text', ''))
                ]
            if topic_bullets:
                evidence_list.append(f"Meeting: {m_date} ({m_url})\n" + "\n".join([f"- {b}" for b in topic_bullets]))

        if not evidence_list:
            continue

        evidence_str = "---".join(evidence_list[:MAX_EVIDENCE_MEETINGS])
        current_hash = hashlib.md5(evidence_str.encode('utf-8')).hexdigest()

        if current_hash != hashes.get(topic) or topic not in summaries:
            topic_tasks.append((topic, evidence_str, display_date, current_hash))
        else:
            print(f"  Skipping {topic} (no new evidence).")

    if topic_tasks:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(synthesize_topic, t[0], t[1], t[2]): t for t in topic_tasks}
            for future in as_completed(futures):
                topic, result_text = future.result()
                if result_text:
                    summaries[topic] = result_text
                    hashes[topic] = futures[future][3]

    with open(summary_lib_path, 'w') as f:
        json.dump(summaries, f, indent=2)
    with open(hashes_lib_path, 'w') as f:
        json.dump(hashes, f, indent=2)

    drive_mod.save_catalog(bucket_uri, catalog)
    print("Post-processing complete.")


if __name__ == "__main__":
    post_process()
