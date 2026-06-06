import os
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from google import genai
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Prioritize high-limit key, otherwise try standard, otherwise fall back to local auth
api_key = os.getenv("GEMINI_PRO_API_KEY") or os.getenv("GEMINI_API_KEY")

# Configuration
DEFAULT_MODEL = 'gemini-2.5-pro'
BACKUP_MODEL = 'gemini-2.5-flash'
MAX_WORKERS = 4 

client = None

# Try to use Application Default Credentials (Vertex AI)
import google.auth
try:
    credentials, project = google.auth.default()
    client = genai.Client(credentials=credentials, project=project, location='us-central1', vertexai=True)
    print(f"Using Vertex AI with local credentials (Project: {project})")
except Exception as e:
    if api_key and "your_" not in api_key:
        client = genai.Client(api_key=api_key)
        print("Using explicit API Key from .env")
        DEFAULT_MODEL = 'gemini-2.0-flash' # Fallback to faster model for key-based
    else:
        print(f"Failed to load local credentials: {e}")
        sys.exit(1)

GLOSSARY = """
- Kaler Elementary School (NOT Caler)
- Dyer Elementary School
- Skillin Elementary School (NOT Skillen)
- Brown Elementary School
- Small Elementary School
- Mahoney Middle School
- Memorial Middle School
- South Portland High School (SPHS)
- SPSD (South Portland School Department)
- SPBoE (South Portland Board of Education)
- SPESPA (South Portland Education Support Professionals Association)
- SPTA (South Portland Teachers Association)
- APC (Administrative Policy Committee)
- MSMA (Maine School Management Association)
- NESDEC (New England School Development Council)
- Zeal Education Group
"""

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
    tags: list[str]
    votes: list[Vote]
    summary: list[SummaryItem]
    timeline: list[TimelineItem]

def get_transcript_mapping():
    mapping = {}
    base_dir = "static/transcripts"
    for root, _, files in os.walk(base_dir):
        for f in files:
            if not f.endswith('.vtt'): continue
            path = os.path.join(root, f)
            m = re.search(r'(\d{4})(\d{2})(\d{2})', f)
            if m:
                mapping[f"{m.group(1)}-{m.group(2)}-{m.group(3)}"] = path
                continue
            m = re.search(r'(\d{2})\.(\d{2})\.(\d{2})', f)
            if m:
                mapping[f"20{m.group(3)}-{m.group(1)}-{m.group(2)}"] = path
                continue
            months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
            m = re.search(rf'({"|".join(months)}) (\d{{1,2}}) (\d{{4}})', f)
            if m:
                from datetime import datetime
                dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y")
                mapping[dt.strftime("%Y-%m-%d")] = path
                continue
    return mapping

def process_single_meeting(date_slug, vtt_path):
    njk_path = f"src/meetings/{date_slug}.njk"
    if not os.path.exists(njk_path):
        return None

    with open(njk_path, 'r') as f:
        existing_content = f.read()
    
    # Skip if already processed (check for summary field in YAML)
    if "summary:" in existing_content and "stub: false" in existing_content:
        # Check if it was processed today to allow for refinement
        if f'processed_date: "{time.strftime("%Y-%m-%d")}"' in existing_content:
             print(f"Skipping {date_slug}: Already processed today.")
             return None

    with open(vtt_path, 'r') as f:
        transcript = f.read()

    topics_path = 'src/_data/topics.json'
    allowed_tags = []
    if os.path.exists(topics_path):
        with open(topics_path, 'r') as f:
            allowed_tags = json.load(f)

    prompt = f"""
    Analyze the following school board meeting transcript. Extract formal votes, high-level summary, timeline, and topic tags.
    
    IMPORTANT: Use the following Glossary for correct spelling:
    {GLOSSARY}

    Guidelines:
    - Tags: Select 3-5 high-level tags from: {allowed_tags}. Propose new shorthand tags only if needed.
    - Votes: Extract exact motion, result (Pass/Fail), tally, and movers (LastName / LastName).
    - Summary: 5-8 bullet points of significant discussion/decisions.
    - Timeline: 10-15 key moments with timestamps (H:MM:SS) and total seconds. 
      DO NOT include fractional seconds (e.g., use 0:01:05, NOT 0:01:05.400).

    Transcript:
    {transcript}
    """

    try:
        # Stagger to avoid rate limits
        time.sleep(2)
        
        response = client.models.generate_content(
            model=DEFAULT_MODEL,
            contents=prompt,
            config={
                'response_mime_type': 'application/json',
                'response_schema': MeetingReport,
                'temperature': 0.1
            },
        )
        report_data = json.loads(response.text)
        
        # Clean timestamps
        for item in report_data.get('timeline', []):
            item['time'] = item['time'].split('.')[0]

        # Update NJK file
        import yaml
        from datetime import date
        match = re.match(r'^(---\s*\n.*?\n---\s*\n)(.*)', existing_content, re.DOTALL)
        if not match: return None
        
        fm_raw = match.group(1)
        body = match.group(2)
        
        fm_raw = fm_raw.replace('stub: true', 'stub: false')
        fm_raw = fm_raw.replace('has_transcript: false', 'has_transcript: true')
        
        today = date.today().isoformat()
        if 'processed_date:' not in fm_raw:
            fm_raw = fm_raw.replace('\n---', f'\nprocessed_date: "{today}"\n---', 1)
        else:
            fm_raw = re.sub(r'processed_date:.*', f'processed_date: "{today}"', fm_raw)

        tags = report_data.pop('tags', [])
        report_yaml = yaml.dump(report_data, sort_keys=False, default_flow_style=False)
        
        fm_raw = re.sub(r'\nvotes:.*?(?=\n\w+:|\n---)', '', fm_raw, flags=re.DOTALL)
        fm_raw = re.sub(r'\nsummary:.*?(?=\n\w+:|\n---)', '', fm_raw, flags=re.DOTALL)
        fm_raw = re.sub(r'\ntimeline:.*?(?=\n\w+:|\n---)', '', fm_raw, flags=re.DOTALL)

        new_fm = fm_raw.replace('\n---', '\n' + report_yaml.strip() + '\n---', 1)
        with open(njk_path, 'w') as f:
            f.write(new_fm + body)

        return {"slug": date_slug, "tags": tags, "status": "Success"}

    except Exception as e:
        if "429" in str(e):
            print(f"Rate limited on {date_slug}. Stopping batch.")
            os._exit(1)
        return {"slug": date_slug, "status": f"Error: {e}"}

def run_fast_loop():
    mapping = get_transcript_mapping()
    to_process = {k: v for k, v in mapping.items() if "2026" in k or "2025" in k}
    
    print(f"Found {len(to_process)} meetings to process.")
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_single_meeting, slug, path): slug for slug, path in to_process.items()}
        for future in as_completed(futures):
            res = future.result()
            if res:
                results.append(res)
                print(f"Completed: {res['slug']} - {res['status']}")

    # Update meetings.json
    json_path = 'src/_data/meetings.json'
    with open(json_path, 'r') as f:
        meetings_data = json.load(f)
    for res in results:
        if res.get('status') == 'Success':
            for m in meetings_data:
                if m['slug'] == res['slug']:
                    m['topics'] = res['tags']
                    m['stub'] = False
                    break
    with open(json_path, 'w') as f:
        json.dump(meetings_data, f, indent=2)

if __name__ == "__main__":
    run_fast_loop()
