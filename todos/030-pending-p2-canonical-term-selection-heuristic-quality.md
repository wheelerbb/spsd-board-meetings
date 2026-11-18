---
name: canonical-term-selection-heuristic-quality
status: pending
priority: p2
issue_id: "030"
tags: [terms-registry, data-quality, pipeline]
dependencies: ["028", "026"]
---

## Problem Statement

`terms_registry.py`'s canonical-`term` selection (which spelling an entity keeps when two
corroborating observations disagree) currently decides almost entirely on **raw string length**
(`_prefer_as_canonical`, added in todo 028) plus a person-specific **completeness** gate
(`is_complete_person_name`) — with **processing order** acting as an implicit, unexamined
tiebreak: whichever observation is `existing` at comparison time wins any true tie, and today
that's whatever happened to be ingested first (soon: whatever todo 026's deterministic rebuild
order — meeting-date ascending, then agenda→packet→minutes, then `file_id` — visits first).

Neither signal is a strong proxy for "which spelling is actually correct":
- **Length** only measures correctness in the narrow case todo 028 fixed (stray punctuation
  inflating length with zero added information). It says nothing about which of two
  equal-or-different-length *substantively different* spellings (a nickname vs. a full name, an
  abbreviated department name vs. its full name, two genuinely different-looking renderings of
  the same entity) is the one actually worth surfacing as canonical.
- **Order/recency** was never a deliberate signal — it's a side effect of iteration order, not a
  chosen heuristic. Todo 026's move to a fixed, deterministic rebuild order makes this order-
  dependence more visible and more permanent (the same observation will always win the same tie,
  every rebuild, forever) rather than removing it.

A better signal is plausibly available but not currently used: **corroboration frequency** (how
many independent sources/documents actually use a given spelling) is a much stronger "which one
is real" signal than length or arrival order — the same information that already justified todo
028's fix in spirit (`Molly. Schen` lost because 70 corroborating mentions never got weighed
against the single, first, malformed one).

## Findings

- Not yet investigated in depth — captured directly from a design discussion following todo 028's
  ship and todo 026's planning pass, before any audit of current behavior beyond what those two
  todos already surfaced.
- Frequency-based selection is not freely available with today's schema: todo 026's plan
  (`docs/plans/2026-11-18-002-...md`) removes the `sources`/`file_ids` field entirely (correctly,
  for the idempotency concern it existed for) and does not track a raw per-observation term
  string anywhere. Implementing frequency-weighting would need a deliberate decision to
  (re-)introduce some form of per-observation raw-term tracking — not an accidental side effect of
  reverting todo 026's cleanup.
- This todo's concrete shape depends on what todo 026 actually ships (deterministic order is its
  design, not yet implemented as of this writing) — revisit/re-scope once 026 lands rather than
  investigating against the current accumulate-in-place code, which todo 026 is replacing.

## Proposed Solutions

Not investigated — rough directions to evaluate, not a decision:

- **Frequency/corroboration-weighted selection**: track enough per-observation data (e.g. a
  `{file_id, term}` list, as todo 022's plan already flagged as a possible need) to pick the
  most-frequent spelling among an entity's observations, falling back to length/completeness only
  to break a genuine frequency tie. Cost: reintroduces some per-entity storage/tracking that todo
  026 deliberately removed — needs to be weighed against the storage-footprint concerns that
  motivated removing `sources` in the first place (todo 022).
- **Source-authority weighting**: if the registry ever ingests from sources of differing
  reliability (today it's official docs only — agenda/packet/minutes — so this may not currently
  apply, but worth confirming rather than assuming), weight by source type rather than treating
  every observation equally.
- **Generalized noise-tolerant matching**: todo 028's `_clean_chars`/`_prefer_as_canonical` fix is
  specific to punctuation/whitespace noise. A more general edit-distance or normalized-similarity
  check could catch other "same underlying string, different noise" cases beyond stray
  punctuation, without needing frequency data at all.

## Acceptance Criteria

- [ ] Audit current canonical-term selection end-to-end (`_prefer_as_canonical`,
  `is_complete_person_name` gate, and whatever processing-order dependence todo 026 ships with)
  and document precisely what decides canonical spelling today, once todo 026 has landed
- [ ] Evaluate whether frequency/corroboration-count is a feasible, worthwhile signal given
  whatever per-observation data todo 026 actually ends up persisting (or doesn't)
- [ ] Decide on a heuristic (or explicitly decide current length+completeness is good enough,
  with reasoning) and, if changing it, what data needs to be tracked to support it
- [ ] Regression tests: the existing `Molly. Schen`-style case (todo 028) plus at least one new
  case where length/order alone would pick wrong but frequency or another chosen signal would
  pick right

## Work Log

- 2026-11-18: Captured from a design discussion following todo 028's ship (length-based tiebreak
  fix) and todo 026's planning pass (deterministic rebuild order). Not investigated or
  implemented — filed to revisit once todo 026 lands, since this heuristic's exact current shape
  depends on code todo 026 is still in the process of replacing.
