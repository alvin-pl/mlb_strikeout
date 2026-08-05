#!/usr/bin/env python3
"""
MLB live game monitor for in-game betting.

Polls the public MLB Stats API during live games and pushes alerts to your
phone via ntfy.sh:

1. Win probability divergence - alerts when the live win probability of the
   team the pregame model picked drops below (or recovers above) key bands,
   so you can consider a live hedge or a better live price.
2. Live pitcher strikeouts vs. prop lines - reads your props CSV (same format
   as mlb_k_prop.py) and alerts when a pitcher is one strikeout away from the
   line, clears it, or exits the game under it.
3. General score/inning updates for games you picked - alerts on lead changes
   and scoring plays.

Usage:

    python3 mlb_live_monitor.py --ntfy-topic your-topic
    python3 mlb_live_monitor.py --ntfy-topic your-topic --props my_lines.csv
    python3 mlb_live_monitor.py --once            # single poll, for cron
    python3 mlb_live_monitor.py --duration 55 --interval 120

Alerts are deduplicated through a JSON state file (--state-file) so repeated
polls or restarts do not re-send the same alert.

This is a monitoring aid, not betting advice.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional

from mlb_win_prediction import (
    GamePrediction,
    build_prediction,
    fetch_json,
    get_games,
    get_standings,
    normalize_team,
)

LIVE_STATUS_CODES = {"I", "IR", "IH", "MA", "MC", "ME", "MF", "MG", "MI"}
FINAL_STATUS_CODES = {"F", "FR", "FT", "O"}
WIN_PROB_BANDS = [0.25, 0.35, 0.50, 0.65, 0.75]
BIG_LEAD_RUNS = 5
BIG_LEAD_INNING = 6


def load_state(path: str) -> Dict[str, Any]:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(path: str, state: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle)


def send_ntfy(topic: Optional[str], title: str, message: str, priority: str = "default", tags: str = "baseball") -> None:
    if not topic:
        print(f"[{title}] {message}")
        return
    request = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Tags": tags},
    )
    try:
        with urllib.request.urlopen(request, timeout=20):
            pass
    except OSError as exc:
        print(f"Could not send ntfy alert: {exc}", file=sys.stderr)


def read_prop_lines(path: str) -> Dict[str, Dict[str, Any]]:
    """Map normalized pitcher name -> {line, pick} from a props CSV."""
    lines: Dict[str, Dict[str, Any]] = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            pitcher = (row.get("pitcher") or "").strip()
            line_raw = (row.get("line") or "").strip()
            if not pitcher or not line_raw:
                continue
            try:
                line = float(line_raw)
            except ValueError:
                continue
            lines[normalize_team(pitcher)] = {
                "pitcher": pitcher,
                "line": line,
                "pick": (row.get("pick") or "").strip().upper(),
            }
    return lines


def get_live_feed(game_pk: int) -> Dict[str, Any]:
    request = urllib.request.Request(
        f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live",
        headers={"User-Agent": "mlb-live-monitor/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def get_live_home_win_prob(game_pk: int) -> Optional[float]:
    try:
        plays = fetch_json(f"/game/{game_pk}/winProbability", {"fields": "homeTeamWinProbability"})
    except RuntimeError:
        return None
    if isinstance(plays, list) and plays:
        value = plays[-1].get("homeTeamWinProbability")
        if value is not None:
            return float(value) / 100.0
    return None


def band_index(prob: float) -> int:
    index = 0
    for threshold in WIN_PROB_BANDS:
        if prob >= threshold:
            index += 1
    return index


def build_pregame_predictions(date: str, season: int) -> Dict[int, GamePrediction]:
    ratings = get_standings(season)
    predictions: Dict[int, GamePrediction] = {}
    for game in get_games(date):
        try:
            prediction = build_prediction(game, ratings, season)
        except RuntimeError:
            continue
        if prediction is not None:
            predictions[prediction.game_pk] = prediction
    return predictions


def pitcher_strikeouts(feed: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return [{name, strikeouts, in_game}] for all pitchers who appeared."""
    results: List[Dict[str, Any]] = []
    boxscore = feed.get("liveData", {}).get("boxscore", {})
    linescore = feed.get("liveData", {}).get("linescore", {})
    current_pitcher_id = (linescore.get("defense", {}).get("pitcher") or {}).get("id")
    for side in ("home", "away"):
        team = boxscore.get("teams", {}).get(side, {})
        for player in team.get("players", {}).values():
            stats = player.get("stats", {}).get("pitching", {})
            if not stats:
                continue
            person = player.get("person", {})
            results.append(
                {
                    "name": person.get("fullName", ""),
                    "strikeouts": int(stats.get("strikeOuts", 0) or 0),
                    "in_game": person.get("id") == current_pitcher_id,
                }
            )
    return results


def alert_once(state: Dict[str, Any], key: str, topic: Optional[str], title: str, message: str, priority: str = "default") -> None:
    sent = state.setdefault("sent", {})
    if key in sent:
        return
    send_ntfy(topic, title, message, priority=priority)
    sent[key] = True


def check_game(
    game: Dict[str, Any],
    prediction: Optional[GamePrediction],
    prop_lines: Dict[str, Dict[str, Any]],
    state: Dict[str, Any],
    topic: Optional[str],
) -> None:
    game_pk = int(game.get("gamePk", 0) or 0)
    status_code = game.get("status", {}).get("statusCode", "")
    if status_code not in LIVE_STATUS_CODES and status_code not in FINAL_STATUS_CODES:
        return

    try:
        feed = get_live_feed(game_pk)
    except OSError as exc:
        print(f"Could not fetch live feed for game {game_pk}: {exc}", file=sys.stderr)
        return

    linescore = feed.get("liveData", {}).get("linescore", {})
    game_data = feed.get("gameData", {})
    home_name = game_data.get("teams", {}).get("home", {}).get("name", "Home")
    away_name = game_data.get("teams", {}).get("away", {}).get("name", "Away")
    home_runs = int(linescore.get("teams", {}).get("home", {}).get("runs", 0) or 0)
    away_runs = int(linescore.get("teams", {}).get("away", {}).get("runs", 0) or 0)
    inning = linescore.get("currentInning", "")
    half = linescore.get("inningHalf", "")
    matchup = f"{away_name} @ {home_name}"
    score_line = f"{away_name} {away_runs} - {home_name} {home_runs} ({half} {inning})"

    game_state = state.setdefault("games", {}).setdefault(str(game_pk), {})

    # --- Score / lead-change updates for picked games ---
    picked_team = prediction.predicted_winner if prediction else None
    if picked_team and status_code in LIVE_STATUS_CODES:
        prev_score = game_state.get("score")
        if prev_score != [away_runs, home_runs]:
            if prev_score is not None:
                alert_once(
                    state,
                    f"{game_pk}:score:{away_runs}-{home_runs}",
                    topic,
                    f"Score update: {matchup}",
                    f"{score_line}\nYour pick: {picked_team}",
                )
            game_state["score"] = [away_runs, home_runs]

    # --- Big lead: team up 5+ runs in the 5th inning or later ---
    if status_code in LIVE_STATUS_CODES and isinstance(inning, int) and inning >= BIG_LEAD_INNING:
        margin = home_runs - away_runs
        if abs(margin) >= BIG_LEAD_RUNS:
            leader = home_name if margin > 0 else away_name
            note = ""
            if picked_team:
                on_pick = normalize_team(leader) == normalize_team(picked_team)
                note = f"\nYour pick: {picked_team} ({'winning' if on_pick else 'losing'})"
            alert_once(
                state,
                f"{game_pk}:big_lead:{leader}",
                topic,
                f"Expected win: {leader}",
                f"{leader} up {abs(margin)} in the {half.lower()} of the {inning}th.\n{score_line}{note}",
                priority="high",
            )

    # --- Win probability divergence for picked games ---
    if picked_team and status_code in LIVE_STATUS_CODES:
        home_prob = get_live_home_win_prob(game_pk)
        if home_prob is not None:
            pick_prob = home_prob if normalize_team(picked_team) == normalize_team(home_name) else 1 - home_prob
            pregame_prob = max(prediction.home_win_prob, prediction.away_win_prob)
            band = band_index(pick_prob)
            prev_band = game_state.get("wp_band")
            if prev_band is not None and band != prev_band:
                direction = "up" if band > prev_band else "down"
                priority = "high" if pick_prob < 0.35 else "default"
                alert_once(
                    state,
                    f"{game_pk}:wp:{band}:{direction}",
                    topic,
                    f"Win prob {direction}: {picked_team}",
                    (
                        f"{score_line}\n"
                        f"Live win prob for {picked_team}: {pick_prob * 100:.0f}% "
                        f"(pregame model: {pregame_prob * 100:.0f}%)"
                    ),
                    priority=priority,
                )
            game_state["wp_band"] = band

    # --- Pitcher strikeouts vs. prop lines ---
    if prop_lines:
        for entry in pitcher_strikeouts(feed):
            info = prop_lines.get(normalize_team(entry["name"]))
            if not info:
                continue
            ks = entry["strikeouts"]
            line = info["line"]
            name = entry["name"]
            if ks > line:
                alert_once(
                    state,
                    f"{game_pk}:k_over:{name}",
                    topic,
                    f"OVER cleared: {name}",
                    f"{name} has {ks} Ks, over the {line} line.\n{score_line}",
                    priority="high",
                )
            elif line - 1 < ks <= line:
                alert_once(
                    state,
                    f"{game_pk}:k_near:{name}",
                    topic,
                    f"1 K away: {name}",
                    f"{name} has {ks} Ks, needs 1 more to clear {line}.\n{score_line}",
                )
            if not entry["in_game"] and ks < line and status_code in LIVE_STATUS_CODES:
                alert_once(
                    state,
                    f"{game_pk}:k_out:{name}",
                    topic,
                    f"Pitcher out under line: {name}",
                    f"{name} appears out of the game with {ks} Ks (line {line}).\n{score_line}",
                    priority="high",
                )

    # --- Final score for picked games ---
    if picked_team and status_code in FINAL_STATUS_CODES:
        winner = home_name if home_runs > away_runs else away_name
        result = "HIT" if normalize_team(winner) == normalize_team(picked_team) else "MISS"
        alert_once(
            state,
            f"{game_pk}:final",
            topic,
            f"Final ({result}): {matchup}",
            f"{away_name} {away_runs} - {home_name} {home_runs}\nYour pick: {picked_team} -> {result}",
        )


def poll(date: str, season: int, predictions: Dict[int, GamePrediction], prop_lines: Dict[str, Dict[str, Any]], state: Dict[str, Any], topic: Optional[str]) -> bool:
    """Run one poll. Returns True if any game is still live or upcoming."""
    games = get_games(date)
    anything_left = False
    for game in games:
        status_code = game.get("status", {}).get("statusCode", "")
        if status_code not in FINAL_STATUS_CODES:
            anything_left = True
        check_game(game, predictions.get(int(game.get("gamePk", 0) or 0)), prop_lines, state, topic)
    return anything_left


def main() -> int:
    today = dt.date.today().isoformat()
    parser = argparse.ArgumentParser(description="Monitor live MLB games and push betting alerts via ntfy.")
    parser.add_argument("--date", default=today, help="Game date in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--season", type=int, default=dt.date.today().year, help="MLB season year.")
    parser.add_argument("--props", help="CSV of pitcher strikeout prop lines (same format as mlb_k_prop.py).")
    parser.add_argument("--ntfy-topic", default=os.environ.get("NTFY_TOPIC"), help="ntfy.sh topic. Defaults to NTFY_TOPIC env var. Prints to stdout if unset.")
    parser.add_argument("--state-file", default="live_monitor_state.json", help="JSON file used to deduplicate alerts across polls.")
    parser.add_argument("--interval", type=int, default=120, help="Seconds between polls. Default 120.")
    parser.add_argument("--duration", type=int, default=0, help="Minutes to keep polling. 0 = until all games finish.")
    parser.add_argument("--once", action="store_true", help="Poll a single time and exit (for external schedulers).")
    args = parser.parse_args()

    prop_lines = read_prop_lines(args.props) if args.props else {}
    state = load_state(args.state_file)

    try:
        predictions = build_pregame_predictions(args.date, args.season)
    except RuntimeError as exc:
        print(f"Could not build pregame predictions: {exc}", file=sys.stderr)
        predictions = {}

    deadline = time.monotonic() + args.duration * 60 if args.duration else None
    while True:
        try:
            anything_left = poll(args.date, args.season, predictions, prop_lines, state, args.ntfy_topic)
        except RuntimeError as exc:
            print(f"Poll failed: {exc}", file=sys.stderr)
            anything_left = True
        save_state(args.state_file, state)
        if args.once:
            break
        if not anything_left:
            print("All games final. Exiting.")
            break
        if deadline is not None and time.monotonic() >= deadline:
            print("Duration reached. Exiting.")
            break
        time.sleep(args.interval)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
