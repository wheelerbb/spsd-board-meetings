---
name: meetingtype-regression-tests
status: pending
priority: p2
issue_id: "004"
tags: [code-review, testing, correctness]
dependencies: [003]
---

## Problem Statement

`meetingType()` in `src/_data/meetings.js` has five `startsWith()` branches plus a null guard and a default fallback. The implementation plan explicitly named the "Exec. Session" return value as a critical bug fix (the original version returned `"Exec."` for executive session meetings). No test locks in this fix. A future refactor that changes the return string or adjusts the prefix check would not be caught.

## Findings

- **File**: `src/_data/meetings.js` lines 24–33
- **Named bug fix**: `meetingType("Exec. Session · April 2026")` must return `"Exec. Session"`, not `"Exec."`
- **Untested branches**: special, workshop, emergency, inauguration, null/falsy input, default fallback
- **`meetingType` is not currently exported** — same extraction needed as `schoolYear` (see [[schoolyear-boundary-tests]])
- **Source**: testing-reviewer TEST-003, correctness-reviewer finding on meetingType

## Proposed Solutions

### Option A: Export meetingType and add to test file
Export `meetingType` from `meetings.js` alongside `schoolYear`. Add assertions to the test suite.

**Test cases**:
```js
const { meetingType } = require('../src/_data/meetings');

// Bug-fix regression lock
assert.strictEqual(meetingType('Exec. Session · April 2026'), 'Exec. Session');
assert.strictEqual(meetingType('exec. session · april 2026'), 'Exec. Session', 'case-insensitive');

// All named types
assert.strictEqual(meetingType('Special Meeting · March 2026'), 'Special');
assert.strictEqual(meetingType('Workshop · March 2026'), 'Workshop');
assert.strictEqual(meetingType('Emergency Meeting'), 'Emergency');
assert.strictEqual(meetingType('Inauguration Ceremony'), 'Inauguration');

// Default fallback
assert.strictEqual(meetingType('Regular Meeting · May 2026'), 'Regular');
assert.strictEqual(meetingType('Regular Board Meeting'), 'Regular');

// Null/falsy guard
assert.strictEqual(meetingType(null), 'Regular');
assert.strictEqual(meetingType(undefined), 'Regular');
assert.strictEqual(meetingType(''), 'Regular');

// Leading whitespace (trimStart is applied)
assert.strictEqual(meetingType('  Exec. Session · April 2026'), 'Exec. Session', 'leading whitespace');
```

**Pros**: All branches covered; Exec. Session fix locked in  
**Cons**: Requires exporting helper  
**Effort**: Small  
**Risk**: Low

## Acceptance Criteria
- [ ] `meetingType` is importable from its module
- [ ] All five type branches have at least one passing assertion
- [ ] `meetingType('Exec. Session · April 2026')` === `'Exec. Session'` is explicitly asserted
- [ ] Null/falsy input produces `'Regular'`

## Work Log
- 2026-06-08: Identified by testing-reviewer (TEST-003) in ce:review of nav/data-derivation refactor
