import requests
import json
import re
from datetime import datetime
from bs4 import BeautifulSoup

SITE_URL = "https://www.spsdme.org/page/school-board"

def fetch_site_data():
    """Fetches the school board page and extracts meeting data using a robust heuristic."""
    print(f"Fetching SPSD site data from {SITE_URL}...")
    try:
        response = requests.get(SITE_URL, timeout=15)
        response.raise_for_status()
        html = response.text
        
        # Extract the JSON blob
        match = re.search(r'window\.clientWorkStateTemp\s*=\s*JSON\.parse\("(.*?)"\);', html)
        if not match:
            print("Could not find clientWorkStateTemp in HTML.")
            content = html
        else:
            # Decode unicode escapes and also common Apptegy-specific escaping
            content = match.group(1).encode().decode('unicode_escape')
            content = content.replace('\\"', '"')
        
        results = {}

        # Heuristic: Find all Drive links and look for a date nearby
        date_patterns = [
            r'(\d{4})-(\d{2})-(\d{2})', # YYYY-MM-DD
            r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,)?\s+(\d{4})', # Month DD, YYYY
            r'(\d{1,2})[\.\-/](\d{1,2})[\.\-/](\d{2,4})' # MM.DD.YY
        ]
        
        # Try to find links with labels first via regex
        # This handles both literal HTML and the escaped version in the JSON
        link_regex = r'<a\s+[^>]*href="(https?://drive\.google\.com/[^"]*)"[^>]*>(.*?)</a>'
        matches = re.finditer(link_regex, content, re.I | re.S)
        
        # Also find raw links just in case
        raw_links = re.finditer(r'(https?://drive\.google\.com/[^\s"\\<>]+)', content)
        
        # Combine them: use the ones with labels if possible
        all_potential_links = []
        seen_urls = set()
        
        for m in matches:
            url = m.group(1).split('\\')[0] # Remove any trailing escapes
            label = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            all_potential_links.append({'url': url, 'label': label, 'pos': m.start()})
            seen_urls.add(url)
            
        for m in raw_links:
            url = m.group(1).split('\\')[0]
            if url not in seen_urls:
                all_potential_links.append({'url': url, 'label': "Document", 'pos': m.start()})
                seen_urls.add(url)

        for item in all_potential_links:
            url = item['url']
            label = item['label']
            pos = item['pos']
            
            # Look at the window around the link
            start_pos = max(0, pos - 400)
            window = content[start_pos:pos + 100]
            
            # Clean window for date parsing
            window_clean = re.sub(r'<[^>]+>', ' ', window)
            window_clean = window_clean.replace('\\u003c', '<').replace('\\u003e', '>')
            window_clean = re.sub(r'<[^>]+>', ' ', window_clean)
            
            found_date = None
            for pattern in date_patterns:
                date_match = re.search(pattern, window_clean, re.I)
                if date_match:
                    if pattern == date_patterns[0]:
                        found_date = date_match.group(0)
                    elif pattern == date_patterns[1]:
                        try:
                            dt = datetime.strptime(f"{date_match.group(1).capitalize()} {date_match.group(2)} {date_match.group(3)}", "%B %d %Y")
                            found_date = dt.strftime("%Y-%m-%d")
                        except: pass
                    elif pattern == date_patterns[2]:
                        m, d, y = date_match.groups()
                        if len(y) == 2: y = f"20{y}"
                        found_date = f"{y}-{m.zfill(2)}-{d.zfill(2)}"
                if found_date: break
            
            if found_date:
                if found_date not in results:
                    results[found_date] = {
                        'date': found_date,
                        'type': "Regular",
                        'docs': []
                    }
                
                # Identify doc type
                doc_type = "misc"
                # Use label first, then window
                search_text = (label + " " + window_clean).lower()
                label_lower = label.lower()
                if "agenda" in search_text: doc_type = "agenda"
                elif "minutes" in search_text or "min" in search_text: doc_type = "minutes"
                elif "packet" in search_text: doc_type = "packet"
                elif label_lower.endswith('.vtt') or 'transcript' in label_lower or re.match(r'spboe_\d', label_lower): doc_type = "transcript"
                
                # Deduplicate by URL
                if not any(d['url'] == url for d in results[found_date]['docs']):
                    results[found_date]['docs'].append({
                        'label': label if label and len(label) > 1 else "Document",
                        'url': url,
                        'type': doc_type
                    })
        
        print(f"Extracted {len(results)} meetings from site.")
        return results

    except Exception as e:
        print(f"Error fetching site data: {e}")
        return {}

if __name__ == "__main__":
    data = fetch_site_data()
    sorted_keys = sorted(data.keys(), reverse=True)
    for k in sorted_keys[:10]:
        print(f"{k}: {data[k]}")
