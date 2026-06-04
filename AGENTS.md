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
`heading`, `breadcrumb`, `meeting_tag`, `display_date`, `day_of_week`, `time`, `location`, `duration`, `prev`, `next`.

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

## Adding a new stub meeting

1. Add an entry to `src/_data/meetings.json` (set `stub: true`, `topics: []`, `doc_count: 0`).
2. Create `src/meetings/YYYY-MM-DD.njk` with front matter (including attendance placeholders) and an empty body.
3. Update `prev`/`next` slugs on the adjacent meeting files.
4. Run `npm run build`.

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
- **Meeting Metadata:** [Apptegy Events API](https://thrillshare-cmsv2.services.thrillshare.com/api/v4/o/14619/cms/events?section_ids=249568).
- **Video:** [SPC TV Vimeo channel](https://vimeo.com/spctv). Deep links use `#t=[seconds]s`.

---

## Media Conventions

### Vimeo Deep-Linking

Timestamp deep links use `#t=[SECONDS]s` (e.g. `https://vimeo.com/12345678#t=300s`). Convert VTT cue times: `(H × 3600) + (M × 60) + S`. Verify a link on the live page when processing a new meeting.
