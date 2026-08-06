---
name: topic-blacklist-duplication
status: complete
priority: p2
issue_id: "005"
tags: [code-review, architecture, maintainability]
dependencies: []
---

## Problem Statement

`TOPIC_BLACKLIST` is defined independently in two files in two different languages:
- `src/_data/meetings.js` line 19: `const TOPIC_BLACKLIST = new Set(['Personnel', 'Contracts', 'Finance', 'Budget']);`
- `post_process.py` line 34: `TOPIC_BLACKLIST = {'Personnel', 'Contracts', 'Finance', 'Budget'}`

The only enforcement is the comment in `meetings.js`: "matches post_process.py TOPIC_BLACKLIST". If a new sensitive topic category is added to one file but not the other, topic pages will include items that are hidden from the index (or vice versa), producing silent inconsistency. There is no test or CI check that would catch drift.

## Findings

- **Affected files**: `src/_data/meetings.js:19`, `post_process.py:34`
- **Semantic difference**: `meetings.js` filters what appears in the *public index*; `post_process.py` filters what is used for *topic synthesis and recency ordering*. They must stay in sync to avoid a topic appearing in synthesis but not on the index.
- **No automated check** verifies they match at any point
- **Source**: maintainability-reviewer M03

## Proposed Solutions

### Option A: Single source of truth in a shared JSON file (Recommended)
Create `src/_data/topic_blacklist.json` containing `["Personnel", "Contracts", "Finance", "Budget"]`.

```js
// meetings.js
const TOPIC_BLACKLIST = new Set(require('./topic_blacklist.json'));
```
```python
# post_process.py
import json
with open('src/_data/topic_blacklist.json') as f:
    TOPIC_BLACKLIST = set(json.load(f))
```

**Pros**: Single place to edit; both systems always agree; readable in context  
**Cons**: One new file (very small)  
**Effort**: Small  
**Risk**: Low

### Option B: Runtime assertion in post_process.py
Read the meetings.js file at runtime, parse the TOPIC_BLACKLIST constant with a regex, and assert it matches the Python set. Fails fast if they diverge.

**Pros**: No new data file  
**Cons**: Fragile to code formatting changes; feels like over-engineering  
**Effort**: Medium  
**Risk**: Medium

### Option C: Accept the duplication with a stronger comment
Add a test that loads both files and asserts the sets match.

**Effort**: Small  
**Risk**: Low but slightly higher than Option A

## Acceptance Criteria
- [x] TOPIC_BLACKLIST has a single authoritative source
- [x] Both `meetings.js` and `post_process.py` read from that source at runtime
- [x] Adding a new topic to the blacklist requires changing exactly one file

## Resolution

Implemented Option A as originally proposed: `src/_data/topic_blacklist.json` (`["Personnel", "Contracts", "Finance", "Budget", "Policy", "Board Governance"]` — extended with two entries during resolution, see below) is now the single source. `meetings.js` loads it via `require('./topic_blacklist.json')`; `post_process.py` loads it via `json.load()` into a module-level `TOPIC_BLACKLIST` set.

Found while resolving: by the time this was picked up, `post_process.py`'s copy hadn't just drifted from `meetings.js` — it had been dropped entirely at some point (no `TOPIC_BLACKLIST` reference existed in the Python pipeline at all). That meant blacklisted words weren't just *inconsistently* filtered, they were **not filtered from `topics.json`/page generation at all** — only hidden from one UI element (the meetings-list filter chips). A full-corpus batch retag surfaced this concretely: a bare `Finance` tag (exact blacklist match) generated a live `/topics/finance/` page. Fixed by adding `_drop_blacklisted()` in `post_process.py`, applied as a deterministic filter (not just a warning) in both tagging paths (`generate_tags()` and `batch_tag_all_meetings()`) — consistent with this session's general pattern of backstopping prompt rules in code once they've been observed to not hold reliably on their own at batch scale.

Extended the list with `Policy` and `Board Governance` (both already listed as bad examples in `_TAG_GENERIC_EXAMPLES`) after they kept recurring as bare, low-value tags in batch-mode output despite explicit prompt instructions not to use them.

## Work Log
- 2026-06-08: Identified by maintainability-reviewer (M03) in ce:review of nav/data-derivation refactor
- 2026-08-06: Resolved (Option A) during topic-identification batch-tagging iteration; see Resolution above.
