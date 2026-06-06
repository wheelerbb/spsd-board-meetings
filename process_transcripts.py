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

# --- CONFIGURATION & PROVIDER-AWARE MODEL MAPPING ---
USE_LOCAL_AUTH = "--local-auth" in sys.argv
if USE_LOCAL_AUTH: sys.argv.remove("--local-auth")

# Vertex AI Model Names vs Studio API Model Names
MODEL_MAP = {
    "vertex": {
        "pro": "gemini-2.5-pro",
        "flash": "gemini-2.5-flash"
    },
    "studio": {
        "pro": "gemini-1.5-pro",
        "flash": "gemini-2.0-flash"
    }
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
    print("Using explicit API Key from .env")
else:
    print("Error: No authentication method found. Use --local-auth or set GEMINI_API_KEY in .env")
    sys.exit(1)

DEFAULT_MODEL = MODEL_MAP[provider]["pro"] # Pro for accuracy, Flash for speed
MAX_WORKERS = 4 if provider == "vertex" else 1

# --- SCHEMA DEFINITION ---
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

    # Load topics for the prompt
    allowed_tags = []
    if os.path.exists('src/_data/topics.json'):
        with open('src/_data/topics.json', 'r') as f: allowed_tags = json.load(f)

    print(f"Analyzing: {date_slug}...")
    with open(vtt_path, 'r') as f: transcript = f.read()

    prompt = f"""
    Analyze the school board meeting transcript for {date_slug}. 
    Extract: formal votes, high-level summary bullets, timestamped timeline, and topic tags.
    
    Guidelines:
    - Tags: Select 3-5 high-level tags from: {allowed_tags}. Propose NEW ones only if major topics are missing.
    - Votes: Exact motion text, result (Pass/Fail), count, and movers (LastName / LastName).
    - Summary: 5-8 significant discussion/decision bullets.
    - Timeline: 10-15 key moments with timestamps (H:MM:SS) and total seconds.
    - SPELING: Kaler (not Caler), Skillin (not Skillen).

    Transcript:
    {transcript}
    """

    try:
        if provider == "vertex": time.sleep(2) # Stagger Vertex calls
        response = client.models.generate_content(
            model=DEFAULT_MODEL,
            contents=prompt,
            config={'response_mime_type': 'application/json', 'response_schema': MeetingReport, 'temperature': 0.1}
        )
        report_data = json.loads(response.text)
        
        # Write to NJK
        with open(njk_path, 'r') as f: content = f.read()
        match = re.match(r'^(---\s*\n.*?\n---\s*\n)(.*)', content, re.DOTALL)
        if not match: return None
        fm_raw, body = match.group(1), match.group(2)
        
        fm_raw = fm_raw.replace('stub: true', 'stub: false')
        fm_raw = fm_raw.replace('has_transcript: false', 'has_transcript: true')
        if 'processed_date:' not in fm_raw: fm_raw = fm_raw.replace('\n---', f'\nprocessed_date: "{date.today().isoformat()}"\n---', 1)
        else: fm_raw = re.sub(r'processed_date:.*', f'processed_date: "{date.today().isoformat()}"', fm_raw)

        tags = report_data.pop('tags', [])
        # We store tags temporarily in the front matter so post_process can find them
        if 'topics:' not in fm_raw: fm_raw = fm_raw.replace('\n---', f'\ntopics: {json.dumps(tags)}\n---', 1)
        else: fm_raw = re.sub(r'topics:.*', f'topics: {json.dumps(tags)}', fm_raw)

        report_yaml = yaml.dump(report_data, sort_keys=False, default_flow_style=False)
        for key in ['votes', 'summary', 'timeline']: fm_raw = re.sub(rf'\n{key}:.*?(?=\n\w+:|\n---)', '', fm_raw, flags=re.DOTALL)
        new_fm = fm_raw.replace('\n---', '\n' + report_yaml.strip() + '\n---', 1)
        
        with open(njk_path, 'w') as f: f.write(new_fm + body)
        return {"slug": date_slug, "status": "Success"}

    except Exception as e:
        if "429" in str(e): 
            print("Rate limit reached. Exiting.")
            os._exit(1)
        return {"slug": date_slug, "status": f"Error: {e}"}

def main():
    args = sys.argv[1:]
    mapping = get_transcript_mapping()
    
    if not args:
        print("Usage: python3 process_transcripts.py <date_slug> OR --batch")
        return

    to_process = {}
    if "--batch" in args:
        # Only process stubs that have VTT files
        with open('src/_data/meetings.json', 'r') as f: meetings = json.load(f)
        for m in meetings:
            if m['stub'] and m['slug'] in mapping:
                to_process[m['slug']] = mapping[m['slug']]
    else:
        for arg in args:
            if arg in mapping: to_process[arg] = mapping[arg]
            else: print(f"Warning: No transcript found for {arg}")

    if not to_process:
        print("No meetings found for processing.")
        return

    print(f"Targeting {len(to_process)} meetings...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_single_meeting, slug, path): slug for slug, path in to_process.items()}
        for future in as_completed(futures):
            res = future.result()
            if res: print(f"Finished {res['slug']}: {res['status']}")

if __name__ == "__main__":
    main()
