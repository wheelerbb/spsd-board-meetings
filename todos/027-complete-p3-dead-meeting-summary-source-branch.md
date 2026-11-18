---
name: dead-meeting-summary-source-branch
status: complete
priority: p3
issue_id: "027"
tags: [data-integrity, pipeline, bugfix]
dependencies: ["023"]
---

## Problem Statement

Found incidentally during Stage 4 of the terms-registry-rebuild validation plan (todos/019),
while characterizing the ~36 "no `votes_source`" meetings. `process_transcripts.py::
_find_priority_source_doc` has a branch meant to prefer an unratified "Meeting Summary" document
over the transcript when no formal Minutes doc exists yet:

```python
summary_docs = [d for d in docs if d.get('type') == 'pdf' and 'summary' in (d.get('label') or '').lower()]
```

No code path anywhere in this repo ever sets a doc's `type` to `'pdf'` — `categorize_document()`
(`sourcing/drive.py`) and the SPSD site scraper (`sourcing/spsd_site.py`) both only ever produce
`agenda`/`packet`/`minutes`/`transcript`/`misc`. `type` here is the document's *role*
(doc_type), not its file format — `'pdf'` looks like a stray mix-up with `file_type`. This
branch has therefore never matched anything, for any meeting, ever.

## Findings

- Confirmed via a corpus-wide check (Stage 4): at least 4 of the 36 "no `votes_source`" meetings
  (`2025-02-03`, `2025-07-01`, `2025-07-10`, `2025-07-30`) have a `misc`-typed doc whose label
  contains "summary" (e.g. "Board Workshop/Retreat Summary", "Special Meeting Summary") that
  would be exactly what this branch is meant to find — but since its `type` is `'misc'`, not
  `'pdf'`, the branch never matches and these meetings fall through to `(None, None)`, same as if
  no written record existed at all.
- Distinct from todos/023 (the native-Google-Doc file_id regex bug) and from the pypdf extraction
  bug — this is a dead-code condition, not a parsing or extraction failure. Even a perfectly
  readable, correctly-typed Meeting Summary doc can never be selected as a priority source today.
- Not yet assessed: how many of the 36 "no `votes_source`" meetings this fully explains once
  combined with the fact that all 36 are currently unprocessed stubs (`stub: true`,
  `has_transcript: false` — see todos/019, 2026-11-17 Stage 4 work log) — this bug only matters
  once/if those meetings are ever processed, since `_find_priority_source_doc` has never run for
  any of them yet either.

## Proposed Solutions

- [x] Fix the condition to match on `doc_type` membership the way the `minutes` branch above it
  does (e.g. `d.get('type') == 'misc' and 'summary' in ...`), or introduce a dedicated
  `doc_type` for standalone summary docs if `misc` is too broad a bucket to safely match on.
- [ ] Decide whether this fix should land before or alongside those 36 stub meetings eventually
  getting their first real transcript processing pass, since it directly affects what
  `votes_source` they'd end up with. — Not decided here; the fix is in place and will simply take
  effect whenever those 36 stubs are next processed (no separate timing decision was requested).

## Acceptance Criteria

- [x] Root cause confirmed against real data (done above) and fixed
- [ ] Impact on the 36 currently-unprocessed meetings assessed once a fix is in place — all 36 are
  still unprocessed stubs as of this session, so there's nothing to assess yet; this will only be
  answerable after their first real processing pass picks up the fixed branch.

## Work Log

- 2026-11-17: found while characterizing Stage 4's "no votes_source" population (todos/019). Not
  fixed — out of Stage 4's scope (a new bug, not confirmation of an already-known one) and all
  affected meetings are unprocessed stubs regardless, so there's no live corruption to assess yet.
- 2026-11-17: Resolved (fix only). `scripts/process_transcripts.py::_find_priority_source_doc`:
  changed `d.get('type') == 'pdf'` to `d.get('type') == 'misc'` to match the actual doc_type
  `categorize_document()`/the site scraper assign to summary docs. Verified with the two real
  label examples from this todo's own Findings ("Board Workshop/Retreat Summary", "Special Meeting
  Summary") — both now correctly resolve to `('Meeting Summary', <doc>)`; a committee-minutes doc
  and an empty doc list both still correctly return `(None, None)`.
