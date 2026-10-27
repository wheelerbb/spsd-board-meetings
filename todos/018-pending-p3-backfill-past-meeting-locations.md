---
name: backfill-past-meeting-locations
status: pending
priority: p3
issue_id: "018"
tags: [pipeline, data-quality, locations]
dependencies: []
---

## Problem Statement

Meeting `location` is now resolved by `source_data.py::_resolve_location` — Apptegy's venue when it
names a place, else the meeting's own agenda header (`drive.extract_location_from_content`), with a
bare Apptegy street address as last resort. But that resolution only runs for **upcoming stubs**
(every run) and for **past meetings with no location at all**. Past meetings that already hold a
location keep it, and many of those values were never sourced from anything: stub creation used to
hardcode `"South Portland High School Lecture Hall"`, and the initial Eleventy build seeded
`"City Hall Community Room"` on 2023–2024 pages.

The narrowing was deliberate (2026-10-23): rewriting historical pages in bulk is a user decision,
not something the daily pipeline should do silently.

## Findings

**Past stubs where the agenda header disagrees with the stored value** (scan 2026-10-27, 22 pages):

| Meeting | Stored | Agenda header |
|---|---|---|
| 2024-03-17 | South Portland High School Lecture Hall | South Portland High School / Lecture Hall |
| 2025-03-27 | South Portland High School Lecture Hall | South Portland High School / Learning Commons |
| 2025-07-01 | South Portland High School Lecture Hall | South Portland High School / Learning Commons |
| 2025-07-10 | South Portland High School Lecture Hall | South Portland High School / Learning Commons |
| 2025-07-30 | South Portland High School Lecture Hall | South Portland High School / Learning Commons |
| 2025-08-20 | South Portland High School Lecture Hall | South Portland High School / Learning Commons |
| 2025-09-15 | South Portland High School Lecture Hall | South Portland High School / Learning Commons |
| 2025-10-15 | South Portland High School Lecture Hall | South Portland High School / Learning Commons Conference Room |
| 2025-11-05 | South Portland High School Lecture Hall | South Portland High School / Learning Commons |
| 2025-12-01 | South Portland High School Lecture Hall | South Portland High School / Lecture Hall |
| 2025-12-03 | South Portland High School Lecture Hall | South Portland High School |
| 2025-12-22 | South Portland High School Lecture Hall | South Portland High School / Learning Commons |
| 2026-01-07 | South Portland High School Lecture Hall | South Portland High School / Learning Commons |
| 2026-01-14 | South Portland High School Lecture Hall | South Portland High School / Learning Commons |
| 2026-01-24 | South Portland High School Lecture Hall | South Portland High School / Lecture Hall |
| 2026-02-24 | South Portland High School Lecture Hall | South Portland High School / Learning Commons |
| 2026-03-17 | South Portland High School Lecture Hall | South Portland High School / Room 137 |
| 2026-04-06 | South Portland High School Lecture Hall | South Portland High School / Admin Suite Conference Room |
| 2026-04-08 | South Portland High School Lecture Hall | Virtual |
| 2026-04-17 | South Portland High School Lecture Hall | Community Center / 21 Nelson Road, South Portland |
| 2026-04-27 | South Portland High School Lecture Hall | Community Center / 21 Nelson Road, South Portland |
| 2026-09-21 | Cobblestone Coffee & Co. | Cobble Stone Coffee & Co. |

- **2026-09-21 is a regression risk, not a fix**: the stored "Cobblestone" was hand-corrected; the
  agenda's "COBBLE STONE" is the district's typo. A backfill must not blindly overwrite it.
- **"City Hall Community Room" (24 pages, 2023-08-21 → 2024-08-19; 17 processed, 7 stubs)** — not in
  the table above because their agenda PDFs extract with word spacing lost
  ("SOUTHPORTLANDHIGHSCHOOL/ LECTUREHALL"), which the extractor deliberately rejects. Those mangled
  headers suggest SPHS Lecture Hall / Learning Commons, not City Hall — worth verifying a few by
  hand against the PDFs before deciding.
- **Processed (non-stub) meetings were not scanned** — the table covers stubs only. A full backfill
  should run the same comparison across every past meeting with an agenda/packet in the catalog.

## Proposed Solutions

1. **One-off backfill script** (preferred): run `_agenda_location` over every past meeting, print a
   diff for review, apply on confirmation. Leave the daily pipeline's narrow scope as-is.
   Skip/whitelist hand-corrected values (2026-09-21).
2. **Widen the pipeline rule** to "agenda wins over any stored value for past meetings". Simpler,
   but re-fights hand corrections forever — rejected unless paired with a stored
   `location_source`/override field.

For the 2023–2024 pages, optionally teach the extractor to recover spaceless headers (e.g. match
against a small vocabulary of known rooms) rather than reject them.

## Acceptance Criteria

- [ ] Diff reviewed by the user before any write
- [ ] Hand-corrected locations preserved
- [ ] 2023–2024 "City Hall Community Room" pages verified against their PDFs and corrected or confirmed
- [ ] Processed (non-stub) meetings included in the scan
