module.exports = function (eleventyConfig) {
  eleventyConfig.addPassthroughCopy("src/assets");

  // Collection: All meetings from the meetings folder
  eleventyConfig.addCollection("meetings", function (collectionApi) {
    return collectionApi.getFilteredByGlob("src/meetings/*.njk");
  });

  // Filter meetings by a data field value
  eleventyConfig.addFilter("where", (array, key, value) =>
    array.filter((item) => item[key] == value)
  );

  // Map doc type to the short icon label shown in the sidebar (file type only)
  eleventyConfig.addFilter("docIcon", (type) => {
    const labels = { agenda: "PDF", packet: "PDF", min: "PDF", minutes: "PDF", vtt: "VTT" };
    return labels[type] || type.toUpperCase();
  });

  // Normalize doc type to a CSS class representing the file format
  eleventyConfig.addFilter("docClass", (type) => {
    if (["agenda", "packet", "min", "minutes"].includes(type)) return "pdf";
    if (type === "vtt") return "vtt";
    return type.toLowerCase();
  });

  eleventyConfig.addFilter("meetingNav", require("./src/_lib/meetingNav"));

  eleventyConfig.addFilter("secondsToTime", (s) => {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `${h}:${String(m).padStart(2, '0')}`;
  });

  // Compress verbose "Moved by X, seconded by Y." → "X / Y"
  eleventyConfig.addFilter("movedShort", (str) => {
    if (!str) return str;
    const m = str.match(/moved by ([^,]+),\s*seconded by ([^.;]+)/i);
    if (m) return `${m[1].trim()} / ${m[2].trim().replace(/\.$/, '')}`;
    return str;
  });

  // Normalize a vote's free-text result into a chip CSS class (pass/fail), passing
  // through anything unrecognized (e.g. "X elected") instead of leaving it unstyled.
  eleventyConfig.addFilter("voteChipClass", (result) => {
    const r = (result || "").toLowerCase();
    if (["pass", "passed", "approved"].includes(r)) return "pass";
    if (["fail", "failed", "denied"].includes(r)) return "fail";
    return r;
  });

  // True if every word of `topicName` appears somewhere in `motion` — looser than a
  // literal substring match, since motions rarely repeat a topic tag verbatim
  // (e.g. "FY25 Budget" vs. "To approve the FY25 Superintendent's Budget.").
  eleventyConfig.addFilter("topicMatch", (motion, topicName) => {
    if (!motion || !topicName) return false;
    const m = motion.toLowerCase();
    const words = topicName.toLowerCase().split(/\s+/).filter(Boolean);
    return words.every((w) => m.includes(w));
  });

  const escapeHtml = (str) =>
    String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  // Split free text into <p> blocks on blank lines, escaping content since the result
  // is rendered with | safe.
  eleventyConfig.addFilter("paragraphs", (text) => {
    if (!text) return "";
    return String(text)
      .split(/\n\s*\n/)
      .map((p) => p.trim())
      .filter(Boolean)
      .map((p) => `<p>${escapeHtml(p).replace(/\n/g, "<br>")}</p>`)
      .join("\n");
  });

  // Wrap literal occurrences of a tagged meeting's display_date within `html` in a link
  // to that meeting's page, so narrative citations ("On July 14, 2025, ...") are clickable.
  // `meetings` items are { date, url } with url already resolved (pathPrefix applied).
  eleventyConfig.addFilter("linkMeetingDates", (html, meetings) => {
    if (!html || !meetings || !meetings.length) return html;
    let result = html;
    for (const m of meetings) {
      if (!m.date) continue;
      const escapedDate = m.date.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      result = result.replace(new RegExp(escapedDate, "g"), `<a href="${m.url}">${m.date}</a>`);
    }
    return result;
  });

  return {
    pathPrefix: process.env.PATH_PREFIX || "/spsd-board-meetings/",
    dir: {
      input: "src",
      output: "_site",
      includes: "_includes",
      data: "_data",
    },
    templateFormats: ["njk", "html"],
    htmlTemplateEngine: "njk",
  };
};
