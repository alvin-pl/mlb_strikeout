"""Download pitcher game logs and team K rates for several seasons from the MLB Stats API.

Writes one JSONL file per season: logs_<season>.jsonl (one line per pitcher-game)
and team_k_rates.json. Safe to re-run: skips seasons already downloaded.
"""
import concurrent.futures as cf
import json
import os
import sys
import urllib.parse
import urllib.request

BASE = "https://statsapi.mlb.com/api/v1"
OUT = os.path.dirname(os.path.abspath(__file__))
SEASONS = [2021, 2022, 2023, 2024, 2025, 2026]


def fetch(path, params=None):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return json.load(resp)
        except Exception:
            if attempt == 3:
                raise
    return None


def innings_to_outs(ip):
    if not ip:
        return 0.0
    whole, _, frac = str(ip).partition(".")
    return float(whole or 0) * 3 + float(frac or 0)


def season_pitchers(season):
    data = fetch("/sports/1/players", {"season": season})
    ids = []
    for p in data.get("people", []):
        pos = (p.get("primaryPosition") or {}).get("abbreviation", "")
        if pos in ("P", "SP", "RP", "TWP"):
            ids.append((int(p["id"]), p.get("fullName", "")))
    return ids


def pitcher_logs(args):
    pid, name, season = args
    data = fetch(f"/people/{pid}/stats", {"stats": "gameLog", "group": "pitching", "season": season})
    stats = data.get("stats", [])
    rows = []
    for split in (stats[0].get("splits", []) if stats else []):
        stat = split.get("stat", {})
        rows.append({
            "pid": pid,
            "name": name,
            "season": season,
            "date": split.get("date", ""),
            "opp_id": int((split.get("opponent") or {}).get("id", 0)),
            "started": int(stat.get("gamesStarted") or 0),
            "ks": float(stat.get("strikeOuts") or 0),
            "bf": float(stat.get("battersFaced") or 0),
            "outs": innings_to_outs(stat.get("inningsPitched")),
        })
    return rows


def team_k_rates():
    out = {}
    for season in SEASONS:
        data = fetch("/teams", {"sportId": 1, "season": season})
        for team in data.get("teams", []):
            tid = int(team["id"])
            s = fetch(f"/teams/{tid}/stats", {"stats": "season", "group": "hitting", "season": season})
            stats = s.get("stats", [])
            splits = stats[0].get("splits", []) if stats else []
            stat = splits[0].get("stat", {}) if splits else {}
            so = float(stat.get("strikeOuts") or 0)
            pa = float(stat.get("plateAppearances") or 0)
            out[f"{season}-{tid}"] = so / pa if so > 0 and pa > 0 else None
        print(f"team rates {season} done", flush=True)
    with open(os.path.join(OUT, "team_k_rates.json"), "w") as f:
        json.dump(out, f)


def main():
    for season in SEASONS:
        path = os.path.join(OUT, f"logs_{season}.jsonl")
        if os.path.exists(path) and os.path.getsize(path) > 0:
            print(f"{season} already downloaded, skipping", flush=True)
            continue
        pitchers = season_pitchers(season)
        print(f"{season}: {len(pitchers)} pitchers", flush=True)
        tasks = [(pid, name, season) for pid, name in pitchers]
        rows = []
        with cf.ThreadPoolExecutor(max_workers=12) as pool:
            for i, result in enumerate(pool.map(pitcher_logs, tasks)):
                rows.extend(result)
                if (i + 1) % 200 == 0:
                    print(f"  {season}: {i + 1}/{len(tasks)} pitchers, {len(rows)} rows", flush=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        os.replace(tmp, path)
        print(f"{season}: wrote {len(rows)} rows", flush=True)
    team_k_rates()
    print("all done", flush=True)


if __name__ == "__main__":
    main()
