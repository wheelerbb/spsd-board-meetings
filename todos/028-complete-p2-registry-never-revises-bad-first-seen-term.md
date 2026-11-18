---
name: registry-never-revises-bad-first-seen-term
status: pending
priority: p2
issue_id: "028"
tags: [data-integrity, terms-registry]
dependencies: ["019"]
---

## Problem Statement

Found while preparing Stage 5 (live cutover) of the terms-registry-rebuild validation plan
(todos/019). `terms_registry.py`'s consolidation logic treats an entity's first-ever extracted
`term` as permanently canonical: once set, nothing later ever re-derives or replaces it, even
when dozens of later, cleaner corroborating observations accumulate under the same entity. In
practice this means a single bad first extraction (e.g. a text-extraction artifact, OCR glitch,
or LLM slip) can outlive hundreds of subsequent correct mentions of the same person, and —
because glossary auto-discovery promotes the registry's `term` value straight into the
glossary's `correct` field — that bad spelling can become the canonical "correct" spelling fed
into every future Gemini prompt via `render_glossary_hints`.

## Findings

- **Concrete example: `person:Molly. Schen`.** This is the single most-corroborated person
  entity in the entire 1684-entity Stage 2 registry rebuild — 70 sources, spanning nearly every
  meeting from `first_seen: 2023-08-21` through `2025-08-18` — and is South Portland Board of
  Education's Vice Chair, a high-profile, frequently-referenced person. Its canonical `term`
  field is `"Molly. Schen"` (stray period after "Molly"), because that malformed form was the
  very first extraction on 2023-08-21, and no later observation — including the clean,
  unambiguous `"Molly Schen"` (no period), which does appear among its corroborating mentions —
  ever displaced it as the entity's canonical form. In the Stage 3 glossary rebuild, this
  propagated directly: the entry is `{"correct": "Molly. Schen", "aliases": ["Molly Schen", ...],
  ...}` — the clean spelling demoted to a mere alias of the malformed one.
- **Shipped live anyway, by explicit user decision** (2026-11-17, during Stage 5 cutover
  planning): presented three options (fix root cause first, hand-patch just this entry, or ship
  as-is and track separately) — user chose to ship as-is. Rationale implicit in that choice: this
  is a single known, cosmetic spelling defect (a stray period), not a wrong-person or
  wrong-attribution error, and didn't justify blocking the cutover of 1684 otherwise-reviewed
  registry entities and 73 otherwise-reviewed glossary entries over one entry.
- **Scope check, not exhaustive.** Scanned the 73-entry Stage 3 glossary for the same failure
  mode against four other known misspelling artifacts already present in the registry
  (`Ms.DeAngleis`, `Ms. Homlan`, `Mr. Ratlifff`, `Mr. Felller`) — none of those were promoted to a
  glossary `correct` field; they stayed as low-frequency registry fragments below whatever
  evidence threshold glossary discovery requires for promotion. This suggests `Molly. Schen` is
  an isolated (if high-profile) exposure rather than a systemic pattern across the current
  glossary — but this was a narrow spot-check against four specific already-known bad strings,
  not an audit of all 1684 registry entities for this failure mode. A real audit would need to
  check every multi-word person/place/org entity's `term` against its own corroborating mentions
  for a cleaner, more-complete, or more-frequent alternative that never got promoted.

## Proposed Solutions

- Registry consolidation (`terms_registry.py`, likely wherever an entity is first created and
  later merged — see `_person_match` and the ingestion path) should not freeze `term` at
  first-seen. Once an entity accumulates enough corroborating observations, re-derive the
  canonical `term` by picking the most-frequent form among its sources (or, if frequency ties,
  the most multi-word-complete form) rather than keeping whatever happened to be seen first.
  This mirrors the "fuller/fragment consolidation" direction already used elsewhere in this same
  module (see todos/019's 2026-11-12 entry) — same idea, applied to a single entity's own
  canonical spelling instead of merging separate entities.
- Alternative, narrower fix: at glossary-discovery promotion time (`glossary_utils.py`), when
  choosing which registry-sourced string becomes `correct` vs. `aliases`, prefer the
  most-frequent/cleanest alias over the registry's raw `term` field if they differ — cheaper to
  implement, but only patches the symptom at the one place it currently surfaces (this
  repository's glossary), not every other current and future consumer of `terms_registry.json`
  directly.

## Acceptance Criteria

- [ ] Root cause confirmed: trace exactly where/why `term` is set once and never revised, even
      across many later corroborating observations.
- [ ] Fix implemented at the layer judged correct above (registry consolidation vs. glossary
      promotion) with regression tests using the `Molly. Schen` / `Molly Schen` case as the
      concrete repro.
- [ ] A real audit (not just the 4-string spot-check above) of the live 1684-entity registry for
      other instances of this same failure mode, before or shortly after the fix lands.
- [ ] Live `terms_registry.json` and `src/_data/glossary.json` corrected for `Molly. Schen`
      specifically once the fix is verified.

## Work Log

- 2026-11-17: found during Stage 5 (todos/019) live-cutover prep, while sanity-checking the two
  cutover artifacts (`rebuild_scratch/stage2_registry.json`, `rebuild_scratch/stage3_glossary.json`)
  before writing them live. User explicitly decided to ship the defect as-is rather than block
  cutover on it — see Findings above. Not fixed in this pass.
