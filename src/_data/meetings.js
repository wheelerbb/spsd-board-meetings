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

// Topics filtered out of the public index. Single source of truth shared with post_process.py —
// see src/_data/topic_blacklist.json (do not hardcode this list here or in the Python pipeline).
const TOPIC_BLACKLIST = new Set(require('./topic_blacklist.json'));

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
  if (t.startsWith('retreat'))   return 'Retreat';
  return 'Regular';
}

let _cache;

function buildMeetings() {
  const dir = path.join(__dirname, '../meetings');
  const today = new Date().toISOString().split('T')[0];

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
          display_date:   data.display_date,
          day_of_week:    data.day_of_week    || '',
          time:           data.time           || '',
          location:       data.location       || '',
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
          // default true — new stubs rarely set stub: false immediately; a missing key means
          // "not yet marked non-stub", matching post_process.py's not m.get('stub', True)
          stub:           data.stub ?? true,
          future:         slug >= today,
          blurb:          data.blurb          || '',
          agenda_preview: data.agenda_preview  || '',
          docs:           Array.isArray(data.docs) ? data.docs : [],
        };
      } catch (err) {
        console.warn(`[meetings.js] Skipped ${f}: ${err.message}`);
        return null;
      }
    })
    .filter(Boolean)
    .sort((a, b) => b.slug.localeCompare(a.slug));
}

// Cache is process-scoped: Eleventy calls this once for the `meetings` global and years.js calls
// it again independently for `years` — without caching that's 2x the file reads per build. In
// watch mode the module is re-required on rebuild, clearing _cache automatically.
function getMeetings() {
  if (!_cache) _cache = buildMeetings();
  return _cache;
}

module.exports = getMeetings;
module.exports.schoolYear = schoolYear;
module.exports.meetingType = meetingType;
