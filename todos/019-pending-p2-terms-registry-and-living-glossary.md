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
- **"Remove the protection, not the data."** *(superseded 2026-11-15, see below.)* `glossary.json`'s
  existing curated content stays as its starting state — nothing is deleted or reset (except
  `SPESPA`/`SPTA`, per above). But no entry, regardless of whether a human originally typed it or the
  pipeline discovered it, is ever permanently locked against automated update again.
  `apply_discovered_terms()` drops the "skip if it collides with a hand-curated entry" branch; a
  collision now means *merge into it*, not *defer to a human*. `auto_discovered` becomes pure
  provenance metadata — it no longer gates anything.
- **Reversed 2026-11-15: delete the data too, and drop `auto_discovered` entirely.** Once there's no
  "hand-curated vs. auto-discovered" distinction left to protect, keeping the 7 original hand-curated
  entries around as permanent, un-reconstructable seed data contradicts "build for the reality of the
  available evidence" — if the corpus doesn't currently support a correction, the file shouldn't assert
  one. The 7 original entries (Kaler, Skillin, Skillins, Atkinson Duina, Claire Holman, Eleni
  Richardson, Ishmeel Daniels) were deleted outright; every entry in the file is now produced the same
  way, by the same discovery mechanism, with no permanently-protected seed data. `auto_discovered` is
  removed from the schema (nothing to tag). One known risk accepted knowingly: `Ishmeel Daniels` (a
  recurring public commenter with no official-document backing at all — the entry existed only to fix
  a `Judd Daniels` mishearing) doesn't appear anywhere in the current trailing-5-meetings window, so
  there's nothing to rediscover it from; if `Judd Daniels` recurs before
  `discover_recurring_speaker_candidates` independently re-earns the correction (2 confirmed meetings),
  it will render uncorrected until it does. Recoverable via git history in the meantime, since nothing
  here has been pushed past this local branch.
- **Favor the complete `first_name last_name` form as `correct`, and split `aliases` (legitimate
  alternate forms — shortform, title, role-reference) from a new `misspellings` field (genuine
  transcription errors), even though both are corrected from the same way.** An official document or
  transcript stating only "Ms. Holman" never asserted the person has no first name, just omitted it —
  so a later mention that supplies the full name (`Claire Holman`) should become `correct`, even though
  it's less frequently attested than the title-only form. Implemented in
  `name_matching.is_complete_person_name`/`is_subset_name` (completeness check, and the
  containment-vs.-conflicting-first-name distinction that decides `kind`), threaded through
  `terms_registry.py`'s own canonical-spelling upgrade logic, `glossary_utils.discover_term_candidates`
  /`discover_recurring_speaker_candidates` (which now tag each observation `kind: 'alias'` or
  `'misspelling'`), and `apply_discovered_terms` (files the alias under the matching list).
  `to_replacement_map`/`render_glossary_hints` read both lists — the split is for readability in the
  file, not a difference in what's enforced.
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

## Additional Open Items (Sourcing Layer)

Surfaced during this same corpus review while checking whether `master_material_map.json` is
still the right place to record per-file findings. Independent of the Terms Registry work above —
tracked here only because they came up in the same session — and shouldn't block Phase 2/3.

- [x] **`no_extractable_text` surfaced in `master_material_map.json`.** Already implemented (see
      the 2026-11-13 Phase 1 entry below): `source_data.py` projects
      `catalog[file_id]['no_extractable_text']` onto each affected `drive[]` doc entry when true.
      Confirms the assumption behind this ask — `master_material_map.json` is still the one
      per-meeting, per-file artifact worth checking for this kind of finding; `drive_catalog.json`
      remains the separate internal Drive-resolution cache it already was (see `docs/pipeline.md`),
      not a competing source of truth.
- [x] **Vimeo video capture stores no label, unlike `drive[]` docs.** Fixed:
      `vimeo.get_vimeo_mapping()` now returns `{date_slug: {'url': ..., 'label': title}}` instead
      of a bare URL string; `master_material_map.json`'s `video` field carries the label through
      the same way `drive[]` entries already do. `source_data.py`'s `.njk` frontmatter writer
      (`video_url`, consumed by `meeting.njk`/`status.njk`) is unchanged — still a plain string,
      unpacked from the new dict shape at the one place that reads it
      (`video_info.get('url')`, `source_data.py` reconcile step). `process_transcripts.py`'s
      vimeo-backfill path updated to match. 75 existing unit tests pass unchanged (no test covered
      this shape before); spot-checked live against `vimeo_master_list.json` — `2026-07-22` and
      `2026-08-04` (the stale non-school-board examples above) correctly return no match, and a
      real school-board entry now returns both `url` and `label`.
- [ ] **Confirm non-school-board videos clear on next full run.** The *local* copy of
      `master_material_map.json` (last regenerated 2026-08-17, predates `f0a9736` "Map only School
      Board Vimeo videos to meeting dates", 2026-10-15) currently shows ~12 stale non-school-board
      videos mapped to dates (e.g. `2026-07-22` → `https://vimeo.com/1212388693`, titled "South
      Portland Planning Board - July 22 2026"). Checked `scripts/sourcing/vimeo.py`'s
      `_SCHOOL_BOARD_TITLE` filter against that title directly — it correctly rejects it. `all_data`
      is rebuilt from scratch every run (`source_data.py:518`) and `get_vimeo_mapping()` is always
      recomputed from `vimeo_master_list.json` with the current regex (never cached), so this
      should self-heal on the next full pipeline run with no code change — but unverified live,
      since the deploy workflow is currently disabled for the Terms Registry rebuild above. Worth a
      quick check (`uv run python3 scripts/source_data.py --bucket ... --force` or equivalent) once
      that workflow is re-enabled, to make sure nothing between here and there reintroduced the gap.

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
- 2026-11-13: the first full live `--bucket` pipeline run (the verification gap noted above)
  surfaced two real production bugs, both now fixed:
  1. **Thread-unsafe registry mutation.** `post_process.py`'s term-extraction step runs
     `ingest_document_terms` for many meetings concurrently via a `ThreadPoolExecutor`, all
     mutating the same in-memory `registry` dict with no lock. The run log showed 8 meetings
     silently losing their extracted terms to `"dictionary changed size during iteration"`
     (`entities_of_type`'s list comprehension over `registry.values()` racing another thread's
     `registry.pop()`/write), caught and swallowed by `post_process.py`'s broad `except`. Fixed by
     giving `terms_registry.py` its own `threading.Lock()` around the mutating path — the module
     that owns the registry's mutation now owns its own thread-safety, callers don't need to know.
     Regression test: `test_terms_registry.py::test_concurrent_ingestion_does_not_race` (verified it
     fails reliably without the lock before confirming it passes with it).
  2. **Runaway false-match corruption of `glossary.json` (the serious one).** The same run grew
     `glossary.json` from 7 hand-curated entries to 626, many garbage, and took 34 minutes just for
     the discovery step. Root cause: `discover_term_candidates`'s place/organization/program
     matcher slides an n-gram window across every word of every meeting's free text and
     fuzzy-matches the whole window against each multi-word registry term via whole-string
     `SequenceMatcher.ratio()`. For a term with a generic trailing phrase (e.g. "Kaler Elementary
     School"), *any* window sharing that tail scores above the 0.8 threshold regardless of the
     leading word — confirmed `"for elementary school"` vs `"kaler elementary school"` scores 0.86.
     Those false matches then collided into the pre-existing `"Kaler"` entry via containment in
     `apply_discovered_terms`, because legacy entries have no `type` field and are therefore never
     excluded as collision candidates (a deliberate earlier decision — see 2026-11-12 above —
     whose blast radius this bug exposed). Separately, some registry terms are themselves
     missing-space text-extraction artifacts (e.g. `SouthPortlandWineCompany`, likely a PDF
     text-extraction defect, not new to this change) and were being promoted straight into
     `glossary.json`'s `correct` field with no sanity check — meaning `render_glossary_hints`
     would have told Gemini the garbled run-together spelling IS correct.
     Fixed both: (a) `discover_term_candidates` now pre-filters each n-gram window on a cheap
     fuzzy match between the window's *leading* word and the registry term's leading token before
     running the expensive whole-string `fuzzy_match` — mirrors the surname-anchor already used
     for persons, just anchored on the leading token instead of the trailing one, since these
     entities are proper-noun-first/generic-tail-last; (b) `apply_discovered_terms` now rejects any
     `correct` candidate that has no spaces and is longer than 15 characters
     (`_plausible_correct_spelling`) before ever adding or merging it. Also reverted the committed
     `glossary.json` back to the known-good 7-entry state (`git show 0056f75:src/_data/glossary.json`)
     — the corruption had already landed on `main` via the deploy workflow's auto-commit, actively
     feeding bad hints into every subsequent Gemini call until reverted.
     Regression tests: `test_glossary_utils.py::test_generic_trailing_phrase_does_not_false_match`,
     `::test_garbled_concatenated_correct_is_rejected`, `::test_normal_single_word_correct_is_not_rejected`.
     Still not verified: a second live `--bucket` run to confirm the fixed discovery step produces
     sane, bounded output against the real corpus (not just that it no longer reproduces this
     specific failure mode in unit tests).
- 2026-11-13 (continued) — **root cause found; redesign in progress on branch
  `terms-registry-rebuild`.** A second local `--bucket --force` run with the fixes above still
  produced 28,808 bad observations (down from 182,487, but not sane) — the leading-token anchor
  only loosely filters that one token before falling back to whole-string `fuzzy_match`, so it
  doesn't stop a *generic* leading word (e.g. "Superintendent ...") or raw substring containment on
  short/generic terms (`"and"` inside `"Luanda"`) from still producing false matches. A side-chat
  investigating the same issue independently reported deeper corruption already present in the
  live (unversioned) `terms_registry.json` itself — ~120 concatenated entity names, ~156 no-space
  person names, and a concrete `fuzzy_surname_match('Dudley', ...)` → `'MadisonDudley'` false-match
  demo. Re-verified every claim directly against the live GCS registry before acting on it.

  Traced specific garbled registry entity names back to their source document (via each entity's
  `sources` field) and pulled the raw cached `official_docs/*.txt` blob for that document — the
  garbling was present in the raw cached text itself, not introduced by Gemini or by the
  registry/glossary matching code. **Root cause: `scripts/sourcing/drive.py`'s PDF extraction used
  `pypdf`'s plain-mode `extract_text()`, which drops the space between adjacent words for a
  meaningful share of this archive's PDFs** (e.g. `"BOARDOFEDUCATIONMEETINGAGENDA"`) — every
  downstream consumer (Gemini extraction, registry ingestion, glossary matching) was faithfully
  processing already-corrupted input. Pre-existing workarounds already in `drive.py` for the same
  defect (`_date_pattern`'s `\s*`-tolerant regex, `extract_location_from_content`'s length-based
  reject-guard) corroborate this was a known, previously-patched-around-but-never-fixed defect.

  User decision: stop chasing symptoms in the matching layer, fix the actual root cause first, then
  *measure* against the real corpus before deciding what in the matching layer is still needed vs.
  dead weight that only existed to compensate for bad input. The "Automate, Build, and Deploy"
  GitHub Actions workflow was manually disabled (`gh workflow disable`) to stop further corruption
  while this is in progress — **do not re-enable until Phase 2/3 below are done and verified.**

  **Phase 1 (extraction fix) — implemented and verified on `terms-registry-rebuild`, not yet
  committed:**
  - `scripts/sourcing/drive.py::read_file_text` — PDF branch swapped from
    `pypdf.PdfReader(...).extract_text()` to `pymupdf.open(...).get_text()`. `pymupdf` pinned
    `<1.25` in `pyproject.toml`/`uv.lock`/`deploy.yml` (unpinned resolves to `2.1`, which pulls in
    `onnxruntime`/`numpy`/`pymupdf-layout`/`pymupdf4llm` as unconditional transitive deps — ~110MB —
    for features this pipeline never uses; `<1.25` resolves to the lean, dependency-free `1.24.14`
    with identical extraction quality for this use case). `pdfplumber` and pypdf's
    `extraction_mode="layout"` were both tried and rejected (pdfplumber uniformly worse; layout mode
    fixes some spots but glues others plain mode got right, e.g. "Call to Order" → "CalltoOrder").
  - Verification: full existing test suite (75 tests) passes unchanged; a randomized 20-document
    sample spanning the full 2023-2026 corpus, measured with a "glued word" heuristic (a ≥13-char
    token containing a lowercase→uppercase transition), went from 264 total artifacts under the old
    extractor to 0 under the new one, with zero documents where the new extractor was worse.
  - Also added: `read_file_text` now returns `''` (distinct from `None`) when a PDF extracts
    cleanly but yields no text at all (a scanned/image-only PDF) — `build_meeting_map` sets
    `entry['no_extractable_text'] = True` on the Drive catalog entry in that case, and
    `source_data.py` projects that flag onto the per-doc dict it writes into
    `master_material_map.json`'s `drive[]` list (only when true, to avoid diff noise) — discoverable
    per-file without a dedicated review page.
  - Changed files on this branch so far: `.github/workflows/deploy.yml`, `pyproject.toml`,
    `scripts/source_data.py`, `scripts/sourcing/drive.py`, `uv.lock`. Not yet committed.

  **Phase 2 — registry rebuild (not started):**
  1. The raw-text GCS cache (`official_docs/*.txt`, keyed by `(file_id, modified_time)`) is itself
     corrupted and won't self-heal — Drive's `modifiedTime` hasn't changed on the affected files, so
     a normal run will cache-hit the old garbled text even with the extractor fixed. Needs an
     explicit invalidation: clear `text_blob`/`modified_time` on the affected `drive_catalog.json`
     entries (or force a full re-extract) so `get_or_extract_text` treats them as a cache miss.
  2. Don't patch the live `terms_registry.json` in place — rebuild it from an empty dict by
     re-running term extraction (the Gemini call in `post_process.py`'s `_extract_official_terms`)
     against the newly-correct text for every official doc, then `terms_registry.save_registry` to
     overwrite the live blob. Patching is unreliable: there's no way to tell, after the fact, which
     existing entries came from good vs. corrupted text.
  3. `terms_registry.json` is unversioned (no git, no revert) — dump before/after counts and a
     sample diff for a manual sanity check before overwriting the live blob.
  4. Check whether any `glossary.json` entries exist only as corrections for registry entries that
     won't exist post-rebuild, and retire those.

  **Phase 3 — matching-layer hardening (not started), in this order:**
  a. An ingestion-time validation gate directly in `terms_registry.ingest_document_terms`
     (`scripts/terms_registry.py`) — the single authoritative point that mutates the registry —
     rejecting implausible terms (glued words, single letters/too-short) before they can enter it,
     so a future upstream bug can't re-corrupt it the same way.
  b. Per-token comparator in `glossary_utils.discover_term_candidates` (~lines 131-162) replacing
     whole-string `difflib.SequenceMatcher.ratio()`: every token position must independently match
     its corresponding entity token, not just the (loosely fuzzy-filtered) leading one.
  c. Word-boundary-aware containment in `name_matching.fuzzy_match` (line 88): `raw_norm in
     candidate_norm` is a raw Python substring check, not word-level — `"and"` matches inside
     `"Luanda"`, `"ban"` inside `"band"`. Needs token-list containment or `\b`-anchored matching.
  d. Tighten `glossary_utils._plausible_correct_spelling` (~lines 204-210): `len(text) <= 15` misses
     shorter glued words (`"SchoolBoard"`, `"BrownSchool"`, 11 chars). Detect the actual glued-word
     signature (lowercase→uppercase transition inside the token — the same heuristic used above to
     verify the extraction fix) instead of relying on length alone.
  e. Candidate-side minimum-length/genericity gate in `fuzzy_match`: today's `len(raw_norm) < 3`
     guard only protects the *observation* from being too short; a short/generic *candidate*
     (`"MA"`, `"ICE"`) can still greedily consume longer raw text via containment.

  **Partially addressed 2026-11-15, remainder still deferred:** longform/shortform + role-template
  conventions for glossary entries. Surfaced while reviewing a local simulation of the
  glossary-discovery layer: some entities genuinely have more than one *correct* form depending on
  context — e.g. `Superintendent Angela Atkinson-Duina` (full/formal) vs. `Superintendent
  Atkinson-Duina` (short) — and some roles may warrant a preferred display template (`Board member
  {surname}`). The *completeness* half of this (always favor a complete `first_name last_name` form as
  `correct`, even over a more-frequently-attested title-only form) is now implemented — see the new
  Decision above and `name_matching.is_complete_person_name`. What's still deferred: there's still no
  notion of "this shorter form is already fine in this context, leave it alone" beyond bare
  completeness — e.g. choosing between an already-complete formal form and an already-complete short
  form, or a role-based display template, when both are equally "correct" and context (not
  completeness) should decide. Pick up separately, with its own design pass on what counts as an
  acceptable variant vs. something that still needs correcting.

  **Recommended approach for Phase 2/3:** not a from-scratch rewrite. The core architecture (Terms
  Registry as corpus-wide canonical store; `glossary.json` as the transcript-mishearing correction
  layer; the three-tier person matcher `match_unique` → `surname_match` → `fuzzy_surname_match`)
  solves a real problem independent of text quality (audio mishearings) and should stay. What's
  uncertain is how much of the *compensating* logic built specifically around the old corrupted
  extraction (the leading-token anchor pre-filter, `_plausible_correct_spelling`'s length heuristic,
  `drive.py`'s `\s*`-tolerant date regex and location length-guard) is now dead weight vs. still
  load-bearing — decide that by measuring against the actual re-extracted corpus, not by guessing
  upfront.

  **Iteration workflow, to set up next:** separate slow data generation (re-extracting the ~75
  already-processed docs' text with `pymupdf`, one Gemini term-extraction pass over that clean
  text — needs live Drive/Gemini access, dumped once to local scratch files) from fast
  matching-logic iteration (registry-build + `discover_term_candidates`/`apply_discovered_terms`,
  pure Python, re-run repeatedly against the frozen local snapshot with no GCS/Gemini calls per
  iteration). Only promote (overwrite the live `terms_registry.json`/`glossary.json`) once the
  local snapshot's output looks right.

  **Feedback format, to use each iteration:** not raw JSON diffs — a short report with (1) counts
  (entities by type, observations, merges), (2) a flagged list via a "looks wrong" heuristic
  (glued-word pattern, very-short term, unusually high source-merge count), (3) a diff against the
  previous iteration, (4) a small random sample of normal-looking entries for a gut check. The user
  points at specific flagged/sampled terms that look wrong by name; the matching code gets adjusted
  and re-run against the same frozen snapshot — no GCS/Gemini cost per round.

  **Immediate next step for whoever picks this up:** on branch `terms-registry-rebuild`, build the
  local snapshot described above (re-extract the ~75 docs, run term extraction once, save results
  to the scratchpad) and produce the first iteration's report for review. Do not touch the live
  `terms_registry.json` or `glossary.json` GCS state, and do not merge this branch, until the user
  has reviewed at least one local iteration. Do not re-enable the GitHub Actions deploy workflow
  until Phase 2/3 are done and verified.

- 2026-11-13 (continued) — **first local iteration run, scoped down from "~75 docs" to the
  trailing 5 meetings' official docs** (user's explicit choice, to keep iteration cheap — 8 docs
  across `2026-11-09`, `2026-11-02`, `2026-10-26`, `2026-10-14`, `2026-10-01`; live catalog
  actually holds 188 official docs/103 meetings with cached text, not 75 — that figure was a
  stale estimate, corrected here). Also landed, independent of the registry work: Vimeo video
  capture in `master_material_map.json` now stores `{'url', 'label'}` instead of a bare URL
  string (`vimeo.get_vimeo_mapping()`, `source_data.py`, `process_transcripts.py`), matching
  `drive[]` doc entries — closes the open item above.

  **Iteration 1** (fresh pymupdf extraction + Gemini term extraction, local registry, nothing
  written to GCS): 349 raw observations → 152 entities (person 55 / organization 36 / program 40
  / place 21). No glued-word corruption anywhere in the extracted text or the registry — the
  Phase 1 extraction fix holds up on a real sample. Found one real merge-logic bug by gut-check
  (not yet a human-pointed finding — the user accepted this analysis and asked for the fix
  directly): `terms_registry._person_match` stripped the title off the *incoming* term but never
  off an already-registered *candidate*, so a candidate that itself still carried a title (e.g.
  "Mr. Feller", extracted that way from an earlier doc) couldn't trailing-token-match a later
  doc's fuller "Mr. Daniel Feller" — confirmed via `match_unique("Daniel Feller", [{"term": "Mr.
  Feller"}])` returning `None` even with no title on the raw side at all. Same root cause showed
  up one layer down in the fuzzy-match fallback: "Mr. Mitchell Ouellet" vs. "Mitch Ouellet" scored
  0.788 (just under the 0.8 bar) purely because of the leftover "Mr. " prefix; 0.897 without it.
  Separately noted, not fixed (lower priority, different mechanism): the same literal string
  extracted with a different `type` across documents can't merge (registry key is `type:term`) —
  `Taj` (person vs. organization — it's a person, confirmed against source text), `GoFundMe` and
  `Trump Accounts` (organization vs. program) each ended up as two disconnected entities. And one
  *intentional* non-merge worth flagging so it isn't mistaken for a bug later: `Kaler Elementary`
  / `Kaler School` stayed separate (ratio 0.5, no whole-word containment) — correctly
  conservative, same reasoning as the fix for the original over-eager-merge corruption.

  **Fix applied:** `terms_registry._person_match` now title-strips both sides before the
  trailing-token comparison; the fuzzy-match fallback for `person`-type terms (not place/org/
  program, which have no notion of "title") now title-strips the raw term before calling
  `name_matching.fuzzy_match`. Two new regression tests in `test_terms_registry.py`
  (`test_titled_surname_merges_with_titled_full_name`,
  `test_titled_nickname_merges_via_fuzzy_fallback`); full suite (77 tests) passes.

  **Iteration 2** (re-ingested iteration 1's already-extracted terms through the fixed code — no
  new Gemini calls, since the text/terms hadn't changed): 152 → 149 entities. Verified directly:
  `Mr. Feller` + `Mr. Daniel Feller` → one entity (8 sources, canonical spelling upgraded to the
  fuller form); `Mr. Risch` + `Mr. George Risch` → one entity (8 sources); `Mitch Ouellet` + `Mr.
  Mitchell Ouellet` → one entity. `Dr. Atkinson Duina` / `Ms. Angela Atkinson-Duina` did **not**
  merge, as expected — title-stripping doesn't touch it; the surname itself tokenizes differently
  ("Atkinson Duina", two tokens, vs. "Atkinson-Duina", one hyphenated token), a separate, not-yet
  fixed gap. Not in scope for this round — flagged for a future iteration alongside the
  type-inconsistency duplicates (`Taj`, `GoFundMe`, `Trump Accounts`) above.

  **Fixed in a third pass, same session:** both items left open above.
  - **Hyphen-vs-space tokenization.** Added `name_matching.name_tokens()` — splits on hyphens the
    same as whitespace — and switched every token-list comparison in `name_matching.py`
    (`surname`, `match_unique`, `fuzzy_surname_match`) and `terms_registry._person_match` to use
    it instead of a plain `.split()`. Scoped deliberately to the token-*list* matchers only;
    `fuzzy_match`'s whole-string containment/ratio comparisons (used for hyphenated program/
    policy names like "McKinney-Vento" or "KLG-R") are untouched since those were never broken by
    this.
  - **Cross-document type-inconsistency duplicates.** `_ingest_document_terms_locked` now checks,
    before creating a new entity, whether the exact same literal string (case-insensitive) already
    exists under a *different* type; if there's a unique such match it merges into it (keeping the
    first-established type and spelling) rather than forking a second entity. Exact-string only
    (not fuzzy) and safe-degrade on ambiguity (2+ existing cross-type matches for the same string
    → stays separate) — same posture as every other matcher here. This also caught one more
    instance iteration 1's manual scan had missed: `South Portland School District` extracted as
    both `organization` (2 sources) and `place` (1 source) — now one `organization` entity, 3
    sources.
  - Four new regression tests (`test_hyphenated_surname_merges_with_spaced_form`,
    `test_cross_type_exact_string_reconciles_to_first_type`,
    `test_cross_type_ambiguous_match_stays_separate` in `test_terms_registry.py`;
    `test_hyphenated_surname_matches_spaced_candidate` in `test_name_matching.py`). Full suite: 81
    tests, all passing.
  - **Iteration 3** (same reingest-from-saved-terms approach, no new Gemini calls): 149 → 144
    entities. Verified directly: `Dr. Atkinson Duina` + `Ms. Angela Atkinson-Duina` → one entity
    (5 sources); `Taj`, `GoFundMe`, `Trump Accounts`, `South Portland School District` each
    collapsed from 2 entities to 1. Scanned the full 144-entity registry for any remaining
    same-string-different-type pairs: none. No sign of over-merging (false-positive collapse of
    two genuinely distinct entities) in this 8-doc sample, but that's only ever as strong as what
    this particular 5-meeting window happened to contain — worth keeping an eye on when the scope
    widens.

  **Still open for a future iteration (not yet asked for, just tracked):** nothing currently
  flagged from this scope — the next useful step is widening beyond 5 meetings to surface cases
  this window didn't contain (per the todo's acceptance criteria, the real target is the full
  103-meeting corpus). Local snapshots for this scope live in three scratch dirs outside the repo
  (`trailing5_iter1/`, `trailing5_iter2/`, `trailing5_iter3/` — raw extracted text, per-doc Gemini
  output, and all three registry snapshots); not committed anywhere, regenerable from the 8
  file_ids listed in `trailing5_iter1/run_iteration.py`. Live `terms_registry.json`/`glossary.json`
  still untouched, deploy workflow still disabled.

- 2026-11-14/15 — **tested the glossary-discovery layer itself** (`discover_term_candidates`/
  `discover_recurring_speaker_candidates`/`apply_discovered_terms` — a different code path from
  the registry-ingestion matching fixed above) against the clean 144-entity local registry, using
  the trailing-5 meetings' real generated `.njk` content and the real `src/_data/glossary.json`,
  mutating only an in-memory `copy.deepcopy()` — nothing written back anywhere. Result: 7 real
  entries -> 37 simulated entries, confirming this layer still had the same over-eager
  false-positive class that caused the original corruption, now with concrete reproducible
  examples: pure substring containment on short acronyms (`ICE` inside "serv**ice**"/"off**ice**",
  `DOE` inside "**doe**s"), edit-distance ratio too permissive for short/medium strings
  (`Activitas`/"activities" ratio 0.84, `DEP`/"deep" 0.86), a shared leading phrase masking a
  mismatched trailing token (`"Elementary Reconfiguration Committee"` false-matching "...plan",
  "...maps", "...update" etc., each differing only in the last word), and a punctuation/possessive
  false-positive family not previously flagged (`Portland's`, `Committee's` flagged as "aliases"
  needing correction when they're the correct word plus a grammatical suffix). Also found, and
  deliberately left open this round: `discover_recurring_speaker_candidates` has no
  ambiguity/uniqueness safeguard (unlike every other matcher here) and proposed `Melinda Hall` /
  `Angelica Hall` as a misspelling pair — almost certainly two different people sharing a surname;
  and a minor directionality quirk on `Rebecca Stern` / `Dr. Rebecca Stern`.

  **Before fixing, checked for an off-the-shelf replacement** rather than continuing to harden a
  bespoke matcher: `dedupe` (dedupeio) is the established Python entity-resolution library, but is
  shaped for structured multi-field records with human-labeled training data, not "does this
  free-text window refer to one of ~150 known canonical strings"; `resolvekit` is alias-tolerant
  but scoped to places/countries; `disamby` has no recent activity. None is a clean fit for this
  domain's specific rules (surname-anchoring, title-stripping, evidence-scoped promotion), so
  hardened the existing stdlib (`difflib`) approach instead of adopting a new dependency —
  consistent with this registry's original "no new dependency" design decision (Phase 2 above).

  **Fixes applied**, all stdlib-only, no new dependency:
  - `scripts/post_process.py`'s term-extraction prompt now explicitly tells Gemini to state
    `context` as a stable fact, never meeting-attendance ("present at the meeting") or
    hedged/speculative ("likely", "probably", "appears to be") phrasing — fixing the verbose,
    repetitive `note` field at its source rather than cleaning up after it.
  - `terms_registry.py`: a backstop hedge-fragment filter (`_is_hedge_fragment`) for whatever
    phrasing still slips through non-deterministically — such a fragment is now never accumulated
    into an entity's `context`, at both entity-creation and merge time.
  - `name_matching.py`: new `is_acronym(term)` (short, all-uppercase terms don't get "misspelled"
    the way names do — a near-miss against one is almost always a coincidental resemblance to an
    ordinary word, not a real correction); `fuzzy_match`'s containment check rewritten from a raw
    Python substring check to whole-token containment (`_token_containment`, with each token's
    outer punctuation stripped so e.g. "KLGC" still matches the leading fragment of "KLGC,
    Relations With..."); its ratio path now requires a length-scaled floor (capped at 0.85, so it
    doesn't also tighten legitimately-longer differences like a nickname expansion) and is skipped
    entirely for acronym candidates (acronyms stay matchable via containment, just not via ratio).
  - `glossary_utils.py`: `discover_term_candidates`'s place/organization/program matching rewritten
    from a leading-token-anchor-then-whole-string-ratio check to a true per-token comparator
    (`_tokens_match`) — every token position must independently agree, not just the first; token
    normalization (`_norm_token`) treats "&" as "and" and strips a trailing possessive "'s" so
    these read as "nothing to correct" rather than a false alias. New `dedupe_observations`
    (also wired into `post_process.py`'s combine-then-apply step) collapses exact-duplicate
    `(type, correct, alias, slug)` observations, whether from the same mention repeated several
    times in one meeting's content or from the two independent discovery mechanisms flagging the
    same pair. New `_primary_note` picks the single longest surviving context fragment for the
    glossary's `note` field instead of joining every fragment ever recorded — specificity tends to
    arrive in later documents, not the first one Gemini ever saw.
  - 21 new regression tests across `test_name_matching.py`, `test_terms_registry.py`,
    `test_glossary_utils.py` (94 total, all passing).

  **Re-verified against the trailing-5 local snapshot** (new `trailing5_iter4/` scratch dir,
  re-ingesting `trailing5_iter3/per_doc_results.json` — no new Gemini calls): registry settled at
  144 entities, same count as iteration 3 but not identical — comparing entity keys surfaced one
  deliberate behavior change (`SPSEA`'s 4 sources split into `SPSEA` (1) + a separate `SEA` (3)
  entity, since "sea" was only ever a false containment match inside "sp**sea**" under the old
  raw-substring check) and confirmed two likely regressions from the token-boundary containment
  rewrite were not real regressions: `KLGC`/`L.D. 2106` briefly split from their fuller
  extractions during development (a trailing-comma-vs-no-comma token mismatch introduced by not
  stripping punctuation per token) and were fixed before this count was taken. Re-ran the
  glossary-discovery simulation: 37 raw observations -> 34 after dedup -> **22 simulated entries**
  (down from the pre-fix 37), with every false-positive class above gone and every previously-
  confirmed legitimate correction (Feller, Risch, Holman, Richardson, Jo Prince, Chelsie
  Kazmierski, Connie DiSanto, Aiden Renaghan, Atkinson-Duina aliases, Augusta/August, Maine/
  Mainers/main, Rebecca Stern, Finance Committee) still present. `Melinda Hall`/`Angelica Hall`
  still appears via the (deliberately untouched this round) recurring-speaker fallback.

  Also added to this todo: the longform/shortform + role-template follow-up (Phase 3 section,
  "Deferred, tracked separately") raised during this review, scoped out of this pass on purpose —
  it's about choosing among already-correctly-identified forms, not preventing a false match.

  **Still not done:** `discover_recurring_speaker_candidates`'s missing ambiguity safeguard and
  the `Rebecca Stern` directionality quirk (both noted above, neither asked for yet); widening
  past the trailing-5-meeting local snapshot; any live GCS/Gemini verification. Live
  `terms_registry.json`/`glossary.json` still untouched, deploy workflow still disabled.

- **2026-11-15 (later same day) — deleted the 7 hand-curated entries, removed `auto_discovered`,
  added `aliases`/`misspellings` split, favor complete names.** See the two new Decisions above for
  the "why." Implementation:
  - `name_matching.is_complete_person_name` (title-stripped token count >= 2) and `is_subset_name`
    (order-independent trailing-containment check — distinguishes a legitimate shortform/longform
    relationship from two sides asserting different first names).
  - `terms_registry._ingest_document_terms_locked`'s canonical-spelling upgrade logic: for `person`
    entities, a complete name now always wins over a title-only/surname-only fragment regardless of
    character length (character length remains the tiebreaker between two forms of the same
    completeness; non-person types unchanged).
  - `glossary_utils.discover_term_candidates`: tracks which matcher fired (`match_unique`/
    `surname_match`/`fuzzy_surname_match`) to tag each observation `kind: 'alias'` (containment,
    e.g. "Holman" inside "Claire Holman") or `'misspelling'` (conflicting first name or misheard
    surname); flips `correct`/`alias` when the raw mention is a complete name and the registry's own
    term isn't.
  - `glossary_utils.discover_recurring_speaker_candidates`: same `kind` tagging, plus the ranking
    key now sorts a complete spelling first regardless of raw mention frequency (frequency remains
    the tiebreaker, and the existing "don't flag a tie/reversal" guard now only applies *within* a
    completeness tier, not across one — a complete form can legitimately win over a more-frequent
    incomplete one).
  - `glossary_utils.apply_discovered_terms`: files each alias under `aliases` or `misspellings`
    per `kind` (default `aliases` if `kind` is absent); guards against the same string ending up in
    both lists for one entry. `to_replacement_map`/`render_glossary_hints` (via new `_variants`
    helper) read both lists — the split is for file readability, not a difference in what gets
    enforced or hinted.
  - Found and fixed a real bug this surfaced: `name_matching._TITLE_RE` didn't strip a bare
    "Member" or "Superintendent" (only "Board member"), so e.g. "Member Smith" and "Superintendent
    Atkinson Duina" looked like two-token name *conflicts* ("member"/"patti") instead of the
    legitimate role-prefixed references they are, and were misfiled under `misspellings`. Added
    both to `_TITLE_RE`.
  - 10 new regression tests (`is_complete_person_name`, `is_subset_name`, bare-"Member"/
    "Superintendent" title-stripping, the containment-vs-conflict `kind` split in both discovery
    functions, the completeness-overrides-frequency tiebreak, `apply_discovered_terms`'s field
    routing and already-elsewhere guard, `to_replacement_map`/`render_glossary_hints` reading both
    lists) — 117 total, all passing.
  - **Re-verified against the trailing-5 local snapshot** (`trailing5_iter4/`, re-ingesting the same
    cached `per_doc_results.json`, no new Gemini calls): registry still 144 entities (the title-
    stripping fix didn't change registry-level consolidation, only the glossary layer's alias/
    misspelling classification). Glossary simulation seeded from empty (no hand-curated entries):
    **17 entries** (down from the prior 22-with-seed / 18-no-seed-before-this-fix results) — the
    `Claire Holman` self-contradiction found at the end of the prior session (one entry asserting
    `"Ms. Holman"` as correct, a second asserting `"Claire Holman"`, pulling in opposite directions)
    is gone: a single `Claire Holman` entry with both `"Ms. Holman"` and `"Holman"` as aliases.
  - **Applied to the real file.** `src/_data/glossary.json`'s original 7 hand-curated entries
    (Kaler, Skillin, Skillins, Atkinson Duina, Claire Holman, Eleni Richardson, Ishmeel Daniels) are
    gone, replaced with the 17 entries from the verified simulation above, written via
    `glossary_utils.save_glossary` (same compact one-line-per-entry style). The real file never had
    an `auto_discovered` field to remove. Known accepted risk: `Ishmeel Daniels` is gone with no
    rediscovery path from the current trailing-5 window (see Decisions above) — the other 6 entries'
    specific old mishearings (`Skillen`, `Atkinson-Dena`, `Elyse Holman`, `Heather Richardson`, etc.)
    aren't in this window either, but all 6 underlying *entities* are independently re-established by
    the simulation (just without those specific historical mishearing aliases attached yet), so a
    recurrence of any of those mishearings in a future meeting should be re-caught the normal way.
  - **Still not done:** same open items as above (recurring-speaker ambiguity safeguard, `Rebecca
    Stern` directionality, live GCS/Gemini verification). Live `terms_registry.json` still untouched,
    branch still unmerged, deploy workflow still disabled.
- 2026-11-16: data-review pass over the real file surfaced two more issues, both root-caused and
  fixed before the file was trusted further:
  1. **Multi-speaker parenthetical conflation.** A topic jointly attributed to two co-presenters —
     `"Student Representatives Report on MSMA Conference (Sarah Lupien, Lizeth Rios Blas)"` from
     `2026-11-09.njk` — produced a false entry: `Lizeth Rios Blas` with alias `"Sarah Lupien, Lizeth
     Rios Blas"`, conflating two unrelated people. Root cause: `_speaker_names_in_meeting`'s trailing-
     parenthetical regex captured the entire comma-joined attribution as one raw "name" string,
     oblivious to the joint-attribution convention. That 5-token blob then failed `match_unique`
     (too many tokens) but passed `surname_match`, which anchors only on the LAST token and has no
     bound on unrelated leading content — and the new `is_subset_name`-based `kind` classification
     also passed, since it only checks the raw string's *trailing* tokens, not the whole thing. Fixed
     at the source: added `_split_speaker_list` to split a trailing parenthetical on commas (and a
     leading "and") into separate raw names before any matching runs, so each co-presenter resolves
     independently.
  2. **Bare surname-only aliases are noise, not corrections.** Flagged during the same review: `Smith`
     as an alias of `Patti Smith`, `Holman` of `Claire Holman`, etc. — a common but not useful pattern,
     since there's no misspelling to correct and persisting the bare surname as a literal find/replace
     target risks rewriting an unrelated same-surname person in a future meeting. Added
     `name_matching.is_bare_surname` (true for a raw mention that's a single token with no title at
     all) and skip emitting an observation for any `kind='alias'` case where the alias is a bare
     surname, in both `discover_term_candidates` and `discover_recurring_speaker_candidates`. A
     title/role-prefixed form (`"Ms. Holman"`, `"Member Smith"`) is unaffected — only the literal bare
     surname is dropped.
  - 7 new regression tests (`is_bare_surname`; the joint-attribution split, both as a no-conflation
    check and as independent-resolution-of-both-names; bare-surname producing no observation in both
    discovery functions; the completeness-vs-frequency tiebreak test re-pointed at a titled minority
    spelling since its original bare-surname fixture is now correctly suppressed) — 124 total, all
    passing.
  - **Re-verified against the trailing-5 local snapshot** (same `trailing5_iter4/` cache, no new
    Gemini calls): registry unaffected (144 entities — this was purely a glossary-layer fix).
    Glossary simulation seeded from empty: **15 entries** (down from 17) — the `Lizeth Rios Blas`/
    `Sarah Lupien` conflation is gone with no trace of "Lupien" anywhere in the output, and `Eleni
    Richardson` / `Mr. George Risch` no longer get entries at all, since the only thing ever observed
    about either of them was a bare-surname mention with nothing else to correct.
  - **Applied to the real file.** `src/_data/glossary.json` rewritten again via
    `glossary_utils.save_glossary` with the corrected 15-entry simulation result.
  - **Still not done:** recurring-speaker ambiguity safeguard, `Rebecca Stern` directionality, live
    GCS/Gemini verification. Live `terms_registry.json` still untouched, branch still unmerged,
    deploy workflow still disabled.

- **2026-11-16 (later same day) — fixed the recurring-speaker ambiguity safeguard
  (`Melinda Hall`/`Angelica Hall`), picking up the item deferred just above.** Confirmed against the
  real source (`src/meetings/2026-10-26.njk`, `2026-11-02.njk`, `2026-11-09.njk`) that these are two
  distinct, unrelated real people — Melinda Hall, a parent/community organizer, vs. Angelica Hall, a
  Skillin teacher — who merely share a surname; `discover_recurring_speaker_candidates` had flagged
  the less-frequent one as a "misspelling" of the more-frequent one purely on raw count. Root cause:
  within one `by_surname` group, two spellings can only ever differ by a missing first name/title
  (safe — `is_subset_name`) or a conflicting first name, and a conflicting first name is, from text
  alone, structurally indistinguishable between "misheard" and "two different people sharing a
  surname" — this fallback has no registry/official-doc corroboration to tell those apart, unlike
  `discover_term_candidates`. **Fix:** the function no longer ever resolves a conflicting-first-name
  pair, regardless of frequency lopsidedness — only the safe subset/superset case remains. This also
  made the `same_completeness` frequency-tie guard fully dead code (every same-token-count pair it
  was guarding against now fails the subset check first, unconditionally) — removed. 2 tests replaced
  with regressions for both the original motivating case (`Ishmeel Daniels`/`Judd Daniels` — now
  correctly produces nothing, since there's no registry corroboration for *that* fallback to lean on
  either) and the real Hall case; 125 total, all passing. Re-verified: 14 simulated entries (down from
  15, `Melinda Hall` gone with no trace). Applied to the real file again.

  Also, per explicit user instruction, did **not** add VTT-transcript handling to term extraction (no
  such path exists in production) — documented this as a deliberate scope boundary in
  `docs/pipeline.md`'s "Terms Registry" section instead, including why (a transcript is the lowest-
  priority, ASR-derived source, and folding it into the registry's canonical-spelling ground truth
  would undermine the registry's whole purpose).

- **2026-11-16 (later still) — two more data-review findings, both root-caused and fixed:**
  1. **"Johanna Prince" was being filed as a misspelling of "Dr. Jo Prince" — it's her correct full
     given name, "Jo" is just the short form.** `is_subset_name` only recognizes exact token
     containment, so a nickname/full-name pair (same surname, differing only in a first name that's
     a literal prefix of the other) fell through to `kind='misspelling'`, identical to a genuine
     conflict like "Elyse"/"Claire". **Fix:** new `is_nickname_variant` (name_matching.py) — same
     surname/trailing tokens, first-name tokens differ, shorter is an exact prefix of the longer
     (catches "Jo"/"Johanna", "Liz"/"Elizabeth"; declines "Bill"/"William" rather than maintaining a
     curated nickname dictionary — no regression for that subset, just no improvement yet). Wired
     into `discover_term_candidates` only (not the no-corroboration recurring-speaker fallback, for
     the same reason the Hall fix above exists — a nickname-looking pair there is still an unverified
     identity claim). `correct` stays the registry's own (official-doc-backed) spelling either way;
     this only changes which list the alias is filed under.
  2. **"Augusta"/"August" and "Maine"/"Mainers"+"main" were false positives — common, unrelated
     words coincidentally resembling place names, not mishearings.** Confirmed against real source
     text: "the **August** survey" (the month), "new **Mainers**" (a real demonym, people from
     Maine), "the **main** item" (the ordinary adjective) — all genuine, unrelated word uses that
     would have corrupted the sentence if ever applied as find/replace ("the Augusta survey", "new
     Maine", "the Maine item"). Root cause: `SequenceMatcher.ratio()` scores a boundary
     insertion/deletion (one string an exact prefix/suffix of the other) far higher than a same-
     length mid-word substitution at comparable length — "main"/"maine" scores 0.89, "august"/
     "augusta" scores 0.92, both comfortably clearing even the length-scaled floor from the
     2026-11-12 Activitas/activities fix, which was calibrated for substitution-type edits, not
     insertions. Real mishearings in this corpus ("Caler"/"Kaler", "Holeman"/"Holman") are mid-word
     edits, never boundary ones. **Fix:** new `is_boundary_variant` (name_matching.py) — true when
     one normalized string is an exact prefix or suffix of the other — wired into both
     `fuzzy_match`'s ratio path and `glossary_utils._tokens_match` (containment still applies to
     both, only the ratio path is gated). Deliberately general rather than a hardcoded month-name
     list: it also caught a third, previously unflagged false positive this pass turned up —
     "Finance Committee"/"Finance committees" (the *plural* is correct there: "Chairs of the Policy,
     Buildings & Grounds, and Finance **committees**" names three committees, not a mishearing of
     one) — which the 2026-11-12 work log above had actually mislisted as a "confirmed legitimate
     correction" without checking the real source text; that confirmation was wrong, now corrected.
     Verified safe against the existing nickname-merge path in `terms_registry.py`
     (`test_titled_nickname_merges_via_fuzzy_fallback`, "Mitch Ouellet"/"Mitchell Ouellet") — that
     comparison is never a prefix/suffix relationship at the full multi-token-string level, since
     the surname sits after the differing first name and breaks the boundary shape; confirmed by the
     full suite passing, not just by inspection.
  - 10 new regression tests across `test_name_matching.py`/`test_glossary_utils.py` — 143 total, all
    passing.
  - **Re-verified against the trailing-5 local snapshot:** registry unaffected (144 entities — both
    fixes are glossary-layer only). Glossary simulation: **11 entries** (down from 14) — `Dr. Jo
    Prince` now correctly carries `Johanna Prince` under `aliases`, and `Augusta`/`Maine`/`Finance
    Committee` no longer appear at all, since every observation ever made about any of them was one
    of these false positives.
  - **Applied to the real file.** `src/_data/glossary.json` rewritten again with the corrected
    11-entry result.
  - **Still not done:** `Rebecca Stern` directionality (both of her forms are complete names, so
    this is a stylistic-preference question, not a false-positive bug like the ones just fixed — left
    deliberately untouched); widening past the trailing-5-meeting local snapshot; any live
    GCS/Gemini verification. Live `terms_registry.json` still untouched, branch still unmerged,
    deploy workflow still disabled.
- 2026-11-16: before a from-scratch registry rebuild (reset + full-corpus re-extraction), agreed a
  staged, confidence-building validation plan rather than jumping straight to a live run: (1)
  full-corpus fresh-extraction sanity check (cheap, no LLM cost), (2) full-corpus registry rebuild
  dry run (local/isolated, real Gemini calls, throwaway registry), (3) full-corpus glossary rebuild
  dry run (local/isolated), (4) targeted diff of the 28/99 meetings whose `votes`/`board_attendance`
  came from a `Minutes`-labeled PDF (`votes_source: Minutes`) — the same old pypdf-exposed path,
  separate from and smaller than a full 99-meeting transcript reprocess — against fresh extraction,
  (5) live cutover (write the already-reviewed local result directly to GCS, not a second live
  run). Noted explicitly: regenerating all 99 meetings' VTT-derived content is **not** required by
  a registry rebuild (the registry only ever reads official docs, never VTTs — see docs/pipeline.md)
  and should stay a separate decision, not get bundled in by default.
  - Also added `extractor_version`/`extracted_at` to the Drive catalog schema
    (`sourcing/drive.py::TEXT_EXTRACTOR_VERSION`), stamped on every fresh `text_blob` write and
    checked by both `get_or_extract_text` and `build_meeting_map` before trusting a cache hit — so
    a stale cache entry from before an extraction-logic change (like this session's pypdf->pymupdf
    fix, version 1->2) is force-refreshed automatically on the next run, without the kind of
    manual before/after comparison this fix itself required to find which entries predated it.
  - **Stage 1 run, live, read-only** (full Drive catalog enumeration + fresh `read_file_text` call
    per qualifying doc, zero GCS writes): **188** qualifying agenda/packet/minutes documents found
    (not 174 — prior count was a partial scan), **zero** glued-word artifacts detected (longest
    word-run anywhere: 17 chars, vs. the old bug's 23-30 char runs) — confirms the pymupdf fix holds
    at full-corpus scale. Side findings, not blockers: several dates carry two documents of the
    *same* doc_type (logged to todos/022, sharpens its file_id tie-break question); one
    `SPECIAL MEETING AGENDA 5_30_24` file has never resolved a `meeting_slug` despite a
    filename-parseable date, predates `extractor_version` entirely, and should get a fresh
    resolution attempt automatically next real `build_meeting_map` run via the staleness fix above
    — worth confirming then rather than hand-fixing now.
  - **Still not done:** Stage 2 (registry rebuild dry run) onward; the 28-meeting votes/attendance
    targeted check; live cutover. Live `terms_registry.json` still untouched, branch still unmerged.
- 2026-11-17: **Stage 2 run, live, real Gemini calls, local/isolated** — full-corpus registry
  rebuild dry run via the real production function (`post_process._extract_official_terms`),
  called per-meeting against each `.njk`'s actual frontmatter `docs` list (read via the read-only
  `_read_njk`, not the write-triggering `_enforce_glossary_pass`), forced through fresh
  pymupdf-based extraction (`bucket_uri=""` bypasses the GCS cache entirely) into a fresh, unseeded
  local registry and a catalog-shaped per-file_id patch — both checkpointed to the gitignored
  `rebuild_scratch/` after every meeting (not just at the end), since a run across 93 meetings/174
  documents realistically gets interrupted (it did, twice, hitting the background time limit) and
  each real Gemini call is not cheap to redo.
  - **Found and fixed a real, previously-unknown production bug along the way** (not related to
    the pypdf fix): the file_id-parsing regex used at 7 call sites across `post_process.py`,
    `process_transcripts.py`, and `source_data.py` only matched Drive's uploaded-file URL shape,
    silently skipping any official document authored as a native Google Doc instead (no error, no
    log line). Confirmed real impact: `2024-12-02`'s Minutes doc is a native Doc and that meeting
    has no `votes_source` at all. Fixed with one shared helper
    (`sourcing/drive.py::extract_file_id`), all 7 call sites updated, 143 tests re-passing. Full
    writeup, remaining downstream-impact assessment, and the user's explicit decision not to
    re-run votes/attendance extraction live yet: **todos/023**.
  - **Final result: 170/174 documents captured, 1684 registry entities** (person: 671, place: 196,
    program: 513, organization: 304). The remaining 4 gaps: 2 were the orchestration script's own
    stale copy of the pre-fix regex wrongly marking a meeting "already done" (fixed in the scratch
    script, not production); the final 2 (`2025-01-08`'s "Workshop Speakers & Agenda" and
    `2025-01-13`'s agenda, both native Docs) consistently return a 404 from the Drive API on a
    direct, isolated test — confirmed non-transient, genuinely inaccessible to current credentials
    (most other native Docs in the corpus succeeded fine, so this isn't the regex bug recurring).
    Accepted as a small, documented, permanent gap rather than something more retrying can fix.
  - Outputs: `rebuild_scratch/stage2_terms_by_file_id.json` (catalog-shaped patch, ready for
    `entry.update(...)` into the live `drive_catalog.json`) and `rebuild_scratch/stage2_registry.json`
    (the candidate fresh registry) — both gitignored, in-repo, durable pending review.
  - **Still not done:** Stage 3 (glossary rebuild dry run) onward; the 28-meeting votes/attendance
    targeted check; live cutover. Live `terms_registry.json`/`drive_catalog.json` still untouched,
    branch still unmerged.
- 2026-11-17: Two follow-up design fixes raised while reviewing Stage 2, both local/isolated
  code changes only (no live state touched):
  - **Never source content from native Google Slides/Sheets.** `read_file_text`'s `get_media()`
    fallback (used for anything that isn't a `google-apps.document`) doesn't actually work
    against those — no binary content to download, no plain-text export path the way Docs have —
    so it was silently failing (caught exception, `None`) for any agenda/packet/minutes doc that
    happened to be a Slides deck or Sheet. Fixed at the one shared point both `build_meeting_map`
    and `get_or_extract_text` go through: a new `_UNSUPPORTED_NATIVE_MIMES` set, checked before
    any download is even attempted. Catalog entries for these now get
    `processing: "unsupported_format"` instead of a confusing warning log.
  - **Added a `processing` field to every catalog entry**, replacing the old boolean
    `no_extractable_text`: `text_extracted`, `no_text_layer` (scanned/image PDF),
    `unsupported_format` (Slides/Sheets), `error`, or `none` (doc_type has no extractable content
    at all — misc/transcript). Surfaced into each meeting's `docs[]` frontmatter via
    `source_data.py` the same way `no_extractable_text` was, for any non-`text_extracted` value.
  - **Throttled native-Doc re-extraction to once/day.** A native Google Doc's `modifiedTime` bumps
    on every edit, including autosave — without a throttle, a Minutes doc still being actively
    drafted would re-trigger extraction, and for terms a real paid Gemini call, on every single
    pipeline run while someone's mid-edit. New shared helper
    `drive.text_cache_fresh(entry, mime_type, current_modified)` requires the current
    `extractor_version` plus either an exact `modified_time` match, or — for a native Doc only —
    an extraction already made today (`extracted_at`); further same-day edits don't invalidate it,
    the next day's run picks up the latest version. One helper, used at all three cache-hit
    checks that previously inlined the same condition independently (`get_or_extract_text`,
    `build_meeting_map`, `post_process.py::_extract_official_terms`) — uploaded files are
    unaffected, since their `modified_time` only changes on a genuine re-upload.
  - 8 new unit tests added (`test_drive.py`); full suite (151 tests) passing.
- 2026-11-17: **Stage 3 run, local/isolated, pure in-memory logic (no LLM calls)** — full-corpus
  glossary rebuild dry run using Stage 2's candidate registry (`rebuild_scratch/stage2_registry.json`,
  1684 entities) as the match target. Confirmed glossary growth (`discover_term_candidates`,
  `discover_recurring_speaker_candidates`, `apply_discovered_terms`) is pure logic with zero
  network/LLM cost, unlike Stage 2 — this run took seconds once past `post_process.py`'s own
  module-level eager Gemini-client init at import.
  - Read every real `.njk` file read-only (`post_process._read_njk`, never
    `_enforce_glossary_pass`, which writes files back) across all 99 meetings; glossary started
    empty, not seeded from the live (possibly-affected) `glossary.json` — same posture as Stage
    1/2's catalog/registry, same reason.
  - **Result**: 351 observations (169 alias, 182 misspelling; 34 from the cross-meeting
    recurring-speaker fallback) → 89 candidate glossary entries (person: 73, place: 6,
    organization: 5, program: 5). A simulated next-enforcement-pass preview (replacements_for_meeting
    against each file's real raw content, nothing written) found 1781 total replacement
    occurrences across 63 of 99 meetings.
  - **Found two real, previously-unknown bugs in `glossary_utils.py`'s discovery logic**, first
    exposed at full-corpus scale against a comprehensive registry (neither is related to this
    session's Drive-extraction work): (1) a severe common-word fuzzy-collision class — short
    registry entities that are also ordinary English words (`State`, `Board`, `District`,
    `Maine`, `Brown`, `Small`) fuzzy-match unrelated common words (`start`, `broad`, `distinct`,
    `machine`, `grown`, `smell`, ...), confirmed to account for 326 of the 1781 preview
    occurrences across 58 meetings — dangerous because `_enforce_glossary_pass`'s replace is a
    raw, non-word-boundary substring replace; (2) a smaller multi-person-alias conflation (8
    occurrences, 7 meetings) where a joined co-presenter string in a timeline child's `speaker`
    field becomes the alias for just one of the named people. Reported, not fixed — full writeup,
    proposed directions, and the deliberate stop-and-ask posture: **todos/024**.
  - Outputs: `rebuild_scratch/stage3_glossary.json` (candidate glossary, in `save_glossary`'s
    hand-authored compact-line format) and `rebuild_scratch/stage3_enforcement_preview.json`
    (per-meeting replacement preview, nothing applied) — both gitignored, in-repo, durable pending
    review and pending the todos/024 decision.
  - **Still not done:** todos/024's fix decision; Stage 4 (28-meeting votes/attendance targeted
    check); live cutover. Live `terms_registry.json`/`drive_catalog.json`/`glossary.json` still
    untouched, branch still unmerged.
- 2026-11-17: **todos/024 fixed, on explicit user go-ahead.** Root-caused and fixed both bug
  classes found during Stage 3: added `name_matching.is_dictionary_word` (bundled
  `scripts/common_words.txt`, filtered from the macOS/BSD `words(5)` list — no new pip
  dependency, portable to CI) with a suffix-stripping fallback for inflected forms, wired into
  `glossary_utils._tokens_match` only (deliberately not into person-name matching, where an
  obscure dictionary-word collision is itself a legitimate mishearing — e.g. "Holeman"); added
  `glossary_utils.apply_replacements`, a word-boundary-safe (`\b`-anchored) replacement, replacing
  `_enforce_glossary_pass`'s raw `content.replace()`, used for both production enforcement and
  Stage 3's own preview counting; fixed `_split_speaker_list` to also split on `&` (previously
  comma-only) and applied it to a timeline child's `speaker` field (previously only the topic
  parenthetical). Re-ran Stage 3 end to end: confirmed 0 of the originally-flagged 334 bad
  occurrences remain, all 6 place-type and 2 of 5 organization-type bad entries are gone
  entirely, the 5 person-entries that existed only because of the multi-person conflation are
  gone, and one new legitimate entry ("Coach Kahill") surfaced as a side effect of the split fix.
  13 new unit tests added; full suite (166 tests) passing.
  - **Live-exposure check (read-only, no writes)**: confirmed both bugs are clean in production —
    the live `glossary.json` (11 entries) is 100% `type: person`, so the place/org/program code
    path these bugs lived in has never had a registry entity to misfire against live; no
    multi-person joined aliases present either. Also directly ruled out the Nov 13 "626-entry
    garbage glossary" incident (`3a88b2c`) as a vector for either bug — that was a *different*,
    already-fixed bug (shared-trailing-word ratio matching), and git history confirms it was
    never actually enforced against any existing meeting content before being reverted ~2 hours
    later (the only file touched in that commit was a brand-new, content-free meeting stub).
  - **Incidental finding, unrelated, not fixed**: live-exposure scanning turned up glued-together
    proper nouns (`SOUTHPORTLANDHIGHSCHOOL`, `SouthPortlandMiddleSchool`, etc.) embedded directly
    in 6 meetings' generated timeline/summary text — traced via `git log -S` to July–August 2026
    commits, months before this project and unrelated to either bug just fixed. Spun off to
    **todos/025** (p3), not investigated further.
  - **Still not done:** Stage 4 (28-meeting votes/attendance targeted check); live cutover; the
    native-Google-Doc regex bug's deferred downstream votes/attendance assessment (todos/023);
    todos/025's glued-word investigation. Live `terms_registry.json`/`drive_catalog.json`/
    `glossary.json` still untouched, branch still unmerged.
- 2026-11-17: **Stage 4, read-only, no live writes** — targeted check of whether the two bugs
  fixed this session (pypdf→pymupdf text extraction; the native-Google-Doc `extract_file_id`
  regex fix, todos/023) actually corrupted or blocked real votes/attendance data, as opposed to
  being theoretical risks. Called the real production functions directly
  (`_find_priority_source_doc` → `_read_doc_text` → `_extract_votes_attendance_from_doc`), never
  the full `process_single_meeting()` (which writes the `.njk` back to disk) — kept this entirely
  read-only, same posture as Stages 1-3.
  - **Re-scoped the "~36 no-`votes_source`" population first.** All 36 turned out to be currently
    unprocessed stubs (`stub: true`, `has_transcript: false`) — `process_single_meeting` has
    never run for any of them, for any reason, regardless of either bug. Of the 36, only
    `2024-12-02` actually has a native-Google-Doc Minutes file; the rest are either normal
    uploaded-file Minutes docs nobody's processed yet (22), blocked by a separate newly-found
    dead-code bug in `_find_priority_source_doc` (4 — **todos/027**, new, not fixed), or have no
    written record at all yet. Full impact assessment written up in **todos/023**.
  - **The 28 "`votes_source: Minutes`" meetings** — fresh-re-extracted every one (bypassing the
    GCS text cache, forcing genuinely fresh pymupdf extraction) and diffed against live. **2 of
    28 (`2024-01-08`, `2024-05-30`) show confirmed real glued-word corruption** in currently-live
    vote/policy-name text (e.g. live: `"UseandPossessionof TobaccoandElectronicSmokingDevices"`;
    fresh: `"Use and Possession of Tobacco and Electronic Smoking Devices"`) — concrete proof the
    old pypdf bug corrupted real, already-published content, not just a theoretical risk. One
    more (`2024-06-10`) carries a duplicate phantom attendance entry in live data (`"Ms. Holeman"`
    alongside the real `"Claire Holman"`) that fresh extraction doesn't reproduce — a real data
    quality issue, but an LLM name-mishearing/dedup artifact, not caused by either of the two
    known bugs. The other 25/28 matched on vote/attendance counts with no corruption signature;
    remaining text-level differences were non-deterministic LLM phrasing (trailing periods, vote
    count detail, full-name vs. title+surname) rather than data errors — the latter is also an
    artifact of testing `_extract_votes_attendance_from_doc` in isolation, since the full pipeline's
    registry-based name canonicalization step (which I didn't replicate here) would resolve most
    of that in a real run.
  - **The native-Doc check (`2024-12-02`)** — confirmed the pre-fix regex would never have matched
    its Minutes doc's URL, confirmed the current fix resolves it, and confirmed real extraction
    against it now produces 3 real votes and 5 real attendance records — concrete value, not just
    an unblocked parse.
  - Outputs: `rebuild_scratch/stage4_minutes_diff.json` (all 28 meetings, live vs. fresh, full
    vote/attendance detail), `rebuild_scratch/stage4_native_doc_check.json` (`2024-12-02`'s
    before/after) — both gitignored, in-repo, durable pending review.
  - **Recommendation**: the 2 confirmed-corrupted meetings (`2024-01-08`, `2024-05-30`) are good
    candidates for a targeted, explicit re-extraction once the user approves a live write (not
    done here) — small, well-understood blast radius. `2024-12-02` similarly, once todos/023's
    go-ahead is given. The other 34 "no `votes_source`" stubs don't need any bug-driven fix at
    all — they just need their first real processing pass, on whatever normal schedule that
    happens; todos/027 should land first so that pass picks up their Meeting Summary docs
    correctly where applicable.
  - **Still not done:** live cutover; todos/025's glued-word investigation; todos/027 (new, the
    dead-code Meeting Summary source-selection bug). Live
    `terms_registry.json`/`drive_catalog.json`/`glossary.json` still untouched, branch still
    unmerged.
