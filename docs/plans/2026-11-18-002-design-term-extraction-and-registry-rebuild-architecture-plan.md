---
title: "design: term extraction staleness, derived registry rebuild, and registry scope"
type: design
status: proposed
date: 2026-11-18
origin: todos/026-pending-p2-dedicated-term-extraction-and-derived-registry-rebuild.md
---

# design: term extraction & registry rebuild architecture

## Steering this plan was written under

The person requesting this plan gave explicit, binding constraints, not just preferences:
favor pipeline simplicity and intuitive architecture; minimize dependencies between pipeline
stages; and when a narrower vs. a more complete solution are both on the table, err toward the
narrower one — tackle less with terms logic, capture fewer terms. Every recommendation below is
made under those constraints, not in spite of them.

**2026-11-18 revision note:** this plan's first draft weighed "minimize stage dependencies"
against adding a dedicated `extract_terms.py` stage and deferred the stage. The person reviewed
that call and pushed back with three concrete, reasoned points (upstream consolidation of file
processing, atomicity of a document's extraction lifecycle, and keeping `post_process.py` scoped
to summarization/topics/cleanup) — see the revised Decision 2 below. This is a considered
exception to the stage-minimization constraint for this one piece, made explicitly and for stated
reasons, not a reversal of the steering itself. The steering still governs everything else in
this plan (Decisions 1, 3, 4 are unchanged).

## Decisions (summary)

1. **Adopt the derived-rebuild registry model (todo 026 pieces 2+3+4).** This fixes a real,
   already-incident-causing thread-safety bug and is a net simplification (removes a lock,
   removes the need for retraction logic by construction, removes the entire reason `sources`
   exists). **Location revised 2026-11-18**: this logic now lives in the new stage from Decision
   2 below, not in `post_process.py` — see that section for why.
2. **Adopt the dedicated `extract_terms.py` stage (todo 026 piece 1) — reversed from the initial
   deferral.** The staleness gap it closes is real and fires every run (confirmed, not
   hypothetical). Initially deferred under the stage-minimization constraint; the person
   overrode that call with three stated reasons (consolidate file processing upstream, make a
   document's extraction lifecycle atomic, keep `post_process.py` focused on
   summarization/topics/cleanup rather than extraction *and* corpus-wide consolidation). Both the
   batched term extraction *and* the registry rebuild from Decision 1 move into this new stage —
   see Decision 2 for why both, not just extraction.
3. **Narrow the `program`-type extraction prompt, don't drop the type or build retention
   logic.** Investigation found `program` entities are genuinely read downstream (not dead
   weight), so cutting the type outright isn't justified — but the prompt can be tightened at
   the source to stop generating disposable one-time-event entities in the first place, which is
   cheaper and simpler than any downstream pruning mechanism. Unaffected by the Decision 2
   reversal — the prompt just moves to wherever extraction now lives.
4. **This plan supersedes todo 022's conditional `sources` → `file_ids` migration.** Once the
   registry is a from-scratch-every-rebuild derived view, there is no "have I already folded
   this file in" question left to answer with a persisted ledger. Todo 022's Step 3 (live
   migration) should be skipped entirely, not merely deferred. Unaffected by the Decision 2
   reversal — this holds regardless of which file the rebuild code lives in.

## Problem recap

Todo 026 names two weaknesses, already described in detail in its own Problem Statement/Findings
— not repeated here:

1. **Staleness gap**: official-term extraction happens in `post_process.py` (stage 3), after
   `process_transcripts.py` (stage 2) has already run — so a term discovered for the first time
   this run can't reach this same run's transcript-extraction prompts.
2. **Accumulate-only registry + thread safety**: `ingest_document_terms()` only ever adds, under
   a lock, inside a `ThreadPoolExecutor`. The Nov 13 production incident (thread-unsafe registry
   mutation, see todos/019's work log) is a direct consequence of this model, not a one-off bug
   in the lock's implementation.

**Current code, verified against the live files** (this todo's own line numbers have drifted —
several intervening fixes landed since it was written: todos/021, 027, 028):

```python
# scripts/post_process.py:927-943 (inside post_process(), step 2)
if bucket_uri:
    term_targets = [m for m in meetings_data if any(d.get('type') in ('agenda', 'packet', 'minutes')
                                                     for d in (m.get('docs') or []))]
    def _extract_terms_task(m):
        try:
            _extract_official_terms(m, bucket_uri, catalog, registry)   # <- mutates registry, concurrently
        except Exception as e:
            print(f"  Warning: term extraction failed for {m.get('slug')}: {e}", flush=True)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(_extract_terms_task, m) for m in term_targets]
        for future in as_completed(futures):
            future.result()
```

`_extract_official_terms()` (`post_process.py:403-502`) does two genuinely separate jobs in one
function, called concurrently per meeting: (a) ensure `catalog[file_id]['terms']` is fresh for
each qualifying doc — a Gemini call on cache miss, a plain cache read on hit — and (b)
`terms_registry.ingest_document_terms(registry, ...)` at lines 464 and 498, mutating the shared
registry dict under `terms_registry._registry_lock`. Only (b) needs the lock; (a) never touches
`registry` at all. **Its return value (`all_terms`) is dead code** — the one call site
(`_extract_terms_task`, line 937) discards it — confirmed by grep: no other call site of
`_extract_official_terms` exists anywhere in the codebase.

`registry` itself is loaded once at the top of `post_process()` (`load_registry(bucket_uri)`,
line 854) and not read again until the term-extraction block — nothing between those two points
depends on its prior (accumulated) state.

## Decision 1 detail: adopt the derived-rebuild model

**Where this now lives (2026-11-18 revision):** the design below — rejected alternatives,
`build_registry_from_catalog()`'s deterministic ordering, the fingerprint skip — is unchanged by
the Decision 2 reversal. Only the caller's location changed: this logic now lives in the new
`scripts/extract_terms.py` stage, not in `post_process.py`. Read this section for *what* the
rebuild does and why; see Decision 2 for *where* it runs and why that changed.

**Rejected alternative — keep accumulate-in-place, just widen/fix the lock.** The lock already
exists and already works for its narrow job (preventing the "dictionary changed size during
iteration" crash); what it can't fix is the *correctness* problem the todo's Findings already
ruled out a scoped-patch fix for: the matcher chain (`_person_match`, `fuzzy_match`,
cross-type reconciliation) is global and insertion-order-dependent, so an entity's state can
depend on which thread's term happened to be ingested first. A lock prevents a crash; it doesn't
make concurrent, order-dependent merges deterministic. Not acceptable as a long-term model for
data this project treats as ground truth (see `glossary.json` auto-discovery, which reads
straight from registry `term` values into every future Gemini prompt).

**Rejected alternative — true incremental/scoped patching** (retract just the affected entity,
re-add new terms). Already correctly rejected in todo 026's own Findings for the same
order-dependence reason — re-confirmed here by reading the matcher chain directly
(`terms_registry.py:162-258`): an entity touched by file A's terms may have absorbed file B's
terms via fuzzy matching *because of* now-stale state; a scoped patch to one entity can't
guarantee the same output a full rebuild would produce. Not revisiting this.

**Chosen: move extraction wholesale into the new `scripts/extract_terms.py` stage (Decision 2),
and rebuild the registry once, serially, right after that stage's extraction work completes —
both inside the same new stage, before `process_transcripts.py` ever runs.**

`_extract_official_terms()`'s current body (the per-document Drive-metadata-fetch/cache-check
loop, the Gemini call, and the two `terms_registry.ingest_document_terms(...)` calls at `:464`/
`:498`) moves to the new stage wholesale, not just split in place. The `ThreadPoolExecutor` block
that currently drives it also moves — it keeps doing exactly the same real I/O-bound work (Gemini
calls on cache miss), just as the new stage's own concurrency, not `post_process.py`'s. Once
extraction is no longer concurrent-with-registry-mutation (because registry-building now happens
serially, once, *after* the new stage's extraction work finishes, in the same stage),
`terms_registry._registry_lock` and its `threading` import become fully dead and should be
deleted — there is no more concurrent registry mutation to guard, anywhere.

After that stage's extraction work finishes (every qualifying document's `catalog[file_id]
['terms']` is now guaranteed fresh), one new serial call — still inside `extract_terms.py` —
replaces the old accumulate-from-whatever-was-loaded model:

```python
registry = terms_registry.build_registry_from_catalog(catalog, meetings_data)
```

`build_registry_from_catalog()` (new function in `terms_registry.py`): starts from `{}`, iterates
every meeting in `meetings_data` in **meeting-date ascending order** (meetings already carry
`slug` as their date string — `sorted(meetings_data, key=lambda m: m['slug'])`, no new data
needed), then within each meeting its qualifying docs in a **fixed `doc_type` priority**
(`agenda` → `packet` → `minutes` — matches the priority order `_find_priority_source_doc` already
uses elsewhere in this pipeline, so this isn't inventing a new convention), then `file_id` as a
final tiebreak for same-meeting-same-type duplicates (todo 022's confirmed `2024-04-08` case: two
`packet` file_ids, two `minutes` file_ids). For each qualifying doc, call the existing
`_ingest_document_terms_locked()` directly (rename it `_ingest_document_terms` — drop the
now-meaningless `_locked` suffix — or keep the name and just stop routing through the lock
wrapper; either is fine, picking the rename for clarity) once per doc, in that order. No other
change to the matcher chain itself — this only changes *when and how many times* ingestion runs,
not what it does per call.

Because the output is wholly reconstructed from the catalog's *current* cached `terms`, a term a
document no longer produces simply isn't re-added on the next rebuild — no explicit retraction
logic required, exactly as todo 026 originally argued.

## Decision 1b: the fingerprint-gated skip (todo 026 piece 3)

**Where this now lives (2026-11-18 revision):** inside `scripts/extract_terms.py`, same as
Decision 1's rebuild — not `post_process.py`. The design below is otherwise unchanged by that
move.

A full rebuild is cheap in absolute terms (196 entities from 5 documents in todo 022's sample —
this is in-memory string matching, not an LLM call), but it's still wasted work on every run where
nothing qualifying changed, which per `docs/pipeline.md`'s own scheduled-run gating is the common
case. Store a **separate, small GCS blob** — `terms_registry_fingerprint.json`, sibling to
`terms_registry.json`, not a field bolted onto the registry itself — holding the sorted list of
`(file_id, extractor_version, modified_time)` for every qualifying catalog entry used in the last
rebuild. (Keeping it separate rather than embedding a non-entity key inside the registry mirrors
this project's existing preference for one owning file per concern — the registry's own schema,
settled in todos/021/022/028, stays purely about entities.)

Each run, inside `extract_terms.py`, after its own extraction work: compute the current
fingerprint from `catalog`, compare against the stored one.
- **Match**: skip the rebuild, `registry = terms_registry.load_registry(bucket_uri)` — it's
  guaranteed to already equal what a fresh rebuild would produce, since the rebuild is
  deterministic.
- **Mismatch**: rebuild via `build_registry_from_catalog()`, `save_registry(...)`, write the new
  fingerprint.

This also directly delivers todo 026's piece 4 for free: a `TEXT_EXTRACTOR_VERSION` bump changes
every qualifying entry's `extractor_version`, which is already part of the fingerprint tuple — so
a version bump simply becomes "every entry's fingerprint changed," triggering one ordinary full
rebuild through the standing code path. **The manual reset-and-rebuild incident-response workflow
from todos/019 Phase 2 becomes unnecessary going forward** — not something to keep around as a
backstop, since the standing path now handles exactly that case by construction.

## Decision 2 detail: adopt the dedicated `extract_terms.py` stage (reversed 2026-11-18)

**What's confirmed, not assumed — unchanged from the original analysis.** Read
`process_transcripts.py::_load_official_terms_from_gcs()` (lines 97-129) directly: it reads
`catalog[file_id]['terms']` — the *exact same* field `_extract_official_terms()` writes in stage
3. `docs/prompts.md` §1 confirms the same thing from the consumer side: these terms are "injected
into the transcript extraction prompt (§1) as authoritative, meeting-scoped spelling hints." Since
stage 2 always runs before stage 3 in a given pipeline invocation, **every meeting's first-ever
transcript-extraction run genuinely has zero official-term hints available for that meeting's own
newly-discovered agenda/packet/minutes** — this is not a rare edge case, it fires for every
meeting, every time, by construction of the fixed stage order. This is a stronger finding than
todo 022's deferred `context` risk, which had no confirmed firing mechanism at all.

**Reversal.** The first draft of this plan weighed that confirmed-but-bounded gap (see "What was
bounded, not catastrophic" below — still true, kept as supporting context, not as the basis for
deferring anymore) against the steering's "minimize stage dependencies" constraint and deferred
piece 1. The person reviewing this plan pushed back with three concrete reasons to adopt it
after all:

1. **Consolidate file processing upstream** — term extraction belongs near where documents are
   first fetched/cached (`source_data.py`'s domain), not bolted onto a later, unrelated stage.
2. **Make file processing more atomic** — a document's full extraction lifecycle (text, then
   terms) should complete in one place, not span two non-adjacent stages.
3. **Simplify `post_process.py`** — it should focus on summarization, topic maintenance, and
   cleanup, not also carry document/term extraction *and* corpus-wide registry consolidation.

These are separation-of-concerns/atomicity arguments, not a rejection of "minimize stage
dependencies" in general — they're a considered exception for this one piece, made for stated
reasons. Adopting.

**Design choice this reversal forces: does the registry rebuild (Decision 1) move into the new
stage too, or stay in `post_process.py` reading an already-fresh catalog?** Reason 3 above
names "corpus-wide registry consolidation" as something that shouldn't stay in `post_process.py`
— so the steering already answers this directly, but it's worth confirming against the actual
code rather than taking the framing on faith. Grepped every `registry`-touching line in
`post_process.py`: besides `_extract_official_terms()` itself (moving wholesale) and the final
`save_registry()` call, there is exactly one other read — `discover_term_candidates(m, registry)`
(`post_process.py:953`, inside glossary-discovery step "2b"), which only needs the already-built
registry *as a value to read*, matched against each meeting's own just-generated transcript
content. It does not need to build or mutate the registry itself. So: **yes, both extraction and
the rebuild move into `scripts/extract_terms.py`.** `post_process.py`'s entire registry footprint
afterward is the one line it already has at the top of `post_process()`
(`registry = terms_registry.load_registry(bucket_uri)`, `:854`) — now a plain read of a registry
the new stage already finished building, rather than something `post_process.py` builds or saves
itself. The `save_registry()` call at `:1154` is deleted — `post_process.py` never mutates
`registry` anymore, so there's nothing to save back. Glossary-discovery (step 2b) and glossary
*application* (`_enforce_glossary_pass`, `apply_discovered_terms`) both stay in `post_process.py`
— they operate on this run's generated meeting content, which doesn't exist until
`process_transcripts.py` has already run, so they structurally cannot move upstream of it. This
is extraction-and-consolidation moving out, not discovery-and-cleanup — consistent with reason 3,
not in tension with it.

**What this precisely closes — and what it doesn't.** Be exact about which staleness mechanism
this fixes, rather than claiming it closes more than it does:
- **Closes fully**: the `catalog[file_id]['terms']` → `_load_official_terms_from_gcs()` path
  above. Once extraction runs in its own stage before `process_transcripts.py`, every meeting's
  transcript-extraction prompt has this run's freshly-extracted official-doc terms available,
  every time, with no exceptions. This was the concretely-named problem in todo 026's Problem
  Statement.
- **Does not fully close**: `GLOSSARY_HINTS` freshness. `process_transcripts.py` renders
  `GLOSSARY_HINTS` from `glossary.json` once at import time — but `glossary.json` itself is only
  ever updated by `post_process.py`'s glossary-discovery step (2b), which compares the registry
  against each meeting's own *already-generated* transcript content. That step cannot run before
  `process_transcripts.py` produces that content, regardless of where the registry is built —
  moving the rebuild upstream doesn't change this, because the dependency is on generated
  content, not on registry freshness. So a brand-new entity's `glossary.json` promotion still
  always happens one stage later than its registry entity does, same as before this plan.
- **What was bounded, not catastrophic (kept as context for why the residual gap above is
  tolerable, not as the reason to defer anymore)**: `GLOSSARY_HINTS` still covers every
  *already-known* recurring person/place/org correctly — the gap only bites a term's *first-ever*
  appearance, where no registry/glossary entry could exist regardless of timing. And
  `post_process.py::_enforce_glossary_pass()` (`:911`) runs at the top of *every* run, rewriting
  already-written `.njk` files against the current `glossary.json` — so a term transcript-mangled
  on its first-appearance run, then correctly extracted and promoted that same run, gets swept
  back over that meeting's (and every other meeting's) content on the *next* run. Same cross-run
  self-heal mechanism already relied on for todo 028's `Molly. Schen` fix.
- **What isn't fully closed by self-healing, still true**: a genuine one-time entity (mentioned in
  exactly one meeting, never again) may never accumulate enough corroborating evidence to be
  promoted to `glossary.json`, in which case that one meeting's transcript-derived content keeps
  whatever spelling the auto-caption produced, permanently. This residual gap is unaffected by
  this plan either way — it was true before this reversal and stays true after it.

**New stage's two responsibilities, run in sequence, both before `process_transcripts.py`:**

1. **Batched term extraction into `drive_catalog.json`.** `_extract_official_terms()`'s current
   per-document body (Drive-metadata fetch, `text_cache_fresh` check, `get_or_extract_text` on a
   cache miss, the Gemini call, caching the result on `entry['terms']`) moves here wholesale.
   Today's version calls Gemini once per document on a cache miss — no batching exists yet. This
   plan adds genuine batching, per the original todo sketch, modeled on
   `post_process.py::batch_tag_all_meetings()`'s existing pattern (one Gemini call covering many
   items via a Pydantic `results: list[...]` response schema, `post_process.py:674-676`,
   `:683-749`): a new `ExtractedTerm { term, type, context }` / `DocumentTerms { file_id, terms:
   list[ExtractedTerm] }` / `BatchTermResponse { results: list[DocumentTerms] }` schema, and a
   `TERMS_BATCH_SIZE` constant (unlike `batch_tag_all_meetings`'s single all-corpus call, this
   needs real chunking — raw agenda/packet/minutes document text is far more token-heavy per item
   than a condensed transcript summary, so one call per `TERMS_BATCH_SIZE`-sized group of
   cache-miss documents, not one call for the whole corpus).
2. **Fingerprint-gated registry rebuild** (Decision 1 / Decision 1b), using the now-fresh
   `catalog`.

**docs/pipeline.md implication**: this repo's own reference doc is titled "the three-stage
ingestion and synthesis pipeline" (`docs/pipeline.md:3`) — adopting this stage makes it four. See
the revised Implementation Steps below for the exact diagram/table changes; this is accepted as
the correct outcome of the person's reasons above, not a cost to minimize.

## Decision 3 detail: narrow `program` extraction, don't drop it or build retention

**Investigated, not assumed.** Grepped every reader of `program`-type registry entities:
`glossary_utils.py:249` (`discover_term_candidates`) includes `program` in its `other` list,
matched against each meeting's own generated content via the same sliding-window comparator used
for `place`/`organization`. This is real, exercised correction capability — a one-off program or
policy name, correctly spelled in its source agenda/packet, can still catch and flag a transcript
mishearing of that same name within the meeting(s) that reference it. **Cutting the `program`
type outright would remove real capability, not dead weight** — todo 022's framing of it as "a
cost paid for nothing" doesn't hold up once the actual downstream reader is checked; don't act on
that framing uncorrected.

**But the growth concern from todo 022 is also real and unaddressed by anything else in this
plan.** Decision 1's rebuild changes *how* the registry is built, not *what* ends up in it — a
derived rebuild of a corpus with 40% one-off `program` entities still produces a registry with
40% one-off `program` entities. Nothing in Decisions 1-2 bounds that growth.

**Rejected — a retention/pruning mechanism** (e.g. drop a `program` entity N meetings after its
`last_seen` with no further corroboration). This is exactly the kind of new terms-logic the
steering says to avoid: it needs a threshold, a notion of "done," and interacts with the
derived-rebuild model awkwardly (a rebuild has no "age" concept across runs unless one is added
back in, partially reintroducing the kind of cross-run bookkeeping Decision 1 just removed).

**Chosen: tighten the extraction prompt itself** (`post_process.py:474-490` today, the
`_extract_official_terms` Gemini prompt — per Decision 2's reversal, this prompt moves into
`scripts/extract_terms.py`'s batched extraction call; the wording change below is independent of
that move, just land it in the new location). Current instruction: `"Named programs, policies, or initiatives"` — add an explicit exclusion for
single-occurrence named events (the todo's own examples, "Culture Day Pep Rally," "Youth Poetry
Slam," are one-time events, not programs/policies/initiatives in the ongoing sense the category
name implies). Suggested wording to replace that bullet:

```
"- Named programs, policies, or ongoing initiatives (not one-time events like a single
  assembly, fundraiser, or celebration — only things with standing beyond one occurrence)"
```

This is a root-cause-level fix (stop generating the low-value entity in the first place, not clean
one up after) and costs nothing but a prompt edit — no new registry field, no new matching logic,
no new pipeline step. It won't perfectly distinguish every case (Gemini's own judgment call, same
as every other extraction heuristic in this prompt already), but it directly targets the category
todo 022 actually measured (named one-time events), is cheap to try, and has no downside worse
than today's status quo if Gemini still extracts some edge cases. No migration needed — this only
affects future extractions; existing cached `program` entities are left alone (acceptable under
"tackle less" — no forced cleanup of what's already there).

## Decision 4 detail: this plan resolves todo 022's `sources`/`file_ids` question — don't migrate

Todo 022's plan (`docs/plans/2026-11-18-001-...md`, Decision 1) proposed `sources` →
`file_ids` specifically to preserve one property: the idempotency guard that stops
`ingest_document_terms()` from re-appending the same file's contribution on every cache-hit
re-run of an *accumulate-in-place* registry. **Once Decision 1 above lands, there is no
accumulate-in-place registry left to guard.** `build_registry_from_catalog()` starts from `{}`
every time it runs and visits each qualifying document exactly once per rebuild, by construction
— there is no "have I already folded this file in?" question for it to ask, because nothing
persists between one rebuild and the next except the GCS-saved *output*, never an in-progress
ledger. The entire reason `sources` (or its proposed `file_ids` replacement) needs to exist
disappears.

This was flagged as a live possibility in the 022 plan ("the entire reason `sources`/`file_ids`
needs to exist ... may become moot") and was also checked against todo 028's landed diff
(`scripts/terms_registry.py:244`, `source = {'file_id': ..., 'meeting': ..., 'doc_type': ...}`) —
028 did **not** add per-observation raw-term tracking, so that conditional branch of the 022 plan
is moot on its own terms too, independent of this plan.

**Recommendation: skip todo 022's Step 1-3 (the `file_ids` migration) entirely once this plan's
Decision 1 lands.** The `sources` field (and everything about it) is simply deleted as part of
Decision 1's entity-creation code (`terms_registry.py:234-239` and the per-doc append at
`:244-247`) — there's no replacement field to migrate *to*, because nothing downstream needs any
per-entity file-tracking once ingestion is a single serial pass per rebuild. This is strictly less
work than todo 022's plan, and it's the kind of "fewer moving parts" outcome the steering asks for
directly: one field removed, zero fields added, zero live-data migration needed (the field simply
stops being written going forward; existing live entities keep a harmless unused `sources` key
until the next rebuild overwrites them entirely, which is every run once Decision 1 ships).

Made the small, surgical correction to todo 022 and its plan doc this implies — see below.
**Unaffected by the Decision 2 reversal**: this reasoning is purely about the registry's
persistence model (derived rebuild vs. accumulate-in-place), not about which file or stage runs
the rebuild — it holds exactly the same whether the rebuild executes inside `post_process.py` or
`scripts/extract_terms.py`.

## Implementation steps (for whoever picks this up)

**Step 1 — `scripts/terms_registry.py`:**
- Remove `threading` import, `_registry_lock`, and the `_locked` wrapper split — collapse
  `ingest_document_terms()`/`_ingest_document_terms_locked()` back into one plain function (no
  lock needed; it's only ever called from a single serial pass, inside `extract_terms.py`, now).
- Remove the `sources` field entirely from entity creation (`:234-239`) and the per-doc append
  block (`:244-247`) — nothing replaces it, per Decision 4.
- Add `build_registry_from_catalog(catalog, meetings_data)`: builds `{}`, iterates
  `sorted(meetings_data, key=lambda m: m['slug'])`, then each meeting's qualifying docs sorted by
  `(doc_type_priority, file_id)` where `doc_type_priority = {'agenda': 0, 'packet': 1,
  'minutes': 2}`, calling `ingest_document_terms(registry, file_id, slug, doc_type,
  catalog.get(file_id, {}).get('terms') or [])` once per doc. Returns the built registry.
- Add `registry_fingerprint(catalog, meetings_data)`: returns the sorted list of
  `(file_id, entry.get('extractor_version'), entry.get('modified_time'))` for the same
  qualifying-doc set the rebuild would visit, so the two functions can't silently drift apart on
  what counts as "qualifying."
- Add `load_fingerprint(bucket_uri)` / `save_fingerprint(bucket_uri, fingerprint)` — same
  load/save pattern as `load_registry`/`save_registry`, writing to a new
  `terms_registry_fingerprint.json` GCS blob.
- These functions are called from `scripts/extract_terms.py` (Step 2 below), not
  `post_process.py` — per Decision 2's reversal, registry-building moves upstream along with
  extraction.

**Step 2 — new file `scripts/extract_terms.py`:**
- Move `_extract_official_terms()`'s current body wholesale from `post_process.py` (Drive-
  metadata fetch, `text_cache_fresh` check, `get_or_extract_text` on a cache miss, caching onto
  `entry['terms']`) — this becomes the per-document extraction logic.
- Replace the current one-call-per-document-on-cache-miss Gemini call with a genuinely batched
  one, modeled on `post_process.py::batch_tag_all_meetings()`'s existing pattern
  (`post_process.py:674-676`, `:683-749` — a Pydantic `results: list[...]` response schema, one
  call covering many items): add `ExtractedTerm { term, type, context }`,
  `DocumentTerms { file_id, terms: list[ExtractedTerm] }`, `BatchTermResponse { results:
  list[DocumentTerms] }`, and a `TERMS_BATCH_SIZE` constant. Unlike `batch_tag_all_meetings`'s
  single all-corpus call, chunk cache-miss documents into `TERMS_BATCH_SIZE`-sized groups, one
  Gemini call per group — raw document text is far more token-heavy per item than a condensed
  transcript summary, so an all-at-once call isn't viable here the way it is there.
- Apply Decision 3's prompt-wording change (the `program` bullet) to this moved prompt.
- After all extraction completes: fingerprint-gated rebuild, same logic as the prior draft's
  post_process.py snippet, just relocated here:
  ```python
  current_fp = terms_registry.registry_fingerprint(catalog, meetings_data)
  if current_fp == terms_registry.load_fingerprint(bucket_uri):
      registry = terms_registry.load_registry(bucket_uri)  # already correct; skip the rebuild
  else:
      registry = terms_registry.build_registry_from_catalog(catalog, meetings_data)
      terms_registry.save_registry(bucket_uri, registry)
      terms_registry.save_fingerprint(bucket_uri, current_fp)
  ```
- Save the updated `catalog` (with freshly-extracted `terms` fields) back to GCS before this
  script exits, the same way `source_data.py`/`post_process.py` already persist catalog changes
  — `process_transcripts.py` needs to read the fresh `catalog` this script just wrote.

**Step 3 — `.github/workflows/deploy.yml`:** insert a new step between "Source data" and
"Process transcripts," matching the existing gating pattern exactly (verified against the live
file, `:69-95`) rather than inventing a new convention:
```yaml
- name: Extract terms (official documents)
  id: extract_terms
  if: ${{ github.event_name != 'schedule' || steps.source_data.outputs.changes == 'true' }}
  run: python scripts/extract_terms.py --bucket "${{ vars.GCS_BUCKET_URI }}"
```
Same `if:` condition as "Process transcripts" (gated on `source_data`'s `changes` output on a
scheduled run; always runs on push/manual dispatch) — this is an LLM-calling stage exactly like
the one after it, so it gets the same gating, not a new rule.

**Step 4 — `scripts/post_process.py` (now simpler, not just rearranged):**
- Delete `_extract_official_terms()` entirely (moved to Step 2).
- Delete the term-extraction `ThreadPoolExecutor` block (`:929-943`, the `term_targets`/
  `_extract_terms_task` logic) — this work no longer happens here.
- Keep `registry = terms_registry.load_registry(bucket_uri)` at `:854` exactly as-is — it's now
  reading a registry the new stage already finished building, instead of one this function would
  go on to mutate.
- Delete the end-of-run `terms_registry.save_registry(bucket_uri, registry)` call (`:1154`) —
  `post_process.py` never mutates `registry` anymore (confirmed by the Decision 2 grep above:
  `discover_term_candidates` only reads it), so there's nothing to save back.
- Glossary-discovery step 2b (`discover_term_candidates`, `apply_discovered_terms`,
  `:945-959`) and `_enforce_glossary_pass` stay exactly as they are — unaffected, per Decision 2's
  "what post_process.py keeps" analysis.
- Net effect on this file: it sheds extraction and registry-consolidation entirely, keeping only
  a one-line registry *read* plus the content-discovery/cleanup work that was always its real
  job — this is the concrete shape of the person's reason 3.

**Step 5 — tests:**
- New `scripts/test_extract_terms.py`: tests for the batching logic (chunking into
  `TERMS_BATCH_SIZE` groups; a `BatchTermResponse` correctly distributes results back onto each
  `file_id`'s `catalog` entry).
- `scripts/test_terms_registry.py`: remove the 8 `entity["sources"]` assertions (the field no
  longer exists); add tests for `build_registry_from_catalog()` (deterministic order given an
  unordered `meetings_data`/`catalog` input; same-meeting same-doc_type collision resolved by
  `file_id` tiebreak) and `registry_fingerprint()` (changes when a qualifying entry's
  `modified_time`/`extractor_version` changes; stable when nothing does). These stay in this test
  file since the functions themselves still live in `terms_registry.py` — only their caller
  moved.

**Step 6 — one-time live cleanup, not a migration.** No live data transform is required for the
`sources` removal (Decision 4) — the first run of the new code naturally rebuilds
`terms_registry.json` from scratch via the fingerprint-mismatch path (the fingerprint blob won't
exist yet, so the first comparison is guaranteed to miss), which by construction no longer writes
a `sources` field into any entity. The existing per-document `terms` cache on `drive_catalog.json`
needs no migration either — it's the same field, same shape, just populated by different code
going forward. Confirm post-ship with a single read-back: `sources` key absent from every entity
in the freshly rebuilt live registry.

**Step 7 — `docs/pipeline.md`:**
- Title (`:3`): "three-stage" → "four-stage."
- Pipeline Stages table (`:69-73`): insert a new row 2 for `scripts/extract_terms.py`
  ("Daily CI / manual" / "Batch-extract canonical names from official documents into
  `drive_catalog.json`; rebuild the Terms Registry"), renumber `process_transcripts.py` to 3 and
  `post_process.py` to 4. Update each row's "Purpose" text to match what actually moved (stage 4's
  entry should no longer claim to extract names into the registry).
- Mermaid diagram (`:15-59`): insert a new `ET["② extract_terms.py"]` node between `SD` and `PT`
  (renumber `PT`→③, `PP`→④); `CAT --> ET`, `ET --> CAT`, `ET --> REG` (the new stage now owns
  writing `terms_registry.json`); change `REG <--> PP` to `REG -.read only.-> PP` (post_process no
  longer writes it, per Step 4); `GLOS <--> PP` stays unchanged (glossary stays post_process's to
  write).
- Terms Registry section (`:153-251`): remove `sources` from the example JSON (`:162-176`);
  replace the "Ingestion matching chain... thread-safe... `ThreadPoolExecutor`" framing
  (`:197-199`, no longer accurate — ingestion is a single serial pass now) with a description of
  the derived-rebuild model, the fingerprint skip, and where it runs (`extract_terms.py`).
- Known limitations (`:352-400`): the `sources`/retention bullet (`:364-367`) → field removed, not
  retained-in-smaller-form; the todo 026 "proposed, not yet implemented" bullet (`:395-400`) →
  shipped (all four original pieces, not just 2-4). (Pre-existing stale `-pending-` links for
  021/028 in this same list are a separate docs-hygiene sweep already noted in the 022 plan — not
  resolved here either.)

**Step 8 — `docs/prompts.md` §6 ("Official Document Term Extraction," `:299-`):** update the
`**Script:**` attribution from `post_process.py → _extract_official_terms()` to
`extract_terms.py → <new function name>()`; add a short note on the batching mechanism (the
section currently documents a single-document, unbatched call). This was explicitly named in
todo 026's own Acceptance Criteria and left undone while piece 1 was deferred — now in scope.

## Effort & risk

- **Step 1 (`terms_registry.py` additions):** unchanged from the prior draft — moderate. The
  riskiest part is `build_registry_from_catalog`'s deterministic ordering (needed for the
  fingerprint skip's byte-identical-rebuild guarantee) — covered by Step 5's dedicated test.
  Effort: medium. Risk: medium, mitigated by tests.
- **Step 2 (new `extract_terms.py`, with real batching):** larger than the prior draft's deferred
  estimate of zero — this is genuinely new code, not a relocation: a new Pydantic schema, new
  chunking logic, and a new script file, on top of the moved extraction/rebuild logic. Effort:
  medium-large. Risk: medium — batching is new behavior this pipeline has never done for term
  extraction (today's calls are one-document-at-a-time), so it needs real testing against
  multi-document responses, not just the mechanical relocation of existing logic.
- **Step 3 (`deploy.yml` wiring):** small — one step, matching an existing, well-understood
  gating pattern exactly. Effort: small. Risk: low.
- **Step 4 (`post_process.py` simplification):** net deletion of code (extraction function,
  concurrency block, a save call) with no new logic added. Effort: small. Risk: low — removing
  code from a path that keeps working the same way for everything it still does.
- **Step 5 (tests):** small-medium (new test file for batching, plus the registry tests from the
  prior draft). Effort: medium. Risk: low.
- **Step 6 (live cleanup):** effectively free, same as the prior draft's analysis — no manual
  script, no backup/rollback choreography, happens automatically on first run. Effort: trivial.
  Risk: low.
- **Steps 7-8 (docs):** more extensive than the prior draft (stage renumbering, title change,
  diagram rewiring) but still small, no risk.
- **Overall, relative to the deferred version**: strictly more work (a new script + schema +
  deploy wiring that the deferred version didn't need), accepted deliberately per the person's
  stated reasons — this is the cost of the atomicity/consolidation benefit, not an oversight.

## Sequencing relative to other open/in-flight work

- **todos/021 (case-insensitive keys) — shipped.** Unaffected; `_find_case_insensitive_key` stays
  exactly as-is, just now called from a single serial pass instead of under a lock.
- **todos/028 (canonical-term tiebreak) — shipped.** Unaffected; `_prefer_as_canonical` stays
  exactly as-is. Confirmed (Decision 4) it did not add raw-term-per-source tracking, so nothing
  here needs to account for that possibility.
- **todos/022 (schema scalability) — plan drafted, implementation pending.** This plan
  **supersedes** its Decision 1 (`sources` → `file_ids`): skip that migration, see Decision 4
  above. Its Decision 2 (`first_seen`/`last_seen` stay date-only) and Decision 3 (defer `context`
  temporal-dedup) are untouched — both are about canonical-value selection among observations,
  which this plan's rebuild doesn't change the logic of, only the execution model. The small,
  surgical correction made to `todos/022-pending-...md` and its plan doc: changed "may become
  moot" / "conditional" framing to a direct statement that the `file_ids` migration is
  superseded and should be skipped once this plan ships, with a pointer to this doc.
- **todos/013 (consolidate glossary artifact) / todos/020 (pipeline stage scope isolation).**
  Not directly touched by this plan, but worth noting for whoever sequences the broader
  terms-registry cluster: this plan's new `extract_terms.py` stage is itself a clean worked
  example of exactly the "corpus-wide vs. single-meeting stage scope" distinction todo 020 wants
  documented pipeline-wide — it's a single stage dedicated entirely to corpus-wide
  extraction-and-consolidation, fully separated from `process_transcripts.py` (single-meeting)
  and the content-cleanup work left in `post_process.py`. If anything, adopting this stage
  *strengthens* the case for todo 020 rather than being independent of it — there are now four
  cleanly-scoped stages to document instead of three, one of whose boundaries this plan just drew
  explicitly.
- **`docs/pipeline.md`'s stage count.** Flagging directly, since it's a visible, repo-wide change:
  this plan takes the pipeline from three stages to four. Anything else in this repo (docs,
  comments, mental models) that assumes "the three-stage pipeline" should be swept for that
  assumption once this ships — Step 7 above covers `docs/pipeline.md` itself, but a broader grep
  for "three-stage" / "three scripts" wasn't done as part of this plan and is worth a quick check
  at implementation time.
