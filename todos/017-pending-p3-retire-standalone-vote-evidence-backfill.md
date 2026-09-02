---
name: retire-standalone-vote-evidence-backfill
status: pending
priority: p3
issue_id: "017"
tags: [pipeline, topics, cleanup]
dependencies: []
---

## Problem Statement

`generate_vote_evidence()` (`scripts/post_process.py`) used to be the only way a meeting got its
`vote_evidence` field: a separate LLM call, run for every meeting with `votes:` and `topics:` but
no `vote_evidence:` yet, right after topic tagging. This session folded that association directly
into topic tagging itself (`generate_tags()` for the incremental path, `batch_tag_all_meetings()`
for `--retag`) — one fewer LLM round-trip per meeting on the daily path, and no more unparallelized
per-meeting loop after a full-corpus `--retag`.

`generate_vote_evidence()` and its driver step ("3b. Backfill vote-topic evidence...") are kept
only to backfill meetings that were tagged during the (short) window before this merge existed —
i.e. any meeting with `topics` but no `vote_evidence` key at all. Once every such meeting has been
backfilled once, this function and its driver step have no remaining purpose.

## Findings

- **Scope of what needs backfilling**: meetings with `topics` set but no `vote_evidence` key in
  their `.njk` front matter at all (checked by key presence — `'vote_evidence' not in m` — not
  truthiness, since an empty `{}` is a legitimate "processed, no matches" result, not "not yet
  processed"; see the correctness fix below).
- **Corpus scan at merge time (2026-09-02) found only 2 meetings missing the key** (`2026-02-04`,
  `2026-03-02`) — and both also have `votes: []`, so the driver's *existing*, unchanged
  `m.get('votes')` gate already excludes them from `vote_evidence_targets` regardless of the key
  check. In other words: as of this merge, the backfill step has **zero actual targets** and is
  already a no-op in practice. It's kept only because a future meeting could theoretically still
  reach this state (e.g. manual `.njk` edits, or a meeting processed by an older pipeline version
  before being deployed to this commit) — but there's a real chance this todo can just be closed
  as already-satisfied rather than requiring an active backfill run. Re-run the corpus scan below
  before doing anything else.
- **Adjacent bug found and fixed in the same change**: the driver's filter previously used
  `not m.get('vote_evidence')`, which treats an empty dict `{}` as falsy. 16 meetings in the
  corpus already have `vote_evidence: {}` (a genuinely correct result — none of that meeting's
  votes matched any of its topics), and were being **re-selected and re-sent to the LLM on every
  single `post_process()` run**, indefinitely, before this fix. Switched to a presence check.
- **Not yet run**: the actual backfill for pre-merge meetings hasn't been executed as part of this
  change — the merge only stops new backfill-worthy meetings from accumulating going forward
  (every meeting tagged from now on gets `vote_evidence` written directly, so it never needs the
  standalone call at all). The existing backfill step in `post_process()` will pick up any
  remaining legacy meetings automatically on its next run; no manual action needed unless someone
  wants to confirm it happened.

## Proposed Solution

Once a `post_process()` run (daily cron or manual) has confirmed zero meetings hit the
`vote_evidence_targets` backfill step — i.e. every meeting with `topics` also has a `vote_evidence`
key, even if empty — delete:
- `generate_vote_evidence()` and its `VoteTopicEvidence` Pydantic schema in `scripts/post_process.py`
- The "3b. Backfill vote-topic evidence..." step in `post_process()`
- §10 of `docs/prompts.md` (Vote-Topic Evidence — legacy backfill only)
- The corresponding row (#7) in `docs/pipeline.md`'s LLM Calls table

**Effort**: Small — mostly deletion, once the backfill is confirmed complete.
**Risk**: Low. Confirm via a scan before deleting, so this isn't removed while a legacy meeting
still needs it — either watch the "Backfilling vote-topic evidence for legacy meetings..." log
line in `processing_log.json` / CI output show zero targets, or re-run:

```python
import yaml, re, os
for filename in sorted(os.listdir('src/meetings/')):
    if not filename.endswith('.njk'): continue
    content = open(f'src/meetings/{filename}').read()
    m = re.match(r'^---\s*\n(.*?)\n---\s*(?:\n|$)', content, re.DOTALL)
    if not m: continue
    data = yaml.safe_load(m.group(1)) or {}
    if data.get('topics') and data.get('votes') and 'vote_evidence' not in data:
        print(filename)  # each printed line is a real, still-pending backfill target
```

## Acceptance Criteria

- [ ] Confirmed (via log or corpus scan) that no meeting has `topics` without a `vote_evidence` key
- [ ] `generate_vote_evidence()`, `VoteTopicEvidence`, and the 3b backfill step removed
- [ ] `docs/prompts.md` §10 and `docs/pipeline.md`'s #7 row removed, with `pipeline.md`'s call
  numbering closed up (or explicitly left with a gap and a note, whichever this repo's convention
  turns out to be by the time this is picked up)

## Work Log

- 2026-09-02: Vote-evidence association merged into `generate_tags()` / `batch_tag_all_meetings()`
  (see commit history around this date). `generate_vote_evidence()` kept as backfill-only per this
  todo. Adjacent truthiness-vs-presence bug (16 meetings re-processed every run) found and fixed
  in the same change.
