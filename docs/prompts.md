# Prompt Templates

Reference for all Gemini prompt templates used in the pipeline. Code retains the canonical strings; this document is the human-readable spec for reviewing and iterating on them.

---

## 1. Transcript Extraction (`process_transcripts.py`)

**Script:** `scripts/process_transcripts.py → process_single_meeting()`  
**Model:** `gemini-2.5-flash` (falls back to `gemini-2.5-pro` on rate limit)  
**Temperature:** 0.1  
**Output:** Structured JSON via `MeetingReport` Pydantic schema

### Injected context

- **GLOSSARY** — hardcoded proper noun corrections (Kaler, Skillin, Angela Atkinson Duina, etc.)
- **Canonical terms from official docs** — extracted from GCS cache (`official_docs/{slug}/`) when available; appended to glossary as "use this exact spelling" hints
- **Existing topic list** — `src/_data/topics.json` injected as `allowed_tags` so Gemini reuses known tags

### Prompt template

```
Analyze the school board meeting transcript for {date_slug}.
Extract: blurb, formal votes, high-level summary bullets, timestamped timeline, and topic tags.

IMPORTANT: Identify perspectives from: Board, Administration, Teachers, Citizens.
Glossary: {glossary_text}

Guidelines:
- Blurb: 1-2 sentence hook for the landing page.
- Tags: Identify 3-5 specific, time-bound or scoped topic tags (e.g., '2026 Equity Policy Update'
  instead of 'Equity', 'FY26 Transportation Challenges' instead of 'Transportation'). Avoid broad,
  generic nouns unless referring to a standing systemic issue (like 'Reconfiguration'). Use
  {allowed_tags} to reuse existing specific tags where appropriate.
- Votes: Exact motion, result, count, and movers.
- Summary: 5-8 bullets showing the arc of conversation.
- Timeline: 10-15 key moments with timestamps (H:MM:SS) and total seconds.
- Board Attendance: Extract the roll call. For each person called, record name, status
  (Present or Absent), and role (Board or Student Rep).

Transcript:
{transcript}
```

### Output schema (`MeetingReport`)

```python
blurb: str
tags: list[str]                      # becomes topics: in .njk frontmatter
votes: list[Vote]                    # {motion, result, count, moved_2nd}
summary: list[SummaryItem]           # {topic, text}
timeline: list[TimelineItem]         # {time, seconds, topic, desc}
board_attendance: list[AttendanceMember]  # {name, status, role}
```

---

## 2. Topic Synthesis (`post_process.py`)

**Script:** `scripts/post_process.py → synthesize_topic()`  
**Model:** `gemini-2.5-flash`  
**Temperature:** 0.1  
**Output:** JSON object (parsed manually — not Pydantic)

### Prompt template

```
You are a policy analyst for the SPSD Board Meeting Archive.
Synthesize the following chronological notes (NEWEST FIRST) regarding the topic: '{topic}'.

Return ONLY a JSON object with these exact keys — no markdown fences, no commentary:
{
  "current_status": "1-2 plain sentences (no markdown) summarizing where things stand right now. Card-ready.",
  "overview": "2-3 paragraphs with natural citations to meeting dates (e.g. 'On {display_date},
    the board decided...'). The first paragraph MUST cover the most recent developments.
    If a prior decision was reversed or modified, reflect that clearly.",
  "perspectives": {
    "Board": "Summary of elected members' stance and questions. Omit key entirely if no data.",
    "Administration": "Summary of Superintendent/Directors' recommendations. Omit key entirely if no data.",
    "Staff": "Summary of staff and union rep viewpoints. Omit key entirely if no data.",
    "Citizens": "Summary of public comment and parent viewpoints. Omit key entirely if no data."
  }
}

RULES:
- Omit any perspective key where there is genuinely no evidence in the notes.
- Use "Staff" (not "Teachers") for the staff/union group.
- SPELING: Kaler (NOT Caler), Skillin (NOT Skillen).

Evidence (Newest First):
{evidence}
```

### Evidence format

Up to 15 most recent meeting chunks, joined by `---`. Each chunk:
```
Meeting: {display_date} ({meeting_url})
- {summary_bullet_text}
- {summary_bullet_text}
```

### Cache strategy

MD5 hash of the evidence string stored in `scripts/topic_hashes.json`. Synthesis is skipped when hash is unchanged AND an existing dict-format summary exists.

---

## 3. Official Document Term Extraction (`post_process.py`)

**Script:** `scripts/post_process.py → _extract_official_terms()`  
**Model:** `gemini-2.5-flash`  
**Temperature:** 0.0  
**Output:** JSON array (parsed manually)

### Purpose

Extracts canonical proper noun spellings from agenda/packet/minutes PDFs stored in Drive, caches results in GCS, and injects them into the transcript extraction prompt as authoritative spelling hints.

### Prompt template

```
Extract all proper nouns from this school board meeting document. Include:
- People with official roles (board members, administrators, staff)
- Named places (schools, buildings, streets, districts)
- Organizations and associations (unions, parent groups, state agencies)
- Named programs, policies, or initiatives

Return ONLY a JSON array:
[{"term": "...", "type": "person|place|organization|program", "context": "short description or role"}]

Use the exact spelling from the document. Return [] if nothing qualifies.

Document type: {doc_type}
Document text:
{text}
```

### GCS cache paths

```
gs://{bucket}/official_docs/{slug}/{doc_type}-{file_id}/{modifiedTime}.txt   ← raw text
gs://{bucket}/official_docs/{slug}/{doc_type}-{file_id}/{modifiedTime}.json  ← extracted terms
```

---

## 4. Agenda Preview (`post_process.py`)

**Script:** `scripts/post_process.py → generate_agenda_preview()`  
**Model:** `gemini-2.5-flash`  
**Temperature:** 0.1  
**Output:** HTML string (stored in `.njk` frontmatter as `agenda_preview`)

### Purpose

Generates a `<ul>` preview of upcoming meeting agenda items for meetings that have a packet or agenda doc but haven't been processed yet (stub meetings).

### Prompt template

```
You are summarizing a school board meeting agenda for a public web archive.
Output ONLY valid HTML — no prose, no markdown, no code fences.

Format:
<ul class="agenda-preview-list">
<li><strong>Topic:</strong> Brief detail (one sentence).</li>
</ul>

Rules:
- 3-6 items only
- Skip boilerplate: Call to Order, Pledge of Allegiance, Opening Statement,
  Public Comment, Adjournment, generic committee report headers with no named item
- Include: substantive votes, named policy items, key personnel changes,
  grants/donations, notable field trips or events, workshops with a stated topic

Agenda text:
{text}
```

---

## 5. Blurb Generation (`post_process.py`)

**Script:** `scripts/post_process.py → generate_blurb()`  
**Model:** `gemini-2.5-flash`  
**Temperature:** 0.1  
**Output:** Plain text string (stored in `.njk` frontmatter as `blurb`)

### Purpose

Generates a 1–2 sentence meeting blurb from the existing summary bullets when one is missing on a fully processed meeting.

### Prompt template

```
Write an extremely concise 1-2 sentence objective summary (a 'blurb') of this school board
meeting based on these notes. Do not use quotes or introductory filler:

- {summary_bullet_1}
- {summary_bullet_2}
...
```
