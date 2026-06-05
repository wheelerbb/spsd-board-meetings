import os
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from google import genai
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Prioritize the high-limit API key for bulk processing
api_key = os.getenv("GEMINI_PRO_API_KEY") or os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

# Configuration
DEFAULT_MODEL = 'gemini-2.0-flash' 
MAX_WORKERS = 10 # Increase workers since we have a pro key

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
    """Maps meeting slugs (YYYY-MM-DD) to their local VTT file paths."""
    mapping = {}
    base_dir = "static/transcripts"
    
    # Walk through transcripts directory
    for root, _, files in os.walk(base_dir):
        for f in files:
            if not f.endswith('.vtt'): continue
            
            # Extract date from filename
            # Handles: spboe_YYYYMMDD.vtt, 04.07.26.vtt, "South Portland Board of Education - March 9 2026.vtt"
            path = os.path.join(root, f)
            
            # Try YYYYMMDD
            m = re.search(r'(\d{4})(\d{2})(\d{2})', f)
            if m:
                mapping[f"{m.group(1)}-{m.group(2)}-{m.group(3)}"] = path
                continue
                
            # Try MM.DD.YY
            m = re.search(r'(\d{2})\.(\d{2})\.(\d{2})', f)
            if m:
                mapping[f"20{m.group(3)}-{m.group(1)}-{m.group(2)}"] = path
                continue
                
            # Try "Month Day Year"
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
        return f"Skipping {date_slug}: NJK file not found."

    print(f"Starting: {date_slug}")
    
    with open(vtt_path, 'r') as f:
        transcript = f.read()

    # Load standardized topics library
    topics_path = 'src/_data/topics.json'
    allowed_tags = []
    if os.path.exists(topics_path):
        with open(topics_path, 'r') as f:
            allowed_tags = json.load(f)

    prompt = f"""
    Analyze the following school board meeting transcript. Extract formal votes, high-level summary, timeline, and topic tags.
    
    Guidelines:
    - Tags: Select 3-5 from {allowed_tags}. Propose new shorthand tags only if major topics are missing.
    - Votes: Extract exact motion, result (Pass/Fail), tally, and movers (LastName / LastName).
    - Summary: 5-8 bullet points of significant discussion/decisions.
    - Timeline: 10-15 key moments with timestamps (H:MM:SS) and total seconds.

    Transcript:
    {transcript}
    """

    try:
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
        
        # 1. Update NJK file
        with open(njk_path, 'r') as f:
            content = f.read()

        import yaml
        from datetime import date
        
        match = re.match(r'^(---\s*\n.*?\n---\s*\n)(.*)', content, re.DOTALL)
        if not match: return f"Error {date_slug}: Front matter not found."
        
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
        
        # Clean up existing votes/summary/timeline if present to avoid duplication
        fm_raw = re.sub(r'\nvotes:.*?(?=\n\w+:|\n---)', '', fm_raw, flags=re.DOTALL)
        fm_raw = re.sub(r'\nsummary:.*?(?=\n\w+:|\n---)', '', fm_raw, flags=re.DOTALL)
        fm_raw = re.sub(r'\ntimeline:.*?(?=\n\w+:|\n---)', '', fm_raw, flags=re.DOTALL)

        new_fm = fm_raw.replace('\n---', '\n' + report_yaml.strip() + '\n---', 1)
        
        with open(njk_path, 'w') as f:
            f.write(new_fm + body)

        # 2. Update Global Metadata (Partial update to avoid race conditions in parallel)
        # We will return the tags to be updated at the end to avoid file locking issues
        return {"slug": date_slug, "tags": tags, "status": "Success"}

    except Exception as e:
        return {"slug": date_slug, "status": f"Error: {e}"}

def run_fast_loop():
    mapping = get_transcript_mapping()
    
    # Filter out already processed if desired, but for now we'll process all available 2026/2025
    to_process = {k: v for k, v in mapping.items() if "2026" in k or "2025" in k}
    
    print(f"Found {len(to_process)} meetings to process.")
    
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_single_meeting, slug, path): slug for slug, path in to_process.items()}
        for future in as_completed(futures):
            results.append(future.result())

    # Final global update
    json_path = 'src/_data/meetings.json'
    with open(json_path, 'r') as f:
        meetings_data = json.load(f)
        
    for res in results:
        if isinstance(res, dict) and res.get('status') == 'Success':
            for m in meetings_data:
                if m['slug'] == res['slug']:
                    m['topics'] = res['tags']
                    m['stub'] = False
                    break
    
    with open(json_path, 'w') as f:
        json.dump(meetings_data, f, indent=2)

    print("\n--- FAST LOOP COMPLETE ---")
    for res in results:
        if isinstance(res, dict):
            print(f"{res['slug']}: {res['status']}")
        else:
            print(res)

if __name__ == "__main__":
    run_fast_loop()
