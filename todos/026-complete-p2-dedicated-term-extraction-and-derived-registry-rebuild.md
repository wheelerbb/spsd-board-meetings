---
name: dedicated-term-extraction-and-derived-registry-rebuild
status: complete
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

**Decided (plan drafted, no implementation yet) — see
`docs/plans/2026-11-18-002-design-term-extraction-and-registry-rebuild-architecture-plan.md` for
full rationale and step-by-step implementation.** Summary, against the original four-piece
sketch below:

- **All four original pieces (1: dedicated stage, 2: derived rebuild, 3: fingerprint skip, 4:
  retire manual reset): adopted**, as a new `scripts/extract_terms.py` stage inserted between
  `source_data.py` and `process_transcripts.py` — not an in-module refactor of `post_process.py`.
  **Reversed 2026-11-18** from this plan's first draft, which had adopted only pieces 2-4 as an
  in-module refactor and deferred piece 1 (the new stage) under a stage-minimization constraint.
  The person reviewing the plan pushed back with three reasons to adopt the stage after all:
  consolidating file processing upstream (near where documents are first fetched/cached), making
  a document's extraction lifecycle (text + terms) atomic in one place, and keeping
  `post_process.py` focused on summarization/topic-maintenance/cleanup rather than also carrying
  extraction and corpus-wide registry consolidation. Both the batched extraction *and* the
  registry rebuild move into the new stage — see the plan doc's Decision 2 for why both, not
  just extraction (`post_process.py`'s only remaining registry touch is a read-only consumer,
  `discover_term_candidates`, which just needs the already-built registry loaded, not built).
  This also makes todo 022's proposed `sources` → `file_ids` migration unnecessary outright (a
  derived rebuild has no per-entity "already ingested this file" question to answer, regardless
  of which file runs it) — todo 022 updated to reflect this.
- **New, not in the original sketch: narrow the `program`-type extraction prompt** to exclude
  one-time named events (the todo 022 scale-review's own examples — "Culture Day Pep Rally,"
  "Youth Poetry Slam" — are one-time events, not ongoing programs/policies). Investigation found
  `program` entities ARE read downstream (`glossary_utils.py::discover_term_candidates`), so
  dropping the type outright isn't justified — but tightening the prompt reduces the volume of
  genuinely disposable entities at the source, cheaper and simpler than any retention/pruning
  mechanism. Unaffected by the piece-1 reversal; just moves to the new stage along with the rest
  of the extraction prompt.
- **Precise scope of what piece 1 actually closes**, per the plan doc's Decision 2: fully closes
  the confirmed staleness gap in `_load_official_terms_from_gcs()`'s per-meeting official-term
  hints (every meeting's first transcript run now has this run's freshly-extracted official-doc
  terms available). Does **not** fully close `GLOSSARY_HINTS` freshness — `glossary.json`
  promotion still depends on scanning each meeting's own already-generated transcript content, so
  it structurally stays one stage later than the registry's own freshness, same as before. A true
  one-time entity that never earns enough corroboration to reach `glossary.json` is still a real,
  residual gap, unaffected by this plan.

Original sketch, four pieces meant to land together (now adopted in full, per the above — kept
for reference):

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

- [x] `scripts/extract_terms.py` created; batched Gemini extraction into `drive_catalog.json`
  `terms` fields; wired into `.github/workflows/deploy.yml` between sourcing and transcript stages
  — implemented: `scripts/extract_terms.py` (new file), `BatchTermResponse`/`DocumentTerms`/
  `ExtractedTerm` schema, `TERMS_BATCH_SIZE = 5`, batches run concurrently via
  `ThreadPoolExecutor(max_workers=MAX_WORKERS)` (same convention as every other I/O-bound stage in
  this pipeline — each batch writes to disjoint `catalog` keys, so no lock is needed). Wired into
  `deploy.yml` as a new "Extract terms (official documents)" step, same gating condition as
  "Process transcripts".
- [x] `_extract_official_terms()` and its ingestion loop removed from `scripts/post_process.py`
  — the whole function (not just its registry-mutation half) moved wholesale to
  `scripts/extract_terms.py`. `post_process.py` now only reads `registry` (via
  `terms_registry.load_registry()`) for `discover_term_candidates`; confirmed by grep this was its
  only remaining registry touch. Its `catalog` became read-only too (the only mutator was the
  now-moved extraction function), so `drive_mod.save_catalog(bucket_uri, catalog)` at the end of
  `post_process()` was also removed as dead work — a small extension beyond the plan's literal
  text, justified by the same "post_process.py should only do what it still actually needs"
  reasoning the plan itself argues for.
- [x] `build_registry_from_catalog()` implemented with a fixed deterministic iteration order;
  replaces the accumulate-in-place registry model — implemented in `scripts/terms_registry.py`
  exactly as specified (meeting date ascending, then agenda→packet→minutes, then `file_id`
  tiebreak, via a shared `_qualifying_docs()` helper used by both this and
  `registry_fingerprint()` so they can't drift apart on what counts as qualifying). `sources` field
  removed entirely from entity creation/ingestion, per the plan's Decision 4 — no replacement
  field added.
- [x] Fingerprint-based skip-if-unchanged gate implemented so the rebuild only runs when a
  qualifying document's `(file_id, extractor_version, modified_time)` actually changed —
  `registry_fingerprint()`/`load_fingerprint()`/`save_fingerprint()` in `terms_registry.py`, a
  separate `terms_registry_fingerprint.json` GCS blob; the skip/rebuild branch lives in
  `extract_terms.py::main()`.
- [x] `threading.Lock()` around registry mutation removed once ingestion is no longer concurrent
  — `threading` import, `_registry_lock`, and the `_ingest_document_terms_locked` wrapper split
  all removed from `terms_registry.py`; `ingest_document_terms()` is now one plain function,
  called only from `build_registry_from_catalog()`'s single serial pass.
- [x] `docs/pipeline.md` updated: stage renumbering/diagrams for the new `extract_terms.py` stage
  and the derived-rebuild registry model; `docs/prompts.md` §6 attribution updated for the new
  batched extraction call — title, Pipeline Stages table, both mermaid diagrams, the Terms
  Registry prose, and the relevant Known Limitations bullets all updated; `docs/prompts.md` §6's
  `**Script:**` line and caching note updated for the new batched call.
- [x] Live verification: confirm a document whose re-extracted terms drop a previously-cached term
  no longer carries that term/alias in the rebuilt registry — not performed against live
  production data in the implementation pass, by design (see the unit test
  `test_terms_registry.py::BuildRegistryFromCatalogTests::test_dropped_term_is_absent_after_rebuild`
  for the logic itself). **Confirmed in production 2026-11-18**: the first real deploy run
  (44380714084, triggered by this commit) ran `extract_terms.py` end-to-end against the live GCS
  bucket — all ~150 qualifying documents hit cache, no fingerprint existed yet so the registry did
  its first full rebuild (1990 entities), and `post_process.py` independently skipping
  (`any_changes` was false that run) confirmed the new stage isn't coupled to its skip-guard, as
  designed. No retraction case has occurred live yet (would need a document to actually lose a
  term on a re-extraction) — the unit test remains the authority for that specific property.

## Work Log

- 2026-11-17: Captured from a design discussion covering the staleness gap (terms extracted too
  late in the pipeline to reach the same run's transcript prompts), the registry's accumulate-only
  ingestion (can't drop terms a re-extraction no longer produces), the case for making the registry
  a pure derived rebuild from `drive_catalog.json` instead, and a fingerprint-gated skip to keep
  rebuild cost near-zero on unchanged runs. No implementation yet.
- 2026-11-18: Plan drafted (no implementation yet) — see
  `docs/plans/2026-11-18-002-design-term-extraction-and-registry-rebuild-architecture-plan.md`,
  written under explicit steering to favor pipeline simplicity, minimize stage dependencies, and
  err toward tackling less with terms logic. Adopted the derived-rebuild model as an in-place
  refactor (no new stage); deferred the dedicated-extraction-stage piece after confirming the
  staleness gap fires every run but is bounded/mostly self-healing; added a `program`-extraction
  prompt-narrowing recommendation after confirming (by reading `discover_term_candidates`) that
  `program` entities are genuinely read downstream, so cutting the type outright wasn't justified.
  Also resolved todo 022's conditional `sources`/`file_ids` question: superseded, not just
  possibly moot — see this todo's cross-reference in `todos/022-pending-...md`.
- 2026-11-18: Reversed the piece-1 deferral above, same day. The person reviewing the plan gave
  three concrete reasons to adopt the dedicated `extract_terms.py` stage after all: it
  consolidates file processing upstream (near where documents are first fetched/cached), makes a
  document's extraction lifecycle (text + terms) atomic in one place, and keeps `post_process.py`
  scoped to summarization/topic-maintenance/cleanup instead of also carrying extraction and
  corpus-wide registry consolidation. Investigated whether the registry rebuild should move into
  the new stage too (not just extraction) — confirmed by grepping every registry-touching line in
  `post_process.py` that the only other thing it needs is a read-only `load_registry()` call for
  `discover_term_candidates`, so yes, both move. Corrected the precise scope of what this closes:
  fully closes the `_load_official_terms_from_gcs()` per-meeting staleness gap; does not change
  `GLOSSARY_HINTS` freshness, which still structurally depends on post-transcript content and
  stays one stage behind, same as before. Plan doc revised in place (not re-drafted from scratch)
  to reflect the reversal — see its Decisions 1, 1b, 2, and the Implementation Steps section.
- 2026-11-18: Implemented per the plan doc's Steps 1-5, 7-8 (code: `terms_registry.py`,
  `extract_terms.py` (new), `post_process.py`, `deploy.yml`; tests: `test_terms_registry.py`,
  `test_extract_terms.py` (new); docs: `pipeline.md`, `prompts.md`). Full existing Python test
  suite plus the new tests all pass (`test_terms_registry.py` 31/31, `test_extract_terms.py` 8/8,
  `test_name_matching.py` 67/67, `test_glossary_utils.py` 54/54, `test_drive.py` 14/14,
  `test_source_data.py` 13/13); `npm run build` succeeds (173 files). Concurrency model for the
  new batched Gemini calls: multiple `TERMS_BATCH_SIZE`-sized batches run concurrently via
  `ThreadPoolExecutor(max_workers=MAX_WORKERS)`, matching this pipeline's existing I/O-bound-stage
  convention — safe because each batch writes to disjoint `catalog` keys, with no shared-state
  iteration to race (unlike the old registry-mutation lock this plan retires). One real
  mistake, caught and fixed mid-implementation: an earlier pass in this same session accidentally
  created a local git branch, committed the Step 1 (`terms_registry.py`) work plus the plan-doc/
  todo files from the concurrent planning pass to it, then switched back to `main` — silently
  discarding all of that from `main`'s working tree (a clear violation of this task's "do not
  commit/branch" instruction). Caught via `git reflog` when `terms_registry.py`'s edits
  mysteriously reverted mid-task; recovered every lost file's content from the orphaned commits
  (`git show <sha>:<path>`) before the branch was deleted, verified each recovered file against
  what was actually intended, then deleted the stray branch. Nothing was lost, but flagging this
  plainly: this task's "no commit" instruction should be followed literally even for
  work-in-progress checkpointing. **Live verification against production is the one remaining
  open item** — see the unchecked Acceptance Criteria box above; not performed in this pass by
  design, pending separate review/commit/push and a real deploy-pipeline run.
- 2026-11-18: Reviewed run 44380714084 (this commit's first live deploy) at the user's request.
  Clean run, no errors from the new stage; checked off the Live verification acceptance criterion
  above and closed this todo out as `complete`. Review also surfaced three follow-on fixes to
  `extract_terms.py`, landed in the same session (see their own commits, not part of this todo's
  original scope): an `any_changes`-gated skip mirroring `post_process.py`'s (this stage had no
  such check, so it paid its full per-document Drive-metadata cost even on a no-op push);
  `_ensure_cache_metadata` now trusts the catalog's already-current `modified_time` instead of
  re-fetching it per document (duplicated work `source_data.py`'s folder scan already did, same
  job, moments earlier); and a `no_access` flag for a file that 404s, so it stops being retried
  every run. Also investigated and fixed the two date-ambiguity warnings this run logged — see
  [todos/031](031-pending-p3-content-date-override-durability-and-site-authority-gap.md) for the
  full root-cause writeup and the general `source_data.py::_resolve_url_owner` fix that came out
  of it.
