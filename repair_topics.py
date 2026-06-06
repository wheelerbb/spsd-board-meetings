import os
import re
import json
import yaml

def repair_topics():
    meeting_dir = 'src/meetings/'
    topics_lib_path = 'src/_data/topics.json'
    
    with open(topics_lib_path, 'r') as f:
        topics_lib = json.load(f)
        
    for filename in os.listdir(meeting_dir):
        if not filename.endswith('.njk'): continue
        filepath = os.path.join(meeting_dir, filename)
        
        with open(filepath, 'r') as f:
            content = f.read()
            
        match = re.match(r'^(---\s*\n(.*?)\n---\s*\n)(.*)', content, re.DOTALL)
        if not match: continue
        
        fm_raw = match.group(1)
        fm_text = match.group(2)
        body = match.group(3)
        
        try:
            data = yaml.safe_load(fm_text)
        except: continue
        
        # If topics is missing or empty, infer from summary
        discovered_topics = set()
        if 'summary' in data:
            for item in data['summary']:
                label = item.get('topic', '')
                # Try to find a match in the library
                for t in topics_lib:
                    if t.lower() in label.lower() or label.lower() in t.lower():
                        discovered_topics.add(t)
        
        # Also check existing 'topics' if they were just empty
        existing_topics = data.get('topics', [])
        if isinstance(existing_topics, list):
            discovered_topics.update(existing_topics)
            
        if discovered_topics:
            topics_list = sorted(list(discovered_topics))
            # Inject topics: into front matter if not present
            if 'topics:' not in fm_text:
                new_fm_text = fm_text.strip() + f"\ntopics: {json.dumps(topics_list)}\n"
            else:
                new_fm_text = re.sub(r'topics:.*', f'topics: {json.dumps(topics_list)}', fm_text)
            
            new_content = f"---\n{new_fm_text}---\n{body}"
            with open(filepath, 'w') as f:
                f.write(new_content)
            print(f"Repaired topics for {filename}: {topics_list}")

if __name__ == "__main__":
    repair_topics()
