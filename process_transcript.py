import sys
import os
import json
import re
import time
from google import genai
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Check for local auth flag
USE_LOCAL_AUTH = "--local-auth" in sys.argv
if USE_LOCAL_AUTH:
    sys.argv.remove("--local-auth")
    print("Using local/browser-based credentials (skipping .env)")
else:
    load_dotenv()

# Prioritize high-limit key, otherwise try standard, otherwise fall back to local auth
api_key = os.getenv("GEMINI_PRO_API_KEY") or os.getenv("GEMINI_API_KEY")

# Configuration
if USE_LOCAL_AUTH:
    DEFAULT_MODEL = 'gemini-2.5-pro'
    BACKUP_MODEL = 'gemini-2.5-flash'
else:
    DEFAULT_MODEL = 'gemini-2.0-flash'
    BACKUP_MODEL = 'gemini-1.5-flash'

client = None

if USE_LOCAL_AUTH:
    # Try to use Application Default Credentials
    import google.auth
    try:
        credentials, project = google.auth.default()
        client = genai.Client(credentials=credentials, project=project, location='us-central1', vertexai=True)
        print(f"Using Vertex AI with local credentials (Project: {project})")
    except Exception as e:
        print(f"Failed to load local credentials: {e}")
        sys.exit(1)
elif api_key and "your_" not in api_key:
    client = genai.Client(api_key=api_key)
    print("Using explicit API Key from .env")
else:
    client = genai.Client()
    print("No API Key found, using standard Client initialization (may fail)")

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
    motion: str = Field(description="The exact motion text")
    result: str = Field(description="'Pass' or 'Fail'")
    count: str = Field(description="Vote tally, e.g., 'Unan.', '7-0', '6-1'")
    moved_2nd: str = Field(description="Last names of the mover and seconder, e.g., 'Smith / Feller'")

class SummaryItem(BaseModel):
    topic: str = Field(description="A brief label for the topic")
    text: str = Field(description="A 1-2 sentence description of the discussion or action")

class TimelineItem(BaseModel):
    time: str = Field(description="Timestamp in H:MM:SS format")
    seconds: int = Field(description="Total seconds representation of the timestamp")
    topic: str = Field(description="A brief label for the timeline event")
    desc: str = Field(description="A 1-2 sentence description of the event")

class MeetingReport(BaseModel):
    tags: list[str] = Field(description="3-5 standardized, recurring high-level topic tags (e.g., 'FY2026 Budget', 'Superintendent Search', 'Policy', 'Facilities', 'Personnel', 'Contracts').")
    votes: list[Vote]
    summary: list[SummaryItem]
    timeline: list[TimelineItem]

def process_transcript(vtt_path):
    with open(vtt_path, 'r') as f:
        transcript = f.read()

    # Load standardized topics library
    topics_path = 'src/_data/topics.json'
    allowed_tags = []
    if os.path.exists(topics_path):
        with open(topics_path, 'r') as f:
            allowed_tags = json.load(f)

    prompt = f"""
    Analyze the following school board meeting transcript. Extract the formal votes, a high-level meeting summary, a chronological timeline of key events, and a list of standardized topic tags.
    
    IMPORTANT: Use the following Glossary for correct spelling:
    {GLOSSARY}

    Guidelines:
    - Tags: Select 3-5 high-level topic tags. Use the following standardized list as your primary source: {allowed_tags}. 
      If a major, recurring topic is discussed that is NOT in this list, you may propose a NEW standardized shorthand tag.
    - Votes: Extract the exact motion, result, tally (use 'Unan.' for unanimous), and the movers (LastName / LastName).
    - Summary: Provide 5-8 bullet points of the most significant topics discussed or decided.
    - Timeline: Extract 10-15 key moments with their exact starting timestamps in H:MM:SS format and the total seconds. 
      DO NOT include fractional seconds (e.g., use 0:01:05, NOT 0:01:05.400).

    Transcript:
    {transcript}
    """

    for model_name in [DEFAULT_MODEL, BACKUP_MODEL]:
        print(f"Attempting with model: {model_name}")
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    'response_mime_type': 'application/json',
                    'response_schema': MeetingReport,
                    'temperature': 0.1
                },
            )
            report_data = json.loads(response.text)
            
            # Post-process: strip fractional seconds if any slipped through
            for item in report_data.get('timeline', []):
                item['time'] = item['time'].split('.')[0]
            
            # Update the library if new tags were proposed
            if os.path.exists(topics_path):
                new_tags = [t for t in report_data.get('tags', []) if t not in allowed_tags]
                if new_tags:
                    allowed_tags.extend(new_tags)
                    allowed_tags = sorted(list(set(allowed_tags)))
                    with open(topics_path, 'w') as f:
                        json.dump(allowed_tags, f, indent=2)
                    print(f"Updated library with new topics: {new_tags}")

            return report_data
        except Exception as e:
            if "429" in str(e):
                print(f"Rate limited (429). Exiting script.")
                sys.exit(1)
            else:
                print(f"Error with {model_name}: {e}")
                
    raise Exception("All models exhausted or error encountered.")

def update_meeting_file(njk_path, report_data):
    with open(njk_path, 'r') as f:
        content = f.read()

    import yaml
    from datetime import date
    
    match = re.match(r'^(---\s*\n.*?\n---\s*\n)(.*)', content, re.DOTALL)
    if not match: return
    
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

    # Update meetings.json
    date_slug = os.path.basename(njk_path).replace('.njk', '')
    json_path = 'src/_data/meetings.json'
    
    if os.path.exists(json_path):
        with open(json_path, 'r') as f:
            meetings_data = json.load(f)
            
        for m in meetings_data:
            if m['slug'] == date_slug:
                m['topics'] = tags
                m['stub'] = False
                break
                
        with open(json_path, 'w') as f:
            json.dump(meetings_data, f, indent=2)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 process_transcript.py <vtt_file> <njk_file>")
        sys.exit(1)
        
    vtt = sys.argv[1]
    njk = sys.argv[2]
    
    print(f"Processing {vtt}...")
    try:
        report = process_transcript(vtt)
        update_meeting_file(njk, report)
        print(f"Successfully updated {njk}")
    except Exception as e:
        print(f"Error: {e}")
