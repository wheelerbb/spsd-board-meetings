import os
import json
import re
from google import genai
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Initialize Gemini client
# Note: Requires GEMINI_API_KEY environment variable to be set
client = genai.Client()

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
    
    Guidelines:
    - Tags: Select 3-5 high-level topic tags. Use the following standardized list as your primary source: {allowed_tags}. 
      If a major, recurring topic is discussed that is NOT in this list, you may propose a NEW standardized shorthand tag (e.g., 'Child Development Services' or 'Grant Funding').
    - Votes: Extract the exact motion, result, tally (use 'Unan.' for unanimous), and the movers (LastName / LastName).
    - Summary: Provide 5-8 bullet points of the most significant topics discussed or decided.
    - Timeline: Extract 10-15 key moments with their exact starting timestamps in H:MM:SS format and the total seconds.

    Transcript:
    {transcript}
    """

    response = client.models.generate_content(
        model='gemini-1.5-pro',
        contents=prompt,
        config={
            'response_mime_type': 'application/json',
            'response_schema': MeetingReport,
            'temperature': 0.1
        },
    )
    
    report_data = json.loads(response.text)
    
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

def update_meeting_file(njk_path, report_data):
    with open(njk_path, 'r') as f:
        content = f.read()

    # We need to inject the YAML data into the front matter
    import yaml
    
    match = re.match(r'^(---\s*\n.*?\n---\s*\n)(.*)', content, re.DOTALL)
    if not match: return
    
    fm_raw = match.group(1)
    fm_raw = fm_raw.replace('stub: true', 'stub: false')
    
    # Extract tags before dumping the rest to YAML
    tags = report_data.pop('tags', [])
    
    report_yaml = yaml.dump(report_data, sort_keys=False, default_flow_style=False)
    new_fm = fm_raw.replace('\n---', '\n' + report_yaml.strip() + '\n---', 1)
    
    with open(njk_path, 'w') as f:
        f.write(new_fm)

    # Update meetings.json with the tags
    date_slug = os.path.basename(njk_path).replace('.njk', '')
    json_path = 'src/_data/meetings.json'
    
    if os.path.exists(json_path):
        with open(json_path, 'r') as f:
            meetings_data = json.load(f)
            
        for m in meetings_data:
            if m['slug'] == date_slug:
                m['topics'] = tags
                break
                
        with open(json_path, 'w') as f:
            json.dump(meetings_data, f, indent=2)


import sys
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
