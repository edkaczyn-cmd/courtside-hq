"""Nightly football data refresh for Courtside HQ (no AI involved).

Source: nflverse (free, open NFL data, updated nightly):
  weekly player stats, rosters, injury reports, snap counts, and the schedule.
Writes data/nfl.json. If anything essential fails, the old file is left in place.
"""
import datetime as dt
import io
import json
import sys
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import requests

CT = ZoneInfo("America/Chicago")
NOW = dt.datetime.now(CT)
SEASON = NOW.year if NOW.month >= 3 else NOW.year - 1
REL = "https://github.com/nflverse/nflverse-data/releases/download"
HEADERS = {"User-Agent": "Mozilla/5.0 (CourtsideHQ nightly refresh)"}
OUT = "data/nfl.json"
POS = ["QB", "RB", "WR", "TE"]


def log(*a):
    print(*a, flush=True)


def csv(url):
    r = requests.get(url, headers=HEADERS, timeout=120)
    r.raise_for_status()
    return pd.read_csv(io.BytesIO(r.content), low_memory=False)


def num(x):
    try:
        v = float(x)
        return 0.0 if v != v else v
    except (TypeError, ValueError):
        return 0.0


def stat_cols(df):
    df = df.copy()
    for c in ["sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost", "passing_2pt_conversions",
              "rushing_2pt_conversions", "receiving_2pt_conversions"]:
        if c not in df:
            df[c] = 0
    df["fl"] = df[["sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost"]].fillna(0).sum(axis=1)
    df["two"] = df[["passing_2pt_conversions", "rushing_2pt_conversions", "receiving_2pt_conversions"]].fillna(0).sum(axis=1)
    return df


# per-game row: att, cmp, pyd, ptd, int, car, ryd, rtd, tgt, rec, recyd, rectd, fl, two
FIELDS = ["attempts", "completions", "passing_yards", "passing_tds", "passing_interceptions", "carries", "rushing_yards",
          "rushing_tds", "targets", "receptions", "receiving_yards", "receiving_tds", "fl", "two"]


def half_ppr(r):
    return (num(r["passing_yards"]) / 25 + 4 * num(r["passing_tds"]) - 2 * num(r["passing_interceptions"])
            + (num(r["rushing_yards"]) + num(r["receiving_yards"])) / 10 + 6 * (num(r["rushing_tds"]) + num(r["receiving_tds"]))
            + 0.5 * num(r["receptions"]) - 2 * num(r["fl"]) + 2 * num(r["two"]))


def age_of(bd):
    try:
        d = dt.date.fromisoformat(str(bd)[:10])
        return round((NOW.date() - d).days / 365.25, 1)
    except Exception:  # noqa: BLE001
        return None


def headlines():
    out, seen = [], set()
    for q in ["fantasy football trade advice", "fantasy football buy low sell high", "fantasy football waiver wire",
              "fantasy football rest of season rankings"]:
        try:
            url = "https://news.google.com/rss/search?q=" + quote(q + " when:7d") + "&hl=en-US&gl=US&ceid=US:en"
            root = ET.fromstring(requests.get(url, headers=HEADERS, timeout=30).content)
        except Exception as e:  # noqa: BLE001
            log("headline query failed:", q, e)
            continue
        for it in root.iter("item"):
            title, src = (it.findtext("title") or "").strip(), (it.findtext("source") or "").strip()
            if src and title.endswith(" - " + src):
                title = title[: -len(src) - 3]
            if not title or title.lower() in seen:
                continue
            seen.add(title.lower())
            try:
                date = parsedate_to_datetime(it.findtext("pubDate")).astimezone(CT).date().isoformat()
            except Exception:  # noqa: BLE001
                date = None
            out.append(dict(title=title, url=it.findtext("link"), src=src, date=date))
    out.sort(key=lambda x: x["date"] or "", reverse=True)
    return out[:30]


def main():
    wk = stat_cols(csv(f"{REL}/stats_player/stats_player_week_{SEASON}.csv"))
    wk = wk[(wk.season_type == "REG") & wk.position.isin(POS)]
    base = stat_cols(csv(f"{REL}/stats_player/stats_player_week_{SEASON - 1}.csv"))
    base = base[(base.season_type == "REG") & base.position.isin(POS)]
    roster = csv(f"{REL}/rosters/roster_{SEASON}.csv")
    if "week" in roster:
        roster = roster.sort_values("week").drop_duplicates("gsis_id", keep="last")
    roster = roster[roster.position.isin(POS) & roster.status.isin(["ACT", "RES", "INA", "PUP", "NON", "SUS", "EXE"])]
    games = csv("https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv")
    games = games[(games.season == SEASON) & (games.game_type == "REG")]
    try:
        inj = csv(f"{REL}/injuries/injuries_{SEASON}.csv")
        inj = inj[inj.season_type == "REG"] if "season_type" in inj else inj
    except Exception as e:  # noqa: BLE001
        log("WARN injuries unavailable:", e)
        inj = pd.DataFrame()
    try:
        snaps = csv(f"{REL}/snap_counts/snap_counts_{SEASON}.csv")
        snaps = snaps[snaps.game_type == "REG"]
    except Exception as e:  # noqa: BLE001
        log("WARN snaps unavailable:", e)
        snaps = pd.DataFrame()

    # ---- schedule
    teams = sorted(set(games.home_team) | set(games.away_team))
    unplayed = games[games.home_score.isna()]
    cur_week = int(unplayed.week.min()) if len(unplayed) else int(games.week.max()) + 1
    sched = {t: {"bye": None, "opp": {}} for t in teams}
    for t in teams:
        played_weeks = set(games[(games.home_team == t) | (games.away_team == t)].week.astype(int))
        byes = [w for w in range(1, 19) if w not in played_weeks]
        sched[t]["bye"] = byes[0] if byes else None
    for _, g in unplayed.iterrows():
        w = str(int(g.week))
        sched[g.home_team]["opp"][w] = g.away_team
        sched[g.away_team]["opp"][w] = "@" + g.home_team
    log("current week", cur_week, "teams", len(teams))

    # ---- matchup multipliers (half-PPR points allowed per game, by position)
    wk["hp"] = wk.apply(half_ppr, axis=1)
    allowed = wk.groupby(["opponent_team", "position", "week"]).hp.sum().reset_index()
    per = allowed.groupby(["opponent_team", "position"]).hp.mean().unstack()
    lg = per.mean()
    dvp = {}
    for t in teams:
        dvp[t] = {}
        for p in POS:
            raw = per.loc[t, p] / lg[p] if t in per.index and p in per.columns and lg[p] > 0 else 1.0
            dvp[t][p] = round(float(min(1.2, max(0.8, 1 + 0.5 * (raw - 1)))), 3)

    # ---- snap share by gsis id
    snap_map = {}
    if len(snaps) and "pfr_id" in roster:
        pfr2gsis = dict(zip(roster.pfr_id, roster.gsis_id))
        for _, s in snaps.iterrows():
            gid = pfr2gsis.get(s.pfr_player_id)
            if gid:
                snap_map[(gid, int(s.week))] = round(num(s.offense_pct) * (100 if num(s.offense_pct) <= 1 else 1))

    # ---- injuries: latest report per player (current week preferred)
    inj_map = {}
    if len(inj):
        inj = inj[inj.week >= cur_week - 1].sort_values("week")
        for _, r in inj.iterrows():
            st = r.report_status if isinstance(r.report_status, str) else None
            if not st:
                continue
            inj_map[r.gsis_id] = dict(s=st, wk=int(r.week), body=r.report_primary_injury if isinstance(r.report_primary_injury, str) else "")

    # ---- baseline (last season per game)
    base_pg = {}
    for pid, grp in base.groupby("player_id"):
        g = len(grp)
        base_pg[pid] = [g] + [round(float(grp[f].fillna(0).sum()) / g, 3) for f in FIELDS]

    # ---- players
    players = []
    wk_by = {pid: grp.sort_values("week") for pid, grp in wk.groupby("player_id")}
    for _, r in roster.iterrows():
        pid = r.gsis_id
        grp = wk_by.get(pid)
        b = base_pg.get(pid)
        cur_ppg = grp.hp.mean() if grp is not None and len(grp) else 0
        base_hp = None
        if b and b[0] >= 4:
            d = dict(zip(FIELDS, b[1:]))
            base_hp = half_ppr(d)
        if not ((grp is not None and len(grp) and cur_ppg >= 3) or (base_hp and base_hp >= 6)):
            continue
        rows = []
        if grp is not None:
            for _, g in grp.iterrows():
                opp = g.opponent_team if isinstance(g.opponent_team, str) else ""
                rows.append([int(g.week), opp] + [round(num(g[f]), 1) for f in FIELDS] + [snap_map.get((pid, int(g.week)))])
        rec = dict(id=pid, n=r.full_name, pos=r.position, t=r.team, st=r.status,
                   sd=r.status_description_abbr if isinstance(r.get("status_description_abbr"), str) else "",
                   exp=int(num(r.years_exp)), age=age_of(r.get("birth_date")), g=rows, b=b)
        if pid in inj_map:
            rec["inj"] = inj_map[pid]
        players.append(rec)
    log("players", len(players))

    data = dict(updated=NOW.isoformat(timespec="seconds"), season=SEASON, curWeek=cur_week,
                fields=["wk", "opp"] + FIELDS + ["snap"], bfields=["g"] + FIELDS, sched=sched, dvp=dvp,
                players=players, sources=["nflverse (stats, rosters, injuries, snaps, schedule)", "Google News (headlines)"])
    try:
        data["news"] = headlines()
    except Exception as e:  # noqa: BLE001
        log("WARN headlines:", e)

    assert len(players) >= 200, "too few players"
    assert len(sched) == 32
    text = json.dumps(data, separators=(",", ":"), allow_nan=False)
    open(OUT, "w").write(text)
    log("wrote", OUT, len(text), "bytes")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log("football refresh failed, data left unchanged:", repr(e))
        sys.exit(1)
