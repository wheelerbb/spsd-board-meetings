import os
import re
from datetime import datetime

_MONTHS = ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]

def _parse_vtt_date(filename):
    """Extract YYYY-MM-DD from a VTT filename. Returns None if unrecognized."""
    m = re.search(r'(\d{4})(\d{2})(\d{2})', filename)
    if m:
        y, mo, d = m.groups()
        if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{mo}-{d}"
    m = re.search(r'(\d{2})\.(\d{2})\.(\d{2})', filename)
    if m:
        return f"20{m.group(3)}-{m.group(1)}-{m.group(2)}"
    m = re.search(rf'({"|".join(_MONTHS)}) (\d{{1,2}}) (\d{{4}})', filename)
    if m:
        dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y")
        return dt.strftime("%Y-%m-%d")
    return None


def get_transcript_mapping(transcript_dir='static/transcripts'):
    """Scans local transcript dir. Returns {date_slug: local_file_path}."""
    mapping = {}
    if not os.path.exists(transcript_dir):
        return mapping
    for root, _, files in os.walk(transcript_dir):
        for f in files:
            if not f.endswith('.vtt'):
                continue
            slug = _parse_vtt_date(f)
            if slug:
                mapping[slug] = os.path.join(root, f)
    return mapping


def get_bucket_vtt_mapping(bucket_uri, cutoff_date='2023-08-01'):
    """Lists transcripts/<date_slug>.vtt blobs in GCS. Returns {date_slug: gs_uri}."""
    from google.cloud import storage
    path = bucket_uri.replace('gs://', '')
    parts = path.split('/', 1)
    bucket_name = parts[0]
    prefix = (parts[1].rstrip('/') + '/') if len(parts) > 1 and parts[1] else ''
    blob_prefix = f"{prefix}transcripts/"
    mapping = {}
    client = storage.Client()
    for blob in client.bucket(bucket_name).list_blobs(prefix=blob_prefix):
        basename = blob.name.split('/')[-1]
        if not basename.endswith('.vtt'):
            continue
        slug = _parse_vtt_date(basename)
        if slug and slug >= cutoff_date:
            mapping[slug] = f"gs://{bucket_name}/{blob.name}"
    return mapping


def sync_local_vtts_to_bucket(bucket_uri, transcript_dir='static/transcripts', cutoff_date='2023-08-01'):
    """Upload local VTTs to GCS bucket at transcripts/<date_slug>.vtt. Skips existing blobs."""
    from google.cloud import storage
    local = get_transcript_mapping(transcript_dir)
    if not local:
        print("VTT sync: no local transcripts found.")
        return 0
    path = bucket_uri.replace('gs://', '')
    parts = path.split('/', 1)
    bucket_name = parts[0]
    prefix = (parts[1].rstrip('/') + '/') if len(parts) > 1 and parts[1] else ''
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    uploaded = 0
    for slug, local_path in sorted(local.items()):
        if slug < cutoff_date:
            continue
        blob_name = f"{prefix}transcripts/{slug}.vtt"
        blob = bucket.blob(blob_name)
        if not blob.exists():
            blob.upload_from_filename(local_path)
            print(f"  Uploaded {slug} → gs://{bucket_name}/{blob_name}")
            uploaded += 1
    print(f"VTT sync: {uploaded} new file(s) uploaded.")
    return uploaded


if __name__ == "__main__":
    import json
    print(json.dumps(get_transcript_mapping(), indent=2))
