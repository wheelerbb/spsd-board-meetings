---
name: registry-case-sensitive-key-duplicates
status: pending
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

- [ ] A corpus-wide scan of the live `terms_registry.json` quantifies how many case-variant
  duplicate keys actually exist today, to inform whether Option A is worth doing now or later
- [ ] A decision is recorded on Option A vs. B
- [ ] If Option A: `_registry_key`/ingestion lookup made case-insensitive; existing live duplicates
  backfill-consolidated (one-time); regression test added for a same-entity-different-casing pair

## Work Log

- 2026-11-16: Captured from an incidental finding during todos/019's live pymupdf-fix verification
  test — not investigated further yet (no corpus-wide duplicate count taken, no fix attempted).
