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
- `Score` - ranking score for sorting slip candidates
- `Decision` - `STRONG O`, `LEAN O`, `PASS`, `LEAN U`, or `STRONG U`
- `Slip` - `CORE`, `WATCH`, or `PASS`
- `Conf` - `high`, `medium`, or `low`, from how many prior starts back the projection
- `Risk` - guardrails such as `HIGH_LINE_MORE`, `LOW_RECENT_CLEAR`, `RECENT_THIN`, `LOW_PK_MORE`, `LOW_BF`, `LOW_CONF`, or `THIN_GAP`
- `Result` - optional hit/miss backtest if you fill in `actual_ks`

## Backtest Your Slips

After the games finish, let the script pull the final strikeout totals for you:

```bash
python3 mlb_k_prop.py --date 2026-07-25 --season 2026 --props my_lines.csv --fill-actuals
```

That writes `actual_ks` for every finished start in the file and then grades the
board. Rows whose game has not ended yet are left alone, so it is safe to rerun.

You can also enter the total by hand:

```csv
date,pitcher,team,opponent,line,pick,over_odds,under_odds,actual_ks,notes
2026-05-27,Casey Mize,Detroit Tigers,Los Angeles Angels,3.5,MORE,,,6,example result
```

Then rerun the same command:

```bash
python3 mlb_k_prop.py --date 2026-05-27 --season 2026 --props my_lines.csv
```

The script will print `HIT`, `MISS`, or `PUSH` and then a summary:

- Projection MAE against the closing line's MAE. If the line wins, the model's
  gaps are not worth much on that sample.
- Log loss of the model's `Over%` against a coin flip and, on rows with both
  prices, against the no-vig market probability. A few dozen bets of ROI is
  mostly noise, so this proper scoring rule is the more honest read on whether a
  model change helped.
- `corr(projection gap, actual - line)`, i.e. whether disagreeing with the book
  has actually predicted the outcome.
- Hit rate and units won per slip tier, using the odds in the CSV.

The file `screenshot_results.csv` accumulates graded slates so you can keep
comparing future model changes against real outcomes.

Projections only use game logs from *before* the date being graded, so grading a
finished slate never lets that day's own strikeout total leak into its own
projection.

## How The Model Works

The script estimates projected strikeouts from:

- Pitcher season strikeout rate
- Pitcher recent strikeout rate
- Expected batters faced from recent workload and season workload
- Opponent team strikeout rate

Both strikeout rates are regressed toward the league average by the number of
batters the pitcher has actually faced (prior weight 100 batters, roughly where
strikeout rate stabilizes), and expected batters faced is regressed toward a
league-average start. Without that, a pitcher with one 8-strikeout start projects
as a 42% strikeout pitcher: low-confidence starts missed by 3.53 Ks on average
before the change, and the projection went from losing to the closing line (MAE
2.03 vs 1.87 over 64 graded props) to edging it (1.83 vs 1.87), with
`corr(gap, actual - line)` going from -0.01 to +0.17.

When odds are provided, it converts American odds to no-vig market probability and compares that to the model's over probability.

Two knobs control how much the model is allowed to disagree with the book:

- `--shrink` (default 0.5) prices the prop off a projection pulled halfway back
  to the sportsbook line. Halfway minimized log loss on the graded set; trusting
  the projection outright (`--shrink 1.0`) was worse even after the projection
  started beating the line on MAE.
- `--dispersion` (default 1.15) inflates the variance of the strikeout
  distribution above Poisson to account for projection error, which flattens the
  tails and keeps thin edges out of the `CORE` tier. `--dispersion 1.0` is plain
  Poisson.

Treat the result as a screening tool, not an auto-bet. Check lineups, opener/bulk roles, pitch-count news, weather, umpire, and book movement before placing anything.
