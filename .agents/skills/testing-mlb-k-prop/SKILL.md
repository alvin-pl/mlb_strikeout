---
name: testing-mlb-k-prop
description: How to exercise and verify mlb_k_prop.py (MLB strikeout prop CLI) end to end — runtimes, API behaviour, safe CSV handling, and the date pitfalls that trip up projections, grading and --fill-actuals.
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

## Verifying the numbers without a GUI
- Props-board columns are `Date Pitcher Pick Line Proj Gap Over% Score Decision Slip Conf Act Result Risk`
  (no `MktO%`/`ProbEd` despite the README). `Over%` is the **priced/shrunk** probability, while `Decision`/`Slip` for
  rows without both odds come from the **unshrunk** probability — so don't expect `Decision` to be consistent with the
  displayed `Over%`; assert against `probability_over(line, projected_ks, dispersion)` instead.
- `--shrink 1.0 --dispersion 1.0` must reproduce `1 - poisson_cdf(floor(line), ProjK)` exactly for every row; parse the
  printed table and recompute — a cheap, strong regression check on the whole pricing path.
- Row accounting matters: rows with a blank `line` are silently skipped with no output line at all, and rows without a
  matched projection print `NO_PROJECTION` but are excluded from the backtest summary. Always compare CSV row count,
  printed row count, and `Settled rows` rather than eyeballing the board.
- ROI/units: `units_won` returns `None` when the relevant side's odds are missing (excluded from ROI) and `0.0` for PUSH
  (which *is* counted in the bet denominator). Hand-check a couple of rows: `+112` HIT = `+1.12u`, any MISS = `-1.00u`,
  `-142` HIT = `+0.7042u`.
- Fastest way to unit-check new math: `sys.path.insert(0, repo)` then `import mlb_k_prop` and build
  `Projection`/`PropGrade` dataclasses directly (field order: see the dataclasses at the top of the file); no network needed.

## Devin Secrets Needed
None. The MLB Stats API used here is public and unauthenticated.
