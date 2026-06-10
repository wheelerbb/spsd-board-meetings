import json
import os
import re
import requests

VIMEO_API_BASE = "https://api.vimeo.com"
SPCTV_USER = "spctv"

_MONTHS = ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]


def get_vimeo_mapping(file_path='vimeo_master_list.json'):
    """Parses the vimeo master list and returns a mapping of date_slug to video_url."""
    try:
        with open(file_path, 'r') as f:
            vimeo_data = json.load(f)

        mapping = {}
        for video_id, title in vimeo_data.items():
            # Match YYYYMMDD
            match = re.search(r'(\d{4})(\d{2})(\d{2})', title)
            if match:
                y, m, d = match.groups()
                date_slug = f"{y}-{m}-{d}"
                mapping[date_slug] = f"https://vimeo.com/{video_id}"
            else:
                # Try Month Day Year
                match = re.search(rf'({"|".join(_MONTHS)})\s+(\d{{1,2}})(?:,)?\s+(\d{{4}})', title, re.I)
                if match:
                    try:
                        from datetime import datetime
                        dt = datetime.strptime(f"{match.group(1).capitalize()} {match.group(2)} {match.group(3)}", "%B %d %Y")
                        date_slug = dt.strftime("%Y-%m-%d")
                        mapping[date_slug] = f"https://vimeo.com/{video_id}"
                    except Exception:
                        pass

        return mapping
    except Exception as e:
        print(f"Error parsing Vimeo list: {e}")
        return {}


def fetch_channel_videos(token, user_id=SPCTV_USER):
    """Fetches all videos from a Vimeo user/channel via the Vimeo API.

    Returns a dict of {video_id_str: title} in reverse-chronological order
    (newest first, matching Vimeo's default sort).
    """
    headers = {"Authorization": f"bearer {token}"}
    params = {"per_page": 100, "fields": "uri,name", "sort": "date", "direction": "desc"}
    url = f"{VIMEO_API_BASE}/users/{user_id}/videos"

    videos = {}
    page = 1
    while url:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("data", []):
            video_id = item["uri"].split("/")[-1]
            videos[video_id] = item["name"]
        # Follow pagination; params only needed on first request
        params = None
        url = None
        next_path = data.get("paging", {}).get("next")
        if next_path:
            url = VIMEO_API_BASE + next_path
        page += 1

    return videos


def update_master_list(file_path='vimeo_master_list.json', user_id=SPCTV_USER):
    """Fetches videos from the Vimeo channel and merges new entries into the local JSON.

    Requires VIMEO_ACCESS_TOKEN in the environment.
    New entries are prepended (newest-first). Existing entries are preserved unchanged
    so manual corrections to titles are not overwritten.
    """
    token = os.getenv("VIMEO_ACCESS_TOKEN")
    if not token:
        print("Error: VIMEO_ACCESS_TOKEN not set.")
        return

    print(f"Fetching videos from Vimeo user '{user_id}'...")
    api_videos = fetch_channel_videos(token, user_id)
    print(f"  Found {len(api_videos)} videos on channel.")

    existing = {}
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            existing = json.load(f)

    # New entries: in API but not in existing file (preserve existing titles)
    new_ids = [vid_id for vid_id in api_videos if vid_id not in existing]

    # Build merged dict: new API entries first (newest→oldest), then existing-only entries
    merged = {vid_id: api_videos[vid_id] for vid_id in api_videos if vid_id not in existing}
    merged.update(existing)

    with open(file_path, 'w') as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    if new_ids:
        print(f"  Added {len(new_ids)} new video(s):")
        for vid_id in new_ids:
            print(f"    {vid_id}: {api_videos[vid_id]}")
    else:
        print("  No new videos found.")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    update_master_list()
    print("\nDate mapping after update:")
    mapping = get_vimeo_mapping()
    import sys
    if "--verbose" in sys.argv:
        print(json.dumps(mapping, indent=2))
    else:
        for slug, url in list(mapping.items())[:5]:
            print(f"  {slug} → {url}")
        if len(mapping) > 5:
            print(f"  ... ({len(mapping)} total)")
