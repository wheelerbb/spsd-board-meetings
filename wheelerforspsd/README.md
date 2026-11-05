# wheelerforspsd.org copy of the digest

A second publishing destination for the digest, served at the root of https://wheelerforspsd.org/
and recolored with the teal/yellow palette (harbor teal `#224d4f`, school bus yellow `#ffd800`,
off-white `#f7f3ec`). The GitHub Pages site is unaffected — nothing outside this folder and
`.github/workflows/deploy-wheelerforspsd.yml` knows about it.

Until Nov 2026 the domain held Wheeler's campaign site (repo `www-wheeler-for-spsd`, archived, tag
`campaign-2026`) and this digest lived under `/spsd-board-meetings/`. `_redirects` keeps those old
URLs working.

## How it works

- `eleventy.config.js` wraps the main `.eleventy.js` and builds to `wheelerforspsd/_site/` with
  `pathPrefix: /`. An HTML transform injects `theme.css` and swaps the SP logo for the
  "[SPSD] SCHOOL BOARD / MEETING DIGEST" wordmark (SPSD as a yellow block). If `base.njk` changes so one of those anchors no longer
  matches, the build throws rather than silently dropping it.
- `icons/` holds the split teal/yellow favicon carried over from the campaign site, copied to the
  site root and linked from every page by the same transform.
- `theme.css` overrides `style.css`'s color tokens (`--navy`, `--gold`, `--gold-light`, `--cream`,
  `--text`) plus a handful of hardcoded gold tints, and styles the wordmark (Sora 800 from `fonts/`).
- `_redirects` (copied to the site root):
  - `/spsd-board-meetings/*` → `/:splat` (301)
  - `/qr` → `/` (302; printed on campaign handouts, so it can be repointed)
  - `/donate` → `/` (301)
- `wrangler.jsonc` deploys `_site/` as an assets-only Worker with `wheelerforspsd.org` as its
  Custom Domain (wrangler manages the DNS record). `www.wheelerforspsd.org` redirects to the apex via
  a zone-level rule in Cloudflare.
- `.github/workflows/deploy-wheelerforspsd.yml` runs after every "Automate, Build, and Deploy" run
  (and on manual dispatch), rebuilds from latest `main`, and deploys. Needs repo secrets
  `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.

## Local build

```bash
npx @11ty/eleventy --config=wheelerforspsd/eleventy.config.js   # from repo root
cd wheelerforspsd && npx wrangler@4.40.0 dev                    # http://localhost:8787/
```

## Removing this destination

1. Delete `wheelerforspsd/` and `.github/workflows/deploy-wheelerforspsd.yml`.
2. In Cloudflare, delete the `spsd-board-meetings` Worker (Workers & Pages), which also drops its
   Custom Domain and DNS record.
3. Optionally delete the `CLOUDFLARE_*` repo secrets and revoke the API token.
