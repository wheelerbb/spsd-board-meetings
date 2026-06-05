import json
import re
import os
from datetime import datetime

def parse_date(text):
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    match = re.search(rf'({"|".join(months)}) \d{{1,2}}, \d{{4}}', text)
    if match:
        try:
            dt = datetime.strptime(match.group(0), "%B %d, %Y")
            return dt.strftime("%Y-%m-%d")
        except:
            return None
    return None

def extract_from_html():
    if not os.path.exists('board_data.html'):
        return {}
    with open('board_data.html', 'r') as f:
        content = f.read()
    
    mapping = {}
    # Split by rows
    rows = re.split(r'<tr>|\\u003ctr>|\\\\u003ctr>', content)
    for row in rows:
        date = parse_date(row)
        if not date: continue
        
        if date not in mapping: mapping[date] = []
        
        # Extract all links
        links = re.findall(r'href=[\\"]+(https?://(?:drive\.google\.com|vimeo\.com|videoplayer\.telvue\.com)[^\\"]+)[\\"]+[^>]*>([^<]+)</a>', row)
        for url, label in links:
            url_clean = url.replace('\\', '')
            label_clean = label.strip()
            
            # Apply user rules
            if 'vimeo.com' in url_clean:
                mapping[date].append({'type': 'video', 'label': label_clean, 'url': url_clean})
            elif 'videoplayer.telvue.com' in url_clean:
                # Only add TelVue if we don't have a Vimeo link for this date yet
                if not any(d['type'] == 'video' and 'vimeo.com' in d['url'] for d in mapping[date]):
                    mapping[date].append({'type': 'video', 'label': label_clean, 'url': url_clean})
            elif 'drive.google.com' in url_clean:
                label_lower = label_clean.lower()
                dtype = 'pdf'
                if 'agenda' in label_lower: dtype = 'agenda'
                if 'packet' in label_lower: dtype = 'packet'
                if 'minutes' in label_lower: dtype = 'min' # ONLY if "minutes" is in label
                # "Summary" remains 'pdf' (Misc)
                
                # Deduplicate by URL
                if not any(d['url'] == url_clean for d in mapping[date]):
                    mapping[date].append({'type': dtype, 'label': label_clean, 'url': url_clean})
    return mapping

def extract_from_articles(mapping):
    if not os.path.exists('articles.json'):
        return mapping
    with open('articles.json', 'r') as f:
        data = json.load(f)
        
    for art in data.get('articles', []):
        content = art.get('content', '')
        # Try to find a date in the content or title
        date = parse_date(content) or parse_date(art.get('title', ''))
        if not date: continue
        
        if date not in mapping: mapping[date] = []
        
        # Find Drive or Vimeo links
        links = re.findall(r'href="(https?://(?:drive\.google\.com|vimeo\.com)[^"]+)"[^>]*>([^<]+)</a>', content)
        for url, label in links:
            url_clean = url.replace('\\', '')
            label_clean = label.strip()
            
            if 'vimeo.com' in url_clean:
                # Add if not present
                if not any(d['url'] == url_clean for d in mapping[date]):
                    mapping[date].append({'type': 'video', 'label': label_clean, 'url': url_clean})
            elif 'drive.google.com' in url_clean:
                label_lower = label_clean.lower()
                dtype = 'pdf'
                if 'agenda' in label_lower: dtype = 'agenda'
                if 'packet' in label_lower: dtype = 'packet'
                if 'minutes' in label_lower: dtype = 'min'
                
                if not any(d['url'] == url_clean for d in mapping[date]):
                    mapping[date].append({'type': dtype, 'label': label_clean, 'url': url_clean})
    return mapping

if __name__ == "__main__":
    mapping = extract_from_html()
    mapping = extract_from_articles(mapping)
    
    # Final cleanup: Remove TelVue if Vimeo is present
    for date in mapping:
        has_vimeo = any(d['type'] == 'video' and 'vimeo.com' in d['url'] for d in mapping[date])
        if has_vimeo:
            mapping[date] = [d for d in mapping[date] if 'videoplayer.telvue.com' not in d['url']]

    with open('master_material_map.json', 'w') as f:
        json.dump(mapping, f, indent=2)
    print(f"Mapped {len(mapping)} dates.")
