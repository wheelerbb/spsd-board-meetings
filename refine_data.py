import os
import json
import re
from datetime import datetime

with open('vimeo_master_list.json', 'r') as f:
    vimeo_data = json.load(f)

with open('master_material_map.json', 'r') as f:
    materials_map = json.load(f)

with open('src/_data/meetings.json', 'r') as f:
    meetings_data = json.load(f)

# From map_vimeo.py
def parse_date(text):
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    match = re.search(rf'({"|".join(months)}) \d{{1,2}} \d{{4}}', text)
    if match:
        try:
            dt = datetime.strptime(match.group(0).replace(',', ''), "%B %d %Y")
            return dt.strftime("%Y-%m-%d")
        except: pass
    match = re.search(rf'({"|".join(months)}) \d{{1,2}}, \d{{4}}', text)
    if match:
        try:
            dt = datetime.strptime(match.group(0), "%B %d, %Y")
            return dt.strftime("%Y-%m-%d")
        except: pass
    match = re.search(r'spboe_(?:bud_)?(?:ws_)?(?:sm_)?(\d{8})', text)
    if match:
        try:
            dt = datetime.strptime(match.group(1), "%Y%m%d")
            return dt.strftime("%Y-%m-%d")
        except: pass
    return None

vimeo_map = {}
for vid_id, title in vimeo_data.items():
    date = parse_date(title)
    if date:
        vimeo_map[date] = f"https://vimeo.com/{vid_id}"

directory = 'src/meetings/'
count_updated = 0

for filename in os.listdir(directory):
    if not filename.endswith('.njk'): continue
    date_str = filename.replace('.njk', '')
    
    filepath = os.path.join(directory, filename)
    with open(filepath, 'r') as f:
        content = f.read()
        
    match = re.match(r'^(---\s*\n.*?\n---\s*\n)(.*)', content, re.DOTALL)
    if not match: continue
    
    fm_raw = match.group(1)
    body = match.group(2)
    
    # 1. Update Video (Vimeo only)
    vimeo_url = vimeo_map.get(date_str)
    # Check materials map too
    if not vimeo_url:
        for d in materials_map.get(date_str, []):
            if 'vimeo.com' in d['url']:
                vimeo_url = d['url']
                break
                
    if vimeo_url:
        fm_raw = re.sub(r'has_video:.*', 'has_video: true', fm_raw)
        fm_raw = re.sub(r'video_url:.*', f'video_url: "{vimeo_url}"', fm_raw)
    else:
        fm_raw = re.sub(r'has_video:.*', 'has_video: false', fm_raw)
        fm_raw = re.sub(r'video_url:.*', 'video_url: ""', fm_raw)
        
    # 2. Update Docs
    scraped_docs = materials_map.get(date_str, [])
    final_docs = []
    for d in scraped_docs:
        # Exclude videos
        if 'vimeo.com' in d['url'] or 'videoplayer.telvue.com' in d['url']:
            continue
        
        label_lower = d['label'].lower()
        dtype = 'pdf'
        if 'agenda' in label_lower: dtype = 'agenda'
        if 'packet' in label_lower: dtype = 'packet'
        # Rule: ONLY "Minutes" in minutes column. "Summary" moves to Misc (pdf).
        if 'minutes' in label_lower and 'summary' not in label_lower: 
            dtype = 'min'
            
        final_docs.append({
            'type': dtype,
            'label': d['label'],
            'url': d['url']
        })
    
    # Deduplicate by URL
    seen_urls = set()
    unique_docs = []
    for d in final_docs:
        if d['url'] not in seen_urls:
            unique_docs.append(d)
            seen_urls.add(d['url'])
            
    new_docs_yaml = "docs:\n"
    for d in unique_docs:
        label = d['label'].replace('"', '\\"')
        new_docs_yaml += f"- type: {d['type']}\n  label: \"{label}\"\n  url: \"{d['url']}\"\n"
        
    if not unique_docs:
        new_docs_yaml = "docs: []\n"
        
    fm_raw = re.sub(r'docs:.*?(?=\n\w+:|\n---)', new_docs_yaml.strip(), fm_raw, flags=re.DOTALL)
    
    # Write back
    with open(filepath, 'w') as f:
        f.write(fm_raw + body)
        
    count_updated += 1
    
    # Update global data
    for m in meetings_data:
        if m['slug'] == date_str:
            m['has_video'] = bool(vimeo_url)
            m['doc_count'] = len(unique_docs)
            break

with open('src/_data/meetings.json', 'w') as f:
    json.dump(meetings_data, f, indent=2)

print(f"Refined {count_updated} meetings with latest data.")
