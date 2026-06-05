import re
import json
import os
from datetime import datetime
from bs4 import BeautifulSoup

def parse_date(text):
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    # Look for "Month Day, Year"
    match = re.search(rf'({"|".join(months)}) \d{{1,2}}, \d{{4}}', text)
    if match:
        try:
            dt = datetime.strptime(match.group(0), "%B %d, %Y")
            return dt.strftime("%Y-%m-%d")
        except:
            return None
    return None

def extract_docs_from_tables():
    if not os.path.exists('board_data.html'):
        return {}
        
    with open('board_data.html', 'r') as f:
        content = f.read()
    
    mapping = {}
    
    # Extract all HTML snippets from the JSON
    # The file contains escaped HTML in "html" fields
    html_snippets = re.findall(r'"html":"(.*?)(?<!\\)"', content, re.DOTALL)
    
    for snippet in html_snippets:
        # Unescape snippet
        snippet = snippet.replace('\\"', '"').replace('\\n', '\n').replace('\\u003c', '<').replace('\\u003e', '>').replace('\\/', '/')
        
        soup = BeautifulSoup(snippet, 'html.parser')
        tables = soup.find_all('table')
        
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) < 2: continue
                
                # Check first cell for a date
                date_text = cells[0].get_text(separator=' ', strip=True)
                date = parse_date(date_text)
                if not date: continue
                
                if date not in mapping: mapping[date] = []
                
                # Process all links in the row
                links = row.find_all('a', href=True)
                for a in links:
                    url = a['href']
                    label = a.get_text(strip=True)
                    if not label: continue
                    
                    label_lower = label.lower()
                    
                    # Rules:
                    # - Video column is usually column 3, but let's check URL
                    if 'vimeo.com' in url:
                        # Video type
                        if not any(d['url'] == url for d in mapping[date]):
                            mapping[date].append({'type': 'video', 'label': label, 'url': url})
                    elif 'drive.google.com' in url or 'docs.google.com' in url:
                        dtype = 'pdf'
                        # Standard categorizations
                        if 'agenda' in label_lower: dtype = 'agenda'
                        if 'packet' in label_lower: dtype = 'packet'
                        if 'minutes' in label_lower: dtype = 'min' # Only "Minutes" in minutes column
                        # "Summary", "Slides", etc. stay 'pdf' (Misc)
                        
                        if not any(d['url'] == url for d in mapping[date]):
                            mapping[date].append({'type': dtype, 'label': label, 'url': url})
                            
    return mapping

if __name__ == "__main__":
    result = extract_docs_from_tables()
    # Save the master map
    with open('master_material_map.json', 'w') as f:
        json.dump(result, f, indent=2)
    print(f"Mapped {len(result)} meetings.")
    # Verify May 11
    if "2026-05-11" in result:
        print(f"May 11: {len(result['2026-05-11'])} docs found.")
    else:
        print("May 11 NOT found in scrape.")
