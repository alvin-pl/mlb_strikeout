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

The constants were then validated on a bulk historical backtest of ~26,000
starter outings (2021-2026) pulled from the MLB Stats API with the scripts in
`history/`: sweeping the rate prior, the season/recent blend, the recent
window, the workload blend and prior, the opponent cap, and the dispersion
around their current values changed MAE by less than 0.005 Ks and held-out 2026
MAE by less than 0.002 Ks, so they are left where they are. Run
`python3 history/download_history.py` (one-time, ~5 minutes) and then
`python3 history/backtest.py` to reproduce.

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

## Daily Picks Pushed To Your Phone (GitHub Actions + ntfy)

The workflow in `.github/workflows/morning-picks.yml` runs the win prediction script every morning at 12:00 UTC (8 AM ET) and pushes the picks to your phone via [ntfy.sh](https://ntfy.sh). It's free — no server, no account.

Setup:

1. Pick a secret, unguessable topic name, e.g. `alvin-mlb-picks-x7k2q9`. Anyone who knows the topic name can read the messages, so keep it random.
2. In this GitHub repo, go to Settings → Secrets and variables → Actions → New repository secret. Name it `NTFY_TOPIC` and set the value to your topic name.
3. On your phone, either install the free ntfy app and subscribe to your topic, or open `https://ntfy.sh/your-topic-name` in your browser and allow notifications.
4. Test it: go to the repo's Actions tab → "Morning MLB Picks" → "Run workflow". You should get a notification within a minute or two.

To change the delivery time, edit the `cron` line in the workflow (times are in UTC).

## Live Game Monitoring For In-Game Betting (GitHub Actions + ntfy)

`mlb_live_monitor.py` polls live games and pushes alerts to your phone via ntfy so you can bet live:

- **Win probability divergence** - when the live win probability of the team the pregame model picked crosses key bands (25/35/50/65/75%), you get an alert comparing live vs. pregame probability. A big drop can mean a hedge spot or a better live price on your side.
- **Live pitcher strikeouts vs. prop lines** - pass your props CSV (same format as `mlb_k_prop.py`) with `--props my_lines.csv` and get alerts when a pitcher is 1 K away from the line, clears it, or leaves the game under it.
- **Two day-level check-ins** - one notification when the first game of the day reaches the 5th inning (with the score), and one high-priority "expected win" notification when the first team of the day goes up 5+ runs in the 5th inning or later. After those, check your phone's scoreboard — no per-game score spam.
- **Final HIT/MISS** - one alert per picked game with the final score and whether the model's pick hit.

Run it locally during games:

```bash
python3 mlb_live_monitor.py --ntfy-topic your-topic --props my_lines.csv
```

It polls every 2 minutes (`--interval` to change) until all games finish, and deduplicates alerts through `live_monitor_state.json` so restarts don't re-send old alerts. Use `--once` for a single poll from an external scheduler.

The workflow in `.github/workflows/live-monitor.yml` runs it automatically on GitHub Actions every hour from 1 PM ET to midnight ET, each run monitoring for 55 minutes. It reuses the same `NTFY_TOPIC` secret as the morning picks workflow and persists alert state between runs with the Actions cache, so you won't get duplicate alerts. If `my_lines.csv` exists in the repo with today's lines filled in, strikeout prop tracking is included automatically.

## How The Win/Loss Model Works

The script estimates each team's strength from:

- Season win percentage
- Pythagorean win expectation from runs scored/allowed
- Last-10-games record

These are blended and regressed toward .500 early in the season (fewer games played = more regression), then combined for both teams with the log5 method to get a neutral-field win probability. A fixed home field edge is added, and a small adjustment is applied based on each probable starter's season ERA versus league average.

Treat the result as a screening tool, not an auto-bet. Check injuries, bullpen usage, weather, and book movement before placing anything.
