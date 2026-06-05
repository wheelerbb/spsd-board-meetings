import os
import json
import re
from datetime import datetime

def parse_date(text):
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    # Look for "Month Day Year"
    match = re.search(rf'({"|".join(months)}) \d{{1,2}} \d{{4}}', text)
    if match:
        try:
            dt = datetime.strptime(match.group(0), "%B %d %Y")
            return dt.strftime("%Y-%m-%d")
        except: pass
    
    # Try "Month Day, Year"
    match = re.search(rf'({"|".join(months)}) \d{{1,2}}, \d{{4}}', text)
    if match:
        try:
            dt = datetime.strptime(match.group(0), "%B %d, %Y")
            return dt.strftime("%Y-%m-%d")
        except: pass

    # Try "YYYYMMDD" in spboe_YYYYMMDD
    match = re.search(r'spboe_(?:bud_)?(?:ws_)?(?:sm_)?(\d{8})', text)
    if match:
        try:
            dt = datetime.strptime(match.group(1), "%Y%m%d")
            return dt.strftime("%Y-%m-%d")
        except: pass

    return None

def apply_vimeo_mapping():
    with open('vimeo_master_list.json', 'r') as f:
        vimeo_data = json.load(f)

    with open('src/_data/meetings.json', 'r') as f:
        meetings_json = json.load(f)

    directory = 'src/meetings/'
    
    # Create a reverse map for vimeo
    vimeo_map = {}
    for vid_id, title in vimeo_data.items():
        date = parse_date(title)
        if date:
            vimeo_map[date] = f"https://vimeo.com/{vid_id}"

    # Handle explicit mapping for known messy ones if any
    # (Checking the list earlier, most look clean)

    count_updated = 0
    for filename in os.listdir(directory):
        if not filename.endswith('.njk'): continue
        date_str = filename.replace('.njk', '')
        
        vimeo_url = vimeo_map.get(date_str)
        if not vimeo_url: continue
        
        filepath = os.path.join(directory, filename)
        with open(filepath, 'r') as f:
            content = f.read()
            
        # Update front matter
        new_content = re.sub(r'has_video:.*', 'has_video: true', content)
        new_content = re.sub(r'video_url:.*', f'video_url: "{vimeo_url}"', new_content)
        
        if new_content != content:
            with open(filepath, 'w') as f:
                f.write(new_content)
            count_updated += 1
            
        # Update global JSON
        for m in meetings_json:
            if m['slug'] == date_str:
                m['has_video'] = True
                break

    with open('src/_data/meetings.json', 'w') as f:
        json.dump(meetings_json, f, indent=2)

    print(f"Updated {count_updated} meetings with Vimeo links.")

if __name__ == "__main__":
    apply_vimeo_mapping()
