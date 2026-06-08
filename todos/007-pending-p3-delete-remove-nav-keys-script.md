---
name: delete-remove-nav-keys-script
status: pending
priority: p3
issue_id: "007"
tags: [code-review, dead-code, cleanup]
dependencies: []
---

## Problem Statement

`scripts/remove_nav_keys.py` is a one-time migration script that removed `prev:`/`next:` YAML keys from 84 meeting `.njk` files. The migration ran as part of commit `459d203`. The script has no "COMPLETED" status marker — its docstring only says "Run: python scripts/remove_nav_keys.py" with no indication it has already been run.

The script is idempotent (safe to re-run on a clean corpus) but causes unnecessary churn: re-running it re-serializes every YAML front matter block, potentially causing cosmetic key-ordering diffs. A developer seeing a `run:` command in a script's header is likely to run it.

## Findings

- **File**: `scripts/remove_nav_keys.py` (34 lines)
- **Status**: Migration complete — `grep -r "^prev:" src/meetings/` returns 0
- **Re-run risk**: Idempotent but causes cosmetic YAML round-trip diffs
- **Source**: maintainability-reviewer M02

## Proposed Solution

Delete the file. Add a one-line note to AGENTS.md confirming prev/next keys were removed by migration in commit `459d203`.

Alternatively, if keeping the script is preferred, add a banner:
```python
# STATUS: COMPLETED — ran 2026-06-08 (commit 459d203). Do not re-run.
```

**Effort**: Trivial  
**Risk**: Zero

## Acceptance Criteria
- [ ] `scripts/remove_nav_keys.py` is either deleted or has a clear COMPLETED status banner
- [ ] No developer can accidentally trigger an unnecessary YAML round-trip by running a script they found in `scripts/`

## Work Log
- 2026-06-08: Identified by maintainability-reviewer (M02) in ce:review
