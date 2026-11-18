---
name: content-date-override-durability-and-site-authority-gap
status: pending
priority: p3
issue_id: "031"
tags: [data-integrity, date-resolution, pipeline]
dependencies: []
---

## Problem Statement

Found while reviewing the first live run of the todo 026 term-extraction rework (run
44380714084) and investigating two date-ambiguity warnings it surfaced. Both root-caused and
hand-corrected in production this session (see Work Log), and a general fix landed in
`source_data.py::_resolve_url_owner` (new) so a same-run URL collision between two dates is now
broken by the Drive catalog's content-derived `meeting_slug` instead of always falling back to
`AMBIGUOUS` and leaving the doc on both dates forever. Two gaps remain, deliberately not fixed in
that pass:

1. **No durable override for a confirmed-wrong content-extracted date.** `1IrSDqybB6p...`
   ("REVISED 01.08.25 Special Meeting Agenda") genuinely belongs to 2025-01-08, but its own
   document content says 2024 (the creator didn't update the year reference when revising it) —
   `extract_date_from_content` faithfully extracts what's there, which is simply wrong.
   `drive_catalog.json`'s `meeting_slug` for this file was hand-corrected to `2025-01-08` with
   `resolved_via: "manual"` and a `resolved_via_note` explaining why — but nothing in
   `build_meeting_map` currently respects or preserves a `manual` resolution. If this file is
   ever re-extracted (a `TEXT_EXTRACTOR_VERSION` bump forces re-extraction for every document
   corpus-wide, or a genuine re-upload changes `modified_time`), `text_cache_fresh` will return
   `False`, content extraction will re-run, and `meeting_slug` will silently regress to
   `2024-01-08` — reintroducing the exact bug, with no warning that a manual override existed and
   was overwritten.

2. **`has_site_data` authority check doesn't cross-check content resolution.** The spurious
   `2024-03-17` meeting stub (now deleted, see Work Log) existed purely because
   `spsd_site.py::fetch_site_data()`'s "find a date near this Drive link" heuristic matched a
   `2024-03-17` date string near the Budget Workshop I Agenda link on the SPSD site, even though
   that same file's Drive content had already been confidently dated to `2025-03-17`. The new
   `_resolve_url_owner` tiebreak fixes the *document* ending up cross-listed on both dates, but
   `reconcile_meetings`'s `has_valid_event`/`has_site_data` authority rule only checks
   `bool(data.get('site'))` — truthy the moment *any* site scrape result exists for a date,
   independent of whether every doc it offered just lost the url_owner tiebreak. A site-only date
   whose sole evidence is a document the catalog has confidently dated elsewhere can therefore
   still spawn/keep a stub alive (now just emptied of docs, but the phantom page — and its
   `day_of_week: Sunday` tell, since this board doesn't meet Sundays — stays).

Also worth noting: `source_data.py`'s site-fetch has a TTL cache (`_cache_fresh(cache_meta,
'site')`) that copies the previous run's `all_data[slug]['site']` forward verbatim on a cache
hit, without re-validating against the live site. The `2024-03-17` entry may well have been
carried forward this way for a long time after whatever originally produced it — worth confirming
whether the live SPSD site still actually has this near-date-match artifact next time site data
is force-refreshed (`--force`), since if it doesn't, gap 2 above may be lower priority than it
looks.

## Findings

- Confirmed via `gs://spsd-pipeline-data/drive_catalog.json` and `master_material_map.json`
  (read/corrected directly this session) — not yet re-validated against a live pipeline run.
- `_resolve_url_owner`'s tiebreak (scripts/source_data.py) only ever defers to
  `catalog[file_id]['meeting_slug']` — it has no concept of a `resolved_via: "manual"` override
  being more authoritative than a routine `content` resolution, because `build_meeting_map`
  doesn't distinguish them either; both just get read as whatever `meeting_slug` currently holds.

## Proposed Solutions

Not designed yet. Two independent threads:

1. For gap 1: `build_meeting_map`'s re-resolution path (`sourcing/drive.py`, the
   `text_cache_fresh` check and its fallthrough to `extract_date_from_content`/
   `parse_meeting_date`) would need to check for and preserve an existing `resolved_via: "manual"`
   entry rather than unconditionally overwriting `meeting_slug` — and probably warn loudly if it's
   about to clobber one, rather than silently reverting. Low urgency unless/until the next
   `TEXT_EXTRACTOR_VERSION` bump.
2. For gap 2: `has_valid_event`'s `has_site_data` check would need to only count a site entry as
   authoritative evidence when at least one of its docs actually survives the url_owner tiebreak
   for *this* date (or some comparable content cross-check) — not just on raw
   `bool(data.get('site'))`. Needs more investigation into whether this would ever incorrectly
   suppress a legitimate site-only meeting that genuinely has no Drive-catalogued documents.

## Work Log

- 2026-11-18: Investigated both date-ambiguity warnings from run 44380714084 at the user's
  request. Confirmed via `drive_catalog.json`: `1xknZQ1mAIx2EhJJhI3laoQ3D1EyjjSGp` ("Budget
  Workshop I Agenda") already correctly resolves to `2025-03-17` via content — the `2024-03-17`
  stub (`src/meetings/2024-03-17.njk`, `stub: true`, no video/transcript, `day_of_week: Sunday`)
  was purely a `site`-sourced artifact with no catalog support; deleted the stub and removed the
  `2024-03-17` entry from `master_material_map.json`. Confirmed `1IrSDqybB6p...` resolves via
  content to `2024-01-08`, which is wrong (document's own content error, per the user); corrected
  `drive_catalog.json`'s `meeting_slug` to `2025-01-08` (`resolved_via: "manual"`, with an
  explanatory note), removed the doc from `2024-01-08`'s `drive` list in
  `master_material_map.json`, and removed it from `src/meetings/2024-01-08.njk`'s `docs:` list.
  Landed `_resolve_url_owner` in `source_data.py` as the general-case fix (content-derived
  `meeting_slug` now breaks a same-run URL collision instead of always falling back to
  `AMBIGUOUS`), with tests. Filed this todo for the two durability gaps the general fix doesn't
  close.
