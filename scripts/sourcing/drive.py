import os
import re
from datetime import datetime
from googleapiclient.discovery import build

from .auth import get_credentials

FOLDER_ID = "0B42s0chw8f_lQmpaNU93ejYyWkU"

def get_drive_service():
    credentials, _ = get_credentials()
    return build('drive', 'v3', credentials=credentials)

def download_file(file_id):
    """Download a Drive file's text content."""
    import io
    from googleapiclient.http import MediaIoBaseDownload
    svc = get_drive_service()
    fh = io.BytesIO()
    dl = MediaIoBaseDownload(fh, svc.files().get_media(fileId=file_id))
    done = False
    while not done:
        _, done = dl.next_chunk()
    return fh.getvalue().decode('utf-8')

# Cached text is always extracted at this generous size regardless of what any single caller
# needs — several unrelated callers (date resolution, official-term extraction, agenda preview,
# vote/attendance extraction) share one GCS cache entry per file, and a cache entry sized for the
# least-demanding caller would silently truncate content for the others. Callers that want less
# just slice the returned text via their own `max_chars`.
CACHE_MAX_CHARS = 15000
CACHE_MAX_PAGES = 10

def read_file_text(service, file_id, max_chars=CACHE_MAX_CHARS, max_pages=CACHE_MAX_PAGES):
    """Download and extract readable text from a Drive file (PDF, Google Doc, or plain text).
    Returns None on failure, if the file is >10MB, or if extraction yields nothing."""
    import io
    try:
        meta = service.files().get(fileId=file_id, fields='mimeType,size').execute()
        mime = meta.get('mimeType', '')
        if int(meta.get('size', 0)) > 10_000_000:
            return None
        from googleapiclient.http import MediaIoBaseDownload
        fh = io.BytesIO()
        req = service.files().export_media(fileId=file_id, mimeType='text/plain') if 'google-apps.document' in mime else service.files().get_media(fileId=file_id)
        dl = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _, done = dl.next_chunk()
        if 'pdf' in mime:
            import pypdf
            fh.seek(0)
            text = '\n'.join(p.extract_text() or '' for p in pypdf.PdfReader(fh).pages[:max_pages])
        else:
            text = fh.getvalue().decode('utf-8', errors='replace')
        return text[:max_chars] if text.strip() else None
    except Exception as e:
        print(f"    Warning: could not read Drive file {file_id}: {e}")
        return None

def _text_cache_blob(bucket_uri, file_id, modified_time, slug, doc_type):
    from google.cloud import storage
    credentials, project_id = get_credentials()
    bucket_name = bucket_uri[5:]  # strip gs://
    gcs_bucket = storage.Client(credentials=credentials, project=project_id).bucket(bucket_name)
    mod_time = (modified_time or 'unknown').replace(':', '-')
    return gcs_bucket.blob(f"official_docs/{slug}/{doc_type}-{file_id}/{mod_time}.txt")

def cache_text(bucket_uri, file_id, modified_time, slug, doc_type, text):
    """Write already-extracted text to the shared GCS cache (official_docs/{slug}/{doc_type}-{file_id}/{mod_time}.txt)."""
    try:
        _text_cache_blob(bucket_uri, file_id, modified_time, slug, doc_type).upload_from_string(
            text, content_type='text/plain', timeout=60)
    except Exception as e:
        print(f"    Warning: could not cache extracted text for {file_id}: {e}")

def get_or_extract_text(bucket_uri, service, file_id, modified_time, slug, doc_type, max_chars=CACHE_MAX_CHARS):
    """Read cached extracted text for a Drive file from GCS, or extract + cache it if missing.
    `max_chars` only truncates what's *returned* to this caller — the cache itself is always
    populated at CACHE_MAX_CHARS/CACHE_MAX_PAGES so other callers can share it. Falls back to an
    uncached extraction (honoring max_chars directly) if no bucket is configured."""
    if not bucket_uri:
        return read_file_text(service, file_id, max_chars=max_chars)
    blob = _text_cache_blob(bucket_uri, file_id, modified_time, slug, doc_type)
    if blob.exists(timeout=60):
        try:
            return blob.download_as_text(timeout=60)[:max_chars]
        except Exception:
            pass
    text = read_file_text(service, file_id)
    if text:
        cache_text(bucket_uri, file_id, modified_time, slug, doc_type, text)
    return text[:max_chars] if text else None

def list_files_in_folder(service, folder_id):
    """Recursively list all files in the folder and its subfolders."""
    files = []
    page_token = None
    while True:
        query = f"'{folder_id}' in parents and trashed = false"
        response = service.files().list(
            q=query,
            spaces='drive',
            fields='nextPageToken, files(id, name, mimeType, webViewLink, modifiedTime)',
            pageToken=page_token
        ).execute()

        for file in response.get('files', []):
            if file.get('mimeType') == 'application/vnd.google-apps.folder':
                files.extend(list_files_in_folder(service, file.get('id')))
            else:
                files.append(file)

        page_token = response.get('nextPageToken', None)
        if page_token is None:
            break

    return files

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]

def parse_meeting_date(filename):
    """
    Tries to extract a date from filenames like:
    "Agenda 06.08.26", "04.29.26 Special Meeting", "Agenda June 8, 2026", "2026-06-08", or "6-8-26"
    """
    filename = filename.replace('_', ' ').replace('-', '.').replace('  ', ' ')

    # YYYYMMDD compact format (e.g. spboe_20260608)
    match = re.search(r'\b(\d{4})(\d{2})(\d{2})\b', filename)
    if match:
        y, m, d = match.groups()
        if 1 <= int(m) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{m}-{d}"

    # MM.DD.YY or MM.DD.YYYY format
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
    match = re.search(rf'({"|".join(MONTHS)})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,)?\s+(\d{{4}})', filename, re.I)
    if match:
        try:
            month_name = match.group(1).capitalize()
            day = match.group(2)
            year = match.group(3)
            dt = datetime.strptime(f"{month_name} {day} {year}", "%B %d %Y")
            return dt.strftime("%Y-%m-%d")
        except: pass

    return None

def extract_date_from_content(text):
    """
    Finds the earliest date-like pattern in free text, e.g. a scanned agenda's header date.
    Unlike parse_meeting_date, position (not pattern precedence) decides the winner: a meeting
    document's own date appears near the top of the text, ahead of any dates it merely
    references (prior minutes being approved, the next meeting being announced, etc).
    """
    if not text:
        return None

    patterns = [
        r'\b(\d{4})(\d{2})(\d{2})\b',
        r'(\d{1,2})\.(\d{1,2})\.(\d{2,4})',
        r'(\d{4})-(\d{2})-(\d{2})',
        rf'({"|".join(MONTHS)})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,)?\s+(\d{{4}})',
    ]

    best = None
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match and (best is None or match.start() < best[0]):
            best = (match.start(), pattern, match)

    if not best:
        return None
    _, pattern, match = best

    if pattern == patterns[0]:
        y, m, d = match.groups()
        if 1 <= int(m) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{m}-{d}"
        return None
    if pattern == patterns[1]:
        month, day, year = match.groups()
        if len(year) == 2:
            year = f"20{year}"
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    if pattern == patterns[2]:
        return match.group(0)
    if pattern == patterns[3]:
        try:
            dt = datetime.strptime(f"{match.group(1).capitalize()} {match.group(2)} {match.group(3)}", "%B %d %Y")
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return None
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
    label = re.sub(r'\.(pdf|vtt|docx|doc|txt)$', '', label, flags=re.I)
    label = re.sub(r'^\d{1,2}[\.\-/]\d{1,2}[\.\-/]\d{2,4}\s*', '', label)
    label = re.sub(r'\s*\d{1,2}[\.\-/]\d{1,2}[\.\-/]\d{2,4}$', '', label)
    return label.strip()

def build_meeting_map(files, bucket_uri=None, service=None, resolved_cache=None, cutoff_date=None):
    """
    Maps Drive files to meeting dates and document types.

    Resolution order: (1) date found in the file's own content, (2) date parsed from the
    filename (only if day-specific). Content is tried first because filenames for this district's
    monthly packets never carry a day (e.g. "August 2026 Board Meeting Packet.pdf") — only the
    document's own text reliably says which meeting it belongs to.

    Content resolution requires downloading and parsing the file, which doesn't scale to
    re-checking the entire historical Drive tree on every run — the shared Drive folder holds
    over a decade of material, most of it long predating any meeting this site covers.
    `resolved_cache` ({file_id: {modified_time, date_slug, doc_type}}, persisted by the caller
    across runs) lets a file whose `modifiedTime` hasn't changed since it was last resolved skip
    re-download entirely. `cutoff_date` (YYYY-MM-DD) additionally skips the download for any file
    whose `modifiedTime` predates it outright — a file modified before the cutoff era can't
    plausibly document a meeting on/after it. Filename parsing (regex only, no I/O) still runs
    regardless of cutoff; a file that stays undated after that is dropped silently if it's also
    pre-cutoff (out of scope for the site, not worth surfacing as "unmapped"), or returned in
    `unresolved` otherwise.

    When a file resolves via a fresh (uncached) content read and a GCS bucket is configured, its
    already-extracted text is cached at official_docs/{slug}/{doc_type}-{file_id}/{mod_time}.txt
    so downstream steps (post_process.py) can reuse it instead of downloading again.

    Returns (mapping: {date_slug: [{type, label, url}]}, unresolved: [{type, label, url, file_id, mime_type}],
    resolved_cache: updated cache to persist for next run).
    """
    mapping = {}
    unresolved = []
    resolved_cache = dict(resolved_cache or {})

    for f in files:
        filename = f.get('name')
        file_id = f.get('id')
        mime_type = f.get('mimeType')
        modified_time = f.get('modifiedTime')
        url = clean_url(f.get('webViewLink'))
        label = clean_label(filename)
        doc_type = categorize_document(filename)

        too_old = bool(cutoff_date and modified_time and modified_time[:10] < cutoff_date)

        date_slug = None
        resolved_via = None
        text = None

        cached = resolved_cache.get(file_id)
        if cached and cached.get('modified_time') == modified_time:
            date_slug = cached.get('date_slug')
            doc_type = cached.get('doc_type', doc_type)
            resolved_via = 'cache'
        elif service and not too_old:
            text = read_file_text(service, file_id)
            if text:
                date_slug = extract_date_from_content(text)
                if date_slug:
                    resolved_via = 'content'

        if not date_slug and resolved_via != 'cache':
            date_slug = parse_meeting_date(filename)
            if date_slug:
                resolved_via = 'filename'

        if not date_slug:
            resolved_cache.pop(file_id, None)
            if too_old:
                continue
            print(f"  Drive file has no resolvable date (deferring to site fallback): {filename}")
            unresolved.append({'type': doc_type, 'label': label, 'url': url, 'file_id': file_id, 'mime_type': mime_type})
            continue

        if resolved_via != 'cache':
            print(f"  Matched Drive file to {date_slug} via {resolved_via}: {filename}")
            resolved_cache[file_id] = {'modified_time': modified_time, 'date_slug': date_slug, 'doc_type': doc_type}

        if bucket_uri and text and modified_time:
            cache_text(bucket_uri, file_id, modified_time, date_slug, doc_type, text)

        if date_slug not in mapping:
            mapping[date_slug] = []

        if not any(clean_url(d['url']) == url for d in mapping[date_slug]):
            mapping[date_slug].append({
                'type': doc_type,
                'label': label,
                'url': url
            })

    return mapping, unresolved, resolved_cache
