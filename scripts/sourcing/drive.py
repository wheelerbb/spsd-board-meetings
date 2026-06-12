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

    # YYYYMMDD compact format (e.g. spboe_20260608)
    match = re.search(r'\b(\d{4})(\d{2})(\d{2})\b', filename)
    if match:
        y, m, d = match.groups()
        if 1 <= int(m) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{m}-{d}"

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
    if 'minutes' in name_lower: return 'minutes'
    if name_lower.endswith('.vtt') or 'transcript' in name_lower or re.match(r'spboe_\d', name_lower): return 'vtt'
    return 'misc'

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

