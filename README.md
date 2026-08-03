# MLB Strikeout Prop Script

This workspace contains standalone Python scripts for projecting MLB pitcher strikeouts and grading strikeout props, plus a separate script for predicting team win/loss outcomes and grading moneyline odds.

## Files

- `mlb_k_prop.py` - pitcher strikeout prop script
- `mlb_win_prediction.py` - team win/loss prediction script
- `sample_props.csv` - example input format for prop lines
- `sample_moneylines.csv` - example input format for moneylines

## Run Today's Probable Pitcher Projections

```bash
python3 mlb_k_prop.py
```

## Run A Specific Date

```bash
python3 mlb_k_prop.py --date 2026-05-23 --season 2026
```

## Grade Props From A CSV

Create a CSV with these columns:

```csv
date,pitcher,team,opponent,line,pick,over_odds,under_odds,actual_ks,notes
2026-05-27,Example Pitcher,Example Team,Example Opponent,5.5,MORE,-110,-120,,manually enter sportsbook line here
```

Then run:

```bash
python3 mlb_k_prop.py --date 2026-05-23 --season 2026 --props sample_props.csv
```

## Make A Manual Line Template

Run this first to create a CSV of the day's probable pitchers:

```bash
python3 mlb_k_prop.py --date 2026-05-23 --season 2026 --export-template my_lines.csv
```

Then open `my_lines.csv`, manually fill in the `line` column from your sportsbook, and optionally fill in `pick`, `over_odds`, and `under_odds`. If your CSV has multiple dates, the script will fetch projections for each date in the `date` column.

After that, rank the board:

```bash
python3 mlb_k_prop.py --date 2026-05-23 --season 2026 --props my_lines.csv
```

The prop board includes:

- `Gap` - projection minus sportsbook line
- `Over%` - model probability that the pitcher goes over
- `MktO%` - no-vig market over probability when both odds are entered
- `ProbEd` - model over probability minus market over probability
- `Score` - ranking score for sorting slip candidates
- `Decision` - `STRONG O`, `LEAN O`, `PASS`, `LEAN U`, or `STRONG U`
- `Slip` - `CORE`, `WATCH`, or `PASS`
- `Risk` - guardrails such as `HIGH_LINE_MORE`, `LOW_RECENT_CLEAR`, `RECENT_THIN`, `LOW_PK_MORE`, `LOW_BF`, or `THIN_GAP`
- `Result` - optional hit/miss backtest if you fill in `actual_ks`

## Backtest Your Slips

After a game finishes, enter the final strikeout total in `actual_ks`:

```csv
date,pitcher,team,opponent,line,pick,over_odds,under_odds,actual_ks,notes
2026-05-27,Casey Mize,Detroit Tigers,Los Angeles Angels,3.5,MORE,,,6,example result
```

Then rerun the same command:

```bash
python3 mlb_k_prop.py --date 2026-05-27 --season 2026 --props my_lines.csv
```

The script will print `HIT`, `MISS`, or `PUSH` and summarize the hit rate for rows with results.

The file `screenshot_results.csv` contains the picks from your screenshots so you can keep comparing future model changes against real slip outcomes.

## How The Model Works

The script estimates projected strikeouts from:

- Pitcher season strikeout rate
- Pitcher recent strikeout rate
- Expected batters faced from recent workload and season workload
- Opponent team strikeout rate

When odds are provided, it converts American odds to no-vig market probability and compares that to the model's over probability.

Treat the result as a screening tool, not an auto-bet. Check lineups, opener/bulk roles, pitch-count news, weather, umpire, and book movement before placing anything.

# MLB Team Win/Loss Prediction Script

`mlb_win_prediction.py` is a separate, standalone script that predicts which team wins each game instead of grading pitcher strikeouts.

## Run Today's Win Predictions

```bash
python3 mlb_win_prediction.py
```

## Run A Specific Date

```bash
python3 mlb_win_prediction.py --date 2026-05-23 --season 2026
```

## Grade Moneylines From A CSV

Create a CSV with these columns:

```csv
date,home_team,away_team,home_ml,away_ml,actual_winner,notes
2026-05-27,Boston Red Sox,New York Yankees,-130,110,,manually enter sportsbook moneyline here
```

Then run:

```bash
python3 mlb_win_prediction.py --date 2026-05-23 --season 2026 --moneylines sample_moneylines.csv
```

## Make A Manual Moneyline Template

Run this first to create a CSV of the day's games:

```bash
python3 mlb_win_prediction.py --date 2026-05-23 --season 2026 --export-template my_moneylines.csv
```

Then open `my_moneylines.csv`, manually fill in `home_ml` and `away_ml` from your sportsbook. If your CSV has multiple dates, the script will fetch predictions for each date in the `date` column.

After that, rank the board:

```bash
python3 mlb_win_prediction.py --date 2026-05-23 --season 2026 --moneylines my_moneylines.csv
```

The moneyline board includes:

- `Pick` - `HOME` or `AWAY`, or blank for `PASS`
- `ModelH%` - model probability the home team wins
- `MktH%` - no-vig market probability the home team wins, when both odds are entered
- `Edge` - model home probability minus market home probability
- `Score` - ranking score for sorting candidates
- `Lean` - `STRONG`, `LEAN`, or `PASS`
- `Result` - optional hit/miss backtest if you fill in `actual_winner` with either team's name

## Backtest Your Predictions

After a game finishes, enter the winning team's name in `actual_winner`:

```csv
date,home_team,away_team,home_ml,away_ml,actual_winner,notes
2026-05-27,Boston Red Sox,New York Yankees,-130,110,Boston Red Sox,example result
```

Then rerun the same command. The script will print `HIT` or `MISS` and summarize the hit rate for rows with results.

## How The Win/Loss Model Works

The script estimates each team's strength from:

- Season win percentage
- Pythagorean win expectation from runs scored/allowed
- Last-10-games record

These are blended and regressed toward .500 early in the season (fewer games played = more regression), then combined for both teams with the log5 method to get a neutral-field win probability. A fixed home field edge is added, and a small adjustment is applied based on each probable starter's season ERA versus league average.

Treat the result as a screening tool, not an auto-bet. Check injuries, bullpen usage, weather, and book movement before placing anything.
