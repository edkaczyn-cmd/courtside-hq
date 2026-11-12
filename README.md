# Courtside HQ

A personal fantasy basketball tool: rest-of-season rankings with punting, a trade analyzer, buy-low/sell-high and pickup lists, a weekly lineup planner and mock drafts. It runs entirely in the browser; no AI and no accounts are involved.

- **Site:** served by GitHub Pages from this repository (`index.html`).
- **Data:** `data/nba.json` is rebuilt every night by `.github/workflows/refresh.yml`, which runs `scripts/refresh.py`:
  - season stats and positions: Hashtag Basketball
  - injuries: ESPN
  - schedule: NBA.com
  - headlines: Google News RSS
  - `data/nba_prior.json` holds the 2025-26 baseline the projections blend with; it doesn't change.
- **Your league** (rosters, punts, settings) is saved in your browser. League Setup has Export/Import buttons for moving it between devices.

To refresh by hand: **Actions → Nightly data refresh → Run workflow**. If a source changes its page layout, the run logs a warning and keeps that source's previous data rather than publishing a broken file.
