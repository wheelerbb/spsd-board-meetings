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

def _bucket(bucket_uri):
    from google.cloud import storage
    credentials, project_id = get_credentials()
    bucket_name = bucket_uri[5:]  # strip gs://
    return storage.Client(credentials=credentials, project=project_id).bucket(bucket_name)

def _is_gsuite_mime(mime_type):
    return bool(mime_type) and mime_type.startswith('application/vnd.google-apps.')

def _blob_name(file_id, entry, ext):
    """official_docs/{created_date}_{doc_type}_{file_id}_{modified_stamp}.{ext} — flat, no
    subfolders. `doc_type` comes from categorize_document()'s deterministic filename-keyword
    match, not from date resolution, so it's safe to bake into the name; the resolved *meeting*
    date deliberately never is (see extract_date_from_content's OCR-misread false positives).
    `modified_stamp` is YYYYMMDD_HHMM for a normal upload (rare edits -> effectively full
    history), or YYYYMMDD for a Google-native file (Docs/Sheets/Slides get edited far more often —
    date-only precision caps accumulation at one blob per day)."""
    created = (entry.get('created_time') or 'unknown')[:10].replace('-', '')
    modified = entry.get('modified_time') or 'unknown'
    if _is_gsuite_mime(entry.get('mime_type')):
        mod_stamp = modified[:10].replace('-', '')
    else:
        mod_stamp = modified[:16].replace('-', '').replace(':', '').replace('T', '_')
    doc_type = entry.get('doc_type') or 'doc'
    return f"official_docs/{created}_{doc_type}_{file_id}_{mod_stamp}.{ext}"

def read_cached_blob(bucket_uri, blob_name, max_chars=None):
    """Read a specific cached blob by name. Returns None if missing, unset, or unreadable."""
    if not bucket_uri or not blob_name:
        return None
    try:
        blob = _bucket(bucket_uri).blob(blob_name)
        if not blob.exists(timeout=60):
            return None
        text = blob.download_as_text(timeout=60)
        return text[:max_chars] if max_chars else text
    except Exception:
        return None

def write_blob(bucket_uri, blob_name, text, content_type='text/plain'):
    """Write text to a specific GCS blob name — for derived/secondary artifacts (e.g. an isolated
    agenda excerpt from a packet) whose name isn't computed via `_blob_name`. Returns True on
    success."""
    if not bucket_uri:
        return False
    try:
        _bucket(bucket_uri).blob(blob_name).upload_from_string(text, content_type=content_type, timeout=60)
        return True
    except Exception as e:
        print(f"    Warning: could not write cache blob {blob_name}: {e}")
        return False

def cache_text(bucket_uri, file_id, entry, text):
    """Write already-extracted text to the shared GCS cache. Returns the blob name written, or
    None if the write failed."""
    name = _blob_name(file_id, entry, 'txt')
    return name if write_blob(bucket_uri, name, text, content_type='text/plain') else None

def cache_terms(bucket_uri, file_id, entry, terms_json_text):
    """Write extracted proper-noun terms (JSON) to the shared GCS cache. Returns the blob name
    written, or None if the write failed."""
    name = _blob_name(file_id, entry, 'json')
    return name if write_blob(bucket_uri, name, terms_json_text, content_type='application/json') else None

def load_catalog(bucket_uri):
    """Load the Drive file catalog (file_id -> metadata + cache pointers) from GCS.
    Returns {} if no bucket is configured or nothing's been cached yet."""
    if not bucket_uri:
        return {}
    import json
    blob = _bucket(bucket_uri).blob('drive_catalog.json')
    if not blob.exists(timeout=60):
        return {}
    try:
        return json.loads(blob.download_as_text(timeout=60))
    except Exception as e:
        print(f"  Warning: could not load Drive catalog: {e}")
        return {}

def save_catalog(bucket_uri, catalog):
    if not bucket_uri:
        return
    import json
    try:
        _bucket(bucket_uri).blob('drive_catalog.json').upload_from_string(
            json.dumps(catalog, indent=2), content_type='application/json', timeout=60)
    except Exception as e:
        print(f"  Warning: could not save Drive catalog: {e}")

def get_or_extract_text(bucket_uri, service, catalog, file_id, max_chars=CACHE_MAX_CHARS):
    """Read cached extracted text for a Drive file via the catalog, or extract + cache it if
    missing/stale. Requires `catalog[file_id]` to already carry doc_type/mime_type/created_time
    (set at resolution time by build_meeting_map, or filled in by the caller for files it
    discovered some other way). `max_chars` only truncates what's *returned* — the cache itself
    is always populated at CACHE_MAX_CHARS/CACHE_MAX_PAGES so other callers sharing this entry
    aren't starved.

    Returns (text, updates): `updates` is None on a cache hit (nothing changed) or a dict of
    fields (`modified_time`, `text_blob`) for the caller to merge into `catalog[file_id]` after a
    fresh extraction — this function reads the catalog but never mutates it itself."""
    entry = catalog.get(file_id) or {}
    if not bucket_uri or not service:
        text = read_file_text(service, file_id, max_chars=max_chars) if service else None
        return text, None

    try:
        current_modified = service.files().get(fileId=file_id, fields='modifiedTime').execute().get('modifiedTime')
    except Exception as e:
        print(f"    Warning: could not check Drive metadata for {file_id}: {e}")
        current_modified = entry.get('modified_time')

    if entry.get('text_blob') and entry.get('modified_time') == current_modified:
        cached = read_cached_blob(bucket_uri, entry['text_blob'], max_chars=max_chars)
        if cached is not None:
            return cached, None

    text = read_file_text(service, file_id)
    if not text:
        return None, None
    blob_name = cache_text(bucket_uri, file_id, {**entry, 'modified_time': current_modified}, text)
    return text[:max_chars], {'modified_time': current_modified, 'text_blob': blob_name}

def list_files_in_folder(service, folder_id):
    """Recursively list all files in the folder and its subfolders."""
    files = []
    page_token = None
    while True:
        query = f"'{folder_id}' in parents and trashed = false"
        response = service.files().list(
            q=query,
            spaces='drive',
            fields='nextPageToken, files(id, name, mimeType, webViewLink, modifiedTime, createdTime)',
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

# A meeting document's own date lives in its header, near the very top — real-world board
# agendas/packets/minutes in this archive never place it later than ~250 characters in (checked
# against every cached document in the archive). Everything past this point is body text, where
# a document routinely references OTHER meeting dates (prior minutes being approved, the next
# meeting being announced) that must never be mistaken for the document's own date. Cutting the
# search off here is a second, independent line of defense on top of "earliest match wins" below.
_HEADER_SCAN_CHARS = 600

# PDF text extraction frequently drops the space between adjacent words (e.g. "Regular
# MeetingonMonday, March11, 2024" — verified directly against this archive's cached documents),
# so every literal-word gap here is `\s*` (optional), not `\s+`. Only the month/day-to-year gap
# stays `\s+`, since a genuinely missing space there would glue the year onto the day.
def _date_pattern(months_re):
    return rf'({months_re})\s*(\d{{1,2}})(?:st|nd|rd|th)?(?:,)?\s+(\d{{4}})'

_MONTHS_RE = '|'.join(MONTHS)

# Minutes' standard preamble states the document's own meeting date, immediately followed by the
# date of a FUTURE meeting where the minutes will be formally approved (e.g. "...at its Regular
# Meeting on Monday, March 11, 2024. ... approved at the next Regular Meeting on April 8, 2024.").
# Matching this phrase explicitly picks the document's own date by what it verifiably *is*, not
# by incidentally being first in the text — the strongest signal available, checked before the
# generic fallback.
_MINUTES_OWN_DATE = re.compile(rf'Regular\s*Meeting\s*on\s*\w+,?\s*{_date_pattern(_MONTHS_RE)}', re.I)
_GENERIC_MONTH_DATE = re.compile(_date_pattern(_MONTHS_RE), re.I)


def _month_date_to_iso(match):
    try:
        dt = datetime.strptime(f"{match.group(1).capitalize()} {match.group(2)} {match.group(3)}", "%B %d %Y")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def extract_date_from_content(text):
    """
    Finds a meeting document's own date from its free text — e.g. a scanned agenda's header date.
    Restricted to the document's header (see _HEADER_SCAN_CHARS) so it can never latch onto a
    date the document merely references in body text (prior minutes being approved, the next
    meeting being announced, etc). Within that header, a minutes document's own standard preamble
    (see _MINUTES_OWN_DATE) is checked first as the strongest available signal; otherwise, of
    every date-like pattern found, position (not pattern precedence) decides the winner — a
    document's own date leads, ahead of anything else that made it into the header.
    """
    if not text:
        return None

    header = text[:_HEADER_SCAN_CHARS]

    m = _MINUTES_OWN_DATE.search(header)
    if m:
        iso = _month_date_to_iso(m)
        if iso:
            return iso

    patterns = [
        r'\b(\d{4})(\d{2})(\d{2})\b',
        r'(\d{1,2})\.(\d{1,2})\.(\d{2,4})',
        r'(\d{4})-(\d{2})-(\d{2})',
        _GENERIC_MONTH_DATE.pattern,
    ]

    best = None
    for pattern in patterns:
        match = re.search(pattern, header, re.I)
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
        return _month_date_to_iso(match)
    return None

def categorize_document(filename):
    """Categorizes the document based on its name."""
    name_lower = filename.lower()
    if 'agenda' in name_lower: return 'agenda'
    if 'packet' in name_lower: return 'packet'
    if 'minutes' in name_lower: return 'minutes'
    if name_lower.endswith('.vtt') or 'transcript' in name_lower or re.match(r'spboe_\d', name_lower): return 'transcript'
    return 'misc'

# Native Google Docs/Sheets/Slides have no filename extension, so their file type can only come
# from mime_type. Everything else (uploaded PDFs, Office files, etc.) keeps its extension on
# Drive, so the filename is checked first below.
_GOOGLE_NATIVE_FILE_TYPES = {
    'application/vnd.google-apps.document': 'DOC',
    'application/vnd.google-apps.spreadsheet': 'XLSX',
    'application/vnd.google-apps.presentation': 'PPTX',
}

def file_type_label(filename, mime_type=None):
    """The short file-format label shown as a document's icon (PDF, DOCX, PPTX, ...) — distinct
    from categorize_document()'s doc_type, which is the document's *role* in the meeting, not its
    format. Derived from the filename's extension since Drive preserves it on upload."""
    if filename and '.' in filename:
        ext = filename.rsplit('.', 1)[-1].upper()
        if ext.isalnum() and len(ext) <= 5:
            return ext
    return _GOOGLE_NATIVE_FILE_TYPES.get(mime_type, 'FILE')

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

def build_meeting_map(files, catalog, bucket_uri=None, service=None, cutoff_date=None):
    """
    Resolves each Drive file to a meeting date and document type, updating `catalog` in place —
    file_id -> {label, url, mime_type, doc_type, file_type, created_time, modified_time, meeting_slug,
    resolved_via, text_blob}. `meeting_slug`/`resolved_via` are `None` for a file nothing could
    date; the caller (source_data.py) attempts a website-based fallback afterward and updates
    those same catalog entries directly rather than tracking a separate unresolved list.

    Two independent concerns, easy to conflate but kept strictly separate:

    - **Sourcing scope** (`cutoff_date`): should this file be processed at all? A file is in scope
      if `created_time` OR `modified_time` is on/after `cutoff_date` — OR, not AND, so an evergreen
      policy doc created long before cutoff but revised after it stays in scope for its revision.
      A file untouched on *both* axes since before cutoff is skipped entirely — no catalog entry,
      no filename parsing, no log line — since the shared Drive folder holds well over a decade of
      material, most of it (going back to the 1960s in one observed case) never touched again after
      upload. This is purely about volume/relevance, not about what date the file's content is for.
    - **Content-date eligibility** (`doc_type`): once a file is in scope, is a "meeting date" even
      a meaningful property of its *content*? Only for agenda/packet/minutes — a policy draft or
      donation letter has none, and policy documents in particular carry misleading historical
      dates (an "Adopted: 1975 / Revised: 2001" citation block reads as a false-positive meeting
      date to extract_date_from_content — confirmed from a real run: recently-revised policy drafts
      were resolving to their citation years). Everything else falls straight to filename parsing.

    Resolution order for an in-scope, content-eligible file: (1) date found in its own content —
    tried first because filenames for this district's monthly packets never carry a day (e.g.
    "August 2026 Board Meeting Packet.pdf"), only the document's own text reliably says which
    meeting it belongs to; (2) date parsed from the filename (only if day-specific).

    Content resolution requires downloading and parsing the file — a catalog entry whose
    `modified_time` matches the file's current `modifiedTime` is trusted as-is and resolution is
    skipped entirely for it (this also means a file that resolves to no date stays that way until
    it's actually edited, rather than being re-checked every run forever).

    When a file resolves via a fresh content read and a GCS bucket is configured, its
    already-extracted text is cached (`cache_text`) and the catalog's `text_blob` is updated so
    downstream steps (post_process.py, process_transcripts.py) can reuse it.

    Returns `catalog` (same dict, mutated in place, returned for convenience).
    """
    for f in files:
        filename = f.get('name')
        file_id = f.get('id')
        mime_type = f.get('mimeType')
        modified_time = f.get('modifiedTime')
        created_time = f.get('createdTime')

        if cutoff_date:
            created_recent = bool(created_time) and created_time[:10] >= cutoff_date
            modified_recent = bool(modified_time) and modified_time[:10] >= cutoff_date
            if not created_recent and not modified_recent:
                continue  # untouched on both axes since before cutoff — out of scope entirely

        url = clean_url(f.get('webViewLink'))
        label = clean_label(filename)
        doc_type = categorize_document(filename)
        file_type = file_type_label(filename, mime_type)

        entry = catalog.setdefault(file_id, {})
        entry.update({'label': label, 'url': url, 'mime_type': mime_type,
                      'created_time': created_time, 'doc_type': doc_type, 'file_type': file_type})

        if entry.get('modified_time') == modified_time and 'meeting_slug' in entry:
            continue  # already resolved for this exact version — nothing to redo

        date_slug = None
        resolved_via = None
        text = None

        if service and doc_type in ('agenda', 'packet', 'minutes'):
            text = read_file_text(service, file_id)
            if text:
                date_slug = extract_date_from_content(text)
                if date_slug:
                    resolved_via = 'content'

        if not date_slug:
            date_slug = parse_meeting_date(filename)
            if date_slug:
                resolved_via = 'filename'

        if date_slug:
            print(f"  Matched Drive file to {date_slug} via {resolved_via}: {filename}")
        else:
            print(f"  Drive file has no resolvable date (deferring to site fallback): {filename}")

        entry['modified_time'] = modified_time
        entry['meeting_slug'] = date_slug
        entry['resolved_via'] = resolved_via

        if bucket_uri and text:
            entry['text_blob'] = cache_text(bucket_uri, file_id, entry, text)

    return catalog
