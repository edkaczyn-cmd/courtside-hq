"""League-winner research for Courtside HQ (no AI involved).

Same idea as the "Anatomy of a League Winner" research: find the players who beat
their draft cost by the most, learn what they had in common (draft cost, age, role
and usage), then score this season's players against those traits.

Writes data/winners_nfl.json, data/winners_nba.json and data/winners_mlb.json.
Each sport runs on its own; if one fails, its old file is kept.

Sources (all free):
  NFL  nflverse (stats, rosters), Fantasy Football Calculator (past half-PPR ADP), ESPN (current ADP)
  NBA  ESPN fantasy (ADP or preseason rank, season stat totals)
  MLB  MLB Stats API (season stats), Baseball Savant (quality of contact), ESPN fantasy (ADP or preseason rank)

Set RAW_DIR to a folder of saved responses to run without the network (for testing).
"""
import datetime as dt
import io
import json
import os
import re
import statistics as st
import sys
import traceback
import unicodedata
from zoneinfo import ZoneInfo

import pandas as pd
import requests

CT = ZoneInfo("America/Chicago")
NOW = dt.datetime.now(CT)
H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
RAW = os.environ.get("RAW_DIR")
NFLV = "https://github.com/nflverse/nflverse-data/releases/download"
TEAMS = 12


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- fetch helpers
def _raw(name):
    if RAW and os.path.exists(os.path.join(RAW, name)):
        return open(os.path.join(RAW, name), encoding="utf-8-sig").read()
    return None


def get_text(url, name, params=None, headers=None, timeout=90):
    t = _raw(name)
    if t is not None:
        return t
    r = requests.get(url, params=params, headers={**H, **(headers or {})}, timeout=timeout)
    r.raise_for_status()
    return r.text


def get_csv(url, name):
    return pd.read_csv(io.StringIO(get_text(url, name, timeout=180)), low_memory=False)


def espn(game, season, sort, n):
    f = {"players": {"limit": n, "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": sort},
                     "filterStatsForTopScoringPeriodIds": {"value": 1, "additionalValue": [f"00{season}"]}}}
    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/{game}/seasons/{season}/segments/0/leaguedefaults/3"
    t = get_text(url, f"espn_{game}_{season}.json", params={"view": "kona_player_info"},
                 headers={"X-Fantasy-Filter": json.dumps(f)})
    return [p["player"] for p in json.loads(t).get("players", [])]


def espn_cost(pl, sort):
    """Draft cost for an ESPN player: real ADP when ESPN still has it, else its preseason rank."""
    adp = (pl.get("ownership") or {}).get("averageDraftPosition")
    rank = ((pl.get("draftRanksByRankType") or {}).get(sort) or {}).get("rank")
    return adp, rank


def espn_season_totals(pl, season):
    for s in pl.get("stats") or []:
        if s.get("id") == f"00{season}" and s.get("statSourceId") == 0 and s.get("statSplitTypeId") == 0:
            return s.get("stats") or {}
    return {}


def norm(name):
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'`,-]", " ", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), 3) if xs else None


def pctl(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[i]


def r1(x, d=1):
    return None if x is None else round(float(x), d)


# ---------------------------------------------------------------- profit vs draft slot
def add_profit(rows, cost_key="cost", undrafted=None):
    """Value expected from a draft slot = the value of the player who actually finished in that slot.
    Profit = what the player produced minus what his slot is normally worth."""
    vals = sorted((r["val"] for r in rows), reverse=True)
    if not vals:
        return rows
    for i, r in enumerate(sorted(rows, key=lambda r: -r["val"])):
        r["fin"] = i + 1
    for r in rows:
        c = r.get(cost_key)
        if c is None or (undrafted and c > undrafted):
            slot_val = max(0.0, vals[min(len(vals) - 1, (undrafted or len(vals)) - 1)])
        else:
            slot_val = vals[min(len(vals) - 1, max(0, int(round(c)) - 1))]
        r["slot"] = round(slot_val, 2)
        r["profit"] = round(r["val"] - slot_val, 2)
    return rows


def round_of(c):
    return None if c is None else int((c - 1) // TEAMS) + 1


def next_cost(r):
    """Rough guess at next year's draft cost: the market prices in this year's finish, so blend it with this year's ADP."""
    fin = r.get("fin")
    if fin is None:
        return r.get("cost")
    if r.get("cost") is None:
        return float(fin)
    return round(r["cost"] ** 0.35 * fin ** 0.65, 1)   # weighted toward the finish: breakouts get priced in


def band(c, bands):
    for lo, hi, lab in bands:
        if c is not None and lo <= c <= hi:
            return lab
    return bands[-1][2]


def winners_of(rows, n=15, fin_max=60):
    w = [r for r in rows if r["fin"] <= fin_max and r["profit"] > 0]
    return sorted(w, key=lambda r: -r["profit"])[:n]


def busts_of(rows, n=10, cost_max=60):
    b = [r for r in rows if r.get("cost") is not None and r["cost"] <= cost_max]
    return sorted(b, key=lambda r: r["profit"])[:n]


def band_table(winners, pool, bands):
    out = []
    for lo, hi, lab in bands:
        wn = sum(1 for r in winners if band(r.get("cost"), bands) == lab)
        pn = sum(1 for r in pool if band(r.get("cost"), bands) == lab)
        out.append({"label": lab, "winners": wn, "winPct": round(100 * wn / max(1, len(winners))),
                    "hitRate": round(100 * wn / pn) if pn else None})
    return out


def trait_rows(winners, pool, specs):
    """specs: (key, label, fmt, direction)  direction 'hi' = winners tend to be higher."""
    out = []
    for k, lab, fmt, d in specs:
        wv = [r.get(k) for r in winners if r.get(k) is not None]
        pv = [r.get(k) for r in pool if r.get(k) is not None]
        if len(wv) < 3:
            continue
        out.append({"key": k, "label": lab, "fmt": fmt, "dir": d, "winners": med(wv), "all": med(pv), "n": len(wv)})
    return out


def write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"), allow_nan=False)
    os.replace(tmp, path)
    log("wrote", path, os.path.getsize(path), "bytes")


def clean(o):
    if hasattr(o, "item") and not isinstance(o, (list, dict, str)):
        o = o.item()               # numpy scalars from pandas
    if isinstance(o, float):
        return None if o != o else o
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o


# =============================================================== NFL
NFL_REPL = {"QB": 13, "RB": 31, "WR": 37, "TE": 13}   # replacement = first player past 12-team starters (+flex share)
NFL_BANDS = [(1, 36, "Rounds 1-3"), (37, 72, "Rounds 4-6"), (73, 120, "Rounds 7-10"), (121, 999, "Round 11+ / undrafted")]


def nfl_half(df):
    z = lambda c: df[c].fillna(0) if c in df else 0
    fl = z("sack_fumbles_lost") + z("rushing_fumbles_lost") + z("receiving_fumbles_lost")
    two = z("passing_2pt_conversions") + z("rushing_2pt_conversions") + z("receiving_2pt_conversions")
    return (z("passing_yards") / 25 + 4 * z("passing_tds") - 2 * z("passing_interceptions")
            + (z("rushing_yards") + z("receiving_yards")) / 10 + 6 * (z("rushing_tds") + z("receiving_tds"))
            + 0.5 * z("receptions") - 2 * fl + 2 * two)


def nfl_season(season):
    """Per-player season line from nflverse weekly stats (regular season)."""
    wk = get_csv(f"{NFLV}/stats_player/stats_player_week_{season}.csv", f"stats_player_week_{season}.csv")
    wk = wk[(wk.season_type == "REG") & (wk.position.isin(["QB", "RB", "WR", "TE"]))].copy()
    wk["pts"] = nfl_half(wk)
    team_tgt = wk.groupby(["team", "week"]).targets.sum()
    team_car = wk.groupby(["team", "week"]).carries.sum()
    wk["tm_tgt"] = [team_tgt.get((t, w), 0) for t, w in zip(wk.team, wk.week)]
    wk["tm_car"] = [team_car.get((t, w), 0) for t, w in zip(wk.team, wk.week)]
    last_wk = int(wk.week.max())
    team_weeks = {t: sorted(x.week.unique().tolist()) for t, x in wk.groupby("team")}
    out = {}
    for pid, g in wk.groupby("player_id"):
        g = g.sort_values("week")
        r = g.iloc[-1]
        gp = len(g)
        tgt, car = g.targets.fillna(0).sum(), g.carries.fillna(0).sum()
        po = g[g.week.isin([15, 16, 17])]
        tw = team_weeks.get(r.team, [])[-4:]          # the team's last 4 games, not his last 4 appearances
        l4 = g[(g.week.isin(tw)) & (g.team == r.team)]
        out[pid] = dict(
            id=pid, n=r.player_display_name, pos=r.position, t=r.team, gp=gp, pts=float(g.pts.sum()),
            ppg=float(g.pts.sum()) / gp, tgt=tgt, car=car,
            ts=tgt / max(1, g.tm_tgt.sum()), rs=car / max(1, g.tm_car.sum()),
            ts4=l4.targets.fillna(0).sum() / max(1, l4.tm_tgt.sum()) if len(l4) else None,
            rs4=l4.carries.fillna(0).sum() / max(1, l4.tm_car.sum()) if len(l4) else None,
            ryg=g.rushing_yards.fillna(0).sum() / gp, att=g.attempts.fillna(0).sum(),
            tds=float(g.rushing_tds.fillna(0).sum() + g.receiving_tds.fillna(0).sum() + g.passing_tds.fillna(0).sum()),
            rz=None, po_ppg=float(po.pts.mean()) if len(po) else None,
            rec=g.receptions.fillna(0).sum(), ryd=g.rushing_yards.fillna(0).sum(), recyd=g.receiving_yards.fillna(0).sum(),
            rtd=g.rushing_tds.fillna(0).sum(), rectd=g.receiving_tds.fillna(0).sum(),
            wk={int(w): (tm, float(c or 0) + float(t or 0)) for w, tm, c, t in zip(g.week, g.team, g.carries.fillna(0), g.targets.fillna(0))},
            l4n=len(l4), team_last=team_weeks.get(r.team, [])[-4:])
    return out, last_wk


def nfl_value(players):
    """Value over a replacement starter at the position, per season (half PPR, 12 teams)."""
    for pos, k in NFL_REPL.items():
        pts = sorted((p["pts"] for p in players.values() if p["pos"] == pos), reverse=True)
        repl = pts[k - 1] if len(pts) >= k else 0
        for p in players.values():
            if p["pos"] == pos:
                p["val"] = p["pts"] - repl


def nfl_ages(season):
    try:
        ro = get_csv(f"{NFLV}/rosters/roster_{season}.csv", f"roster_{season}.csv")
    except Exception as e:  # noqa: BLE001
        log("  roster", season, "failed:", e)
        return {}, {}
    ages, espn_ids = {}, {}
    start = dt.date(season, 9, 1)
    for r in ro.drop_duplicates("gsis_id").itertuples():
        try:
            bd = dt.date.fromisoformat(str(r.birth_date)[:10])
            ages[r.gsis_id] = round((start - bd).days / 365.25, 1)
        except Exception:  # noqa: BLE001
            pass
        if r.espn_id == r.espn_id and r.espn_id is not None:
            try:
                espn_ids[str(int(float(r.espn_id)))] = r.gsis_id
            except (TypeError, ValueError):
                pass
    return ages, espn_ids


def nfl_usage(p):
    """One usage number per position: what the role is worth regardless of touchdown luck."""
    if p["pos"] == "QB":
        return p["ryg"]                     # rushing yards per game: the QB league-winner trait
    if p["pos"] == "RB":
        return 100 * (p["rs"] + 0.5 * p["ts"])  # rush share plus half of target share
    return 100 * p["ts"]                    # WR/TE: target share


USAGE_LABEL = {"QB": "rush yds/game", "RB": "rush share + ½ target share", "WR": "target share %", "TE": "target share %"}


def nfl():
    seasons = [NOW.year - 3, NOW.year - 2, NOW.year - 1] if NOW.month >= 9 else [NOW.year - 4, NOW.year - 3, NOW.year - 2]
    cur = seasons[-1] + 1
    lines, ages = {}, {}
    for s in [seasons[0] - 1] + seasons + [cur]:
        try:
            lines[s], _ = nfl_season(s)
            nfl_value(lines[s])
            log("  NFL", s, len(lines[s]), "players")
        except Exception as e:  # noqa: BLE001
            log("  NFL season", s, "failed:", e)
    out_seasons, all_w, all_pool = [], [], []
    for s in seasons:
        if s not in lines:
            continue
        ffc = json.loads(get_text("https://fantasyfootballcalculator.com/api/v1/adp/half-ppr", f"ffc_{s}.json",
                                  params={"teams": TEAMS, "year": s, "position": "all"}))["players"]
        adp = {}
        for x in ffc:
            adp[(norm(x["name"]), x["position"])] = x["adp"]
        age, _ = nfl_ages(s)
        prev = lines.get(s - 1, {})
        rows = []
        for pid, p in lines[s].items():
            c = adp.get((norm(p["n"]), p["pos"]))
            if c is None and p["val"] < 0:
                continue
            pv = prev.get(pid)
            rows.append(dict(n=p["n"], pos=p["pos"], t=p["t"], cost=c, rd=round_of(c), val=round(p["val"], 1),
                             pts=round(p["pts"], 1), ppg=r1(p["ppg"]), gp=p["gp"], age=age.get(pid),
                             use=r1(nfl_usage(p)), use_prev=r1(nfl_usage(pv)) if pv else None,
                             ppg_prev=r1(pv["ppg"]) if pv else None, po=r1(p["po_ppg"])))
        add_profit(rows, undrafted=180)
        w = winners_of(rows)
        pool = [r for r in rows if r["cost"] is not None]
        for r in w:
            r["season"] = s
        all_w += w
        all_pool += pool
        out_seasons.append(dict(key=str(s), label=f"{s} season", cost="FFC half-PPR ADP, 12 teams", done=True,
                                winners=w, busts=busts_of(rows)))
        log("  NFL", s, "winners:", ", ".join(r["n"] for r in w[:6]))

    # ---- anatomy
    bands = band_table(all_w, all_pool, NFL_BANDS)
    pos_mix = {}
    for r in all_w:
        pos_mix[r["pos"]] = pos_mix.get(r["pos"], 0) + 1
    traits = trait_rows(all_w, all_pool, [("age", "Age at season start", "1", "lo"), ("rd", "Draft round", "0", "hi")])
    use_by_pos = {}
    for pos in ["QB", "RB", "WR", "TE"]:
        wv = [r["use"] for r in all_w if r["pos"] == pos and r.get("use") is not None]
        pv = [r["use"] for r in all_pool if r["pos"] == pos and r.get("use") is not None and (r.get("rd") or 99) <= 12]
        prevw = [r["use_prev"] for r in all_w if r["pos"] == pos and r.get("use_prev") is not None]
        use_by_pos[pos] = dict(label=USAGE_LABEL[pos], winners=med(wv), drafted=med(pv), winners_prev=med(prevw), n=len(wv))
    age_cut = pctl([r["age"] for r in all_w], 0.6) or 26
    rd_cut = med([r["rd"] or 16 for r in all_w]) or 6

    # ---- current season: who is beating their ADP, and who fits the profile for next year
    cur_block, targets = None, None
    if cur in lines:
        try:
            ep = espn("ffl", cur, "PPR", 450)
            age, eids = nfl_ages(cur)
            cost = {}
            for pl in ep:
                g = eids.get(str(pl["id"]))
                a, _ = espn_cost(pl, "PPR")
                if g and a and a < 170:
                    cost[g] = a
            weeks = max(p["gp"] for p in lines[cur].values())
            rows, prev = [], lines.get(cur - 1, {})
            for pid, p in lines[cur].items():
                c = cost.get(pid)
                if c is None and p["val"] < 0:
                    continue
                pv = prev.get(pid)
                u, u4 = nfl_usage(p), None
                if p["pos"] == "RB" and p["rs4"] is not None:
                    u4 = 100 * (p["rs4"] + 0.5 * (p["ts4"] or 0))
                elif p["pos"] in ("WR", "TE") and p["ts4"] is not None:
                    u4 = 100 * p["ts4"]
                rows.append(dict(n=p["n"], pos=p["pos"], t=p["t"], cost=c, rd=round_of(c), val=round(p["val"], 1),
                                 pts=round(p["pts"], 1), ppg=r1(p["ppg"]), gp=p["gp"], age=age.get(pid),
                                 use=r1(u), use4=r1(u4), use_prev=r1(nfl_usage(pv)) if pv else None, tds=p["tds"],
                                 _p=p))
            add_profit(rows, undrafted=180)
            beat = winners_of(rows, n=20)
            cur_block = dict(key=str(cur), label=f"{cur} so far", cost="ESPN ADP", done=False, weeks=weeks,
                             winners=[{k: v for k, v in r.items() if k != "_p"} for r in beat],
                             busts=[{k: v for k, v in r.items() if k != "_p"} for r in busts_of(rows)])
            # touchdown luck: TDs above what the volume usually produces
            td_rate = {}
            for pos in ["RB", "WR", "TE"]:
                ps = [r["_p"] for r in rows if r["pos"] == pos]
                td_rate[pos] = (sum(p["rtd"] for p in ps) / max(1, sum(p["car"] for p in ps)),
                                sum(p["rectd"] for p in ps) / max(1, sum(p["tgt"] for p in ps)))
            # fill-in detection: touches earned while a teammate at his position, drafted ahead of him, was out
            by_team = {}
            for pid2, p2 in lines[cur].items():
                by_team.setdefault((p2["t"], p2["pos"]), []).append((pid2, p2))
            def fill_in_share(pid, p, c):
                ahead = [p2 for pid2, p2 in by_team.get((p["t"], p["pos"]), []) if pid2 != pid
                         and cost.get(pid2) is not None and (c is None or cost[pid2] < c)]
                if not ahead:
                    return 0.0, []
                tot = fill = 0.0
                missing = set()
                for w, (tm, touches) in p["wk"].items():
                    if tm != p["t"]:
                        continue
                    out_now = [p2["n"] for p2 in ahead if w not in p2["wk"]]
                    tot += touches
                    if out_now:
                        fill += touches
                        missing.update(out_now)
                return (fill / tot if tot else 0.0), sorted(missing)
            cand, excluded = [], []
            for r in rows:
                p, pos = r["_p"], r["pos"]
                if p["gp"] < 3:
                    continue
                fshare, blockers = fill_in_share(next(k for k, v in lines[cur].items() if v is p), p, r["cost"])
                r["fill"] = round(fshare, 2)
                r["fill_by"] = blockers if fshare >= 0.5 else []
                played_recent = p["l4n"] >= 2         # played in at least 2 of his team's last 4 games
                if fshare >= 0.5 and r["use"] is not None and r["use"] >= need * 0.9:
                    log(f"    fill-in role, not counted: {r['n']} ({r['t']}) — {round(100 * fshare)}% of touches while {', '.join(r['fill_by'])} out")
                    excluded.append(dict(n=r["n"], t=r["t"], pos=pos,
                                         why=f"{round(100 * fshare)}% of his touches came while {', '.join(r['fill_by'])} (drafted ahead of him) was out"))
                ub = use_by_pos[pos]
                need = ub["winners"] if ub["winners"] is not None else 0
                exp_td = (p["car"] * td_rate[pos][0] + p["tgt"] * td_rate[pos][1]) if pos in td_rate else None
                checks = {
                    "young": r["age"] is not None and r["age"] <= age_cut,
                    # a workload that only existed while the drafted starter was out doesn't count
                    "role": r["use"] is not None and r["use"] >= need * 0.9 and fshare < 0.5,
                    "rising": played_recent and fshare < 0.5 and (
                              (r["use4"] is not None and r["use"] is not None and r["use4"] >= r["use"] * 1.1)
                              or (r["use_prev"] is not None and r["use"] is not None and r["use"] >= r["use_prev"] * 1.25)),
                    "cheap": (next_cost(r) or 999) >= 73,
                    "unlucky": exp_td is not None and p["tds"] < exp_td - 0.5,
                }
                if pos == "QB":
                    checks["unlucky"] = False
                score = sum(checks.values()) + min(1.0, max(0.0, (r["use"] or 0) / max(1, need * 1.5)))
                if sum(checks.values()) >= 3 and checks["role"] and checks["cheap"]:
                    cand.append(dict({k: v for k, v in r.items() if k != "_p"}, checks=checks, score=round(score, 2),
                                     exp_td=r1(exp_td), nxt=r1(next_cost(r), 0)))
            cand.sort(key=lambda x: -x["score"])
            targets = dict(label=f"{cur + 1} draft targets", season=cur,
                           checks=[dict(k="young", label=f"Age {age_cut:g} or younger",
                                        desc="Most past league winners were young enough to still be improving."),
                                   dict(k="role", label="League-winner workload",
                                        desc="Usage at or near what past league winners at his position had, and not mostly earned while a teammate drafted ahead of him was out."),
                                   dict(k="rising", label="Role growing",
                                        desc="Usage up over his team's last 4 games (and he played in them), or well above last season."),
                                   dict(k="cheap", label="Should still be cheap (round 7+)",
                                        desc="Next year's price, estimated from this year's ADP and finish, is round 7 or later: where 3 of 4 past league winners came from."),
                                   dict(k="unlucky", label="Touchdowns below his volume",
                                        desc="Scored fewer TDs than his touches usually produce, so his points undersell the role.")],
                           rows=cand[:30], excluded=excluded)
            log("  NFL targets:", ", ".join(x["n"] for x in cand[:8]))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            log("  NFL current season failed:", e)

    hit = {b["label"]: b for b in bands}
    late = sum(b["winners"] for b in bands[2:])
    summary = [
        f"{late} of {len(all_w)} league winners ({round(100 * late / max(1, len(all_w)))}%) were drafted in round 7 or later, or not drafted at all.",
        f"Typical league winner was {med([r['age'] for r in all_w])} years old at the start of the season (drafted players overall: {med([r['age'] for r in all_pool])}).",
        "Position mix: " + ", ".join(f"{k} {v}" for k, v in sorted(pos_mix.items(), key=lambda x: -x[1])) + ".",
    ]
    for pos in ["RB", "WR", "TE", "QB"]:
        u = use_by_pos[pos]
        if u["winners"] is not None and u["drafted"] is not None:
            summary.append(f"{pos} league winners averaged {u['winners']:.1f} ({u['label']}) vs {u['drafted']:.1f} for drafted {pos}s.")
    return dict(sport="nfl", updated=NOW.isoformat(timespec="seconds"),
                intro="League winners are the players whose production beat their draft cost by the most. Value is half-PPR points above a replacement starter at the position in a 12-team league; a draft slot is worth what the player who finished in that slot produced.",
                anatomy=dict(span=f"{seasons[0]}–{seasons[-1]}", n=len(all_w), summary=summary, bands=bands,
                             positions=pos_mix, traits=traits, usage=use_by_pos),
                seasons=([cur_block] if cur_block else []) + out_seasons[::-1], targets=targets,
                cols=[dict(k="pos", label="Pos"), dict(k="cost", label="ADP", fmt="0"), dict(k="fin", label="Finish", fmt="0"),
                      dict(k="ppg", label="PPG", fmt="1"), dict(k="age", label="Age", fmt="1"),
                      dict(k="use", label="Usage", fmt="1", tip="QB: rush yds/game · RB: rush share + ½ target share · WR/TE: target share %"),
                      dict(k="profit", label="Profit", fmt="0", tip="Value above what that draft slot normally returns")],
                sources="nflverse (stats, rosters) · Fantasy Football Calculator (past ADP) · ESPN (this year's ADP)")


# =============================================================== NBA
NBA_TEAM = {1: "ATL", 2: "BOS", 3: "NO", 4: "CHI", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GS", 10: "HOU", 11: "IND",
            12: "LAC", 13: "LAL", 14: "MIA", 15: "MIL", 16: "MIN", 17: "BKN", 18: "NY", 19: "ORL", 20: "PHI", 21: "PHO",
            22: "POR", 23: "SAC", 24: "SA", 25: "OKC", 26: "UTA", 27: "WAS", 28: "TOR", 29: "MEM", 30: "CHA"}
NBA_POS = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C"}
NBA_BANDS = [(1, 36, "Rounds 1-3"), (37, 72, "Rounds 4-6"), (73, 120, "Rounds 7-10"), (121, 9999, "Round 11+ / undrafted")]


def nba_rows(season):
    ps = espn("fba", season, "STANDARD", 500)
    rows = []
    for pl in ps:
        s = espn_season_totals(pl, season)
        gp, mn = s.get("42", 0), s.get("40", 0)
        adp, rank = espn_cost(pl, "STANDARD")   # ESPN keeps this rank after the season; it tracks real ADP closely
        rows.append(dict(id=pl["id"], n=pl["fullName"], t=NBA_TEAM.get(pl.get("proTeamId"), ""),
                         pos="/".join(NBA_POS[x] for x in sorted(pl.get("eligibleSlots") or []) if x in NBA_POS),
                         adp=adp, rank=rank, gp=gp, mn=mn, pts=s.get("0", 0), blk=s.get("1", 0), stl=s.get("2", 0),
                         ast=s.get("3", 0), reb=s.get("6", 0), to=s.get("11", 0), fgm=s.get("13", 0), fga=s.get("14", 0),
                         ftm=s.get("15", 0), fta=s.get("16", 0), tpm=s.get("17", 0)))
    return rows


def nba_value(rows):
    """9-category season value: z-scores of season totals against the top 156 by minutes (12 teams x 13)."""
    pool = sorted([r for r in rows if r["gp"] > 0], key=lambda r: -r["mn"])[:TEAMS * 13]
    if len(pool) < 30:
        for r in rows:
            r["val"] = 0
        return
    lg_fg = sum(r["fgm"] for r in pool) / max(1, sum(r["fga"] for r in pool))
    lg_ft = sum(r["ftm"] for r in pool) / max(1, sum(r["fta"] for r in pool))
    for r in rows:
        r["fgi"] = r["fgm"] - lg_fg * r["fga"]
        r["fti"] = r["ftm"] - lg_ft * r["fta"]
    cats = ["pts", "reb", "ast", "stl", "blk", "tpm", "fgi", "fti", "to"]
    mu = {c: st.mean(r[c] for r in pool) for c in cats}
    sd = {c: (st.pstdev([r[c] for r in pool]) or 1) for c in cats}
    for r in rows:
        r["val"] = sum(((r[c] - mu[c]) / sd[c]) * (-1 if c == "to" else 1) for c in cats)
    vals = sorted((r["val"] for r in rows), reverse=True)
    repl = vals[min(len(vals) - 1, TEAMS * 13 - 1)]
    for r in rows:
        r["val"] = round(r["val"] - repl, 2)
        r["mpg"] = r["mn"] / r["gp"] if r["gp"] else None
        r["usg"] = 36 * (r["fga"] + 0.44 * r["fta"] + r["to"]) / r["mn"] if r["mn"] >= 100 else None
        r["vpg"] = r["val"] / r["gp"] if r["gp"] else None


def nba():
    yr = NOW.year + (1 if NOW.month >= 10 else 0)          # season named by the year it ends
    cur = yr
    done = [yr - 2, yr - 1]
    data = {}
    for s in [yr - 3] + done + [cur]:
        try:
            data[s] = nba_rows(s)
            nba_value(data[s])
            log("  NBA", s, len(data[s]), "players")
        except Exception as e:  # noqa: BLE001
            log("  NBA", s, "failed:", e)
    out_seasons, all_w, all_pool = [], [], []
    for s in done + [cur]:
        if s not in data:
            continue
        prev = {r["id"]: r for r in data.get(s - 1, [])}
        real_adp = sum(1 for r in data[s] if r["adp"] and r["adp"] < 130) > 50
        rows = []
        for r in data[s]:
            c = r["adp"] if real_adp else r["rank"]
            if real_adp and (c is None or c >= 130):
                c = None
            if c is None and r["val"] < 0:
                continue
            pv = prev.get(r["id"])
            rows.append(dict(n=r["n"], pos=r["pos"], t=r["t"], cost=r1(c), rd=round_of(c), val=round(r["val"], 1),
                             gp=int(r["gp"]), mpg=r1(r["mpg"]), usg=r1(r["usg"]), vpg=r1(r["vpg"], 2),
                             mpg_prev=r1(pv["mpg"]) if pv and pv["gp"] >= 10 else None,
                             usg_prev=r1(pv["usg"]) if pv and pv["gp"] >= 10 else None,
                             gp_prev=int(pv["gp"]) if pv else None))
        for r in rows:
            r["dmin"] = r1(r["mpg"] - r["mpg_prev"]) if r["mpg"] is not None and r["mpg_prev"] is not None else None
            r["dusg"] = r1(r["usg"] - r["usg_prev"]) if r["usg"] is not None and r["usg_prev"] is not None else None
        if s == cur:
            # season in progress: judge by value per game so a month of games is comparable to full seasons
            for r in rows:
                r["val"] = round((r["vpg"] or 0) * 70, 1) if r["gp"] >= 5 else -99
        add_profit(rows, undrafted=156 if real_adp else 200)
        w = winners_of(rows, n=20 if s == cur else 15)
        lab = f"{s - 1}-{str(s)[2:]}"
        blk = dict(key=str(s), label=f"{lab} so far" if s == cur else f"{lab} season",
                   cost="ESPN ADP" if real_adp else "ESPN preseason rank (ESPN drops ADP after the season)",
                   done=s != cur, winners=w, busts=busts_of(rows))
        out_seasons.append(blk)
        if s != cur:
            all_w += w
            all_pool += [r for r in rows if r["cost"] is not None]
        else:
            cur_rows = rows
        log("  NBA", s, "winners:", ", ".join(r["n"] for r in w[:6]))

    bands = band_table(all_w, all_pool, NBA_BANDS)
    traits = trait_rows(all_w, all_pool, [("dmin", "Minutes per game, change from prior season", "1", "hi"),
                                          ("dusg", "Usage per 36 (shots + FTs + TOs), change from prior season", "1", "hi"),
                                          ("mpg", "Minutes per game", "1", "hi"), ("usg", "Usage per 36", "1", "hi"),
                                          ("gp", "Games played", "0", "hi"), ("rd", "Draft round", "0", "hi")])
    dmin_cut = max(2.0, med([r["dmin"] for r in all_w]) or 3)
    mpg_cut = med([r["mpg"] for r in all_w]) or 30
    rd_cut = med([r["rd"] or 14 for r in all_w]) or 6
    targets = None
    if cur in data:
        cand = []
        for r in cur_rows:
            if r["gp"] < 5 or r["mpg"] is None:
                continue
            checks = {
                "minutes": r["dmin"] is not None and r["dmin"] >= dmin_cut,
                "usage": r["dusg"] is not None and r["dusg"] >= 1.0,
                "role": r["mpg"] >= mpg_cut - 2,
                "beating": r["profit"] > 0,
                "cheap": (next_cost(r) or 999) >= 73,
            }
            score = sum(checks.values()) + max(0.0, min(1.0, (r["vpg"] or 0) / 5))
            if sum(checks.values()) >= 3 and (checks["minutes"] or checks["usage"]) and checks["cheap"]:
                cand.append(dict(r, checks=checks, score=round(score, 2), nxt=r1(next_cost(r), 0)))
        cand.sort(key=lambda x: -x["score"])
        lab = f"{cur}-{str(cur + 1)[2:]}"
        targets = dict(label=f"{lab} draft targets", season=cur,
                       checks=[dict(k="minutes", label=f"Minutes up {dmin_cut:.0f}+ per game",
                                    desc="A minutes jump was the most common trait of past league winners."),
                               dict(k="usage", label="Usage up 1+ per 36", desc="More shots, free throws and ball-handling than last season."),
                               dict(k="role", label=f"Starter minutes ({mpg_cut - 2:.0f}+)", desc="Playing the minutes past league winners played."),
                               dict(k="beating", label="Beating his draft cost", desc="Producing above what his draft slot normally returns."),
                               dict(k="cheap", label="Should still be cheap (round 7+)",
                                    desc="Next year's price, estimated from this year's ADP and his finish so far, is round 7 or later.")],
                       rows=cand[:30])
    late = sum(b["winners"] for b in bands[2:])
    summary = [
        f"{late} of {len(all_w)} league winners ({round(100 * late / max(1, len(all_w)))}%) came from round 7 or later, or went undrafted.",
        f"Typical league winner added {med([r['dmin'] for r in all_w]) or 0:+.1f} minutes per game over the prior season (drafted players overall: {med([r['dmin'] for r in all_pool]) or 0:+.1f}).",
        f"Typical league winner's usage per 36 changed by {med([r['dusg'] for r in all_w]) or 0:+.1f} (drafted players overall: {med([r['dusg'] for r in all_pool]) or 0:+.1f}).",
        f"League winners played a median {med([r['gp'] for r in all_w]):.0f} games: staying on the floor matters as much as the per-game line.",
    ]
    return dict(sport="nba", updated=NOW.isoformat(timespec="seconds"),
                intro="League winners are the players whose 9-category value beat their draft cost by the most (12 teams, season totals, so games played count). A draft slot is worth what the player who finished in that slot produced. The season in progress is judged per game.",
                anatomy=dict(span=f"{done[0] - 1}-{str(done[-1])[2:]}", n=len(all_w), summary=summary, bands=bands, traits=traits),
                seasons=out_seasons[::-1], targets=targets,
                cols=[dict(k="pos", label="Pos"), dict(k="cost", label="Cost", fmt="0", tip="ADP, or ESPN preseason rank where ADP isn't kept"),
                      dict(k="fin", label="Finish", fmt="0"), dict(k="gp", label="GP", fmt="0"), dict(k="mpg", label="MPG", fmt="1"),
                      dict(k="dmin", label="Δ MPG", fmt="+1", tip="Minutes per game vs prior season"),
                      dict(k="usg", label="Usage", fmt="1", tip="(FGA + 0.44×FTA + TO) per 36 minutes"),
                      dict(k="profit", label="Profit", fmt="1", tip="9-cat value above what that draft slot normally returns")],
                sources="ESPN fantasy (ADP, preseason ranks, season stats)")


# =============================================================== MLB
MLB_TEAM = {108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHC", 113: "CIN", 114: "CLE", 115: "COL", 116: "DET",
            117: "HOU", 118: "KC", 119: "LAD", 120: "WSH", 121: "NYM", 133: "ATH", 134: "PIT", 135: "SD", 136: "SEA", 137: "SF",
            138: "STL", 139: "TB", 140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI", 144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY",
            158: "MIL"}
MLB_BANDS = [(1, 36, "Rounds 1-3"), (37, 84, "Rounds 4-7"), (85, 180, "Rounds 8-15"), (181, 9999, "Round 16+ / undrafted")]


def mlb_stats(season, group):
    t = get_text("https://statsapi.mlb.com/api/v1/stats", f"mlb_{group}_{season}.json",
                 params={"stats": "season", "group": group, "season": season, "sportId": 1, "limit": 3000, "playerPool": "ALL"})
    out = {}
    for x in json.loads(t)["stats"][0]["splits"]:
        out[x["player"]["id"]] = dict(id=x["player"]["id"], n=x["player"]["fullName"], t=MLB_TEAM.get((x.get("team") or {}).get("id"), ""),
                                      pos=(x.get("position") or {}).get("abbreviation", ""), **x["stat"])
    return out


def savant(season, kind):
    name = {"bat": f"savant_bat_{season}.csv", "pit": f"savant_pit_{season}.csv", "pitx": f"savant_pitx_{season}.csv"}[kind]
    if kind == "bat":
        url, params = "https://baseballsavant.mlb.com/leaderboard/custom", {"year": season, "type": "batter", "min": "50", "selections": "pa,barrel_batted_rate,hard_hit_percent,xwoba,woba,sprint_speed", "csv": "true"}
    elif kind == "pit":
        url, params = "https://baseballsavant.mlb.com/leaderboard/custom", {"year": season, "type": "pitcher", "min": "50", "selections": "pa,k_percent,bb_percent,xera,whiff_percent", "csv": "true"}
    else:
        url, params = "https://baseballsavant.mlb.com/leaderboard/expected_statistics", {"type": "pitcher", "year": season, "position": "", "team": "", "min": "50", "csv": "true"}
    try:
        df = pd.read_csv(io.StringIO(get_text(url, name, params=params)))
        df.columns = [c.strip().lstrip("﻿") for c in df.columns]
        return {int(r["player_id"]): r for r in df.to_dict("records")}
    except Exception as e:  # noqa: BLE001
        log("  savant", season, kind, "failed:", e)
        return {}


def fnum(x):
    try:
        v = float(str(x).replace("-.--", "nan"))
        return None if v != v else v
    except (TypeError, ValueError):
        return None


def ip_of(x):
    s = str(x or "0")
    whole, _, frac = s.partition(".")
    return int(whole or 0) + int(frac or 0) / 3


def mlb_value(hit, pit):
    """5x5 roto value. Hitters: R HR RBI SB AVG. Pitchers: W SV K ERA WHIP.
    z-scores within each group, minus the replacement level of a 12-team league (13 hitters, 9 pitchers)."""
    H_, P_ = list(hit.values()), list(pit.values())
    for h in H_:
        h["PA"] = h.get("plateAppearances", 0)
        h["AB"], h["Hh"] = h.get("atBats", 0), h.get("hits", 0)
    for p in P_:
        p["IP"] = ip_of(p.get("inningsPitched"))
        p["ER"], p["WH"] = p.get("earnedRuns", 0), p.get("hits", 0) + p.get("baseOnBalls", 0)
    hp = sorted(H_, key=lambda h: -h["PA"])[:TEAMS * 14]
    pp = sorted(P_, key=lambda p: -(p["IP"] + 3 * p.get("saves", 0)))[:TEAMS * 10]
    if len(hp) < 50 or len(pp) < 50:
        raise RuntimeError("not enough MLB players")
    lg_avg = sum(h["Hh"] for h in hp) / max(1, sum(h["AB"] for h in hp))
    lg_era = 9 * sum(p["ER"] for p in pp) / max(1, sum(p["IP"] for p in pp))
    lg_whip = sum(p["WH"] for p in pp) / max(1, sum(p["IP"] for p in pp))
    for h in H_:
        h["avgi"] = h["Hh"] - lg_avg * h["AB"]
    for p in P_:
        p["erai"] = (lg_era * p["IP"] / 9) - p["ER"]
        p["whipi"] = lg_whip * p["IP"] - p["WH"]

    def z(group, pool, cats):
        mu = {c: st.mean(x.get(c, 0) for x in pool) for c in cats}
        sd = {c: (st.pstdev([x.get(c, 0) for x in pool]) or 1) for c in cats}
        for x in group:
            x["val"] = sum((x.get(c, 0) - mu[c]) / sd[c] for c in cats)
    z(H_, hp, ["runs", "homeRuns", "rbi", "stolenBases", "avgi"])
    z(P_, pp, ["wins", "saves", "strikeOuts", "erai", "whipi"])
    hv = sorted((h["val"] for h in H_), reverse=True)
    pv = sorted((p["val"] for p in P_), reverse=True)
    hr, pr = hv[TEAMS * 13 - 1], pv[TEAMS * 9 - 1]
    for h in H_:
        h["val"] -= hr
    for p in P_:
        p["val"] -= pr


def mlb():
    cur_done = NOW.year if NOW.month >= 10 else NOW.year - 1
    seasons = [cur_done - 1, cur_done]
    hit, pit, sv_b, sv_p, sv_x = {}, {}, {}, {}, {}
    for s in [seasons[0] - 1] + seasons:
        hit[s], pit[s] = mlb_stats(s, "hitting"), mlb_stats(s, "pitching")
        mlb_value(hit[s], pit[s])
        sv_b[s], sv_p[s], sv_x[s] = savant(s, "bat"), savant(s, "pit"), savant(s, "pitx")
        log("  MLB", s, len(hit[s]), "hitters", len(pit[s]), "pitchers")
    out_seasons, all_w, all_pool, last_rows = [], [], [], []
    for s in seasons:
        ep = espn("flb", s, "STANDARD", 800)
        real_adp = sum(1 for pl in ep if (pl.get("ownership") or {}).get("averageDraftPosition", 999) < 250) > 100
        cost = {}
        for pl in ep:
            a, rk = espn_cost(pl, "STANDARD")
            c = (a if a and a < 250 else None) if real_adp else rk
            if c is not None:
                cost.setdefault(norm(pl["fullName"]), c)
        rows = []
        for kind, src in (("H", hit[s]), ("P", pit[s])):
            for pid, x in src.items():
                c = cost.get(norm(x["n"]))
                if kind == "H" and x.get("plateAppearances", 0) < 50 and c is None:
                    continue
                if kind == "P" and x["IP"] < 15 and c is None:
                    continue
                if c is None and x["val"] < 0:
                    continue
                prev = (hit if kind == "H" else pit).get(s - 1, {}).get(pid)
                row = dict(n=x["n"], t=x["t"], pos=("SP" if x.get("gamesStarted", 0) >= 5 else "RP") if kind == "P" else (x["pos"] or "DH"),
                           kind=kind, cost=r1(c), rd=round_of(c), val=round(x["val"], 2), age=x.get("age"),
                           val_prev=r1(prev["val"], 2) if prev else None)
                if kind == "H":
                    b, bp = sv_b[s].get(pid, {}), sv_b.get(s - 1, {}).get(pid, {})
                    row.update(pa=x.get("plateAppearances"), hr=x.get("homeRuns"), sb=x.get("stolenBases"), avg=fnum(x.get("avg")),
                               brl=fnum(b.get("barrel_batted_rate")), xw=fnum(b.get("xwoba")), w=fnum(b.get("woba")),
                               spd=fnum(b.get("sprint_speed")),
                               pa_prev=prev.get("plateAppearances") if prev else None,
                               brl_prev=fnum(bp.get("barrel_batted_rate")), xw_prev=fnum(bp.get("xwoba")), w_prev=fnum(bp.get("woba")))
                    row["xgap"] = r1((row["xw"] - row["w"]) * 1000, 0) if row["xw"] is not None and row["w"] is not None else None
                    row["xgap_prev"] = r1((row["xw_prev"] - row["w_prev"]) * 1000, 0) if row["xw_prev"] is not None and row["w_prev"] is not None else None
                else:
                    b, xx = sv_p[s].get(pid, {}), sv_x[s].get(pid, {})
                    bp = sv_p.get(s - 1, {}).get(pid, {})
                    k, bb = fnum(b.get("k_percent")), fnum(b.get("bb_percent"))
                    kp, bbp = fnum(bp.get("k_percent")), fnum(bp.get("bb_percent"))
                    row.update(ip=r1(x["IP"]), era=fnum(x.get("era")), whip=fnum(x.get("whip")), so=x.get("strikeOuts"), sv=x.get("saves"),
                               kbb=r1(k - bb) if k is not None and bb is not None else None,
                               kbb_prev=r1(kp - bbp) if kp is not None and bbp is not None else None,
                               xera=fnum(xx.get("xera")), ip_prev=r1(prev["IP"]) if prev else None)
                rows.append(row)
        # two-way players (Ohtani): the draft cost belongs to the more valuable side only
        by_name = {}
        for r in rows:
            by_name.setdefault(r["n"], []).append(r)
        for same in by_name.values():
            if len(same) > 1:
                same.sort(key=lambda r: -r["val"])
                for r in same[1:]:
                    r["cost"], r["rd"] = None, None
        rows = [r for r in rows if r["cost"] is not None or r["val"] >= 0]
        add_profit(rows, undrafted=TEAMS * 23 if real_adp else 300)
        w = winners_of(rows, n=20, fin_max=100)
        out_seasons.append(dict(key=str(s), label=f"{s} season",
                                cost="ESPN ADP" if real_adp else "ESPN preseason rank (ESPN drops ADP after the season)",
                                done=True, winners=w, busts=busts_of(rows, cost_max=72)))
        all_w += w
        all_pool += [r for r in rows if r["cost"] is not None]
        last_rows = rows
        log("  MLB", s, "winners:", ", ".join(r["n"] for r in w[:6]))

    hw, hpool = [r for r in all_w if r["kind"] == "H"], [r for r in all_pool if r["kind"] == "H"]
    pw, ppool = [r for r in all_w if r["kind"] == "P"], [r for r in all_pool if r["kind"] == "P"]
    traits = trait_rows(hw, hpool, [("age", "Hitters: age", "0", "lo"), ("brl", "Hitters: barrel %", "1", "hi"),
                                    ("xgap_prev", "Hitters: prior-year xwOBA minus wOBA (×1000, + = unlucky)", "0", "hi"),
                                    ("pa", "Hitters: plate appearances", "0", "hi"), ("sb", "Hitters: stolen bases", "0", "hi")]) + \
        trait_rows(pw, ppool, [("age", "Pitchers: age", "0", "lo"), ("kbb", "Pitchers: K% minus BB%", "1", "hi"),
                               ("kbb_prev", "Pitchers: prior-year K% minus BB%", "1", "hi"), ("ip", "Pitchers: innings", "0", "hi")])
    bands = band_table(all_w, all_pool, MLB_BANDS)
    age_h = pctl([r["age"] for r in hw], 0.6) or 27
    age_p = pctl([r["age"] for r in pw], 0.6) or 28
    brl_cut = med([r["brl"] for r in hw]) or 10
    kbb_cut = med([r["kbb"] for r in pw]) or 18
    cand = []
    for r in last_rows:
        if r["kind"] == "H":
            if (r.get("pa") or 0) < 250:
                continue
            checks = {"young": r["age"] is not None and r["age"] <= age_h,
                      "skills": r["brl"] is not None and r["brl"] >= brl_cut * 0.85 or (r.get("sb") or 0) >= 25,
                      "unlucky": r["xgap"] is not None and r["xgap"] >= 10,
                      "playing": (r.get("pa") or 0) >= 450,
                      "cheap": (next_cost(r) or 999) >= 85}
        else:
            if (r.get("ip") or 0) < 40:
                continue
            checks = {"young": r["age"] is not None and r["age"] <= age_p,
                      "skills": r["kbb"] is not None and r["kbb"] >= kbb_cut * 0.9,
                      "unlucky": r["xera"] is not None and r["era"] is not None and r["era"] - r["xera"] >= 0.3,
                      "playing": (r.get("ip") or 0) >= 120 or (r.get("sv") or 0) >= 15,
                      "cheap": (next_cost(r) or 999) >= 85}
        n = sum(checks.values())
        if n >= 3 and checks["skills"] and checks["cheap"]:
            cand.append(dict(r, checks=checks, score=round(n + max(0, min(1, (r["val"] + 3) / 6)), 2), nxt=r1(next_cost(r), 0)))
    cand.sort(key=lambda x: -x["score"])
    late = sum(b["winners"] for b in bands[2:])
    summary = [
        f"{late} of {len(all_w)} league winners ({round(100 * late / max(1, len(all_w)))}%) were drafted after round 7 or went undrafted.",
        f"Hitter league winners: median age {med([r['age'] for r in hw])}, barrel rate {med([r['brl'] for r in hw])}% (drafted hitters overall: {med([r['brl'] for r in hpool])}%).",
        f"Pitcher league winners: median age {med([r['age'] for r in pw])}, K-BB% {med([r['kbb'] for r in pw])} (drafted pitchers overall: {med([r['kbb'] for r in ppool])}).",
        f"{sum(1 for r in hw if (r.get('xgap_prev') or 0) >= 10)} of {len(hw)} hitter league winners had a prior-year xwOBA at least 10 points above their actual wOBA: bad luck the year before is a buying signal.",
    ]
    return dict(sport="mlb", updated=NOW.isoformat(timespec="seconds"),
                intro="League winners are the players whose 5x5 roto value (R, HR, RBI, SB, AVG / W, SV, K, ERA, WHIP) beat their draft cost by the most in a 12-team league. A draft slot is worth what the player who finished in that slot produced.",
                anatomy=dict(span=f"{seasons[0]}–{seasons[-1]}", n=len(all_w), summary=summary, bands=bands, traits=traits),
                seasons=out_seasons[::-1],
                targets=dict(label=f"{cur_done + 1} draft targets", season=cur_done,
                             checks=[dict(k="young", label=f"Young (hitters {age_h}-, pitchers {age_p}-)", desc="Past league winners skewed young."),
                                     dict(k="skills", label=f"League-winner skills (barrel {brl_cut * 0.85:.0f}%+ or 25+ SB / K-BB {kbb_cut * 0.9:.0f}+)",
                                          desc="Quality of contact or speed for hitters, strikeout-minus-walk rate for pitchers."),
                                     dict(k="unlucky", label="Unlucky this year", desc="Hitters: xwOBA 10+ points over wOBA. Pitchers: ERA 0.30+ above xERA."),
                                     dict(k="playing", label="Everyday role", desc="450+ PA, 120+ IP, or 15+ saves."),
                                     dict(k="cheap", label="Should still be cheap (round 8+)", desc="Next year's price, estimated from this year's draft cost and finish, is round 8 or later.")],
                             rows=cand[:40]),
                cols=[dict(k="pos", label="Pos"), dict(k="cost", label="Cost", fmt="0", tip="ADP, or ESPN preseason rank where ADP isn't kept"),
                      dict(k="fin", label="Finish", fmt="0"), dict(k="age", label="Age", fmt="0"),
                      dict(k="brl", label="Brl% / K-BB", fmt="1", alt="kbb", tip="Hitters: barrel rate · Pitchers: K% minus BB%"),
                      dict(k="xgap", label="xwOBA gap / ERA-xERA", fmt="0", tip="Hitters: (xwOBA − wOBA)×1000 · Pitchers: ERA − xERA"),
                      dict(k="profit", label="Profit", fmt="1", tip="Roto value above what that draft slot normally returns")],
                sources="MLB Stats API (stats) · Baseball Savant (barrels, xwOBA, xERA, K%, BB%) · ESPN fantasy (ADP, preseason ranks)")


def main():
    ok = 0
    for name, fn in (("nfl", nfl), ("nba", nba), ("mlb", mlb)):
        log(f"== {name.upper()}")
        try:
            out = clean(fn())
            if not out.get("seasons"):
                raise RuntimeError("no seasons built")
            if name == "mlb":
                for s in out["seasons"]:
                    for r in s["winners"] + s["busts"]:
                        if r["kind"] == "P":
                            r["brl"] = r.get("kbb")
                            r["xgap"] = r1((r["era"] - r["xera"]), 2) if r.get("era") is not None and r.get("xera") is not None else None
                for r in (out.get("targets") or {}).get("rows", []):
                    if r["kind"] == "P":
                        r["brl"] = r.get("kbb")
                        r["xgap"] = r1((r["era"] - r["xera"]), 2) if r.get("era") is not None and r.get("xera") is not None else None
            write(f"data/winners_{name}.json", out)
            ok += 1
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            log(f"{name} failed, keeping the previous file:", e)
    log(f"done: {ok}/3 sports")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
