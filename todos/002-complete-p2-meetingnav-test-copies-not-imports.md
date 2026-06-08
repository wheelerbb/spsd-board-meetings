---
name: meetingnav-test-copies-not-imports
status: pending
priority: p2
issue_id: "002"
tags: [code-review, testing, architecture]
dependencies: []
---

## Problem Statement

`test/meetingNav.test.js` opens with "Replicate the filter logic from .eleventy.js" and then copies the function body verbatim. The production filter registered with Eleventy is an anonymous arrow function that is never imported by the test. If the production implementation changes (renamed property, changed null check, different return shape), every test assertion continues to pass while production breaks. The test provides zero regression protection for the actual deployed code path.

## Findings

- **File**: `test/meetingNav.test.js` lines 4–16
- **Root cause**: `eleventyConfig.addFilter()` wraps the function in Eleventy internals, making it hard to test in isolation without extracting it first
- **Impact**: The only test in the project guards against changes to a copy, not the real code
- **Related finding**: maintainability-reviewer M07, testing-reviewer TEST-001

## Proposed Solutions

### Option A: Extract meetingNav to a lib module (Recommended)
Create `src/_lib/meetingNav.js` that exports the raw function. `.eleventy.js` imports and registers it; the test imports and tests it directly.

```js
// src/_lib/meetingNav.js
'use strict';
module.exports = function meetingNav(meetings, currentSlug) {
  const idx = meetings.findIndex((m) => m.slug === currentSlug);
  if (idx === -1) return { prev: null, next: null };
  return {
    prev: idx > 0 ? { slug: meetings[idx-1].slug, label: meetings[idx-1].display_date } : null,
    next: idx < meetings.length - 1 ? { slug: meetings[idx+1].slug, label: meetings[idx+1].display_date } : null,
  };
};

// .eleventy.js change:
const meetingNav = require('./src/_lib/meetingNav');
eleventyConfig.addFilter('meetingNav', meetingNav);

// test/meetingNav.test.js change:
const meetingNav = require('../src/_lib/meetingNav');
// ... existing assertions unchanged
```

**Pros**: Test imports real code; ~5 lines changed; existing assertions are reused unchanged  
**Cons**: New file  
**Effort**: Small  
**Risk**: Low

### Option B: Export the function from .eleventy.js
Add a named export from `.eleventy.js` for the filter function.

**Pros**: No new file  
**Cons**: `.eleventy.js` is a config file, not a library — exporting from it is unconventional  
**Effort**: Small  
**Risk**: Low

## Acceptance Criteria
- [ ] `test/meetingNav.test.js` imports the function from its source module, not a copy
- [ ] All 10 existing assertions still pass
- [ ] `npm test` (or `node test/meetingNav.test.js`) exits 0

## Work Log
- 2026-06-08: Identified by maintainability-reviewer (M07) and testing-reviewer (TEST-001) in ce:review
