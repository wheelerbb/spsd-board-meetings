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
from sourcing.transcripts import get_bucket_vtt_mapping
from sourcing.vimeo import get_vimeo_mapping
from sourcing import drive
from sourcing.auth import get_credentials

_vimeo_map = {}

# --- CONFIGURATION ---
credentials, project_id = get_credentials()
client = genai.Client(credentials=credentials, project=project_id, location='us-central1', vertexai=True)

DEFAULT_MODEL = 'gemini-2.5-pro'
FLASH_MODEL = 'gemini-2.5-flash'
MAX_WORKERS = 4

CUTOFF_DATE = "2023-08-01"

GLOSSARY = """
- Kaler Elementary School (NOT Caler)
- Skillin Elementary School (NOT Skillen)
- SPESPA (Support Professionals)
- SPTA (Teachers)
- Angela Atkinson Duina (Superintendent; NOT Atkinson-Dena or Atkinson Dena)
Board members (use full names in attendance roll call):
- Rosemarie DeAngelis (Board Chair)
- Tyler Smith (Board Vice Chair)
- Daniel Feller
- Claire Holman
- Eleni Richardson
- George Risch
Student Representatives (use full names in attendance):
- Sarah Lian
- Lizette Rios Blas
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

class TimelineChild(BaseModel):
    seconds: int
    speaker: str
    text: str

class TimelineItem(BaseModel):
    time: str
    seconds: int
    topic: str
    desc: str
    children: list[TimelineChild] = []

class AttendanceMember(BaseModel):
    name: str
    status: str  # "Present" or "Absent"
    role: str    # "Board" or "Student Rep"

class MeetingReport(BaseModel):
    blurb: str = Field(description="An extremely concise (1-2 sentence) summary.")
    tags: list[str] = Field(description="3-5 high-level topic tags.")
    votes: list[Vote]
    summary: list[SummaryItem]
    timeline: list[TimelineItem]
    board_attendance: list[AttendanceMember]

def _load_official_terms_from_gcs(slug, bucket_uri):
    """Load cached proper nouns extracted from official docs for a given meeting slug."""
    if not bucket_uri:
        return []
    try:
        from google.cloud import storage
        bucket_name = bucket_uri[5:]  # strip gs://
        blobs = [b for b in storage.Client(credentials=credentials, project=project_id).bucket(bucket_name).list_blobs(prefix=f"official_docs/{slug}/")
                 if b.name.endswith('.json')]
        all_terms = []
        for blob in blobs:
            try:
                all_terms.extend(json.loads(blob.download_as_text()))
            except Exception:
                pass
        return all_terms
    except Exception as e:
        print(f"    Warning: could not load official terms for {slug}: {e}")
        return []


# --- VTT SOURCES ---




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
        return drive.download_file(path[6:])
    with open(path, 'r') as f:
        return f.read()

# --- PROCESSING ---

def process_single_meeting(date_slug, vtt_path, bucket_uri=None):
    njk_path = f"src/meetings/{date_slug}.njk"
    if not os.path.exists(njk_path): return None

    allowed_tags = []
    if os.path.exists('src/_data/topics.json'):
        with open('src/_data/topics.json', 'r') as f: allowed_tags = json.load(f)

    print(f"Analyzing: {date_slug} (source: {vtt_path[:40]}...)...")
    transcript = fetch_vtt_content(vtt_path)

    glossary_text = GLOSSARY
    official_terms = _load_official_terms_from_gcs(date_slug, bucket_uri)
    if official_terms:
        term_hints = '\n'.join(
            f"- {n['term']} ({n.get('type', '')}" + (f", {n['context']}" if n.get('context') else "") + ") — use this exact spelling"
            for n in official_terms if n.get('term')
        )
        glossary_text = GLOSSARY + "\nCanonical proper nouns from official documents (use these exact spellings):\n" + term_hints

    prompt = f"""
    Analyze the school board meeting transcript for {date_slug}.
    Extract: blurb, formal votes, high-level summary bullets, timestamped timeline, and topic tags.

    IMPORTANT: Identify perspectives from: Board, Administration, Teachers, Citizens.
    Glossary: {glossary_text}

    Guidelines:
    - Blurb: 1-2 sentence hook for the landing page.
    - Tags: Identify 3-5 specific, time-bound or scoped topic tags (e.g., '2026 Equity Policy Update' instead of 'Equity', 'FY26 Transportation Challenges' instead of 'Transportation'). Avoid broad, generic nouns unless referring to a standing systemic issue (like 'Reconfiguration'). Use {allowed_tags} to reuse existing specific tags where appropriate.
    - Votes: Exact motion, result (use "Passed" or "Failed" — a unanimous vote is "Passed"), count, and movers.
    - Summary: 5-8 bullets capturing the high-level arc of the meeting. Topics should be
      issue-level (e.g. "FY2026 Budget Update", "Cell Phone Policy") not speaker-level.
      Do not create separate bullets per speaker; perspectives can be noted briefly within
      a bullet's text if important.
    - Timeline: 20-30 entries covering the full meeting arc with timestamps (H:MM:SS) and
      total seconds. Use children[] for grouped speaker sections:
      - Public comment periods: one parent entry (topic: "Public Comment", seconds at period start,
        desc summarizing the period) with one child per NAMED commenter (seconds at their start,
        speaker full name, text 2-3 sentences). Unnamed speakers described in parent desc only.
      - Board discussion sections where multiple members speak: one parent entry
        (topic: "Board Discussion on [Subject]") with one child per member who speaks substantively.
      - All other entries: flat (empty children).
      Topic format: "Description (Speaker)" for flat entries — no possessive constructions
      (use "Reconfiguration Priorities (Daniel Feller)" not "Feller's Reconfiguration Priorities").
      Each desc/text should be 2-3 sentences of substance.
    - Board Attendance: Extract the roll call. For each person called, record name, status
      (Present or Absent), and role — use exactly "Board" for board members and "Student Rep"
      for student representatives.

    Transcript:
    {transcript}
    """

    try:
        time.sleep(2)
        models_to_try = [FLASH_MODEL, DEFAULT_MODEL]
        response = None
        for model in models_to_try:
            print(f"  Sending request to Gemini ({model}) for {date_slug}...", flush=True)
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config={'response_mime_type': 'application/json', 'response_schema': MeetingReport, 'temperature': 0.1}
                )
                break
            except Exception as model_err:
                if "429" in str(model_err):
                    print(f"  Rate-limited on {model}, trying next model...", flush=True)
                    continue
                raise
        if response is None:
            print(f"  All models rate-limited for {date_slug}. Run via production pipeline for higher limits.", flush=True)
            return {"slug": date_slug, "status": "RateLimit"}
        print(f"  Received response for {date_slug}.")
        report_data = json.loads(response.text)

        print(f"  Updating .njk file for {date_slug}...")
        with open(njk_path, 'r') as f: content = f.read()
        match = re.match(r'^(---\s*\n(.*?)\n---\s*(?:\n|$))(.*)', content, re.DOTALL)
        if not match: return None
        fm_text, body = match.group(2), match.group(3)

        data = yaml.safe_load(fm_text)

        # Backfill video URL if it appeared after stub creation
        if not data.get('video_url'):
            if not _vimeo_map:
                _vimeo_map.update(get_vimeo_mapping())
            if date_slug in _vimeo_map:
                data['has_video'] = True
                data['video_url'] = _vimeo_map[date_slug]

        data['stub'] = False
        data['has_transcript'] = True
        data['processed_date'] = date.today().isoformat()
        data['blurb'] = report_data.pop('blurb', '')
        data['topics'] = report_data.pop('tags', [])
        extracted_attendance = report_data.pop('board_attendance', [])
        data.update(report_data)  # votes, summary, timeline
        data['votes_source'] = 'Transcript (Unofficial)'
        if extracted_attendance:
            data['board_attendance'] = extracted_attendance
            data['attendance_source'] = 'Transcript (Unofficial)'

        new_fm = yaml.dump(data, sort_keys=False, default_flow_style=False)
        with open(njk_path, 'w') as f: f.write('---\n' + new_fm + '---\n' + body)
        return {"slug": date_slug, "status": "Success"}

    except Exception as e:
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

    # Build transcript mapping: bucket (lower priority) < Drive (higher priority)
    mapping = {}
    if bucket:
        try:
            mapping.update(get_bucket_vtt_mapping(bucket, CUTOFF_DATE))
        except Exception as e:
            print(f"Warning: could not read bucket VTTs: {e}")
    mapping.update(get_drive_vtt_mapping())

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
    print(f"Targeting {len(to_process)} meetings...", flush=True)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_single_meeting, slug, path, bucket): slug for slug, path in to_process.items()}
        rate_limited = []
        for future in as_completed(futures):
            res = future.result()
            if res:
                print(f"Finished {res['slug']}: {res['status']}", flush=True)
                if res['status'] == 'RateLimit':
                    rate_limited.append(res['slug'])
        if rate_limited:
            print(f"\nRate-limited on all models for: {', '.join(rate_limited)}")
            sys.exit(1)

if __name__ == "__main__":
    main()
