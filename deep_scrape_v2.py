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
                    url = a['href'].strip('"').strip('\\"').strip()
                    label = a.get_text(strip=True)
                    if not label: continue
                    
                    label_lower = label.lower()
                    
                    # Rules
                    if 'vimeo.com' in url:
                        dtype = 'video'
                    elif 'videoplayer.telvue.com' in url:
                        dtype = 'video'
                    elif 'drive.google.com' in url or 'docs.google.com' in url:
                        dtype = 'pdf'
                        if 'agenda' in label_lower: dtype = 'agenda'
                        if 'packet' in label_lower: dtype = 'packet'
                        if 'minutes' in label_lower: dtype = 'min'
                        # "Summary" remains 'pdf' (Misc)
                    else:
                        dtype = 'link'
                    
                    # Deduplicate by URL
                    if not any(d['url'] == url for d in mapping[date]):
                        mapping[date].append({'type': dtype, 'label': label, 'url': url})
                            
    return mapping

if __name__ == "__main__":
    result = extract_docs_from_tables()
    with open('master_material_map.json', 'w') as f:
        json.dump(result, f, indent=2)
    print(f"Mapped {len(result)} meetings.")
