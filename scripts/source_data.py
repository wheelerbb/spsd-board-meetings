import os
import sys
import json
import yaml
import re
import argparse
from datetime import datetime, date
from dotenv import load_dotenv

# Add the scripts directory to the path so we can import our modules
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

from sourcing import drive, apptegy, spsd_site, vimeo, transcripts
from board_members_utils import active_members
import pipeline_log

load_dotenv()

# Configuration
FOLDER_ID = "0B42s0chw8f_lQmpaNU93ejYyWkU"
CUTOFF_DATE = "2023-08-01"

# Sentinel for a URL that resolves to more than one date_slug in the same run's fresh source
# data — treated as unclaimed by anyone for reassignment purposes, so a conflicting resolution
# never causes a doc to be stripped from a meeting it might actually still belong to.
AMBIGUOUS = object()

def merge_documents(existing_docs, new_docs, url_owner=None, date_slug=None):
    """
    Merges an existing meeting's docs with this run's freshly-resolved docs for the same date,
    deduplicating by URL. Returns (merged_docs, added_count, removed_count).

    - On a URL collision, new_docs always wins — it reflects this run's current best resolution,
      which may have corrected a label/type since existing_docs was last written.
    - `url_owner` (optional), if given, maps every URL claimed by *any* date this run to the
      date_slug that claims it (or AMBIGUOUS if more than one date claims it). An existing doc
      whose URL is confidently owned by a *different* date_slug is dropped — unambiguous evidence
      it was reassigned. A doc whose URL isn't in url_owner at all (absent from every date's fresh
      data this run, not just this one) is left untouched: absence here isn't evidence of
      anything, only presence under a different date is. This can't catch reassignment to a date
      outside url_owner's coverage (e.g. before CUTOFF_DATE), which is an accepted limitation.
    """
    removed_count = 0
    merged = {}
    for d in existing_docs:
        url = drive.clean_url(d.get('url'))
        if not url:
            continue
        owner = url_owner.get(url) if url_owner else None
        if owner is not None and owner is not AMBIGUOUS and owner != date_slug:
            removed_count += 1
            continue
        merged[url] = d

    added_count = 0
    for d in new_docs:
        url = drive.clean_url(d.get('url'))
        if url not in merged:
            added_count += 1
        merged[url] = d  # new_docs always wins on collision

    return list(merged.values()), added_count, removed_count

def _backfill_file_types(docs, catalog):
    """Fills in `file_type` (PDF, DOCX, ...) on any doc missing it, by looking up its URL in the
    Drive catalog — the sole owner of file-format metadata (see drive.file_type_label). Needed for
    docs sourced from the SPSD site listing (which only has label/url/doc_type, never mime info)
    and for docs written to disk before file_type existed. Mutates `docs` in place; returns True
    if anything changed."""
    if not catalog:
        return False
    url_to_file_type = {
        drive.clean_url(e.get('url')): e['file_type']
        for e in catalog.values() if e.get('url') and e.get('file_type')
    }
    changed = False
    for d in docs:
        if not d.get('file_type'):
            file_type = url_to_file_type.get(drive.clean_url(d.get('url')))
            if file_type:
                d['file_type'] = file_type
                changed = True
    return changed

def _combine_site_and_drive_docs(site_docs, drive_docs):
    """Drive is authoritative for a doc's type/label (filename/content-derived — see
    drive.py::categorize_document and docs/pipeline.md). Site is a secondary fallback, used only
    for a doc Drive doesn't have at all — the SPSD site listing types a doc via a positional text
    window around its link, not a reliable classifier."""
    combined = list(drive_docs)
    seen = {drive.clean_url(d['url']) for d in combined}
    for d in site_docs:
        u = drive.clean_url(d['url'])
        if u not in seen:
            combined.append(d)
            seen.add(u)
    return combined


def _dedupe_identical_content(docs, catalog, bucket_uri):
    """Drops a doc when another doc of the *same type* on the same meeting has byte-identical
    extracted text — the same underlying document uploaded to Drive twice under different
    file_ids (confirmed archive-wide: 5 real duplicate pairs, e.g. a meeting's minutes uploaded
    twice nine minutes apart). Restricted to matching doc_type because two genuinely different
    document types (an agenda and a packet, say) can coincidentally extract to overlapping text
    without being duplicates of each other. Only fetches cached text for a type that actually has
    2+ docs, so a normal one-of-each meeting costs no extra GCS reads. Returns (docs, removed_count)."""
    if not bucket_uri:
        return docs, 0

    by_type = {}
    for i, d in enumerate(docs):
        by_type.setdefault(d.get('type'), []).append(i)

    to_drop = set()
    for idxs in by_type.values():
        if len(idxs) < 2:
            continue
        seen_texts = {}
        for i in idxs:
            m = re.search(r'/file/d/([^/?]+)', docs[i].get('url') or '')
            if not m:
                continue
            entry = catalog.get(m.group(1)) if catalog else None
            if not entry or not entry.get('text_blob'):
                continue
            text = drive.read_cached_blob(bucket_uri, entry['text_blob'])
            if text is None:
                continue
            if text in seen_texts:
                to_drop.add(i)
            else:
                seen_texts[text] = i

    if not to_drop:
        return docs, 0
    return [d for i, d in enumerate(docs) if i not in to_drop], len(to_drop)

def reconcile_meetings(all_data, dry_run=False, catalog=None, bucket_uri=''):
    """
    Unified reconciliation and stub generation.
    all_data: dict mapping date_slug to {
        'events': [...],
        'site': {...},
        'drive': [...],
        'video': url,
        'transcript': path
    }
    `catalog` (optional) backfills `file_type` on docs sourced without it — see
    _backfill_file_types.
    Mutates all_data in place to add '_stub_action' and '_authority' audit fields.
    """
    board_members_path = os.path.join(BASE_DIR, '..', 'src', '_data', 'board_members.json')
    with open(board_members_path) as f:
        board_members = json.load(f)

    meeting_dir = 'src/meetings/'
    if not os.path.exists(meeting_dir):
        if not dry_run: os.makedirs(meeting_dir)

    changes_made = 0

    # Sort dates descending
    sorted_dates = sorted(all_data.keys(), reverse=True)

    # Precompute this run's combined (site + drive) docs for every in-scope date, plus a
    # cross-date URL ownership index, before reconciling any single meeting — this is what lets
    # merge_documents() tell "reassigned to another meeting" (URL claimed by a different date)
    # apart from "just not re-derived this run" (URL claimed by no date at all).
    combined_docs_by_slug = {}
    for slug, d in all_data.items():
        if slug < CUTOFF_DATE:
            continue
        site_docs = list(d.get('site', {}).get('docs', [])) if d.get('site') else []
        combined_docs_by_slug[slug] = _combine_site_and_drive_docs(site_docs, d.get('drive', []))

    url_owner = {}
    for slug, docs in combined_docs_by_slug.items():
        for doc in docs:
            u = drive.clean_url(doc.get('url'))
            if not u:
                continue
            if u in url_owner and url_owner[u] not in (slug, AMBIGUOUS):
                print(f"  Warning: URL {u} resolves to both {url_owner[u]!r} and {slug!r} "
                      f"this run; not treating it as reassigned from either.")
                url_owner[u] = AMBIGUOUS
            elif u not in url_owner:
                url_owner[u] = slug

    for date_slug in sorted_dates:
        if date_slug < CUTOFF_DATE:
            continue

        data = all_data[date_slug]

        # Determine if we SHOULD have a meeting for this date (Authority Rule)
        has_valid_event = False
        if data.get('events'):
            for event in data['events']:
                t = event.get('title', '').lower()
                if 'board' in t or 'executive' in t:
                    has_valid_event = True
                    break

        has_site_data = bool(data.get('site'))
        has_event = has_valid_event or has_site_data

        # Audit: record which authority source(s) claim this date
        if has_valid_event and has_site_data:
            data['_authority'] = 'both'
        elif has_valid_event:
            data['_authority'] = 'apptegy'
        elif has_site_data:
            data['_authority'] = 'site'
        else:
            data['_authority'] = None

        njk_path = os.path.join(meeting_dir, f"{date_slug}.njk")
        exists = os.path.exists(njk_path)

        if not has_event and not exists:
            # Strict Authority: No valid event, no file exists -> Skip
            data['_stub_action'] = 'skipped'
            continue

        # Gather all docs for this date (Priority: Drive > Site) — precomputed above.
        combined_docs = combined_docs_by_slug.get(date_slug, [])

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
                    data['_stub_action'] = 'parse_error'
                    continue

                existing_docs = fm_data.get('docs', [])
                merged_docs, added_docs_count, removed_docs_count = merge_documents(
                    existing_docs, combined_docs, url_owner, date_slug)
                merged_docs, deduped_count = _dedupe_identical_content(merged_docs, catalog, bucket_uri)
                removed_docs_count += deduped_count
                file_types_backfilled = _backfill_file_types(merged_docs, catalog)

                # Compare against the merged result (not just added/removed counts) so a
                # same-URL label/type correction — which changes neither count — still gets
                # written back.
                docs_changed = merged_docs != existing_docs

                updated = False
                if docs_changed or file_types_backfilled:
                    fm_data['docs'] = merged_docs
                    updated = True

                if video_url and not fm_data.get('video_url'):
                    fm_data['video_url'] = video_url
                    fm_data['has_video'] = True
                    updated = True

                has_vtt = any(d.get('type') == 'transcript' for d in combined_docs)
                if (transcript_path or has_vtt) and not fm_data.get('has_transcript'):
                    fm_data['has_transcript'] = True
                    fm_data['has_vtt_source'] = True
                    updated = True

                if updated:
                    data['_stub_action'] = 'updated'
                    msg = f"{'[DRY RUN] ' if dry_run else ''}Updating meeting: {date_slug}"
                    if added_docs_count:
                        msg += f" (+{added_docs_count} docs)"
                    if removed_docs_count:
                        msg += f" (-{removed_docs_count} docs reassigned/deduped)"
                    print(msg)
                    if not dry_run:
                        fm_yaml = yaml.dump(fm_data, sort_keys=False, default_flow_style=False, allow_unicode=True)
                        with open(njk_path, 'w') as f:
                            f.write(f"---\n{fm_yaml}---\n{body}")
                    changes_made += 1
                else:
                    data['_stub_action'] = 'unchanged'
            else:
                data['_stub_action'] = 'parse_error'
            continue

        # Create new stub (only if has_event is true)
        data['_stub_action'] = 'created'
        print(f"{'[DRY RUN] ' if dry_run else ''}Creating new meeting stub: {date_slug}...")
        changes_made += 1

        if dry_run:
            continue

        combined_docs, _ = _dedupe_identical_content(combined_docs, catalog, bucket_uri)
        _backfill_file_types(combined_docs, catalog)

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
        if mtype == "Regular":
            title = f"{dt.strftime('%B')} Regular Meeting"
        elif "meeting" not in title_lower:
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
            "has_vtt_source": bool(transcript_path) or any(d.get('type') == 'transcript' for d in data.get('drive', [])),
            "has_transcript": bool(transcript_path) or any(d.get('type') == 'transcript' for d in data.get('drive', [])),
            "stub": True,
            "board_attendance": active_members(board_members, dt.date()),
            "docs": combined_docs
        }

        fm_yaml = yaml.dump(front_matter, sort_keys=False, default_flow_style=False)
        with open(njk_path, 'w') as f:
            f.write(f"---\n{fm_yaml}---\n")

    return changes_made


# --- GCP Bucket helpers ---

def _bucket_parts(bucket_uri):
    """Returns (bucket_name, prefix) from a gs://bucket or gs://bucket/prefix URI."""
    path = bucket_uri.replace('gs://', '')
    parts = path.split('/', 1)
    bucket_name = parts[0]
    prefix = (parts[1].rstrip('/') + '/') if len(parts) > 1 and parts[1] else ''
    return bucket_name, prefix

def download_from_bucket(bucket_uri, local_path):
    from google.cloud import storage
    bucket_name, prefix = _bucket_parts(bucket_uri)
    blob_name = prefix + os.path.basename(local_path)
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(blob_name)
    if blob.exists():
        blob.download_to_filename(local_path)
        print(f"Downloaded gs://{bucket_name}/{blob_name} → {local_path}")
    else:
        print(f"No existing gs://{bucket_name}/{blob_name} (first run, skipping download)")

def upload_to_bucket(bucket_uri, local_path):
    from google.cloud import storage
    bucket_name, prefix = _bucket_parts(bucket_uri)
    blob_name = prefix + os.path.basename(local_path)
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(blob_name)
    blob.upload_from_filename(local_path)
    print(f"Uploaded {local_path} → gs://{bucket_name}/{blob_name}")


def sync_drive_vtts_to_bucket(bucket_uri, all_data, existing_bucket_slugs, cutoff_date):
    """Download Drive VTTs (type=vtt docs) not yet in bucket and upload them."""
    from google.cloud import storage
    bucket_name, prefix = _bucket_parts(bucket_uri)
    gcs = storage.Client()
    bucket = gcs.bucket(bucket_name)
    uploaded = 0
    for date_slug, data in sorted(all_data.items()):
        if date_slug < cutoff_date or date_slug in existing_bucket_slugs:
            continue
        for doc in (data.get('drive') or []):
            if doc.get('type') != 'transcript':
                continue
            fid = re.search(r'/file/d/([^/?]+)', doc.get('url', ''))
            if not fid:
                continue
            blob_name = f"{prefix}transcripts/{date_slug}.vtt"
            blob = bucket.blob(blob_name)
            if blob.exists():
                break
            try:
                content = drive.download_file(fid.group(1))
                blob.upload_from_string(content.encode('utf-8'), content_type='text/vtt')
                print(f"  Synced Drive VTT {date_slug} → gs://{bucket_name}/{blob_name}")
                uploaded += 1
            except Exception as e:
                print(f"  Warning: could not sync Drive VTT {date_slug}: {e}")
            break
    return uploaded


_SOURCE_TTL_HOURS = 12


def _cache_fresh(meta, source):
    """Return True if source was fetched within _SOURCE_TTL_HOURS."""
    entry = meta.get(source)
    if not entry:
        return False
    fetched_at = datetime.fromisoformat(entry['fetched_at'])
    age = (datetime.utcnow() - fetched_at).total_seconds()
    return age < _SOURCE_TTL_HOURS * 3600


def main():
    parser = argparse.ArgumentParser(description='Reconcile meeting data from multiple sources.')
    parser.add_argument('--dry-run', action='store_true', help='Log changes without writing files.')
    parser.add_argument('--force', action='store_true', help='Re-fetch all sources, bypassing TTL cache.')
    parser.add_argument('--bucket', default='', metavar='BUCKET_URI',
                        help='GCS bucket URI (e.g. gs://my-bucket) for persisting pipeline data files.')
    args = parser.parse_args()
    bucket = args.bucket or os.getenv('GCS_BUCKET_URI', '')
    args.bucket = bucket

    # Download previous master map from bucket (enables incremental awareness + cache timestamps)
    if args.bucket:
        download_from_bucket(args.bucket, 'master_material_map.json')

    old_master_map = {}
    if os.path.exists('master_material_map.json'):
        with open('master_material_map.json') as f:
            old_master_map = json.load(f)

    cache_meta = old_master_map.get('_cache_meta', {})
    cache_updated = False
    all_data = {}

    # 1. Fetch Apptegy Events (Authority)
    print("Step 1: Fetching Apptegy Events...")
    apptegy_fetched = False
    apptegy_changed = []
    if args.force or not _cache_fresh(cache_meta, 'apptegy'):
        events = apptegy.fetch_events()
        if not args.dry_run:
            with open('apptegy_events_raw.json', 'w') as f:
                json.dump(events, f, indent=2)
        cache_meta['apptegy'] = {'fetched_at': datetime.utcnow().isoformat()}
        cache_updated = True
        apptegy_fetched = True
        event_mapping = apptegy.get_event_mapping(events)
        for date_slug, info in event_mapping.items():
            if date_slug < CUTOFF_DATE: continue
            if date_slug not in all_data: all_data[date_slug] = {}
            if 'events' not in all_data[date_slug]: all_data[date_slug]['events'] = []
            all_data[date_slug]['events'].append(info)
        apptegy_changed = [
            s for s in {s for s in event_mapping if s >= CUTOFF_DATE}
            if all_data.get(s, {}).get('events') != old_master_map.get(s, {}).get('events')
        ]
    else:
        print("  Using cached Apptegy data.")
        for date_slug, data in old_master_map.items():
            if date_slug.startswith('_') or date_slug < CUTOFF_DATE: continue
            if 'events' in data:
                if date_slug not in all_data: all_data[date_slug] = {}
                all_data[date_slug]['events'] = data['events']

    # 2. Fetch SPSD Site Data (Authority & Document Primary)
    print("Step 2: Fetching SPSD Site Data...")
    site_fetched = False
    site_changed = []
    if args.force or not _cache_fresh(cache_meta, 'site'):
        site_mapping = spsd_site.fetch_site_data()
        cache_meta['site'] = {'fetched_at': datetime.utcnow().isoformat()}
        cache_updated = True
        site_fetched = True
        for date_slug, info in site_mapping.items():
            if date_slug < CUTOFF_DATE: continue
            if date_slug not in all_data: all_data[date_slug] = {}
            all_data[date_slug]['site'] = info
        site_changed = [
            s for s in site_mapping
            if s >= CUTOFF_DATE and all_data.get(s, {}).get('site') != old_master_map.get(s, {}).get('site')
        ]
    else:
        print("  Using cached site data.")
        for date_slug, data in old_master_map.items():
            if date_slug.startswith('_') or date_slug < CUTOFF_DATE: continue
            if 'site' in data:
                if date_slug not in all_data: all_data[date_slug] = {}
                all_data[date_slug]['site'] = data['site']

    # 3. Fetch Drive Files (dated primarily by content, then filename — see drive.build_meeting_map)
    # The Drive catalog (file_id -> metadata + resolution + cache pointers) is its own dedicated
    # GCS resource, not folded into master_material_map.json — see
    # docs/pipeline.md#the-drive-catalog-drive_catalogjson.
    catalog = drive.load_catalog(args.bucket)
    prev_modified = {fid: e.get('modified_time') for fid, e in catalog.items()}
    drive_fetched = False
    if args.force or not _cache_fresh(cache_meta, 'drive'):
        service = drive.get_drive_service()
        if service:
            print("Step 3: Fetching files from Google Drive...")
            try:
                files = drive.list_files_in_folder(service, FOLDER_ID)
                drive.build_meeting_map(files, catalog, bucket_uri=args.bucket, service=service, cutoff_date=CUTOFF_DATE)
                cache_meta['drive'] = {'fetched_at': datetime.utcnow().isoformat()}
                cache_updated = True
                drive_fetched = True

                # Anything content/filename couldn't date might still be linked (and dated) on the
                # SPSD site — that source is a fallback here, checked last, not primary. Persisted
                # directly into the catalog entry so it isn't re-checked from scratch every run.
                site_url_to_slug = {
                    drive.clean_url(d['url']): slug
                    for slug, info in all_data.items() if info.get('site')
                    for d in info['site'].get('docs', [])
                }
                for entry in catalog.values():
                    if entry.get('meeting_slug') is not None:
                        continue
                    site_slug = site_url_to_slug.get(drive.clean_url(entry.get('url')))
                    if site_slug:
                        entry['meeting_slug'] = site_slug
                        entry['resolved_via'] = 'site'
            except Exception as e:
                print(f"Error accessing Drive: {e}")
        else:
            print("Step 3: Skipping Google Drive (Service not initialized).")
    else:
        print("Step 3: Using cached Drive data.")

    drive_changed = [
        {
            "file_id": fid,
            "label": catalog[fid].get('label'),
            "doc_type": catalog[fid].get('doc_type'),
            "meeting_slug": catalog[fid].get('meeting_slug'),
            "change": "new" if fid not in prev_modified else "modified",
        }
        for fid in catalog
        if catalog[fid].get('modified_time') != prev_modified.get(fid)
    ]

    # Project the catalog into all_data — regardless of whether Step 3 fetched fresh or reused
    # the existing catalog, this is what feeds each meeting's docs[] in reconcile_meetings().
    for entry in catalog.values():
        slug = entry.get('meeting_slug')
        if not slug or slug < CUTOFF_DATE:
            continue
        if slug not in all_data: all_data[slug] = {}
        all_data[slug].setdefault('drive', []).append({
            'type': entry.get('doc_type', 'misc'),
            'label': entry.get('label'),
            'url': entry.get('url'),
            'file_type': entry.get('file_type'),
        })

    # Files nothing could date. build_meeting_map already excludes anything out of cutoff scope
    # from the catalog entirely, so every entry here is already something worth a human's look.
    unmapped_docs = [
        {'type': e.get('doc_type'), 'label': e.get('label'), 'url': e.get('url')}
        for e in catalog.values()
        if e.get('meeting_slug') is None
    ]
    if unmapped_docs:
        print(f"  {len(unmapped_docs)} Drive file(s) have no date from content, filename, or the SPSD site.")

    # 4. Fetch Vimeo Videos
    print("Step 4: Fetching Vimeo mapping...")
    vimeo_fetched = False
    vimeo_new = []
    if args.force or not _cache_fresh(cache_meta, 'vimeo'):
        try:
            vimeo_new = vimeo.update_master_list()
            cache_meta['vimeo'] = {'fetched_at': datetime.utcnow().isoformat()}
            cache_updated = True
            vimeo_fetched = True
        except Exception as e:
            print(f"  Warning: Vimeo RSS fetch failed: {e}")
    else:
        print("  Using cached Vimeo data.")
    vimeo_mapping = vimeo.get_vimeo_mapping()
    for date_slug, url in vimeo_mapping.items():
        if date_slug < CUTOFF_DATE: continue
        if date_slug not in all_data: all_data[date_slug] = {}
        all_data[date_slug]['video'] = url

    # 5. Fetch Transcripts (GCS bucket only)
    print("Step 5: Fetching Transcripts...")
    bucket_vtts = {}
    if args.bucket:
        try:
            bucket_vtts = transcripts.get_bucket_vtt_mapping(args.bucket, CUTOFF_DATE)
            print(f"  Found {len(bucket_vtts)} VTT(s) in bucket.")
        except Exception as e:
            print(f"  Warning: could not read bucket VTTs: {e}")
    for date_slug, path in bucket_vtts.items():
        if date_slug < CUTOFF_DATE: continue
        if date_slug not in all_data: all_data[date_slug] = {}
        all_data[date_slug]['transcript'] = path

    transcripts_changed = [
        s for s, path in bucket_vtts.items()
        if s >= CUTOFF_DATE and old_master_map.get(s, {}).get('transcript') != path
    ]

    # 6. Reconcile & Update Meetings (also annotates all_data with _stub_action/_authority)
    print("Step 6: Reconciling and updating meetings...")
    changes = reconcile_meetings(all_data, dry_run=args.dry_run, catalog=catalog, bucket_uri=args.bucket)

    # Signal to the calling workflow (via a step output) whether any source produced a
    # visible change this run — used to gate expensive downstream steps on scheduled runs.
    # `changes` already folds in every source (Apptegy/site/Drive/video/transcript all feed
    # into all_data before reconcile_meetings runs), so it's a complete "did anything
    # happen" signal on its own.
    gh_output = os.environ.get('GITHUB_OUTPUT')
    if gh_output:
        with open(gh_output, 'a') as f:
            f.write(f"changes={'true' if changes > 0 else 'false'}\n")

    pipeline_log.append_entry(args.bucket, 'sourcing_log', {
        "run_id": pipeline_log.current_run_id(),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "trigger": os.environ.get('GITHUB_EVENT_NAME', 'manual'),
        "sourcing": {
            "apptegy": {"fetched": apptegy_fetched, "changed": apptegy_changed},
            "site": {"fetched": site_fetched, "changed": site_changed},
            "drive": {"fetched": drive_fetched, "changed": drive_changed},
            "vimeo": {"fetched": vimeo_fetched, "changed": vimeo_new},
            "transcripts": {"fetched": bool(args.bucket), "changed": transcripts_changed},
        },
        "meetings": {
            "created": [s for s, d in all_data.items() if d.get('_stub_action') == 'created'],
            "updated": [s for s, d in all_data.items() if d.get('_stub_action') == 'updated'],
        },
        "any_changes": changes > 0,
    })

    # 7. Sync Drive VTTs to bucket; update canonical GCS URIs in all_data
    if args.bucket and not args.dry_run:
        print("Step 7: Syncing VTTs to bucket...")
        try:
            # Reuse bucket_vtts from Step 5 to avoid a redundant GCS list call
            existing_bucket_slugs = set(bucket_vtts.keys())
            sync_drive_vtts_to_bucket(args.bucket, all_data, existing_bucket_slugs, CUTOFF_DATE)
            # Refresh canonical GCS URIs in all_data after any newly uploaded VTTs
            for slug, gcs_uri in transcripts.get_bucket_vtt_mapping(args.bucket, CUTOFF_DATE).items():
                if slug in all_data:
                    all_data[slug]['transcript'] = gcs_uri
        except Exception as e:
            print(f"  Warning: VTT sync failed: {e}")

    # 8. Write master map (with cache timestamps) and upload if anything changed
    if not args.dry_run:
        sorted_data = dict(sorted(all_data.items(), reverse=True))
        sorted_data['_cache_meta'] = cache_meta
        with open('master_material_map.json', 'w') as f:
            json.dump(sorted_data, f, indent=2)
        if args.bucket and (changes > 0 or cache_updated):
            upload_to_bucket(args.bucket, 'master_material_map.json')
            drive.save_catalog(args.bucket, catalog)
            if apptegy_fetched:
                upload_to_bucket(args.bucket, 'apptegy_events_raw.json')

        # 9. Publish unmapped Drive files for Eleventy (visible on the homepage instead of silently dropped)
        os.makedirs('src/_data', exist_ok=True)
        with open('src/_data/unmapped_documents.json', 'w') as f:
            json.dump(unmapped_docs, f, indent=2)

    if changes > 0:
        print(f"Finished. Updated {changes} meetings.")
    else:
        print("Finished. No new updates found.")

if __name__ == "__main__":
    main()
