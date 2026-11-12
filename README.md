# Courtside HQ

Personal fantasy tools for basketball (`index.html`) and football (`football.html`).

**Football:** rest-of-season rankings with points above replacement, a trade analyzer that simulates your starting lineup week by week (playoff weeks called out) and values future draft picks, buy-low/sell-high and waiver lists, a weekly lineup planner, and a mock draft for next season. Data: nflverse via `scripts/refresh_nfl.py` → `data/nfl.json`, refreshed nightly.

**Basketball:** rest-of-season rankings with punting, a trade analyzer, buy-low/sell-high and pickup lists, a weekly lineup planner and mock drafts. It runs entirely in the browser; no AI and no accounts are involved.

- **Site:** served by GitHub Pages from this repository (`index.html`).
- **Data:** `data/nba.json` is rebuilt every night by `.github/workflows/refresh.yml`, which runs `scripts/refresh.py`:
  - season stats and positions: Hashtag Basketball
  - injuries: ESPN
  - schedule: NBA.com
  - headlines: Google News RSS
  - `data/nba_prior.json` holds the 2025-26 baseline the projections blend with; it doesn't change.
- **Your league** (rosters, punts, settings) is saved in your browser. League Setup has Export/Import buttons for moving it between devices.

To refresh by hand: **Actions → Nightly data refresh → Run workflow**. If a source changes its page layout, the run logs a warning and keeps that source's previous data rather than publishing a broken file.
