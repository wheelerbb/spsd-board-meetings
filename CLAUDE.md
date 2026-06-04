# CLAUDE.md

Claude Code-specific guidance for this repository.

## Commands

```bash
npm run build    # build to _site/
npm start        # build + local dev server with live reload
```

Node is not in the system PATH on this machine. Use the Zed-bundled binary:
```bash
export PATH="/Users/wboyd-boffa/Library/Application Support/Zed/node/node-v24.11.0-darwin-arm64/bin:$PATH"
```

## Deployment

GitHub Pages via GitHub Actions (`.github/workflows/deploy.yml`). Pushes to `main` trigger a build and deploy automatically. To enable: go to repo Settings → Pages → Source → **GitHub Actions**.

The site is served at `https://wheelerbb.github.io/spsd-board-meetings/`. All internal links go through Eleventy's `| url` filter and are automatically prefixed with `pathPrefix` (default: `/spsd-board-meetings/`).

**Custom domain**: point your DNS at GitHub Pages, set the domain in repo Settings → Pages, then set `PATH_PREFIX=/` as a repository variable (Settings → Variables → Actions) so the prefix is dropped.
