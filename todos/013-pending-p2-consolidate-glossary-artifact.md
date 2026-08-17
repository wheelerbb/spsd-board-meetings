---
name: consolidate-glossary-artifact
status: pending
priority: p2
issue_id: "013"
tags: [architecture, maintainability, data-integrity, pipeline]
dependencies: []
---

## Problem Statement

Proper-noun/spelling correction is spread across four separate locations in two scripts, two of which share the identifier `GLOSSARY` despite being structurally unrelated:

1. `scripts/process_transcripts.py` → `GLOSSARY` (string, ~line 43) — static hints injected into *every* meeting's Gemini extraction prompt, regardless of meeting date.
2. `scripts/process_transcripts.py` → `_load_official_terms_from_gcs()` / `term_hints` (~lines 117-135, 194-200) — per-meeting canonical terms loaded from a GCS cache and appended to the same prompt.
3. `scripts/post_process.py` → `_extract_official_terms()` (~lines 302-380) — extracts canonical proper nouns from each meeting's own agenda/packet/minutes via a separate Gemini call, caches them to GCS (populates the cache #2 reads).
4. `scripts/post_process.py` → `GLOSSARY` (dict, ~lines 247-254) — a *different* `GLOSSARY`, applied as a deterministic find/replace over every already-written meeting `.njk` file on each `post_process()` run.

This was found while investigating a factual error: a topic page said "Superintendent Angela Atkinson Duina" acted on a date when the official minutes show Dr. Entwistle was Superintendent. Root cause was #1 asserting "Angela Atkinson Duina (Superintendent...)" as a blanket, date-blind fact for all 93 meetings in the archive — already corrected to a spelling-only hint in this same session (`process_transcripts.py:48`), but that was a point fix, not a structural one.

**#3's applicability is likely broader than the two pages originally reported.** Spot-checking official minutes against generated data for two independent dates (2024-01-08: actual Superintendent was Mr. Matheney; 2025-10-06: actual Superintendent was Dr. Entwistle) found the *same* Duina misattribution both times, despite those dates being ~20 months apart. Since "Superintendent Angela Atkinson Duina" appears in nearly every processed meeting's generated `summary`/`timeline` from 2024 through 2026, this is plausibly a widespread issue across a large fraction of the 57 processed meetings, not two isolated pages — full scope is unconfirmed (no archive-wide audit has been run; historical meetings have not been reprocessed as part of this fix).

## Findings

- **Naming collision**: two unrelated `GLOSSARY` symbols (a prompt-hint string in `process_transcripts.py`, a find/replace dict in `post_process.py`) — easy to edit one and assume it covers both.
- **Duplication/drift risk**: identical in shape to the `TOPIC_BLACKLIST` duplication already fixed in [todos/005](005-complete-p2-topic-blacklist-duplication.md) — a term added to #4 (post-hoc spelling fix) has no effect on future generation unless someone remembers to also add it to #1, and vice versa.
- **Time-variance mismatch**: #1 and #4 are hand-maintained, "current as of whenever last edited" data (spellings, but #1 also *used to* carry a role claim and still carries a hardcoded board roster) applied blanket across a multi-year archive. #2/#3 are inherently correct per-meeting, since they're sourced from that specific meeting's own official documents. The Duina bug happened because a #1/#4-style static fact was used where only #2/#3-style per-meeting truth is safe.
- **Docs already describe part of this**: `docs/prompts.md` §1 (line 16-17) and §6 (lines 269-303) document the current GLOSSARY + official-terms mechanism; `docs/pipeline.md:13` references "Enforce glossary" for the post_process.py pass. Both will need updating to match whatever the consolidated design looks like.
- **Source**: user code review during topic-page content-accuracy investigation, 2026-08-07.

## Proposed Solutions

### Option A: Single static glossary artifact + keep dynamic extraction as-is (Recommended)

Replace the two static, hand-maintained structures (#1 string, #4 dict) with one data file, e.g. `src/_data/glossary.json`:

```json
[
  { "correct": "Kaler Elementary School", "aliases": ["Caler"] },
  { "correct": "Skillin Elementary School", "aliases": ["Skillen", "Skillins"] },
  { "correct": "Angela Atkinson Duina", "aliases": ["Atkinson-Dena", "Atkinson Dena"] },
  { "correct": "SPESPA", "aliases": [], "note": "Support Professionals" },
  { "correct": "SPTA", "aliases": [], "note": "Teachers" }
]
```

- `process_transcripts.py` renders this into prompt-hint text for #1 (`"- {correct} (NOT {aliases})"`).
- `post_process.py` derives its find/replace pass for #4 directly from `{alias: correct}` pairs — no separately maintained dict.
- **Remove the hardcoded board member / student rep roster from the static glossary entirely.** Roster is inherently time-varying (already observed stale/wrong for both 2024 and 2025 meetings); it should come only from #2/#3's per-meeting official-document extraction, never from a blanket hint. Anything that can change over the life of the archive (who holds a role, who's on the board) does not belong in a static file — only true-for-all-time spelling corrections (school names, acronyms) do.
- Leave #2/#3 (the dynamic per-meeting extraction/cache pipeline) architecturally as-is — it's already correctly scoped per meeting. Just document it more clearly alongside the new static artifact so the "static vs. per-meeting" split is obvious to future readers.

**Pros**: One file to edit for spelling fixes; eliminates the drift risk; removes the exact class of bug that caused the Duina error; keeps the already-correct dynamic mechanism untouched.
**Cons**: Touches both scripts' glossary-consumption code (not just data).
**Effort**: Small–Medium.
**Risk**: Low — mechanical refactor, behavior-preserving for the spelling-correction pieces; the roster removal is the only behavior change, and it's the fix that's actually wanted.

### Option B: Keep two files, add a consistency test

Leave #1 and #4 as separate structures but add a test asserting their alias sets match.

**Pros**: Smaller diff.
**Cons**: Doesn't fix the naming collision, the board-roster staleness, or the "two things to remember to update" ergonomics — just detects drift after the fact. Same category of fix `todos/005` explicitly passed over.
**Effort**: Small.
**Risk**: Medium (fixes symptom, not cause).

### Option C: Leave as four locations, document thoroughly

Document the four-location flow clearly (already partially done in `docs/prompts.md`) and rely on process discipline.

**Effort**: Trivial.
**Risk**: Medium-High — this is close to the status quo that produced the Duina bug.

## Acceptance Criteria
- [x] Static spelling corrections live in exactly one artifact, consumed by both `process_transcripts.py` and `post_process.py`
- [x] The `GLOSSARY` naming collision is resolved (single name, single shape, or clearly distinct names if any legitimate split remains)
- [x] Hardcoded board member / student rep roster is removed from the static prompt glossary; role/roster facts come only from per-meeting official-document extraction
- [x] `docs/prompts.md` (§1, §6) and `docs/pipeline.md` (glossary/enforcement row) updated to match the new design
- [ ] Scope of the historical Duina-style misattribution is at least tracked (archive-wide audit comparing generated Superintendent mentions against official minutes) — reprocessing affected meetings can be a separate follow-up, but the scope should be known, not just assumed from two spot checks

## Work Log
- 2026-08-07: Identified while investigating factual-accuracy reports on the `elementary-school-reconfiguration` and `school-calendar-committee` topic pages. Point fix applied to `process_transcripts.py`'s `GLOSSARY` (removed the blanket Superintendent role claim for Angela Atkinson Duina) as an interim measure; this todo tracks the structural follow-up.
- 2026-08-07: Implemented Option A. Added `src/_data/glossary.json` (short-form `correct` + `aliases` + optional `note`, per user decision to preserve `post_process.py`'s existing bare-name find/replace behavior rather than the todo's original full-name sketch) and a shared `scripts/glossary_utils.py` (`load_glossary`/`render_glossary_hints`/`to_replacement_map`) so the two derivation shapes can't drift apart. Both scripts' `GLOSSARY` symbols removed; `process_transcripts.py` now uses `GLOSSARY_HINTS`, `post_process.py` uses `GLOSSARY_REPLACEMENTS` via a new `_enforce_glossary_pass()` helper (extracted from previously-inline logic, no behavior change). Hardcoded board/student-rep roster dropped entirely, not replaced — role/roster facts continue to come only from `src/_data/board_members.json` (next-meeting stub seeding only, per user clarification) and per-meeting transcript/official-doc extraction. `docs/prompts.md` §1/§6 and `docs/pipeline.md` updated. Last acceptance criterion (historical audit) explicitly deferred per user decision — not part of this change.
- 2026-08-17: Live-verified against real meetings (`2024-01-08`, `2024-01-22`) via `process_transcripts.py` with real Gemini calls. Confirmed: zero "Duina"/"Atkinson" mentions, correct "Kaler"/"Skillin" spelling, correct Superintendent attribution (Mr. Matheney) for both dates. Testing surfaced a real regression: removing the roster also silently dropped its only "(use full names in attendance roll call)" instruction, so attendance collapsed to surname/title-only ("Ms. DeAngelis") — fixed by restoring a generic full-name instruction (no roster) in both attendance-extraction prompts. That still couldn't produce true first+last names when a transcript only states a surname, so — per user request — built a new, out-of-scope-for-the-original-todo but directly-motivated-by-it feature: `scripts/board_members_utils.py` canonicalizes extracted attendance names against whoever was actually active in `board_members.json` on that meeting's date (never determines *who* attended, only formats names already extracted), and auto-grows `board_members.json` with placeholder entries (`"auto_discovered": true`) for names with no match, converging them to full names as fuller observations come in across meetings. Hand-curated entries are never auto-modified. `source_data.py`'s `_active_members()` moved into the shared module. Re-verified live end-to-end after this addition; `board_members.json` writes preserve the file's original compact formatting (custom serializer, not generic `json.dump`) to keep diffs minimal.
