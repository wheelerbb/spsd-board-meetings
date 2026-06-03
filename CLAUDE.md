# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

## Architecture

Eleventy 3.x static site. Input: `src/`, output: `_site/`.

```
src/
  _data/
    meetings.json   ← source of truth for all meeting metadata
    years.js        ← derives unique year list for index grouping
  _includes/layouts/
    base.njk        ← HTML shell: Google Fonts, stylesheet, nav, footer
    meeting.njk     ← extends base; renders meeting header + two-column layout
  assets/style.css  ← single shared stylesheet (passthrough-copied to _site/)
  index.njk         ← archive listing; loops over years/meetings from _data
  meetings/
    YYYY-MM-DD.njk  ← one file per meeting; front matter drives sidebar
```

## Page types

**Index** (`src/index.njk`) — hero, year-filter bar (client-side JS), grid of meeting cards. Loops `years` × `meetings | where("year", year)`.

**Meeting detail** (`src/meetings/YYYY-MM-DD.njk`) — front matter defines everything the layout needs (heading, sidebar docs, prev/next nav, video/transcript URLs). The template body is only the main column content: summary, timeline, votes. Stub pages have placeholder text bodies; full pages have the real HTML.

## Data and filters

`meetings.json` fields: `slug`, `year`, `date`, `display_date`, `day_of_week`, `type`, `title`, `topics[]`, `doc_count`, `has_video`, `has_transcript`, `stub`.

Custom Eleventy filters (`.eleventy.js`):
- `where(array, key, value)` — used in index to filter meetings by year
- `docIcon(type)` — maps `agenda`→`AGD`, `min`→`MIN`, `pdf`→`PDF` for sidebar doc badges

## Meeting front matter keys

| Key | Used by |
|-----|---------|
| `heading` | `<h1>` in meeting header; supports `<br>` and HTML entities via `\| safe` |
| `breadcrumb` | base.njk nav right side (vs. nav-links on index) |
| `meeting_tag`, `display_date`, `day_of_week`, `time`, `location`, `duration` | meeting header metadata |
| `has_video`, `video_url` | video sidebar card |
| `has_transcript`, `drive_url` | Google Drive transcript card |
| `docs[]` | meeting materials sidebar; each doc: `{ type, label, size, url }` |
| `prev`, `next` | navigation card; each: `{ slug, label }` or `null` |
| `stub` | controls copy variations in meeting.njk (e.g. "Watch Full Recording" vs "Watch Recording") |

## Design system

CSS custom properties in `assets/style.css`:
```
--navy: #0d2240   --gold: #c8963e   --gold-light: #f0c87a
--cream: #faf7f2  --warm-gray: #e8e3da  --text: #1a1a2e
--muted: #6b6b7b  --rule: #ddd6c8   --green: #2d7a4f  --green-bg: #edf7f1
```
Fonts: Playfair Display (headings) + DM Sans (body), loaded from Google Fonts.

## Adding a new meeting

1. Add an entry to `src/_data/meetings.json` (set `stub: true` initially).
2. Create `src/meetings/YYYY-MM-DD.njk` with front matter + body.
3. Update `prev`/`next` slugs on the adjacent meeting files.
4. Run `npm run build` — the index card is generated automatically from `meetings.json`.
