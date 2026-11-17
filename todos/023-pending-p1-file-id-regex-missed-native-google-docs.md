---
name: file-id-regex-missed-native-google-docs
status: pending
priority: p1
issue_id: "023"
tags: [data-integrity, pipeline, bugfix]
dependencies: ["019"]
---

## Problem Statement

Found and fixed during Stage 2 of the terms-registry-rebuild validation plan (todos/019): seven
production call sites across three files each independently inlined the same
`re.search(r'/file/d/([^/?]+)', url)` pattern to parse a Drive `file_id` out of a document URL.
That pattern only matches an *uploaded file's* Drive URL shape
(`drive.google.com/file/d/<id>/view`) — it never matched a **native Google Doc's** URL shape
(`docs.google.com/document/d/<id>/edit`), and every call site treated a non-match as "nothing to
do here" (`if not match: continue`) with **no error, warning, or log line**. Any official document
authored as a native Google Doc instead of an uploaded PDF has been silently invisible to these
code paths for as long as they've existed.

## Findings

- **Confirmed root cause and impact during Stage 2** (the full-corpus registry rebuild dry run,
  see todos/019 2026-11-17 work log): 174 real official documents across 93 meetings, 7 of which
  are native Google Docs tagged `agenda`/`minutes` in frontmatter. All 7 were never even attempted
  by `post_process.py::_extract_official_terms` — not a failure, just silently skipped, with zero
  signal anywhere in logs or output.
- **Bigger than term extraction.** The identical inline regex was independently duplicated in:
  - `post_process.py` — `_extract_official_terms` (term extraction, line 423) and
    `_load_cached_agenda_text` (agenda-isolation lookup, line 517 — now folded into the shared
    helper directly)
  - `process_transcripts.py` — the official-terms glossary-hint loader (line 111) and
    `_read_doc_text` (line 155), which backs the votes/attendance-from-Minutes-doc extraction path
    (`_find_priority_source_doc` → `_read_doc_text` → `drive.get_or_extract_text`)
  - `source_data.py` — duplicate-content dedup (line 121), agenda-location extraction (line 190),
    and Drive-VTT sync (line 467)
- **Confirmed concrete real-world fallout, not just a theoretical gap:** `2024-12-02`'s Minutes
  document is a native Google Doc, and that meeting's frontmatter carries **no `votes_source` line
  at all** — consistent with `process_transcripts.py`'s copy of this same bug silently preventing
  its Minutes doc from ever being read for vote/attendance extraction. There may be other affected
  meetings among the ~36 with no `votes_source` noted during the registry-rebuild risk discussion
  (todos/019, 2026-11-16) — not individually confirmed here, only `2024-12-02`.
- **Fixed in this pass**: added `sourcing/drive.py::extract_file_id(url)`, a single shared helper
  matching `(?:file|document|spreadsheets|presentation)/d/([^/?]+)` (every Drive/Docs/Sheets/Slides
  URL shape observed in this corpus — confirmed via a corpus-wide survey: 255 `drive/file`, 33
  `docs/document`, 29 `docs/presentation`, no `docs/spreadsheets` seen but included for
  completeness/symmetry). All 7 call sites now call this one helper instead of their own inline
  pattern. Full test suite (143 tests) passes unchanged.
- **Deliberately not done in this pass** (per explicit user decision when this was found): no live
  re-run of `process_transcripts.py`'s votes/attendance extraction for `2024-12-02` or any other
  possibly-affected meeting. The regex fix changes that code path's behavior on its next live run,
  but re-running it live now was explicitly deferred to a separate, dedicated review — this is a
  visible-content change (votes/attendance on a real meeting page), not just a registry-internal
  one, and deserves its own look rather than being bundled into the registry-rebuild session.

## Proposed Solutions

Already implemented: the shared `extract_file_id` helper (see Findings). Remaining work is
entirely about the *downstream* consequence, not the parsing bug itself:

- [ ] Identify every meeting whose `votes`/`board_attendance` may be affected (Minutes/Summary doc
  is a native Google Doc) — start from the ~36 meetings with no `votes_source` line
  (todos/019, 2026-11-16 risk discussion) and cross-reference against frontmatter URL shapes.
- [ ] Decide whether to re-run `process_transcripts.py` for those specific meetings (narrow,
  targeted) vs. let it happen naturally on their next regular reprocessing pass.
- [ ] Confirm no other doc-URL-keyed behavior (dedup, agenda-location, VTT sync) was silently
  affected in a way that changed a meeting's resolved data, not just skipped a nice-to-have.

## Acceptance Criteria

- [x] Root cause identified and confirmed against real data (`2024-12-02`)
- [x] Single shared fix applied across all 7 call sites; regression-tested
- [ ] Downstream votes/attendance impact assessed and addressed for affected meetings

## Work Log

- 2026-11-17: found while reconciling Stage 2's "174 expected vs. 163 captured" gap during the
  terms-registry-rebuild validation plan (todos/019). Root-caused, fixed (shared
  `drive.extract_file_id` helper), tests re-passed. Live votes/attendance re-extraction explicitly
  deferred per user decision — tracked here as the open remainder.
