---
name: terms-registry-and-living-glossary
status: pending
priority: p2
issue_id: "019"
tags: [architecture, data-integrity, performance, pipeline]
dependencies: ["013"]
---

## Problem Statement

Three mistranscribed names on the 2026-09-14 meeting page ("Elyse Holman" → Claire Holman, "Heather
Richardson" → Eleni Richardson, "Judd Daniels" → Ishmeel Daniels) led to a targeted `glossary.json`
fix, then a full review of how the pipeline identifies and canonicalizes domain-specific proper nouns
— people, places, organizations, programs — across every meeting artifact, not just that one bug.

The general need: raw material (auto-captioned video, OCR'd PDFs) produces noisy *surface forms* of a
fixed-but-growing set of real-world entities. Every downstream artifact (timeline, summary,
attendance, votes) needs the *canonical* form, consistently, and that mapping has to (1) be
discoverable from authoritative sources where one exists and inferable from pattern/frequency where
it doesn't, (2) apply everywhere the entity appears, not just one field, (3) respect time — an
entity's *validity* can change (board terms) while its *spelling* doesn't, conflating the two is
literally the bug that created [todos/013](013-pending-p2-consolidate-glossary-artifact.md) — and
(4) grow without a human in the loop, because hand-curation doesn't scale to an ever-growing archive.

Review against that need went through several rounds, each changing the shape of the one before it —
documented in full below because the reasoning matters as much as the conclusion for whoever picks
this up. The headline result: what started as "extend `glossary.json`" ended as "promote official-term
extraction to a first-class corpus-wide registry, and retire `board_members.json` entirely" — a
smaller surface area than the original plan, not a larger one.

## Findings

**1. Official-term extraction is the highest-quality signal in the system and is being thrown away.**
`_extract_official_terms()` (`scripts/post_process.py:383-459`) extracts proper nouns —
`person`/`place`/`organization`/`program`, with context — from vetted official documents
(agenda/packet/minutes), which is categorically more reliable than ASR-derived transcript text. But
it's stored as a per-document GCS blob pointer (`entry['terms_blob']`), read once as a one-off prompt
hint for that single meeting, then never consolidated anywhere. "Ishmeel Daniels" extracted from five
different agendas is five disconnected fragments, not one entity. There is no single place to look and
see what the official record already knows — which is exactly why this data is hard to find today.

**2. It's also a performance problem.** On every cache hit, `_extract_official_terms()` does two
network round trips per document — a live Drive `modifiedTime` check, then a separate GCS blob read —
even when nothing changed. Across ~100+ meetings × 2-3 official docs, that's 200-300+ redundant calls
per run, which is what generated the wall of `Loaded N terms ... from cache` lines that prompted this
review. The `catalog` dict these live in (`drive_catalog.json`, GCS, never git-tracked) is already
loaded once per run and saved once at the end — today's design just doesn't take advantage of that.

**3. Prompt-time grounding is wired into 2 of 5 generation paths; a 3rd has a live drift bug.**
`glossary.json` hints reach the main transcript-extraction prompt and
`_extract_votes_attendance_from_doc()` (minutes/summary extraction). They reach neither
`generate_agenda_preview()` (agenda/packet → preview HTML — zero grounding today) nor
`synthesize_topic()`, which instead carries its own hardcoded, already-stale duplicate:
`"- SPELING: Kaler (NOT Caler), Skillin (NOT Skillen)."` — 2 of glossary.json's 9 entries, frozen
independently, already out of sync. This is the same "two places to remember, they silently drift"
failure mode [todos/013](013-pending-p2-consolidate-glossary-artifact.md) was written to eliminate,
recurring in a part of the pipeline that review hadn't reached until this question surfaced it. The
post-hoc find/replace pass (`_enforce_glossary_pass`) *does* already apply everywhere, uniformly,
regardless of source — it's the one mechanism that's already general-purpose.

**4. There is exactly one explicit, code-enforced precedence rule, and it only covers content, not
spelling.** `_find_priority_source_doc()` (`process_transcripts.py:126-144`) ranks Minutes > Meeting
Summary > Transcript for *what happened* (votes, attendance). Nothing analogous exists for *how to
spell a name* — hints from different sources just get string-concatenated with no arbitration.

**5. `board_members.json`'s roster was never fed to Gemini before generation** — only consulted after,
to clean up the `board_attendance` array. That looked at first like the direct root cause of the
Holman/Richardson bug, and a natural fix looked like "feed the roster into the prompt as a hint too."
Investigating that fix (see Findings 6-8) found a better answer: retire the file instead.

**6. `board_members.json`'s term dates have no traceable source of truth.** The curated entries'
specific date ranges (e.g. `"2025-01-01"` to `"2028-12-31"`) aren't derivable from anything the
pipeline itself tracks (VTT transcripts, Drive documents) — they were manually entered from some
external source that isn't captured or cited anywhere in the repo. Injecting them into a prompt as
grounding "fact," even date-windowed, means asserting an unverifiable manual claim — the same
*category* of risk as the original Duina bug, just narrower. The actual Holman/Richardson bug was a
pure spelling problem (a correctly-identified person's first name misheard), which doesn't need
date-scoped validity to fix — it needs correct spelling, which document-sourced grounding (Finding 1,
once fixed) already provides with no special-casing for "board member."

**7. `board_members.json`'s own `auto_discovered` growth mechanism has a visible blind spot that
produced real garbage.** `apply_discovered_members()` converges correctly when a name is transcribed
with a *consistent* surname across observations — confirmed by five entries that did converge cleanly
(Jennifer Kinney, Tandy Ratliff, Molly Schen, Counselor Ryan, Mr. Domingos). It silently leaves
garbage behind when the surname itself varies, because its matching is an exact-string surname anchor
with no tolerance for the surname being misheard:

| Garbage entry | Real entry | Failure mode |
|---|---|---|
| `Daniel Fowler` | `Daniel Feller` | surname misheard |
| `George Rich` | `George Risch` | surname misheard |
| `Holeman` | `Claire Holman` | surname misheard |
| `Susan Rocher` | `Susan Rauscher` | surname misheard |
| `Rosemary` | `Rosemarie DeAngelis` | no surname captured at all |
| `Angela` / `AngelaKabisa` / `Angela Kaba` | `Angela Kabisa` | no surname / concatenated / truncated |
| `Shin` / `Shen` | (unresolved) | no surname captured |

`glossary.json`'s own "Atkinson Duina" entry (aliases "Atkinson-Dena", "Atkinson Dena") is the same
failure mode in a different file.

**8. Checked whether `board_members.json` is actually load-bearing anywhere else before proposing to
remove it — it isn't:**
- No template reads `board_members.json` or any `board_members_utils` output directly (`grep` across
  `src/` for `board_members`/`boardMembers` returns nothing in any `.njk`/`.html`).
- `role` (Board vs. Student Rep) on an attendance entry is part of Gemini's own structured extraction
  from transcript/document context — `canonicalize_attendance_names()`'s own docstring confirms it
  "never touches status/role." The roster file never supplied this.
- `src/_includes/layouts/meeting.njk:229` guards attendance rendering with
  `{% if board_attendance and not stub %}` — stub pages are *already* excluded from rendering
  attendance regardless of whether `source_data.py:416` seeded the field. The next-meeting
  stub-seeding is dead code today, not a feature being removed.
- `board_members.json`'s only genuinely load-bearing job is attendance-name spelling canonicalization
  — resolving "Ms. DeAngelis" to "Rosemarie DeAngelis" — which is a direct instance of the same
  surname/fuzzy matching already being built for the Terms Registry (Decision below), just pointed at
  registry `person` entities instead of a separate hand-maintained roster. The one thing date-scoping
  theoretically protected against — two different people with the same surname across a board
  turnover — already degrades safely without it: the shared matcher returns no match rather than
  guessing wrong when more than one candidate matches.

## Decisions

Made directly with the user across this review, recorded because each changes what "correct" means
for the implementation below:

- **No manual-review gate, anywhere.** An early design proposed a `board_members.json`-style
  `auto_discovered: true` flag that kept a glossary candidate inert until a human removed it.
  Rejected — doesn't scale, leaves the "living repository" mostly not-living. Risk is bounded by
  *evidence scope* (apply immediately, bounded to the meeting(s) that produced the evidence, promote
  to global once independently confirmed) instead of a review queue.
- **`note` is never rendered to the model, on any entry.** `render_glossary_hints()` drops `note`
  from what's shown to Gemini entirely — it stays in the file as human-readable metadata only. Root
  cause fix for date-blindness: a note like "At-Large board member" asserted into every meeting's
  prompt regardless of date is the same bug class as the original Superintendent misattribution.
  Entries that exist *only* for their note and have no `aliases` (`SPESPA`, `SPTA`) become dead weight
  under this rule and should simply be deleted — they were never corrections in the first place.
- **"Remove the protection, not the data."** `glossary.json`'s existing curated content stays as its
  starting state — nothing is deleted or reset (except `SPESPA`/`SPTA`, per above). But no entry,
  regardless of whether a human originally typed it or the pipeline discovered it, is ever permanently
  locked against automated update again. `apply_discovered_terms()` drops the "skip if it collides
  with a hand-curated entry" branch; a collision now means *merge into it*, not *defer to a human*.
  `auto_discovered` becomes pure provenance metadata — it no longer gates anything.
- **Official-term extraction is promoted from an ephemeral per-document cache to a first-class,
  corpus-wide Terms Registry** (new — see Proposed Solution, Phase 2). This is the architectural
  center of this todo: once it exists, `glossary.json`'s discovery and attendance-name canonicalization
  both become applications of the *same* consolidation mechanism, reading from the *same* ground-truth
  asset, instead of separately-reasoned-about comparisons living in separate files.
  - This resolves the "glossary and terms should maybe be one file" question raised earlier in review
    the other direction: they stay separate artifacts (git-tracked, human-reviewable curated
    corrections vs. GCS-hosted, bulk, lineage-tracked ground truth) — but the registry's existence is
    what finally makes the relationship between them real, instead of terms being extracted once and
    discarded.
- **`board_members.json` is retired entirely**, not patched. Its only load-bearing job (attendance-name
  canonicalization) moves to the Terms Registry via the same shared matcher everything else uses; its
  only other job (next-meeting stub seeding) is already dead code (Finding 8) and is simply deleted,
  not replaced. This also makes the once-planned backfill-reconciliation work for the garbage entries
  in Finding 7 unnecessary — they don't need merging, they cease to exist along with the file. Term
  dates / seat assignments (the data with no traceable source of truth, Finding 6) are not replicated
  anywhere else: nothing currently needs "who is on the board right now," only "how do you spell this
  person's name," which the registry answers on firmer footing.
- **A backfill reconciliation pass is still required for the Terms Registry's own self-consolidation**
  (merging e.g. "Kaler" and "Kaler Elementary School" extracted from different documents into one
  entity) — but this is folded into the registry's normal build step (Phase 2), not a separate
  high-risk phase, since it only ever merges *fragments of the same extraction output* rather than
  merging and deleting already-established, separately-authored entries the way the (now-cancelled)
  `board_members.json` reconciliation would have.
- **Topics are out of scope, on purpose.** A topic isn't a misheard transcription of another topic —
  it's a classification decision, already served by a separate, mature mechanism
  (`all_topics.json`/`generate_tags`/the blacklist and qualification-bar work). Folding it into
  `glossary.json`'s string-replacement model would fit neither problem well.

## Proposed Solution

### Phase 1 — foundation fixes (small, safe, independently shippable)

- **1A — inline per-document term caching.** `_extract_official_terms()` and
  `_load_official_terms_from_gcs()` store/read `entry['terms']` directly on the already-once-per-run
  `catalog` dict instead of a separate `terms_blob` GCS pointer + read. Keep the Drive `modifiedTime`
  staleness check (still needed for correctness); drop the second network round trip on every cache
  hit. Lazy migration: an entry with a legacy `terms_blob` pointer but no inline `terms` is hydrated
  once on next touch, then the pointer is dropped. No backfill script needed. This is a stepping
  stone, not the final resting place — Phase 2's registry is what the inlined per-document terms
  actually feed.
- **1B — close the coverage gap.** Wire `render_glossary_hints()` into `generate_agenda_preview()`
  and `synthesize_topic()`; delete `synthesize_topic()`'s hardcoded `SPELING` line entirely.

### Phase 2 — Terms Registry + board_members.json retirement

A single, well-known, corpus-wide artifact — `terms_registry.json`, GCS-hosted (too large and
generated to be a useful git diff, same reasoning as `drive_catalog.json`), not fragmented across
hundreds of per-document blobs. Keyed by `type:canonical-string` (documented limitation: two distinct
real entities sharing an exact canonical string and type would collide — acceptable for this corpus's
scale, not solved here). Each entity carries `context` (best available description/role, accumulated
across sources), and `sources`: which documents/meetings mention it, plus `first_seen`/`last_seen`.

New module `scripts/name_matching.py` — the single consolidation primitive used everywhere a near-miss
proper noun needs resolving (registry self-consolidation, glossary discovery, attendance
canonicalization), not three separately designed comparisons:
- `strip_title(name)`, `surname(name)` — generalized from `board_members_utils.py`'s current
  `_strip_title`/trailing-token logic (ported here before that module is deleted).
- `match_unique(raw_name, candidates, name_key)` — the generalized, origin-agnostic form of today's
  `_match_active` — returns the unique match, or `None` on zero or multiple matches (the safe-degrade
  behavior attendance canonicalization relies on, per Finding 8).
- A fuzzy/edit-distance matcher (stdlib `difflib`, no new dependency) for `place`/`organization`/
  `program` terms, which have no "surname" to anchor on — and as a *fallback* for `person` terms when
  surname-anchoring finds nothing, specifically to catch surname-level mishearings (closes the
  Atkinson Duina / Fowler-Feller / Rich-Risch / Holeman-Holman class of error that a pure surname
  anchor structurally cannot catch).

New module `scripts/terms_registry.py` (dedicated — per this repo's "new cross-cutting metadata gets
its own file" convention, not bolted onto `drive_catalog.json` or `glossary_utils.py`). This becomes
the one owning module for the registry, both writing to it and serving lookups against it:
- `load_registry(bucket_uri)` / `save_registry(bucket_uri, registry)` — GCS read/write.
- `ingest_document_terms(registry, file_id, meeting_slug, doc_type, extracted_terms)` — for each
  freshly-extracted-or-cached per-document term (from Phase 1A's `catalog[file_id].terms`), uses the
  shared matcher to find-or-create a registry entity (merging e.g. "Kaler" and "Kaler Elementary
  School" extracted from different documents into one entity) and updates its lineage. Run once over
  every already-cached document in the corpus on first build, not just new extractions going forward
  — the registry should have full historical coverage from day one, which is what makes an immediate,
  fallback-free cutover of attendance canonicalization safe (see below).
- `canonical_person(raw_name, registry)` — the attendance-canonicalization replacement:
  `match_unique(raw_name, persons_in(registry))`. Used directly by `process_transcripts.py` in place
  of `canonicalize_attendance_names(attendance, active_members(...))`.
- Wired into `post_process.py`'s existing per-meeting term-extraction loop, right after a document's
  terms are produced or loaded from cache. Batched single write at the end of the run, same
  race-avoidance reasoning as every other shared-write in this pipeline.

**Retirement, once the registry has run its initial full-corpus build:**
- Delete `src/_data/board_members.json` and `scripts/board_members_utils.py`.
- `scripts/source_data.py:416` — remove the next-meeting `board_attendance` stub-seeding line (already
  unrendered, per Finding 8 — this is a pure deletion, not a replacement).
- `scripts/process_transcripts.py` — attendance canonicalization calls `terms_registry.canonical_person`
  per entry instead of `board_members_utils.canonicalize_attendance_names`/`active_members`; remove the
  `unresolved_attendance`/`apply_discovered_members` batch-growth call from `main()` entirely (no
  roster file left to grow — an unmatched attendance name simply stays as extracted until the registry
  picks it up naturally from a future document, same as any other unmatched entity).

### Phase 3 — glossary.json living growth, registry-backed

- `glossary_utils.discover_term_candidates(meeting_data, registry)` compares a meeting's own generated
  content (timeline speakers, topic titles, summary text) against the *entire* registry, not just that
  meeting's own documents. This is a real capability gain over a meeting-scoped design: an entity
  extracted from meeting A's agenda can correct a mishearing of the same entity in meeting B's
  generated content, even though B has no official document of its own naming them.
- `discover_recurring_speaker_candidates()` (cross-meeting frequency matching, no registry backing
  required) is retained as the fallback of last resort — only fires for entities the registry has no
  record of at all (true registry-blind cases, e.g. a public commenter genuinely never named in any
  official document).
- `apply_discovered_terms(glossary_entries, observations)`: evidence-scoped `scope: [slug, ...]`,
  auto-widening, promotion to global (`scope` key removed) at `PROMOTION_THRESHOLD` (2) independently
  confirming meetings — no human gate, no "skip if hand-curated" branch (see Decisions).
- `render_glossary_hints()` / new `replacements_for_meeting(entries, slug)`: note-free hints (see
  Decisions); scope-aware replacement — `_enforce_glossary_pass()` computes replacements per-file by
  slug instead of one global map applied to every file.
- Delete `SPESPA`/`SPTA` from `glossary.json` (dead weight, see Decisions). The remaining 7 entries
  (Kaler, Skillin, Skillins, Atkinson Duina, Claire Holman, Eleni Richardson, Ishmeel Daniels) keep
  their current values unchanged, lose their special protected status, same as everything else.

### Documentation

- `docs/pipeline.md`: add a `glossary.json` row to the "Data Files Reference" table (currently
  missing); remove the `board_members.json` section and any references to it; new section for the
  Terms Registry, including that it now also serves attendance-name canonicalization; document the
  explicit **spelling hierarchy** alongside the existing content hierarchy
  (`_find_priority_source_doc`):
  1. `glossary.json` entries with no `scope` (global — hand-typed or promoted, no distinction anymore)
  2. Terms Registry (corpus-wide, vetted-document-backed, even if not yet promoted into glossary.json)
  3. `glossary.json` entries still carrying `scope` (single/few-meeting evidence, not yet corroborated)
  4. Cross-meeting frequency matching only (registry-blind, weakest tier)
- `docs/prompts.md`: update §1 (no more board-roster hint — document-sourced grounding only, see
  Finding 5/6), §6 (registry-backed extraction, no more per-doc blob).
- Remove the "Board attendance name canonicalization" section from `docs/pipeline.md` and replace with
  a short note on registry-backed attendance canonicalization in the new Terms Registry section.

### Tests

`scripts/test_name_matching.py`, `scripts/test_terms_registry.py`, `scripts/test_glossary_utils.py` —
stdlib `unittest`, colocated with the scripts they test, `patch()`-on-qualified-name style matching
`test_source_data.py`/`test_drive.py`'s existing conventions. Not wired into CI (no Python test step
exists in `deploy.yml` today; out of scope here, matches existing state).

## Acceptance Criteria

- [x] Official-term extraction output is consolidated into one corpus-wide, lineage-tracked Terms
      Registry instead of fragmented per-document blobs (`scripts/terms_registry.py`), built
      incrementally as `post_process.py` processes each meeting's documents
- [x] Official-term loading does two network round trips per document on a cache miss only, not on
      every cache hit (`entry['terms']` inline on the catalog, lazy `terms_blob` migration)
- [x] `board_members.json` and `board_members_utils.py` are deleted; attendance-name canonicalization
      reads from the Terms Registry via the shared matcher instead (`terms_registry.canonical_person`)
- [x] The dead next-meeting stub-attendance seeding in `source_data.py` is removed
- [x] `glossary.json` grows automatically from evidence (registry matches + cross-meeting frequency
      fallback), bounded by evidence scope, with no human-review gate
- [x] No entry in `glossary.json` is permanently protected from automated update based on how it
      originated (`apply_discovered_terms`'s "skip if hand-curated" branch removed)
- [x] `note` text on glossary entries never reaches the model; dead-weight note-only entries
      (`SPESPA`, `SPTA`) removed
- [x] `generate_agenda_preview()` and `synthesize_topic()` receive glossary grounding; the hardcoded
      duplicate spelling list in `synthesize_topic()` is gone
- [x] An explicit spelling-precedence hierarchy is documented in `docs/pipeline.md` alongside the
      existing content-precedence hierarchy
- [ ] **Live-verified against the real corpus** — everything above is implemented and passes locally
      (71 unit tests; `_enforce_glossary_pass` run against the real `src/meetings/*.njk` produces
      zero diff; `npm run build` succeeds), but discovery quality (fuzzy-match thresholds, registry
      consolidation behavior), the actual Drive/GCS call reduction, and whether the roster-hint
      removal holds up against a real Gemini call all still need a real `--bucket` pipeline run —
      see Verification in the design section above for the exact local/live boundary

## Work Log

- 2026-09-15/16: targeted fix for the originating bug (3 `glossary.json` entries, hand-applied to
  `2026-09-14.njk`) — see commit history.
- 2026-11-12: full architectural review conducted across chat/plan discussion. Design went through
  several rounds: generalize glossary beyond person names → find coverage gaps in non-transcript
  generation paths → question "static reference data" naming, find board_members.json's dormant
  `roles` field and a real garbage-entry pattern from inconsistent surname matching → decide to remove
  curated-vs-auto-discovered protection rather than the data → question board_members.json's prompt-hint
  role and its data provenance → decide to retire the file entirely in favor of registry-backed
  attendance canonicalization, which also eliminated the need for the garbage-entry backfill work.
- 2026-11-12: implemented all three phases. New: `scripts/name_matching.py` (shared matcher —
  `match_unique`, `surname_match`, `fuzzy_match`, `fuzzy_surname_match`; the surname/fuzzy split
  wasn't fully worked out in the design pass — writing real test cases against "Elyse Holman" →
  "Claire Holman" and "Holeman" → "Claire Holman" found that a single matcher can't cover both
  "correct surname, wrong first name" and "surname itself misheard", so they're two distinct
  functions, chained in that order by both consumers), `scripts/terms_registry.py` (registry
  load/save, `ingest_document_terms` with bidirectional fuller/fragment consolidation,
  `canonical_person`). Rewrote `scripts/glossary_utils.py` (note-free hints, scope-aware
  replacement, registry-backed + frequency-fallback discovery, protection-free apply). Edited
  `scripts/post_process.py` (inline term caching, registry wiring, glossary discovery step 2b,
  glossary hints threaded into `generate_agenda_preview`/`synthesize_topic`, hardcoded SPELING line
  removed) and `scripts/process_transcripts.py` (inline term reading, registry-backed attendance
  canonicalization, board_members_utils removed entirely). Deleted `scripts/board_members_utils.py`,
  `src/_data/board_members.json`; removed the dead stub-seeding line from `source_data.py`; removed
  `SPESPA`/`SPTA` from `glossary.json`. Added `scripts/test_name_matching.py`,
  `scripts/test_terms_registry.py`, `scripts/test_glossary_utils.py` (71 tests total, all passing).
  Verified locally: full test suite green, `_enforce_glossary_pass` run against the real
  `src/meetings/*.njk` corpus produces zero diff (confirms the 7 surviving unscoped glossary
  entries behave identically under the new scope-aware replace logic), `npm run build` succeeds.
  Not yet verified: anything requiring live GCS/Drive access or a real Gemini call (registry
  build/consolidation quality against the real corpus, the actual network-call reduction, a live
  roster-hint-removal regression check) — needs a real `--bucket` pipeline run, see the now-partially-
  checked Acceptance Criteria above.
