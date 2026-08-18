# Data Pipeline Reference

Technical reference for the three-stage ingestion and synthesis pipeline. All scripts live in `scripts/` and are run from the repo root.

---

## Pipeline Stages

| # | Script | Trigger | Purpose |
|---|--------|---------|---------|
| 1 | `scripts/source_data.py` | Daily CI / manual | Discover meetings from authority sources; create/update `.njk` stubs |
| 2 | `scripts/process_transcripts.py` | Daily CI / manual | Analyze `.vtt` transcripts with Gemini; populate per-meeting AI fields |
| 3 | `scripts/post_process.py` | Daily CI / manual | Enforce glossary (`src/_data/glossary.json`); sort topic taxonomy; synthesize cross-meeting topic summaries |

### Board attendance name canonicalization (`process_transcripts.py`)

Extracted attendance names (e.g. "Ms. DeAngelis") are resolved to full names (e.g. "Rosemarie DeAngelis") by matching against `src/_data/board_members.json`'s roster active on that specific meeting's date (`scripts/board_members_utils.py::canonicalize_attendance_names()`). This only formats names already extracted from the transcript/official document — it never determines *who* attended.

Names with no match are recorded back to `board_members.json` as placeholder entries (`"auto_discovered": true`) so the roster converges toward full coverage as more meetings are processed — a later observation of a fuller name (e.g. "Jennifer Kinney") upgrades the matching placeholder. Hand-curated (non-placeholder) entries are never auto-modified; a coverage gap there is for a human to fix. This update happens once per `process_transcripts.py` run, after all meetings finish processing, to avoid concurrent writes.

---

## Running the Pipeline

```bash
python scripts/source_data.py [--bucket gs://BUCKET] [--force] [--dry-run]
python scripts/process_transcripts.py --batch [--force] [--bucket gs://BUCKET]
python scripts/post_process.py
```

`--bucket` on `source_data.py`: downloads the previous `master_material_map.json`, checks GCS for VTTs when marking `has_transcript`, and syncs Drive VTTs up to the bucket. Omit to run without GCS.

`--force` on `source_data.py`: bypasses the 12-hour TTL fetch cache and re-fetches all sources (Apptegy, SPSD site, Google Drive). Use when a new document or event has been added and the cache is still fresh. Without `--force`, each source is skipped if it was fetched within the last 12 hours.

`--dry-run` on `source_data.py`: logs planned stub creates/updates without writing any files.

`--bucket` on `process_transcripts.py`: supplements the Drive VTT mapping with VTTs stored in the bucket.

`--force` on `process_transcripts.py`: reprocesses all meetings in the VTT mapping, not just stubs. Meetings whose `pipeline_version` field already matches `SCRIPT_VERSION` (the version constant at the top of the script) are skipped — so restarting a failed run is safe and won't duplicate work. Use `--force --batch` when the script's extraction logic has changed and all existing processed meetings need to be updated. After finishing, bump `SCRIPT_VERSION` to a new date string so the next `--force` run knows which meetings are already current.

All scripts require Google ADC. For local dev: `gcloud auth application-default login` or set `GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json`. In CI, `google-github-actions/auth@v3` sets up ADC automatically via the `GCP_CREDENTIALS` secret.

### Triggering a force run via GitHub Actions

```bash
gh workflow run deploy.yml -f force=true
```

A normal dispatch (no flag, or `force=false`) respects the TTL cache as usual.

---

## Objects and Sources

### Meeting creation authority

Meeting stubs are created only when a date is confirmed by an authority source. All other sources enrich existing meetings but never trigger stub creation.

| Source | Role |
|--------|------|
| Apptegy Events API — title contains "board" or "executive" | Primary — creates stubs |
| SPSD site — "board activities" meeting-date entries | Secondary — creates stubs; fills gaps when Apptegy uses non-standard titles |
| Drive filenames · Vimeo titles · VTT filenames | Auxiliary — enrich existing meetings only, never create |

### Per-object sources

| Object | Primary | Secondary | Auxiliary |
|--------|---------|-----------|-----------|
| **Meetings** | Apptegy Events | SPSD site board-activities | Drive · Vimeo · VTTs |
| **Documents** | Date extracted from the document's own content | Date parsed from the filename (day-specific only) | SPSD site board-activities links (last resort) |
| **Videos** | `vimeo_master_list.json` | — | — |
| **Transcripts** | Google Drive VTT docs | GCS bucket | — |

### Document date resolution (`scripts/sourcing/drive.py::build_meeting_map`)

Every file discovered in the Drive folder tree is attributed to a meeting date in this order:

1. **Content** — the file is downloaded and its text extracted (`drive.read_file_text`); the
   earliest date-like string found wins. This is deliberately position-based, not
   pattern-priority: packet PDFs put the current meeting's date in the header, ahead of any dates
   they merely reference (prior minutes being approved, the next meeting being announced).
2. **Filename** — `drive.parse_meeting_date`, only when it resolves to a full day-specific date.
   Monthly packet filenames (e.g. "August 2026 Board Meeting Packet.pdf") never satisfy this —
   content is what actually dates them.
3. **SPSD site** — checked last, in `source_data.py`, by cross-referencing the file's URL against
   documents the site scraper already found and dated independently. Not primary: relying on the
   website being timely and correctly formatted for every meeting-critical document was the
   original cause of documents going missing (see git history around 2026-08-17 for a worked
   example — a packet the website hadn't linked yet, dated correctly from its own PDF text).

A file none of the three can date is left off the meeting entirely and recorded in
`src/_data/unmapped_documents.json` (rendered as a small table on the homepage) instead of being
silently dropped.

The shared Drive folder holds over a decade of material — far more than the site's `CUTOFF_DATE`
(2023-08-01) covers. Content resolution (the download + extraction step) is skipped outright for
any file whose Drive `modifiedTime` predates `CUTOFF_DATE`, since a file modified before the
cutoff era can't plausibly document a meeting on/after it; filename parsing (regex only, no
download) still runs regardless. A pre-cutoff file that stays undated after that is dropped
silently rather than reported as unmapped. `source_data.py` passes its `CUTOFF_DATE` into
`drive.build_meeting_map(..., cutoff_date=CUTOFF_DATE)` — `drive.py` has no cutoff constant of its
own, specifically to avoid two independently-maintained copies drifting apart (which is exactly
how this went unenforced the first time: `drive.py` had its own unused copy of the same value).

Text extracted during content resolution is cached in GCS at
`official_docs/{slug}/{doc_type}-{file_id}/{mod_time}.txt` (`drive.get_or_extract_text`) — every
downstream consumer of an official document's text (`post_process.py`'s official-term extraction
and agenda-preview generation; `process_transcripts.py`'s votes/attendance extraction from
minutes) reads this shared cache instead of re-downloading the same file. The cache is always
populated at a generous size (`drive.CACHE_MAX_CHARS` / `CACHE_MAX_PAGES`) regardless of what the
first caller needed, so a later caller wanting more text than an earlier one doesn't get silently
truncated — callers only control how much of the cached text is *returned* to them via their own
`max_chars`.

### Field-level data priority

When multiple sources provide conflicting values for the same field:

1. Meeting Minutes PDF (official record)
2. SPSD Website board-activities section
3. Google Drive files
4. Vimeo video metadata
5. Raw `.vtt` transcript (lowest — subject to speech recognition errors)

---

## AI Outputs

All per-meeting AI outputs are written to the `.njk` front matter. Cross-meeting outputs are written to `src/_data/`.

### Per-meeting (written by `scripts/process_transcripts.py`)

| Field | Type | Description |
|-------|------|-------------|
| `blurb` | `string` | 1-2 sentence hook for the landing page |
| `topics` | `string[]` | 3-5 specific, time-bound topic tags — see [topic-taxonomy.md](topic-taxonomy.md) for criteria |
| `votes` | `{motion, result, count, moved_2nd}[]` | All formal board votes |
| `summary` | `{topic, text}[]` | 5-8 bullets tracing the arc of the meeting; each includes a perspective attribution (Board / Administration / Teachers / Citizens) |
| `timeline` | `{time, seconds, topic, desc}[]` | 10-15 timestamped moments for Vimeo deep-linking |
| `processed_date` | `string` | ISO date the meeting was last processed (e.g. `'2026-07-24'`) |
| `pipeline_version` | `string` | Value of `SCRIPT_VERSION` at processing time; used by `--force --batch` to skip meetings already at the current version |

These are set when a meeting moves from `stub: true` → `stub: false`, and updated on any subsequent `--force` reprocess. To reprocess all meetings after a script change, bump `SCRIPT_VERSION` in `scripts/process_transcripts.py` and run `--force --batch`.

`blurb` may also be (re-)generated by `scripts/post_process.py` if missing on a fully processed meeting.

### Cross-meeting (written by `scripts/post_process.py`)

| File | Description |
|------|-------------|
| `src/_data/all_topics.json` | Sorted list of all active topics (newest activity first); excludes `TOPIC_BLACKLIST` terms — see [topic-taxonomy.md](topic-taxonomy.md) |
| `src/_data/topic_summaries.json` | Per-topic narrative synthesized from all meeting evidence — prompt template in [prompts.md](prompts.md#2-topic-synthesis-post_processpy) |
| `scripts/topic_hashes.json` | MD5 hash of the evidence fed to the LLM per topic; used to skip redundant API calls when evidence hasn't changed |

`topic_hashes.json` is pipeline state, not Eleventy data — it lives in `scripts/` and is committed to git so the cache persists across CI runs.

---

## Data Files Reference

| File | Committed | Owner | Description |
|------|-----------|-------|-------------|
| `src/meetings/YYYY-MM-DD.njk` | Yes | All 3 scripts | One file per meeting; YAML front matter + empty body |
| `src/_data/all_topics.json` | Yes | `post_process.py` | Sorted topic list consumed by Eleventy topic pages |
| `src/_data/topic_summaries.json` | Yes | `post_process.py` | AI-synthesized topic narratives consumed by Eleventy |
| `src/_data/unmapped_documents.json` | Yes | `source_data.py` | Drive files no source could date; rendered as a table on the homepage |
| `scripts/topic_hashes.json` | Yes | `post_process.py` | Evidence cache (pipeline state, not rendered) |
| `master_material_map.json` | GCS bucket | `source_data.py` | Full reconciled source map with `_stub_action` / `_authority` audit fields per date, plus `_unmapped_docs` |
| `apptegy_events_raw.json` | GCS bucket | `source_data.py` | Complete unfiltered Apptegy API response (all calendar events, full payload) |
| `vimeo_master_list.json` | Yes | Manual | Vimeo video ID → meeting date mapping |
| `official_docs/{slug}/{doc_type}-{file_id}/{mod_time}.txt` | GCS bucket | `source_data.py` (via `drive.get_or_extract_text`) | Extracted text of an official doc, cached once and reused by `post_process.py` and `process_transcripts.py` |

---

## Processing Dependencies

```
External sources
  Apptegy Events API  ─┐
  SPSD Website        ─┤──► scripts/source_data.py ──► src/meetings/*.njk (stubs)
  Google Drive        ─┤                           ──► master_material_map.json
  Vimeo list          ─┤                           ──► GCS bucket/transcripts/ (VTT sync)
  GCS bucket VTTs    ─┘                             ──► GCS bucket/official_docs/*.txt (extracted doc text)
                                                     ──► src/_data/unmapped_documents.json

VTT sources
  Google Drive VTT docs    ─┐
  GCS bucket/transcripts/  ─┴──► scripts/process_transcripts.py ──► src/meetings/*.njk (stub: false)
                                    (reads src/_data/all_topics.json for tag reuse)

src/meetings/*.njk (all)
  └──► scripts/post_process.py ──► src/_data/all_topics.json
                                ──► src/_data/topic_summaries.json
                                ──► scripts/topic_hashes.json

src/meetings/*.njk + src/_data/*.json
  └──► npm run build (Eleventy) ──► _site/
```

---

## Sourcing Modules (`scripts/sourcing/`)

For which sources are authoritative for each object type, see [Meeting creation authority](#meeting-creation-authority). For how `drive.py` dates a document, see [Document date resolution](#document-date-resolution-scriptssourcingdrivepybuild_meeting_map).

| Module | Source | Returns |
|--------|--------|---------|
| `apptegy.py` | Apptegy Events v2 API | `{date_slug: {title, location, id}}` |
| `spsd_site.py` | SPSD board-activities "meeting date" entries (scraped) | `{date_slug: {date, type, docs[]}}` |
| `drive.py` | Google Drive folder — sourcing, text extraction/GCS caching, and content/filename-based date mapping | `({date_slug: [{type, label, url}]}, unresolved[])` |
| `vimeo.py` | `vimeo_master_list.json` (title-based) | `{date_slug: vimeo_url}` |
| `transcripts.py` | Google Drive VTTs + GCS bucket | `{date_slug: path_or_uri}` |
