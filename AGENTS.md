# AGENTS.md

General guidance for AI agents working in this repository.

## Project overview

Eleventy 3.x static site archiving South Portland School Department (SPSD) board meetings. Input: `src/`, output: `_site/`. Build command: `npm run build`.

## Architecture

```
src/
  _data/
    meetings.json   ← source of truth for all meeting metadata
    years.js        ← derives unique year list for index grouping
  _includes/layouts/
    base.njk        ← HTML shell: Google Fonts, stylesheet, nav, footer
    meeting.njk     ← extends base; renders meeting header + two-column layout
  assets/style.css  ← single shared stylesheet (passthrough-copied to _site/)
  index.njk         ← archive listing; loops over years/meetings from _data
  meetings/
    YYYY-MM-DD.njk  ← one file per meeting; uses structured front matter
```

## Data model

### `meetings.json` fields

| Field | Type | Notes |
|---|---|---|
| `slug` | string | `YYYY-MM-DD` |
| `school_year` | string | `"2025-2026"` |
| `date` | string | ISO date, used for sorting |
| `display_date` | string | `"May 11, 2026"` |
| `day_of_week` | string | `"Monday"` |
| `type` | string | `"Regular"`, `"Special"`, `"Workshop"`, `"Exec. Session"` |
| `title` | string | Short meeting title |
| `topics` | string[] | 5–8 topic strings; empty `[]` on stubs |
| `doc_count` | number | Count of real docs in the `.njk` front matter |
| `has_video` | bool | |
| `has_transcript` | bool | True only if a human-edited transcript doc exists in Drive |
| `stub` | bool | `true` until the meeting is fully processed |

## Meeting front matter (Structured Data)

To keep meeting files maintainable, use structured data in the YAML front matter instead of manual HTML in the body. The layout handles rendering automatically.

### Header & Navigation
`heading`, `breadcrumb`, `meeting_tag`, `display_date`, `day_of_week`, `time`, `location`, `duration`.

### Media & Attendance
`has_video`, `video_url`, `board_attendance`.

### Documents
`docs[]`: each doc: `{ type, label, size, url }`. 
Standard `type` values: `agenda`, `packet`, `min`, `pdf`, `xlsx`, `pptx`.

### Structured Content
```yaml
# 1. Votes & Actions
votes:
  - motion: "Formal motion text"
    result: "Pass" # or "Fail"
    count: "Unan." # or "7-0"
    moved_2nd: "Mover / Seconder"

# 2. Meeting Summary
summary:
  - topic: "Topic Label"
    text: "1-2 sentence description."

# 3. Transcript Highlights
timeline:
  - time: "H:MM:SS"
    seconds: 300 # Total seconds for deep link
    topic: "Topic Label"
    desc: "1-2 sentence description."
```

## Technical Processing Pipeline

The archive uses a two-stage modular pipeline for ingesting and synthesizing board meeting data.

### 1. Ingestion: `process_transcripts.py`
This tool analyzes raw `.vtt` transcripts using Gemini (Vertex AI or AI Studio).
- **Authentication:** Supports `--local-auth` (Vertex AI ADC) or standard API keys.
- **Concurrency:** Uses a ThreadPoolExecutor for parallel processing.
- **Discrete Outputs:** The LLM is prompted via a Pydantic schema to extract the following exact fields:
  - `blurb`: A 1-2 sentence hook summarizing the primary outcome for the landing page.
  - `tags` (Topics): 3-5 high-level, specific, time-bound tags (e.g., 'FY2026 Equity Policy').
  - `votes`: Exact motion text, result, vote count, and movers.
  - `summary`: 5-8 bullets reflecting the arc of conversation, with explicit tracking of viewpoints from the Board, Administration, Teachers, and Citizens.
  - `timeline`: 10-15 key moments with timestamps (H:MM:SS) to support video deep-linking.

### 2. Synthesis: `post_process.py`
This maintenance tool synchronizes the global metadata and generates high-level thematic content.
- **Topic Taxonomy:** Sorts the global `topics.json` library by recent activity (newest first). Filters out generic blacklisted tags (e.g., "Personnel").
- **Missing Blurbs:** Automatically generates blurbs for older meetings that lack them.
- **Evidence Caching:** Hashes the chronological evidence for each topic. If the hash hasn't changed, the API call is skipped to save credits.
- **Topic Explorer Generation:** Synthesizes chronological evidence (fed newest-first) into a 'Current Status & Evolution' summary for each topic.

---

## Conventions & Standards

### Perspective Grouping
When summarizing meeting discussions, identify viewpoints from these four groups to provide a balanced overview:
1. **Board:** Elected members, their questions, and policy direction.
2. **Administration:** The Superintendent and Directors; focus on recommendations and operational reports.
3. **Teachers:** Staff members and Union representatives; focus on classroom impact and contract concerns.
4. **Citizens:** Public comment, parents, and community members.

### Topic Identification
- **Specificity:** Topics MUST be specific, contextual, and ideally time-bound. Avoid generic nouns like "Transportation" or "Equity" unless they represent a standing, systemic issue spanning multiple years.
- **Examples:** Use "FY26 Transportation Challenges" instead of "Transportation"; "2026 Equity Policy Update" instead of "Equity".
- **Consistency:** Use `src/_data/topics.json` as the authoritative list. Re-use existing specific tags where appropriate.

### SPELING & Terminology
- **Schools:** Kaler, Dyer, Skillin, Brown, Small, Mahoney, Memorial.
- **Groups:** SPESPA (Support Professionals), SPTA (Teachers).
- **Phonetic Fixes:** Raw VTT files often misspell local names; always verify against the official Meeting Minutes PDF.

---

## Adding a new stub meeting

1. Add an entry to `src/_data/meetings.json` (set `stub: true`, `topics: []`, `doc_count: 0`).
2. Create `src/meetings/YYYY-MM-DD.njk` with front matter (including attendance placeholders) and an empty body.
3. Run `npm run build` — prev/next navigation is derived automatically at build time (no manual updates needed).

---

## Identifying New Meetings (Automation)

The SPSD website uses the Apptegy (Thrillshare) CMS. You can identify new meetings and agendas directly via their public JSON APIs.

### Endpoints

- **District Calendar (Meetings):**
  `https://thrillshare-cmsv2.services.thrillshare.com/api/v4/o/14619/cms/events?section_ids=249568`

- **Board Agendas/News:**
  `https://thrillshare-cmsv2.services.thrillshare.com/api/v2/s/249567/articles?filter_ids=482712`

- **Documents API:**
  `https://thrillshare-cmsv2.services.thrillshare.com/api/v2/s/249570/documents`

### Automated Workflow

1. **Check for new dates:** Fetch the Events API.
2. **Add stubs:** Use the standard structure in the front matter. Set `stub: true`.
3. **Source Agendas:** Search for Google Drive links across the Articles API, the direct Board page, and the Documents API.
4. **Update Stubs:** Add found links to `docs[]` and update `doc_count` in `src/_data/meetings.json`.

---

## Processing a meeting (stub → full)

1. Source the VTT transcript and PDF materials from Google Drive.
2. Read the VTT; extract summary, timeline highlights, and votes.
3. Populate the front matter structured data (`summary`, `timeline`, `votes`).
4. Update `stub: false` and `duration`.
5. Empty the body of the `.njk` file.
6. Verify proper nouns against authoritative PDFs.

---

### Sources of Truth

- **Board Attendance:** Official [Board Members page](https://www.spsdme.org/page/members-of-the-board) and **Meeting Minutes** PDF.
- **Meeting Minutes:** **AUTHORITATIVE SOURCE.** Content from official Minutes (Votes, Actions, formal Summaries) MUST supersede and/or replace content extracted from raw transcripts. When processing a meeting, always prioritize the PDF Minutes if available.
- **Meeting Metadata:** [Apptegy Events API](https://thrillshare-cmsv2.services.thrillshare.com/api/v4/o/14619/cms/events?section_ids=249568).
- **Video:** [SPC TV Vimeo channel](https://vimeo.com/spctv). Deep links use `#t=[seconds]s`.
- **Transcripts:** Download raw `.vtt` files from the [SchoolBoardMeetingTranscripts Google Drive Folder](https://drive.google.com/drive/folders/1qKRujrhJd1c8BW94A7-QGg9epc4y6n4g?usp=drive_link). Transcript-sourced data is for supplemental highlights and deep-linking only.

---

## Media Conventions

### Vimeo Deep-Linking

Timestamp deep links use `#t=[SECONDS]s` (e.g. `https://vimeo.com/12345678#t=300s`). Convert VTT cue times: `(H × 3600) + (M × 60) + S`. Verify a link on the live page when processing a new meeting.
