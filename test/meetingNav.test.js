'use strict';
const assert = require('assert');

// Replicate the filter logic from .eleventy.js
function meetingNav(meetings, currentSlug) {
  const idx = meetings.findIndex((m) => m.slug === currentSlug);
  if (idx === -1) return { prev: null, next: null };
  return {
    prev: idx > 0
      ? { slug: meetings[idx - 1].slug, label: meetings[idx - 1].display_date }
      : null,
    next: idx < meetings.length - 1
      ? { slug: meetings[idx + 1].slug, label: meetings[idx + 1].display_date }
      : null,
  };
}

// Fixture: 3 meetings pre-sorted descending (newest first)
const meetings = [
  { slug: '2026-05-01', display_date: 'May 1, 2026' },
  { slug: '2026-03-01', display_date: 'March 1, 2026' },
  { slug: '2025-01-01', display_date: 'January 1, 2025' },
];

// Middle meeting: both prev (newer) and next (older)
const mid = meetingNav(meetings, '2026-03-01');
assert.strictEqual(mid.prev.slug, '2026-05-01', 'prev should be newer');
assert.strictEqual(mid.prev.label, 'May 1, 2026');
assert.strictEqual(mid.next.slug, '2025-01-01', 'next should be older');
assert.strictEqual(mid.next.label, 'January 1, 2025');

// Newest meeting: no prev
const newest = meetingNav(meetings, '2026-05-01');
assert.strictEqual(newest.prev, null, 'newest has no prev');
assert.strictEqual(newest.next.slug, '2026-03-01');

// Oldest meeting: no next
const oldest = meetingNav(meetings, '2025-01-01');
assert.strictEqual(oldest.prev.slug, '2026-03-01');
assert.strictEqual(oldest.next, null, 'oldest has no next');

// Unknown slug: both null
const unknown = meetingNav(meetings, 'does-not-exist');
assert.strictEqual(unknown.prev, null);
assert.strictEqual(unknown.next, null);

// Single-item corpus: both null
const single = meetingNav([meetings[0]], '2026-05-01');
assert.strictEqual(single.prev, null);
assert.strictEqual(single.next, null);

console.log('meetingNav: all 10 assertions passed ✓');
