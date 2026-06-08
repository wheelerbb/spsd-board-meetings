'use strict';
const assert = require('assert');
const { schoolYear, meetingType } = require('../src/_data/meetings');

// ── schoolYear ────────────────────────────────────────────────────────────────

// August is the first month of the new school year
assert.strictEqual(schoolYear('2024-08-19'), '2024-2025', 'August starts new year');
assert.strictEqual(schoolYear('2025-08-18'), '2025-2026', 'August starts new year (2025)');

// July belongs to the PRIOR school year (the documented boundary)
assert.strictEqual(schoolYear('2024-07-08'), '2023-2024', 'July is prior year');
assert.strictEqual(schoolYear('2025-07-01'), '2024-2025', 'July 2025 is prior year');
assert.strictEqual(schoolYear('2025-07-30'), '2024-2025', 'July 2025 is prior year');
// Anomalous entry: was '2026-2027' in old meetings.json (sync_drive bug); correctly '2025-2026' now
assert.strictEqual(schoolYear('2026-07-13'), '2025-2026', 'July 2026 anomaly uses prior year');

// September through June are unambiguous
assert.strictEqual(schoolYear('2024-09-09'), '2024-2025', 'September is new year');
assert.strictEqual(schoolYear('2025-06-10'), '2024-2025', 'June stays in same year');
assert.strictEqual(schoolYear('2025-01-01'), '2024-2025', 'January stays in same year');

// ── meetingType ───────────────────────────────────────────────────────────────

// Regression lock: must return 'Exec. Session', not 'Exec.' (named bug fix)
assert.strictEqual(meetingType('Exec. Session · April 2026'), 'Exec. Session', 'exec session full tag');
assert.strictEqual(meetingType('exec. session · april 2026'), 'Exec. Session', 'exec session lowercase');
assert.strictEqual(meetingType('Executive Session'), 'Exec. Session', 'executive prefix');

// All other named types
assert.strictEqual(meetingType('Special Meeting · March 2026'), 'Special');
assert.strictEqual(meetingType('Workshop · March 2026'), 'Workshop');
assert.strictEqual(meetingType('Emergency Meeting'), 'Emergency');
assert.strictEqual(meetingType('Inauguration Ceremony'), 'Inauguration');

// Default fallback for regular meetings
assert.strictEqual(meetingType('Regular Meeting · May 2026'), 'Regular');
assert.strictEqual(meetingType('Regular Board Meeting'), 'Regular');

// Null/falsy guard
assert.strictEqual(meetingType(null),      'Regular', 'null → Regular');
assert.strictEqual(meetingType(undefined), 'Regular', 'undefined → Regular');
assert.strictEqual(meetingType(''),        'Regular', 'empty string → Regular');

// Leading whitespace is trimmed
assert.strictEqual(meetingType('  Exec. Session · April 2026'), 'Exec. Session', 'leading whitespace trimmed');

console.log('meetings (schoolYear + meetingType): all assertions passed ✓');
