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
        client = genai.Client(credentials=credentials, project=project, location='us-central1', vertexai=True)
        provider = "vertex"
        print(f"Using Vertex AI (Project: {project})")
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

# --- UTILITIES ---
def get_transcript_mapping():
    mapping = {}
    base_dir = "static/transcripts"
    for root, _, files in os.walk(base_dir):
        for f in files:
            if not f.endswith('.vtt'): continue
            path = os.path.join(root, f)
            m = re.search(r'(\d{4})(\d{2})(\d{2})', f)
            if m: mapping[f"{m.group(1)}-{m.group(2)}-{m.group(3)}"] = path
            elif re.search(r'(\d{2})\.(\d{2})\.(\d{2})', f):
                dm = re.search(r'(\d{2})\.(\d{2})\.(\d{2})', f)
                mapping[f"20{dm.group(3)}-{dm.group(1)}-{dm.group(2)}"] = path
            else:
                months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
                m = re.search(rf'({"|".join(months)}) (\d{{1,2}}) (\d{{4}})', f)
                if m:
                    from datetime import datetime
                    dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y")
                    mapping[dt.strftime("%Y-%m-%d")] = path
    return mapping

def process_single_meeting(date_slug, vtt_path):
    njk_path = f"src/meetings/{date_slug}.njk"
    if not os.path.exists(njk_path): return None

    allowed_tags = []
    if os.path.exists('src/_data/topics.json'):
        with open('src/_data/topics.json', 'r') as f: allowed_tags = json.load(f)

    print(f"Analyzing: {date_slug}...")
    with open(vtt_path, 'r') as f: transcript = f.read()

    prompt = f"""
    Analyze the school board meeting transcript for {date_slug}. 
    Extract: blurb, formal votes, high-level summary bullets, timestamped timeline, and topic tags.
    
    IMPORTANT: Identify perspectives from: Board, Administration, Teachers, Citizens.
    Glossary: {GLOSSARY}

    Guidelines:
    - Blurb: 1-2 sentence hook for the landing page.
    - Tags: Select 3-5 high-level tags from: {allowed_tags}.
    - Votes: Exact motion, result, count, and movers.
    - Summary: 5-8 bullets showing the arc of conversation.
    - Timeline: 10-15 key moments with timestamps (H:MM:SS) and total seconds.

    Transcript:
    {transcript}
    """

    try:
        if provider == "vertex": time.sleep(2)
        response = client.models.generate_content(
            model=DEFAULT_MODEL,
            contents=prompt,
            config={'response_mime_type': 'application/json', 'response_schema': MeetingReport, 'temperature': 0.1}
        )
        report_data = json.loads(response.text)
        
        # Load NJK
        with open(njk_path, 'r') as f: content = f.read()
        match = re.match(r'^(---\s*\n(.*?)\n---\s*\n)(.*)', content, re.DOTALL)
        if not match: return None
        fm_text, body = match.group(2), match.group(3)
        
        data = yaml.safe_load(fm_text)
        data['stub'] = False
        data['has_transcript'] = True
        data['processed_date'] = date.today().isoformat()
        data['blurb'] = report_data.pop('blurb', '')
        data['topics'] = report_data.pop('tags', [])
        data.update(report_data) # votes, summary, timeline

        new_fm = yaml.dump(data, sort_keys=False, default_flow_style=False)
        with open(njk_path, 'w') as f: f.write('---\n' + new_fm + '---\n' + body)
        return {"slug": date_slug, "status": "Success"}

    except Exception as e:
        if "429" in str(e): os._exit(1)
        return {"slug": date_slug, "status": f"Error: {e}"}

def main():
    args = sys.argv[1:]
    mapping = get_transcript_mapping()
    to_process = {}
    if "--batch" in args:
        with open('src/_data/meetings.json', 'r') as f: meetings = json.load(f)
        for m in meetings:
            if m['stub'] and m['slug'] in mapping: to_process[m['slug']] = mapping[m['slug']]
    else:
        for arg in args:
            if arg in mapping: to_process[arg] = mapping[arg]
    
    if not to_process: return
    print(f"Targeting {len(to_process)} meetings...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_single_meeting, slug, path): slug for slug, path in to_process.items()}
        for future in as_completed(futures):
            res = future.result()
            if res: print(f"Finished {res['slug']}: {res['status']}")

if __name__ == "__main__":
    main()
