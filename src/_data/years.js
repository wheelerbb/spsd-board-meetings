const getMeetings = require('./meetings.js');
module.exports = [...new Set(getMeetings().map((m) => m.school_year))].sort().reverse();
