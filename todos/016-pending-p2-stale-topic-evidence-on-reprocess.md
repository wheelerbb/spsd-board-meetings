---
name: stale-topic-evidence-on-reprocess
status: pending
priority: p2
issue_id: "016"
tags: [pipeline, topics, data-quality]
dependencies: []
---

## Problem Statement

`process_transcripts.py::process_single_meeting()` can silently corrupt a meeting's `topic_evidence` (and, since this session, `vote_evidence`) whenever it reprocesses a meeting that was already tagged. Both fields are `{tag: [index, ...]}` maps into that meeting's `summary`/`votes` lists — position-based references. When `--force`/`reprocess_all` (or a `SCRIPT_VERSION` bump) causes an already-tagged meeting's `summary`/`votes` to be regenerated from scratch, the bullet/vote order and count can change, but nothing clears the old `topic_evidence`/`vote_evidence` alongside it — so the indices silently point at different (or nonexistent) content after the rewrite.

This isn't hypothetical: it already happened. A manual full reprocess on 2026-08-20 (`56e10b8`, triggered by a `SCRIPT_VERSION` bump) regenerated `summary`/`votes` for 56 meetings. None had their `topic_evidence` cleared. A direct scan this session found **at least 14 of those 56 meetings have detectably out-of-range `topic_evidence` indices** (i.e. an index ≥ the new, shorter `summary` list length), spanning at least 10 different topics: FY25/26/27 Budget, SPHS Athletic Complex, DEI Coordinator Resignation, Elementary School Reconfiguration, Kaler Elementary School Closure, and others. The true footprint is almost certainly larger — an index that's still *in range* but now points at the wrong bullet (confirmed for `2026-04-29.njk`: index 5 shifted from a reconfiguration-delay bullet to an unrelated "Public Criticism of Superintendent Search" bullet) can't be detected by bounds-checking alone, only by manual/semantic review.

This was discovered while investigating why the "Elementary Reconfiguration Steering Committee" — the headline phrase in that topic's `current_status`/`overview` through 2026-08-20 15:48 UTC — dropped out after the same day's later reprocess (18:32 UTC). The evidence corruption on `2026-04-29.njk` (one relevant bullet dropped, one irrelevant bullet injected) is a plausible contributing factor, though the wording drop itself also has a stochastic-LLM-synthesis component independent of this bug — see Work Log.

## Findings

- **Root cause location**: `scripts/process_transcripts.py::process_single_meeting()`, ~line 329-345. `data = yaml.safe_load(fm_text)` loads the meeting's *existing* front matter (including `topics`/`topic_evidence`/`vote_evidence` if already tagged), then `data.update(report_data)` (line 345) overwrites `votes`/`summary`/`timeline` with freshly-extracted content — but nothing pops the now-potentially-misaligned `topics`/`topic_evidence`/`vote_evidence` keys.
- **Not routine, but not rare either**: the daily cron only reprocesses new stubs (`stub: true`), never touches already-tagged meetings. The corruption path requires a deliberate `--force`/`reprocess_all` run or a `SCRIPT_VERSION` bump — but `deploy.yml` exposes `reprocess_all` as a plain `workflow_dispatch` checkbox, so it's one click away from happening again, and will recur identically for every future full-corpus reprocess until fixed.
- **`vote_evidence` (added 2026-08-25, this session) is not implicated in the known 2026-08-20 incident** — it didn't exist yet — but is exposed to the exact same bug going forward: any future `--force` reprocess of a meeting that already has `vote_evidence` will leave it stale the same way.
- **Detection is asymmetric**: out-of-range indices are trivially greppable (a one-off scan already did this for the known incident). In-range-but-wrong indices are not — there's no cheap way to confirm the full corruption footprint without either a semantic re-check against each meeting's actual content or just re-tagging everything.
- **Source**: identified during investigation of a user-reported content regression on `/topics/elementary-school-reconfiguration/`, 2026-08-25.

## Proposed Solutions

### Option A: Fix the root cause + full corpus repair (Recommended)

1. In `process_single_meeting()`, immediately after `data.update(report_data)`, pop `topics`, `topic_evidence`, and `vote_evidence` from `data` unconditionally. This makes `post_process.py`'s existing incremental logic (`if m.get('topics'): continue` for tagging; `not m.get('vote_evidence')` for vote evidence) naturally re-tag and re-link the meeting against its new content on the next `post_process.py` run — no new machinery needed there.
2. Separately (one-time, not part of the code fix), clear `topics`/`topic_evidence`/`vote_evidence` for all 56 meetings touched by `56e10b8` and re-run `post_process.py` so every affected topic's evidence — and therefore its synthesized `current_status`/`overview`/`perspectives` — gets rebuilt against correct data.

**Pros**: Fixes the bug so it can't recur; repairs the known corruption instead of leaving it latent; single authoritative fix point (clear-on-rewrite), matching the pattern already used for `--retag`'s `vote_evidence` clearing.
**Cons**: The corpus repair is a real cost — dozens of Gemini calls (re-tagging + re-linking vote evidence for 56 meetings), and will resynthesize `topic_summaries.json` for every topic any of those meetings touch (~10+ topics), which changes live, public-facing content beyond just the one topic that prompted this investigation.
**Effort**: Small (code fix) + Medium (repair run).
**Risk**: Low for the code fix. Medium for the repair run — re-tagging isn't perfectly deterministic, so topic assignments could shift slightly beyond just fixing the evidence-index bug; should be spot-checked against a few known-good meetings (e.g. `2026-03-30`, `2026-04-29`) after running.

### Option B: Targeted repair only

Fix the root cause (item 1 above), but only clear+regenerate evidence for the meetings that actually feed a topic a human has confirmed is affected (currently: `2026-04-29.njk`, `2026-07-20.njk` for Elementary School Reconfiguration), leaving the other ~8+ affected meetings' stale evidence in place until something else surfaces them.

**Pros**: Much smaller blast radius; addresses the reported symptom directly.
**Cons**: Leaves known corruption in the corpus indefinitely, affecting topic pages nobody's looked at closely yet; the 14-meeting count is a lower bound (out-of-range only), so "targeted" likely misses real problems even within the topics already touched.
**Effort**: Small.
**Risk**: Low short-term, but leaves latent incorrect public-facing content with no plan to ever find/fix the rest.

### Option C: Pipeline fix only, no backfill

Apply item 1 (clear-on-rewrite) so this can never happen again, and explicitly leave all existing corruption from the 2026-08-20 incident as-is / for a separate future decision.

**Pros**: Smallest, lowest-risk change; stops the bleeding immediately.
**Cons**: Known-corrupted evidence stays live on the public site indefinitely unless someone remembers to come back to it.
**Effort**: Trivial.
**Risk**: Low for the change itself; Medium for leaving confirmed data-quality issues unresolved on a public archive.

## Acceptance Criteria

- [ ] `process_single_meeting()` clears `topics`/`topic_evidence`/`vote_evidence` whenever it rewrites a meeting's `summary`/`votes`, so future `--force`/`reprocess_all` runs can't reintroduce this bug
- [ ] Scope of existing corruption from the 2026-08-20 incident (`56e10b8`) is at least fully enumerated (not just the 14 out-of-range meetings already found — the in-range-but-wrong footprint is still unknown)
- [ ] A decision is made and executed on how much of the existing corruption to repair (full corpus / targeted / none-yet), per whichever option above is chosen
- [ ] If any repair runs, spot-check at least the `elementary-school-reconfiguration` topic page (the one that surfaced this) to confirm evidence is now correct and the "steering committee" content question from the original investigation is resolved or explicitly understood as a separate synthesis-wording issue

## Work Log

- 2026-08-25: Identified while investigating why "steering committee" dropped out of `/topics/elementary-school-reconfiguration/`'s Current Status/Overview. Traced to `56e10b8` (2026-08-20 18:32 UTC), a `--force` mass reprocess that regenerated `summary`/`votes` for 56 meetings without invalidating their `topic_evidence`. Confirmed 14/56 meetings have out-of-range indices across ~10 topics via direct scan; confirmed at least one in-range-but-wrong case (`2026-04-29.njk` index 5). User asked to defer the repair-scope decision to focus on other work first (processing-log/model-attribution changes); this todo captures the item so it isn't lost.
