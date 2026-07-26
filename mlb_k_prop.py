#!/usr/bin/env python3
"""
MLB strikeout prop helper.

This script projects pitcher strikeouts using public MLB Stats API data:
- Today's games and probable pitchers
- Pitcher season and recent game logs
- Opponent team strikeout tendency

It can either:
1. Print projections for probable starters on a date.
2. Grade a CSV of strikeout props and estimate over/under edge.

This is a modeling aid, not betting advice. Always sanity-check probable
pitchers, lineups, weather, pitch-count news, and book prices before betting.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import statistics
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


BASE_URL = "https://statsapi.mlb.com/api/v1"
DEFAULT_LEAGUE_K_RATE = 0.222

# Single-game strikeout counts scatter wider than a Poisson draw around a
# projection, because the projection itself carries error. Measured variance /
# mean on the graded 2026-07-25 slate was 1.16 for the model and 0.93 for the
# closing line. Inflating the variance keeps Over%/Under% from reading as more
# certain than the projection can support.
DEFAULT_DISPERSION = 1.15

# The projection is a weaker point estimate than the market: on the same slate
# it missed by 2.00 Ks on average versus 1.80 for the line, and its
# disagreements with the line did not predict the outcome. Probabilities are
# therefore priced off a projection pulled part way back toward the line.
DEFAULT_MARKET_SHRINK = 0.5


@dataclass(frozen=True)
class GameContext:
    game_pk: int
    game_date: str
    pitcher_id: int
    pitcher_name: str
    pitcher_team: str
    opponent_team: str
    opponent_team_id: int
    home_away: str


@dataclass(frozen=True)
class Projection:
    date: str
    pitcher: str
    team: str
    opponent: str
    pitcher_k_rate: float
    opponent_k_rate: float
    expected_batters_faced: float
    projected_ks: float
    recent_strikeouts: Tuple[float, ...]
    recent_avg_ks: float
    season_avg_ks: float
    confidence: str


@dataclass(frozen=True)
class PropGrade:
    projection: Projection
    line: float
    pick: str
    over_odds: Optional[float]
    under_odds: Optional[float]
    line_edge: float
    model_over: float
    market_over: Optional[float]
    probability_edge: Optional[float]
    lean: str
    slip_tier: str
    risk_notes: str
    score: float
    actual_ks: Optional[float]
    result: str


def fetch_json(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    query = urllib.parse.urlencode(params or {})
    url = f"{BASE_URL}{path}"
    if query:
        url = f"{url}?{query}"

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "mlb-k-prop-script/1.0",
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


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    return "".join(char for char in text if not unicodedata.combining(char)).lower().strip()


def innings_to_outs(value: Any) -> int:
    """Convert MLB innings notation, e.g. 5.2 means 5 innings and 2 outs."""
    if value in (None, ""):
        return 0
    text = str(value)
    if "." not in text:
        return int(as_float(text) * 3)
    whole, frac = text.split(".", 1)
    return int(whole or 0) * 3 + int((frac or "0")[0])


def american_to_implied_probability(odds: Optional[float]) -> Optional[float]:
    if odds is None:
        return None
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


def poisson_cdf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0
    term = math.exp(-lam)
    total = term
    for i in range(1, k + 1):
        term *= lam / i
        total += term
    return min(max(total, 0.0), 1.0)


def count_cdf(k: int, mean: float, dispersion: float = 1.0) -> float:
    """P(X <= k) for a count with the given mean and variance = dispersion * mean."""
    if mean <= 0:
        return 1.0
    if dispersion <= 1.0 + 1e-9:
        return poisson_cdf(k, mean)
    # Negative binomial parameterized by its mean and variance ratio.
    success = 1.0 / dispersion
    size = mean / (dispersion - 1.0)
    total = 0.0
    for i in range(k + 1):
        log_pmf = (
            math.lgamma(i + size)
            - math.lgamma(size)
            - math.lgamma(i + 1)
            + size * math.log(success)
            + i * math.log1p(-success)
        )
        total += math.exp(log_pmf)
    return min(max(total, 0.0), 1.0)


def probability_over(line: float, projection: float, dispersion: float = 1.0) -> float:
    # For a 5.5 line, over is P(K >= 6). For a 5.0 line, push is ignored
    # and over is P(K >= 6), which is conservative for whole-number markets.
    needed = math.floor(line) + 1
    return 1 - count_cdf(needed - 1, projection, dispersion)


def shrink_to_line(projection: float, line: float, shrink: float) -> float:
    """Pull the projection toward the sportsbook line before pricing it."""
    return line + shrink * (projection - line)


def no_vig_probs(over_odds: Optional[float], under_odds: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    over_prob = american_to_implied_probability(over_odds)
    under_prob = american_to_implied_probability(under_odds)
    if over_prob is None or under_prob is None:
        return over_prob, under_prob
    vig_sum = over_prob + under_prob
    if vig_sum <= 0:
        return over_prob, under_prob
    return over_prob / vig_sum, under_prob / vig_sum


def get_probable_pitchers(date: str) -> List[GameContext]:
    data = fetch_json(
        "/schedule",
        {
            "sportId": 1,
            "date": date,
            "hydrate": "probablePitcher,team",
        },
    )

    contexts: List[GameContext] = []
    for day in data.get("dates", []):
        for game in day.get("games", []):
            teams = game.get("teams", {})
            away = teams.get("away", {})
            home = teams.get("home", {})
            away_team = away.get("team", {})
            home_team = home.get("team", {})

            for side, opponent_side in (("away", "home"), ("home", "away")):
                team_block = teams.get(side, {})
                opponent_block = teams.get(opponent_side, {})
                probable = team_block.get("probablePitcher")
                if not probable:
                    continue
                contexts.append(
                    GameContext(
                        game_pk=int(game["gamePk"]),
                        game_date=game.get("gameDate", date),
                        pitcher_id=int(probable["id"]),
                        pitcher_name=probable["fullName"],
                        pitcher_team=(away_team if side == "away" else home_team).get("name", side),
                        opponent_team=(home_team if side == "away" else away_team).get("name", opponent_side),
                        opponent_team_id=int(opponent_block.get("team", {}).get("id", 0)),
                        home_away=side,
                    )
                )
    return contexts


def extract_splits(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    stats = data.get("stats", [])
    if not stats:
        return []
    return stats[0].get("splits", [])


def get_pitcher_game_logs(player_id: int, season: int) -> List[Dict[str, Any]]:
    data = fetch_json(
        f"/people/{player_id}/stats",
        {"stats": "gameLog", "group": "pitching", "season": season},
    )
    rows = []
    for split in extract_splits(data):
        stat = split.get("stat", {})
        rows.append(
            {
                "date": split.get("date", ""),
                "strikeouts": as_float(stat.get("strikeOuts")),
                "batters_faced": as_float(stat.get("battersFaced")),
                "outs": innings_to_outs(stat.get("inningsPitched")),
            }
        )
    return rows


def get_team_hitting_k_rate(team_id: int, season: int) -> float:
    if not team_id:
        return DEFAULT_LEAGUE_K_RATE
    data = fetch_json(
        f"/teams/{team_id}/stats",
        {"stats": "season", "group": "hitting", "season": season},
    )
    splits = extract_splits(data)
    if not splits:
        return DEFAULT_LEAGUE_K_RATE
    stat = splits[0].get("stat", {})
    strikeouts = as_float(stat.get("strikeOuts"))
    plate_appearances = as_float(stat.get("plateAppearances"))
    if strikeouts <= 0 or plate_appearances <= 0:
        return DEFAULT_LEAGUE_K_RATE
    return strikeouts / plate_appearances


def weighted_average(values: Iterable[float], fallback: float) -> float:
    clean = [v for v in values if v > 0]
    if not clean:
        return fallback
    return statistics.mean(clean)


def project_pitcher(context: GameContext, season: int, recent_games: int = 5) -> Projection:
    game_date = context.game_date[:10]
    # Only games strictly before the target date, so grading a finished slate
    # cannot feed that day's own result back into the projection.
    logs = [row for row in get_pitcher_game_logs(context.pitcher_id, season) if row["date"] < game_date]
    logs = sorted(logs, key=lambda row: row["date"], reverse=True)
    recent = logs[:recent_games]

    season_bf = sum(row["batters_faced"] for row in logs)
    season_ks = sum(row["strikeouts"] for row in logs)
    season_outs = float(sum(row["outs"] for row in logs))

    season_k_rate = season_ks / season_bf if season_bf > 0 else 0.21
    recent_k_rate = (
        sum(row["strikeouts"] for row in recent) / sum(row["batters_faced"] for row in recent)
        if recent and sum(row["batters_faced"] for row in recent) > 0
        else season_k_rate
    )

    # More stable than using Ks/start directly: estimate workload, then K rate.
    season_bf_per_out = season_bf / season_outs if season_bf > 0 and season_outs > 0 else 1.42
    recent_bf = [row["batters_faced"] for row in recent if row["batters_faced"] > 0]
    recent_outs = [row["outs"] for row in recent if row["outs"] > 0]

    avg_recent_bf = weighted_average(recent_bf, fallback=0.0)
    avg_recent_outs = weighted_average(recent_outs, fallback=0.0)
    estimated_recent_bf = avg_recent_bf or (avg_recent_outs * season_bf_per_out)
    estimated_season_bf = season_bf / len(logs) if logs and season_bf > 0 else 22.5
    expected_bf = 0.6 * estimated_recent_bf + 0.4 * estimated_season_bf
    expected_bf = min(max(expected_bf, 12.0), 32.0)

    opponent_k_rate = get_team_hitting_k_rate(context.opponent_team_id, season)
    pitcher_k_rate = 0.65 * season_k_rate + 0.35 * recent_k_rate
    opponent_factor = min(max(opponent_k_rate / DEFAULT_LEAGUE_K_RATE, 0.82), 1.18)
    projected_ks = expected_bf * pitcher_k_rate * opponent_factor

    season_avg_ks = season_ks / len(logs) if logs else 0.0
    recent_avg_ks = weighted_average((row["strikeouts"] for row in recent), fallback=season_avg_ks)
    sample = len(logs)
    confidence = "high" if sample >= 8 and expected_bf >= 19 else "medium" if sample >= 4 else "low"

    return Projection(
        date=context.game_date[:10],
        pitcher=context.pitcher_name,
        team=context.pitcher_team,
        opponent=context.opponent_team,
        pitcher_k_rate=pitcher_k_rate,
        opponent_k_rate=opponent_k_rate,
        expected_batters_faced=expected_bf,
        projected_ks=projected_ks,
        recent_strikeouts=tuple(row["strikeouts"] for row in recent),
        recent_avg_ks=recent_avg_ks,
        season_avg_ks=season_avg_ks,
        confidence=confidence,
    )


def read_props(path: str) -> List[Dict[str, str]]:
    return read_props_with_fields(path)[0]


def read_props_with_fields(path: str) -> Tuple[List[Dict[str, str]], List[str]]:
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return rows, list(reader.fieldnames or [])


def parse_optional_odds(value: Optional[str]) -> Optional[float]:
    if value in (None, ""):
        return None
    return as_float(value)


def format_optional_percent(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{value * 100:.1f}%"


def format_optional_number(value: Optional[float]) -> str:
    if value is None:
        return ""
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}"


def normalize_pick(value: Optional[str]) -> str:
    text = (value or "").strip().lower()
    if text in ("m", "more", "over", "o", "up", "higher"):
        return "MORE"
    if text in ("l", "less", "under", "u", "down", "lower"):
        return "LESS"
    return ""


def confidence_bonus(confidence: str) -> float:
    if confidence == "high":
        return 0.35
    if confidence == "medium":
        return 0.10
    return -0.40


def make_lean(line_edge: float, model_over: float, probability_edge: Optional[float], confidence: str) -> str:
    if confidence == "low":
        return "PASS"
    if probability_edge is not None:
        if probability_edge >= 0.07 and line_edge >= 1.00:
            return "STRONG O"
        if probability_edge >= 0.045 and line_edge >= 0.55:
            return "LEAN O"
        if probability_edge <= -0.06 and line_edge <= -0.75:
            return "STRONG U"
        if probability_edge <= -0.035 and line_edge <= -0.35:
            return "LEAN U"
        return "PASS"

    if line_edge >= 1.15 and model_over >= 0.62:
        return "STRONG O"
    if line_edge >= 0.65 and model_over >= 0.56:
        return "LEAN O"
    if line_edge <= -1.0 and model_over <= 0.42:
        return "STRONG U"
    if line_edge <= -0.5 and model_over <= 0.47:
        return "LEAN U"
    return "PASS"


def build_risk_notes(projection: Projection, line: float, line_edge: float, pick: str) -> List[str]:
    notes: List[str] = []
    recent_games = len(projection.recent_strikeouts)
    recent_more_rate = (
        sum(1 for strikeouts in projection.recent_strikeouts if strikeouts > line) / recent_games
        if recent_games
        else 0.0
    )
    recent_less_rate = (
        sum(1 for strikeouts in projection.recent_strikeouts if strikeouts < line) / recent_games
        if recent_games
        else 0.0
    )
    if projection.confidence == "low":
        notes.append("LOW_CONF")
    if projection.expected_batters_faced < 19:
        notes.append("LOW_BF")
    if abs(line_edge) < 0.50:
        notes.append("THIN_GAP")
    if pick == "MORE":
        if recent_games >= 3 and recent_more_rate < 0.60 and line_edge < 2.00:
            notes.append("LOW_RECENT_CLEAR")
        if projection.recent_avg_ks < line:
            notes.append("RECENT_BELOW")
        elif line_edge < 1.75 and projection.recent_avg_ks < line + 0.50:
            notes.append("RECENT_THIN")
        if line >= 6.5 and (line_edge < 1.25 or projection.pitcher_k_rate < 0.25 or projection.expected_batters_faced < 23):
            notes.append("HIGH_LINE_MORE")
        if line >= 4.5 and projection.pitcher_k_rate < 0.20:
            notes.append("LOW_PK_MORE")
        if projection.opponent_k_rate < DEFAULT_LEAGUE_K_RATE * 0.95 and line_edge < 1.00:
            notes.append("LOW_OPP_K")
    if pick == "LESS":
        if recent_games >= 3 and recent_less_rate < 0.60 and line_edge > -2.00:
            notes.append("LOW_RECENT_CLEAR")
        if projection.expected_batters_faced >= 24 and projection.pitcher_k_rate >= 0.23:
            notes.append("K_UPSIDE")
        if projection.opponent_k_rate > DEFAULT_LEAGUE_K_RATE * 1.05 and line_edge > -1.00:
            notes.append("HIGH_OPP_K")
    return notes


def make_slip_tier(lean: str, risk_notes: List[str]) -> str:
    hard_risks = {
        "LOW_CONF",
        "LOW_BF",
        "HIGH_LINE_MORE",
        "LOW_PK_MORE",
        "K_UPSIDE",
        "LOW_RECENT_CLEAR",
        "RECENT_BELOW",
        "RECENT_THIN",
    }
    if lean == "PASS":
        return "PASS"
    if hard_risks.intersection(risk_notes):
        return "WATCH"
    if lean.startswith("STRONG") and len(risk_notes) <= 1:
        return "CORE"
    return "WATCH"


def grade_result(pick: str, actual_ks: Optional[float], line: float) -> str:
    if not pick or actual_ks is None:
        return ""
    if actual_ks == line:
        return "PUSH"
    if pick == "MORE":
        return "HIT" if actual_ks > line else "MISS"
    if pick == "LESS":
        return "HIT" if actual_ks < line else "MISS"
    return ""


def grade_prop(
    projection: Projection,
    line: float,
    over_odds: Optional[float],
    under_odds: Optional[float],
    pick: str = "",
    actual_ks: Optional[float] = None,
    dispersion: float = DEFAULT_DISPERSION,
    shrink: float = DEFAULT_MARKET_SHRINK,
) -> PropGrade:
    market_over, _ = no_vig_probs(over_odds, under_odds)
    priced_ks = shrink_to_line(projection.projected_ks, line, shrink)
    model_over = probability_over(line, priced_ks, dispersion)
    probability_edge = model_over - market_over if market_over is not None else None
    line_edge = projection.projected_ks - line
    # Without both prices there is no market probability to shrink against, so
    # the unshrunk projection has to carry the decision on its own.
    unpriced_over = probability_over(line, projection.projected_ks, dispersion)
    lean = make_lean(line_edge, unpriced_over, probability_edge, projection.confidence)
    recommended_pick = "MORE" if lean.endswith("O") else "LESS" if lean.endswith("U") else ""
    pick = pick or recommended_pick
    risks = build_risk_notes(projection, line, line_edge, pick)
    slip_tier = make_slip_tier(lean, risks)

    if probability_edge is None:
        score = line_edge + confidence_bonus(projection.confidence)
    else:
        score = line_edge + (probability_edge * 10) + confidence_bonus(projection.confidence)

    if projection.expected_batters_faced < 19:
        score -= 0.35
    if projection.opponent_k_rate > DEFAULT_LEAGUE_K_RATE:
        score += 0.15
    if "HIGH_LINE_MORE" in risks:
        score -= 0.70
    if "LOW_PK_MORE" in risks:
        score -= 0.60
    if "THIN_GAP" in risks:
        score -= 0.25
    if "LOW_CONF" in risks:
        score -= 0.50
    if "LOW_RECENT_CLEAR" in risks:
        score -= 0.55

    return PropGrade(
        projection=projection,
        line=line,
        pick=pick,
        over_odds=over_odds,
        under_odds=under_odds,
        line_edge=line_edge,
        model_over=model_over,
        market_over=market_over,
        probability_edge=probability_edge,
        lean=lean,
        slip_tier=slip_tier,
        risk_notes=",".join(risks),
        score=score,
        actual_ks=actual_ks,
        result=grade_result(pick, actual_ks, line),
    )


def export_prop_template(projections: List[Projection], output_path: str) -> None:
    fieldnames = [
        "pitcher",
        "date",
        "team",
        "opponent",
        "line",
        "pick",
        "over_odds",
        "under_odds",
        "actual_ks",
        "notes",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for projection in sorted(projections, key=lambda item: item.projected_ks, reverse=True):
            writer.writerow(
                {
                    "pitcher": projection.pitcher,
                    "date": projection.date,
                    "team": projection.team,
                    "opponent": projection.opponent,
                    "line": "",
                    "pick": "",
                    "over_odds": "",
                    "under_odds": "",
                    "actual_ks": "",
                    "notes": "",
                }
            )


def match_context(contexts: List[GameContext], pitcher: str, team: str) -> Optional[GameContext]:
    target = normalize_name(pitcher)
    for context in contexts:
        if normalize_name(context.pitcher_name) == target:
            return context
    parts = target.split()
    if not parts:
        return None
    last_name = parts[-1]
    team_key = normalize_name(team)
    for context in contexts:
        context_parts = normalize_name(context.pitcher_name).split()
        if not context_parts or context_parts[-1] != last_name:
            continue
        if not team_key or normalize_name(context.pitcher_team) == team_key:
            return context
    return None


def fill_actual_strikeouts(contexts: List[GameContext], season: int, props_path: str) -> int:
    """Backfill actual_ks from finished games so slips can be graded automatically."""
    rows, fieldnames = read_props_with_fields(props_path)
    if "actual_ks" not in fieldnames:
        print(f"{props_path} has no actual_ks column.", file=sys.stderr)
        return 0

    logs_cache: Dict[int, List[Dict[str, Any]]] = {}
    filled = 0
    for row in rows:
        if (row.get("actual_ks") or "").strip():
            continue
        pitcher = (row.get("pitcher") or row.get("name") or "").strip()
        context = match_context(contexts, pitcher, row.get("team") or "")
        if context is None:
            print(f"No scheduled start found for {pitcher}.", file=sys.stderr)
            continue
        game_date = (row.get("date") or "").strip() or context.game_date[:10]
        if context.pitcher_id not in logs_cache:
            logs_cache[context.pitcher_id] = get_pitcher_game_logs(context.pitcher_id, season)
        appearances = [log for log in logs_cache[context.pitcher_id] if log["date"] == game_date]
        if not appearances:
            print(f"No final line yet for {pitcher} on {game_date}.", file=sys.stderr)
            continue
        row["actual_ks"] = f"{sum(log['strikeouts'] for log in appearances):.0f}"
        filled += 1

    if filled:
        with open(props_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    return filled


def print_projection_table(projections: List[Projection]) -> None:
    header = (
        f"{'Pitcher':24} {'Team':22} {'Opp':22} {'ProjK':>6} {'BF':>5} "
        f"{'pK%':>6} {'oppK%':>7} {'Conf':>6}"
    )
    print(header)
    print("-" * len(header))
    for row in sorted(projections, key=lambda item: item.projected_ks, reverse=True):
        print(
            f"{row.pitcher[:24]:24} {row.team[:22]:22} {row.opponent[:22]:22} "
            f"{row.projected_ks:6.2f} {row.expected_batters_faced:5.1f} "
            f"{row.pitcher_k_rate * 100:5.1f}% {row.opponent_k_rate * 100:6.1f}% "
            f"{row.confidence:>6}"
        )


def print_prop_grades(
    projections: List[Projection],
    props_path: str,
    dispersion: float = DEFAULT_DISPERSION,
    shrink: float = DEFAULT_MARKET_SHRINK,
) -> None:
    by_name = {normalize_name(projection.pitcher): projection for projection in projections}
    by_date_name = {(projection.date, normalize_name(projection.pitcher)): projection for projection in projections}
    by_last_team = {
        (normalize_name(projection.pitcher).split()[-1], normalize_name(projection.team)): projection
        for projection in projections
        if normalize_name(projection.pitcher).split()
    }
    by_date_last_team = {
        (projection.date, normalize_name(projection.pitcher).split()[-1], normalize_name(projection.team)): projection
        for projection in projections
        if normalize_name(projection.pitcher).split()
    }
    props = read_props(props_path)
    grades: List[PropGrade] = []

    header = (
        f"{'Date':10} {'Pitcher':24} {'Pick':>5} {'Line':>5} {'Proj':>5} {'Gap':>6} {'Over%':>7} "
        f"{'Score':>6} {'Decision':>8} {'Slip':>5} {'Conf':>6} {'Act':>4} {'Result':>6} {'Risk':20}"
    )
    print(header)
    print("-" * len(header))
    for prop in props:
        pitcher = (prop.get("pitcher") or prop.get("name") or "").strip()
        row_date = (prop.get("date") or "").strip()
        row_team = normalize_name(prop.get("team") or "")
        name_parts = normalize_name(pitcher).split()
        row_last_name = name_parts[-1] if name_parts else ""
        pick = normalize_pick(prop.get("pick") or prop.get("direction"))
        line = as_float(prop.get("line"), default=0.0)
        actual_ks = parse_optional_odds(prop.get("actual_ks") or prop.get("actual"))
        # A dated row must match a start on that date, otherwise a pitcher who
        # also started another day in the range gets graded against that game.
        if row_date:
            projection = by_date_name.get((row_date, normalize_name(pitcher)))
            if projection is None and row_last_name and row_team:
                projection = by_date_last_team.get((row_date, row_last_name, row_team))
        else:
            projection = by_name.get(normalize_name(pitcher))
            if projection is None and row_last_name and row_team:
                projection = by_last_team.get((row_last_name, row_team))
        if projection is None:
            result = grade_result(pick, actual_ks, line) if prop.get("line") else ""
            print(
                f"{row_date[:10]:10} {pitcher[:24]:24} {pick[:5]:>5} {line if prop.get('line') else '':>5} "
                f"{'':>5} {'':>6} {'':>7} {'':>6} {'NO DATA':>8} {'PASS':>5} "
                f"{'':>6} {format_optional_number(actual_ks):>4} {result:>6} {'NO_PROJECTION':20}"
            )
            continue

        if not prop.get("line"):
            continue

        over_odds = parse_optional_odds(prop.get("over_odds"))
        under_odds = parse_optional_odds(prop.get("under_odds"))
        grades.append(
            grade_prop(projection, line, over_odds, under_odds, pick, actual_ks, dispersion, shrink)
        )

    for grade in sorted(grades, key=lambda item: item.score, reverse=True):
        projection = grade.projection
        print(
            f"{projection.date[:10]:10} {projection.pitcher[:24]:24} {grade.pick[:5]:>5} {grade.line:5.1f} {projection.projected_ks:5.2f} "
            f"{grade.line_edge:+6.2f} {grade.model_over * 100:6.1f}% "
            f"{grade.score:6.2f} {grade.lean:>8} {grade.slip_tier:>5} {projection.confidence:>6} "
            f"{format_optional_number(grade.actual_ks):>4} {grade.result:>6} {grade.risk_notes[:20]:20}"
        )

    print_backtest_summary(grades)


def decimal_payout(odds: Optional[float]) -> Optional[float]:
    if odds is None:
        return None
    return odds / 100 if odds > 0 else 100 / abs(odds)


def units_won(grade: PropGrade) -> Optional[float]:
    """Profit on a one unit bet, or None when the price is missing."""
    if grade.result not in ("HIT", "MISS"):
        return 0.0 if grade.result == "PUSH" else None
    payout = decimal_payout(grade.over_odds if grade.pick == "MORE" else grade.under_odds)
    if payout is None:
        return None
    return payout if grade.result == "HIT" else -1.0


def print_backtest_summary(grades: List[PropGrade]) -> None:
    settled = [grade for grade in grades if grade.actual_ks is not None]
    if not settled:
        return

    print()
    print(f"Settled rows: {len(settled)}")

    model_error = statistics.mean(abs(g.projection.projected_ks - g.actual_ks) for g in settled)
    line_error = statistics.mean(abs(g.line - g.actual_ks) for g in settled)
    bias = statistics.mean(g.actual_ks - g.projection.projected_ks for g in settled)
    print(f"Projection MAE {model_error:.2f} Ks vs closing line MAE {line_error:.2f} Ks (bias {bias:+.2f}).")
    if line_error < model_error:
        print("The line is the better point estimate on this sample, so treat gaps as weak evidence.")

    graded = [grade for grade in settled if grade.result in ("HIT", "MISS", "PUSH")]
    for label, subset in (
        ("all picks", graded),
        ("CORE", [g for g in graded if g.slip_tier == "CORE"]),
        ("WATCH", [g for g in graded if g.slip_tier == "WATCH"]),
    ):
        decided = [g for g in subset if g.result in ("HIT", "MISS")]
        if not decided:
            continue
        hits = sum(1 for g in decided if g.result == "HIT")
        profits = [units_won(g) for g in subset]
        priced = [value for value in profits if value is not None]
        roi = ""
        if priced:
            roi = f", {sum(priced):+.2f}u on {len(priced)} bets ({sum(priced) / len(priced) * 100:+.1f}% ROI)"
        print(f"  {label:10} {hits}/{len(decided)} hit ({hits / len(decided) * 100:.1f}%){roi}")


def main() -> int:
    today = dt.date.today().isoformat()
    parser = argparse.ArgumentParser(description="Project and grade MLB strikeout props.")
    parser.add_argument("--date", default=today, help="Game date in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--season", type=int, default=dt.date.today().year, help="MLB season year.")
    parser.add_argument("--props", help="CSV with manual lines. Required: pitcher,line. Optional: date,pick,over_odds,under_odds,actual_ks,notes")
    parser.add_argument("--export-template", help="Write a CSV of probable pitchers so you can manually fill in lines.")
    parser.add_argument("--recent-games", type=int, default=5, help="Recent starts/games to blend into projection.")
    parser.add_argument(
        "--fill-actuals",
        action="store_true",
        help="Backfill the actual_ks column of --props from finished games, then grade.",
    )
    parser.add_argument(
        "--dispersion",
        type=float,
        default=DEFAULT_DISPERSION,
        help="Variance / mean ratio for the strikeout distribution. 1.0 is plain Poisson.",
    )
    parser.add_argument(
        "--shrink",
        type=float,
        default=DEFAULT_MARKET_SHRINK,
        help="How much of the projection's gap to the line to trust when pricing. 1.0 ignores the line.",
    )
    args = parser.parse_args()

    if args.fill_actuals and not args.props:
        parser.error("--fill-actuals requires --props")

    dates = [args.date]
    if args.props:
        prop_dates = sorted(
            {
                (row.get("date") or "").strip()
                for row in read_props(args.props)
                if (row.get("date") or "").strip()
            }
        )
        if prop_dates:
            dates = prop_dates

    projections: List[Projection] = []
    all_contexts: List[GameContext] = []
    for date in dates:
        contexts = get_probable_pitchers(date)
        if not contexts:
            print(f"No probable pitchers found for {date}.", file=sys.stderr)
            continue
        all_contexts.extend(contexts)
        for context in contexts:
            try:
                projections.append(project_pitcher(context, args.season, args.recent_games))
            except RuntimeError as exc:
                print(f"Skipping {context.pitcher_name}: {exc}", file=sys.stderr)

    if args.fill_actuals and args.props:
        filled = fill_actual_strikeouts(all_contexts, args.season, args.props)
        print(f"Filled actual_ks for {filled} row(s) in {args.props}.")

    if not projections:
        print("No projections available.")
        return 1

    if args.export_template:
        export_prop_template(projections, args.export_template)
        print(f"Wrote prop template to {args.export_template}. Fill in the line column, then rerun with --props.")
    elif args.props:
        print_prop_grades(projections, args.props, args.dispersion, args.shrink)
    else:
        print_projection_table(projections)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
