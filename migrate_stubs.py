import os
import re
import json

with open('material_map_iso.json', 'r') as f:
    materials = json.load(f)

with open('src/_data/meetings.json', 'r') as f:
    meetings_data = json.load(f)

attendance_yaml = """board_attendance:
- name: "Rosemarie DeAngelis"
  status: "Present"
  role: "Board"
- name: "Tyler Smith"
  status: "Present"
  role: "Board"
- name: "Daniel Feller"
  status: "Present"
  role: "Board"
- name: "Claire Holman"
  status: "Present"
  role: "Board"
- name: "Eleni Richardson"
  status: "Present"
  role: "Board"
- name: "George Risch"
  status: "Present"
  role: "Board"
- name: "Angela Kabisa"
  status: "Present"
  role: "Student Rep"
- name: "Alex Davison"
  status: "Present"
  role: "Student Rep"
"""

processed_files = [
    '2026-05-11.njk', 
    '2026-05-27.njk', 
    '2026-04-29.njk', 
    '2026-04-13.njk', 
    '2026-04-07.njk', 
    '2025-12-08.njk', 
    '2025-12-10.njk'
]

directory = 'src/meetings/'
count_converted = 0
count_direct_links = 0
count_waiting = 0

for filename in os.listdir(directory):
    if not filename.endswith('.njk'):
        continue
        
    date_str = filename.replace('.njk', '')
    
    if filename in processed_files:
        # Just tally stats
        doc_count = len(materials.get(date_str, []))
        if doc_count > 0: count_direct_links += 1
        else: count_waiting += 1
        # Update meeting.json
        for m in meetings_data:
            if m['slug'] == date_str:
                m['doc_count'] = doc_count
                break
        continue
        
    filepath = os.path.join(directory, filename)
    with open(filepath, 'r') as f:
        content = f.read()
        
    match = re.match(r'^(---\s*\n.*?\n---\s*\n)(.*)', content, re.DOTALL)
    if not match:
        continue
        
    front_matter = match.group(1)
    
    # Check if board_attendance is already there, if not, add it
    if 'board_attendance:' not in front_matter:
        # insert before docs:
        front_matter = front_matter.replace('docs:', attendance_yaml + 'docs:')
        
    # Replace docs section
    docs_list = materials.get(date_str, [])
    
    new_docs_yaml = "docs:\n"
    for d in docs_list:
        label = d['label'].replace('"', '\\"')
        new_docs_yaml += f"- type: {d['type']}\n  label: \"{label}\"\n  url: \"{d['url']}\"\n"
        
    if not docs_list:
        new_docs_yaml = "docs: []\n"
        
    # Regex replace docs up to next field or end of front matter
    front_matter = re.sub(r'docs:.*?(?=\n\w+:|\n---)', new_docs_yaml.strip(), front_matter, flags=re.DOTALL)
    
    # Make sure layout is present
    if 'layout: layouts/meeting.njk' not in front_matter:
        front_matter = front_matter.replace('---\n', '---\nlayout: layouts/meeting.njk\n', 1)
        
    # Ensure stub is true
    front_matter = re.sub(r'stub:\s*false', 'stub: true', front_matter)
    
    new_content = front_matter.strip() + "\n"
    
    with open(filepath, 'w') as f:
        f.write(new_content)
        
    count_converted += 1
    if len(docs_list) > 0:
        count_direct_links += 1
    else:
        count_waiting += 1
        
    # Update meeting.json
    for m in meetings_data:
        if m['slug'] == date_str:
            m['doc_count'] = len(docs_list)
            break

with open('src/_data/meetings.json', 'w') as f:
    json.dump(meetings_data, f, indent=2)

print(f"Total meetings checked: {len(os.listdir(directory))}")
print(f"Stubs converted/cleared: {count_converted}")
print(f"Meetings with direct links: {count_direct_links}")
print(f"Meetings waiting for materials: {count_waiting}")