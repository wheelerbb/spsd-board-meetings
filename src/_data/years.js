const getMeetings = require('./meetings.js');
module.exports = [...new Set(getMeetings().filter(m => !m.future).map((m) => m.school_year))].sort().reverse();
