---
name: parse-meeting-date-pattern-order-bug
status: pending
priority: p3
issue_id: "015"
tags: [bug, date-parsing, drive-sourcing]
dependencies: []
---

## Problem Statement

`parse_meeting_date()` in `scripts/sourcing/drive.py` checks its ambiguous `MM.DD.YY` pattern
before its unambiguous `YYYY.MM.DD` pattern. A filename containing a 4-digit-year-first date gets
misparsed: after normalization (`-` → `.`), `"SERVICES AGREEMENT [2026-01-05].pdf"` becomes
`"...[2026.01.05]..."`; the `MM.DD.YY` regex `(\d{1,2})\.(\d{1,2})\.(\d{2,4})` greedily matches
`"26.01.05"` (reading the year's last two digits as the month), producing the invalid date
`2005-26-01` (month=26) instead of the correct `2026-01-05`.

## Findings

- **File**: `scripts/sourcing/drive.py`, `parse_meeting_date()` (~line 122)
- **Root cause**: pattern order — the `MM.DD.YY` branch is tried before the `YYYY.MM.DD` branch,
  even though a 4-digit leading group unambiguously signals year-first format and should take
  priority.
- **Reproduction**: `parse_meeting_date('SERVICES AGREEMENT [2026-01-05].pdf')` → `'2005-26-01'`
  (should be `'2026-01-05'`).
- **Impact today**: harmless in production — `CUTOFF_DATE` filtering (`slug < CUTOFF_DATE` in
  `source_data.py`) happens to remove this specific case, since `"2005-26-01"` sorts before
  `"2023-08-01"` lexicographically. That's incidental, not by design: a different MM.DD.YY-shaped
  misparse could just as easily sort *after* cutoff and get silently attached to the wrong meeting.
- **`extract_date_from_content()`** (same file) is *not* affected by this specific bug — it picks
  whichever pattern's match starts earliest in the text, and the correct `YYYY.MM.DD` match always
  starts 2 characters before the erroneous `MM.DD.YY` match would, so it already wins. Only the
  filename parser's fixed try-in-order structure is vulnerable.
- **Discovered**: 2026-08-18, verifying the `drive_catalog.json` rebuild after the Drive
  sourcing-scope/content-eligibility fixes — this was one of 3 remaining pre-cutoff catalog
  entries; the other 2 were legitimate old documents correctly in scope for sourcing.
- No unit test currently guards `parse_meeting_date` against 4-digit-year-first inputs.

## Proposed Solutions

### Option A: Reorder + validate (Recommended)

- Move the `YYYY.MM.DD` pattern check before `MM.DD.YY` in `parse_meeting_date()`, since a 4-digit
  leading group is unambiguous and should never be reinterpreted.
- Add month/day range validation (1-12 / 1-31) to the `MM.DD.YY` branch, matching the validation
  the `YYYYMMDD` branch already has — reject and fall through to the next pattern on failure. This
  guards against any other similarly-ambiguous string, not just this one instance.

**Pros**: Fixes the root cause and adds defense-in-depth; small, localized change.
**Cons**: None significant.
**Effort**: Small.
**Risk**: Low — reordering two independent pattern checks; add a regression test with the exact
reproduction case plus a spot-check of known-good filenames from the real corpus.

### Option B: Validate only, don't reorder

Add month-range validation to `MM.DD.YY` without changing pattern order.

**Pros**: Smaller diff.
**Cons**: Doesn't fully solve it — a coincidentally-valid-looking wrong month (≤12) from a
misinterpreted year could still slip through.
**Effort**: Trivial.
**Risk**: Medium — partial fix, same class of bug can resurface.

### Option C: Leave as-is

Accept the current (incidental, not designed) harmlessness.

**Effort**: None.
**Risk**: Low today, but fragile — depends on misparsed dates always happening to sort before
`CUTOFF_DATE`, which isn't guaranteed for future filenames.

## Acceptance Criteria
- [ ] `parse_meeting_date('SERVICES AGREEMENT [2026-01-05].pdf')` returns `'2026-01-05'`
- [ ] `YYYY.MM.DD` / `YYYY-MM-DD` formatted dates parse correctly regardless of surrounding
      `MM.DD.YY`-shaped substrings
- [ ] The `MM.DD.YY` branch validates month (1-12) and day (1-31), rejecting and continuing to the
      next pattern on failure
- [ ] A sample of already-correctly-parsed filenames from the real Drive corpus remains unaffected

## Work Log
- 2026-08-18: Identified while verifying the `drive_catalog.json` rebuild after the Drive
  sourcing-scope/content-eligibility fixes. Currently harmless (filtered by `CUTOFF_DATE`
  downstream) but not by design. Deferred to keep scope tight on the catalog redesign work; logged
  here per user request.
