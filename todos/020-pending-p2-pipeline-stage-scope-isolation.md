---
name: pipeline-stage-scope-isolation
status: pending
priority: p2
issue_id: "020"
tags: [architecture, documentation, pipeline]
dependencies: ["013", "019"]
---

## Problem Statement

The pipeline mixes two structurally different kinds of work in the same files, often the same
function, with no documented or visible boundary between them:

- **Single-file/single-meeting stages**: read and write only one meeting's own artifacts (its
  `.njk` frontmatter, its own transcript/official docs). Safe to reason about, test, and re-run in
  isolation.
- **Cross-file/corpus-wide stages**: read or mutate state shared across every meeting in a run —
  the Terms Registry, `glossary.json`, `master_material_map.json`, topic recency/qualification
  sorting. Order-dependent, concurrency-sensitive, and a change to one meeting's processing can
  silently affect another's.

`scripts/post_process.py` is the clearest example: `post_process()` (post_process.py:840) calls
per-meeting generation (`generate_agenda_preview`, `synthesize_topic`, `generate_vote_evidence`,
`_enforce_glossary_pass` — all scoped to one meeting) in the same loop/run as `batch_tag_all_meetings`
(post_process.py:682, explicitly reads `meetings_data` for every meeting to compute topic
recency/qualification) and the glossary-discovery block (post_process.py:950-958) that combines
`discover_term_candidates` observations across *every* meeting being processed, adds
`discover_recurring_speaker_candidates`'s explicitly cross-meeting fallback, and mutates the one
shared `glossary.json`. Nothing in the code or docs marks where "this only touches the current
meeting" stops and "this reads/writes something shared across the whole corpus" starts.

This isn't hypothetical risk — every one of the following was a production bug with exactly this
shape, a cross-file mechanism applied with per-file assumptions (or the reverse):
- [todos/013](013-pending-p2-consolidate-glossary-artifact.md): a corpus-wide static hint
  (`GLOSSARY`, applied blanket to every meeting regardless of date) caused a date-blind Superintendent
  misattribution — treated as safe because it *looked* like a per-meeting concern.
- [todos/016](016-pending-p2-stale-topic-evidence-on-reprocess.md): stale `topic_evidence` on
  reprocess — a single meeting's reprocessing silently corrupted evidence derived from the
  cross-meeting topic-aggregation step.
- [todos/019](019-pending-p2-terms-registry-and-living-glossary.md): `ingest_document_terms`
  needed a thread lock once it became clear `post_process.py`'s `ThreadPoolExecutor` mutates the
  one shared registry dict concurrently across meetings that look, from the call site, like
  independent per-meeting work.

## Findings

Rough first pass at classifying the stages involved in this session's work (not exhaustive — the
point of this todo is to make this list real and complete, not to finalize it here):

- **Single-file/single-meeting**: `process_transcripts.process_single_meeting`,
  `post_process.generate_agenda_preview`, `synthesize_topic`, `generate_vote_evidence` — read this
  meeting's own docs/transcript, write this meeting's own `.njk`.
- **Cross-file/corpus-wide**: `post_process.batch_tag_all_meetings` (topic recency/qualification
  needs every meeting's tags), the glossary-discovery block (post_process.py:950-958,
  `glossary_utils.discover_term_candidates`/`discover_recurring_speaker_candidates`/
  `apply_discovered_terms`), `terms_registry.ingest_document_terms` (one shared, lock-protected
  registry dict), `source_data.reconcile_meetings` (writes `master_material_map.json`, corpus-wide).
- **Deliberately hybrid**: `glossary.json`'s `scope: [slug, ...]` field is a middle ground by
  design — a correction starts bounded to the meeting(s) that produced its evidence and is promoted
  to global once independently confirmed (`PROMOTION_THRESHOLD`, see todos/019). Worth documenting
  as its own category rather than forcing it into the single-file/cross-file binary.
- `_enforce_glossary_pass` (post_process.py:354) is itself mixed: it only *writes* the current
  meeting's `.njk`, but *reads* the shared `glossary.json` — a stage can be read-cross-file,
  write-single-file, which the single-file/cross-file label alone doesn't capture.

## Proposed Solution

Not proposing an implementation here — this is a planning/documentation todo. Three options for
how far to take it:

### Option A: Documentation-only pass (Recommended to start)
Add a "Pipeline Stage Scope" section/table to `docs/pipeline.md` enumerating every stage/function
and classifying it (single-file / cross-file-read / cross-file-mutating / hybrid), cross-referencing
the three bugs above as concrete cautionary examples. No code changes.
**Effort**: Small. **Risk**: None.

### Option B: Documentation + structural separation
Do (A), then physically separate per-meeting stages from cross-meeting stages in
`post_process.py`'s main loop (e.g. two distinct phases instead of one function interleaving both),
so the scope is visible in code structure, not just prose. Touches the main pipeline entrypoint.
**Effort**: Medium. **Risk**: Low-medium — mechanical reordering, but it's the entrypoint every
meeting's generation depends on, so needs careful re-verification (same bar as todos/019's
trailing-5-meetings local verification before touching live state).

### Option C: Formal per-stage interface/contract
Introduce an explicit abstraction (e.g. a decorator or base class marking a function's scope) that
could be lint- or test-enforced. Likely overkill for the current codebase size/team — listed for
completeness, not recommended.
**Effort**: Large. **Risk**: Medium — new abstraction to maintain for a problem Option A/B already
solve at the "humans can see it" level.

## Acceptance Criteria
- [ ] `docs/pipeline.md` has a section/table enumerating every pipeline stage and classifying its
  scope (single-file, cross-file-read, cross-file-mutating, or hybrid like `glossary.json`'s `scope`
  field)
- [ ] That section cross-references todos/013, /016, /019 as concrete examples of scope-blurring
  causing real bugs
- [ ] A decision is recorded on whether Option A (docs only) is sufficient or Option B (structural
  separation in `post_process.py`) is worth doing
- [ ] If Option B is chosen, `post_process()`'s main loop visibly separates per-meeting stages from
  cross-meeting stages, re-verified against a local snapshot before any live-state change (same
  verification discipline as todos/019)

## Work Log
- 2026-11-16: Created via side-chat request while the `terms-registry-rebuild` branch work
  (todos/019) was in flight in the main thread — that session's own work kept running into this
  exact single-file/cross-file blurring (the registry concurrency race, the glossary's cross-meeting
  recurring-speaker fallback, `discover_term_candidates` reading corpus-wide but being called inside
  a per-meeting loop), which is what prompted the request. No implementation done yet.
