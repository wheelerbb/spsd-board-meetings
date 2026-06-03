module.exports = function (eleventyConfig) {
  eleventyConfig.addPassthroughCopy("src/assets");

  // Filter meetings by a data field value
  eleventyConfig.addFilter("where", (array, key, value) =>
    array.filter((item) => item[key] == value)
  );

  // Map doc type to the short icon label shown in the sidebar
  eleventyConfig.addFilter("docIcon", (type) => {
    const labels = { agenda: "AGD", min: "MIN", pdf: "PDF" };
    return labels[type] || type.toUpperCase();
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
