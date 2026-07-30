"""Replicate mlb_k_prop's projection on historical starts and fit its constants.

Uses only game logs strictly before each start (same rule as the live model).
Opponent K rate is the team's season-final rate (mild look-ahead, noted).
"""
import json
import math
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
LEAGUE_K = 0.222


def load(seasons):
    logs = defaultdict(list)
    for season in seasons:
        for line in open(os.path.join(HERE, f"logs_{season}.jsonl")):
            r = json.loads(line)
            logs[(r["pid"], r["season"])].append(r)
    for key in logs:
        logs[key].sort(key=lambda r: r["date"])
    return logs


TEAM_K = json.load(open(os.path.join(HERE, "team_k_rates.json")))


def project(prior, opp_rate, p):
    """prior = list of logs before the start, oldest first. p = params dict."""
    recent = prior[-p["recent_n"]:]
    season_bf = sum(r["bf"] for r in prior)
    season_ks = sum(r["ks"] for r in prior)
    season_outs = sum(r["outs"] for r in prior)

    def shrink(ks, bf):
        denom = bf + p["prior_bf"]
        if denom <= 0:
            return LEAGUE_K
        return (ks + LEAGUE_K * p["prior_bf"]) / denom

    season_rate = shrink(season_ks, season_bf)
    rec_bf_total = sum(r["bf"] for r in recent)
    recent_rate = shrink(sum(r["ks"] for r in recent), rec_bf_total) if rec_bf_total > 0 else season_rate

    season_bf_per_out = season_bf / season_outs if season_bf > 0 and season_outs > 0 else 1.42
    rec_bf = [r["bf"] for r in recent if r["bf"] > 0]
    rec_outs = [r["outs"] for r in recent if r["outs"] > 0]
    avg_rec_bf = sum(rec_bf) / len(rec_bf) if rec_bf else 0.0
    avg_rec_outs = sum(rec_outs) / len(rec_outs) if rec_outs else 0.0
    est_rec_bf = avg_rec_bf or (avg_rec_outs * season_bf_per_out)
    est_season_bf = season_bf / len(prior) if prior and season_bf > 0 else p["league_bf"]
    ebf = p["w_rec_bf"] * est_rec_bf + (1 - p["w_rec_bf"]) * est_season_bf
    ebf = (ebf * len(prior) + p["league_bf"] * p["prior_starts"]) / (len(prior) + p["prior_starts"])
    ebf = min(max(ebf, 12.0), 32.0)

    k_rate = p["w_season"] * season_rate + (1 - p["w_season"]) * recent_rate
    factor = min(max(opp_rate / LEAGUE_K, p["opp_lo"]), p["opp_hi"])
    return ebf * k_rate * factor


def nb_logpmf(k, mean, dispersion):
    if mean <= 0:
        return 0.0 if k == 0 else -30.0
    if dispersion <= 1.0 + 1e-9:
        return k * math.log(mean) - mean - math.lgamma(k + 1)
    success = 1.0 / dispersion
    size = mean / (dispersion - 1.0)
    return (math.lgamma(k + size) - math.lgamma(size) - math.lgamma(k + 1)
            + size * math.log(success) + k * math.log1p(-success))


def build_samples(logs, min_prior=1):
    samples = []
    for (pid, season), rows in logs.items():
        for i, r in enumerate(rows):
            if not r["started"]:
                continue
            prior = rows[:i]
            if len(prior) < min_prior:
                continue
            opp = TEAM_K.get(f"{season}-{r['opp_id']}") or LEAGUE_K
            samples.append((prior, opp, r["ks"], season))
    return samples


def evaluate(samples, p):
    abs_err = 0.0
    ll = 0.0
    n = 0
    for prior, opp, ks, _ in samples:
        mu = project(prior, opp, p)
        abs_err += abs(mu - ks)
        ll += nb_logpmf(int(ks), mu, p["dispersion"])
        n += 1
    return abs_err / n, -ll / n


BASE = dict(prior_bf=100.0, recent_n=5, w_season=0.65, w_rec_bf=0.6,
            league_bf=22.5, prior_starts=2.0, opp_lo=0.82, opp_hi=1.18,
            dispersion=1.15)


def sweep(samples, name, values):
    print(f"--- {name} ---", flush=True)
    for v in values:
        p = dict(BASE)
        p[name] = v
        mae, nll = evaluate(samples, p)
        print(f"{name}={v}: MAE {mae:.4f}  NLL {nll:.4f}", flush=True)


if __name__ == "__main__":
    train = build_samples(load([2021, 2022, 2023, 2024, 2025]))
    test = build_samples(load([2026]))
    print(f"train starts: {len(train)}  test starts: {len(test)}", flush=True)
    mae, nll = evaluate(train, BASE)
    print(f"BASE train: MAE {mae:.4f} NLL {nll:.4f}", flush=True)
    mae, nll = evaluate(test, BASE)
    print(f"BASE test:  MAE {mae:.4f} NLL {nll:.4f}", flush=True)
    sweep(train, "prior_bf", [0, 25, 50, 75, 100, 150, 200, 300, 500])
    sweep(train, "w_season", [0.4, 0.5, 0.6, 0.65, 0.7, 0.8, 0.9, 1.0])
    sweep(train, "recent_n", [3, 5, 8, 10, 15])
    sweep(train, "w_rec_bf", [0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    sweep(train, "prior_starts", [0.0, 1.0, 2.0, 4.0, 8.0])
    sweep(train, "opp_hi", [1.0, 1.1, 1.18, 1.3, 1.5])
    sweep(train, "dispersion", [1.0, 1.05, 1.1, 1.15, 1.2, 1.3, 1.5])
