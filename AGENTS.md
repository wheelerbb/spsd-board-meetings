# AGENTS.md

Guidance for AI agents processing SPSD board meeting content into this archive.

## What "processing a meeting" means

A meeting starts as a stub (`stub: true` in `meetings.json`, placeholder body in `src/meetings/YYYY-MM-DD.njk`). Processing it means:

1. Sourcing the transcript and materials from Google Drive
2. Extracting summary, timeline highlights, and vote record from the VTT
3. Cross-referencing proper nouns against the PDF materials
4. Replacing stub content in the `.njk` file with full HTML
5. Updating `meetings.json` with topics, doc_count, and `stub: false`

## Google Drive structure

Two separate folder hierarchies, both owned by `web@spsdme.org`:

### Transcripts (VTT files)

```
SchoolBoardMeetingTranscripts/  (id: 1qKRujrhJd1c8BW94A7-QGg9epc4y6n4g)
  └── {YYYY}/                   (e.g., id: 1FMNBEhd-NCgaGyJkteSB10bNXJHHWrYi for "2026")
        └── MM.DD.YY.vtt        (e.g., 05.11.26.vtt)
```

VTT files are plain-text WebVTT caption files. They cannot be read via `read_file_content` (unsupported MIME type) — use `download_file_content` instead, then decode the base64 `content` field:

```bash
python3 -c "import sys,json,base64; d=json.load(sys.stdin); print(base64.b64decode(d['content']).decode('utf-8'))" \
  < downloaded.json > meeting.vtt
```

### Meeting materials (packets, slides)

```
{YYYY.MM Month}/               (e.g., "2026.05 May", id: 1_KF4LHL2Iyq5Sed-XJfJpCx2xfDpTFCV)
  └── MM.DD.YY/                (e.g., "05.11.26",    id: 1d5WbtRFHwDlr_QYfqfYG3iiA4tSldQrO)
        ├── Month YYYY Meeting Packet.pdf   (e.g., "May 2026 Meeting Packet.pdf")
        └── Board Slides M.DD.YY.pdf        (e.g., "Board Slides 5.11.26.pdf")
```

PDF metadata (including a content snippet) is readable via `get_file_metadata`. Full content via `read_file_content` (PDFs are supported). The snippet is usually sufficient for name-checking.

### Finding files by date

Use `search_files` with a date-pattern query (e.g., `"05.11.26"`) when a direct file ID is not provided. Match the result title against the naming conventions above.

## Source precedence

VTT transcription makes errors on proper nouns (names, titles). Always cross-check against the meeting packet or slides PDF, which are the authoritative source.

| Data | Primary source | Fallback |
|---|---|---|
| Speaker names, scholar names | Meeting packet / slides PDF | VTT |
| Vote motions and movers | Meeting packet / slides PDF | VTT |
| Timestamps | VTT | — |
| Topics, discussion substance | VTT | Packet agenda |

## What to extract from the VTT

Read the entire VTT before summarizing — it is typically 4,000–6,000 lines. Extract:

- **Summary** — one bullet per major topic, past tense, third person. Format: `<strong>Topic Label:</strong> 1–2 sentences.` Aim for 5–8 bullets covering the meeting's main threads.
- **Topics** — 5–8 short strings for `meetings.json` `topics[]` (e.g., `"FY2027 Budget"`)
- **Timeline highlights** — 8–15 key moments with cue start times (HH:MM:SS), topic label, 1–2 sentence description, speaker if identifiable. Each entry gets a "Jump to recording" deep link — convert the cue time to seconds and append `?start=SECONDS` to the media URL (see template below).
- **Votes and actions** — each formal motion: what was voted on, result, mover/seconder

## File changes

### `src/_data/meetings.json`

Update the meeting's entry:

```json
{
  "topics": ["Topic One", "Topic Two", ...],
  "doc_count": 2,
  "stub": false
}
```

`doc_count` = number of real docs added to the `.njk` front matter (not placeholder links).

### `src/meetings/YYYY-MM-DD.njk`

**Front matter changes:**
- `stub: false`
- `duration: "H hr MM min"` (from VTT cue times — first to last)
- `docs:` — replace placeholder entries with real Google Drive links
- Keep `has_transcript: false` unless a human-edited transcript document exists in Drive

**Doc types** (controls the sidebar badge):

| `type` value | Badge | Use for |
|---|---|---|
| `agenda` | AGD | Meeting packet / agenda |
| `min` | MIN | Approved minutes |
| `pdf` | PDF | Slides, supplemental reports |

Doc entry format: `{ type: agenda, label: "Meeting Packet — May 11, 2026", size: "PDF · 2.6 MB", url: "..." }`

Get the file size from `get_file_metadata` → `fileSize` (bytes); convert to KB or MB.

**Body structure** — three `content-block` divs in order:

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
    ...
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

## Finding the TelVue media URL

The playlist URL (used as `video_url` on stub pages) is generic. Each recording has a direct media URL needed for timestamp deep links:

```
https://videoplayer.telvue.com/player/NzN-Z2CpIDNbXMWB16nIzGKjRlHJozGq/playlists/4004/media/[MEDIA_ID]
```

To find the media ID: fetch `https://videoplayer.telvue.com/player/NzN-Z2CpIDNbXMWB16nIzGKjRlHJozGq/playlists/4004` and match the video title by date. Use that media URL as `video_url` in the front matter and as the base for all `tl-link` hrefs.

Timestamp deep links use `?start=SECONDS` (seconds as an integer). Convert VTT cue times: `(H × 3600) + (M × 60) + S`. The `?start=` parameter is unconfirmed in TelVue's public docs — verify a link works on the live page when processing a new meeting.

## Build and verify

After edits, run:

```bash
export PATH="/Users/wboyd-boffa/Library/Application Support/Zed/node/node-v24.11.0-darwin-arm64/bin:$PATH"
npm run build
```

Zero errors expected. The meeting page renders at `_site/meetings/YYYY-MM-DD/index.html`.
