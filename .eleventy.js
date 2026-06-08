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
    const labels = { agenda: "PDF", packet: "PDF", min: "PDF" };
    return labels[type] || type.toUpperCase();
  });

  // Normalize doc type to a CSS class representing the file format
  eleventyConfig.addFilter("docClass", (type) => {
    if (["agenda", "packet", "min"].includes(type)) return "pdf";
    return type.toLowerCase();
  });

  // Compute prev/next navigation from the sorted meetings array.
  // meetings is already sorted descending by date slug (YYYY-MM-DD string sort).
  // 'prev' = chronologically newer (right arrow), 'next' = older (left arrow).
  // Same-day slugs: undefined ordering — no two meetings currently share a date.
  eleventyConfig.addFilter("meetingNav", (meetings, currentSlug) => {
    const idx = meetings.findIndex((m) => m.slug === currentSlug);
    if (idx === -1) return { prev: null, next: null };
    return {
      prev: idx > 0
        ? { slug: meetings[idx - 1].slug, label: meetings[idx - 1].display_date }
        : null,
      next: idx < meetings.length - 1
        ? { slug: meetings[idx + 1].slug, label: meetings[idx + 1].display_date }
        : null,
    };
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
