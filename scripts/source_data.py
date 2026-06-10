import os
import sys
import json
import yaml
import re
import argparse
from datetime import datetime
from dotenv import load_dotenv

# Add the scripts directory to the path so we can import our modules
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

from sourcing import drive, apptegy, spsd_site, vimeo, transcripts

load_dotenv()

# Configuration
FOLDER_ID = "0B42s0chw8f_lQmpaNU93ejYyWkU"
CUTOFF_DATE = "2023-08-01"

def merge_documents(existing_docs, new_docs):
    """
    Merges two lists of documents, deduplicating by URL.
    new_docs take precedence if there's a collision in URL (labels might be updated).
    """
    merged = {drive.clean_url(d.get('url')): d for d in existing_docs if d.get('url')}

    added_count = 0
    for d in new_docs:
        url = drive.clean_url(d.get('url'))
        if url not in merged:
            merged[url] = d
            added_count += 1
        else:
            # Optionally update label/type if new one is more specific?
            # For now, let's stick with the first one found (or priority based)
            pass

    return list(merged.values()), added_count

def reconcile_meetings(all_data, dry_run=False):
    """
    Unified reconciliation and stub generation.
    all_data: dict mapping date_slug to {
        'events': [...],
        'site': {...},
        'drive': [...],
        'video': url,
        'transcript': path
    }
    """
    meeting_dir = 'src/meetings/'
    if not os.path.exists(meeting_dir):
        if not dry_run: os.makedirs(meeting_dir)

    changes_made = 0

    # Sort dates descending
    sorted_dates = sorted(all_data.keys(), reverse=True)

    for date_slug in sorted_dates:
        if date_slug < CUTOFF_DATE:
            continue

        data = all_data[date_slug]

        # Determine if we SHOULD have a meeting for this date (Authority Rule)
        has_valid_event = False
        if data.get('events'):
            for event in data['events']:
                if 'board' in event.get('title', '').lower():
                    has_valid_event = True
                    break

        has_site_data = bool(data.get('site'))
        has_event = has_valid_event or has_site_data

        njk_path = os.path.join(meeting_dir, f"{date_slug}.njk")
        exists = os.path.exists(njk_path)

        if not has_event and not exists:
            # Strict Authority: No valid event, no file exists -> Skip
            continue

        # Gather all docs for this date
        # Priority: Site > Drive
        combined_docs = []
        if data.get('site'):
            combined_docs.extend(data['site'].get('docs', []))

        # Add drive docs if not already present by URL
        site_urls = {drive.clean_url(d['url']) for d in combined_docs}
        for d in data.get('drive', []):
            if drive.clean_url(d['url']) not in site_urls:
                combined_docs.append(d)

        video_url = data.get('video')
        transcript_path = data.get('transcript')

        if exists:
            # Update existing file
            with open(njk_path, 'r') as f:
                content = f.read()

            parts = re.split(r'^---+\s*$', content, flags=re.MULTILINE)
            if len(parts) >= 3:
                fm_text = parts[1]
                body = "---".join(parts[2:])

                try:
                    fm_data = yaml.safe_load(fm_text) or {}
                except Exception as e:
                    print(f"Error parsing YAML in {njk_path}: {e}")
                    continue

                existing_docs = fm_data.get('docs', [])
                merged_docs, added_docs_count = merge_documents(existing_docs, combined_docs)

                updated = False
                if added_docs_count > 0:
                    fm_data['docs'] = merged_docs
                    updated = True

                if video_url and not fm_data.get('video_url'):
                    fm_data['video_url'] = video_url
                    fm_data['has_video'] = True
                    updated = True

                if transcript_path and not fm_data.get('has_transcript'):
                    fm_data['has_transcript'] = True
                    fm_data['has_vtt_source'] = True
                    updated = True

                if updated:
                    print(f"{'[DRY RUN] ' if dry_run else ''}Updating meeting: {date_slug}")
                    if not dry_run:
                        fm_yaml = yaml.dump(fm_data, sort_keys=False, default_flow_style=False, allow_unicode=True)
                        with open(njk_path, 'w') as f:
                            f.write(f"---\n{fm_yaml}---\n{body}")
                    changes_made += 1
            continue

        # Create new stub (only if has_event is true)
        print(f"{'[DRY RUN] ' if dry_run else ''}Creating new meeting stub: {date_slug}...")
        changes_made += 1

        if dry_run:
            continue

        dt = datetime.strptime(date_slug, "%Y-%m-%d")
        display_date = dt.strftime("%B %d, %Y").replace(" 0", " ")
        day_of_week = dt.strftime("%A")

        # Resolve Title & Location
        title = "Regular Meeting"
        location = "South Portland High School Lecture Hall"
        mtype = "Regular"

        if data.get('events'):
            valid_events = [e for e in data['events'] if 'board' in e.get('title', '').lower() or 'executive' in e.get('title', '').lower()]
            event = valid_events[0] if valid_events else data['events'][0]
            title = event.get('title', title)
            location = event.get('location', location)
        elif data.get('site'):
            mtype = data['site'].get('type', mtype)
            title = f"{mtype} Meeting"

        # Refine mtype based on title
        title_lower = title.lower()
        if "special" in title_lower: mtype = "Special"
        elif "workshop" in title_lower: mtype = "Workshop"
        elif "budget" in title_lower: mtype = "Budget"
        elif "executive" in title_lower: mtype = "Executive Session"

        # Final title cleanup
        if mtype != "Regular" and "meeting" not in title_lower:
            title = f"{mtype} Meeting"

        front_matter = {
            "layout": "layouts/meeting.njk",
            "title": f"{display_date} — School Board Meeting — SPSD",
            "heading": title,
            "breadcrumb": dt.strftime("%b %d, %Y").replace(" 0", " "),
            "display_date": display_date,
            "day_of_week": day_of_week,
            "meeting_tag": f"{mtype} Meeting · {dt.strftime('%B %Y')}",
            "time": "6:00 PM",
            "location": location,
            "has_video": bool(video_url),
            "video_url": video_url or "",
            "has_vtt_source": bool(transcript_path),
            "has_transcript": bool(transcript_path),
            "stub": True,
            "board_attendance": [
                {"name": "Rosemarie DeAngelis", "role": "Board"},
                {"name": "Tyler Smith", "role": "Board"},
                {"name": "Daniel Feller", "role": "Board"},
                {"name": "Claire Holman", "role": "Board"},
                {"name": "Eleni Richardson", "role": "Board"},
                {"name": "George Risch", "role": "Board"},
                {"name": "Angela Kabisa", "role": "Student Rep"},
                {"name": "Alex Davison", "role": "Student Rep"}
            ],
            "docs": combined_docs
        }

        fm_yaml = yaml.dump(front_matter, sort_keys=False, default_flow_style=False)
        with open(njk_path, 'w') as f:
            f.write(f"---\n{fm_yaml}---\n")

    return changes_made

def main():
    parser = argparse.ArgumentParser(description='Reconcile meeting data from multiple sources.')
    parser.add_argument('--dry-run', action='store_true', help='Log changes without writing files.')
    args = parser.parse_args()

    all_data = {}

    # 1. Fetch Apptegy Events (Authority)
    print("Step 1: Fetching Apptegy Events...")
    events = apptegy.fetch_events()
    event_mapping = apptegy.get_event_mapping(events)
    for date_slug, info in event_mapping.items():
        if date_slug not in all_data: all_data[date_slug] = {}
        if 'events' not in all_data[date_slug]: all_data[date_slug]['events'] = []
        all_data[date_slug]['events'].append(info)

    # 2. Fetch SPSD Site Data (Authority & Document Primary)
    print("Step 2: Fetching SPSD Site Data...")
    site_mapping = spsd_site.fetch_site_data()
    for date_slug, info in site_mapping.items():
        if date_slug not in all_data: all_data[date_slug] = {}
        all_data[date_slug]['site'] = info

    # 3. Fetch Drive Files (Auxiliary)
    service = drive.get_drive_service()
    if service:
        print("Step 3: Fetching files from Google Drive...")
        try:
            files = drive.list_files_in_folder(service, FOLDER_ID)
            drive_mapping = drive.build_meeting_map(files)
            for date_slug, docs in drive_mapping.items():
                if date_slug not in all_data: all_data[date_slug] = {}
                all_data[date_slug]['drive'] = docs
        except Exception as e:
            print(f"Error accessing Drive: {e}")
    else:
        print("Step 3: Skipping Google Drive (Service not initialized).")

    # 4. Fetch Vimeo Videos
    print("Step 4: Fetching Vimeo mapping...")
    vimeo_mapping = vimeo.get_vimeo_mapping()
    for date_slug, url in vimeo_mapping.items():
        if date_slug not in all_data: all_data[date_slug] = {}
        all_data[date_slug]['video'] = url

    # 5. Fetch Transcripts
    print("Step 5: Fetching Transcripts...")
    transcript_mapping = transcripts.get_transcript_mapping()
    for date_slug, path in transcript_mapping.items():
        if date_slug not in all_data: all_data[date_slug] = {}
        all_data[date_slug]['transcript'] = path

    # 6. Reconcile & Update Meetings
    print("Step 6: Reconciling and updating meetings...")
    changes = reconcile_meetings(all_data, dry_run=args.dry_run)

    # 7. Update Master Map
    if not args.dry_run:
        # We might want to save the final reconciled state
        with open('master_material_map.json', 'w') as f:
            json.dump(all_data, f, indent=2, sort_keys=True)

    if changes > 0:
        print(f"Finished. Updated {changes} meetings.")
    else:
        print("Finished. No new updates found.")
if __name__ == "__main__":
    main()
