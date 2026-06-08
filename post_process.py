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

TOPIC_BLACKLIST = ["Personnel", "Contracts", "Finance", "Budget"]

def post_process():
    meeting_dir = 'src/meetings/'
    topics_lib_path = 'src/_data/topics.json'
    meetings_json_path = 'src/_data/meetings.json'
    summary_lib_path = 'src/_data/topic_summaries.json'

    # 1. Enforce Glossary & Extract Data
    print("Enforcing Glossary and scanning meetings...")
    all_discovered_topics = set()
    meetings_data = []

    for filename in sorted(os.listdir(meeting_dir)):
        if not filename.endswith('.njk'): continue
        filepath = os.path.join(meeting_dir, filename)
        
        with open(filepath, 'r') as f: content = f.read()
        
        # Glossary fix
        orig = content
        for w, r in GLOSSARY.items(): content = content.replace(w, r)
        if content != orig:
            with open(filepath, 'w') as f: f.write(content)

        # FM Extract
        match = re.search(r'^(---\s*\n(.*?)\n---\s*\n)', content, re.DOTALL)
        if match:
            try:
                data = yaml.safe_load(match.group(2))
                data['slug'] = filename.replace('.njk', '')
                meetings_data.append(data)
                if 'topics' in data: all_discovered_topics.update(data['topics'])
            except: pass

    # 2. Topics Lib
    print("Updating topics library...")
    new_lib = sorted([t for t in all_discovered_topics if t not in TOPIC_BLACKLIST])
    with open(topics_lib_path, 'w') as f: json.dump(new_lib, f, indent=2)

    # 3. Sync meetings.json
    print("Synchronizing meetings.json...")
    with open(meetings_json_path, 'r') as f: global_json = json.load(f)
    for g in global_json:
        local = next((m for m in meetings_data if m['slug'] == g['slug']), None)
        if local:
            g['stub'] = local.get('stub', True)
            g['topics'] = [t for t in local.get('topics', []) if t not in TOPIC_BLACKLIST]
            g['has_transcript'] = local.get('has_transcript', False)
            g['blurb'] = local.get('blurb', '')

    # 3.5 Generate Missing Blurbs
    print("Generating missing blurbs...")
    for local in meetings_data:
        if not local.get('stub', True) and not local.get('blurb') and local.get('summary'):
            print(f"  Generating blurb for {local['slug']}...")
            prompt = f"Write an extremely concise 1-2 sentence objective summary (a 'blurb') of this school board meeting based on these notes. Do not use quotes or introductory filler:\n"
            prompt += "\n".join([f"- {s.get('text', '')}" for s in local.get('summary', [])])
            try:
                response = client.models.generate_content(model=model_name, contents=prompt, config={'temperature': 0.1})
                blurb = response.text.strip().replace('\n', ' ').replace('"', "'")
                local['blurb'] = blurb
                
                # Update NJK file
                njk_path = os.path.join(meeting_dir, local['slug'] + '.njk')
                with open(njk_path, 'r') as f: content = f.read()
                
                if 'blurb:' not in content:
                    content = content.replace('---\n', f'---\nblurb: "{blurb}"\n', 1)
                else:
                    content = re.sub(r'blurb:.*', f'blurb: "{blurb}"', content)
                with open(njk_path, 'w') as f: f.write(content)
                
                # Update global json
                for g in global_json:
                    if g['slug'] == local['slug']: g['blurb'] = blurb
            except Exception as e:
                print(f"  Error generating blurb for {local['slug']}: {e}")

    with open(meetings_json_path, 'w') as f: json.dump(global_json, f, indent=2)

    # 4. Generate Synthesized Summaries
    print("Generating high-level topic summaries...")
    summaries = {}
    if os.path.exists(summary_lib_path):
        with open(summary_lib_path, 'r') as f: summaries = json.load(f)

    for topic in new_lib:
        evidence = []
        # Sort by date DESCENDING (newest first) for the LLM
        sorted_m = sorted(meetings_data, key=lambda x: str(x.get('date', x.get('slug', ''))), reverse=True)
        
        for m in sorted_m:
            if topic in m.get('topics', []):
                m_date = m.get('display_date', m.get('slug', ''))
                m_url = f"/meetings/{m['slug']}/"
                summary_bullets = m.get('summary', [])
                topic_bullets = [b['text'] for b in summary_bullets if topic.lower() in b['topic'].lower() or topic.lower() in b['text'].lower()]
                
                if topic_bullets:
                    # Include URL in evidence for citation
                    evidence.append(f"Meeting: {m_date} ({m_url})\n" + "\n".join([f"- {b}" for b in topic_bullets]))
        
        if not evidence: continue
        
        print(f"  Synthesizing: {topic}...")
        # Note: We provide evidence in NEWEST FIRST order so the LLM knows the current state
        prompt = f"""
        You are a policy analyst for the SPSD Board Meeting Archive. 
        Synthesize the following chronological notes (NEWEST FIRST) regarding the topic: '{topic}'.
        
        TASK:
        1. Write a 2-3 paragraph 'Current Status & Evolution' summary.
        2. MANDATORY: The first paragraph MUST focus on the absolute most recent developments, votes, or resolutions.
        3. If a previous decision was reversed or modified (e.g. reconfiguration delayed), reflect that clearly as the current status.
        4. Identify specific viewpoints from: Board, Administration, Teachers, Citizens.
        5. Include natural citations to meeting dates (e.g. "On {sorted_m[0].get('display_date', 'recent dates')}, the board decided...")
        6. SPELING: Kaler (NOT Caler), Skillin (NOT Skillen).

        Evidence (Newest First):
        {"---".join(evidence[:15])} 
        """
        try:
            response = client.models.generate_content(model=model_name, contents=prompt, config={'temperature': 0.1})
            summaries[topic] = response.text
        except Exception as e:
            print(f"  Error synthesizing {topic}: {e}")

    with open(summary_lib_path, 'w') as f: json.dump(summaries, f, indent=2)
    print("Post-processing complete.")

if __name__ == "__main__":
    post_process()
