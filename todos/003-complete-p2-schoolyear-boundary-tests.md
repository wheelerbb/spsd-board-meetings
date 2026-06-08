---
name: schoolyear-boundary-tests
status: pending
priority: p2
issue_id: "003"
tags: [code-review, testing, correctness]
dependencies: [002]
---

## Problem Statement

`schoolYear()` in `src/_data/meetings.js` has a boundary at month `>= 8` (August). The source code comment explicitly documents that `sync_drive.py` used `>= 7` (a known off-by-one), that July meetings must group under the prior school year, and that `2026-07-13` is an anomalous entry. None of these cases have an automated test. A one-character typo changing `>= 8` to `>= 7` would silently misclassify every August meeting, and a reader modifying the logic has no test to lean on.

## Findings

- **File**: `src/_data/meetings.js` lines 12–16
- **Documented edge case**: July belongs to prior school year (e.g., `2025-07-30 → 2024-2025`)
- **August boundary**: First month of new school year — no test covers this
- **Anomaly**: `2026-07-13` was `2026-2027` in old `meetings.json` (sync_drive bug); now correctly `2025-2026`
- **`schoolYear` is not currently exported** — needs extraction or export to be testable
- **Source**: testing-reviewer TEST-002

## Proposed Solutions

### Option A: Export schoolYear alongside the main function (Recommended)
Add `module.exports.schoolYear = schoolYear;` at the bottom of `meetings.js` (or use `Object.assign`). Alternatively, extract to the same `src/_lib/` module created for meetingNav (see todo [[meetingnav-test-copies-not-imports]]).

**Test cases to add to `test/meetingNav.test.js` or a new `test/meetings.test.js`**:
```js
const { schoolYear } = require('../src/_data/meetings');

assert.strictEqual(schoolYear('2024-08-19'), '2024-2025', 'August starts new year');
assert.strictEqual(schoolYear('2024-07-08'), '2023-2024', 'July is prior year');
assert.strictEqual(schoolYear('2025-07-01'), '2024-2025', 'July 2025 is prior year');
assert.strictEqual(schoolYear('2025-07-30'), '2024-2025', 'July 2025 is prior year');
assert.strictEqual(schoolYear('2026-07-13'), '2025-2026', 'anomalous July 2026 now uses prior year');
assert.strictEqual(schoolYear('2024-09-09'), '2024-2025', 'September is new year');
assert.strictEqual(schoolYear('2025-06-10'), '2024-2025', 'June stays in same year');
assert.strictEqual(schoolYear('2025-01-01'), '2024-2025', 'January stays in same year');
```

**Pros**: Locks in all boundary behavior; documents the anomaly as a test  
**Cons**: Requires exporting a helper  
**Effort**: Small  
**Risk**: Low

## Acceptance Criteria
- [ ] `schoolYear` is importable from its module
- [ ] Tests cover: August (starts new year), July (prior year), September, January, the anomalous `2026-07-13`
- [ ] All assertions pass with current implementation

## Work Log
- 2026-06-08: Identified by testing-reviewer (TEST-002) in ce:review of nav/data-derivation refactor
