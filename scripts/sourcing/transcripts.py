import os
import re

def get_transcript_mapping(transcript_dir='static/transcripts/'):
    """
    Scans the local transcript directory for .vtt files and maps them to dates.
    Expected filename pattern: spboe_YYYYMMDD.vtt or similar.
    """
    mapping = {}
    if not os.path.exists(transcript_dir):
        print(f"Transcript directory {transcript_dir} not found.")
        return mapping

    for root, dirs, files in os.walk(transcript_dir):
        for file in files:
            if file.endswith('.vtt'):
                # Try to extract date YYYYMMDD
                match = re.search(r'(\d{4})(\d{2})(\d{2})', file)
                if match:
                    y, m, d = match.groups()
                    date_slug = f"{y}-{m}-{d}"
                    # Store relative path for web use
                    rel_path = os.path.join(root, file).replace('static/', '/')
                    mapping[date_slug] = rel_path
                else:
                    # Try YYYY-MM-DD
                    match = re.search(r'(\d{4})-(\d{2})-(\d{2})', file)
                    if match:
                        date_slug = match.group(0)
                        rel_path = os.path.join(root, file).replace('static/', '/')
                        mapping[date_slug] = rel_path

    return mapping

if __name__ == "__main__":
    mapping = get_transcript_mapping()
    import json
    print(json.dumps(mapping, indent=2))
