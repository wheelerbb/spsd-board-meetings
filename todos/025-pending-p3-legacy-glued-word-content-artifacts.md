---
name: legacy-glued-word-content-artifacts
status: pending
priority: p3
issue_id: "025"
tags: [data-quality, content]
dependencies: []
---

## Problem Statement

Incidental finding while checking live-exposure for todos/024's glossary discovery bugs (not
caused by either of them, not caused by the Nov 13 glossary-corruption incident either — see
todos/024's Findings for the full negative-evidence trail). Several live, committed meeting
files have glued-together proper nouns directly embedded in generated timeline/summary text —
e.g. `SOUTHPORTLANDHIGHSCHOOL`, `SouthPortlandMiddleSchool`, `SouthPortlandSchool`,
`ElementaryBoundariesandConfigurationsSteeringCommittee`, `PortlandPrideParade`.

## Findings

- Confirmed via `git log -S` on each term: all trace back to July–August 2026 commits (`6765076`
  "feat: reprocess all transcript meetings with updated pipeline", `56e10b8` "chore: auto-update
  meeting stubs and summaries") — months before the Terms Registry project, the Nov 13
  glossary.json corruption, and the pypdf→pymupdf extraction fix (todos/019). Not caused by any
  of this session's work.
- Affects at least: `src/meetings/2024-01-08.njk`, `2024-01-22.njk`, `2024-03-18.njk`,
  `2024-03-28.njk`, `2024-05-30.njk`, `2024-07-08.njk` — found via a targeted grep for known
  glued terms, not a systematic scan; there may be others.
- Root cause not investigated. Timeline/summary content comes from `process_transcripts.py`'s VTT
  transcript analysis, not PDF extraction (the known pypdf word-gluing bug's domain) — so if this
  is the same underlying pattern, it arrived via a different path. Could also simply be a Gemini
  output quirk from an earlier prompt version.

## Proposed Solutions

Not investigated. Options once picked up: (a) root-cause the generation path that produced these
and fix prospectively (per this project's stated preference for fixing generation-time causes
over manual cleanup); (b) a one-off targeted correction for the known-affected meetings via the
glossary (now safe to do given todos/024's word-boundary-safe enforcement fix); (c) a full-corpus
scan for the broader pattern (ALLCAPS or CamelCase multi-word concatenations) to find the true
extent before deciding.

## Acceptance Criteria

- [ ] Root cause identified
- [ ] Full extent of affected meetings established (systematic scan, not just the terms found
  incidentally here)
- [ ] Fix applied and affected meetings corrected

## Work Log

- 2026-11-17: found incidentally during todos/024's live-exposure check. Not investigated further
  or fixed — out of scope for that task.
