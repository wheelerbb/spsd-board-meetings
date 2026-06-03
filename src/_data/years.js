const meetings = require("./meetings.json");
module.exports = [...new Set(meetings.map((m) => m.year))].sort().reverse();
