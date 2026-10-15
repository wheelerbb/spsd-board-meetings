// Build config for the wheelerforspsd.org copy of the digest. Wraps the main .eleventy.js
// without modifying it, so this whole publishing destination can be removed by deleting
// campaign/ and .github/workflows/deploy-campaign.yml. See campaign/README.md.
const base = require("../.eleventy.js");

const PATH_PREFIX = "/spsd-board-meetings/";
const CAMPAIGN_URL = "https://wheelerforspsd.org/";
// Campaign site wordmark, using the footer (on-teal) coloring since the digest nav is teal.
const LOCKUP = `<a href="${CAMPAIGN_URL}" class="lockup on-teal" aria-label="Wheeler for School Board">
    <div class="l1"><span class="name">Wheeler</span><span class="chip">For</span></div>
    <div class="l2"><span>S</span><span>C</span><span>H</span><span>O</span><span>O</span><span>L</span><span class="gap"></span><span>B</span><span>O</span><span>A</span><span>R</span><span>D</span></div>
  </a>`;
const PAID_FOR =
  "Paid for and authorized by Wheeler for School Board, 7 Ship Channel Rd, South Portland, ME 04106";

// Each edit must match exactly once; a miss throws so a future change to base.njk fails
// this build loudly instead of silently dropping (e.g.) the paid-for line.
const EDITS = [
  {
    name: "campaign stylesheet",
    find: "</head>",
    replace: `<link rel="stylesheet" href="${PATH_PREFIX}assets/campaign.css">\n</head>`,
  },
  {
    name: "wordmark",
    find: new RegExp(`<a href="${PATH_PREFIX}" class="nav-logo">[\\s\\S]*?</a>`),
    replace: LOCKUP,
  },
  {
    name: "paid-for line",
    find: "</footer>",
    replace: `  <p class="footer-small footer-paid-for">${PAID_FOR}</p>\n</footer>`,
  },
];

module.exports = function (eleventyConfig) {
  const cfg = base(eleventyConfig);

  eleventyConfig.addPassthroughCopy({
    "campaign/campaign.css": "assets/campaign.css",
    "campaign/fonts": "assets/campaign-fonts",
  });

  eleventyConfig.addTransform("campaign", function (content) {
    if (!(this.page.outputPath || "").endsWith(".html")) return content;
    return EDITS.reduce((html, edit) => {
      const count =
        typeof edit.find === "string"
          ? html.split(edit.find).length - 1
          : (html.match(new RegExp(edit.find.source, "g")) || []).length;
      if (count !== 1) {
        throw new Error(
          `campaign transform "${edit.name}": expected 1 match in ${this.page.inputPath}, found ${count}`
        );
      }
      return html.replace(edit.find, edit.replace);
    }, content);
  });

  return {
    ...cfg,
    pathPrefix: PATH_PREFIX,
    // Nested under spsd-board-meetings/ because the Worker serves assets by full request path.
    dir: { ...cfg.dir, output: "campaign/_site/spsd-board-meetings" },
  };
};
