import os
import json
import re
import yaml
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from google import genai
from dotenv import load_dotenv

load_dotenv()

# Prioritize Vertex AI auth
provider = "studio"
import google.auth
try:
    credentials, project = google.auth.default(
        scopes=['https://www.googleapis.com/auth/cloud-platform']
    )
    project_id = 'spsd-board-meetings'
    client = genai.Client(credentials=credentials, project=project_id, location='us-central1', vertexai=True)
    model_name = 'gemini-2.5-flash'
    MAX_WORKERS = 4
except:
    api_key = os.getenv("GEMINI_PRO_API_KEY") or os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    model_name = 'gemini-1.5-flash'
    MAX_WORKERS = 1

GLOSSARY = {
    "Caler": "Kaler",
    "Skillen": "Skillin",
    "Skillens": "Skillins",
    "Caler's": "Kaler's"
}

TOPIC_BLACKLIST = ["Personnel", "Contracts", "Finance", "Budget"]

def generate_blurb(local, meeting_dir):
    print(f"  Generating blurb for {local['slug']}...")
    prompt = f"Write an extremely concise 1-2 sentence objective summary (a 'blurb') of this school board meeting based on these notes. Do not use quotes or introductory filler:\n"
    prompt += "\n".join([f"- {s.get('text', '')}" for s in local.get('summary', [])])
    try:
        response = client.models.generate_content(model=model_name, contents=prompt, config={'temperature': 0.1})
        blurb = response.text.strip().replace('\n', ' ').replace('"', "'")
        
        # Update NJK file
        njk_path = os.path.join(meeting_dir, local['slug'] + '.njk')
        with open(njk_path, 'r') as f: content = f.read()
        
        if 'blurb:' not in content:
            content = content.replace('---\n', f'---\nblurb: "{blurb}"\n', 1)
        else:
            content = re.sub(r'blurb:.*', f'blurb: "{blurb}"', content)
        with open(njk_path, 'w') as f: f.write(content)
        return local['slug'], blurb
    except Exception as e:
        print(f"  Error generating blurb for {local['slug']}: {e}")
        return local['slug'], None

def synthesize_topic(topic, evidence, display_date):
    print(f"  Synthesizing: {topic}...")
    prompt = f"""
    You are a policy analyst for the SPSD Board Meeting Archive.
    Synthesize the following chronological notes (NEWEST FIRST) regarding the topic: '{topic}'.

    Return ONLY a JSON object with these exact keys — no markdown fences, no commentary:
    {{
      "current_status": "1-2 plain sentences (no markdown) summarizing where things stand right now. Card-ready.",
      "overview": "2-3 paragraphs with natural citations to meeting dates (e.g. 'On {display_date}, the board decided...'). The first paragraph MUST cover the most recent developments. If a prior decision was reversed or modified, reflect that clearly.",
      "perspectives": {{
        "Board": "Summary of elected members' stance and questions. Omit key entirely if no data.",
        "Administration": "Summary of Superintendent/Directors' recommendations. Omit key entirely if no data.",
        "Staff": "Summary of staff and union rep viewpoints. Omit key entirely if no data.",
        "Citizens": "Summary of public comment and parent viewpoints. Omit key entirely if no data."
      }}
    }}

    RULES:
    - Omit any perspective key where there is genuinely no evidence in the notes.
    - Use "Staff" (not "Teachers") for the staff/union group.
    - SPELING: Kaler (NOT Caler), Skillin (NOT Skillen).

    Evidence (Newest First):
    {evidence}
    """
    try:
        response = client.models.generate_content(model=model_name, contents=prompt, config={'temperature': 0.1})
        raw = response.text.strip()
        # Strip code fences if present
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        result = json.loads(raw)
        return topic, result
    except json.JSONDecodeError as e:
        print(f"  JSON parse error for {topic}: {e}\n  Raw: {raw[:200]}")
        return topic, None
    except Exception as e:
        print(f"  Error synthesizing {topic}: {e}")
        return topic, None

def post_process():
    meeting_dir = 'src/meetings/'
    topics_lib_path = 'src/_data/topics.json'
    summary_lib_path = 'src/_data/topic_summaries.json'
    hashes_lib_path = 'scripts/topic_hashes.json'

    # 1. Enforce Glossary & Extract Data
    print("Enforcing Glossary and scanning meetings...")
    all_discovered_topics = set()
    meetings_data = []

    for filename in sorted(os.listdir(meeting_dir)):
        if not filename.endswith('.njk'): continue
        filepath = os.path.join(meeting_dir, filename)
        
        with open(filepath, 'r') as f: content = f.read()
        
        orig = content
        for w, r in GLOSSARY.items(): content = content.replace(w, r)
        if content != orig:
            with open(filepath, 'w') as f: f.write(content)

        match = re.search(r'^(---\s*\n(.*?)\n---\s*(?:\n|$))', content, re.DOTALL)
        if match:
            try:
                data = yaml.safe_load(match.group(2))
                data['slug'] = filename.replace('.njk', '')
                meetings_data.append(data)
                if 'topics' in data: all_discovered_topics.update(data['topics'])
            except: pass

    # 2. Topics Lib (Sorted by recency)
    print("Updating topics library...")
    # Find the most recent date for each topic
    topic_recent_dates = {}
    for m in meetings_data:
        m_date = str(m.get('date', m.get('slug', '')))
        for t in m.get('topics', []):
            if t not in TOPIC_BLACKLIST:
                if t not in topic_recent_dates or m_date > topic_recent_dates[t]:
                    topic_recent_dates[t] = m_date
                    
    # Sort topics based on the date (newest first)
    new_lib = sorted(list(topic_recent_dates.keys()), key=lambda x: topic_recent_dates[x], reverse=True)
    with open(topics_lib_path, 'w') as f: json.dump(new_lib, f, indent=2)

    # 3. Generate Missing Blurbs (write directly to .njk files; meetings.json is derived at build time)
    print("Generating blurbs for unprocessed meetings...")
    blurb_tasks = [m for m in meetings_data
                   if not m.get('stub') and not m.get('blurb') and m.get('summary')]
    if blurb_tasks:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(generate_blurb, local, meeting_dir): local for local in blurb_tasks}
            for future in as_completed(futures):
                future.result()  # generate_blurb writes blurb back to the .njk file

    # 4. Generate Synthesized Summaries (Concurrent + Caching)
    print("Generating high-level topic summaries...")
    summaries = {}
    if os.path.exists(summary_lib_path):
        with open(summary_lib_path, 'r') as f: summaries = json.load(f)
        
    hashes = {}
    if os.path.exists(hashes_lib_path):
        with open(hashes_lib_path, 'r') as f: hashes = json.load(f)

    topic_tasks = []
    sorted_m = sorted(meetings_data, key=lambda x: str(x.get('date', x.get('slug', ''))), reverse=True)

    for topic in new_lib:
        evidence_list = []
        display_date = sorted_m[0].get('display_date', 'recent dates') if sorted_m else 'recent dates'
        
        for m in sorted_m:
            if topic in m.get('topics', []):
                m_date = m.get('display_date', m.get('slug', ''))
                m_url = f"/meetings/{m['slug']}/"
                summary_bullets = m.get('summary', [])
                topic_bullets = [b['text'] for b in summary_bullets if topic.lower() in b['topic'].lower() or topic.lower() in b['text'].lower()]
                if topic_bullets:
                    evidence_list.append(f"Meeting: {m_date} ({m_url})\n" + "\n".join([f"- {b}" for b in topic_bullets]))
        
        if not evidence_list: continue
        
        evidence_str = "---".join(evidence_list[:15])
        current_hash = hashlib.md5(evidence_str.encode('utf-8')).hexdigest()
        
        # Check cache: Only synthesize if hash changed or summary missing
        if current_hash != hashes.get(topic) or topic not in summaries:
            topic_tasks.append((topic, evidence_str, display_date, current_hash))
        else:
            print(f"  Skipping {topic} (no new evidence).")

    if topic_tasks:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(synthesize_topic, t[0], t[1], t[2]): t for t in topic_tasks}
            for future in as_completed(futures):
                topic, result_text = future.result()
                if result_text:
                    summaries[topic] = result_text
                    # Update hash only on success
                    original_task = futures[future]
                    hashes[topic] = original_task[3]

    with open(summary_lib_path, 'w') as f: json.dump(summaries, f, indent=2)
    with open(hashes_lib_path, 'w') as f: json.dump(hashes, f, indent=2)
    print("Post-processing complete.")

if __name__ == "__main__":
    post_process()
