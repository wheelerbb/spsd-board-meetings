import os
import json
import re
import sys
import time
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from google import genai
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from datetime import date

# Load environment variables
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
from sourcing.transcripts import get_transcript_mapping, get_bucket_vtt_mapping

# --- CONFIGURATION ---
USE_LOCAL_AUTH = "--local-auth" in sys.argv
if USE_LOCAL_AUTH: sys.argv.remove("--local-auth")

MODEL_MAP = {
    "vertex": {"pro": "gemini-2.5-pro", "flash": "gemini-2.5-flash"},
    "studio": {"pro": "gemini-1.5-pro", "flash": "gemini-2.0-flash"}
}

client = None
provider = "studio"
api_key = os.getenv("GEMINI_PRO_API_KEY") or os.getenv("GEMINI_API_KEY")

if USE_LOCAL_AUTH:
    import google.auth
    try:
        credentials, project = google.auth.default()
        project_id = 'spsd-board-meetings'
        client = genai.Client(credentials=credentials, project=project_id, location='us-central1', vertexai=True)
        provider = "vertex"
        print(f"Using Vertex AI (Project: {project_id})")
    except Exception as e:
        print(f"Failed to load local credentials: {e}")
        sys.exit(1)
elif api_key and "your_" not in api_key:
    client = genai.Client(api_key=api_key)
    provider = "studio"
else:
    print("Error: No authentication method found.")
    sys.exit(1)

DEFAULT_MODEL = MODEL_MAP[provider]["pro"]
MAX_WORKERS = 4 if provider == "vertex" else 1

CUTOFF_DATE = "2023-08-01"

GLOSSARY = """
- Kaler Elementary School (NOT Caler)
- Skillin Elementary School (NOT Skillen)
- SPESPA (Support Professionals)
- SPTA (Teachers)
"""

# --- SCHEMA ---
class Vote(BaseModel):
    motion: str
    result: str
    count: str
    moved_2nd: str

class SummaryItem(BaseModel):
    topic: str
    text: str

class TimelineItem(BaseModel):
    time: str
    seconds: int
    topic: str
    desc: str

class MeetingReport(BaseModel):
    blurb: str = Field(description="An extremely concise (1-2 sentence) summary.")
    tags: list[str] = Field(description="3-5 high-level topic tags.")
    votes: list[Vote]
    summary: list[SummaryItem]
    timeline: list[TimelineItem]

# --- VTT SOURCES ---

def _download_drive_file(file_id):
    """Download a Drive file's content as text. Prefers API key (public folder), falls back to ADC."""
    import requests
    api_key = os.getenv("GOOGLE_DRIVE_API_KEY")
    if api_key:
        resp = requests.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            params={"alt": "media", "key": api_key},
            timeout=60
        )
        resp.raise_for_status()
        return resp.text
    import io
    from sourcing.drive import get_drive_service
    from googleapiclient.http import MediaIoBaseDownload
    svc = get_drive_service()
    if not svc:
        raise RuntimeError(f"Drive service unavailable for file {file_id}")
    fh = io.BytesIO()
    dl = MediaIoBaseDownload(fh, svc.files().get_media(fileId=file_id))
    done = False
    while not done:
        _, done = dl.next_chunk()
    return fh.getvalue().decode('utf-8')



def get_drive_vtt_mapping():
    """Scans meeting stubs for docs with type=vtt. Returns {date_slug: 'drive:<fileId>'}."""
    mapping = {}
    meeting_dir = 'src/meetings/'
    for filename in sorted(os.listdir(meeting_dir)):
        if not filename.endswith('.njk'):
            continue
        slug = filename.replace('.njk', '')
        if slug < CUTOFF_DATE:
            continue
        njk_path = os.path.join(meeting_dir, filename)
        with open(njk_path, 'r') as f:
            content = f.read()
        m = re.match(r'^---\s*\n(.*?)\n---\s*(?:\n|$)', content, re.DOTALL)
        if not m:
            continue
        try:
            data = yaml.safe_load(m.group(1)) or {}
        except Exception:
            continue
        for doc in (data.get('docs') or []):
            if doc.get('type') == 'vtt':
                fid = re.search(r'/file/d/([^/?]+)', doc.get('url', ''))
                if fid:
                    mapping[slug] = f"drive:{fid.group(1)}"
                break
    return mapping


def fetch_vtt_content(path):
    """Returns VTT text from a local path, gs:// URI, or drive:<fileId> reference."""
    if path.startswith('gs://'):
        from google.cloud import storage
        without_scheme = path[5:]
        bucket_name, blob_name = without_scheme.split('/', 1)
        return storage.Client().bucket(bucket_name).blob(blob_name).download_as_text()
    if path.startswith('drive:'):
        return _download_drive_file(path[6:])
    with open(path, 'r') as f:
        return f.read()

# --- PROCESSING ---

def process_single_meeting(date_slug, vtt_path):
    njk_path = f"src/meetings/{date_slug}.njk"
    if not os.path.exists(njk_path): return None

    allowed_tags = []
    if os.path.exists('src/_data/topics.json'):
        with open('src/_data/topics.json', 'r') as f: allowed_tags = json.load(f)

    print(f"Analyzing: {date_slug} (source: {vtt_path[:40]}...)...")
    transcript = fetch_vtt_content(vtt_path)

    prompt = f"""
    Analyze the school board meeting transcript for {date_slug}.
    Extract: blurb, formal votes, high-level summary bullets, timestamped timeline, and topic tags.

    IMPORTANT: Identify perspectives from: Board, Administration, Teachers, Citizens.
    Glossary: {GLOSSARY}

    Guidelines:
    - Blurb: 1-2 sentence hook for the landing page.
    - Tags: Identify 3-5 specific, time-bound or scoped topic tags (e.g., '2026 Equity Policy Update' instead of 'Equity', 'FY26 Transportation Challenges' instead of 'Transportation'). Avoid broad, generic nouns unless referring to a standing systemic issue (like 'Reconfiguration'). Use {allowed_tags} to reuse existing specific tags where appropriate.
    - Votes: Exact motion, result, count, and movers.
    - Summary: 5-8 bullets showing the arc of conversation.
    - Timeline: 10-15 key moments with timestamps (H:MM:SS) and total seconds.

    Transcript:
    {transcript}
    """

    try:
        if provider == "vertex": time.sleep(2)
        print(f"  Sending request to Gemini for {date_slug}...")
        response = client.models.generate_content(
            model=DEFAULT_MODEL,
            contents=prompt,
            config={'response_mime_type': 'application/json', 'response_schema': MeetingReport, 'temperature': 0.1}
        )
        print(f"  Received response for {date_slug}.")
        report_data = json.loads(response.text)

        print(f"  Updating .njk file for {date_slug}...")
        with open(njk_path, 'r') as f: content = f.read()
        match = re.match(r'^(---\s*\n(.*?)\n---\s*(?:\n|$))(.*)', content, re.DOTALL)
        if not match: return None
        fm_text, body = match.group(2), match.group(3)

        data = yaml.safe_load(fm_text)
        data['stub'] = False
        data['has_transcript'] = True
        data['processed_date'] = date.today().isoformat()
        data['blurb'] = report_data.pop('blurb', '')
        data['topics'] = report_data.pop('tags', [])
        data.update(report_data)  # votes, summary, timeline

        new_fm = yaml.dump(data, sort_keys=False, default_flow_style=False)
        with open(njk_path, 'w') as f: f.write('---\n' + new_fm + '---\n' + body)
        return {"slug": date_slug, "status": "Success"}

    except Exception as e:
        if "429" in str(e): os._exit(1)
        return {"slug": date_slug, "status": f"Error: {e}"}


def main():
    args = sys.argv[1:]

    # Extract --bucket <URI> if present, fall back to env var
    bucket = None
    if "--bucket" in args:
        idx = args.index("--bucket")
        bucket = args[idx + 1]
        args = args[:idx] + args[idx + 2:]
    bucket = bucket or os.getenv('GCS_BUCKET_URI', '') or None

    # Build transcript mapping: Drive (lowest) < bucket < local (highest priority)
    mapping = {}
    mapping.update(get_drive_vtt_mapping())
    if bucket:
        try:
            mapping.update(get_bucket_vtt_mapping(bucket, CUTOFF_DATE))
        except Exception as e:
            print(f"Warning: could not read bucket VTTs: {e}")
    mapping.update(get_transcript_mapping())  # local always wins

    to_process = {}

    if "--batch" in args:
        meeting_dir = 'src/meetings/'
        for filename in os.listdir(meeting_dir):
            if not filename.endswith('.njk'): continue
            slug = filename.replace('.njk', '')
            if slug not in mapping: continue
            with open(os.path.join(meeting_dir, filename), 'r') as f:
                content = f.read()
            if 'stub: true' in content:
                to_process[slug] = mapping[slug]
    else:
        for arg in args:
            if arg in mapping: to_process[arg] = mapping[arg]

    if not to_process:
        print("No new meetings to process.")
        return
    print(f"Targeting {len(to_process)} meetings...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_single_meeting, slug, path): slug for slug, path in to_process.items()}
        for future in as_completed(futures):
            res = future.result()
            if res: print(f"Finished {res['slug']}: {res['status']}")

if __name__ == "__main__":
    main()
