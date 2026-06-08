---
name: topic-blacklist-duplication
status: pending
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
- [ ] TOPIC_BLACKLIST has a single authoritative source
- [ ] Both `meetings.js` and `post_process.py` read from that source at runtime
- [ ] Adding a new topic to the blacklist requires changing exactly one file

## Work Log
- 2026-06-08: Identified by maintainability-reviewer (M03) in ce:review of nav/data-derivation refactor
