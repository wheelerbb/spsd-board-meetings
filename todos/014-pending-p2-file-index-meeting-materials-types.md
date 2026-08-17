---
name: file-index-meeting-materials-types
status: pending
priority: p2
issue_id: "014"
tags: [architecture, maintainability, pipeline, data-integrity]
dependencies: []
---

## Problem Statement

There isn't a single "file index" powering Meeting Materials today — document type classification happens independently in at least two ingestion-time functions, and then gets *re-derived independently again* by at least three downstream consumers via label substring matching. None of these five places agree, and none of them currently recognize a "meeting summary" / unapproved-minutes document as its own category.

This follows directly from a design decision made earlier in this session: split the `minutes` doc type into `minutes_approved` (a genuine official minutes doc — rare/nonexistent in the archive today) and `minutes_unapproved` (aka "meeting summary" — the informal, pre-ratification write-up a district often shares well before the board formally approves it at a later meeting). We deliberately scoped that decision to the type split only, deferring auto-detection of *when* a summary becomes approved as harder, separate work. This todo is the implementation follow-up for the type split itself, done properly at the source instead of via scattered label-matching.

## Findings

- **Ingestion path 1 — Drive scan**: `scripts/sourcing/drive.py:92-98` `categorize_document(filename)` — classifies by filename substring (`agenda`/`packet`/`minutes`/`vtt`, else `misc`). No `summary` case; `minutes` also catches non-full-board docs (e.g. committee minutes — the only `type: minutes` docs anywhere in the current archive are all "Buildings & Grounds Committee meeting minutes", never full-board minutes).
- **Ingestion path 2 — District website scrape**: `scripts/sourcing/spsd_site.py:99-107` — a *separate*, slightly different inline heuristic (checks `agenda`/`minutes`/`packet`/`vtt` against label+surrounding text, defaults to `misc`). Duplicates path 1's logic with drift already: e.g. path 2 treats bare "min" as a minutes signal, path 1 doesn't.
- **Merge step**: `scripts/source_data.py:22-40` `merge_documents()` combines the `drive` and `site` doc lists (from the per-date working cache — see below) by URL, but doesn't reconcile type when the two paths would classify the same or a similar doc differently.
- **The closest thing to "the file index"**: `master_material_map.json` (repo root; synced to/from GCS via `download_from_bucket`/`upload_to_bucket` in `source_data.py`) — the per-date intermediate cache (`{'events', 'site', 'drive', 'video', 'transcript'}`) that `reconcile_meetings()` consumes to assemble each meeting's final `docs:` front matter. This is upstream of everything else and is the natural place for a real, authoritative type to live.
- **Downstream re-derivation #1**: `src/status.njk:89` (Minutes column) — `'minutes' in d.label | lower`, ignores `type` entirely.
- **Downstream re-derivation #2**: `src/_includes/layouts/meeting.njk` sidebar — `'minutes' in labelLower or d.type == 'minutes' or d.type == 'min'` for the "Minutes" materials slot.
- **Downstream re-derivation #3**: `scripts/process_transcripts.py` `_find_priority_source_doc()` (added this session) — `d.get('type') == 'pdf' and 'summary' in (d.get('label') or '').lower()`, since there was no reliable `type` to key off at the time.
- None of the three consumers would recognize `minutes_approved`/`minutes_unapproved` without being individually updated — the exact drift risk already called out for the glossary in [todos/013](013-pending-p2-consolidate-glossary-artifact.md), here applied to document types.

## Proposed Solutions

### Option A: Classify at ingestion, consume from `type` everywhere (Recommended)

- Update both ingestion-time categorizers (`drive.py:categorize_document()`, `spsd_site.py`'s inline equivalent) to recognize meeting-summary filenames/labels (the same "summary" signal `_find_priority_source_doc()` currently checks at consumption time) and tag them `minutes_unapproved`. Reserve `minutes_approved` for a genuine official minutes doc (per this session's decision, no auto-promotion logic — a doc is tagged whatever it looks like at ingestion and stays that way).
- Exclude committee-labeled minutes from either new type (they aren't the board's own minutes) — keep them as `misc` or a distinct `committee_minutes` type if worth preserving as a status-page column later.
- Update `status.njk`, `meeting.njk`'s sidebar, and `process_transcripts.py`'s `_find_priority_source_doc()` to key off `d.type in ('minutes_approved', 'minutes_unapproved')` as the primary signal (label matching can remain a fallback for docs ingested before this change, but stops being the source of truth going forward).
- Display "Approved"/"Unapproved" consistently in both the sidebar's Minutes slot and the status page's Minutes column, sourced from the same classification.
- Backfill: re-run the categorization pass over the existing `docs:` entries (via `master_material_map.json` and/or a one-off script over `src/meetings/*.njk`) so already-ingested "Summary" docs get reclassified, not just newly-found ones.

**Pros**: One authoritative classification, computed once, consumed everywhere; eliminates the three-way drift risk; status page and sidebar automatically agree; `_find_priority_source_doc()` becomes a trivial type check instead of a label heuristic.
**Cons**: Touches two ingestion scripts plus three consumers; needs a backfill pass over existing data to be fully effective.
**Effort**: Medium.
**Risk**: Low — additive type values, existing `pdf`/`misc`/etc. docs unaffected unless they match the new summary/minutes signal.

### Option B: Leave ingestion as-is, only fix the three consumers to agree with each other

Pick one label-matching heuristic and make `status.njk`, `meeting.njk`, and `process_transcripts.py` all call the same shared helper instead of three independent regexes.

**Pros**: Smaller change, no ingestion-script edits.
**Cons**: Still fragile (a labeling convention change breaks all three at once instead of independently); doesn't give the status page or sidebar a real `type` to filter/sort on; doesn't produce the "Approved"/"Unapproved" distinction cleanly.
**Effort**: Small.
**Risk**: Medium — fixes the immediate duplication but not the underlying "no real file index" problem.

### Option C: Leave as-is

Keep three independent label-matching implementations.

**Effort**: None.
**Risk**: High — this is the same pattern that's already caused real bugs twice this session (Meeting History matching, votes matching) applied to a fourth area.

## Acceptance Criteria
- [ ] `categorize_document()` (drive.py) and the spsd_site.py equivalent both recognize meeting-summary documents and tag them `minutes_unapproved`
- [ ] `minutes_approved` is reserved for genuine official minutes docs, with no auto-promotion/approval-tracking logic (that remains explicitly out of scope, per this session's earlier decision)
- [ ] Committee minutes are not classified as `minutes_approved`/`minutes_unapproved`
- [ ] `status.njk`, `meeting.njk`'s sidebar, and `process_transcripts.py`'s `_find_priority_source_doc()` all read the `type` field as the primary source of truth
- [ ] Sidebar and status page both display "Approved"/"Unapproved" consistently, from the same classification
- [ ] Existing meetings' `docs:` entries are backfilled/reclassified, not just newly-ingested ones

## Work Log
- 2026-08-07: Identified immediately after deciding (in conversation) to split the `minutes` doc type into `minutes_approved`/`minutes_unapproved`; this todo tracks doing that classification once at the source instead of via the three independent label-matching implementations found across `status.njk`, `meeting.njk`, and `process_transcripts.py`.
