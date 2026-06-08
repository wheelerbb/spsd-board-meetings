'use strict';

// Compute prev/next neighbours from the sorted meetings array.
// meetings is pre-sorted descending by date slug (YYYY-MM-DD).
// 'prev' = chronologically newer (right arrow), 'next' = older (left arrow).
module.exports = function meetingNav(meetings, currentSlug) {
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
};
