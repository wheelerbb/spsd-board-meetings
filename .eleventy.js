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
