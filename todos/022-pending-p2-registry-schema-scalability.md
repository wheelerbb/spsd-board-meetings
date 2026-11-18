---
name: registry-schema-scalability
status: pending
priority: p2
issue_id: "022"
tags: [terms-registry, data-quality, pipeline, performance]
dependencies: ["019"]
---

## Problem Statement

A live review of `terms_registry.py`'s entity schema (`term`, `type`, `context`, `sources:
[{file_id, meeting, doc_type}, ...]`, `first_seen`, `last_seen`) — prompted by inspecting
`fresh_registry.json` from todos/019's live pymupdf-fix verification test — surfaced two
unrelated-to-that-test design concerns worth deciding on before the corpus grows much further:

1. `context`'s accumulation has no temporal/semantic awareness, and risks the same date-blind-fact
   problem todos/019 exists to prevent.
2. `sources` is the single largest contributor to each entity's storage footprint, duplicates data
   that's largely unused, and grows strictly with *run count*, not just genuinely new documents —
   but it's not purely decorative either; it's currently the only thing keeping repeated ingestion
   idempotent. Any fix needs to preserve that property under a smaller footprint, not just delete it.

## Findings

### Scale observed

A sample ingestion of 2 meetings' 5 real official documents (of 174 total qualifying documents
across the corpus's 99 meetings, 93 of which have at least one) produced **196 entities / ~70KB**
serialized. Type breakdown: `program: 78, person: 60, organization: 33, place: 25`.

- `person`/`place`/`organization` counts will **saturate** — the same ~9 board members, ~10
  schools, and handful of standing committees recur in nearly every document.
- `program` (40% of the sample) is almost entirely **one-off, non-recurring names** — specific
  policy citations, named one-time events ("Culture Day Pep Rally," "Youth Poetry Slam"). Entity
  *count* here will grow close to linearly with meeting count indefinitely, with no retention
  policy for programs that will never be referenced again once their meeting has passed.
- The whole registry is loaded/saved as one JSON blob per run (`load_registry`/`save_registry`),
  not incrementally — so total registry *size*, not just entity count, drives per-run I/O cost.

### `context`: no semantic/temporal dedup (`terms_registry.py:196`)

Dedup is exact-substring-match only. Two phrasings of the same underlying fact ("Board Member" vs.
"School Board Member"), or two phrasings that were each true at a different time (a board member's
role changing from "Board Member" to "Board Chair" over the corpus's multi-year span), both
accumulate as separate semicolon fragments forever — no fragment carries a date. `_primary_note`
(used for the glossary's `note`) picks the *longest* surviving fragment as a proxy for "most
specific," which isn't the same as "most current." Milder than the original todos/013 bug (`context`
is never injected into any Gemini prompt — only `render_glossary_hints`'s spelling-only output is),
but still misinforms anything reading `context` for human review.

### `sources`: largest footprint, mostly unused, but not safely droppable as-is

- Confirmed via a full-codebase grep: `sources` (and its `meeting`/`doc_type` fields) are **never
  read anywhere outside `terms_registry.py` itself** — not by `glossary_utils.py`, not by anything
  display-facing. Currently pure lineage/audit weight.
- **But it is load-bearing for idempotency.** `post_process.py`'s `_extract_official_terms` calls
  `terms_registry.ingest_document_terms()` unconditionally every run — on a cache *hit* (cached
  terms reloaded) exactly as much as on a fresh extraction — because the registry's own persistence
  is independent of the Drive catalog's text/terms cache: a reset or newly-built registry has to be
  able to replay every already-cached document's terms back into it. `if source not in
  existing['sources']` (`terms_registry.py:193`) is the *only* thing preventing that replay from
  re-appending a duplicate entry every single run, forever (growth by run-count, not just by
  genuinely-new-document-count).
- A user-proposed quick win: drop `sources` entirely, add `file_id` to `first_seen`/`last_seen`
  instead. Directionally right (most of `sources`' bulk — `meeting`/`doc_type` duplicated per
  entry — is genuinely unused), but as stated it has two gaps:
  1. It drops the idempotency guard above without replacing it — needs *some* minimal per-entity
     record of which `file_id`s have already been folded in, just much smaller than today's full
     `{file_id, meeting, doc_type}` triple (e.g. a bare list/set of `file_id`s only).
  2. Meetings average 1.87 qualifying documents each, and some have 3 (packet + minutes + agenda) —
     so "the file_id for this date" is ambiguous whenever a meeting has more than one qualifying
     document. Whichever one "wins" for `first_seen`/`last_seen.file_id` would depend on iteration
     order inside `post_process.py`'s `ThreadPoolExecutor`, which is non-deterministic. Needs an
     explicit tie-break rule (e.g. a fixed doc-type priority) rather than leaving it to scheduling
     order, or accepting that `first_seen`/`last_seen` only ever carry a date, not a specific file.

## Proposed Solutions

**Resolved — see `docs/plans/2026-11-18-001-design-registry-schema-scalability-plan.md` for full
rationale and step-by-step implementation.** Summary of the three decisions:

1. `sources: [{file_id, meeting, doc_type}, ...]` → bare `file_ids: [file_id, ...]`. Keeps the
   idempotency check at a fraction of the storage, since `meeting`/`doc_type` are confirmed read
   nowhere outside the one append site. Conditional note: if todos/028 (in flight concurrently)
   ends up needing per-source raw-term tracking, use `{file_id, term}` pairs instead of bare
   strings — check 028's landed diff before implementing.
2. `first_seen`/`last_seen` stay **date-only**, no `file_id` attached — no current or foreseeable
   consumer needs one, and attaching one would require an arbitrary tie-break rule (for same-type
   multi-doc meetings, e.g. `2024-04-08`'s two `packet`/two `minutes` file_ids) to serve zero
   readers.
3. `context`'s temporal blindness: **defer**, not fix. Milder than the original todos/013 bug
   (`context` never reaches a Gemini prompt, only human-facing `note`/glossary display) and no
   concrete observed defect yet (unlike todos/028's `Molly. Schen`, a verified live case). Revisit
   if a real context-staleness bug is ever found.

Implementation is not yet done — the plan doc also flags a sequencing consideration with
todos/026 (dedicated term extraction + derived registry rebuild): if 026 makes the registry
derived/rebuildable rather than accumulate-only, the whole `sources`/`file_ids` idempotency
question may become moot. The code change (Steps 1-2) is cheap/safe to land regardless; the live
migration (Step 3) may be worth deferring if 026 is imminent — left as a judgment call for
whoever implements.

## Acceptance Criteria

- [ ] Decide `sources` → `file_ids`-only (or an equivalent minimal idempotency ledger); implement
  and backfill-migrate the live registry — **decided, see plan doc**; implementation still pending
- [ ] Decide whether `first_seen`/`last_seen` carry a `file_id`, and if so the tie-break rule for a
  meeting with more than one qualifying document — **decided (no), see plan doc**; no
  implementation needed, status quo already correct
- [ ] Decide whether `context`'s temporal-blindness risk is worth addressing now or deferred;
  if deferred, note why (e.g. corpus not yet large/old enough for it to have caused a real issue)
  — **decided (deferred), see plan doc** for the reasoning

## Work Log

- 2026-11-16: Captured from a live schema/scalability review requested alongside todos/019's
  pymupdf-fix verification and todos/021's case-sensitivity finding — same test run, same scratch
  data (`fresh_registry.json`). No implementation yet.
- 2026-11-16: Stage 1 of the full registry-rebuild validation plan (read-only, full-corpus
  fresh-pymupdf-extraction sanity check against live Drive) found **188** qualifying
  (agenda/packet/minutes) documents in the live catalog, not 174 — the prior count was from a
  partial scan. More relevant here: several meeting dates have *two* documents of the *same*
  doc_type (e.g. `2024-04-08` has two distinct `packet` file_ids and two distinct `minutes`
  file_ids, likely revision pairs), not just one-of-each-type. This sharpens the open
  "`first_seen`/`last_seen.file_id` tie-break" question above — a same-type collision needs its
  own rule, not just a packet-vs-minutes-vs-agenda priority order. No fix yet; noting for whoever
  implements the Acceptance Criteria above. (Separately, zero glued-word extraction artifacts
  found across all 188 docs — see todos/019's Stage 1 note — confirming the pymupdf fix at full
  scale, not scope for this todo.)
- 2026-11-18: Plan drafted (no implementation yet) — see
  `docs/plans/2026-11-18-001-design-registry-schema-scalability-plan.md`. All three open design
  questions decided: `sources` → bare `file_ids` (conditional on todos/028's landed shape),
  `first_seen`/`last_seen` stay date-only, `context` temporal-dedup deferred. Flagged a real
  sequencing question against todos/026 (derived/rebuildable registry could obsolete the
  `sources`/`file_ids` idempotency concern entirely) for whoever implements to weigh.
