---
name: memoize-getmeetings
status: pending
priority: p3
issue_id: "009"
tags: [code-review, performance, simplicity]
dependencies: []
---

## Problem Statement

`src/_data/meetings.js` exports a function. Eleventy calls it once for the `meetings` global. `src/_data/years.js` also calls `getMeetings()` independently for the `years` global. Because the module exports a function (not a cached result), both calls execute `fs.readdirSync` + `matter.read()` on all 112 `.njk` files — 224 file reads per build instead of 112.

Both reads happen synchronously during Eleventy's data cascade, so they are always identical. This is a performance issue only (no correctness impact), but it is avoidable with a two-line memoization.

## Findings

- **File**: `src/_data/meetings.js`
- **Double-read confirmed**: `years.js` calls `getMeetings()` independently; Node module cache preserves the *function* but not its *return value*
- **Current cost**: ~19ms cold / ~3.5ms warm for 112 files (from plan benchmarks) × 2 = ~38ms cold per build
- **Source**: code-simplicity-reviewer, correctness-reviewer residual risk

## Proposed Solution

Add a module-level cache variable:

```js
// src/_data/meetings.js
let _cache;
function buildMeetings() {
  const dir = path.join(__dirname, '../meetings');
  return fs.readdirSync(dir)
    // ... existing implementation unchanged ...
    .sort((a, b) => b.date.localeCompare(a.date));
}
module.exports = function getMeetings() {
  if (!_cache) _cache = buildMeetings();
  return _cache;
};
```

**Note**: The cache is process-scoped. In Eleventy watch mode, the module is re-required on rebuild, clearing `_cache` automatically. No stale-cache risk.

**Effort**: Small (5 lines changed)  
**Risk**: Low

## Acceptance Criteria
- [ ] `getMeetings()` reads files only once per Eleventy build process
- [ ] `npm run build` produces identical output before and after
- [ ] Watch mode (`npm start`) correctly rebuilds when `.njk` files change

## Work Log
- 2026-06-08: Identified by code-simplicity-reviewer in ce:review
