# Prompt Templates

Reference for all Gemini prompt templates used in the pipeline. Code retains the canonical strings; this document is the human-readable spec for reviewing and iterating on them.

---

## 1. Transcript Extraction (`process_transcripts.py`)

**Script:** `scripts/process_transcripts.py → process_single_meeting()`  
**Model:** `DEFAULT_MODEL` (`gemini-2.5-pro`), falling back to `BACKUP_MODEL` (`gemini-3.5-flash`) on a 429 rate-limit — see `scripts/model_config.py`, shared with `post_process.py`.  
**Temperature:** 0.1  
**Output:** Structured JSON via `MeetingReport` Pydantic schema

### Injected context

- **Static glossary** (`src/_data/glossary.json`) — hand-maintained spelling corrections only (school names, acronyms, name misspellings) that hold true for the entire archive regardless of meeting date. Rendered into prompt-hint text by `scripts/glossary_utils.py::render_glossary_hints()`. Deliberately contains no time-varying facts (roles, roster membership) — see §6 and `src/_data/board_members.json`.
- **Canonical terms from official docs** — extracted from GCS cache (`official_docs/{slug}/`) when available; appended to the static glossary as "use this exact spelling" hints, per-meeting.

Note: this script does **not** generate topic tags — that moved to `post_process.py`'s `generate_tags()` / `batch_tag_all_meetings()` (§2/§3 below) when tagging became summary-based. Do not reintroduce a `tags`/`allowed_tags` field here; `all_topics.json` is owned by `post_process.py`.

### Prompt template

```
Analyze the school board meeting transcript for {date_slug}.
Extract: blurb, formal votes, high-level summary bullets, timestamped timeline, and board attendance.

IMPORTANT: Identify perspectives from: Board, Administration, Teachers, Citizens.
Glossary: {glossary_text}

Guidelines:
- Blurb: 1-2 sentence hook for the landing page.
- Votes: Exact motion, result (use "Passed" or "Failed" — a unanimous vote is "Passed"), count, and movers.
- Summary: 5-8 bullets capturing the high-level arc of the meeting. Topics should be
  issue-level (e.g. "FY2026 Budget Update", "Cell Phone Policy") not speaker-level.
  Do not create separate bullets per speaker; perspectives can be noted briefly within
  a bullet's text if important.
- Timeline: 20-30 entries covering the full meeting arc with timestamps (H:MM:SS) and
  total seconds. Use children[] for grouped speaker sections:
  - Public comment periods: one parent entry (topic: "Public Comment", seconds at period start,
    desc summarizing the period) with one child per NAMED commenter (seconds at their start,
    speaker full name, text 2-3 sentences). Unnamed speakers described in parent desc only.
  - Board discussion sections where multiple members speak: one parent entry
    (topic: "Board Discussion on [Subject]") with one child per member who speaks substantively.
  - All other entries: flat (empty children).
  Topic format: "Description (Speaker)" for flat entries — no possessive constructions
  (use "Reconfiguration Priorities (Daniel Feller)" not "Feller's Reconfiguration Priorities").
  Each desc/text should be 2-3 sentences of substance.
- Board Attendance: Extract the roll call. For each person called, record name, status
  (Present or Absent), and role — use exactly "Board" for board members and "Student Rep"
  for student representatives.

Transcript:
{transcript}
```

### Output schema (`MeetingReport`)

```python
blurb: str
votes: list[Vote]                    # {motion, result, count, moved_2nd}
summary: list[SummaryItem]           # {topic, text}
timeline: list[TimelineItem]         # {time, seconds, topic, desc}
board_attendance: list[AttendanceMember]  # {name, status, role}
```

---

## 2. Topic Tag Generation — Single Meeting (`post_process.py`)

**Script:** `scripts/post_process.py → generate_tags()`
**Model:** `DEFAULT_MODEL` (`gemini-2.5-pro`), falling back to `BACKUP_MODEL` (`gemini-3.5-flash`) on a 429 rate-limit — see `scripts/model_config.py`.
**Temperature:** default (no `temperature` override in this call's config)
**Output:** JSON array of `{tag, evidence_bullets}` objects (parsed manually — not Pydantic), then post-processed by `_normalize_fy()` and `_strip_modifiers()`. Returns `(tags, evidence)` where `evidence` is `{tag: [summary_bullet_index, ...]}`.
**Called from:** the incremental (non-`--retag`) branch of `post_process()`'s tagging step — one call per meeting that doesn't yet have `topics:`, in chronological order. Also used by `--dry-run-tag` for testing.

### Injected context

- **Existing tags + `current_status`** (`topic_context`) — every tag in `all_topics.json`, paired with its synthesized `current_status` blurb from `topic_summaries.json` when one exists, so the model can judge topical fit by what a tag actually covers, not just its name.
- **Most recent prior meeting's tags** (`recent_topics`) — signals a likely-continuing thread.
- **Agenda excerpt** (`agenda_text`) — from `_load_cached_agenda_text()` (§4), district-authored item framing independent of the Gemini-generated summary.

### Prompt template

```
You are tagging a school board meeting. Given the meeting summary below, identify 3-5 topic tags for the PRIMARY issues discussed.

**First-order rule:** Only tag a topic if it received substantial, independent discussion — not just a passing mention or as context within another topic. Ask: "Would a reader coming to this meeting specifically for this topic find meaningful content?" If not, omit the tag.

**Existing tags** (name: what it currently covers, when known):
{allowed_text}

**Topics discussed at the board's most recent prior meeting:** {recent_text}
If this meeting's content continues one of those threads, that's a strong signal to reuse the same tag rather than coin a new one — even if the wording of this meeting's bullets doesn't closely match the tag's name. Use the "what it currently covers" descriptions above, not just name-matching, to judge whether an existing tag fits.
{agenda_section}
**Tag selection rules, in priority order:**
1. **Reuse an existing tag** when it accurately describes the topic — judge this by what the tag covers (its description above and the recent-meeting context), not by superficial wording overlap with the tag's name.
2. **Time-binding — 3 categories.** Whether (and how granularly) a tag carries a year/FY depends on what kind of issue it is:
   - **Cyclical** (budget cycles, audits, bargaining rounds, calendar approval): time-bound, but **one tag per fiscal year, period**. If a tag already exists for this fiscal year's instance of the cycle (e.g. an existing "FY27 Budget"), reuse it — do NOT create a new tag for a different phase or sub-event within the same cycle (no separate "FY27 Budget Approval" / "FY27 Budget Development" / "FY27 Budget Challenges"). Use a small, fixed vocabulary for the theme name itself — "Budget", "Audit", "School Calendar", "Collective Bargaining" — never invent a new theme word like "Financial Deficit" or "Staffing Adjustments" for what is really just that year's budget cycle. This includes deficits, shortfalls, and funding gaps — those are always part of that year's budget story, never a standalone tag; always keep the FY prefix on them ("FY27 Budget", never bare "Financial Deficit"). This district's fiscal year is named for its ending year and runs July 1 - June 30 (e.g. FY27 = July 2026-June 2027). Budget deliberation for a fiscal year typically happens in the spring before it starts (roughly January-June) — a budget-related tag from that window usually belongs to the UPCOMING fiscal year, not the one about to end. Occasionally a district starts budget planning for a later fiscal year unusually early (e.g. discussing next year's budget in December instead of the following spring) — when the content clearly indicates that, tag it with the fiscal year actually being discussed, not the one implied by a rigid calendar cutoff.
   - **Discrete initiatives** (a specific search, closure, or policy rollout): bind to a year/era only when the same type of event could plausibly recur later and future disambiguation will matter (e.g. "Superintendent Search", "Student Cell Phone Policy 2026").
   - **Evergreen** (standing, systemic domains with no natural end — special ed, transportation, facilities, equity, governance): never time-bound.
3. **Subject, not process stage.** A tag names the SUBJECT being discussed, never what stage of board deliberation it's at. Words like "Development", "Presentation", "Discussion", "Update", "Overview", "Review", "Consideration", "Process", "Session", "Report", "Debate", "Announcement", "Revision(s)", "Projection" describe *where something is in the meeting cycle*, not *what the topic is* — never end a tag with one of these (e.g. "Cell Phone Policy Development" → "Cell Phone Policy"). Words naming a specific, substantive outcome — "Closure", "Adoption", "Referendum", "Resignation", "Appointment" — are fine to keep; they're not process-stage words.
4. **Create a new specific tag** when no existing tag fits. Name the specific issue, not a category — "SPESPA Contract 2025" not "Union Contracts"; "FY26 Staff Reductions" not "Property Taxes". Generic category nouns (Policy Review, Community Engagement, School Operations, Board Governance, Student Recognition) are never valid tags.
5. **Evidence relevance.** When citing evidence_bullets for a tag, cite only bullets where THIS topic is the actual subject of that bullet — not bullets that mention it only in passing while primarily discussing something else. If a topic genuinely has no bullet substantially about it, don't tag it at all.

**Format rules:** Fiscal years must use FYYY format (FY27, FY26) — never FY2027 or FY2026. No parentheses or acronyms. No concatenated words. No symbols (&, /, etc.).

Meeting: {slug}
Summary (numbered):
{summary_text}

Respond with a JSON array of objects, one per tag: [{"tag": "...", "evidence_bullets": [0, 2]}, ...]
"evidence_bullets" are the 0-indexed numbers from the Summary list above that support this tag —
every tag must cite at least one. Example:
[{"tag": "Elementary School Reconfiguration", "evidence_bullets": [0]}, {"tag": "FY27 Budget", "evidence_bullets": [1, 3]}]
```

### Evidence linkage (todos/012)

The Summary is rendered with 0-indexed bullet numbers, and the model reports which bullet(s) support each tag directly in its response — this replaces `_evidence_match()`'s fuzzy text matching for any meeting tagged after this was added. `synthesize_topic()`'s evidence-gathering step (§5) prefers a meeting's `topic_evidence` frontmatter field when present, falling back to fuzzy matching only for meetings tagged before this existed. See `docs/topic-taxonomy.md` for the frontmatter shape.

### Shared rule text (todos/005)

Rules 2–5 above (`_RULE_TIME_BINDING`, `_RULE_SUBJECT_NOT_PROCESS`, `_RULE_GENERIC_NOUN`, `_RULE_EVIDENCE_RELEVANCE` in `post_process.py`) are single Python constants interpolated into *both* this prompt and §3's batch prompt — not copy-pasted prose kept in sync by hand. If you're editing tag-quality rules, edit the constant once; it updates both tagging paths. This mirrors the shared `src/_data/topic_blacklist.json` artifact (also referenced from `_RULE_GENERIC_NOUN`'s rendered text) — the general principle for this pipeline is: topic-tagging logic that needs to behave the same way across the single-meeting and batch paths should be one shared artifact/constant, not two things a human has to remember to update together.

### Post-processing (applies to every generated tag, both here and in §3)

- **`_normalize_fy()`** — `FY2027` → `FY27`.
- **`_strip_modifiers()`** — deterministically strips a trailing generic process-stage word (`Development`, `Presentation`, `Discussion`, `Update(s)`, `Overview`, `Review(s)`, `Consideration`, `Process`, `Session`, `Report(s)`, `Debate`, `Announcement`, `Revision(s)`, `Projection(s)`; plus `Approval`/`Vote(s)` only for FY-prefixed cyclical tags), repeated until none remain. Backstops rule 3 above regardless of how well the model follows the prompt — added after a full-corpus batch run (§3) violated the modifier rule extensively despite the instruction being present, showing the constraint needed to be enforced in code, not just requested.
- **`_warn_if_budget_synonym()`** — logs (does not rewrite) when a tag matching `deficit`/`shortfall` survives without an FY prefix, so a human notices if the fiscal-year calibration text in rule 2 above didn't take. Deliberately detection-only, not an auto-fix: an earlier attempt to guess the fiscal year from the meeting's date alone was reverted after checking it against real data — the district's actual budget calendar doesn't follow a rigid month cutoff (e.g. a December 2025 meeting was legitimately discussing the *next* fiscal year's budget early), so a date formula risked silently mis-tagging exactly the cases that matter most.
- **`_drop_blacklisted()`** — removes (not just warns on) any tag that's an exact match for `src/_data/topic_blacklist.json` (`Personnel`, `Contracts`, `Finance`, `Budget`, `Policy`, `Board Governance`). Unlike the budget-synonym check, this one *does* rewrite (by dropping), because bare category nouns have no ambiguous edge case the way a mis-dated fiscal year does — a tag named exactly "Finance" is never correct regardless of context. Added after batch-mode tagging repeatedly reverted to exactly these words despite rule 4's explicit instruction not to, at large-corpus scale — see todos/005.

---

## 3. Topic Tag Generation — Batch (`post_process.py`)

**Script:** `scripts/post_process.py → batch_tag_all_meetings()`
**Model:** `DEFAULT_MODEL` (`gemini-2.5-pro`), falling back to `BACKUP_MODEL` (`gemini-3.5-flash`) on a 429 rate-limit — see `scripts/model_config.py`.
**Temperature:** 0.0
**Output:** Structured JSON via `BatchTagResponse` Pydantic schema (`{results: [{slug, tags: [{tag, evidence_bullets}]}]}`)
**Timeout:** 600s (`BATCH_TAGGING_TIMEOUT`), enforced via subprocess `SIGTERM` — same pattern as §2, just longer given the much larger input/output
**Called from:** the `--retag` branch of `post_process()`'s tagging step — one call covering every non-stub meeting with a summary, instead of looping §2's single-meeting call once per meeting. Reserved for periodic full recalibration, not the daily incremental path (full-corpus retagging on every cron run risks topic churn on a public site).

### Why batch mode exists

Tagging one meeting at a time — even with the recent-meeting hint in §2 — is structurally the wrong shape for recognizing a narrative that spans many meetings (e.g. a school closure recommended, voted on, then worked through logistically over several later meetings): the model never sees the whole story at once. Batch mode feeds every meeting's summary and agenda excerpt into a single call so the model can assign self-consistent tags across the full corpus in one pass.

### Injected context

Same shape as §2 per meeting (transcript summary + agenda excerpt from `_load_cached_agenda_text()`, §4), but for **every** meeting at once, chronologically ordered — no `allowed_tags`/`topic_context`/`recent_topics`, since the model is deciding the entire tag set directly from the full evidence rather than reusing an externally-tracked list.

### Prompt template

```
You are tagging the full history of a school board's meetings with topic tags, all at once.
For EACH meeting below, identify 3-5 topic tags for the PRIMARY issues discussed.

**First-order rule:** Only tag a topic if it received substantial, independent discussion in that meeting — not just a passing mention or as context within another topic.

**Consistency is the whole point of tagging everything at once:** if the same underlying story spans multiple meetings (e.g. a school closure that gets recommended, voted on, and then has its logistics worked out over several later meetings), use the SAME tag across all of them, even if the specific wording of each meeting's summary differs. You are the single source of truth for the entire tag set here — there is no external tag list to consult, decide it directly from the evidence across the whole corpus.

**Tag selection rules:**
1. **Reuse the same tag** for the same underlying story wherever it recurs across meetings — this is the most important rule for this pass.
2. **Time-binding — 3 categories.**
   - **Cyclical** (budget cycles, audits, bargaining rounds, calendar approval): time-bound, but **one tag per fiscal year, period** — never split a single fiscal year's cycle into phase-specific tags (no separate "FY27 Budget Approval" / "FY27 Budget Development" / "FY27 Budget Challenges" — just "FY27 Budget"). Use a small, fixed vocabulary for the theme name — "Budget", "Audit", "School Calendar", "Collective Bargaining" — never invent a new theme word like "Financial Deficit" or "Staffing Adjustments" for what is really just that year's budget cycle. This includes deficits, shortfalls, and funding gaps — those are always part of that year's budget story, never a standalone tag; always keep the FY prefix on them ("FY27 Budget", never bare "Financial Deficit"). This district's fiscal year is named for its ending year and runs July 1 - June 30 (e.g. FY27 = July 2026-June 2027). Budget deliberation for a fiscal year typically happens in the spring before it starts (roughly January-June) — a budget-related tag from that window usually belongs to the UPCOMING fiscal year, not the one about to end. Occasionally a district starts budget planning for a later fiscal year unusually early (e.g. discussing next year's budget in December instead of the following spring) — when the content clearly indicates that, tag it with the fiscal year actually being discussed, not the one implied by a rigid calendar cutoff. This rule is the one most often violated when tagging many meetings at once — before finalizing, scan your own output for every budget-adjacent tag (including ones without an FY prefix that should have one) and collapse any that share a fiscal year and theme into one.
   - **Discrete initiatives** (a specific search, closure, or policy rollout): bind to a year/era only when the same type of event could plausibly recur later and future disambiguation will matter.
   - **Evergreen** (standing, systemic domains with no natural end — special ed, transportation, facilities, equity, governance): never time-bound.
3. **Subject, not process stage.** A tag names the SUBJECT being discussed, never what stage of board deliberation it's at. Words like "Development", "Presentation", "Discussion", "Update", "Overview", "Review", "Consideration", "Process", "Session", "Report", "Debate", "Announcement", "Revision(s)", "Projection" describe *where something is in the meeting cycle*, not *what the topic is* — never end a tag with one of these (e.g. "Cell Phone Policy Development" → "Cell Phone Policy"). Words naming a specific, substantive outcome — "Closure", "Adoption", "Referendum", "Resignation", "Appointment" — are fine to keep; they're not process-stage words.
4. **Name the specific issue, not a category** — "SPESPA Contract 2025" not "Union Contracts". Generic category nouns are never valid tags.
5. **Evidence relevance.** When citing evidence_bullets for a tag, cite only bullets where THIS topic is the actual subject of that bullet — not bullets that mention it only in passing while primarily discussing something else. If a topic genuinely has no bullet substantially about it, don't tag it at all.

**Format rules:** Fiscal years must use FYYY format (FY27, FY26) — never FY2027 or FY2026. No parentheses or acronyms. No concatenated words. No symbols (&, /, etc.).

Meetings (chronological):
{meetings_text}

Respond with tags for every meeting listed above, keyed by its slug. For each tag, cite which
0-indexed bullet number(s) from THAT meeting's own numbered Summary support it — every tag must
cite at least one bullet from its own meeting.
```

Each meeting's Summary is rendered with its own 0-indexed bullet numbers (restarting at 0 per meeting) — same evidence-linkage mechanism as §2, see that section's "Evidence linkage" note.

### Known failure mode

A first attempt at this prompt (without rule 2's explicit "scan your own output" instruction and fixed theme vocabulary, and without §2's `_strip_modifiers()` backstop existing yet) produced *worse* fragmentation than the single-meeting path it replaced — e.g. seven separate FY26 budget tags (`FY26 Budget Development`, `Presentation`, `Referendum`, `Approval`, `Discussion`, `Projection`, `Financial Deficit`) instead of one. Removing the external `allowed_tags` anchor that keeps §2 grounded, and asking the model to hold self-consistency across ~57 meetings of output in a single generation pass, turned out to need much more explicit reinforcement than the single-meeting prompt did. `_strip_modifiers()` (§2) now backstops the modifier-word part of this regardless of prompt compliance; the fixed-vocabulary instruction is the current mitigation for the theme-synonym part (e.g. "Financial Deficit" vs "Budget"), which has no equivalent deterministic backstop yet.

A second, different failure mode showed up after adding evidence-bullet citation (todos/012): tags reverted toward bare generic category nouns (`Personnel`, `Policy`, `Finance`, `Board Governance`) that rule 4 already explicitly prohibits. Hypothesis: a generic tag is trivially easy to "back" with almost any bullet, so requiring per-tag evidence at 57-meeting scale seems to have nudged the model toward tags chosen for citability rather than specificity — the single-meeting path (§2), tested at much smaller scale via `--dry-run-tag`, didn't show this. Rule 4 now explicitly warns against this exact trade-off ("pick the specific tag first... never let ease of citation pull you toward a broader tag"), and `_drop_blacklisted()` (§2's Post-processing) backstops it in code regardless — this is the same lesson as the first failure mode: prompt-only fixes for this class of problem have repeatedly needed a deterministic backstop once observed failing at full-corpus scale.

---

## 4. Agenda Text Retrieval (`post_process.py`)

**Script:** `scripts/post_process.py → _load_cached_agenda_text()`
**Model:** `DEFAULT_MODEL`/`BACKUP_MODEL` fallback pair via `scripts/model_config.py` (only when the packet-isolation fallback below triggers)
**Temperature:** 0.0
**Output:** Plain text (agenda excerpt), truncated to `max_chars` (default 2500)
**Called from:** §2 and §3, to ground tagging in the district's own agenda item framing rather than only the Gemini-generated transcript summary.

### Behavior

1. Look for a cached standalone `agenda`-type doc under `official_docs/{slug}/` in GCS (populated by `_extract_official_terms()`, §6) — if found, return its text.
2. Otherwise, if a `packet`-type doc is cached (agendas are sometimes bundled into the packet rather than standing alone), isolate just the agenda/order-of-business items from it via the prompt below — a one-time Gemini call, cached back to GCS (`{packet_blob}.agenda-extract.txt`) so it never re-runs for the same packet.
3. Returns `None` if neither is cached for that slug.

### Prompt template (packet-isolation fallback only)

```
This is a school board meeting packet, which bundles the agenda with supporting materials.
Extract ONLY the agenda / order-of-business item list (the itemized list of what the board
will discuss or vote on) — not the attached supporting documents, reports, or exhibits.
Return the isolated agenda text only, no commentary or markdown fences.

{packet_text}
```

---

## 5. Topic Synthesis (`post_process.py`)

**Script:** `scripts/post_process.py → synthesize_topic()`  
**Model:** `DEFAULT_MODEL` (`gemini-2.5-pro`), falling back to `BACKUP_MODEL` (`gemini-3.5-flash`) on a 429 rate-limit — see `scripts/model_config.py`, shared with `process_transcripts.py`.  
**Temperature:** 0.1  
**Output:** JSON object (parsed manually — not Pydantic)

### Prompt template

```
You are a policy analyst for the SPSD Board Meeting Archive.
Synthesize the following chronological notes (NEWEST FIRST) regarding the topic: '{topic}'.

Return ONLY a JSON object with these exact keys — no markdown fences, no commentary:
{
  "current_status": "1-2 plain sentences (no markdown) summarizing where things stand right now. Card-ready.",
  "overview": ["Array of 3-6 bullet points, newest-first. Each bullet is a single plain sentence
    (no markdown) citing a meeting date naturally (e.g. 'On {display_date}, the board decided...').
    The first bullet MUST cover the most recent developments. If a prior decision was reversed or
    modified, say so explicitly in its own bullet."],
  "perspectives": {
    "Board": ["Array of 1-4 short bullets on elected members' stance and questions. Omit key entirely if no data."],
    "Administration": ["Array of 1-4 short bullets on Superintendent/Directors' recommendations. Omit key entirely if no data."],
    "Staff": ["Array of 1-4 short bullets on staff and union rep viewpoints. Omit key entirely if no data."],
    "Citizens": ["Array of 1-4 short bullets on public comment and parent viewpoints. Omit key entirely if no data."]
  }
}

RULES:
- Omit any perspective key where there is genuinely no evidence in the notes.
- Use "Staff" (not "Teachers") for the staff/union group.
- SPELING: Kaler (NOT Caler), Skillin (NOT Skillen).

Evidence (Newest First):
{evidence}
```

`overview` and each `perspectives` value are JSON arrays of short bullet strings (not prose blocks) — rendered by `topic.njk` as `<ul class="summary-bullets">`, the same markup meeting pages use for their own summary bullets. `current_status` stays a single short string (1-2 sentences, "card-ready").

### Evidence format

Up to `MAX_EVIDENCE_MEETINGS` (60) most recent meeting chunks, joined by `---` — raised from an earlier hardcoded cap of 15, which was a meeting-count limit mislabeled as a token-budget constraint; at current corpus/model scale there's no real token pressure. Each chunk:
```
Meeting: {display_date} ({meeting_url})
- {summary_bullet_text}
- {summary_bullet_text}
```

Which bullets go into a given topic's chunk: a meeting's `topic_evidence` frontmatter field (`{tag: [bullet_index, ...]}`, populated by §2/§3's tagging calls) is used directly when present. `_evidence_match()` fuzzy text matching is only a fallback, for meetings tagged before `topic_evidence` existed (see todos/012). §2/§3's tagging prompts also carry a fifth rule (`_RULE_EVIDENCE_RELEVANCE`) instructing the model to only cite bullets where the topic is the bullet's actual subject, not a passing mention — this keeps tangential content out of the evidence that reaches this synthesis step in the first place.

### Cache strategy

MD5 hash of the evidence string stored in `scripts/topic_hashes.json`. Synthesis is skipped when hash is unchanged AND an existing dict-format summary exists.

---

## 6. Official Document Term Extraction (`post_process.py`)

**Script:** `scripts/post_process.py → _extract_official_terms()`  
**Model:** `DEFAULT_MODEL` (`gemini-2.5-pro`), falling back to `BACKUP_MODEL` (`gemini-3.5-flash`) on a 429 rate-limit — see `scripts/model_config.py`.  
**Temperature:** 0.0  
**Output:** JSON array (parsed manually)

### Purpose

Extracts canonical proper noun spellings from agenda/packet/minutes PDFs stored in Drive, caches results in GCS, and injects them into the transcript extraction prompt as authoritative spelling hints.

This is the dynamic, per-meeting counterpart to the static `src/_data/glossary.json` (§1) — use the static file only for spellings that never change over the archive's lifetime; use this mechanism for anything specific to a given meeting's own documents, including anything role- or date-dependent.

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

Keyed by Drive `file_id` (via `drive_catalog.json`), not by meeting date — see
[pipeline.md#the-drive-catalog-drive_catalogjson](pipeline.md#the-drive-catalog-drive_catalogjson).

```
gs://{bucket}/official_docs/{created_date}_{doc_type}_{file_id}_{modified_stamp}.txt   ← raw text
gs://{bucket}/official_docs/{created_date}_{doc_type}_{file_id}_{modified_stamp}.json  ← extracted terms
```

---

## 7. Agenda Preview (`post_process.py`)

**Script:** `scripts/post_process.py → generate_agenda_preview()`  
**Model:** `DEFAULT_MODEL` (`gemini-2.5-pro`), falling back to `BACKUP_MODEL` (`gemini-3.5-flash`) on a 429 rate-limit — see `scripts/model_config.py`.  
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

## 8. Blurb Generation (`post_process.py`)

**Script:** `scripts/post_process.py → generate_blurb()`  
**Model:** `DEFAULT_MODEL` (`gemini-2.5-pro`), falling back to `BACKUP_MODEL` (`gemini-3.5-flash`) on a 429 rate-limit — see `scripts/model_config.py`.  
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
