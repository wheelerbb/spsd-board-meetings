---
name: dedicated-term-extraction-and-derived-registry-rebuild
status: pending
priority: p2
issue_id: "026"
tags: [terms-registry, pipeline, architecture, performance]
dependencies: ["019"]
---

## Problem Statement

A design discussion (not yet implemented) surfaced two related weaknesses in how official-document
terms get extracted and fed into `terms_registry.json`:

1. **Staleness gap**: term extraction currently happens inside `post_process.py` (stage 3), after
   `process_transcripts.py` (stage 2) has already run. Any official term discovered for the first
   time in a given pipeline run can't reach that same run's transcript-extraction prompts — it's
   only available starting the *next* run.
2. **Accumulate-only registry**: `ingest_document_terms()` only ever adds. If a document gets
   re-extracted (revised upload, or a `TEXT_EXTRACTOR_VERSION` bump) and the new extraction no
   longer produces a term it previously did (false positive fixed, OCR garbage corrected, hedge
   fragment filtered), the old term/alias/source has no path to leave the registry. Today this is
   only handled for the corpus-wide case (extractor-version bump → full manual reset-and-rebuild,
   per todos/019 Phase 2) — there's no standing mechanism for the same problem at the single-document
   level.

## Findings

- Confirmed via `docs/pipeline.md`'s pipeline-sequencing diagram and `.github/workflows/deploy.yml`
  that stage order (`source_data.py` → `process_transcripts.py` → `post_process.py`) is fixed, and
  `_extract_official_terms()` (inside `post_process.py`) is the only place official terms get pulled
  from cached GCS text and ingested into the registry.
- The existing cache-hit guard (`modified_time == current_modified` AND `extractor_version ==
  TEXT_EXTRACTOR_VERSION`) already forces a fresh extraction + re-ingestion whenever a document's
  text changes or the extractor itself is bumped — so *re-extraction* already happens correctly.
  What's missing is *retraction*: re-ingesting new terms is additive on top of whatever the stale
  extraction already wrote into the registry (merged aliases, `context` fragments, `sources`
  entries) — nothing ever removes what a document no longer supports.
- The unconditional per-run replay of cached terms into the registry (`ingest_document_terms()`
  called every run, cache hit or miss) exists only to self-heal a reset/rebuilt registry — not
  because accumulation is the right persistence model. If the registry were instead a pure derived
  view of `drive_catalog.json`'s current cached terms, this replay cost and the retraction gap
  would both disappear by construction.
- True incremental/scoped registry patching (retract just the affected entity, re-add new terms)
  was considered and set aside: the matcher chain (`_person_match`, `fuzzy_match`, cross-type
  reconciliation in `terms_registry.py`/`name_matching.py`) is global and insertion-order-dependent.
  An entity touched by one file's terms may have absorbed another file's terms via fuzzy matching
  *because* of the now-stale state — a scoped patch to one entity can't guarantee the same result a
  full rebuild would produce.

## Proposed Solutions

Sketch only — no code written yet. Four pieces, meant to land together:

1. **Dedicated `scripts/extract_terms.py`**, run between `source_data.py` and `process_transcripts.py`.
   Pulls cached GCS text for official docs (agenda/packet/minutes), batches multiple documents per
   Gemini call for cost efficiency (model on `batch_tag_all_meetings()`'s `BatchTagResponse`
   pattern — e.g. a new `BatchTermResponse`/`DocumentTerms`/`ExtractedTerm` schema and a
   `TERMS_BATCH_SIZE` constant), and writes results into each `drive_catalog.json` entry's `terms`
   field (same `modified_time`/`extractor_version` cache-gating as today). Removes the
   `_extract_official_terms()` loop from `post_process.py` entirely. Gated on the scheduled-run
   `any_changes` signal like the other LLM-calling stages; always runs on push/manual dispatch.
2. **Registry as a derived rebuild, not an accumulated ledger**: replace the "load registry, mutate,
   save" model with `build_registry_from_catalog(catalog)` — starts from an empty `{}`, iterates
   every qualifying catalog entry (agenda/packet/minutes with cached `terms`) in a **fixed
   deterministic order** (meeting date ascending, then doc_type priority agenda→packet→minutes,
   then `file_id` as a final tiebreak), and replays `_ingest_document_terms_locked()` once per doc.
   Because the output is wholly reconstructed from the catalog's *current* state, a term a document
   no longer produces simply isn't re-added — no explicit retraction logic required. Also removes
   the need for the `threading.Lock()` around registry mutation (the lock exists because ingestion
   today happens inside `post_process.py`'s `ThreadPoolExecutor` workers; a rebuild is a single
   serial pass after extraction is done) — directly eliminates the class of bug behind the Nov 13
   production incident (thread-unsafe registry mutation, documented in todos/019's work log).
3. **Skip the rebuild when nothing changed**: store a fingerprint — e.g. the set of
   `(file_id, extractor_version, modified_time)` for every qualifying catalog entry used in the last
   rebuild — alongside the registry. Compare against the current catalog each run; if identical,
   skip the rebuild and keep the existing `terms_registry.json` (it's guaranteed to already equal
   what a fresh rebuild would produce, since the rebuild is deterministic). Only pay the full-rebuild
   cost on runs where at least one qualifying document actually changed — zero cost on the common
   case (no doc changes that tick). Avoids the correctness risk of true incremental/scoped updates
   (see Findings) while still addressing the "full rebuild every run" cost concern.
4. **Do not build automatic cross-run retraction for the corpus-wide extractor-version-bump case** —
   once (2) and (3) land, a version bump just becomes "every qualifying entry's fingerprint changed,"
   which naturally triggers one full rebuild from the now-correctly-extracted catalog. This
   supersedes the need for the manual reset-and-rebuild workflow todos/019 Phase 2 used as a
   one-off incident response — it becomes the standing behavior instead of a special case.

## Acceptance Criteria

- [ ] `scripts/extract_terms.py` created; batched Gemini extraction into `drive_catalog.json`
  `terms` fields; wired into `.github/workflows/deploy.yml` between sourcing and transcript stages
- [ ] `_extract_official_terms()` and its ingestion loop removed from `scripts/post_process.py`
- [ ] `build_registry_from_catalog()` implemented with a fixed deterministic iteration order;
  replaces the accumulate-in-place registry model
- [ ] Fingerprint-based skip-if-unchanged gate implemented so the rebuild only runs when a
  qualifying document's `(file_id, extractor_version, modified_time)` actually changed
- [ ] `threading.Lock()` around registry mutation removed once ingestion is no longer concurrent
- [ ] `docs/pipeline.md` updated: stage renumbering/diagrams for the new `extract_terms.py` stage
  and the derived-rebuild registry model; `docs/prompts.md` §6 attribution updated for the new
  batched extraction call
- [ ] Live verification: confirm a document whose re-extracted terms drop a previously-cached term
  no longer carries that term/alias in the rebuilt registry

## Work Log

- 2026-11-17: Captured from a design discussion covering the staleness gap (terms extracted too
  late in the pipeline to reach the same run's transcript prompts), the registry's accumulate-only
  ingestion (can't drop terms a re-extraction no longer produces), the case for making the registry
  a pure derived rebuild from `drive_catalog.json` instead, and a fingerprint-gated skip to keep
  rebuild cost near-zero on unchanged runs. No implementation yet.
