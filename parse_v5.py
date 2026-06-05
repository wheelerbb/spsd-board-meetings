import re
import json
from datetime import datetime

def parse_raw_text():
    with open('board_data.html', 'r') as f:
        content = f.read()
    
    mapping = {}
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]

    # Date pattern
    date_pattern = rf'({"|".join(months)}) \d{{1,2}}, \d{{4}}'
    
    # Split by rows (escaped <tr>)
    rows = re.split(r'<tr>|\\u003ctr>|\\\\u003ctr>', content)
    
    for row in rows:
        # Find date in this row
        date_match = re.search(date_pattern, row)
        if not date_match: continue
        
        date_str = date_match.group(0)
        try:
            dt = datetime.strptime(date_str, "%B %d, %Y")
            iso_date = dt.strftime("%Y-%m-%d")
        except: continue
        
        if iso_date not in mapping: mapping[iso_date] = []
        
        # Find all Google Drive links in this row
        links = re.findall(r'href=[\\"]+(https://drive\.google\.com/[^\\"]+)[\\"]+[^>]*>([^<]+)</a>', row)
        
        # Also check for Vimeo links
        vimeo_links = re.findall(r'href=[\\"]+(https://vimeo\.com/[^\\"]+)[\\"]+[^>]*>([^<]+)</a>', row)
        
        for url, label in links:
            url_clean = url.replace('\\', '')
            label_clean = label.strip()
            label_lower = label_clean.lower()
            
            dtype = 'pdf'
            if 'agenda' in label_lower: dtype = 'agenda'
            if 'packet' in label_lower: dtype = 'packet'
            if 'minutes' in label_lower or 'summary' in label_lower: dtype = 'min'
            
            if any(d['url'] == url_clean for d in mapping[iso_date]): continue
            
            mapping[iso_date].append({
                'type': dtype,
                'label': label_clean,
                'url': url_clean
            })
            
        for url, label in vimeo_links:
            url_clean = url.replace('\\', '')
            if any(d['url'] == url_clean for d in mapping[iso_date]): continue
            mapping[iso_date].append({
                'type': 'video',
                'label': label.strip(),
                'url': url_clean
            })

    return mapping

if __name__ == "__main__":
    result = parse_raw_text()
    with open('material_map_iso.json', 'w') as f:
        json.dump(result, f, indent=2)
    print(f"Mapped {len(result)} meetings.")
