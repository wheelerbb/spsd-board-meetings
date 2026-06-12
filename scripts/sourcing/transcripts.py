import re
from datetime import datetime

_MONTHS = ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]

def _parse_vtt_date(filename):
    """Extract YYYY-MM-DD from a VTT filename. Returns None if unrecognized."""
    # ISO slug format used for bucket blobs: 2024-01-08.vtt
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', filename)
    if m:
        y, mo, d = m.groups()
        if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{mo}-{d}"
    # Compact YYYYMMDD: spboe_20240108.vtt
    m = re.search(r'(\d{4})(\d{2})(\d{2})', filename)
    if m:
        y, mo, d = m.groups()
        if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{mo}-{d}"
    # MM.DD.YY: 04.07.26.vtt
    m = re.search(r'(\d{2})\.(\d{2})\.(\d{2})', filename)
    if m:
        return f"20{m.group(3)}-{m.group(1)}-{m.group(2)}"
    # Long month: South Portland Board of Education - June 8 2026.vtt
    m = re.search(rf'({"|".join(_MONTHS)}) (\d{{1,2}}) (\d{{4}})', filename)
    if m:
        dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y")
        return dt.strftime("%Y-%m-%d")
    return None


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


