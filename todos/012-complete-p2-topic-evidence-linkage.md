---
name: topic-evidence-linkage
status: complete
priority: p2
issue_id: "012"
tags: [pipeline, topics, data-quality]
dependencies: []
---

## Problem Statement

`synthesize_topic()` (`post_process.py`) needs to know which summary bullets across the corpus are "evidence" for a given topic tag, so it can build the chronological narrative fed to Gemini. Today that's done by `_evidence_match(topic, bullet_topic, bullet_text)` — a fuzzy heuristic (FY-normalized substring match, then 2-word overlap with the bullet's own `topic` label, then 3-word overlap with the bullet `text`). It has no ground truth to work from: bullets are written by `process_transcripts.py` *before* tagging happens in `post_process.py`, so a bullet's own `topic` label was never chosen with the eventual tag name in mind, and the two frequently diverge in wording.

After the 2026-08 batch-tagging rework (`batch_tag_all_meetings()`), this gap got worse: batch-generated tag names are more abstract/normalized (e.g. `Financial Health`, `Staffing Reductions`) than the per-meeting bullet topics they're meant to summarize, so `_evidence_match()` finds nothing for a meaningful fraction of tags. In the most recent full retag, 22 of 96 topics ended up with no synthesized summary — 21 of those from evidence-matching misses, not errors.

## Findings

- **Affected code**: `_evidence_match()`, `synthesize_topic()`, and the `topic_meetings` inverted-index build in `post_process()` (`scripts/post_process.py`)
- Considered and rejected: rewriting each bullet's `topic` label to match its meeting's final tag after tagging runs. This is a slippery slope — every future retag, merge, or prompt change would need to cascade into rewriting bullets too, permanently coupling two things that don't need to be coupled, and bullets often carry legitimately more specific framing than the broader tag covering them.
- **Source**: raised during topic-identification iteration work, 2026-08-06 (see `docs/prompts.md` §2–3 for the tagging prompts this interacts with)

## Proposed Solutions

### Option A: Tagging call reports its own evidence (Recommended)

Extend `generate_tags()` and `batch_tag_all_meetings()`'s response schema so each tag comes back paired with which bullet(s) support it (e.g. bullet index, or a short verbatim quote) — the model already reads the full bullet list to decide tags, so this adds no extra Gemini calls, just a richer response shape. Store the linkage alongside `topics:` in each meeting's frontmatter. `synthesize_topic()` reads this directly instead of calling `_evidence_match()`.

**Pros**: Solves the problem at the source instead of reverse-engineering it after the fact — consistent with how this session approached the tagging-quality issues generally. No new Gemini calls.
**Cons**: Schema change to both tagging paths; needs a fallback (`_evidence_match()` stays as a legacy path) for already-tagged meetings that predate the new field, until they're re-tagged.
**Effort**: Medium
**Risk**: Low — additive change, old data keeps working via the existing fuzzy fallback.

### Option B: Improve `_evidence_match()` heuristics

Tune the word-overlap thresholds, add stemming/lemmatization, or weight FY-prefix matches more heavily.

**Pros**: No schema change, isolated to one function.
**Cons**: Still fundamentally guessing after the fact; batch-mode's more abstract tag naming makes this harder to tune reliably, and it doesn't get better as the tagging prompts keep evolving — it needs re-tuning against whatever the tags currently look like.
**Effort**: Small
**Risk**: Medium — easy to overfit to the current corpus's naming patterns.

## Acceptance Criteria

- [x] Every tag generated going forward (single-meeting and batch) has an explicit evidence link, not a fuzzy-matched one
- [x] `synthesize_topic()` no longer produces "no evidence" gaps for newly-tagged meetings
- [x] Legacy (already-tagged) meetings still synthesize correctly via the existing fuzzy fallback until re-tagged

## Resolution

Implemented Option A. `generate_tags()` and `batch_tag_all_meetings()` now render each meeting's summary bullets with 0-indexed numbers and ask the model to cite `evidence_bullets` per tag in the same response — no extra Gemini calls. Stored as a new `topic_evidence: {tag: [bullet_index, ...]}` frontmatter field alongside `topics:`. `synthesize_topic()`'s evidence-gathering step reads `topic_evidence` directly when present, falling back to `_evidence_match()` only for meetings tagged before this field existed. Validated via `--dry-run-tag` against real meetings (e.g. `2026-06-04`: `Kaler Elementary` → bullets `[0, 1]`, matching the actual Kaler-specific bullets exactly) before running against the full corpus.

## Work Log

- 2026-08-06: Identified during topic-identification batch-tagging iteration; 22/96 topics missing summaries after a full retag, 21 from evidence-matching misses.
- 2026-08-06: Implemented and validated (Option A). See Resolution above.
