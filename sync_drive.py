import os
import json
import re
import yaml
from datetime import datetime
from googleapiclient.discovery import build
from google.oauth2 import service_account
from dotenv import load_dotenv

load_dotenv()

# The specific Drive folder provided
FOLDER_ID = "0B42s0chw8f_lQmpaNU93ejYyWkU"

# Do not create stubs for meetings before the 2023-2024 school year
CUTOFF_DATE = "2023-08-01"

def get_drive_service():
    """
    Since the folder is public, we can use an API key. 
    If a service account is available, we use that.
    """
    api_key = os.getenv("GOOGLE_DRIVE_API_KEY")
    if api_key:
        print("Using API Key for Google Drive...")
        return build('drive', 'v3', developerKey=api_key)
    
    print("Using default Google Auth (ADC) for Google Drive...")
    import google.auth
    try:
        credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/drive.metadata.readonly', 'https://www.googleapis.com/auth/drive.readonly'])
        return build('drive', 'v3', credentials=credentials)
    except Exception as e:
        print(f"Auth failed: {e}")
        return None

def list_files_in_folder(service, folder_id):
    """Recursively list all files in the folder and its subfolders."""
    files = []
    page_token = None
    while True:
        query = f"'{folder_id}' in parents and trashed = false"
        response = service.files().list(
            q=query,
            spaces='drive',
            fields='nextPageToken, files(id, name, mimeType, webViewLink)',
            pageToken=page_token
        ).execute()
        
        for file in response.get('files', []):
            if file.get('mimeType') == 'application/vnd.google-apps.folder':
                # Recursively search subfolders
                files.extend(list_files_in_folder(service, file.get('id')))
            else:
                files.append(file)
                
        page_token = response.get('nextPageToken', None)
        if page_token is None:
            break
            
    return files

def parse_meeting_date(filename):
    """
    Tries to extract a date from filenames like:
    "Agenda 06.08.26", "04.29.26 Special Meeting", "Agenda June 8, 2026", "2026-06-08", or "6-8-26"
    """
    # Clean up the filename
    filename = filename.replace('_', ' ').replace('-', '.').replace('  ', ' ')

    # MM.DD.YY or MM.DD.YYYY format (now handles . or - or / because of replace above)
    match = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{2,4})', filename)
    if match:
        month, day, year = match.groups()
        if len(year) == 2:
            year = f"20{year}"
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    
    # YYYY.MM.DD format
    match = re.search(r'(\d{4})\.(\d{2})\.(\d{2})', filename)
    if match:
        return match.group(0).replace('.', '-')
    
    # Month Day, Year format
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    match = re.search(rf'({"|".join(months)})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,)?\s+(\d{{4}})', filename, re.I)
    if match:
        try:
            month_name = match.group(1).capitalize()
            day = match.group(2)
            year = match.group(3)
            dt = datetime.strptime(f"{month_name} {day} {year}", "%B %d %Y")
            return dt.strftime("%Y-%m-%d")
        except: pass
    
    return None

def categorize_document(filename):
    """Categorizes the document based on its name."""
    name_lower = filename.lower()
    if 'agenda' in name_lower: return 'agenda'
    if 'packet' in name_lower: return 'packet'
    if 'minute' in name_lower: return 'min'
    return 'pdf'

def clean_url(url):
    """Removes tracking parameters from Google Drive URLs."""
    if not url: return url
    return re.sub(r'[?&]usp=[^&]+', '', url)

def clean_label(label):
    """Cleans up document labels by removing leading/trailing dates and common extensions."""
    if not label: return label
    # Remove common extensions
    label = re.sub(r'\.(pdf|vtt|docx|doc|txt)$', '', label, flags=re.I)
    # Remove leading date like "6.4.26 "
    label = re.sub(r'^\d{1,2}[\.\-/]\d{1,2}[\.\-/]\d{2,4}\s*', '', label)
    # Remove trailing date like " 6.4.26"
    label = re.sub(r'\s*\d{1,2}[\.\-/]\d{1,2}[\.\-/]\d{2,4}$', '', label)
    return label.strip()

def build_meeting_map(files):
    mapping = {}
    for f in files:
        date_slug = parse_meeting_date(f.get('name'))
        if not date_slug: continue
        
        if date_slug not in mapping:
            mapping[date_slug] = []
            
        doc_type = categorize_document(f.get('name'))
        url = clean_url(f.get('webViewLink'))
        label = clean_label(f.get('name'))
        
        if not any(clean_url(d['url']) == url for d in mapping[date_slug]):
            mapping[date_slug].append({
                'type': doc_type,
                'label': label,
                'url': url
            })
            
    return mapping

def generate_stubs(mapping):
    meeting_dir = 'src/meetings/'
    if not os.path.exists(meeting_dir): os.makedirs(meeting_dir)
    
    meetings_json_path = 'src/_data/meetings.json'
    global_json = []
    if os.path.exists(meetings_json_path):
        with open(meetings_json_path, 'r') as f:
            global_json = json.load(f)
            
    existing_slugs = [m['slug'] for m in global_json]
    changes_made = 0

    for date_slug, new_docs in mapping.items():
        if date_slug < CUTOFF_DATE:
            continue
        njk_path = os.path.join(meeting_dir, f"{date_slug}.njk")
        
        if os.path.exists(njk_path):
            with open(njk_path, 'r') as f: content = f.read()
            # More robust front matter extraction
            parts = re.split(r'^---+\s*$', content, flags=re.MULTILINE)
            if len(parts) >= 3:
                fm_text = parts[1]
                body = "---".join(parts[2:])
                
                try:
                    data = yaml.safe_load(fm_text) or {}
                except Exception as e:
                    print(f"Error parsing YAML in {njk_path}: {e}")
                    continue
                
                existing_docs = data.get('docs', [])
                if not isinstance(existing_docs, list):
                    existing_docs = []
                
                # Deduplicate and clean existing docs
                seen_urls = set()
                unique_docs = []
                for d in existing_docs:
                    url = clean_url(d.get('url'))
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        d['label'] = clean_label(d.get('label', ''))
                        unique_docs.append(d)
                
                existing_docs = unique_docs
                existing_urls = seen_urls
                
                # Merge new docs that don't already exist
                added_any = False
                for nd in new_docs:
                    if clean_url(nd['url']) not in existing_urls:
                        existing_docs.append(nd)
                        added_any = True
                
                if added_any or len(unique_docs) < len(data.get('docs', [])):
                    print(f"Merged/Cleaned docs for meeting: {date_slug}")
                    data['docs'] = existing_docs
                    # Use a custom Dumper to avoid some escaping if possible, 
                    # but safe_dump is generally fine.
                    fm_yaml = yaml.dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)
                    with open(njk_path, 'w') as f:
                        f.write(f"---\n{fm_yaml}---\n{body}")
                    
                    # Update global json doc count
                    found_in_json = False
                    for g in global_json:
                        if g['slug'] == date_slug:
                            g['doc_count'] = len([d for d in existing_docs if d.get('type') != 'video'])
                            changes_made += 1
                            found_in_json = True
                            break
                    
                    if not found_in_json:
                        # If it's in NJK but not in JSON, we'll add it to JSON in the next block
                        # (or just assume it's already there if existing_slugs is accurate)
                        pass
            continue

            
        print(f"Creating new meeting stub: {date_slug}...")
        changes_made += 1
        
        dt = datetime.strptime(date_slug, "%Y-%m-%d")
        year = dt.year
        if dt.month >= 7: school_year = f"{year}-{year+1}"
        else: school_year = f"{year-1}-{year}"
        
        display_date = dt.strftime("%B %d, %Y").replace(" 0", " ")
        day_of_week = dt.strftime("%A")
        
        mtype = "Regular"
        title = f"{dt.strftime('%B')} Regular Meeting"
        agenda_doc = next((d for d in new_docs if d['type'] == 'agenda'), None)
        if agenda_doc:
            if "special" in agenda_doc['label'].lower():
                mtype = "Special"
                title = "Special Meeting"
            elif "workshop" in agenda_doc['label'].lower():
                mtype = "Workshop"
                title = "Board Workshop"
        
        front_matter = {
            "layout": "layouts/meeting.njk",
            "title": f"{display_date} — School Board Meeting — SPSD",
            "heading": title,
            "breadcrumb": dt.strftime("%b %d, %Y").replace(" 0", " "),
            "display_date": display_date,
            "day_of_week": day_of_week,
            "meeting_tag": f"{mtype} Meeting · {dt.strftime('%B %Y')}",
            "time": "6:00 PM",
            "location": "South Portland High School Lecture Hall",
            "has_video": False,
            "video_url": "",
            "has_vtt_source": False,
            "has_transcript": False,
            "stub": True,
            "board_attendance": [],
            "docs": new_docs
        }
        
        fm_yaml = yaml.dump(front_matter, sort_keys=False, default_flow_style=False)
        with open(njk_path, 'w') as f:
            f.write(f"---\n{fm_yaml}---\n")

        global_json.append({
            "slug": date_slug,
            "school_year": school_year,
            "date": date_slug,
            "display_date": display_date,
            "day_of_week": day_of_week,
            "type": mtype,
            "title": title,
            "topics": [],
            "doc_count": len([d for d in new_docs if d['type'] != 'video']),
            "has_video": False,
            "has_transcript": False,
            "stub": True,
            "blurb": ""
        })

    if changes_made > 0:
        global_json.sort(key=lambda x: x['date'], reverse=True)
        with open(meetings_json_path, 'w') as f:
            json.dump(global_json, f, indent=2)
        print(f"Updated {changes_made} meetings in the archive.")
    else:
        print("No new updates found.")

def main():
    service = get_drive_service()
    if not service: return
    print("Fetching files from Google Drive...")
    try:
        files = list_files_in_folder(service, FOLDER_ID)
        print(f"Found {len(files)} total files.")
        
        mapping = build_meeting_map(files)
        with open('master_material_map.json', 'w') as f:
            json.dump(mapping, f, indent=2)
            
        generate_stubs(mapping)
    except Exception as e:
        print(f"Error accessing Drive: {e}")

if __name__ == "__main__":
    main()
