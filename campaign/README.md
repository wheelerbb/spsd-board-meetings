# Campaign copy: wheelerforspsd.org/spsd-board-meetings/

A second publishing destination for the digest, recolored with the campaign palette
(harbor teal `#224d4f`, school bus yellow `#ffd800`, off-white `#f7f3ec`). The GitHub Pages
site is unaffected — nothing outside this folder and `.github/workflows/deploy-campaign.yml`
knows about it.

## How it works

- `eleventy.config.js` wraps the main `.eleventy.js` and builds to `campaign/_site/spsd-board-meetings/`
  with `pathPrefix: /spsd-board-meetings/`. An HTML transform injects `campaign.css`, swaps the
  SP logo for the campaign wordmark (linked to wheelerforspsd.org), and adds the "Paid for…" line to the footer. If `base.njk` changes so
  one of those anchors no longer matches, the build throws rather than silently dropping it.
- `campaign.css` overrides `style.css`'s color tokens (`--navy`, `--gold`, `--gold-light`, `--cream`,
  `--text`) plus a handful of hardcoded gold tints.
- `wrangler.jsonc` deploys `_site/` as an assets-only Worker on the route
  `wheelerforspsd.org/spsd-board-meetings*`, which runs in front of the main campaign site's Worker.
- `.github/workflows/deploy-campaign.yml` runs after every "Automate, Build, and Deploy" run (and on
  manual dispatch), rebuilds from latest `main`, and deploys. Needs repo secrets
  `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.

## Local build

```bash
npx @11ty/eleventy --config=campaign/eleventy.config.js   # from repo root
cd campaign && npx wrangler@4.40.0 dev                    # http://localhost:8787/spsd-board-meetings/
```

## Removing this destination

1. Delete `campaign/` and `.github/workflows/deploy-campaign.yml`.
2. In Cloudflare, delete the `spsd-board-meetings` Worker (Workers & Pages), which also drops its route.
3. Optionally delete the `CLOUDFLARE_*` repo secrets and revoke the API token.
