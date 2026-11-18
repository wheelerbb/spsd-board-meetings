---
name: file-id-regex-missed-native-google-docs
status: complete
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

Already implemented: the shared `extract_file_id` helper (see Findings). The downstream
votes/attendance impact has now been assessed (Stage 4, see below) — all that's left is deciding
when to act on it:

- [x] Identify every meeting whose `votes`/`board_attendance` may be affected (Minutes/Summary doc
  is a native Google Doc) — done, Stage 4: only `2024-12-02`, among all 36 "no `votes_source`"
  meetings, actually has a native-Doc Minutes file. The other 35 are unrelated to this bug (see
  Findings below).
- [ ] Decide whether to re-run `process_transcripts.py` for `2024-12-02` specifically (narrow,
  targeted) vs. let it happen naturally whenever this meeting's stub is next processed (it has
  never been processed at all yet — see Findings).
- [ ] Confirm no other doc-URL-keyed behavior (dedup, agenda-location, VTT sync) was silently
  affected in a way that changed a meeting's resolved data, not just skipped a nice-to-have.

## Findings — Stage 4 downstream-impact assessment (2026-11-17)

Re-scoped the "~36 meetings with no `votes_source`" population named above: **all 36 are
currently unprocessed stubs** (`stub: true`, `has_transcript: false`) — `process_single_meeting`,
the function that ever sets `votes_source` at all, has never run for any of them, regardless of
either known bug. Of the 36, only **`2024-12-02`** actually has a native-Google-Doc Minutes file;
the other 35 either have a normal uploaded-file Minutes doc (22 of them — simply not processed
yet, nothing to do with this bug) or no Minutes/Summary doc readable at all under the current
`_find_priority_source_doc` logic (4 of those are blocked by a *different*, newly-found dead-code
bug — see todos/027 — and the rest genuinely have no written record yet).

For `2024-12-02` specifically: confirmed the pre-fix regex (`r'/file/d/([^/?]+)'`) does not match
its Minutes doc's URL (`docs.google.com/document/d/...`), so the override branch could never have
fired under the old code. Confirmed the current `drive.extract_file_id` *does* resolve it, and
running the real extraction functions directly (read-only, no file written) against it produced 3
real votes and 5 real attendance records — concrete evidence of what this meeting would gain once
it's actually (re)processed, not just a parsing fix with unconfirmed value.

## Acceptance Criteria

- [x] Root cause identified and confirmed against real data (`2024-12-02`)
- [x] Single shared fix applied across all 7 call sites; regression-tested
- [x] Downstream votes/attendance impact assessed — see Stage 4 Findings above. Only `2024-12-02`
  is actually affected by this specific bug.
- [x] Live re-extraction applied to `2024-12-02` — see Work Log.

## Work Log

- 2026-11-17: found while reconciling Stage 2's "174 expected vs. 163 captured" gap during the
  terms-registry-rebuild validation plan (todos/019). Root-caused, fixed (shared
  `drive.extract_file_id` helper), tests re-passed. Live votes/attendance re-extraction explicitly
  deferred per user decision — tracked here as the open remainder.
- 2026-11-17: Stage 4 (todos/019) assessed the downstream impact — see Findings above. Only one
  meeting (`2024-12-02`) is actually explained by this bug; the other 35 "no votes_source"
  meetings are unprocessed stubs, unrelated. Live re-extraction for `2024-12-02` still needs
  explicit go-ahead before it happens.
- 2026-11-17: User approved the live re-extraction. Ran the real override functions
  (`_find_priority_source_doc`/`_read_doc_text`/`_extract_votes_attendance_from_doc`) directly
  against `2024-12-02`'s now-resolvable native-Doc Minutes record, narrowly updating only
  `votes`/`votes_source`/`board_attendance`/`attendance_source` (not the full
  `process_single_meeting` path, which would also regenerate blurb/summary/timeline from a fresh
  transcript read — this meeting has none, `has_vtt_source: false`). Added 3 real votes (officer
  elections + adjournment) and replaced the 8-person roster-stub attendance with the document's
  actual stated roll call (4 present + 1 explicitly absent) — a real completeness tradeoff the
  user explicitly accepted after it was flagged (the official record names fewer people than the
  roster stub did). Live Drive catalog updated with the fresh text extraction. Build verified
  clean (172 files, no errors); 166 tests still passing. Also found and fixed, in the one-off
  script only (not production), a missing `allow_unicode=True` on the `yaml.dump` call that would
  have corrupted unrelated unicode fields (em-dashes, middot) into escape sequences — the same
  omission exists in `process_transcripts.py`'s own `process_single_meeting`, currently masked
  there because a later pipeline stage's write (which does pass `allow_unicode=True`) overwrites
  it; worth a follow-up fix in production but not done here (cosmetic, not a content bug).
