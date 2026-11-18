---
title: "design: Terms Registry schema scalability — sources/file_ids and context temporal-dedup"
type: design
status: proposed
date: 2026-11-18
origin: todos/022-pending-p2-registry-schema-scalability.md
---

# design: Terms Registry schema scalability

## Decisions (summary)

1. **`sources` → `file_ids`.** Replace `sources: [{file_id, meeting, doc_type}, ...]` with a bare
   `file_ids: [file_id, ...]` list. Keeps the only property `sources` actually provides
   (idempotency: "have I already folded this document in?") at a fraction of the storage, since
   `meeting`/`doc_type` are confirmed-by-grep read nowhere outside this one append site.
2. **`first_seen`/`last_seen` stay date-only — no `file_id` attached.** No current or foreseeable
   consumer needs a specific file reference for the date range, and attaching one would require an
   arbitrary tie-break rule for meetings with two-or-more same-doc_type documents (confirmed real:
   `2024-04-08` has two `packet` file_ids and two `minutes` file_ids) to serve zero readers.
3. **`context`'s temporal/semantic blindness: defer, not fix.** Milder risk than the original
   todos/013 date-blind-fact bug (`context` never reaches a Gemini prompt — only human-facing
   `note`/glossary display via `_primary_note`), and unlike todos/028's `Molly. Schen` case, there
   is no concrete observed defect yet, only a plausible future one. Revisit if a real
   context-staleness bug surfaces, rather than building speculative mitigation now.

## Problem recap

`terms_registry.py`'s entity schema (`term`, `type`, `context`, `sources`, `first_seen`,
`last_seen`) has two unresolved scale/quality risks, both already described in detail in
todos/022's Problem Statement and Findings — not repeated here. This plan resolves the three open
Acceptance Criteria questions and lays out the concrete implementation.

**Current code, verified against the live file** (todo's cited line numbers have drifted since
todos/021 shipped and added `_find_case_insensitive_key`; using current lines):

```python
# scripts/terms_registry.py:204-217 (_ingest_document_terms_locked)
if existing is None:
    existing = {
        'term': term, 'type': term_type,
        'context': context if context and not _is_hedge_fragment(context) else '',
        'sources': [], 'first_seen': meeting_slug, 'last_seen': meeting_slug,
    }
    mutated = True

registry[key] = existing

source = {'file_id': file_id, 'meeting': meeting_slug, 'doc_type': doc_type}
if source not in existing['sources']:
    existing['sources'].append(source)
    mutated = True
```

`file_id` is always truthy by the time `ingest_document_terms()` is called — both call sites in
`post_process.py::_extract_official_terms` (lines 464, 498) are reached only after
`if not file_id: continue` (line 423-424) — so no null-guard is needed on `file_id` itself in the
new code.

**Confirmed scope by grep** (`grep -rn "sources" scripts/`): `sources` and its `meeting`/`doc_type`
sub-fields are read only inside `terms_registry.py` (the append site) and `test_terms_registry.py`
(8 assertions, all `len(entity["sources"])`-shaped or fixture literals) — nothing in
`glossary_utils.py` or any display-facing code touches them. This matches the todo's own grep
finding; re-confirmed here rather than taken on faith.

## Decision 1 detail: `sources` → `file_ids`

**Rejected alternative — keep `sources` as-is, accept the footprint.** Rejected because the
footprint genuinely grows unboundedly with run count, not document count (every cache-hit re-run
re-walks the same `if source not in existing['sources']` check against an ever-growing list), and
the fields being carried (`meeting`, `doc_type`) have no reader. Paying that cost indefinitely for
literally nothing has no upside.

**Rejected alternative — drop `sources` entirely, no replacement.** This was the user's original
"quick win" framing per the todo, but it silently removes the only thing preventing
`post_process.py`'s unconditional-every-run `ingest_document_terms()` call from re-appending a
duplicate fact/entry on every single cache-hit re-run forever. Not acceptable without a
replacement idempotency guard.

**Chosen: `file_ids: [file_id, ...]`**, built as a deduplicated, sorted list (sort for deterministic
JSON serialization — avoids noisy diffs on every run from dict/set iteration order). This preserves
the idempotency guard (`if file_id not in existing['file_ids']`) in its exact current form, just
without the two unread fields riding along.

**Conditional on todos/028's outcome.** A separate, concurrently-run fix for todos/028 (registry
canonical `term` never gets revised after first-seen) may need to know *which raw term string* was
seen in *which file*, to re-derive the most-frequent/cleanest form among an entity's corroborating
observations — information today's `sources` entries don't carry either (they only ever recorded
`file_id`/`meeting`/`doc_type`, never the raw term string itself, so this isn't a regression caused
by this plan). **If 028 lands first and introduces per-observation raw-term tracking**, this plan's
`file_ids` shape should become `file_ids: [{"file_id": ..., "term": ...}, ...]` instead of bare
strings — still a fraction of today's footprint (two fields instead of three, and no `meeting`/
`doc_type`), but able to back whatever `term`-frequency logic 028 ends up needing. **Check 028's
final diff to `terms_registry.py` before implementing this plan's Step 1** to see whether it
already added such tracking; if not, proceed with the bare-string form.

## Decision 2 detail: `first_seen`/`last_seen` stay date-only

The todo flags that attaching a `file_id` to `first_seen`/`last_seen` would need a tie-break rule
for a meeting with more than one qualifying document of the *same* doc_type (`2024-04-08` has two
distinct `packet` file_ids, two distinct `minutes` file_ids — confirmed in todos/022's 2026-11-16
Stage-1 work-log entry). Any such rule (fixed doc-type priority, first-in-iteration-order, etc.)
would exist purely to answer a question nothing currently asks: no code anywhere reads a `file_id`
off `first_seen`/`last_seen` today, and no concrete future consumer is named in any open todo.
Building a tie-break rule to serve a hypothetical reader is exactly the kind of premature structure
this project avoids elsewhere. **No schema or code change needed here** — this is confirming the
status quo is already correct, not implementing anything.

## Decision 3 detail: defer `context` temporal-dedup

Options considered (per the todo): (a) do nothing, (b) tag each `context` fragment with the
meeting slug/date that produced it. Choosing (a) for now:

- `context` only ever feeds `_primary_note()` (`scripts/glossary_utils.py:223`), which picks the
  *longest* surviving semicolon-joined fragment for the glossary's human-facing `note` field — it
  is never injected into a Gemini prompt (unlike the original todos/013 bug this risk is explicitly
  compared against), so the blast radius of a stale fragment winning is "a human reads a slightly
  outdated role description," not "every future extraction gets contaminated with a wrong fact."
- No concrete instance of this actually causing a wrong `note` has been found yet — the todo itself
  frames this as a risk ("risks the same... problem"), not a confirmed defect, unlike todos/028's
  `Molly. Schen`, which is a verified, named, live case.
- Tagging every fragment with a date is real schema complexity (every `context`-accumulation site,
  plus `_primary_note`'s selection logic, plus anything that ever reads `context` downstream) for a
  problem that may never materialize at this corpus's size (~99 meetings spanning a few years;
  board-member role changes mid-tenure are the cited example and are rare events).

**Revisit this decision** if a concrete context-staleness defect is ever found (the same way
todos/028 escalated `Molly. Schen` from a theoretical risk to a fix) — don't pre-build the fix for
a risk that hasn't materialized, consistent with this project's preference for root-causing real
defects over speculative cleanup tooling.

## Implementation steps (for whoever picks this up)

**Step 1 — `scripts/terms_registry.py`:**
- Entity creation (`_ingest_document_terms_locked`, ~line 205-209): `'sources': []` →
  `'file_ids': []`.
- Append logic (~line 214-217): replace the `source` dict + membership check with:
  ```python
  if file_id not in existing['file_ids']:
      existing['file_ids'].append(file_id)
      mutated = True
  ```
  (Drop the now-unused `meeting`/`doc_type` capture entirely — `meeting_slug`/`doc_type` are still
  used elsewhere in the same function for `first_seen`/`last_seen`, just no longer bundled into a
  per-source record.)
- No other in-module reads of `sources` exist (verified above) — these are the only two sites.

**Step 2 — `scripts/test_terms_registry.py`:** mechanical rename, `entity["sources"]` →
`entity["file_ids"]` at all 8 occurrences (lines 19, 31, 43, 80, 116, 126, 128, 159, 172 as of this
writing), including the two fixture literals (`"sources": []` → `"file_ids": []`). Assertions on
`len(...)` stay structurally the same since `file_ids` is still a list.

**Step 3 — one-time live migration**, mirroring the backup discipline established (the hard way)
during todos/021:
1. Download the live `terms_registry.json`.
2. Back it up to a **uniquely timestamped** path (e.g. a run-id or `int(time.time())` embedded in
   the filename) in the scratchpad — never a fixed, reusable path. Todos/021's fork correctly wrote
   a pre-merge backup, then accidentally clobbered that exact file by re-running its verification
   script a second time against the same hardcoded output path. Do not repeat that.
3. For each entity: `file_ids = sorted({s['file_id'] for s in entity.get('sources', []) if s.get('file_id')})`;
   set `entity['file_ids'] = file_ids`; delete the `sources` key.
4. Write back to GCS; round-trip-verify by re-reading and spot-checking a sample of entities'
   `len(file_ids)` against their pre-migration `len(sources)` (should match 1:1 — each `sources`
   entry has a unique `file_id` by construction, so no count should shrink from dedup).
5. Delete the one-time migration script once verified, following this project's existing
   disposable-migration-script convention (see todos 006/007 — delete rather than leave a
   "COMPLETED, do not re-run" script lying around in `scripts/`), noting the migration in the
   commit message.

**Step 4 — `docs/pipeline.md`:** the schema example at lines 162-176 shows a live `"sources": [...]`
block — update to `"file_ids": ["1aOu6...", "9xQp2..."]`. The ingestion-matching-chain mermaid
diagram's "merge: append source" label (line 205) should become "merge: append file_id". The
known-limitations bullet for this todo (lines 364-367, "`sources`/`context` have no retention or
temporal-dedup strategy") should be updated to reflect what shipped (file_ids fixes the footprint
question; context is explicitly deferred, not fixed) and the `[todos/022]` link updated once the
todo file is renamed to `-complete-`. While there, the adjacent bullets for todos/021 (line
360-363) and todos/028 (line 371-377) are already stale pending-links as of this writing (021
shipped, 028 in flight) — worth a combined sweep of that whole limitations list rather than
touching only the 022 line in isolation, but that's a docs-hygiene pass, not blocking for this
todo.

## Effort & risk

- **Code change (Step 1-2):** trivial — one data-shape swap at a single append site, mechanical
  test renames. Effort: small. Risk: low (behavior-preserving; idempotency semantics unchanged,
  just a smaller record per check).
- **Live migration (Step 3):** the only real risk in this plan, and it's operational (backup
  discipline), not logical — the transform itself is a pure projection (`sources` → distinct
  `file_id`s) with no ambiguity or merge logic to get wrong. Effort: small. Risk: low if the backup
  discipline above is followed; medium if it isn't (see todos/021's own disclosed mistake).
- **Docs (Step 4):** trivial, no risk.

## Sequencing relative to other open/in-flight work

- **todos/021 (case-insensitive registry keys) — already shipped.** No interaction with this
  plan's schema change; unrelated code path (`_find_case_insensitive_key`, the lookup-key logic,
  not `sources`/`context`).
- **todos/028 (registry never revises bad first-seen `term`) — in flight as of this writing.**
  See Decision 1's "conditional on todos/028's outcome" note above: check what 028 actually lands
  as before implementing Step 1, specifically whether it needed to start tracking a raw term string
  per source/observation. If it did, use the `{file_id, term}` variant of `file_ids`; if not, the
  bare-string form is correct.
- **todos/026 (dedicated term extraction + derived registry rebuild) — pending, bigger
  architectural scope.** This is the one with real sequencing weight: 026's stated goal is making
  the registry **derived/rebuildable** rather than accumulate-only (re-extracting fresh each run
  instead of incrementally mutating a persisted blob across runs forever). If 026 lands as
  described, the entire reason `sources`/`file_ids` needs to exist — guarding against
  re-appending duplicates across *incremental* runs — may become moot: a registry that's rebuilt
  fresh each run from the current document set doesn't need a per-entity "have I seen this file
  before" ledger at all, because there's no accumulation to guard against in the first place.
  **This plan's Step 1 is cheap and safe to land independently right now** (it's a net win
  regardless of 026's eventual shape, and easy to delete later if 026 obsoletes it entirely), but
  if 026 is imminent, consider deferring Step 3 (the live migration) specifically, to avoid
  migrating live data twice in quick succession. This is a judgment call for whoever sequences the
  two todos, not resolved here — flagging it rather than picking an order.
  Decision 2 and Decision 3 (defer `context`) are unaffected by 026 either way — a derived registry
  would still have the identical `first_seen`/`last_seen` tie-break and `context` temporal-dedup
  questions, since both are about canonical-value selection among multiple observations, not about
  incremental-vs-derived persistence.

## Acceptance criteria mapping (back to todos/022)

- "Decide `sources` → `file_ids`-only... implement and backfill-migrate" — **decided** (this doc,
  Decision 1); implementation is Steps 1-3 above, not yet executed.
- "Decide whether `first_seen`/`last_seen` carry a `file_id`..." — **decided: no** (Decision 2);
  no implementation needed, status quo is already correct.
- "Decide whether `context`'s temporal-blindness risk is worth addressing now or deferred" —
  **decided: deferred** (Decision 3), with the stated reason (milder than todos/013, no confirmed
  defect yet, real schema-complexity cost).
