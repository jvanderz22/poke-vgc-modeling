"""Which spread prior best predicts the turn orders that actually happened?

A human's Stat Points are not recoverable from a replay — that is the premise of Phase 8, and it
means a prior over spreads cannot be scored against the truth. It can be scored against its
*consequences*. A prior implies a distribution over who moves first, and 7,514 cached replays are
full of observed turn orders, so the prior that assigns them the highest likelihood is the better
description of what people build. No ground truth required.

For one racing pair, with both spreads hidden:

    P(A moved first) = Σ_a Σ_b P(a) P(b) · [eff(a) > eff(b)] + ½·[eff(a) = eff(b)]

the half because Showdown breaks a speed tie at random. Under Trick Room the comparison inverts.

Intervals are cluster bootstraps over replays, not over pairs: the eight pairs in one game share
both teams and both players, and resampling them independently would claim precision the corpus
does not have (gate 4b).

    .venv/bin/python scripts/analysis/spread_prior.py --replays 2500
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
import math
from typing import Any, Callable

import numpy as np

from vgc.belief import prior as pr
from vgc.belief import speed
from vgc.data.observe import MoveEvent, Observer
from vgc.regulation import Regulation, load_regulation

PriorFn = Callable[[Regulation, str, str | None, list[str]], pr.SpeedPrior]


def effective_curve(reg: Regulation, ev: MoveEvent, cap: int) -> np.ndarray:
    """Effective Speed at every legal investment, for the forme that was on the field."""
    out = np.zeros(cap + 1)
    for sp in range(cap + 1):
        stat = speed.speed_stat(reg, ev.forme or ev.species, _nature(ev), sp)
        out[sp] = 0.0 if stat is None else speed.effective_speed(stat, ev)
    return out


_NATURES: dict[tuple[str, str], str | None] = {}


def _nature(ev: MoveEvent) -> str | None:
    return _NATURES.get((ev.side, ev.species))


def order_probability(first: np.ndarray, second: np.ndarray,
                      pa: np.ndarray, pb: np.ndarray, sign: int) -> float:
    """P(the observed order) under two independent priors."""
    diff = first[:, None] - second[None, :]
    faster = (diff > 0) if sign > 0 else (diff < 0)
    win = faster.astype(float) + 0.5 * (diff == 0)
    return float(pa @ win @ pb)


def build_priors(reg: Regulation, obs: Observer, fn: PriorFn, cap: int) -> dict[tuple[str, str], np.ndarray]:
    out = {}
    for sid, side in obs.sides.items():
        for mon in side.mons:
            p = fn(reg, mon.species, mon.nature, list(mon.moves))
            out[(sid, mon.species)] = np.asarray(p.mass[: cap + 1], dtype=float)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regulation", default="reg_mc")
    ap.add_argument("--replays", type=int, default=2500)
    ap.add_argument("--boots", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    reg = load_regulation(args.regulation)
    cap = reg.sp_per_stat_cap
    marks, tier = pr.benchmarks(reg)
    pool_marks, _ = pr.benchmarks(reg, report={})   # force the no-corpus fallback

    candidates: dict[str, PriorFn] = {
        "flat": lambda r, s, n, m: pr.flat_prior(r, s, n),
        "imputed (impute_sp)": lambda r, s, n, m: pr.imputed_prior(r, s, n, m),
        "structural": lambda r, s, n, m: pr.speed_prior(r, s, n, m, tier="structural"),
        "benchmark (pool tier)": lambda r, s, n, m: pr.speed_prior(r, s, n, m, marks=pool_marks, tier="pool"),
        "benchmark (usage tier)": lambda r, s, n, m: pr.speed_prior(r, s, n, m, marks=marks, tier=tier),
    }

    # log-likelihood per pair, kept per replay so the bootstrap can resample whole games
    per_replay: dict[str, list[list[float]]] = {k: [] for k in candidates}
    close_only: dict[str, list[list[float]]] = {k: [] for k in candidates}
    n_pairs = 0

    for path in sorted(glob.glob("data/replays/*/*.json.gz"))[: args.replays]:
        rep = json.loads(gzip.decompress(open(path, "rb").read()))
        obs = Observer("spectator", reg.dex)
        obs.feed_many(rep["log"].split("\n"))
        pairs = speed.pairs(reg, obs.moves_log)
        if not pairs:
            continue
        _NATURES.clear()
        for sid, side in obs.sides.items():
            for mon in side.mons:
                _NATURES[(sid, mon.species)] = mon.nature

        curves: dict[int, np.ndarray] = {}
        for ev in obs.moves_log:
            curves[id(ev)] = effective_curve(reg, ev, cap)

        priors = {name: build_priors(reg, obs, fn, cap) for name, fn in candidates.items()}
        rows = {name: [] for name in candidates}
        close = {name: [] for name in candidates}
        for a, b, sign in pairs:
            ca, cb = curves[id(a)], curves[id(b)]
            if not ca.any() or not cb.any():
                continue
            n_pairs += 1
            # "Close" = the flat prior is genuinely unsure. Pairs decided by base stats alone
            # carry no information about the prior, and pooling them buries the comparison.
            flat = np.ones(cap + 1) / (cap + 1)
            p_flat = order_probability(ca, cb, flat, flat, sign)
            is_close = 0.05 < p_flat < 0.95
            for name in candidates:
                pa = priors[name].get((a.side, a.species))
                pb = priors[name].get((b.side, b.species))
                if pa is None or pb is None:
                    continue
                p = order_probability(ca, cb, pa / pa.sum(), pb / pb.sum(), sign)
                ll = math.log(max(p, 1e-12))
                rows[name].append(ll)
                if is_close:
                    close[name].append(ll)
        for name in candidates:
            if rows[name]:
                per_replay[name].append(rows[name])
                close_only[name].append(close[name])

    rng = np.random.default_rng(args.seed)

    def score(bundle: dict[str, list[list[float]]]) -> dict[str, Any]:
        out = {}
        games = len(next(iter(bundle.values())))
        idx = rng.integers(0, games, (args.boots, games)) if games else None
        for name, per_game in bundle.items():
            flat_ll = [x for g in per_game for x in g]
            if not flat_ll:
                continue
            sums = np.array([sum(g) for g in per_game], dtype=float)
            counts = np.array([len(g) for g in per_game], dtype=float)
            boots = (sums[idx].sum(axis=1) / np.maximum(counts[idx].sum(axis=1), 1)) if idx is not None else None
            out[name] = {
                "pairs": len(flat_ll),
                "mean_logloss": -float(np.mean(flat_ll)),
                "ci95": [round(-float(np.percentile(boots, 97.5)), 4),
                         round(-float(np.percentile(boots, 2.5)), 4)] if boots is not None else None,
            }
        return out

    def paired(bundle: dict[str, list[list[float]]], base: str = "flat") -> dict[str, Any]:
        """Difference against `base`, bootstrapped over the same resampled games.

        Two priors scored on the *same* pairs are a paired comparison, and reading their separate
        intervals instead throws most of the power away: the between-game variance is shared and
        cancels in the difference. Gate 3 says "beats X" means the whole interval does, so the
        interval that has to clear zero is this one.
        """
        games = len(bundle[base])
        if not games:
            return {}
        idx = rng.integers(0, games, (args.boots, games))
        out = {}
        counts = np.array([len(g) for g in bundle[base]], dtype=float)
        denom = np.maximum(counts[idx].sum(axis=1), 1)
        ref = np.array([sum(g) for g in bundle[base]], dtype=float)
        for name, per_game in bundle.items():
            if name == base:
                continue
            mine = np.array([sum(g) for g in per_game], dtype=float)
            # positive = this prior is worse than the baseline, in nats per pair
            delta = (ref[idx].sum(axis=1) - mine[idx].sum(axis=1)) / denom
            lo, hi = np.percentile(delta, [2.5, 97.5])
            # Three outcomes, not two. An interval that clears zero on the *other* side is a
            # prior that beats flat, and a verdict field that only knows how to say "flat wins"
            # would have reported the benchmark prior's win as a non-result.
            out[name] = {"worse_than_flat_by": round(float(np.mean(delta)), 5),
                         "ci95": [round(float(lo), 5), round(float(hi), 5)],
                         "verdict": "flat is better" if lo > 0 else
                                    "beats flat" if hi < 0 else "not distinguishable"}
        return out

    print(json.dumps({
        "regulation": reg.id,
        "replays_scanned": args.replays,
        "racing_pairs": n_pairs,
        "benchmark_tier": tier,
        "benchmark_speeds": len(marks),
        "unit_of_independence": "replay (cluster bootstrap over games, not pairs)",
        "all_pairs": score(per_replay),
        "close_pairs_only": score(close_only),
        "vs_flat_all_pairs": paired(per_replay),
        "vs_flat_close_pairs": paired(close_only),
        "note": "lower is better; the score is the negative log-likelihood the prior assigns to "
                "the turn order that actually happened",
    }, indent=1))


if __name__ == "__main__":
    main()
