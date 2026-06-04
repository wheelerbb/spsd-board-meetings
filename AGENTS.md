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
    YYYY-MM-DD.njk  ← one file per meeting; front matter drives sidebar
```

## Page types

**Index** (`src/index.njk`) — hero, year-filter bar (client-side JS), grid of meeting cards. Loops `years` × `meetings | where("school_year", sy)`.

**Meeting detail** (`src/meetings/YYYY-MM-DD.njk`) — front matter defines everything the layout needs (heading, sidebar docs, prev/next nav, video/transcript URLs). The template body is only the main column content: summary, timeline, votes. Stub pages have placeholder text bodies; full pages have the real HTML.

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

### Custom Eleventy filters (`.eleventy.js`)

- `where(array, key, value)` — filters meetings by a field value
- `docIcon(type)` — maps `agenda`→`AGD`, `min`→`MIN`, `pdf`→`PDF` for sidebar badges

## Meeting front matter keys

| Key | Used by |
|---|---|
| `heading` | `<h1>` in meeting header; supports `<br>` and HTML entities via `\| safe` |
| `breadcrumb` | base.njk nav right side |
| `meeting_tag`, `display_date`, `day_of_week`, `time`, `location`, `duration` | meeting header metadata |
| `has_video`, `video_url` | video sidebar card |
| `has_transcript`, `drive_url` | Google Drive transcript card |
| `docs[]` | meeting materials sidebar; each doc: `{ type, label, size, url }` |
| `prev`, `next` | navigation card; each: `{ slug, label }` or `null` |
| `stub` | controls copy in meeting.njk and "Highlights" vs "No transcript" on index cards |

## Design system

CSS custom properties in `assets/style.css`:
```
--navy: #0d2240   --gold: #c8963e   --gold-light: #f0c87a
--cream: #faf7f2  --warm-gray: #e8e3da  --text: #1a1a2e
--muted: #6b6b7b  --rule: #ddd6c8   --green: #2d7a4f  --green-bg: #edf7f1
```
Fonts: Playfair Display (headings) + DM Sans (body), loaded from Google Fonts.

## Adding a new stub meeting

1. Add an entry to `src/_data/meetings.json` (set `stub: true`, `topics: []`, `doc_count: 0`).
2. Create `src/meetings/YYYY-MM-DD.njk` with front matter and placeholder body.
3. Update `prev`/`next` slugs on the adjacent meeting files.
4. Run `npm run build` — the index card generates automatically.

---

## Identifying New Meetings (Automation)

The SPSD website uses the Apptegy (Thrillshare) CMS. You can identify new meetings and agendas directly via their public JSON APIs instead of scraping the HTML.

### Endpoints

- **District Calendar (Meetings):**
  `https://thrillshare-cmsv2.services.thrillshare.com/api/v4/o/14619/cms/events?section_ids=249568`
  Look for items with `"title": "School Board Meeting"` or `"Special Board Meeting"`.

- **Board Agendas/News:**
  `https://thrillshare-cmsv2.services.thrillshare.com/api/v2/s/249567/articles?filter_ids=482712`
  This returns articles tagged "School Board". New agendas often appear here with links to Google Drive packets.

### Automated Workflow

1. **Check for new dates:** Fetch the Events API. Compare `start_at` dates against `src/_data/meetings.json`.
2. **Add stubs:** For any missing dates, create the `.njk` stub and update the JSON.
3. **Check for agendas:** Fetch the Articles API. Match article titles/dates to existing stubs. Extract Google Drive links from the `content` HTML and add to the stub's `docs[]` front matter.

---

## Processing a meeting (stub → full)

A stub has placeholder body content and `stub: true`. Processing it means sourcing materials from Google Drive, extracting content from the VTT transcript, and replacing the stub with real HTML.

### Steps

1. Source the VTT transcript and PDF materials from Google Drive (see below)
2. Read the entire VTT; extract summary, timeline highlights, and votes
3. Cross-reference proper nouns against the PDF materials (PDFs are authoritative)
4. Update `src/meetings/YYYY-MM-DD.njk` with full content
5. Update `src/_data/meetings.json`: topics, doc_count, `stub: false`

### Google Drive structure

Two separate folder hierarchies, both owned by `web@spsdme.org`:

**Transcripts (VTT files)**
```
SchoolBoardMeetingTranscripts/  (id: 1qKRujrhJd1c8BW94A7-QGg9epc4y6n4g)
  └── {YYYY}/                   (e.g., id: 1FMNBEhd-NCgaGyJkteSB10bNXJHHWrYi for "2026")
        └── MM.DD.YY.vtt        (e.g., 05.11.26.vtt)
```

VTT files cannot be read via `read_file_content` (unsupported MIME type). Use `download_file_content` instead, then decode the base64 `content` field:

```bash
python3 -c "import sys,json,base64; d=json.load(sys.stdin); print(base64.b64decode(d['content']).decode('utf-8'))" \
  < downloaded.json > meeting.vtt
```

**Meeting materials (packets, slides)**
```
{YYYY.MM Month}/               (e.g., "2026.05 May", id: 1_KF4LHL2Iyq5Sed-XJfJpCx2xfDpTFCV)
  └── MM.DD.YY/                (e.g., "05.11.26",    id: 1d5WbtRFHwDlr_QYfqfYG3iiA4tSldQrO)
        ├── Month YYYY Meeting Packet.pdf   (e.g., "May 2026 Meeting Packet.pdf")
        └── Board Slides M.DD.YY.pdf        (e.g., "Board Slides 5.11.26.pdf")
```

PDF metadata (including a content snippet) is readable via `get_file_metadata`. Full content via `read_file_content`. The snippet is usually sufficient for name-checking.

Use `search_files` with a date-pattern query (e.g., `"05.11.26"`) when a direct file ID is not provided.

### Source precedence

VTT transcription makes errors on proper nouns. Always cross-check against PDFs.

| Data | Primary source | Fallback |
|---|---|---|
| Speaker names, scholar names | Meeting packet / slides PDF | VTT |
| Vote motions and movers | Meeting packet / slides PDF | VTT |
| Timestamps | VTT | — |
| Topics, discussion substance | VTT | Packet agenda |

### What to extract from the VTT

Read the entire VTT before summarizing — typically 4,000–6,000 lines. Extract:

- **Summary** — one bullet per major topic, past tense, third person. Format: `<strong>Topic Label:</strong> 1–2 sentences.` Aim for 5–8 bullets.
- **Topics** — 5–8 short strings for `meetings.json` `topics[]` (e.g., `"FY2027 Budget"`)
- **Timeline highlights** — 8–15 key moments with cue start times (HH:MM:SS), topic label, 1–2 sentence description, speaker if identifiable. Each entry gets a "Jump to recording" deep link — convert the cue time to seconds and append `?start=SECONDS` to the media URL (see body template below).
- **Votes and actions** — each formal motion: what was voted on, result, mover/seconder

### Front matter changes

- `stub: false`
- `duration: "H hr MM min"` (from VTT first to last cue time)
- `video_url` — update from the generic playlist URL to the specific media URL (see TelVue section below)
- `docs:` — replace placeholder entries with real Google Drive links; keep `has_transcript: false` unless a human-edited transcript document exists in Drive

**Doc types** (controls the sidebar badge):

| `type` | Badge | Use for |
|---|---|---|
| `agenda` | AGD | Meeting packet / agenda |
| `min` | MIN | Approved minutes |
| `pdf` | PDF | Slides, supplemental reports |

Doc entry format: `{ type: agenda, label: "Meeting Packet — May 11, 2026", size: "PDF · 2.6 MB", url: "..." }`

Get file size from `get_file_metadata` → `fileSize` (bytes); convert to KB or MB.

### Body template

```html
<div class="content-block">
  <div class="section-head">Meeting Summary</div>
  <div class="summary-intro">
    <ul class="summary-bullets">
      <li><strong>Topic Label:</strong> 1–2 sentence description.</li>
      <li><strong>Topic Label:</strong> 1–2 sentence description.</li>
    </ul>
  </div>
</div>

<div class="content-block">
  <div class="section-head">Transcript Highlights with Timestamps</div>
  <ul class="timeline">
    <li class="tl-item">
      <div class="tl-time">H:MM:SS</div>
      <div class="tl-body">
        <div class="tl-topic">[Topic label]</div>
        <div class="tl-desc">[1–2 sentence description]</div>
        <a href="[video_media_url]?start=[seconds]" target="_blank" class="tl-link"><svg viewBox="0 0 16 16" fill="currentColor"><path d="M8 2a6 6 0 1 0 0 12A6 6 0 0 0 8 2zm-1.5 4l4 2-4 2V6z"/></svg> Jump to recording</a>
      </div>
    </li>
  </ul>
</div>

<div class="content-block">
  <div class="section-head">Votes &amp; Actions</div>
  <table class="vote-table">
    <thead>
      <tr><th>Motion</th><th>Result</th><th>Moved / Seconded</th></tr>
    </thead>
    <tbody>
      <tr>
        <td>[Motion description]</td>
        <td><span class="vote-pass">Unanimous</span></td>
        <td class="vote-tally">Smith / Feller</td>
      </tr>
    </tbody>
  </table>
</div>
```

Use `vote-pass` (green) or `vote-fail` (red) on the result span. For roll-call votes, put the tally in the result cell (e.g., `5–2`).

Note: `.tl-link` is currently `display:none` pending confirmation of TelVue's `?start=` parameter support. The markup should still be written — re-enabling is a one-line CSS change.

### Finding the TelVue media URL

The generic playlist URL used on stub pages is:
```
https://videoplayer.telvue.com/player/NzN-Z2CpIDNbXMWB16nIzGKjRlHJozGq/playlists/4004
```

Each recording has a direct media URL for use as `video_url` and in deep links:
```
https://videoplayer.telvue.com/player/NzN-Z2CpIDNbXMWB16nIzGKjRlHJozGq/playlists/4004/media/[MEDIA_ID]
```

To find the media ID: fetch the playlist URL and match the video title by date.

Timestamp deep links use `?start=SECONDS` (integer). Convert VTT cue times: `(H × 3600) + (M × 60) + S`. The `?start=` parameter is unconfirmed in TelVue's public docs — verify a link on the live page when processing a new meeting.
