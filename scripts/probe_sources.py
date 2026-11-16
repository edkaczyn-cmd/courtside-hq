import json, requests
H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
def espn_raw(game, season, sort, n=2, extra=None):
    f = {"players": {"limit": n, "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": sort}}}
    if extra: f["players"].update(extra)
    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/{game}/seasons/{season}/segments/0/leaguedefaults/3"
    r = requests.get(url, params={"view": "kona_player_info"}, headers={**H, "X-Fantasy-Filter": json.dumps(f)}, timeout=60)
    return r.json().get("players", [])
for game, season, sort in [("fba", 2026, "STANDARD"), ("flb", 2026, "STANDARD"), ("ffl", 2025, "PPR")]:
    ps = espn_raw(game, season, sort, 2, {"filterStatsForTopScoringPeriodIds": {"value": 2, "additionalValue": [f"00{season}", f"10{season}", f"02{season}"]}})
    print(f"== ESPN {game} {season}")
    for p in ps:
        pl = p["player"]; print(" ", pl["fullName"], "ranks:", {k: v.get("rank") for k, v in pl.get("draftRanksByRankType", {}).items()}, "proTeam", pl.get("proTeamId"), "elig", pl.get("eligibleSlots"))
        for s in pl.get("stats", []):
            print("   id", s.get("id"), "src", s.get("statSourceId"), "split", s.get("statSplitTypeId"), "sp", s.get("scoringPeriodId"), "applied", round(s.get("appliedTotal", 0) or 0, 1), json.dumps(s.get("stats"))[:900])
# ESPN fba 2026 bigger pull size / timing
import time
t = time.time(); ps = espn_raw("fba", 2026, "STANDARD", 400); print("== fba 400 players", len(ps), round(time.time() - t, 1), "s")
t = time.time(); ps = espn_raw("flb", 2026, "STANDARD", 600); print("== flb 600 players", len(ps), round(time.time() - t, 1), "s")
for y in (2023, 2024):
    r = requests.get("https://fantasyfootballcalculator.com/api/v1/adp/half-ppr", params={"teams": 12, "year": y, "position": "all"}, headers=H, timeout=40).json()
    print("== FFC", y, r.get("meta"), len(r.get("players", [])))
r = requests.get("https://statsapi.mlb.com/api/v1/stats", params={"stats": "season", "group": "pitching", "season": 2026, "sportId": 1, "limit": 1, "playerPool": "ALL", "sortStat": "inningsPitched", "order": "desc"}, headers=H, timeout=40).json()
print("== MLB pitching", json.dumps(r["stats"][0]["splits"][0])[:1500])
r = requests.get("https://statsapi.mlb.com/api/v1/stats", params={"stats": "season", "group": "hitting", "season": 2026, "sportId": 1, "limit": 1, "playerPool": "ALL", "sortStat": "plateAppearances", "order": "desc"}, headers=H, timeout=40).json()
print("== MLB hitting", json.dumps(r["stats"][0]["splits"][0])[:1500])
r = requests.get("https://baseballsavant.mlb.com/leaderboard/expected_statistics", params={"type": "pitcher", "year": 2026, "position": "", "team": "", "min": "100", "csv": "true"}, headers=H, timeout=40)
print("== Savant pitchers", r.status_code, r.text[:500])
r = requests.get("https://baseballsavant.mlb.com/leaderboard/custom", params={"year": 2026, "type": "pitcher", "min": "100", "selections": "pa,k_percent,bb_percent,xera,whiff_percent,barrel_batted_rate", "csv": "true"}, headers=H, timeout=40)
print("== Savant pitchers custom", r.status_code, r.text[:500])
r = requests.get("https://hashtagbasketball.com/fantasy-basketball-adp", headers=H, timeout=40)
import re; t = re.sub(r"<[^>]+>", " ", r.text); t = re.sub(r"\s+", " ", t); i = t.find("ADP"); print("== Hashtag ADP text", t[i:i+1200])
