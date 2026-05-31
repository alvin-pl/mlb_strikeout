# MLB Strikeout Prop Script

This workspace contains a standalone Python script for projecting MLB pitcher strikeouts and grading strikeout props.

## Files

- `mlb_k_prop.py` - main script
- `sample_props.csv` - example input format for prop lines

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
