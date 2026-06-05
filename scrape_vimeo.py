import re
import requests
import json
import time

def scrape_vimeo_channel(pages=10):
    all_videos = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    for page in range(1, pages + 1):
        print(f"Scraping Vimeo page {page}...")
        url = f"https://vimeo.com/spctv/videos/page:{page}"
        try:
            r = requests.get(url, headers=headers)
            if r.status_code != 200:
                print(f"Failed to fetch page {page}: {r.status_code}")
                break
            
            # Pattern: <li id="clip_(\d+)" ... title="([^"]+)"
            matches = re.findall(r'id="clip_(\d+)"[^>]*>.*?title="([^"]+)"', r.text, re.DOTALL)
            if not matches:
                print(f"No more videos found on page {page}.")
                break
                
            for vid_id, title in matches:
                # Clean up title (Vimeo sometimes has extra whitespace or newlines)
                clean_title = " ".join(title.split())
                if "Board of Education" in clean_title or "SPBoE" in clean_title or "School Board" in clean_title:
                    all_videos[vid_id] = clean_title
            
            time.sleep(1) # Be nice
        except Exception as e:
            print(f"Error on page {page}: {e}")
            break
            
    return all_videos

if __name__ == "__main__":
    videos = scrape_vimeo_channel(50) # Scan up to 50 pages to go back several years
    with open('vimeo_master_list.json', 'w') as f:
        json.dump(videos, f, indent=2)
    print(f"Captured {len(videos)} board meeting videos.")
