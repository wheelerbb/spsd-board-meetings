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
