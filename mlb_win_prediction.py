#!/usr/bin/env python3
"""
MLB team win/loss prediction helper.

This script projects which team wins each game using public MLB Stats API
data:
- Team season record, run differential, and last-10-games form
- Probable starting pitcher season ERA
- Home field advantage

It can either:
1. Print win probability predictions for a date's games.
2. Grade a CSV of moneyline odds and estimate over/under edge.

This is a modeling aid, not betting advice. Always sanity-check probable
pitchers, lineups, injuries, and book prices before betting.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


BASE_URL = "https://statsapi.mlb.com/api/v1"
DEFAULT_LEAGUE_ERA = 4.20
HOME_FIELD_EDGE = 0.03
PITCHER_WEIGHT = 0.02
REGRESSION_GAMES = 20


@dataclass(frozen=True)
class TeamRating:
    team_id: int
    team_name: str
    wins: int
    losses: int
    games_played: int
    win_pct: float
    runs_scored: float
    runs_allowed: float
    pythag_pct: float
    recent_win_pct: Optional[float]
    rating: float


@dataclass(frozen=True)
class StarterInfo:
    pitcher_id: Optional[int]
    pitcher_name: str
    era: Optional[float]
    games_started: int


@dataclass(frozen=True)
class GamePrediction:
    date: str
    game_pk: int
    home_team: str
    away_team: str
    home_pitcher: str
    away_pitcher: str
    home_win_prob: float
    away_win_prob: float
    predicted_winner: str
    home_rating: float
    away_rating: float
    pitcher_edge: float
    confidence: str


@dataclass(frozen=True)
class MoneylineGrade:
    prediction: GamePrediction
    home_ml: Optional[float]
    away_ml: Optional[float]
    market_home_prob: Optional[float]
    probability_edge: Optional[float]
    pick: str
    lean: str
    score: float
    actual_winner: str
    result: str


def fetch_json(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    query = urllib.parse.urlencode(params or {})
    url = f"{BASE_URL}{path}"
    if query:
        url = f"{url}?{query}"

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "mlb-win-prediction-script/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"MLB API HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach MLB API for {url}: {exc.reason}") from exc


def as_float(value: Any, default: float = 0.0) -> float:
    if value in (None, "", "-.--"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def american_to_implied_probability(odds: Optional[float]) -> Optional[float]:
    if odds is None:
        return None
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


def no_vig_probs(odds_a: Optional[float], odds_b: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    prob_a = american_to_implied_probability(odds_a)
    prob_b = american_to_implied_probability(odds_b)
    if prob_a is None or prob_b is None:
        return prob_a, prob_b
    vig_sum = prob_a + prob_b
    if vig_sum <= 0:
        return prob_a, prob_b
    return prob_a / vig_sum, prob_b / vig_sum


def extract_splits(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    stats = data.get("stats", [])
    if not stats:
        return []
    return stats[0].get("splits", [])


def get_games(date: str) -> List[Dict[str, Any]]:
    data = fetch_json(
        "/schedule",
        {
            "sportId": 1,
            "date": date,
            "hydrate": "probablePitcher,team",
        },
    )
    games: List[Dict[str, Any]] = []
    for day in data.get("dates", []):
        games.extend(day.get("games", []))
    return games


def pythagorean_pct(runs_scored: float, runs_allowed: float, fallback: float) -> float:
    if runs_scored <= 0 or runs_allowed <= 0:
        return fallback
    rs_exp = runs_scored ** 1.83
    ra_exp = runs_allowed ** 1.83
    total = rs_exp + ra_exp
    if total <= 0:
        return fallback
    return rs_exp / total


def extract_last_ten_pct(team_record: Dict[str, Any]) -> Optional[float]:
    for split in team_record.get("records", {}).get("splitRecords", []):
        if split.get("type") == "lastTen":
            wins = as_float(split.get("wins"))
            losses = as_float(split.get("losses"))
            total = wins + losses
            if total > 0:
                return wins / total
    return None


def blended_rating(win_pct: float, pythag_pct: float, recent_win_pct: Optional[float], games_played: int) -> float:
    recent = recent_win_pct if recent_win_pct is not None else win_pct
    blended = 0.5 * win_pct + 0.35 * pythag_pct + 0.15 * recent
    weight = games_played / (games_played + REGRESSION_GAMES) if games_played > 0 else 0.0
    rating = 0.5 + (blended - 0.5) * weight
    return min(max(rating, 0.25), 0.75)


def get_standings(season: int) -> Dict[int, TeamRating]:
    data = fetch_json(
        "/standings",
        {"leagueId": "103,104", "season": season, "standingsTypes": "regularSeason"},
    )
    ratings: Dict[int, TeamRating] = {}
    for record in data.get("records", []):
        for team_record in record.get("teamRecords", []):
            team = team_record.get("team", {})
            team_id = int(team.get("id", 0) or 0)
            if not team_id:
                continue
            wins = int(as_float(team_record.get("wins")))
            losses = int(as_float(team_record.get("losses")))
            games_played = wins + losses
            win_pct = wins / games_played if games_played > 0 else 0.5
            runs_scored = as_float(team_record.get("runsScored"))
            runs_allowed = as_float(team_record.get("runsAllowed"))
            pythag_pct = pythagorean_pct(runs_scored, runs_allowed, fallback=win_pct)
            recent_win_pct = extract_last_ten_pct(team_record)
            rating = blended_rating(win_pct, pythag_pct, recent_win_pct, games_played)
            ratings[team_id] = TeamRating(
                team_id=team_id,
                team_name=team.get("name", ""),
                wins=wins,
                losses=losses,
                games_played=games_played,
                win_pct=win_pct,
                runs_scored=runs_scored,
                runs_allowed=runs_allowed,
                pythag_pct=pythag_pct,
                recent_win_pct=recent_win_pct,
                rating=rating,
            )
    return ratings


def get_starter_info(pitcher_id: Optional[int], pitcher_name: str, season: int) -> StarterInfo:
    if not pitcher_id:
        return StarterInfo(pitcher_id=None, pitcher_name=pitcher_name or "TBD", era=None, games_started=0)
    data = fetch_json(
        f"/people/{pitcher_id}/stats",
        {"stats": "season", "group": "pitching", "season": season},
    )
    splits = extract_splits(data)
    if not splits:
        return StarterInfo(pitcher_id=pitcher_id, pitcher_name=pitcher_name, era=None, games_started=0)
    stat = splits[0].get("stat", {})
    games_started = int(as_float(stat.get("gamesStarted")))
    era = as_float(stat.get("era"), default=-1.0)
    if era < 0 or games_started == 0:
        era = None
    return StarterInfo(pitcher_id=pitcher_id, pitcher_name=pitcher_name, era=era, games_started=games_started)


def log5(rating_a: float, rating_b: float) -> float:
    denom = rating_a + rating_b - 2 * rating_a * rating_b
    if denom <= 0:
        return 0.5
    return (rating_a - rating_a * rating_b) / denom


def era_delta(starter: StarterInfo) -> float:
    if starter.era is None or starter.games_started < 3:
        return 0.0
    return min(max(DEFAULT_LEAGUE_ERA - starter.era, -2.0), 2.0)


def pitcher_prob_shift(home_starter: StarterInfo, away_starter: StarterInfo) -> float:
    shift = (era_delta(home_starter) - era_delta(away_starter)) * PITCHER_WEIGHT
    return min(max(shift, -0.08), 0.08)


def build_prediction(game: Dict[str, Any], ratings: Dict[int, TeamRating], season: int) -> Optional[GamePrediction]:
    teams = game.get("teams", {})
    home = teams.get("home", {})
    away = teams.get("away", {})
    home_team = home.get("team", {})
    away_team = away.get("team", {})
    home_id = int(home_team.get("id", 0) or 0)
    away_id = int(away_team.get("id", 0) or 0)
    home_rating_info = ratings.get(home_id)
    away_rating_info = ratings.get(away_id)
    if not home_rating_info or not away_rating_info:
        return None

    home_probable = home.get("probablePitcher") or {}
    away_probable = away.get("probablePitcher") or {}
    home_starter = get_starter_info(
        int(home_probable["id"]) if home_probable.get("id") else None,
        home_probable.get("fullName", "TBD"),
        season,
    )
    away_starter = get_starter_info(
        int(away_probable["id"]) if away_probable.get("id") else None,
        away_probable.get("fullName", "TBD"),
        season,
    )

    base_home_prob = log5(home_rating_info.rating, away_rating_info.rating)
    edge = pitcher_prob_shift(home_starter, away_starter)
    home_prob = min(max(base_home_prob + HOME_FIELD_EDGE + edge, 0.05), 0.95)

    confidence = (
        "high"
        if (
            home_rating_info.games_played >= 20
            and away_rating_info.games_played >= 20
            and home_starter.games_started >= 4
            and away_starter.games_started >= 4
        )
        else "medium"
        if (home_rating_info.games_played >= 8 and away_rating_info.games_played >= 8)
        else "low"
    )

    predicted_winner = home_team.get("name", "Home") if home_prob >= 0.5 else away_team.get("name", "Away")

    return GamePrediction(
        date=str(game.get("gameDate", ""))[:10],
        game_pk=int(game.get("gamePk", 0) or 0),
        home_team=home_team.get("name", "Home"),
        away_team=away_team.get("name", "Away"),
        home_pitcher=home_starter.pitcher_name,
        away_pitcher=away_starter.pitcher_name,
        home_win_prob=home_prob,
        away_win_prob=1 - home_prob,
        predicted_winner=predicted_winner,
        home_rating=home_rating_info.rating,
        away_rating=away_rating_info.rating,
        pitcher_edge=edge,
        confidence=confidence,
    )


def read_moneylines(path: str) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def parse_optional_odds(value: Optional[str]) -> Optional[float]:
    if value in (None, ""):
        return None
    return as_float(value)


def format_optional_percent(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{value * 100:.1f}%"


def normalize_team(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def make_ml_lean(probability_edge: Optional[float], confidence: str) -> Tuple[str, str]:
    if confidence == "low" or probability_edge is None:
        return "", "PASS"
    if probability_edge >= 0.07:
        return "HOME", "STRONG"
    if probability_edge >= 0.04:
        return "HOME", "LEAN"
    if probability_edge <= -0.07:
        return "AWAY", "STRONG"
    if probability_edge <= -0.04:
        return "AWAY", "LEAN"
    return "", "PASS"


def grade_moneyline(
    prediction: GamePrediction,
    home_ml: Optional[float],
    away_ml: Optional[float],
    actual_winner: str = "",
) -> MoneylineGrade:
    market_home_prob, _ = no_vig_probs(home_ml, away_ml)
    probability_edge = prediction.home_win_prob - market_home_prob if market_home_prob is not None else None
    pick, lean = make_ml_lean(probability_edge, prediction.confidence)

    score = (probability_edge * 10) if probability_edge is not None else 0.0
    if prediction.confidence == "high":
        score += 0.3
    elif prediction.confidence == "low":
        score -= 0.5

    result = ""
    normalized_actual = normalize_team(actual_winner)
    if normalized_actual and pick:
        if normalized_actual in (normalize_team(prediction.home_team), "home"):
            actual_side = "HOME"
        elif normalized_actual in (normalize_team(prediction.away_team), "away"):
            actual_side = "AWAY"
        else:
            actual_side = ""
        if actual_side:
            result = "HIT" if actual_side == pick else "MISS"

    return MoneylineGrade(
        prediction=prediction,
        home_ml=home_ml,
        away_ml=away_ml,
        market_home_prob=market_home_prob,
        probability_edge=probability_edge,
        pick=pick,
        lean=lean,
        score=score,
        actual_winner=actual_winner,
        result=result,
    )


def export_prediction_template(predictions: List[GamePrediction], output_path: str) -> None:
    fieldnames = ["date", "away_team", "home_team", "away_ml", "home_ml", "actual_winner", "notes"]
    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for prediction in sorted(predictions, key=lambda item: item.date):
            writer.writerow(
                {
                    "date": prediction.date,
                    "away_team": prediction.away_team,
                    "home_team": prediction.home_team,
                    "away_ml": "",
                    "home_ml": "",
                    "actual_winner": "",
                    "notes": "",
                }
            )


def print_prediction_table(predictions: List[GamePrediction]) -> None:
    header = (
        f"{'Matchup':46} {'Home Pitcher':20} {'Away Pitcher':20} {'HomeW%':>7} "
        f"{'AwayW%':>7} {'Pick':22} {'Conf':>6}"
    )
    print(header)
    print("-" * len(header))
    for row in sorted(predictions, key=lambda item: abs(item.home_win_prob - 0.5), reverse=True):
        matchup = f"{row.away_team} @ {row.home_team}"
        print(
            f"{matchup[:46]:46} {row.home_pitcher[:20]:20} {row.away_pitcher[:20]:20} "
            f"{row.home_win_prob * 100:6.1f}% {row.away_win_prob * 100:6.1f}% "
            f"{row.predicted_winner[:22]:22} {row.confidence:>6}"
        )


def print_moneyline_grades(predictions: List[GamePrediction], moneylines_path: str) -> None:
    by_date_matchup = {
        (prediction.date, normalize_team(prediction.home_team), normalize_team(prediction.away_team)): prediction
        for prediction in predictions
    }
    by_matchup = {
        (normalize_team(prediction.home_team), normalize_team(prediction.away_team)): prediction
        for prediction in predictions
    }
    rows = read_moneylines(moneylines_path)
    grades: List[MoneylineGrade] = []

    header = (
        f"{'Date':10} {'Matchup':40} {'Pick':>5} {'ModelH%':>8} {'MktH%':>7} "
        f"{'Edge':>7} {'Score':>6} {'Lean':>7} {'Conf':>6} {'Actual':8} {'Result':6}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        date = (row.get("date") or "").strip()
        home_team = (row.get("home_team") or "").strip()
        away_team = (row.get("away_team") or "").strip()
        prediction = by_date_matchup.get((date, normalize_team(home_team), normalize_team(away_team))) if date else None
        prediction = prediction or by_matchup.get((normalize_team(home_team), normalize_team(away_team)))
        if prediction is None:
            matchup = f"{away_team} @ {home_team}"
            print(
                f"{date[:10]:10} {matchup[:40]:40} {'':>5} {'':>8} {'':>7} {'':>7} "
                f"{'':>6} {'':>7} {'':>6} {'':8} {'NO DATA':6}"
            )
            continue

        home_ml = parse_optional_odds(row.get("home_ml"))
        away_ml = parse_optional_odds(row.get("away_ml"))
        actual_winner = (row.get("actual_winner") or "").strip()
        grades.append(grade_moneyline(prediction, home_ml, away_ml, actual_winner))

    for grade in sorted(grades, key=lambda item: item.score, reverse=True):
        prediction = grade.prediction
        matchup = f"{prediction.away_team} @ {prediction.home_team}"
        print(
            f"{prediction.date[:10]:10} {matchup[:40]:40} {grade.pick or 'PASS':>5} "
            f"{prediction.home_win_prob * 100:7.1f}% {format_optional_percent(grade.market_home_prob):>7} "
            f"{format_optional_percent(grade.probability_edge):>7} {grade.score:6.2f} "
            f"{grade.lean:>7} {prediction.confidence:>6} {grade.actual_winner[:8]:8} {grade.result:6}"
        )

    finished = [grade for grade in grades if grade.result in ("HIT", "MISS")]
    if finished:
        hits = sum(1 for grade in finished if grade.result == "HIT")
        print()
        print(f"Backtest: {hits}/{len(finished)} hit ({hits / len(finished) * 100:.1f}%).")


def main() -> int:
    today = dt.date.today().isoformat()
    parser = argparse.ArgumentParser(description="Project and grade MLB team win/loss predictions.")
    parser.add_argument("--date", default=today, help="Game date in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--season", type=int, default=dt.date.today().year, help="MLB season year.")
    parser.add_argument(
        "--moneylines",
        help="CSV with moneyline odds. Required: home_team,away_team. Optional: date,home_ml,away_ml,actual_winner,notes",
    )
    parser.add_argument("--export-template", help="Write a CSV of the day's games so you can manually fill in moneylines.")
    args = parser.parse_args()

    dates = [args.date]
    if args.moneylines:
        ml_dates = sorted(
            {
                (row.get("date") or "").strip()
                for row in read_moneylines(args.moneylines)
                if (row.get("date") or "").strip()
            }
        )
        if ml_dates:
            dates = ml_dates

    try:
        ratings = get_standings(args.season)
    except RuntimeError as exc:
        print(f"Could not fetch standings: {exc}", file=sys.stderr)
        return 1

    predictions: List[GamePrediction] = []
    for date in dates:
        games = get_games(date)
        if not games:
            print(f"No games found for {date}.", file=sys.stderr)
            continue
        for game in games:
            try:
                prediction = build_prediction(game, ratings, args.season)
            except RuntimeError as exc:
                print(f"Skipping game {game.get('gamePk')}: {exc}", file=sys.stderr)
                continue
            if prediction is not None:
                predictions.append(prediction)

    if not predictions:
        print("No predictions available.")
        return 1

    if args.export_template:
        export_prediction_template(predictions, args.export_template)
        print(f"Wrote moneyline template to {args.export_template}. Fill in the odds columns, then rerun with --moneylines.")
    elif args.moneylines:
        print_moneyline_grades(predictions, args.moneylines)
    else:
        print_prediction_table(predictions)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
