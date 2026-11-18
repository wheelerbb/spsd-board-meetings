---
name: registry-never-revises-bad-first-seen-term
status: complete
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

- [x] Root cause confirmed: trace exactly where/why `term` is set once and never revised, even
      across many later corroborating observations.
- [x] Fix implemented at the layer judged correct above (registry consolidation vs. glossary
      promotion) with regression tests using the `Molly. Schen` / `Molly Schen` case as the
      concrete repro.
- [x] A real audit (not just the 4-string spot-check above) of the live registry for other
      instances of *this specific failure mode* (see Work Log — a broader, differently-shaped
      duplicate problem was also surfaced and deliberately left untouched as out of scope).
- [x] Live `terms_registry.json` and `src/_data/glossary.json` corrected for `Molly. Schen`
      specifically once the fix is verified.

## Work Log

- 2026-11-17: found during Stage 5 (todos/019) live-cutover prep, while sanity-checking the two
  cutover artifacts (`rebuild_scratch/stage2_registry.json`, `rebuild_scratch/stage3_glossary.json`)
  before writing them live. User explicitly decided to ship the defect as-is rather than block
  cutover on it — see Findings above. Not fixed in this pass.
- 2026-11-18: Resolved.
  - **Root cause, confirmed precisely.** Not simply "frozen" in the sense of never being
    re-examined — `_ingest_document_terms_locked` (`terms_registry.py`) does re-compare `term`
    against `existing['term']` on every corroborating mention that merges into an entity via
    `_person_match`/`fuzzy_match`. The actual defect is the *tiebreak* used when both forms are
    equally "complete" (`is_complete_person_name` ties): `len(term) > len(existing['term'])`, a
    raw-character-length proxy for "fuller/cleaner". A stray trailing period makes "Molly."
    literally longer than "Molly" with zero added information, so the malformed form wins that
    comparison every time a clean "Molly Schen" mention is compared against it — one-on-one,
    with no frequency/voting across the 72 (now; was 70 when this todo was written) accumulated
    sources — so no amount of later corroboration ever overturns it. Confirmed by tracing the
    exact comparison values for this pair.
  - **Data-shape check, as directed before picking a layer**: `sources` entries are
    `{file_id, meeting, doc_type}` only — no raw per-source extracted string is retained anywhere.
    This rules out literally implementing the "most-frequent form among sources" wording from this
    todo's Option 1 (that history doesn't exist in the data), and also means no audit can ever
    reconstruct which alternate spellings were seen-and-lost in past merges — see audit note below.
  - **Fix layer: registry consolidation** (`terms_registry.py`), per the todo's Option 1,
    single-authoritative-fix-point convention. Added `_clean_chars()` and `_prefer_as_canonical()`
    (after `_is_hedge_fragment`); the latter replaces the raw-length tiebreak in both the
    person-completeness-tie branch and the non-person (place/org/program) upgrade branch of
    `_ingest_document_terms_locked`. It only overrides the original length heuristic when the two
    candidate spellings reduce to *identical* alphanumeric content once incidental
    punctuation/whitespace is stripped — i.e. provably the same real string, just noisier in one
    spelling — in which case the *shorter* (less incidentally-punctuated) form wins instead.
    Every other comparison (genuinely different content, e.g. an added middle name) falls through
    to the original length-based behavior unchanged, so this is additive/narrow, not a rewrite of
    the existing upgrade logic.
  - **Regression tests**: `test_punctuation_artifact_term_gets_corrected_by_cleaner_corroboration`
    and `test_clean_term_not_overridden_by_later_punctuation_artifact` added to
    `test_terms_registry.py` (both merge orderings, mirroring the style already used for
    todos/021's case-insensitivity tests). Full suite: `test_terms_registry.py` 24/24 (incl. the 2
    new tests), `test_name_matching.py` 67/67, `test_glossary_utils.py` 54/54 — all passing, no
    regressions in any existing test (traced through each by hand: none of the existing upgrade
    tests exercise the specific "both complete, tied" branch this touches except via
    `len(registry) == 1`-only assertions, which are unaffected).
  - **Audit.** Backed up the live registry first to a uniquely timestamped path
    (`terms_registry_PREMERGE_BACKUP_20261118T105109.json`, 2183 entities as of today — grown from
    2107 since todos/021's 2026-11-17 backfill via normal daily pipeline runs) before any write,
    guarded so a re-run can't silently overwrite it (explicit "refuse if exists" check — the prior
    fork on todos/021 lost its own pre-merge backup this way; this one didn't). Ran two audit
    passes against the backup:
    1. **Targeted heuristic** (the one actually built for this bug's shape): scanned every
       person/place/organization/program entity's own `term` for a 2+-letter alphabetic token
       immediately followed by a stray period, excluding a ~50-entry allowlist of real
       abbreviations/titles/months (`Mr.`, `Jr.`, `Oct.`, etc.). **Result: exactly 1 hit across all
       2183 entities — `person:Molly. Schen` itself.** Confirms the todo's own 4-string spot-check
       conclusion: this is genuinely an isolated exposure, not a systemic pattern.
    2. **Broader cross-entity check** (same type + alphanumeric-identical term but still two
       separate registry keys — the general "should-have-merged" shape): found 66 groups, but on
       inspection essentially none of them are *this* bug. The large majority are the already
       separately-tracked "glued-word text-extraction artifact" class (todos/025 — several of its
       own named examples, e.g. `SouthPortlandHighSchool`, `SouthPortlandSchool`, appear verbatim
       in this scan), which is a text-extraction-level defect, not a registry-tiebreak one. A
       smaller number (e.g. `program:FY26 Budget` / `program:FY26 budget`) look like pure-case
       duplicate stragglers that *should* have been caught by todos/021's fix/backfill but weren't
       — worth a follow-up look at todos/021, but deliberately not chased down or touched here:
       it's a different mechanism than this todo's "already-one-entity, wrong-term-won-a-tiebreak"
       bug, and fixing it would mean reopening 021's scope from inside 028. Left entirely alone.
    - **Honest limitation**: neither audit pass (nor any audit) can detect a *past* instance of
      this exact mechanism where the clean corroborating mention was absorbed into the entity
      (its `sources` count went up) without ever displacing `term`, and the registry's own stored
      `term` doesn't otherwise look visibly malformed — because the losing spelling's raw string
      is never retained anywhere once ingestion finishes. `Molly. Schen` is only auditable at all
      because its specific defect (a mid-name stray period) remains visible in the surviving
      canonical string. A structurally invisible instance of this same bug class cannot be ruled
      out from current data; it would need the `sources` schema to start retaining each
      observation's raw extracted string (a bigger change, overlapping todos/022's "`sources`
      schema scalability" concerns in the *opposite* direction — that todo wants `sources` smaller,
      not larger — so deliberately not attempted here).
  - **Live data correction**: fresh-reloaded the live registry immediately before writing (not
    reusing the audit snapshot), renamed `person:Molly. Schen` → `person:Molly Schen` with
    `term: "Molly Schen"`, all 72 sources preserved, wrote back to
    `gs://spsd-pipeline-data/terms_registry.json`, and round-trip-verified by reloading from GCS
    afterward (count, key, term, and source count all confirmed). `src/_data/glossary.json`:
    swapped `correct` from `"Molly. Schen"` to `"Molly Schen"`; removed the now-redundant
    `"Molly Schen"` from `aliases`; moved `"Molly. Schen"` into `misspellings` (it's a genuine
    extraction error, not a legitimate shortform/role-reference like the other `aliases` entries —
    matches `glossary_utils.py`'s own aliases-vs-misspellings distinction).
    - **Side-effect noted, not acted on**: `post_process.py`'s deterministic glossary find/replace
      pass (`_enforce_glossary_pass`) was, under the old (backwards) glossary entry, actively
      rewriting clean "Molly Schen" occurrences *into* "Molly. Schen" in rendered output — 31 live
      `.njk` files under `src/meetings/` currently contain the literal malformed string. With
      today's glossary.json correction, the *next* regular `post_process.py` run will self-heal
      all 31 via that same existing mechanism, now running in the correct direction (misspellings
      → correct) — no separate corpus-wide reprocess run was triggered here, consistent with
      treating that as routine pipeline operation rather than part of this fix.
