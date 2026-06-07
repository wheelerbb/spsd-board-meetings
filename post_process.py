import os
import json
import re
import yaml
from google import genai
from dotenv import load_dotenv

load_dotenv()

# Prioritize Vertex AI auth
provider = "studio"
import google.auth
try:
    credentials, project = google.auth.default()
    client = genai.Client(credentials=credentials, project=project, location='us-central1', vertexai=True)
    model_name = 'gemini-2.5-flash'
except:
    api_key = os.getenv("GEMINI_PRO_API_KEY") or os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    model_name = 'gemini-1.5-flash'

GLOSSARY = {
    "Caler": "Kaler",
    "Skillen": "Skillin",
    "Skillens": "Skillins",
    "Caler's": "Kaler's"
}

TOPIC_BLACKLIST = ["Personnel", "Contracts", "Finance", "Budget"] # Generic versions

def get_vtt_files():
    vtt_dates = set()
    base_dir = "static/transcripts"
    if not os.path.exists(base_dir): return vtt_dates
    for root, _, files in os.walk(base_dir):
        for f in files:
            if not f.endswith('.vtt'): continue
            # Basic date extraction logic
            m = re.search(r'(\d{4})(\d{2})(\d{2})', f)
            if m: vtt_dates.add(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
            elif re.search(r'(\d{2})\.(\d{2})\.(\d{2})', f):
                dm = re.search(r'(\d{2})\.(\d{2})\.(\d{2})', f)
                vtt_dates.add(f"20{dm.group(3)}-{dm.group(1)}-{dm.group(2)}")
            else:
                months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
                m = re.search(rf'({"|".join(months)}) (\d{{1,2}}) (\d{{4}})', f)
                if m:
                    from datetime import datetime
                    dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y")
                    vtt_dates.add(dt.strftime("%Y-%m-%d"))
    return vtt_dates

def post_process():
    meeting_dir = 'src/meetings/'
    topics_lib_path = 'src/_data/topics.json'
    meetings_json_path = 'src/_data/meetings.json'
    summary_lib_path = 'src/_data/topic_summaries.json'

    vtt_files = get_vtt_files()

    # 1. Enforce Glossary & Extract Data for Sync
    print("Enforcing Glossary and scanning meetings...")
    all_discovered_topics = set()
    meetings_data = []

    for filename in os.listdir(meeting_dir):
        if not filename.endswith('.njk'): continue
        filepath = os.path.join(meeting_dir, filename)
        date_slug = filename.replace('.njk', '')
        
        with open(filepath, 'r') as f: content = f.read()
        
        # Glossary enforcement
        original_content = content
        for wrong, right in GLOSSARY.items():
            content = content.replace(wrong, right)
        
        # Inject VTT source status
        has_vtt = date_slug in vtt_files
        if 'has_vtt_source:' not in content:
            content = re.sub(r'has_transcript:', f'has_vtt_source: {str(has_vtt).lower()}\nhas_transcript:', content)
        else:
            content = re.sub(r'has_vtt_source:.*', f'has_vtt_source: {str(has_vtt).lower()}', content)

        if content != original_content:
            with open(filepath, 'w') as f: f.write(content)
            # print(f"  Updated {filename}")

        # Extract Front Matter
        match = re.search(r'^(---\s*\n(.*?)\n---\s*\n)', content, re.DOTALL)
        if match:
            try:
                data = yaml.safe_load(match.group(2))
                data['slug'] = date_slug
                meetings_data.append(data)
                if 'topics' in data:
                    all_discovered_topics.update(data['topics'])
            except: pass

    # 2. Update Topics Library
    print("Updating topics library...")
    with open(topics_lib_path, 'r') as f: current_topics = json.load(f)
    new_filtered_topics = [t for t in all_discovered_topics if t not in TOPIC_BLACKLIST]
    new_lib = sorted(list(set(new_filtered_topics + current_topics)))
    with open(topics_lib_path, 'w') as f: json.dump(new_lib, f, indent=2)

    # 3. Synchronize meetings.json
    print("Synchronizing meetings.json...")
    with open(meetings_json_path, 'r') as f: global_json = json.load(f)
    
    for g_meeting in global_json:
        local_data = next((m for m in meetings_data if m['slug'] == g_meeting['slug']), None)
        if local_data:
            g_meeting['stub'] = local_data.get('stub', True)
            g_meeting['topics'] = [t for t in local_data.get('topics', []) if t not in TOPIC_BLACKLIST]
            g_meeting['has_transcript'] = local_data.get('has_transcript', False)
            g_meeting['blurb'] = local_data.get('blurb', '')
            
    with open(meetings_json_path, 'w') as f: json.dump(global_json, f, indent=2)

    # 4. Generate Topic Summaries
    print("Generating high-level topic summaries...")
    summaries = {}
    if os.path.exists(summary_lib_path):
        with open(summary_lib_path, 'r') as f: summaries = json.load(f)

    for topic in new_lib:
        evidence = []
        sorted_m_data = sorted(meetings_data, key=lambda x: str(x.get('date', x.get('slug', ''))))
        for m in sorted_m_data:
            if topic in m.get('topics', []):
                summary_bullets = m.get('summary', [])
                topic_bullets = [b['text'] for b in summary_bullets if topic.lower() in b['topic'].lower() or topic.lower() in b['text'].lower()]
                if topic_bullets:
                    m_date = m.get('date', m.get('slug', 'Unknown Date'))
                    evidence.append(f"Date: {m_date}\n" + "\n".join(topic_bullets))
        
        if not evidence: continue
        
        print(f"  Summarizing: {topic}...")
        prompt = f"""
        You are a policy analyst for the SPSD Board Meeting Archive. 
        Synthesize the following chronological notes regarding: '{topic}'.
        
        TASK:
        1. Write 2-3 paragraphs of 'Current Status & Impact'. 
        2. Start with the most recent developments, votes, or resolutions.
        3. Trace the evolution across these groups: Board, Administration, Teachers, Citizens.
        4. SPELING: Kaler (NOT Caler), Skillin (NOT Skillen).

        Meeting Evidence:
        {"---".join(evidence)}
        """
        try:
            response = client.models.generate_content(model=model_name, contents=prompt, config={'temperature': 0.1})
            summaries[topic] = response.text
        except Exception as e:
            print(f"  Error summarizing {topic}: {e}")

    with open(summary_lib_path, 'w') as f: json.dump(summaries, f, indent=2)
    print("Post-processing complete.")

if __name__ == "__main__":
    post_process()
