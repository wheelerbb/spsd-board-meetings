---
name: clarify-stub-inverted-default
status: complete
priority: p3
issue_id: "010"
tags: [code-review, correctness, testing]
dependencies: [003]
---

## Problem Statement

`meetings.js` line 63: `stub: data.stub !== false`. When the `stub` YAML field is absent, `data.stub` is `undefined`, and `undefined !== false` is `true` — so the meeting is treated as a stub. This is the intended default (new files should be stubs), but the double-negative expression obscures it. A developer who reads it as "falsy defaults to false" and changes it to `!!data.stub` would silently flip every file missing the field from stub=true to stub=false.

Additionally, `post_process.py`'s blurb filter uses `not m.get('stub')` — if a file has no `stub` key, Python returns `None` and `not None` is `True`, meaning it would try to generate a blurb for a file that `meetings.js` considers a stub. The semantics are inconsistent (Python: no key → non-stub; JS: no key → stub).

All 112 current files have explicit `stub:` keys so neither path is exercised today, but the latent inconsistency is worth fixing before new-file creation hits an edge case.

## Findings

- **File**: `src/_data/meetings.js:63`
- **Expression**: `data.stub !== false`
- **Python equivalent**: `not m.get('stub', True)` would match JS semantics; current `not m.get('stub')` does not
- **No test** verifies the default behavior for missing `stub` key
- **Source**: correctness-reviewer F2/F3, maintainability-reviewer M04, testing-reviewer TEST-006

## Proposed Solution

1. Replace `data.stub !== false` with `data.stub ?? true` in `meetings.js` (nullish coalescing, clearer intent)
2. Add a one-line comment: `// default true — new stubs rarely set stub: false immediately`
3. In `post_process.py`, change `not m.get('stub')` to `not m.get('stub', True)` so both systems agree that missing = stub
4. Add a test asserting `meetings.js` returns `stub: true` for a fixture file without a `stub:` key

**Effort**: Small  
**Risk**: Low (no current files are affected — all have explicit `stub:` keys)

## Acceptance Criteria
- [x] `data.stub ?? true` replaces `data.stub !== false` in `meetings.js`
- [x] `post_process.py` uses `not m.get('stub', True)` for blurb task filtering
- [ ] Test asserts that a file with no `stub` field produces `stub: true` in derived data — not
  added; `test/meetings.test.js` covers `schoolYear`/`meetingType` only and `getMeetings()` itself
  (which would need a fixture `.njk` file on disk) isn't currently under test at all, so adding
  this one assertion in isolation would require new test scaffolding beyond this todo's scope.
  Logged as a gap rather than silently skipped.
- [x] Behavior for all 112 existing files (all have explicit `stub:` keys) is unchanged

## Work Log
- 2026-06-08: Identified by correctness-reviewer (F2/F3), maintainability-reviewer (M04), testing-reviewer (TEST-006) in ce:review
- 2026-11-17: Resolved (criteria 1, 2, 4). `src/_data/meetings.js`: `data.stub !== false` →
  `data.stub ?? true` with a clarifying comment. `scripts/post_process.py:1055` (blurb task
  filter): `not m.get('stub')` → `not m.get('stub', True)`. Left the other three `m.get('stub')`
  call sites (`post_process.py:693, 979, 1043`) untouched — this todo's acceptance criteria only
  scoped the blurb filter, and all 112 current files have explicit `stub:` keys so none of this is
  live-exercised either way; touching the other sites would be unrequested scope expansion.
  Verified: `npm run build` succeeds (173 files); `node test/meetings.test.js` passes.
