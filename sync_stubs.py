import os
import json
import re

def fix_stub_status():
    meeting_dir = 'src/meetings/'
    json_path = 'src/_data/meetings.json'
    
    with open(json_path, 'r') as f:
        meetings_data = json.load(f)
        
    for filename in os.listdir(meeting_dir):
        if not filename.endswith('.njk'): continue
        slug = filename.replace('.njk', '')
        
        with open(os.path.join(meeting_dir, filename), 'r') as f:
            content = f.read()
            
        # If there is real summary data
        has_summary = 'summary:' in content and '  - topic:' in content
        
        for m in meetings_data:
            if m['slug'] == slug:
                m['stub'] = not has_summary
                break
                
    with open(json_path, 'w') as f:
        json.dump(meetings_data, f, indent=2)
    print("Synchronized stub status from NJK files.")

if __name__ == "__main__":
    fix_stub_status()
