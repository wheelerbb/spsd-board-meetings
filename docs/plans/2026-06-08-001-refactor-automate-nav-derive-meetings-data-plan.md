---
title: "refactor: Automate meeting navigation and derive meetings metadata at build time"
type: refactor
status: completed
date: 2026-06-08
deepened: 2026-06-08
origin: docs/ideation/2026-06-08-simplify-data-driven-ideation.md
---

# refactor: Automate meeting navigation and derive meetings metadata at build time

## Enhancement Summary

**Deepened:** 2026-06-08
**Agents used:** correctness-reviewer, reliability-reviewer, architecture-strategist, performance-oracle, code-simplicity-reviewer, data-integrity-guardian, testing-reviewer, Context7 (Eleventy + gray-matter)

### Key Improvements Added by Research

1. **Critical bug fix in `meetingType()`** — the original implementation returns `"Exec."` (not `"Exec. Session"`) for all 6 executive session meetings, silently breaking the type badge on the index. Fixed.
2. **Atomic commit requirement** — steps 2b–2f cannot be separate commits. A GitHub Actions cron race between any two commits recreates `meetings.json` from scratch (empty) or hard-crashes `post_process.py`. The plan now specifies one atomic commit for Phase 2.
3. **Remove redundant sort from `meetingNav`** — the data file already returns a sorted array. The filter re-sorting every page render is O(n² log n) across the build (safe at n=112 but needless).
4. **Use `gray-matter.read()` instead of `matter(fs.readFileSync())`** — cleaner, same result.
5. **Alternative architecture documented** — the `eleventyComputed` + `collections.meetings` approach is more idiomatic Eleventy 3.x and avoids gray-matter entirely; documented as a research alternative.
6. **Three concrete tests added** — unit test for `meetingNav`, `eleventy --dryrun` gate, output file count assertion.
7. **Validation script extended** — add `doc_count` and `title`/`heading` fields; add guard for absent `meeting_tag`; add check for the 21 newly-visible stubs.
8. **21 new meetings will appear on first deploy** — this is correct behavior (they are valid stubs invisible due to the dual-SOT gap). Confirmed intentional.

### New Risks Discovered

- `years.js` currently does `require('./meetings.json')` — breaks build instantly if JSON is deleted before it is updated
- Python YAML round-trip in migration script can introduce cosmetic diffs (key reordering, quote style) across all 84 modified files
- `2026-07-13` in `meetings.json` has school year `2026-2027` (matches `>= 7` rule) while all other July meetings use `>= 8` — an existing inconsistency; plan uses `>= 8` (majority convention)

---

## Overview

Two interconnected refactors that eliminate the two largest sources of manual maintenance:

1. **Compute prev/next navigation at build time** — remove 228 hand-maintained YAML fields from 112 .njk files; a filter in `.eleventy.js` derives neighbors from the global meetings array at render time.
2. **Derive `meetings.json` from .njk front matter at build time** — replace the hand-maintained `meetings.json` with a JS data file that reads .njk front matter; remove the Python sync loop; establish .njk files as the single source of truth.

Phase 1 (nav) can ship independently. Phase 2 (data derivation) depends on Phase 1 being complete to simplify the migration. **Both phases combined address the root cause:** the dual source-of-truth between `.njk` front matter and `meetings.json`.

---

## Problem Statement

### The dual source-of-truth problem

Eight fields — `stub`, `topics`, `blurb`, `has_transcript`, `has_video`, `display_date`, `day_of_week`, `doc_count` — exist in both `.njk` front matter and `meetings.json`. `post_process.py` lines 129–154 exist solely to reconcile drift between them. Any edit to a `.njk` field without re-running `post_process.py` produces a stale index. The reconciliation step is ~25 lines of Python that would not exist with a single source of truth.

Additionally, 21 `.njk` files exist that have no entry in `meetings.json` — they are invisible on the index until `post_process.py` is run manually.

### The manual navigation problem

`prev:` and `next:` keys must be updated in three files for every meeting insertion. **28 of 112 meetings are already missing these keys** — they render with no navigation. At least 5 entries have inverted directions (prev/next pointing the wrong way). Every new stub added by `sync_drive.py` silently lands without navigation.

---

## Proposed Solution

### Phase 1: Compute navigation at build time

Add a `meetingNav` filter to `.eleventy.js` that takes the `meetings` global array and the current slug, and returns `{ prev, next }`. In `meeting.njk`, replace the static front matter references with a filter call. Remove all `prev:`/`next:` keys from .njk files via a migration script.

The filter receives `meetings` as a Nunjucks template variable (same pattern as the existing `where` filter) — no file-system dependency, works with both `meetings.json` and the future `meetings.js`.

### Phase 2: Derive meetings.json at build time

Replace `src/_data/meetings.json` with `src/_data/meetings.js`: a synchronous Node function that reads all `.njk` files via `gray-matter`, computes derived fields, and returns the sorted array. Update `years.js` to import the new module. Remove the reconciliation block from `post_process.py` and the `meetings.json` write paths from `sync_drive.py`. **All of these changes ship in one atomic commit** (see sequencing risk below).

---

## Technical Approach

### Phase 1 — Implementation

#### 1a. Add `meetingNav` filter to `.eleventy.js`

```js
// .eleventy.js — add inside module.exports function
eleventyConfig.addFilter("meetingNav", (meetings, currentSlug) => {
  // meetings is already sorted descending by date (slug = YYYY-MM-DD, string sort is correct).
  // Do NOT re-sort here — the data file owns sort order; re-sorting is O(n²) across the build.
  const idx = meetings.findIndex(m => m.slug === currentSlug);
  if (idx === -1) return { prev: null, next: null };
  return {
    // prev = chronologically newer (lower index in descending list)
    prev: idx > 0
      ? { slug: meetings[idx - 1].slug, label: meetings[idx - 1].display_date }
      : null,
    // next = chronologically older (higher index in descending list)
    next: idx < meetings.length - 1
      ? { slug: meetings[idx + 1].slug, label: meetings[idx + 1].display_date }
      : null
  };
  // NOTE: 'prev' means newer (right arrow) and 'next' means older (left arrow).
  // This inverted convention is intentional — it matches the existing template expectation.
  // Same-day assumption: no two meetings share the same date slug. If they ever do,
  // sort order between them is undefined (deterministic but arbitrary).
});
```

> **Research insight (performance):** The original plan included `.sort()` inside the filter. This creates O(n² log n) sort work across a build of n pages. Removed — the data file's trailing `.sort()` is the single source of order. The filter just does `findIndex` (O(n)) per call, which is O(n²) total but fast in practice (19ms for 112 pages, per benchmark).

#### 1b. Update `meeting.njk` — replace static nav with filter call

The `page` variable (including `page.fileSlug`) is available in Eleventy layout templates — not just page templates. Add these three lines immediately before the `<div class="meeting-nav">` block in `meeting.njk`:

```njk
{# Derive prev/next from sorted meetings array. 'meetings' is the global data variable. #}
{% set nav = meetings | meetingNav(page.fileSlug) %}
{% set prev = nav.prev %}
{% set next = nav.next %}
```

The existing `{% if prev %}` / `{% if next %}` rendering blocks below are unchanged.

> **Note on transition period:** If the filter is added before the migration script removes `prev`/`next` from front matter, the `{% set prev %}` statement above overrides the front matter values. There is no conflict — old front matter is harmlessly overridden by the filter output.

#### 1c. Migration script — remove prev/next keys from all .njk files

Save as `scripts/remove_nav_keys.py` (run once, then delete or keep in scripts/):

```python
# scripts/remove_nav_keys.py
import os, re, yaml

meetings_dir = 'src/meetings'
modified = 0
for fname in sorted(os.listdir(meetings_dir)):
    if not fname.endswith('.njk'):
        continue
    path = os.path.join(meetings_dir, fname)
    with open(path, 'r') as f:
        content = f.read()
    # Split on bare '---' lines. Produces [pre, front_matter, body...]
    parts = re.split(r'^---\s*$', content, flags=re.MULTILINE)
    if len(parts) < 3:
        continue  # No closing delimiter — skip (should not occur in current corpus)
    fm = yaml.safe_load(parts[1]) or {}
    if 'prev' not in fm and 'next' not in fm:
        continue  # Nothing to remove
    fm.pop('prev', None)
    fm.pop('next', None)
    new_fm = yaml.dump(fm, sort_keys=False, default_flow_style=False, allow_unicode=True)
    body = '---'.join(parts[2:])  # Rejoin any body that contained '---' lines
    with open(path, 'w') as f:
        f.write(f'---\n{new_fm}---\n{body}')
    modified += 1
    print(f'  Removed nav keys: {fname}')
print(f'\nDone — modified {modified} files.')
```

Run: `python scripts/remove_nav_keys.py`

Expected: ~84 files modified. The 28 files that never had these keys are unchanged.

> **Research caveat (YAML round-trip):** `yaml.safe_dump` normalises key quoting and ordering. Fields that were originally double-quoted strings may be emitted as single-quoted strings after the round-trip. This is semantically identical and gray-matter parses it correctly, but the git diff will show cosmetic changes on strings containing apostrophes or colons. This is expected and not a correctness issue — run `git diff --stat` after the migration to confirm only `prev:`/`next:` removal and quote normalisation.

> **Research caveat (sexagesimal integers):** PyYAML treats unquoted `H:MM:SS` values as integers under YAML 1.1. All non-zero-hour timeline `time:` values in the current corpus are already single-quoted (e.g., `'1:22:42'`). The round-trip is safe. If a future meeting file adds an unquoted non-zero-hour time AND has `prev`/`next` keys, this migration would corrupt that `time` value. The migration script does not need special handling for the current corpus, but this behaviour should be noted.

#### 1d. Update `sync_drive.py` — confirm no nav key writes

Confirmed by research: `sync_drive.py` does not currently write `prev:` or `next:` keys (the `front_matter` dict in `generate_stubs()` at line 247 does not include them). No code change needed. Add a one-line comment confirming intentional omission:

```python
# prev/next navigation is computed at build time from the sorted meetings array.
# Do not add these fields here.
```

#### 1e. Update `AGENTS.md`

Remove step 3 from "Adding a new stub meeting":
> ~~Update `prev`/`next` slugs on the adjacent meeting files.~~

Replace with: navigation is derived automatically at build time.

---

### Phase 2 — Implementation

> **⚠️ Critical sequencing requirement:** Steps 2b through 2f **must ship in one atomic commit**. Any intermediate state where `meetings.json` is deleted without `post_process.py` and `sync_drive.py` being patched causes the GH Actions pipeline to either hard-crash or silently recreate a partial `meetings.json` that shadows `meetings.js`. Any intermediate state where `meetings.js` exists alongside `meetings.json` causes Eleventy to merge them (JSON overwrites JS), silently serving stale data. There is no safe intermediate commit between state A (JSON only) and state B (JS only).

#### 2a. Add `gray-matter` as a dev dependency (commit separately — no risk)

```bash
npm install --save-dev gray-matter
```

`gray-matter` is already present as a transitive dependency of Eleventy 3.x (`@11ty/eleventy` → `gray-matter`), but it must be listed explicitly in `package.json` to be relied upon.

> **Research insight (gray-matter API):** Use `matter.read(filePath)` instead of `matter(fs.readFileSync(filePath, 'utf8'))`. It reads and parses in one call, is documented in the gray-matter API, and is slightly faster:
>
> ```js
> const { data } = matter.read(path.join(dir, f));
> // vs:
> const { data } = matter(fs.readFileSync(path.join(dir, f), 'utf8'));
> ```

#### 2b–2f. The atomic commit — all five steps together

**2b. Create `src/_data/meetings.js`**

```js
// src/_data/meetings.js
'use strict';

const path  = require('path');
const matter = require('gray-matter');
const fs    = require('fs');

// School year: August (month 8) starts the new academic year.
// July and earlier belong to the prior school year.
// NOTE: sync_drive.py used month >= 7 (a known off-by-one in that script).
// This file uses month >= 8, matching the actual data for all July meetings
// except the anomalous 2026-07-13 (which shows 2026-2027 in meetings.json).
// That entry will change school year on first deploy — this is a correctness fix.
function schoolYear(slug) {
  const m = parseInt(slug.slice(5, 7), 10);
  const y = parseInt(slug.slice(0, 4), 10);
  return m >= 8 ? `${y}-${y + 1}` : `${y - 1}-${y}`;
}

// Parse meeting type from meeting_tag front matter field.
// meeting_tag examples: "Regular Meeting · May 2026", "Exec. Session · April 2026",
// "Special Meeting · April 2026", "Workshop · March 2026", "Regular Board Meeting"
function meetingType(tag) {
  if (!tag) return 'Regular';
  const t = tag.toLowerCase().trimStart();
  if (t.startsWith('exec'))       return 'Exec. Session';  // "Exec." or "Executive"
  if (t.startsWith('special'))    return 'Special';
  if (t.startsWith('workshop'))   return 'Workshop';
  if (t.startsWith('emergency'))  return 'Emergency';
  if (t.startsWith('inaug'))      return 'Inauguration';
  return 'Regular';
}

module.exports = function () {
  const dir = path.join(__dirname, '../meetings');

  return fs.readdirSync(dir)
    .filter(f => /^\d{4}-\d{2}-\d{2}\.njk$/.test(f))
    .map(f => {
      const slug = f.replace('.njk', '');
      try {
        const { data } = matter.read(path.join(dir, f));
        if (!data.display_date) return null;  // Skip malformed files gracefully
        return {
          slug,
          school_year:    schoolYear(slug),
          date:           slug,
          display_date:   data.display_date,
          day_of_week:    data.day_of_week   || '',
          type:           meetingType(data.meeting_tag),
          // NOTE: meetings.json 'title' = .njk 'heading' (the short display title).
          //       .njk 'title' is the HTML <title> tag value — do NOT use that field.
          title:          data.heading       || '',
          topics:         Array.isArray(data.topics) ? data.topics : [],
          doc_count:      Array.isArray(data.docs)
                            ? data.docs.filter(d => d.type !== 'video').length
                            : 0,
          has_video:      !!data.has_video,
          has_transcript: !!data.has_transcript,
          stub:           data.stub !== false,
          blurb:          data.blurb         || ''
        };
      } catch (err) {
        console.warn(`[meetings.js] Skipped ${f}: ${err.message}`);
        return null;
      }
    })
    .filter(Boolean)
    .sort((a, b) => b.date.localeCompare(a.date));
};
```

> **Research insight (correctness):** The original plan had `meetingType` returning `"Exec."` for `meeting_tag: "Exec. Session · …"`. The startsWith check on the lowercased tag correctly returns `"Exec. Session"` since `"exec. session".startsWith("exec")` → true. Confirmed correct.

> **Research insight (doc_count):** No current meeting has `type: 'video'` in its docs array — the filter `d => d.type !== 'video'` is currently a no-op. The value will equal `docs.length` for all meetings. The 16 meetings with stale `doc_count` in the current `meetings.json` will show corrected counts on first deploy.

**2c. Update `src/_data/years.js`**

```js
// src/_data/years.js
const getMeetings = require('./meetings.js');
module.exports = [...new Set(getMeetings().map(m => m.school_year))].sort().reverse();
```

> **Why this must be in the same commit as 2f:** `years.js` currently does `require('./meetings.json')`. If `meetings.json` is deleted before `years.js` is updated, the build fails with `MODULE_NOT_FOUND` during Eleventy's data cascade — all 112 pages fail to render.

**2d. Remove `meetings.json` write paths from `sync_drive.py`**

In `generate_stubs()`, delete these blocks entirely:

- Lines 147–152: loading `meetings.json` into `global_json`
- Lines 205–216: updating `doc_count` in `global_json` after doc merge
- Lines 270–284: appending new stub entries to `global_json`
- Lines 286–289: sorting and writing `global_json` back to `meetings.json`
- Line 145: `meetings_json_path = 'src/_data/meetings.json'` variable

After removal, `generate_stubs()` only writes `.njk` files. The `changes_made` counter and logging are unaffected.

Also add the same one-line comment from Phase 1 confirming intentional omission of `meetings.json` writes.

**2e. Remove meetings.json sync block from `post_process.py`**

Delete lines 129–154 (the `# --- 3. Sync meetings.json` block). This block synced `stub`, `topics`, `has_transcript`, `blurb` from `.njk` → `meetings.json`. Those fields are now picked up by `meetings.js` automatically on every build.

The `generate_blurb` function (lines 141–150) that writes blurbs back to `.njk` files can remain — writing AI-generated blurbs into the `.njk` is correct. Only the step that subsequently copies the blurb into `meetings.json` is removed.

> **Research insight (reliability):** `post_process.py` line 131 does a bare `open(meetings_json_path, 'r')` with no existence check. If `meetings.json` is deleted before this line is removed, the pipeline hard-crashes and GitHub Actions fails every day until the file is restored or the line is removed. This is why 2e and 2f must be in the same commit.

**2f. Delete `src/_data/meetings.json`**

```bash
git rm src/_data/meetings.json
```

**2g. Update GitHub Actions workflow** (can be in the same atomic commit or a separate follow-up)

Remove `master_material_map.json` and any reference to `src/_data/meetings.json` from the `git add` command in the pipeline step. Update the commit message template accordingly.

---

### Alternative Architecture (Research Insight)

The architecture strategist identified a more idiomatic Eleventy 3.x approach that avoids `gray-matter` entirely:

**`eleventyComputed` + `collections.meetings` in templates**

```js
// src/meetings/meetings.11tydata.js — inject computed fields via Eleventy's cascade
module.exports = {
  eleventyComputed: {
    slug: data => data.page.fileSlug,
    date: data => data.page.fileSlug,
    school_year: data => {
      const m = parseInt(data.page.fileSlug.slice(5, 7), 10);
      const y = parseInt(data.page.fileSlug.slice(0, 4), 10);
      return m >= 8 ? `${y}-${y+1}` : `${y-1}-${y}`;
    },
    doc_count: data => (data.docs || []).length,
    type: data => { /* derive from meeting_tag */ }
  }
};
```

Then change `index.njk` to loop over `collections.meetings` instead of the global `meetings` variable. Replace `years.js` with an `addCollection("years", ...)` call in `.eleventy.js`.

**Why we chose the `meetings.js` approach instead:** Fewer template changes. The `meetings` global variable is used in `index.njk`, `topics-index.njk`, `topic.njk`, and the `meeting.njk` layout. Changing all of those to `collections.meetings` is a larger diff. The `meetings.js` approach is one new file and one 2-line update to `years.js`.

The `eleventyComputed` approach is the right long-term direction if this system grows significantly. Document it in AGENTS.md as the recommended path if `meetings.js` ever needs to be revisited.

---

## System-Wide Impact

### Interaction Graph

**Phase 1 (nav filter):**
`npm run build` → Eleventy loads `meetings` global data → renders each `src/meetings/*.njk` → `meeting.njk` layout calls `{% set nav = meetings | meetingNav(page.fileSlug) %}` → filter returns `{ prev, next }` → nav links render

**Phase 2 (derive meetings.json):**
`npm run build` → Eleventy runs `src/_data/meetings.js` → `matter.read()` on all 112 .njk files → returns sorted meetings array → same array used by index, topics, and Phase 1 nav filter → `years.js` imports same module

**Python pipeline → site (post-Phase 2):**
`sync_drive.py` writes new `.njk` stubs only → `post_process.py` writes AI content to `.njk` files only → `npm run build` derives `meetings.json` array from updated `.njk` files → site reflects all changes

### Error Propagation

- Malformed YAML in a `.njk` file: the `try/catch` in `meetings.js` logs a warning and returns `null` (filtered out). The meeting disappears from the index rather than crashing the build.
- `meetingNav` called with an unknown slug (e.g., a test page): returns `{ prev: null, next: null }` — the template renders boundary-state nav gracefully.
- `sync_drive.py` run after Phase 2: does not touch `meetings.json` (write paths removed). New stubs appear on the next build automatically.

### State Lifecycle Risks

| Risk | Trigger | Mitigation |
|---|---|---|
| `sync_drive.py` recreates partial `meetings.json` | Deployed between Phase 2 steps | Single atomic commit for 2b–2f |
| `post_process.py` hard-crashes on missing `meetings.json` | Deployed between Phase 2 steps | Same atomic commit |
| `years.js` crashes build on missing `meetings.json` | Deleted before `years.js` updated | Same atomic commit |
| Eleventy merges `meetings.json` + `meetings.js` (JSON wins) | Both files present during any build | Same atomic commit |

---

## Testing Strategy

### Minimum viable test surface (all run in < 5 seconds, no framework needed)

**Test 1 — `meetingNav` unit test** (add as `test/meetingNav.test.js`)

```js
// test/meetingNav.test.js
const assert = require('assert');

// Replicate the filter logic
function meetingNav(meetings, currentSlug) {
  const idx = meetings.findIndex(m => m.slug === currentSlug);
  if (idx === -1) return { prev: null, next: null };
  return {
    prev: idx > 0 ? { slug: meetings[idx-1].slug, label: meetings[idx-1].display_date } : null,
    next: idx < meetings.length-1 ? { slug: meetings[idx+1].slug, label: meetings[idx+1].display_date } : null
  };
}

const meetings = [
  { slug: '2026-05-01', display_date: 'May 1, 2026' },
  { slug: '2026-03-01', display_date: 'March 1, 2026' },
  { slug: '2025-01-01', display_date: 'January 1, 2025' }
];  // Pre-sorted descending

// Middle meeting: both prev and next present
const mid = meetingNav(meetings, '2026-03-01');
assert.equal(mid.prev.slug, '2026-05-01', 'prev should be newer');
assert.equal(mid.next.slug, '2025-01-01', 'next should be older');

// Newest meeting: no prev
const newest = meetingNav(meetings, '2026-05-01');
assert.equal(newest.prev, null, 'newest has no prev');
assert.equal(newest.next.slug, '2026-03-01');

// Oldest meeting: no next
const oldest = meetingNav(meetings, '2025-01-01');
assert.equal(oldest.prev.slug, '2026-03-01');
assert.equal(oldest.next, null, 'oldest has no next');

// Unknown slug
const unknown = meetingNav(meetings, 'does-not-exist');
assert.equal(unknown.prev, null);
assert.equal(unknown.next, null);

console.log('meetingNav: all assertions passed');
```

Run: `node test/meetingNav.test.js`

**Test 2 — Build integrity gate** (add to CI or run manually after Phase 1)

```bash
# Assert build succeeds and expected page count is produced
export PATH="/Users/wboyd-boffa/Library/Application Support/Zed/node/node-v24.11.0-darwin-arm64/bin:$PATH"
npm run build && \
  echo "Build succeeded" && \
  test "$(ls _site/meetings/ | wc -l | tr -d ' ')" -ge 112 && \
  echo "Page count OK: $(ls _site/meetings/ | wc -l) meeting pages"
```

After Phase 2, expected count increases to ≥ 133 (adds the 21 previously-invisible stubs).

**Test 3 — No prev/next keys remain after migration** (run after migration script)

```bash
grep -r "^prev:" src/meetings/ | wc -l  # Must be 0
grep -r "^next:" src/meetings/ | wc -l  # Must be 0
```

### Validation script (pre-deletion diff check for Phase 2)

Before committing the atomic Phase 2 commit, run this to verify the derived data matches on all fields:

```bash
# Step 1: generate derived JSON
node -e "
  const m = require('./src/_data/meetings.js');
  process.stdout.write(JSON.stringify(m()));
" > /tmp/derived.json

# Step 2: run comparison
python3 scripts/validate_meetings_derivation.py
```

```python
# scripts/validate_meetings_derivation.py
import json

with open('src/_data/meetings.json') as f:
    original = {m['slug']: m for m in json.load(f)}

with open('/tmp/derived.json') as f:
    derived = {m['slug']: m for m in json.load(f)}

# Fields to compare (blocking mismatches)
compare = ['display_date', 'day_of_week', 'school_year', 'has_video',
           'has_transcript', 'stub', 'topics']
# Fields to warn (known expected differences)
warn = ['type', 'doc_count', 'title']

blocking = 0
warnings = 0
for slug, orig in original.items():
    drv = derived.get(slug)
    if not drv:
        print(f'MISSING from derived: {slug}')
        blocking += 1
        continue
    for f in compare:
        if orig.get(f) != drv.get(f):
            print(f'MISMATCH {slug} [{f}]: json={orig.get(f)!r} vs derived={drv.get(f)!r}')
            blocking += 1
    for f in warn:
        if orig.get(f) != drv.get(f):
            print(f'  WARN {slug} [{f}]: json={orig.get(f)!r} vs derived={drv.get(f)!r}')
            warnings += 1

print()
print(f'{len(derived) - len(original):+d} new meetings in derived (previously-invisible stubs)')
print(f'{blocking} blocking mismatches | {warnings} expected differences')
print()
if blocking > 0:
    print('❌ Blocking mismatches found — do not proceed with deletion')
    exit(1)
else:
    print('✅ No blocking mismatches — safe to proceed with deletion')
```

Expected output on a clean run: 0 blocking mismatches, ~16 `doc_count` warnings, 6 `type` warnings (Exec. Session → `"Exec. Session"` vs current `"Exec. Session"` — should match), +21 new meetings.

---

## Acceptance Criteria

### Phase 1

- [ ] `grep -r "^prev:" src/meetings/ | wc -l` = 0
- [ ] `grep -r "^next:" src/meetings/ | wc -l` = 0
- [ ] `npm run build` succeeds with no errors
- [ ] `node test/meetingNav.test.js` exits 0 (all assertions pass)
- [ ] Nav renders correctly on 5 randomly chosen meeting pages — spot-check in browser
- [ ] Oldest meeting (`2023-08-21`) shows "← Oldest meeting" and working Newer link
- [ ] Newest meeting shows working Older link and "Most recent meeting →"
- [ ] 28 previously nav-less pages now show correct nav links
- [ ] AGENTS.md updated: step 3 of "Adding a new stub" removed

### Phase 2

- [ ] Validation script exits 0 (no blocking mismatches) before committing
- [ ] `src/_data/meetings.json` not in git tree (`git ls-files src/_data/meetings.json` = empty)
- [ ] `npm run build` succeeds after atomic commit
- [ ] `ls _site/meetings/ | wc -l` ≥ 133 (accounts for 21 newly-visible stubs)
- [ ] Year filter correctly groups July meetings: `2025-07-30` appears in `2024-2025` group
- [ ] Index cards show short descriptive title (e.g., "May Regular Meeting"), not HTML page title
- [ ] `gray-matter` appears in `devDependencies` in `package.json`
- [ ] Running `sync_drive.py` does not create or modify `src/_data/meetings.json`
- [ ] Running `post_process.py` writes blurbs to `.njk` files; next build reflects them
- [ ] AGENTS.md updated to describe new data architecture

---

## Dependencies & Risks

| Risk | Severity | Mitigation |
|---|---|---|
| GH Actions cron fires between Phase 2 sub-steps | Critical | Atomic commit: 2b–2f in one commit |
| `post_process.py` crashes on missing `meetings.json` | Critical | Same atomic commit |
| `years.js` crashes build if JSON deleted first | Critical | Same atomic commit |
| `meetingType` returning wrong string for Exec. Session | High | Fixed in plan (`startsWith('exec')`) |
| 21 new stub cards appear on index on first deploy | Medium | Intentional — confirm in commit message |
| YAML round-trip cosmetic diffs (key ordering, quoting) | Low | Expected — review `git diff --stat` |
| Python sexagesimal integer corruption on unquoted times | Low | Current corpus uses quoted values; latent risk |
| `2026-07-13` school year changes from `2026-2027` to `2025-2026` | Low | Correctness fix; note in commit message |
| Build time increase from 112 fs reads | Negligible | Benchmarked at 3.5ms warm, 19ms cold CI |

---

## Sources & References

### Origin document

[docs/ideation/2026-06-08-simplify-data-driven-ideation.md](../ideation/2026-06-08-simplify-data-driven-ideation.md)

Key decisions carried forward:
- Ideas #1 and #2 from ranked survivors
- Phase ordering: nav first (lower risk), data derivation second
- `meetingNav` filter takes `meetings` as template variable (no file-system dependency)

### Internal references

- `.eleventy.js` — existing `where` filter pattern (model for `meetingNav`)
- `src/_data/years.js` — existing synchronous JS data file pattern
- `src/_includes/layouts/meeting.njk:25–38` — current nav rendering block
- `post_process.py:129–154` — sync block to remove
- `sync_drive.py:147–289` — meetings.json write paths to remove

### External references

- Eleventy global data files: https://www.11ty.dev/docs/data-global/
- Eleventy computed data: https://www.11ty.dev/docs/data-computed/
- gray-matter `read()` API: https://github.com/jonschlinkert/gray-matter#matter-read

### Related commits

- `54ba33c` — "Add 46 missing meetings": demonstrates the scale of the manual nav problem
- `e52e90a` — previous AGENTS.md cleanup: established .njk as the processing SOT in docs
- `dda4d20` — CSS/HTML cleanup: `meeting.njk` in its current clean state
