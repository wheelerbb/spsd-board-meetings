// Build config for the wheelerforspsd.org copy of the digest. Wraps the main .eleventy.js
// without modifying it, so this whole publishing destination can be removed by deleting
// wheelerforspsd/ and .github/workflows/deploy-wheelerforspsd.yml. See wheelerforspsd/README.md.
const base = require("../.eleventy.js");

const PATH_PREFIX = "/";
// "[SPSD] SCHOOL BOARD / MEETING DIGEST" wordmark; the digest nav is teal, so on-teal coloring.
const LOCKUP = `<a href="${PATH_PREFIX}" class="lockup on-teal" aria-label="SPSD School Board Meeting Digest">
    <span class="chip">SPSD</span>
    <span class="txt"><span class="t1">School Board</span><span class="t2"><span>M</span><span>e</span><span>e</span><span>t</span><span>i</span><span>n</span><span>g</span><span>&nbsp;</span><span>D</span><span>i</span><span>g</span><span>e</span><span>s</span><span>t</span></span></span>
  </a>`;

// Each edit must match exactly once; a miss throws so a future change to base.njk fails
// this build loudly instead of silently dropping (e.g.) the theme stylesheet.
const EDITS = [
  {
    name: "theme stylesheet and favicons",
    find: "</head>",
    replace: `<link rel="stylesheet" href="${PATH_PREFIX}assets/theme.css">
<link rel="icon" href="${PATH_PREFIX}favicon.ico" sizes="32x32">
<link rel="icon" href="${PATH_PREFIX}favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="${PATH_PREFIX}apple-touch-icon.png">
</head>`,
  },
  {
    name: "wordmark",
    find: new RegExp(`<a href="${PATH_PREFIX}" class="nav-logo">[\\s\\S]*?</a>`),
    replace: LOCKUP,
  },
];

module.exports = function (eleventyConfig) {
  const cfg = base(eleventyConfig);

  eleventyConfig.addPassthroughCopy({
    "wheelerforspsd/theme.css": "assets/theme.css",
    "wheelerforspsd/fonts": "assets/theme-fonts",
    "wheelerforspsd/_redirects": "_redirects",
    // Split teal/yellow icon carried over from the campaign site; served from the root.
    "wheelerforspsd/icons": "/",
  });

  eleventyConfig.addTransform("wheelerforspsd", function (content) {
    if (!(this.page.outputPath || "").endsWith(".html")) return content;
    return EDITS.reduce((html, edit) => {
      const count =
        typeof edit.find === "string"
          ? html.split(edit.find).length - 1
          : (html.match(new RegExp(edit.find.source, "g")) || []).length;
      if (count !== 1) {
        throw new Error(
          `wheelerforspsd transform "${edit.name}": expected 1 match in ${this.page.inputPath}, found ${count}`
        );
      }
      return html.replace(edit.find, edit.replace);
    }, content);
  });

  return {
    ...cfg,
    pathPrefix: PATH_PREFIX,
    dir: { ...cfg.dir, output: "wheelerforspsd/_site" },
  };
};
