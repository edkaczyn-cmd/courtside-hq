"""Temporary: download raw source files so the league-winner script can be built offline."""
import json, os, requests
H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
os.makedirs("raw", exist_ok=True)
def save(name, text): open(f"raw/{name}", "w").write(text); print("saved", name, len(text))
def espn(game, season, sort, n):
    f = {"players": {"limit": n, "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": sort},
                     "filterStatsForTopScoringPeriodIds": {"value": 1, "additionalValue": [f"00{season}"]}}}
    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/{game}/seasons/{season}/segments/0/leaguedefaults/3"
    r = requests.get(url, params={"view": "kona_player_info"}, headers={**H, "X-Fantasy-Filter": json.dumps(f)}, timeout=90)
    save(f"espn_{game}_{season}.json", r.text)
for s in (2024, 2025, 2026, 2027): espn("fba", s, "ROTO", 500)
for s in (2025, 2026): espn("flb", s, "ROTO", 800)
espn("ffl", 2026, "PPR", 450)
for y in (2023, 2024, 2025):
    save(f"ffc_{y}.json", requests.get("https://fantasyfootballcalculator.com/api/v1/adp/half-ppr", params={"teams": 12, "year": y, "position": "all"}, headers=H, timeout=60).text)
for s in (2024, 2025, 2026):
    for g in ("hitting", "pitching"):
        save(f"mlb_{g}_{s}.json", requests.get("https://statsapi.mlb.com/api/v1/stats", params={"stats": "season", "group": g, "season": s, "sportId": 1, "limit": 3000, "playerPool": "ALL"}, headers=H, timeout=90).text)
    save(f"savant_bat_{s}.csv", requests.get("https://baseballsavant.mlb.com/leaderboard/custom", params={"year": s, "type": "batter", "min": "50", "selections": "pa,barrel_batted_rate,hard_hit_percent,xwoba,woba,sprint_speed", "csv": "true"}, headers=H, timeout=90).text)
    save(f"savant_pit_{s}.csv", requests.get("https://baseballsavant.mlb.com/leaderboard/custom", params={"year": s, "type": "pitcher", "min": "50", "selections": "pa,k_percent,bb_percent,xera,whiff_percent", "csv": "true"}, headers=H, timeout=90).text)
    save(f"savant_pitx_{s}.csv", requests.get("https://baseballsavant.mlb.com/leaderboard/expected_statistics", params={"type": "pitcher", "year": s, "position": "", "team": "", "min": "50", "csv": "true"}, headers=H, timeout=90).text)
