---
name: registry-case-sensitive-key-duplicates
status: complete
priority: p2
issue_id: "021"
tags: [terms-registry, data-quality, pipeline]
dependencies: ["019"]
---

## Problem Statement

`terms_registry.py`'s registry keys are case-sensitive, so two spellings of the exact same
real-world entity that differ only in casing — common in this corpus, since an agenda/packet
header is typically ALL-CAPS while the same proper noun in a minutes document body is normal
mixed-case — are never consolidated. They silently fork into two permanently separate registry
entities instead of one, each accumulating its own `context`/`sources` lineage.

## Findings

- **Root cause**: `_registry_key(term_type, canonical)` (`terms_registry.py:62-63`) is a raw
  `f"{term_type}:{canonical}"` format with no case normalization. `_ingest_document_terms_locked`
  only attempts its fuzzy/surname merge-matching fallback (`_person_match` for persons,
  `name_matching.fuzzy_match` for everything else) when `registry.get(key) is None` — i.e. when
  there's no *exact, case-sensitive* key already present.
- **The merge fallback can't rescue this either.** `name_matching.fuzzy_match` treats an exact
  match (its own normalization already lowercases both sides for comparison) as "nothing to
  correct" and returns `None` — by design, for every *other* caller of `fuzzy_match` this is
  correct (an exact restatement of a known term isn't a correction candidate). But it means a
  pure case-variant pair never gets a "yes, these are the same entity, merge them" signal from
  anywhere in the ingestion path. The two case-sensitive-distinct keys just coexist forever.
- **Confirmed live, 2026-11-16**, incidentally, during an unrelated live verification test for the
  `pypdf`→`pymupdf` text-extraction fix (todos/019): running the real official-term-extraction
  prompt against two real meetings' documents (`2024-01-08`, `2024-02-12`) produced both
  `place:South Portland, Maine` (from a minutes document's mixed-case body text) and
  `place:SOUTH PORTLAND, MAINE` (from an agenda header) as two separate entities in the resulting
  registry — see `todos/019`'s 2026-11-16 work log entries and the scratch test at
  `/private/tmp/claude-501/.../scratchpad/live_extraction_test/fresh_registry.json`.
- **General to every entity type**, not place-specific — `_registry_key` has no type-specific
  logic — though places/orgs/programs are the likeliest to actually hit it in practice, since
  header-vs-body casing conventions are a document-formatting habit, not a spelling choice, and
  apply to whatever proper noun happens to appear in a header.
- **Distinct from the already-documented key-collision limitation** in `todos/019` ("two distinct
  real entities sharing an exact canonical string and type would collide") — that's about two
  *different* real things accidentally sharing one key; this is the mirror case, one real thing
  getting two keys.
- **Impact, as currently understood**: registry entity-count inflation and lineage fragmentation
  (one real place/org's `sources`/`context` history split across two entries instead of
  accumulating in one). Not yet confirmed whether this produces a user-visible downstream bug
  beyond the registry itself — `glossary.json`'s discovery layer and `render_glossary_hints` only
  ever act on whichever entity a given raw mention happens to resolve against, so a document that
  consistently uses one casing convention would just keep reinforcing the same (wrong-casing) half
  of the fork, not alternate between them. Worth a corpus-wide scan for the actual duplicate count
  before prioritizing a fix.

## Proposed Solutions

### Option A: Case-insensitive registry keys, case-preserving display spelling (Recommended)

Normalize `_registry_key` to lowercase (or add a parallel lowercase lookup index) so ingestion
and lookup are case-insensitive, while the entity's own `term` field (the actual canonical
spelling shown in hints/glossary) keeps using the existing length/completeness-based upgrade logic
unchanged — casing becomes purely a *lookup* concern, not a *display* one.
**Pros**: Single authoritative fix point (the one place every caller already goes through to find
an existing entity), no change needed to `fuzzy_match`'s broader contract (which is correct as-is
for every other caller). **Cons**: Need to decide which casing wins as the display spelling when
two case-variants are later consolidated after already having separately accumulated
`context`/`sources` — probably "same rule as any other merge" (prefer complete-name/longer-string
per existing logic), but worth confirming. **Effort**: Small. **Risk**: Low — purely additive to
the lookup path.

### Option B: Leave as-is, document as an accepted limitation

Same posture as the existing "two distinct entities, one key" limitation already documented in
todos/019 — note it and move on, if a corpus-wide scan shows the actual duplicate count is small
enough not to matter in practice.
**Effort**: Trivial. **Risk**: Low now; leaves registry entity-count inflation uncorrected
indefinitely.

## Acceptance Criteria

- [x] A corpus-wide scan of the live `terms_registry.json` quantifies how many case-variant
  duplicate keys actually exist today, to inform whether Option A is worth doing now or later
- [x] A decision is recorded on Option A vs. B
- [x] If Option A: `_registry_key`/ingestion lookup made case-insensitive; existing live duplicates
  backfill-consolidated (one-time); regression test added for a same-entity-different-casing pair

## Work Log

- 2026-11-16: Captured from an incidental finding during todos/019's live pymupdf-fix verification
  test — not investigated further yet (no corpus-wide duplicate count taken, no fix attempted).
- 2026-11-17: Resolved.
  - **Scan**: loaded the live `terms_registry.json` (`gs://spsd-pipeline-data`, 2149 entities) and
    grouped by `type:term.lower()`. Found **41 case-variant duplicate groups** (40 pairs + 1
    triple), spanning **place (16), program (17), organization (8)** — zero `person` duplicates,
    confirming `_person_match`'s already-lowercased token comparison was accidentally covering
    that one type. Not negligible — **Option A** chosen.
  - **Code fix**: added `_find_case_insensitive_key()` (`terms_registry.py`, after `_registry_key`)
    and call it in `_ingest_document_terms_locked` immediately after the exact-key lookup misses,
    before the person/fuzzy fallback — the single authoritative lookup point, per Option A. Pure
    case variants are always equal length, so no new "which spelling wins" upgrade logic was
    needed beyond what already exists; whichever side is already registered keeps its display
    casing, matching the recommendation that casing is a lookup concern, not a display one.
  - **Regression tests**: added `test_case_variant_place_consolidates_into_one_entity` and
    `test_case_variant_matches_regardless_of_which_casing_came_first` to
    `scripts/test_terms_registry.py` (place + program types, both orderings). Full suite:
    `test_terms_registry.py` 22/22, `test_name_matching.py` 67/67, `test_glossary_utils.py` 54/54 —
    all passing.
  - **Backfill**: before any live write, downloaded and saved the full pre-merge live registry to
    `/private/tmp/claude-501/.../scratchpad/terms_registry_backup_20261117.json` (2149 entities).
    Ran a one-time merge: for each of the 41 groups, picked a deterministic winner (prefer
    non-ALL-CAPS, then more Title-Cased words, tie-broken lexicographically — ALL-CAPS is a
    document-header formatting artifact, not a deliberate spelling), unioned `sources` (dedup),
    concatenated `context` (dedup via substring check), took min `first_seen`/max `last_seen`.
    Result: 2149 → 2107 entities (exactly the expected 2149-42 reduction). Wrote the merged registry
    back to `gs://spsd-pipeline-data/terms_registry.json`, round-trip-verified by re-reading it
    (2107), then re-ran the scan: **0 remaining case-variant duplicate groups**. The 41-group
    kept/removed key mapping was printed during the run and is reproducible from this log's
    description of the winner rule if ever needed to audit a specific entity's merge.
  - **Backup-handling mistake, disclosed**: the pre-merge backup file above was correctly written
    *before* the merge, but was then inadvertently **overwritten** by re-running the same scan
    script a second time (post-merge, to verify zero remaining duplicates) — it reused the same
    output path and dumped the *post-merge* (2107-entity) live state over it. So the on-disk
    backup no longer reflects the pre-merge (2149-entity) state byte-for-byte. GCS bucket
    versioning is **disabled** on `spsd-pipeline-data` (confirmed via the bucket's
    `versioning_enabled` property — one single generation exists for the object), so there is no
    GCS-side prior version to fall back to either. Practical impact is believed low: the merge was
    a strict union (every source/context from both sides of a pair is preserved in the winner
    unless it was an exact duplicate or exact substring of the other, which is the correct
    de-duplication outcome anyway), and the 41-group kept/removed mapping is on record above — but
    a byte-exact rollback of the pre-merge registry is not available, only a reconstruction from
    this log plus the current (merged) state. Flagging this plainly rather than understating it:
    next time, write the backup to an immutable, run-specific filename instead of a fixed path a
    later diagnostic call can collide with.
