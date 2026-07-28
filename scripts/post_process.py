import os
import sys
import io
import json
import re
import yaml
import hashlib
import queue as _stdlib_queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from multiprocessing import Process, Queue as MpQueue
from google import genai
from google.genai import types as genai_types
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
model_name = 'gemini-2.5-flash'
MAX_WORKERS = 4
TAGGING_TIMEOUT = 120  # seconds; enforced via subprocess SIGTERM

def _tagging_subprocess(prompt_text, result_queue):
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

def _normalize_fy(tag):
    """Normalize FY20YY → FYYYY in a tag string (e.g. FY2027 → FY27)."""
    return re.sub(r'\bFY20(\d{2})\b', lambda m: f'FY{m.group(1)}', tag)

def _update_topics_lib(new_tags, path='src/_data/topics.json'):
    existing = json.load(open(path)) if os.path.exists(path) else []
    existing_set = set(existing)
    added = [t for t in new_tags if t not in existing_set]
    if added:
        with open(path, 'w') as f:
            json.dump(added + existing, f, indent=2)

def generate_tags(slug, summary_bullets, allowed_tags):
    """Call Gemini to generate topic tags from a meeting's summary bullets."""
    summary_text = '\n'.join(f'- {b["topic"]}: {b["text"]}' for b in summary_bullets)
    prompt = f"""You are tagging a school board meeting. Given the meeting summary below, identify 3-5 topic tags for the PRIMARY issues discussed.

**First-order rule:** Only tag a topic if it received substantial, independent discussion — not just a passing mention or as context within another topic. Ask: "Would a reader coming to this meeting specifically for this topic find meaningful content?" If not, omit the tag.

**Tag selection rules, in priority order:**
1. **Reuse an existing tag** from {allowed_tags} when it accurately describes the topic.
2. **Adapt an existing tag** for a new time-bound instance of a recurring theme — add fiscal year or scope (e.g. "FY27 Labor Contract Negotiations" not "Union Contracts").
3. **Create a new specific tag** when no existing tag fits. Name the specific issue, not a category — "SPESPA Contract 2025" not "Union Contracts"; "FY26 Staff Reductions" not "Property Taxes". Generic category nouns ({_TAG_GENERIC_EXAMPLES}) are never valid tags.

**Format rules:** Fiscal years must use FYYY format (FY27, FY26) — never FY2027 or FY2026. No parentheses or acronyms. No concatenated words. No symbols (&, /, etc.).

Meeting: {slug}
Summary:
{summary_text}

Respond with a JSON array of tag strings only. Example: ["Elementary School Reconfiguration", "FY27 Budget"]"""

    q = MpQueue()
    p = Process(target=_tagging_subprocess, args=(prompt, q), daemon=True)
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
    tags = json.loads(val)
    return [_normalize_fy(t) for t in tags]


GLOSSARY = {
    "Caler": "Kaler",
    "Skillen": "Skillin",
    "Skillens": "Skillins",
    "Caler's": "Kaler's",
    "Atkinson-Dena": "Atkinson Duina",
    "Atkinson Dena": "Atkinson Duina",
}

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


def _read_doc_text_from_drive(url, max_chars=8000):
    """Download readable text from a Drive file URL. Returns None on failure."""
    fid_match = re.search(r'/file/d/([^/?]+)', url)
    if not fid_match:
        return None
    file_id = fid_match.group(1)
    try:
        svc = drive_mod.get_drive_service()
        meta = svc.files().get(fileId=file_id, fields='mimeType,size').execute()
        mime = meta.get('mimeType', '')
        if int(meta.get('size', 0)) > 10_000_000:
            return None
        from googleapiclient.http import MediaIoBaseDownload
        fh = io.BytesIO()
        req = svc.files().export_media(fileId=file_id, mimeType='text/plain') if 'google-apps.document' in mime else svc.files().get_media(fileId=file_id)
        dl = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _, done = dl.next_chunk()
        if 'pdf' in mime:
            import pypdf
            fh.seek(0)
            text = '\n'.join(p.extract_text() or '' for p in pypdf.PdfReader(fh).pages[:5])
        else:
            text = fh.getvalue().decode('utf-8', errors='replace')
        return text[:max_chars] if text.strip() else None
    except Exception as e:
        print(f"    Warning: could not read Drive doc: {e}")
        return None


def _extract_official_terms(meeting_data, bucket_uri):
    """Extract canonical proper nouns from agenda/packet/minutes docs; raw text and terms cached in GCS."""
    from google.cloud import storage

    docs = meeting_data.get('docs') or []
    qualifying = [d for d in docs if d.get('type') in ('agenda', 'packet', 'minutes', 'min')]
    if not qualifying:
        return []

    slug = meeting_data['slug']
    bucket_name = bucket_uri[5:]  # strip gs://
    gcs_client = storage.Client(credentials=credentials, project=project_id)
    gcs_bucket = gcs_client.bucket(bucket_name)
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
            meta = svc.files().get(fileId=file_id, fields='modifiedTime').execute()
            mod_time = meta.get('modifiedTime', 'unknown').replace(':', '-')
        except Exception as e:
            print(f"    Warning: could not get Drive metadata for {file_id}: {e}")
            continue

        folder = f"official_docs/{slug}/{doc_type}-{file_id}"
        json_blob = gcs_bucket.blob(f"{folder}/{mod_time}.json")

        if json_blob.exists(timeout=60):
            try:
                cached = json.loads(json_blob.download_as_text(timeout=60))
                all_terms.extend(cached)
                print(f"    Loaded {len(cached)} terms for {slug}/{doc_type} from cache.", flush=True)
            except Exception:
                pass
            continue

        print(f"    Extracting terms for {slug}/{doc_type} (not in cache)...", flush=True)
        text = _read_doc_text_from_drive(url)
        if not text:
            continue

        # Store raw text for audit/reprocessing
        gcs_bucket.blob(f"{folder}/{mod_time}.txt").upload_from_string(text, content_type='text/plain', timeout=60)

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
            response = client.models.generate_content(
                model=model_name, contents=prompt, config={'temperature': 0.0}
            )
            raw = response.text.strip()
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            terms = json.loads(raw)
            json_blob.upload_from_string(json.dumps(terms), content_type='application/json', timeout=60)
            print(f"    Extracted and cached {len(terms)} terms for {slug}/{doc_type}.", flush=True)
            all_terms.extend(terms)
        except Exception as e:
            print(f"    Warning: could not extract terms for {slug}/{doc_type}: {e}", flush=True)

    return all_terms


def generate_agenda_preview(meeting_data, meeting_dir):
    """Generate and write a topic-structured agenda_preview for an upcoming stub."""
    docs = meeting_data.get('docs') or []
    agenda_doc = next((d for d in docs if d.get('type') in ('agenda', 'packet')), None)
    if not agenda_doc:
        return
    slug = meeting_data['slug']
    print(f"  Generating agenda preview for {slug}...")
    text = _read_doc_text_from_drive(agenda_doc['url'])
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
        response = client.models.generate_content(model=model_name, contents=prompt, config={'temperature': 0.1})
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
        response = client.models.generate_content(model=model_name, contents=prompt, config={'temperature': 0.1})
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
      "overview": "2-3 paragraphs with natural citations to meeting dates (e.g. 'On {display_date}, the board decided...'). The first paragraph MUST cover the most recent developments. If a prior decision was reversed or modified, reflect that clearly.",
      "perspectives": {{
        "Board": "Summary of elected members' stance and questions. Omit key entirely if no data.",
        "Administration": "Summary of Superintendent/Directors' recommendations. Omit key entirely if no data.",
        "Staff": "Summary of staff and union rep viewpoints. Omit key entirely if no data.",
        "Citizens": "Summary of public comment and parent viewpoints. Omit key entirely if no data."
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
        response = client.models.generate_content(model=model_name, contents=prompt, config={'temperature': 0.1})
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


def post_process():
    meeting_dir = 'src/meetings/'
    topics_lib_path = 'src/_data/topics.json'
    summary_lib_path = 'src/_data/topic_summaries.json'
    hashes_lib_path = 'scripts/topic_hashes.json'

    retag = '--retag' in sys.argv

    bucket_uri = None
    if '--bucket' in sys.argv:
        idx = sys.argv.index('--bucket')
        if idx + 1 < len(sys.argv):
            bucket_uri = sys.argv[idx + 1]
    bucket_uri = bucket_uri or os.getenv('GCS_BUCKET_URI') or None

    if retag:
        with open(topics_lib_path, 'w') as f:
            json.dump([], f)
        with open(hashes_lib_path, 'w') as f:
            json.dump({}, f)
        print("Retag mode: cleared topics.json and topic_hashes.json", flush=True)

    # 1. Enforce Glossary & Extract Data
    print("Enforcing Glossary and scanning meetings...", flush=True)
    meetings_data = []

    for filename in sorted(os.listdir(meeting_dir)):
        if not filename.endswith('.njk'): continue
        filepath = os.path.join(meeting_dir, filename)

        with open(filepath, 'r') as f:
            content = f.read()

        orig = content
        for w, r in GLOSSARY.items():
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

    # 2. Extract canonical names from official docs (agenda/packet/minutes) → cache in GCS
    if bucket_uri:
        print("Extracting canonical names from official meeting documents...", flush=True)
        term_targets = [
            m for m in meetings_data
            if any(d.get('type') in ('agenda', 'packet', 'minutes', 'min') for d in (m.get('docs') or []))
        ]
        def _extract_terms_task(m):
            try:
                _extract_official_terms(m, bucket_uri)
            except Exception as e:
                print(f"  Warning: term extraction failed for {m.get('slug')}: {e}", flush=True)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(_extract_terms_task, m) for m in term_targets]
            for future in as_completed(futures):
                future.result()

    # 3. Generate topic tags from summary bullets (sequential, chronological)
    print("Generating topic tags from meeting summaries...", flush=True)
    allowed_tags = json.load(open(topics_lib_path)) if os.path.exists(topics_lib_path) else []
    tag_candidates = sorted(
        [m for m in meetings_data if not m.get('stub') and m.get('summary')],
        key=lambda m: m['slug']
    )
    for m in tag_candidates:
        if not retag and m.get('topics'):
            continue
        slug = m['slug']
        print(f"  Tagging {slug}...", flush=True)
        try:
            tags = generate_tags(slug, m['summary'], allowed_tags)
            njk_path = os.path.join(meeting_dir, f"{slug}.njk")
            data, body = _read_njk(njk_path)
            data['topics'] = tags
            _write_njk(njk_path, data, body)
            m['topics'] = tags  # keep in-memory data in sync for synthesis step
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
        generate_agenda_preview(m, meeting_dir)

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
            topic_bullets = [
                b['text'] for b in m.get('summary', [])
                if _evidence_match(topic, b.get('topic', ''), b.get('text', ''))
            ]
            if topic_bullets:
                evidence_list.append(f"Meeting: {m_date} ({m_url})\n" + "\n".join([f"- {b}" for b in topic_bullets]))

        if not evidence_list:
            continue

        evidence_str = "---".join(evidence_list[:15])  # cap at 15 to stay within token limits
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
    print("Post-processing complete.")


if __name__ == "__main__":
    post_process()
