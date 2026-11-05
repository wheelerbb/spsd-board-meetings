// Build config for the wheelerforspsd.org copy of the digest. Wraps the main .eleventy.js
// without modifying it, so this whole publishing destination can be removed by deleting
// wheelerforspsd/ and .github/workflows/deploy-wheelerforspsd.yml. See wheelerforspsd/README.md.
const base = require("../.eleventy.js");

const PATH_PREFIX = "/";
// "SOUTH PORTLAND / [BoE] DIGEST" wordmark; the digest nav is teal, so on-teal coloring.
const LOCKUP = `<a href="${PATH_PREFIX}" class="lockup on-teal" aria-label="South Portland BoE Digest">
    <div class="l1"><span class="name">South Portland</span></div>
    <div class="l2"><span class="chip">BoE</span><span class="word"><span>D</span><span>I</span><span>G</span><span>E</span><span>S</span><span>T</span></span></div>
  </a>`;

// Each edit must match exactly once; a miss throws so a future change to base.njk fails
// this build loudly instead of silently dropping (e.g.) the theme stylesheet.
const EDITS = [
  {
    name: "theme stylesheet",
    find: "</head>",
    replace: `<link rel="stylesheet" href="${PATH_PREFIX}assets/theme.css">\n</head>`,
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
