---
name: remove-redundant-date-field-and-sort
status: complete
priority: p3
issue_id: "008"
tags: [code-review, simplicity, dead-code]
dependencies: []
---

## Problem Statement

`meetings.js` returns `{ slug: slug, date: slug, ... }` — both fields are the identical YYYY-MM-DD string. The `date` field was kept for schema compatibility with the deleted `meetings.json`. It is consumed in `index.njk` to sort a pre-sorted array: `meetings | where("school_year", sy) | sort(true, false, "date")`. Since `meetings.js` already returns the array sorted descending, and `where` preserves order, the `sort(true, false, "date")` call in `index.njk` re-sorts an already-sorted filtered slice — it is a no-op in practice.

## Findings

- **Redundant field**: `src/_data/meetings.js:48` — `date: slug`
- **Comment in source**: `// duplicate of slug — exists for schema compat` (schema was deleted)
- **Redundant sort**: `src/index.njk` — `| sort(true, false, "date")` on a pre-sorted array
- **Note**: `status.njk` and `topic.njk` use `sort(true, false, "date")` on Eleventy *collection items* (file objects), not on the derived `meetings` array — those are unrelated to this issue
- **Source**: maintainability-reviewer M05, code-simplicity-reviewer

## Proposed Solution

1. Remove `date: slug,` from the object returned in `meetings.js`
2. Remove `| sort(true, false, "date")` from the relevant filter chain in `index.njk`
3. Verify `npm run build` still produces correct output

**Effort**: Small  
**Risk**: Low (the sort was a no-op; removing it changes nothing observable)

## Acceptance Criteria
- [x] `date` field removed from `meetings.js` return object
- [x] Redundant sort removed from `index.njk` — by the time of this fix the chain the todo quoted
  (`meetings | where("school_year", sy) | sort(true, false, "date")`) had moved to
  `src/meetings-list.njk:244` (the page was restructured/renamed since this todo was written);
  `src/index.njk` itself no longer references the `meetings` data array at all. Removed the sort
  from its actual current location instead.
- [x] `npm run build` succeeds and page count is unchanged
- [x] Year grouping on index page is correct

## Work Log
- 2026-06-08: Identified by maintainability-reviewer (M05) and code-simplicity-reviewer in ce:review
- 2026-11-17: Resolved. Removed `date: slug,` from `src/_data/meetings.js`'s return object and
  changed the module's own internal sort to key on `slug` instead of the now-gone `date` field
  (`b.slug.localeCompare(a.slug)`). Removed `| sort(true, false, "date")` from
  `src/meetings-list.njk:244` (confirmed via grep this was the only such sort on the derived
  `meetings` array; `status.njk` and the `topic.njk` layout's uses of the same sort syntax are on
  `collections.meetings`, Eleventy file objects, and were left untouched per the todo's own note).
  Verified: `npm run build` succeeds (173 files); `_site/meetings/index.html` shows all 4
  `data-year` groups present and meeting cards within a year still render newest-first.
