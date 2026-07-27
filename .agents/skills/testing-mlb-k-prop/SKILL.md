---
name: testing-mlb-k-prop
description: How to exercise and verify mlb_k_prop.py (MLB strikeout prop CLI) end to end — runtimes, API behaviour, safe CSV handling, oracles for the projection/pricing/calibration math, and the date pitfalls that trip up projections, grading and --fill-actuals.
---

# Testing `mlb_k_prop.py`

## Environment
- Pure Python stdlib, single file, no server, no secrets, no dependency install needed. `python3 mlb_k_prop.py ...`
  from the repo root is the whole setup.
- It hits the public MLB Stats API (`https://statsapi.mlb.com/api/v1`). Confirm reachability first:
  `curl -s -o /dev/null -w "%{http_code}" "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=YYYY-MM-DD"` → 200.
- **Runtimes:** ~1 API call per pitcher (+1 per opponent team). A full 30-pitcher slate takes 2-4 minutes; a multi-date
  props file (e.g. `screenshot_results.csv`, 3 dates) takes 5-8 minutes. Background long runs and poll, never assume a hang.
- `scipy` is usually present and is the best independent oracle for the hand-rolled distribution code
  (`count_cdf(k, mean, d)` == `scipy.stats.nbinom.cdf(k, n=mean/(d-1), p=1/d)`).
- `sklearn` is usually present too and is the oracle for `log_loss`:
  `mlb_k_prop.log_loss(list(zip(probs, bools)))` == `sklearn.metrics.log_loss(ys, probs, labels=[0,1])` to ~1e-16.

## Safe CSV practice
- The tracked CSVs (`my_lines.csv`, `screenshot_results.csv`, `manual_lines.csv`) are inputs *and* fixtures.
  `--fill-actuals` rewrites the file in place, so always copy to `/tmp` first, record `md5sum`, and `git checkout`/verify
  `git status --porcelain` afterwards.
- Good no-op safety test: copy a props file, blank `actual_ks`, set the dates to a future day, run `--fill-actuals`,
  and assert `Filled actual_ks for 0 row(s)` plus a byte-identical file (`diff`), not just "looks unchanged".
- Good positive test: blank `actual_ks` on a copy of a finished slate, run `--fill-actuals`, then `diff` the result
  against the tracked file — it should come back byte-identical, which proves values, column order and other fields.
- Rerun the same command to prove idempotency (expect `0 row(s)` and no byte change).

## Date pitfalls (the #1 source of real bugs here)
- `GameContext.game_date` is the **UTC** `gameDate` timestamp from `/schedule`. West-coast night games roll into the next
  UTC day, so `game_date[:10]` can be slate date + 1. On a typical slate ~8 of 30 pitchers are affected. Always check:
  `[c for c in get_probable_pitchers(D) if c.game_date[:10] != D]`.
- Consequences to test explicitly whenever projection/grading/date-matching code changes:
  - projections keyed/filtered by `game_date[:10]` can include the pitcher's own start (leak) for rolled games;
  - `--export-template --date D` writes `D+1` in the `date` column for those rows;
  - props rows dated with the true slate date fail strict date matching → `NO DATA ... NO_PROJECTION`;
  - `--fill-actuals` looks for a game log on the row's date, so rolled rows report `No final line yet` forever.
- Build a tiny 2-4 row props CSV with the same pitcher under both the true and the rolled date; one run then shows the
  whole class of problems.
- **Not every slate has rolled games** — 2026-07-26 had 0 of 30, while 2026-07-24 had 8 of 30. Before concluding a
  rollover fix works, assert the slate you picked actually contains rolled games, otherwise the test proves nothing.
  Pick the date by running the `game_date[:10] != D` filter first.
- The fix for this class is `GameContext.slate_date` (the schedule's `day["date"]`); `game_date` is still the raw UTC
  timestamp, so any *new* code that reaches for `game_date[:10]` is reintroducing the bug.

## Verifying the numbers without a GUI
- Props-board columns are `Date Pitcher Pick Line Proj Gap Over% Score Decision Slip Conf Act Result Risk`
  (no `MktO%`/`ProbEd` despite the README). `Over%` is the **priced/shrunk** probability, while `Decision`/`Slip` for
  rows without both odds come from the **unshrunk** probability — so don't expect `Decision` to be consistent with the
  displayed `Over%`; assert against `probability_over(line, projected_ks, dispersion)` instead.
- `--shrink 1.0 --dispersion 1.0` must reproduce `1 - poisson_cdf(floor(line), ProjK)` exactly for every row; parse the
  printed table and recompute — a cheap, strong regression check on the whole pricing path.
- When recomputing `Over%` from the printed table, remember `ProjK` is shown to 2 dp: compare against the interval
  `[poisson(ProjK-0.005), poisson(ProjK+0.005)]` rather than a tight epsilon, or you will report ~0.1 pp of display
  rounding as a failure.
- Row accounting matters: compare CSV row count, printed row count, and `Settled rows` rather than eyeballing the board.
  Blank-`line` rows should print a `NO LINE` / `NO_LINE` row; rows with no matched projection print `NO_PROJECTION` and
  are excluded from the backtest summary. A blank-line row for a pitcher with no projection prints `NO_PROJECTION`
  (the `NO LINE` branch is only reached once a projection matched).
- ROI/units: `units_won` returns `None` when the relevant side's odds are missing *and* for PUSH, so both are excluded
  from the bet denominator. Hand-check a couple of rows: `+112` HIT = `+1.12u`, any MISS = `-1.00u`,
  `-142` HIT = `+0.7042u`. A quick regression: 1 HIT @ +100 plus 1 PUSH must print `on 1 bets`, not `on 2 bets`.

## Projection / calibration math checks
- `project_pitcher` is easy to make deterministic without network: monkeypatch `m.get_pitcher_game_logs` to return a
  crafted list of `{"date", "strikeouts", "batters_faced", "outs"}` dicts and `m.get_team_hitting_k_rate` to return
  `0.222` (opponent factor 1.0). Then exact expected values are hand-computable.
- High-value cases for the empirical-Bayes path: zero prior logs (must give `k_rate == DEFAULT_LEAGUE_K_RATE` and
  `expected_bf == LEAGUE_BF_PER_START`, never a divide-by-zero), `recent_bf_total == 0` (must fall back to the season
  rate), the 12/32 `expected_bf` clamp (regression must run *before* the clamp), and monotonicity of `projected_ks`
  in logged strikeouts at fixed BF.
- Watch the asymmetry of reusing one prior for two windows: the same 100-BF prior applied to a 5-start recent window
  (~130 BF) regresses it ~43% toward league average vs ~15% for a full season. Not a bug, but it damps recent form.
- `print_probability_calibration` is guard-heavy and worth attacking directly with synthetic `PropGrade` lists and
  `contextlib.redirect_stdout`: <10 decided rows returns silently, all-pushes must not crash, market comparison needs
  >=10 priced rows, and `statistics.correlation` raises when *either* input is constant. Test constant `gaps` **and**
  constant `actual - line` margins — a guard that only checks gaps will crash the whole CLI (exit 1) on constant
  margins, and because the calibration block runs before the ROI block, the tier/units lines are lost too.
- Fastest way to unit-check new math: `sys.path.insert(0, repo)` then `import mlb_k_prop` and build
  `Projection`/`PropGrade` dataclasses directly (field order: see the dataclasses at the top of the file); no network needed.

## Devin Secrets Needed
None. The MLB Stats API used here is public and unauthenticated.
