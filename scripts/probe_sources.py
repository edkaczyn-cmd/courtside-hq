"""One-off check of which draft-position and stats sources answer from GitHub's servers."""
import json, requests
H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
def show(name, url, headers=None, params=None, n=600):
    try:
        r = requests.get(url, headers={**H, **(headers or {})}, params=params, timeout=40)
        print(f"== {name}: HTTP {r.status_code}, {len(r.content)} bytes")
        print(r.text[:n].replace("\n", " "))
    except Exception as e:
        print(f"== {name}: FAILED {e}")
def espn(game, season, sort):
    f = {"players": {"limit": 3, "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": sort}}}
    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/{game}/seasons/{season}/segments/0/leaguedefaults/3"
    try:
        r = requests.get(url, params={"view": "kona_player_info"}, headers={**H, "X-Fantasy-Filter": json.dumps(f)}, timeout=40)
        print(f"== ESPN {game} {season}: HTTP {r.status_code}, {len(r.content)} bytes")
        j = r.json()
        for p in j.get("players", [])[:3]:
            pl = p.get("player", {})
            own = pl.get("ownership", {})
            st = [(s.get("seasonId"), s.get("statSourceId"), s.get("statSplitTypeId"), round(s.get("appliedTotal", 0), 1), list((s.get("stats") or {}).items())[:12]) for s in pl.get("stats", [])][:4]
            print(" ", pl.get("fullName"), pl.get("defaultPositionId"), "ADP", own.get("averageDraftPosition"), "auction", own.get("auctionValueAverage"), "rank", pl.get("draftRanksByRankType", {}).get(sort, {}).get("rank"))
            print("   stats:", st)
    except Exception as e:
        print(f"== ESPN {game} {season}: FAILED {e}")
espn("ffl", 2025, "PPR"); espn("ffl", 2026, "PPR"); espn("ffl", 2024, "PPR")
espn("fba", 2026, "STANDARD"); espn("fba", 2027, "STANDARD"); espn("fba", 2025, "STANDARD")
espn("flb", 2026, "STANDARD"); espn("flb", 2025, "STANDARD")
show("FFC ADP 2025", "https://fantasyfootballcalculator.com/api/v1/adp/half-ppr", params={"teams": 12, "year": 2025, "position": "all"})
show("FFC ADP 2026", "https://fantasyfootballcalculator.com/api/v1/adp/half-ppr", params={"teams": 12, "year": 2026, "position": "all"})
show("MLB statsapi hitting", "https://statsapi.mlb.com/api/v1/stats", params={"stats": "season", "group": "hitting", "season": 2026, "sportId": 1, "limit": 2, "playerPool": "ALL"})
show("Savant expected batters", "https://baseballsavant.mlb.com/leaderboard/expected_statistics", params={"type": "batter", "year": 2026, "position": "", "team": "", "min": "q", "csv": "true"})
show("Savant statcast batters", "https://baseballsavant.mlb.com/leaderboard/custom", params={"year": 2026, "type": "batter", "min": "q", "selections": "pa,barrel_batted_rate,hard_hit_percent,xwoba,woba", "csv": "true"})
show("Hashtag NBA ADP", "https://hashtagbasketball.com/fantasy-basketball-adp", n=300)
show("nflverse 2025 stats", "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_reg_2025.csv", n=300)
show("nflverse 2025 snaps", "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_2025.csv", n=200)
