import os
import sys
import json
import re
import yaml
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from google import genai
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
from sourcing.auth import get_credentials

credentials, project_id = get_credentials()
client = genai.Client(credentials=credentials, project=project_id, location='us-central1', vertexai=True)
model_name = 'gemini-2.5-flash'
MAX_WORKERS = 4

GLOSSARY = {
    "Caler": "Kaler",
    "Skillen": "Skillin",
    "Skillens": "Skillins",
    "Caler's": "Kaler's",
    "Atkinson-Dena": "Atkinson Duina",
    "Atkinson Dena": "Atkinson Duina",
}

TOPIC_BLACKLIST = ["Personnel", "Contracts", "Finance", "Budget"]

def _read_doc_text_from_drive(url, max_chars=8000):
    """Download readable text from a Drive file URL. Returns None on failure."""
    import io
    from sourcing import drive as drive_mod
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
    from sourcing import drive as drive_mod
    from google.cloud import storage

    docs = meeting_data.get('docs') or []
    qualifying = [d for d in docs if d.get('type') in ('agenda', 'packet', 'minutes', 'min')]
    if not qualifying:
        return []

    slug = meeting_data['slug']
    bucket_name = bucket_uri[5:]  # strip gs://
    gcs_client = storage.Client()
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

        if json_blob.exists():
            try:
                cached = json.loads(json_blob.download_as_text())
                all_terms.extend(cached)
                print(f"    Loaded {len(cached)} terms for {slug}/{doc_type} from cache.")
            except Exception:
                pass
            continue

        text = _read_doc_text_from_drive(url)
        if not text:
            continue

        # Store raw text for audit/reprocessing
        gcs_bucket.blob(f"{folder}/{mod_time}.txt").upload_from_string(text, content_type='text/plain')

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
            json_blob.upload_from_string(json.dumps(terms), content_type='application/json')
            print(f"    Extracted and cached {len(terms)} terms for {slug}/{doc_type}.")
            all_terms.extend(terms)
        except Exception as e:
            print(f"    Warning: could not extract terms for {slug}/{doc_type}: {e}")

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
        preview_html = raw
        njk_path = os.path.join(meeting_dir, slug + '.njk')
        with open(njk_path, 'r') as f:
            content = f.read()
        parts = re.split(r'^---+\s*$', content, flags=re.MULTILINE)
        if len(parts) < 3:
            return
        fm = yaml.safe_load(parts[1]) or {}
        fm['agenda_preview'] = preview_html
        fm_yaml = yaml.dump(fm, sort_keys=False, default_flow_style=False, allow_unicode=True)
        with open(njk_path, 'w') as f:
            f.write(f'---\n{fm_yaml}---\n{"---".join(parts[2:])}')
        print(f"    Written agenda preview for {slug}.")
    except Exception as e:
        print(f"    Error generating agenda preview for {slug}: {e}")


def generate_blurb(local, meeting_dir):
    print(f"  Generating blurb for {local['slug']}...")
    prompt = f"Write an extremely concise 1-2 sentence objective summary (a 'blurb') of this school board meeting based on these notes. Do not use quotes or introductory filler:\n"
    prompt += "\n".join([f"- {s.get('text', '')}" for s in local.get('summary', [])])
    try:
        response = client.models.generate_content(model=model_name, contents=prompt, config={'temperature': 0.1})
        blurb = response.text.strip().replace('\n', ' ').replace('"', "'")
        
        # Update NJK file
        njk_path = os.path.join(meeting_dir, local['slug'] + '.njk')
        with open(njk_path, 'r') as f: content = f.read()
        
        if 'blurb:' not in content:
            content = content.replace('---\n', f'---\nblurb: "{blurb}"\n', 1)
        else:
            content = re.sub(r'blurb:.*', f'blurb: "{blurb}"', content)
        with open(njk_path, 'w') as f: f.write(content)
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
        # Strip code fences if present
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

    bucket_uri = None
    if '--bucket' in sys.argv:
        idx = sys.argv.index('--bucket')
        if idx + 1 < len(sys.argv):
            bucket_uri = sys.argv[idx + 1]
    bucket_uri = bucket_uri or os.getenv('GCS_BUCKET_URI') or None

    # 1. Enforce Glossary & Extract Data
    print("Enforcing Glossary and scanning meetings...")
    all_discovered_topics = set()
    meetings_data = []

    for filename in sorted(os.listdir(meeting_dir)):
        if not filename.endswith('.njk'): continue
        filepath = os.path.join(meeting_dir, filename)
        
        with open(filepath, 'r') as f: content = f.read()
        
        orig = content
        for w, r in GLOSSARY.items(): content = content.replace(w, r)
        if content != orig:
            with open(filepath, 'w') as f: f.write(content)

        match = re.search(r'^(---\s*\n(.*?)\n---\s*(?:\n|$))', content, re.DOTALL)
        if match:
            try:
                data = yaml.safe_load(match.group(2))
                data['slug'] = filename.replace('.njk', '')
                meetings_data.append(data)
                if 'topics' in data: all_discovered_topics.update(data['topics'])
            except: pass

    # 1.5. Extract canonical names from official docs (agenda/packet/minutes) → cache in GCS
    if bucket_uri:
        print("Extracting canonical names from official meeting documents...")
        for m in meetings_data:
            docs = m.get('docs') or []
            if any(d.get('type') in ('agenda', 'packet', 'minutes', 'min') for d in docs):
                _extract_official_terms(m, bucket_uri)

    # 2. Topics Lib (Sorted by recency)
    print("Updating topics library...")
    # Find the most recent date for each topic
    topic_recent_dates = {}
    for m in meetings_data:
        m_date = str(m.get('date', m.get('slug', '')))
        for t in m.get('topics', []):
            if t not in TOPIC_BLACKLIST:
                if t not in topic_recent_dates or m_date > topic_recent_dates[t]:
                    topic_recent_dates[t] = m_date
                    
    # Sort topics based on the date (newest first)
    new_lib = sorted(list(topic_recent_dates.keys()), key=lambda x: topic_recent_dates[x], reverse=True)
    with open(topics_lib_path, 'w') as f: json.dump(new_lib, f, indent=2)

    # 3. Generate Agenda Previews for upcoming stubs that have a packet/agenda doc
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

    # 4. Generate Missing Blurbs (write directly to .njk files; meetings.json is derived at build time)
    print("Generating blurbs for unprocessed meetings...")
    blurb_tasks = [m for m in meetings_data
                   if not m.get('stub') and not m.get('blurb') and m.get('summary')]
    if blurb_tasks:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(generate_blurb, local, meeting_dir): local for local in blurb_tasks}
            for future in as_completed(futures):
                future.result()  # generate_blurb writes blurb back to the .njk file

    # 4. Generate Synthesized Summaries (Concurrent + Caching)
    print("Generating high-level topic summaries...")
    summaries = {}
    if os.path.exists(summary_lib_path):
        with open(summary_lib_path, 'r') as f: summaries = json.load(f)
        
    hashes = {}
    if os.path.exists(hashes_lib_path):
        with open(hashes_lib_path, 'r') as f: hashes = json.load(f)

    topic_tasks = []
    sorted_m = sorted(meetings_data, key=lambda x: str(x.get('date', x.get('slug', ''))), reverse=True)

    for topic in new_lib:
        evidence_list = []
        display_date = sorted_m[0].get('display_date', 'recent dates') if sorted_m else 'recent dates'
        
        for m in sorted_m:
            if topic in m.get('topics', []):
                m_date = m.get('display_date', m.get('slug', ''))
                m_url = f"/meetings/{m['slug']}/"
                summary_bullets = m.get('summary', [])
                topic_bullets = [b['text'] for b in summary_bullets if topic.lower() in b['topic'].lower() or topic.lower() in b['text'].lower()]
                if topic_bullets:
                    evidence_list.append(f"Meeting: {m_date} ({m_url})\n" + "\n".join([f"- {b}" for b in topic_bullets]))
        
        if not evidence_list: continue
        
        evidence_str = "---".join(evidence_list[:15])
        current_hash = hashlib.md5(evidence_str.encode('utf-8')).hexdigest()
        
        # Check cache: Only synthesize if hash changed or summary missing
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
                    # Update hash only on success
                    original_task = futures[future]
                    hashes[topic] = original_task[3]

    with open(summary_lib_path, 'w') as f: json.dump(summaries, f, indent=2)
    with open(hashes_lib_path, 'w') as f: json.dump(hashes, f, indent=2)
    print("Post-processing complete.")

if __name__ == "__main__":
    post_process()
