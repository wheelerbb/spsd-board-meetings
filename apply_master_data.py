import os
import json
import re

with open('master_material_map.json', 'r') as f:
    materials = json.load(f)

with open('src/_data/meetings.json', 'r') as f:
    meetings_data = json.load(f)

directory = 'src/meetings/'

for filename in os.listdir(directory):
    if not filename.endswith('.njk'):
        continue
        
    date_str = filename.replace('.njk', '')
    docs_list = materials.get(date_str, [])
    
    filepath = os.path.join(directory, filename)
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Split front matter
    match = re.match(r'^(---\s*\n.*?\n---\s*\n)(.*)', content, re.DOTALL)
    if not match: continue
    
    fm_raw = match.group(1)
    body = match.group(2)
    
    # 1. Update Video (Vimeo only)
    vimeo_link = next((d['url'] for d in docs_list if 'vimeo.com' in d['url']), None)
    if vimeo_link:
        fm_raw = re.sub(r'has_video:.*', 'has_video: true', fm_raw)
        fm_raw = re.sub(r'video_url:.*', f'video_url: "{vimeo_link}"', fm_raw)
    else:
        fm_raw = re.sub(r'has_video:.*', 'has_video: false', fm_raw)
        fm_raw = re.sub(r'video_url:.*', 'video_url: ""', fm_raw)
        
    # 2. Update Docs (Exclude video from docs array, apply Minutes vs Summary rule)
    final_docs = []
    for d in docs_list:
        if 'vimeo.com' in d['url'] or 'videoplayer.telvue.com' in d['url']:
            continue
            
        label_lower = d['label'].lower()
        dtype = 'pdf'
        if 'agenda' in label_lower: dtype = 'agenda'
        if 'packet' in label_lower: dtype = 'packet'
        if 'minutes' in label_lower: dtype = 'min'
        # "Summary" stays 'pdf' -> moves to Misc
        
        final_docs.append({
            'type': dtype,
            'label': d['label'],
            'url': d['url']
        })
        
    new_docs_yaml = "docs:\n"
    for d in final_docs:
        label = d['label'].replace('"', '\\"')
        new_docs_yaml += f"- type: {d['type']}\n  label: \"{label}\"\n  url: \"{d['url']}\"\n"
        
    if not final_docs:
        new_docs_yaml = "docs: []\n"
        
    fm_raw = re.sub(r'docs:.*?(?=\n\w+:|\n---)', new_docs_yaml.strip(), fm_raw, flags=re.DOTALL)
    
    # Write back
    with open(filepath, 'w') as f:
        f.write(fm_raw + body)
        
    # Update global data
    for m in meetings_data:
        if m['slug'] == date_str:
            m['has_video'] = bool(vimeo_link)
            m['doc_count'] = len(final_docs)
            break

with open('src/_data/meetings.json', 'w') as f:
    json.dump(meetings_data, f, indent=2)

print("Applied master data updates site-wide.")
