"""Nightly data refresh for Courtside HQ (no AI involved).

Pulls season stats (Hashtag Basketball), injuries (ESPN), the schedule (NBA.com)
and fantasy headlines (Google News RSS), then rewrites data/nba.json.
Each source is independent: if one fails, its previous data is kept, and the
run never writes a file that fails validation.
"""
import datetime as dt
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

CT = ZoneInfo("America/Chicago")
NOW = dt.datetime.now(CT)
TODAY = NOW.date()
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
PATH = "data/nba.json"

TEAMS = "ATL BKN BOS CHA CHI CLE DAL DEN DET GS HOU IND LAC LAL MEM MIA MIL MIN NO NY OKC ORL PHI PHO POR SAC SA TOR UTA WAS".split()
# Other sites' team codes -> ours (Hashtag Basketball style)
ALIAS = {"GSW": "GS", "NOP": "NO", "NOR": "NO", "NYK": "NY", "SAS": "SA", "PHX": "PHO", "BRK": "BKN", "BRO": "BKN",
         "CHO": "CHA", "UTAH": "UTA", "UTH": "UTA", "WSH": "WAS", "NJ": "BKN", "SAN": "SA"}
FULL = {"Atlanta Hawks": "ATL", "Brooklyn Nets": "BKN", "Boston Celtics": "BOS", "Charlotte Hornets": "CHA",
        "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
        "Detroit Pistons": "DET", "Golden State Warriors": "GS", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
        "LA Clippers": "LAC", "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
        "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NO",
        "New York Knicks": "NY", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
        "Phoenix Suns": "PHO", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SA",
        "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS"}
STATS = ["gp", "mpg", "fgm", "fga", "ftm", "fta", "tpm", "pts", "reb", "ast", "stl", "blk", "to"]


def team(code):
    code = (code or "").strip().upper()
    code = ALIAS.get(code, code)
    return code if code in TEAMS else None


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"['’.`]", "", s)
    s = re.sub(r"[^a-z]+", " ", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def first_num(text):
    m = re.search(r"-?\d*\.?\d+", text.replace(",", ""))
    return float(m.group()) if m else None


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- players
def fetch_players():
    r = requests.get("https://hashtagbasketball.com/fantasy-basketball-rankings", headers=HEADERS, timeout=40)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    table, cols = None, None
    for t in soup.find_all("table"):
        head = t.find("tr")
        if not head:
            continue
        hdr = [c.get_text(" ", strip=True).upper() for c in head.find_all(["th", "td"])]
        if "PLAYER" in hdr and "TREB" in hdr and "AST" in hdr:
            table, cols = t, hdr
            break
    if table is None:
        raise RuntimeError("rankings table not found")
    ix = {name: cols.index(name) for name in ["PLAYER", "POS", "TEAM", "GP", "MPG", "FG%", "FT%", "3PM", "PTS", "TREB", "AST", "STL", "BLK", "TO"]}
    players = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < len(cols):
            continue
        cell = lambda k: tds[ix[k]]
        txt = lambda k: cell(k).get_text(" ", strip=True)
        if not re.match(r"^\d", tds[0].get_text(" ", strip=True)):
            continue  # repeated header rows
        a = cell("PLAYER").find("a")
        name = (a.get_text(" ", strip=True) if a else txt("PLAYER")).strip()
        tm = team(txt("TEAM"))
        if not name or not tm:
            continue
        pos = "/".join(p.strip() for p in re.split(r"[,/]", txt("POS")) if p.strip())

        def made_att(k):
            m = re.search(r"\(\s*([\d.]+)\s*/\s*([\d.]+)\s*\)", txt(k))
            return (float(m.group(1)), float(m.group(2))) if m else (None, None)

        fgm, fga = made_att("FG%")
        ftm, fta = made_att("FT%")
        row = dict(n=name, pos=pos, t=tm, gp=first_num(txt("GP")), mpg=first_num(txt("MPG")), fgm=fgm, fga=fga,
                   ftm=ftm, fta=fta, tpm=first_num(txt("3PM")), pts=first_num(txt("PTS")), reb=first_num(txt("TREB")),
                   ast=first_num(txt("AST")), stl=first_num(txt("STL")), blk=first_num(txt("BLK")), to=first_num(txt("TO")))
        if any(row[k] is None for k in STATS):
            continue
        players.append(row)
    if len(players) < 150:
        raise RuntimeError(f"only {len(players)} players parsed")
    return players


# ---------------------------------------------------------------- injuries
def fetch_injuries():
    j = requests.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries", headers=HEADERS, timeout=40).json()
    out = []
    for grp in j.get("injuries", []):
        grp_team = FULL.get(grp.get("displayName", "")) or team(grp.get("abbreviation"))
        for inj in grp.get("injuries", []):
            ath = inj.get("athlete") or {}
            name = ath.get("displayName") or ath.get("fullName")
            tm = team((ath.get("team") or {}).get("abbreviation")) or grp_team
            if not name or not tm:
                continue
            status = (inj.get("status") or (inj.get("type") or {}).get("description") or "").lower()
            details = inj.get("details") or {}
            body = details.get("type") or details.get("location") or "Injury"
            comment = (inj.get("longComment") or inj.get("shortComment") or "").lower()
            row = dict(n=name, t=tm, inj=body)
            if "out" in status or "injured reserve" in status or "suspend" in status:
                row["s"] = "OUT"
                ret = details.get("returnDate")
                if "season" in comment and ("out for" in comment or "remainder" in comment):
                    row["ret"] = f"{TODAY.year + (1 if TODAY.month >= 7 else 0)}-04-15"
                elif ret:
                    row["ret"] = ret[:10]
                else:
                    row["ret"] = (TODAY + dt.timedelta(days=7)).isoformat()
            else:  # day-to-day, questionable, doubtful, probable
                row["s"] = "GTD"
            out.append(row)
    if not out:
        raise RuntimeError("no injuries parsed")
    return out


# ---------------------------------------------------------------- schedule
def fetch_schedule():
    j = requests.get("https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json", headers=HEADERS, timeout=60).json()
    monday = TODAY - dt.timedelta(days=TODAY.weekday())
    days = {t: [0] * 7 for t in TEAMS}
    played = {t: 0 for t in TEAMS}
    for gd in j["leagueSchedule"]["gameDates"]:
        for g in gd.get("games", []):
            if not str(g.get("gameId", "")).startswith("002"):  # regular season only
                continue
            d = dt.date.fromisoformat((g.get("gameDateEst") or g.get("gameDateTimeEst"))[:10])
            for side in ("homeTeam", "awayTeam"):
                tm = team((g.get(side) or {}).get("teamTricode"))
                if not tm:
                    continue
                if g.get("gameStatus") == 3:
                    played[tm] += 1
                off = (d - monday).days
                if 0 <= off < 7:
                    days[tm][off] = 1
    if sum(map(sum, days.values())) == 0 and TODAY.month not in (7, 8, 9):
        raise RuntimeError("empty week")
    return dict(start=monday.isoformat(), days=days), played


# ---------------------------------------------------------------- headlines
def fetch_news():
    queries = ["fantasy basketball waiver wire", "fantasy basketball buy low sell high", "fantasy basketball pickups",
               "fantasy basketball trade targets", "Locked On Fantasy Basketball", "fantasy basketball rankings"]
    seen, items = set(), []
    for q in queries:
        url = "https://news.google.com/rss/search?q=" + quote(q + " when:7d") + "&hl=en-US&gl=US&ceid=US:en"
        try:
            root = ET.fromstring(requests.get(url, headers=HEADERS, timeout=30).content)
        except Exception as e:  # noqa: BLE001
            log("news query failed:", q, e)
            continue
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            src = (it.findtext("source") or "").strip()
            if src and title.endswith(" - " + src):
                title = title[: -len(src) - 3]
            key = norm(title)
            if not title or key in seen:
                continue
            seen.add(key)
            try:
                date = parsedate_to_datetime(it.findtext("pubDate")).astimezone(CT).date().isoformat()
            except Exception:  # noqa: BLE001
                date = None
            items.append(dict(title=title, url=it.findtext("link"), src=src, date=date))
    items.sort(key=lambda x: x["date"] or "", reverse=True)
    return items[:30]


# ---------------------------------------------------------------- main
def main():
    old = json.load(open(PATH))
    new = dict(old)

    try:
        new["injuries"] = fetch_injuries()
        log("injuries:", len(new["injuries"]))
    except Exception as e:  # noqa: BLE001
        log("WARN injuries kept from last run:", e)
        new["injuries"] = [i for i in old.get("injuries", []) if i.get("s") != "OUT" or (i.get("ret") or "9999") >= TODAY.isoformat()]

    try:
        players = fetch_players()
        log("players:", len(players))
        have = {norm(p["n"]) for p in players}
        injured = {norm(i["n"]) for i in new["injuries"] if i.get("s") == "OUT"}
        for p in old.get("players", []):  # keep injured regulars who dropped out of the top 200
            k = norm(p["n"])
            if k not in have and k in injured:
                players.append(p)
        new["players"] = players
    except Exception as e:  # noqa: BLE001
        log("WARN player stats kept from last run:", e)

    try:
        new["week"], played = fetch_schedule()
        new["teamGP"] = {t: played[t] for t in TEAMS}
        log("week of", new["week"]["start"])
    except Exception as e:  # noqa: BLE001
        log("WARN schedule kept from last run:", e)
        gp = {}
        for p in new["players"]:
            gp[p["t"]] = max(gp.get(p["t"], 0), p.get("gp") or 0)
        new["teamGP"] = {t: max(gp.get(t, 0), old.get("teamGP", {}).get(t, 0)) for t in TEAMS}

    try:
        news = fetch_news()
        if news:
            new["news"] = news
        log("headlines:", len(news))
    except Exception as e:  # noqa: BLE001
        log("WARN headlines kept from last run:", e)

    cutoff = (TODAY - dt.timedelta(days=21)).isoformat()
    new["buzz"] = [b for b in old.get("buzz", []) if (b.get("date") or "") >= cutoff]
    new["sources"] = ["Hashtag Basketball (season stats & positions)", "Basketball-Reference (2025-26 baseline)",
                      "ESPN (injuries)", "NBA.com (schedule)", "Google News (headlines)"]
    new["updated"] = NOW.isoformat(timespec="seconds")

    # validation: never publish a broken file
    assert len(new["players"]) >= 150, "too few players"
    for p in new["players"]:
        assert p["t"] in TEAMS and all(isinstance(p[k], (int, float)) for k in STATS), p
    assert set(new["week"]["days"]) >= set(TEAMS) and set(new["teamGP"]) >= set(TEAMS)
    text = json.dumps(new, separators=(",", ":"), allow_nan=False)
    open(PATH, "w").write(text)
    log("wrote", PATH, len(text), "bytes")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log("refresh failed, data left unchanged:", e)
        sys.exit(1)
