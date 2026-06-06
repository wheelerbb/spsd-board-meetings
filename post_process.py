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
    model_name = 'gemini-2.0-flash'

GLOSSARY = {
    "Caler": "Kaler",
    "Skillen": "Skillin",
    "Skillens": "Skillins",
    "Caler's": "Kaler's"
}

TOPIC_BLACKLIST = ["Personnel", "Contracts"]

def post_process():
    meeting_dir = 'src/meetings/'
    topics_lib_path = 'src/_data/topics.json'
    meetings_json_path = 'src/_data/meetings.json'
    summary_lib_path = 'src/_data/topic_summaries.json'

    # 1. Enforce Glossary & Extract Data for Sync
    print("Enforcing Glossary and scanning meetings...")
    all_discovered_topics = set()
    meetings_data = []

    for filename in os.listdir(meeting_dir):
        if not filename.endswith('.njk'): continue
        filepath = os.path.join(meeting_dir, filename)
        
        with open(filepath, 'r') as f: content = f.read()
        
        # Glossary enforcement (surgically)
        original_content = content
        for wrong, right in GLOSSARY.items():
            content = content.replace(wrong, right)
        
        if content != original_content:
            with open(filepath, 'w') as f: f.write(content)
            print(f"  Fixed typos in {filename}")

        # Extract Front Matter
        match = re.search(r'^(---\s*\n(.*?)\n---\s*\n)', content, re.DOTALL)
        if match:
            try:
                data = yaml.safe_load(match.group(2))
                data['slug'] = filename.replace('.njk', '')
                meetings_data.append(data)
                # Track topics
                if 'topics' in data:
                    all_discovered_topics.update(data['topics'])
            except: pass

    # 2. Update Topics Library (Respecting Blacklist)
    print("Updating topics library...")
    with open(topics_lib_path, 'r') as f: current_topics = json.load(f)
    
    new_lib = sorted(list(set([t for t in all_discovered_topics if t not in TOPIC_BLACKLIST] + current_topics)))
    with open(topics_lib_path, 'w') as f: json.dump(new_lib, f, indent=2)

    # 3. Synchronize meetings.json
    print("Synchronizing meetings.json...")
    with open(meetings_json_path, 'r') as f: global_json = json.load(f)
    
    for g_meeting in global_json:
        # Find matching data from NJK
        local_data = next((m for m in meetings_data if m['slug'] == g_meeting['slug']), None)
        if local_data:
            g_meeting['stub'] = local_data.get('stub', True)
            g_meeting['topics'] = [t for t in local_data.get('topics', []) if t not in TOPIC_BLACKLIST]
            g_meeting['has_transcript'] = local_data.get('has_transcript', False)
            # doc_count is already handled by scraper generally, but we can preserve it
            
    with open(meetings_json_path, 'w') as f: json.dump(global_json, f, indent=2)

    # 4. Generate Topic Summaries (Topic Explorer)
    print("Generating high-level topic summaries...")
    summaries = {}
    if os.path.exists(summary_lib_path):
        with open(summary_lib_path, 'r') as f: summaries = json.load(f)

    # We only summarize topics that appear in at least 2 meetings or are major
    for topic in new_lib:
        # Gather chronological evidence
        evidence = []
        # Sort by date, handle missing date field
        sorted_m_data = sorted(meetings_data, key=lambda x: str(x.get('date', x.get('slug', ''))))
        
        for m in sorted_m_data:
            if topic in m.get('topics', []):
                summary_bullets = m.get('summary', [])
                topic_bullets = [b['text'] for b in summary_bullets if topic.lower() in b['topic'].lower() or topic.lower() in b['text'].lower()]
                if topic_bullets:
                    m_date = m.get('date', m.get('slug', 'Unknown Date'))
                    evidence.append(f"Date: {m_date}\n" + "\n".join(topic_bullets))
        
        if len(evidence) < 1: continue # Skip if no evidence
        
        print(f"  Summarizing: {topic}...")
        prompt = f"""
        You are a policy analyst for the SPSD Board Meeting Archive. 
        Synthesize the following chronological notes regarding: '{topic}'.
        
        TASK:
        1. Write 2-3 paragraphs of 'Current Status & Impact'. 
        2. Start with the most recent developments, votes, or resolutions.
        3. Trace the evolution of the topic across the provided meetings.
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
