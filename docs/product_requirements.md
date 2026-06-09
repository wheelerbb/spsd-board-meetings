# Product Requirements: Data Sourcing

This document outlines the requirements and logic for the automated data sourcing system that populates the SPSD Board Meeting Archive.

## 1. Overview
The `source_data.py` script and its supporting modules are responsible for identifying new meetings, sourcing associated documents (Agendas, Packets, Minutes), videos, and transcripts, and reconciling this data into `.njk` files for the Eleventy-based website.

## 2. Data Sources

### 2.1 Events (Meeting Metadata)
- **Primary Source:** Apptegy Events API (`https://thrillshare-cmsv2.services.thrillshare.com/api/v4/o/14619/cms/events?section_ids=249568`)
- **Secondary Source:** "Board Activities" section of the [SPSD School Board Page](https://www.spsdme.org/page/school-board).
- **Requirement:** Meeting stubs (`.njk` files) are created ONLY when a date is found in one of these official event sources.

### 2.2 Documents
- **Primary Authority:** Explicit links in the "Board Activities" table on the SPSD website.
- **Auxiliary Source:** Files discovered in the SPSD Board Meeting Google Drive folder.
- **Requirement:** Links found on the website take precedence over heuristic filename matching from Google Drive.

### 2.3 Videos
- **Source:** SPC TV Vimeo channel, tracked via `vimeo_master_list.json`.
- **Requirement:** Mapped to meetings based on date patterns in the video title (e.g., `20240108`).

### 2.4 Transcripts
- **Source:** Local `static/transcripts/` or Google Drive Transcript folder.
- **Requirement:** Mapped to meetings based on date patterns in the filename.

## 3. Mapping & Classification Logic

### 3.1 Date Normalization
All sources must be normalized to a `YYYY-MM-DD` slug to serve as the unique identifier for a meeting.

### 3.2 Strict Authority Rule
If a document or transcript date is discovered but does NOT match an official Event date, it is ignored by the automated system (it is not used to create a "ghost" meeting).

### 3.3 Document Classification
Documents are classified into exactly one of the following types:
- `agenda`: Formal meeting agenda.
- `packet`: Supporting materials/meeting packet.
- `minutes`: Approved meeting minutes.
- `misc`: Any other document (e.g., presentation slides, supplemental handouts).

Classification priority:
1.  **Site Table Column:** If the link is found in the "Agenda" column on the website, it is an `agenda`.
2.  **Filename Keywords:** If sourced from Drive, use keyword matching (e.g., "packet" -> `packet`, "minute" -> `minutes`).
3.  **Fallback:** Default to `misc`.

## 4. Reconciler Behavior
- **Stubs:** Create new `.njk` files for new dates found in event sources.
- **Merging:** Add new documents or videos to the `docs` array in existing `.njk` front matter.
- **Deduplication:** Deduplicate documents by URL.
- **Persistence:** Update `master_material_map.json` after each run.
