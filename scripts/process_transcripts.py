import os
import json
import re
import sys
import time
import yaml
from datetime import datetime as _dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from google import genai
from google.genai import types as genai_types
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
import pipeline_log

_vimeo_map = {}

# --- CONFIGURATION ---
def _make_client():
    """Client factory passed to call_llm() — memoized so repeated in-process calls reuse one
    client, while a subprocess (fresh process memory, no cache) builds its own on first use."""
    global _client
    if _client is None:
        creds, pid = get_credentials()
        _client = genai.Client(
            credentials=creds, project=pid, location='global', vertexai=True,
            http_options=genai_types.HttpOptions(client_args={'timeout': 120.0}),  # 2-min read timeout
        )
    return _client

_client = None
client = _make_client()  # eager: surfaces credential problems at import, warms the cache above

from model_config import DEFAULT_MODEL, call_llm, AllModelsRateLimited

MAX_WORKERS = 2

CUTOFF_DATE = "2023-08-01"
SCRIPT_VERSION = '2026-08-20'

from glossary_utils import load_glossary, render_glossary_hints
from board_members_utils import (
    load_board_members, save_board_members, active_members,
    canonicalize_attendance_names, unresolved_attendance, apply_discovered_members,
)

GLOSSARY_PATH = 'src/_data/glossary.json'
# Single source of truth shared with post_process.py — do not hardcode spelling
# corrections in either place (see todos/013). Board/role facts are intentionally
# NOT here; those come from src/_data/board_members.json and per-meeting official-
# document extraction only (see _load_official_terms_from_gcs below).
GLOSSARY_HINTS = render_glossary_hints(load_glossary(GLOSSARY_PATH))

BOARD_MEMBERS = load_board_members()

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
    votes: list[Vote]
    summary: list[SummaryItem]
    timeline: list[TimelineItem]
    board_attendance: list[AttendanceMember]

class VotesAndAttendance(BaseModel):
    votes: list[Vote]
    board_attendance: list[AttendanceMember]

GEMINI_TIMEOUT = 300  # seconds; enforced via subprocess SIGTERM


def _load_official_terms_from_gcs(docs, bucket_uri, catalog):
    """Load cached proper nouns extracted from a meeting's official docs, looked up directly by
    each doc's file_id in the shared Drive catalog (no bucket listing — see
    docs/pipeline.md#the-drive-catalog-drive_catalogjson)."""
    if not bucket_uri:
        return []
    all_terms = []
    for doc in (docs or []):
        m = re.search(r'/file/d/([^/?]+)', doc.get('url', ''))
        if not m:
            continue
        entry = catalog.get(m.group(1))
        if not entry or not entry.get('terms_blob'):
            continue
        cached = drive.read_cached_blob(bucket_uri, entry['terms_blob'])
        if cached is None:
            continue
        try:
            all_terms.extend(json.loads(cached))
        except Exception:
            pass
    return all_terms


def _find_priority_source_doc(docs):
    """Pick the best written record of votes/attendance for a meeting, preferring official
    minutes, then a meeting summary doc, over the transcript. Board packets often include an
    unratified "Meeting Summary" of a meeting well before its minutes are formally approved at
    a later meeting — that's still a far more reliable source than an auto-caption transcript.
    Returns (source_label, doc) or (None, None)."""
    docs = docs or []
    for d in docs:
        # No meeting in the archive currently tags its own full-board minutes this way (only
        # committee minutes use type=minutes today), but check first in case that changes.
        if d.get('type') == 'minutes' and 'committee' not in (d.get('label') or '').lower():
            return 'Minutes', d
    summary_docs = [d for d in docs if d.get('type') == 'pdf' and 'summary' in (d.get('label') or '').lower()]
    if summary_docs:
        # Prefer the plain meeting summary over an executive-session or standalone workshop one.
        preferred = [d for d in summary_docs
                     if 'workshop' not in d['label'].lower() and 'executive session' not in d['label'].lower()]
        return 'Meeting Summary', (preferred[0] if preferred else summary_docs[0])
    return None, None


def _read_doc_text(url, doc_type, bucket_uri, catalog, max_chars=15000):
    """Read (cached, or extract + cache) text for a Drive doc URL belonging to a meeting. Returns None on failure."""
    fid_match = re.search(r'/file/d/([^/?]+)', url)
    if not fid_match:
        return None
    file_id = fid_match.group(1)
    try:
        svc = drive.get_drive_service()
        entry = catalog.setdefault(file_id, {})
        if not all(k in entry for k in ('doc_type', 'mime_type', 'created_time')):
            meta = svc.files().get(fileId=file_id, fields='mimeType,createdTime').execute()
            entry.setdefault('doc_type', doc_type)
            entry.setdefault('mime_type', meta.get('mimeType'))
            entry.setdefault('created_time', meta.get('createdTime'))
        text, updates = drive.get_or_extract_text(bucket_uri, svc, catalog, file_id, max_chars=max_chars)
        if updates:
            entry.update(updates)
        return text
    except Exception as e:
        print(f"    Warning: could not read doc text: {e}")
        return None


def _extract_votes_attendance_from_doc(text, glossary_text):
    """Extract votes and board attendance from an official written record (minutes or meeting
    summary) — preferred over transcript-derived data when available."""
    prompt = f"""
    Extract formal board votes and attendance from this official school board document.

    Glossary: {glossary_text}

    Guidelines:
    - Votes: Exact motion, result (use "Passed" or "Failed" — a unanimous vote is "Passed"),
      count, and movers. For moved_2nd, use last names only (e.g. "DeAngelis, Richardson") —
      not full names, titles, or "Moved by X, seconded by Y" phrasing. Return [] if this
      document doesn't record any votes.
    - Board Attendance: Each board member or student representative this document states was
      present or absent. Use each person's full name as stated in the document (not just
      surname), with role "Board" or "Student Rep". Only include people whose presence/absence
      this document actually states — do not infer or guess at a full roster.
      Return [] if attendance isn't stated in this document.

    Document text:
    {text}
    """
    try:
        response_text, _model = call_llm(
            _make_client, prompt, temperature=0.1, response_schema=VotesAndAttendance,
        )
        return json.loads(response_text)
    except Exception as e:
        print(f"    Warning: could not extract votes/attendance from doc: {e}")
        return None


# --- VTT SOURCES ---




def fetch_vtt_content(path):
    """Returns VTT text from a local path or gs:// URI."""
    if path.startswith('gs://'):
        from google.cloud import storage
        without_scheme = path[5:]
        bucket_name, blob_name = without_scheme.split('/', 1)
        return storage.Client().bucket(bucket_name).blob(blob_name).download_as_text()
    with open(path, 'r') as f:
        return f.read()

# --- PROCESSING ---

def process_single_meeting(date_slug, vtt_path, bucket_uri=None, catalog=None):
    njk_path = f"src/meetings/{date_slug}.njk"
    if not os.path.exists(njk_path): return None
    catalog = catalog if catalog is not None else {}

    # A light early read of just `docs` — official-term lookup needs it before the full
    # front-matter read (and the transcript-analysis Gemini call) happens further down.
    with open(njk_path, 'r') as f:
        early_fm_match = re.match(r'^---\s*\n(.*?)\n---\s*(?:\n|$)', f.read(), re.DOTALL)
    early_docs = (yaml.safe_load(early_fm_match.group(1)) or {}).get('docs') if early_fm_match else None

    print(f"Analyzing: {date_slug} (source: {vtt_path[:40]}...)...")
    transcript = fetch_vtt_content(vtt_path)

    glossary_text = GLOSSARY_HINTS
    official_terms = _load_official_terms_from_gcs(early_docs, bucket_uri, catalog)
    if official_terms:
        term_hints = '\n'.join(
            f"- {n['term']} ({n.get('type', '')}" + (f", {n['context']}" if n.get('context') else "") + ") — use this exact spelling"
            for n in official_terms if n.get('term')
        )
        glossary_text = GLOSSARY_HINTS + "\nCanonical proper nouns from official documents (use these exact spellings):\n" + term_hints

    prompt = f"""
    Analyze the school board meeting transcript for {date_slug}.
    Extract: blurb, formal votes, high-level summary bullets, timestamped timeline, and board attendance.

    IMPORTANT: Identify perspectives from: Board, Administration, Teachers, Citizens.
    Glossary: {glossary_text}

    Guidelines:
    - Blurb: 1-2 sentence hook for the landing page.
    - Votes: Exact motion, result (use "Passed" or "Failed" — a unanimous vote is "Passed"), count, and movers.
      For moved_2nd, use last names only (e.g. "DeAngelis, Richardson") — not full names, titles,
      or "Moved by X, seconded by Y" phrasing.
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
    - Board Attendance: Extract the roll call. For each person called, record their full name
      as stated in the transcript (not just surname), status (Present or Absent), and role —
      use exactly "Board" for board members and "Student Rep" for student representatives.

    Transcript:
    {transcript}
    """

    try:
        time.sleep(2)

        def _log_attempt(model):
            print(f"  Sending request to Gemini ({model}) for {date_slug} at {_dt.now().strftime('%H:%M:%S')}...", flush=True)

        try:
            response_text, model_used = call_llm(
                _make_client, prompt, temperature=0.1, response_schema=MeetingReport,
                timeout=GEMINI_TIMEOUT, label=date_slug, on_attempt=_log_attempt,
            )
        except AllModelsRateLimited:
            print(f"  All models rate-limited for {date_slug}. Run via production pipeline for higher limits.", flush=True)
            return {"slug": date_slug, "status": "RateLimit"}
        print(f"  Received response for {date_slug} at {_dt.now().strftime('%H:%M:%S')}.")
        report_data = json.loads(response_text)

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
        data['pipeline_version'] = SCRIPT_VERSION
        data['blurb'] = report_data.pop('blurb', '')
        extracted_attendance = report_data.pop('board_attendance', [])
        data.update(report_data)  # votes, summary, timeline
        data['votes_source'] = 'Transcript (Unofficial)'
        if extracted_attendance:
            data['board_attendance'] = extracted_attendance
            data['attendance_source'] = 'Transcript (Unofficial)'

        # Favor a written record (minutes, or an unratified meeting summary) over the transcript
        # for votes/attendance when one is available — overriding independently, since a summary
        # doc may record one but not the other.
        source_label, source_doc = _find_priority_source_doc(data.get('docs'))
        if source_doc:
            doc_type = source_doc.get('type', 'doc')
            doc_text = _read_doc_text(source_doc['url'], doc_type, bucket_uri, catalog)
            if doc_text:
                extracted = _extract_votes_attendance_from_doc(doc_text, glossary_text)
                if extracted:
                    if extracted.get('votes'):
                        data['votes'] = extracted['votes']
                        data['votes_source'] = source_label
                    if extracted.get('board_attendance'):
                        data['board_attendance'] = extracted['board_attendance']
                        data['attendance_source'] = source_label

        # Canonicalize attendance names (e.g. "Ms. DeAngelis" -> "Rosemarie DeAngelis") against
        # whoever was actually active on this meeting's date. This only formats names already
        # extracted from the transcript/official document — it never adds, removes, or
        # determines who attended (see src/_data/board_members.json's date-scoped terms).
        # Names that don't uniquely match are returned as-is here, and surfaced to main() as
        # "unresolved" so board_members.json can be grown to cover them (see apply_discovered_members).
        unresolved = []
        if data.get('board_attendance'):
            try:
                meeting_date = date.fromisoformat(date_slug)
                active = active_members(BOARD_MEMBERS, meeting_date)
                unresolved = unresolved_attendance(data['board_attendance'], active)
                for u in unresolved:
                    u['date'] = meeting_date
                data['board_attendance'] = canonicalize_attendance_names(data['board_attendance'], active)
            except Exception as e:
                print(f"    Warning: could not canonicalize attendance names for {date_slug}: {e}")

        new_fm = yaml.dump(data, sort_keys=False, default_flow_style=False)
        with open(njk_path, 'w') as f: f.write('---\n' + new_fm + '---\n' + body)
        return {"slug": date_slug, "status": "Success", "model": model_used, "unresolved_attendance": unresolved}

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

    # GCS is the sole transcript source — source_data.py mirrors every Drive VTT into the
    # bucket before this script runs, so there's no freshness reason to ever read from Drive.
    mapping = {}
    if bucket:
        try:
            mapping = get_bucket_vtt_mapping(bucket, CUTOFF_DATE)
        except Exception as e:
            print(f"Warning: could not read bucket VTTs: {e}")

    to_process = {}

    if "--batch" in args:
        force = "--force" in args
        meeting_dir = 'src/meetings/'
        for filename in os.listdir(meeting_dir):
            if not filename.endswith('.njk'): continue
            slug = filename.replace('.njk', '')
            if slug not in mapping: continue
            with open(os.path.join(meeting_dir, filename), 'r') as f:
                content = f.read()
            if force:
                if f"pipeline_version: '{SCRIPT_VERSION}'" in content:
                    continue  # already processed at this version
                to_process[slug] = mapping[slug]
            else:
                if 'stub: true' in content:
                    to_process[slug] = mapping[slug]
    else:
        for arg in args:
            if arg in mapping: to_process[arg] = mapping[arg]

    if not to_process:
        print("No new meetings to process.")
        pipeline_log.append_entry(bucket, 'processing_log', {
            "run_id": pipeline_log.current_run_id(),
            "timestamp": _dt.utcnow().isoformat() + "Z",
            "stage": "transcripts",
            "targeted": [],
            "results": [],
            "rate_limited": [],
        })
        return
    print(f"Targeting {len(to_process)} meetings...", flush=True)
    catalog = drive.load_catalog(bucket)
    all_unresolved = []
    all_results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_single_meeting, slug, path, bucket, catalog): slug for slug, path in to_process.items()}
        rate_limited = []
        for future in as_completed(futures):
            slug = futures[future]
            try:
                res = future.result()
            except Exception as e:
                res = {"slug": slug, "status": f"Error: {e}"}
            if res:
                print(f"Finished {res['slug']}: {res['status']}", flush=True)
                all_results.append(res)
                if res['status'] == 'RateLimit':
                    rate_limited.append(res['slug'])
                all_unresolved.extend(res.get('unresolved_attendance') or [])

    # Grow board_members.json toward full coverage: any attendance name that couldn't be
    # matched to the active roster gets recorded (as a placeholder, or upgrading an existing
    # placeholder to a fuller name) so future runs canonicalize it correctly. Done once here,
    # single-threaded, rather than per-worker, to avoid concurrent writes to the shared file.
    if all_unresolved:
        board_members = load_board_members()
        if apply_discovered_members(board_members, all_unresolved):
            save_board_members(board_members)
            print(f"Updated board_members.json with {len(all_unresolved)} unresolved attendance observation(s).", flush=True)

    drive.save_catalog(bucket, catalog)

    pipeline_log.append_entry(bucket, 'processing_log', {
        "run_id": pipeline_log.current_run_id(),
        "timestamp": _dt.utcnow().isoformat() + "Z",
        "stage": "transcripts",
        "targeted": list(to_process.keys()),
        "results": [{"slug": r["slug"], "status": r["status"]} for r in all_results],
        "rate_limited": rate_limited,
    })

    if rate_limited:
        print(f"\nRate-limited on all models for: {', '.join(rate_limited)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
