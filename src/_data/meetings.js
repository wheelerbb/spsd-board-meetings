'use strict';

const fs     = require('fs');
const path   = require('path');
const matter = require('gray-matter');

// School year: August (month 8) starts the new academic year.
// July and earlier belong to the prior school year.
// NOTE: sync_drive.py used month >= 7 (a known off-by-one in that script).
// This file uses month >= 8, matching actual data for all July meetings
// except the anomalous 2026-07-13 entry.
function schoolYear(slug) {
  const m = parseInt(slug.slice(5, 7), 10);
  const y = parseInt(slug.slice(0, 4), 10);
  return m >= 8 ? `${y}-${y + 1}` : `${y - 1}-${y}`;
}

// Topics filtered out of the public index (matches post_process.py TOPIC_BLACKLIST).
const TOPIC_BLACKLIST = new Set(['Personnel', 'Contracts', 'Finance', 'Budget']);

// Parse meeting type from meeting_tag front matter field.
// Examples: "Regular Meeting · May 2026", "Exec. Session · April 2026",
//           "Workshop · March 2026", "Regular Board Meeting"
function meetingType(tag) {
  if (!tag) return 'Regular';
  const t = tag.toLowerCase().trimStart();
  if (t.startsWith('exec'))      return 'Exec. Session';
  if (t.startsWith('special'))   return 'Special';
  if (t.startsWith('workshop'))  return 'Workshop';
  if (t.startsWith('emergency')) return 'Emergency';
  if (t.startsWith('inaug'))     return 'Inauguration';
  return 'Regular';
}

function getMeetings() {
  const dir = path.join(__dirname, '../meetings');

  return fs.readdirSync(dir)
    .filter(f => /^\d{4}-\d{2}-\d{2}\.njk$/.test(f))
    .map(f => {
      const slug = f.replace('.njk', '');
      try {
        const { data } = matter.read(path.join(dir, f));
        if (!data.display_date) return null;
        return {
          slug,
          school_year:    schoolYear(slug),
          date:           slug,
          display_date:   data.display_date,
          day_of_week:    data.day_of_week    || '',
          type:           meetingType(data.meeting_tag),
          // NOTE: meetings.json 'title' = .njk 'heading' (short display title).
          //       .njk 'title' is the HTML <title> tag value — do NOT use that.
          //       Strip HTML tags and any trailing date from the heading for the index.
          title:          (data.heading || '').split('<br>')[0].replace(/\s+—\s+.*$/, '').trim(),
          topics:         Array.isArray(data.topics)
                            ? data.topics.filter(t => !TOPIC_BLACKLIST.has(t))
                            : [],
          doc_count:      Array.isArray(data.docs)
                            ? data.docs.filter(d => d.type !== 'video').length
                            : 0,
          has_video:      !!data.has_video,
          has_transcript: !!data.has_transcript,
          stub:           data.stub !== false,
          blurb:          data.blurb          || '',
        };
      } catch (err) {
        console.warn(`[meetings.js] Skipped ${f}: ${err.message}`);
        return null;
      }
    })
    .filter(Boolean)
    .sort((a, b) => b.date.localeCompare(a.date));
}

module.exports = getMeetings;
module.exports.schoolYear = schoolYear;
module.exports.meetingType = meetingType;
