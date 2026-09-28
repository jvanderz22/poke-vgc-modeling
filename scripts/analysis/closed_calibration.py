"""Is late-game miscalibration with sheets hidden a temperature problem?

Every model fails `closed_in_battle_ece`, worst at t7+ (0.093 on the served recipe against a 0.03
gate), while beating the constant. PLAN-v2 Phase 8 step 4 asks which of two things that is before
spending anything on closed-sheet self-play: too little data, or a confidence fault that a
temperature per turn bucket would fix. This measures the second, and first measures whether the
gate can be read at all on this many rows.

  **floor**     ECE of a model that is calibrated *by construction*: outcomes redrawn from the
                model's own probabilities, on the same rows. ECE is biased upward on small n, so
                this is the smallest ECE the gate could ever see here. Two couplings bracket it —
                every row of a battle redrawn independently, and every row of a battle sharing one
                draw, which is how a real winner behaves.
  **observed**  the held-out ECE, with a 95% interval from resampling battles.
  **current**   the calibration the model ships with: one temperature for closed-sheet human play.
  **train**     one temperature per bucket, fitted on closed-sheet human *training* rows — what a
                production fix would do. In-sample, so it can come out sharper than held-out
                games warrant.
  **xfit**      one temperature per bucket, cross-fitted by battle on the held-out set itself.
                Not shippable — it is fitted on the eval set — but it is the ceiling: if even this
                cannot bring the worst bucket under the gate, no temperature can.
  **xfit_one**  a single temperature, cross-fitted the same way. Separates "the shape across
                buckets is wrong" from "the level is wrong".

    .venv/bin/python scripts/analysis/closed_calibration.py --version wp-v1d-sw-split-small --dataset wp-v1d
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vgc import paths                                                    # noqa: E402
from vgc.regulation import load_regulation                               # noqa: E402
from vgc.wp import dataset                                               # noqa: E402
from vgc.wp.evaluate import ECE_GATE, IN_BATTLE_BUCKETS, _bucket, metrics  # noqa: E402
from vgc.wp.models import HUMAN_CTX_COL, SHEETS_COL, fit_temperature, load_model, symmetrize  # noqa: E402

BUCKETS = ("preview",) + IN_BATTLE_BUCKETS
sig = lambda z: 1 / (1 + np.exp(-z))  # noqa: E731


def raw_logits(model, d: dict[str, np.ndarray]) -> np.ndarray:
    """The model's logits before any temperature, so every arm starts from the same numbers."""
    if hasattr(model, "calibration"):
        model.calibration = {}
    p = np.clip(model.predict(d)[0].astype(np.float64), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def spectator_by_bucket(d: dict[str, np.ndarray], p: np.ndarray) -> dict[str, dict[str, np.ndarray]]:
    """The rows the gate scores: symmetrized spectator predictions, split by turn bucket."""
    s = symmetrize(d, p)
    b = _bucket(s["kind"], s["turn"])
    spec = s["perspective"] == 0
    return {k: {"p": s["p"][spec & (b == k)], "y": s["y"][spec & (b == k)],
                "battle": s["battle"][spec & (b == k)]} for k in BUCKETS}


def ece(p: np.ndarray, y: np.ndarray) -> float:
    return metrics(p, y)["ece"]


def floor(p: np.ndarray, battle: np.ndarray, rng, sims: int, observed: float) -> dict[str, Any]:
    """ECE when the outcomes are drawn from `p` itself, under two couplings within a battle.

    `p_calibrated_worse` is the test the gate is really asking: how often a model that is right by
    construction scores an ECE at least as bad as the one observed. The shared coupling is the
    realistic one — a battle has one winner, so its rows cannot disagree about the outcome."""
    _, inv = np.unique(battle, return_inverse=True)
    indep, shared = [], []
    for _ in range(sims):
        indep.append(ece(p, (rng.random(len(p)) < p).astype(float)))
        u = rng.random(inv.max() + 1)[inv]
        shared.append(ece(p, (u < p).astype(float)))
    q = lambda xs: [round(float(np.median(xs)), 4), round(float(np.quantile(xs, 0.95)), 4)]  # noqa: E731
    return {"independent_median_p95": q(indep), "shared_median_p95": q(shared),
            "p_calibrated_worse": {"independent": round(float(np.mean(np.array(indep) >= observed)), 3),
                                   "shared": round(float(np.mean(np.array(shared) >= observed)), 3)},
            "p_below_gate": {"independent": round(float(np.mean(np.array(indep) < ECE_GATE)), 3),
                             "shared": round(float(np.mean(np.array(shared) < ECE_GATE)), 3)}}


def cluster_ci(p: np.ndarray, y: np.ndarray, battle: np.ndarray, rng, sims: int) -> list[float]:
    ids = np.unique(battle)
    rows = {b: np.nonzero(battle == b)[0] for b in ids}
    out = []
    for _ in range(sims):
        pick = np.concatenate([rows[b] for b in rng.choice(ids, len(ids))])
        out.append(ece(p[pick], y[pick]))
    return [round(float(np.quantile(out, 0.025)), 4), round(float(np.quantile(out, 0.975)), 4)]


def fit_by_bucket(z: np.ndarray, y: np.ndarray, bucket: np.ndarray) -> dict[str, float]:
    return {b: fit_temperature(z[bucket == b], y[bucket == b]) for b in BUCKETS if (bucket == b).any()}


def apply(z: np.ndarray, bucket: np.ndarray, temps: dict[str, float] | float) -> np.ndarray:
    if isinstance(temps, float):
        return sig(z / temps)
    t = np.array([temps.get(b, 1.0) for b in bucket])
    return sig(z / t)


def crossfit(z: np.ndarray, y: np.ndarray, bucket: np.ndarray, battle: np.ndarray, rng,
             folds: int, per_bucket: bool) -> tuple[np.ndarray, list]:
    """Out-of-fold probabilities: each battle is scored with temperatures fitted without it."""
    ids = np.unique(battle)
    fold_of = dict(zip(ids, rng.permutation(len(ids)) % folds))
    f = np.array([fold_of[b] for b in battle])
    p, fitted = np.empty(len(z)), []
    for k in range(folds):
        tr, te = f != k, f == k
        temps: Any = fit_by_bucket(z[tr], y[tr], bucket[tr]) if per_bucket else fit_temperature(z[tr], y[tr])
        fitted.append(temps)
        p[te] = apply(z[te], bucket[te], temps)
    return p, fitted


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", required=True)
    ap.add_argument("--dataset", default="wp-v1d")
    ap.add_argument("--sims", type=int, default=1000)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    reg = load_regulation("reg_mc")
    rng = np.random.default_rng(args.seed)
    model = load_model(reg.id, args.version)
    shipped = dict(getattr(model, "calibration", {}) or {})
    t_current = shipped.get("human_closed", shipped.get("human", 1.0))

    ev = dataset.load(reg, args.dataset, "eval_human_closed")
    z_ev = raw_logits(model, ev)
    b_ev = _bucket(ev["kind"], ev["turn"])

    tr = dataset.load(reg, args.dataset, "train")
    g = tr["glob"]
    rows = (g[:, HUMAN_CTX_COL] == 1) & (g[:, SHEETS_COL] == 0)
    tr = {k: v[rows] for k, v in tr.items() if k != "battle_names"}
    z_tr = raw_logits(model, tr)
    t_train = fit_by_bucket(z_tr, tr["y"], _bucket(tr["kind"], tr["turn"]))

    p_xfit, fit_xfit = crossfit(z_ev, ev["y"], b_ev, ev["battle"], rng, args.folds, per_bucket=True)
    p_xone, fit_xone = crossfit(z_ev, ev["y"], b_ev, ev["battle"], rng, args.folds, per_bucket=False)

    arms = {"raw": sig(z_ev), "current": apply(z_ev, b_ev, float(t_current)),
            "train": apply(z_ev, b_ev, t_train), "xfit": p_xfit, "xfit_one": p_xone}
    scored = {name: spectator_by_bucket(ev, p) for name, p in arms.items()}

    result: dict[str, Any] = {
        "version": args.version, "dataset": args.dataset, "gate": ECE_GATE,
        "eval": {"rows": int(len(ev["y"])), "battles": int(len(np.unique(ev["battle"])))},
        "train_rows": int(len(tr["y"])), "train_battles": int(len(np.unique(tr["battle"]))),
        "temperatures": {"current": t_current, "train": {k: round(v, 4) for k, v in t_train.items()},
                         "xfit": [{k: round(v, 4) for k, v in t.items()} for t in fit_xfit],
                         "xfit_one": [round(t, 4) for t in fit_xone]},
        "by_bucket": {},
    }
    for b in BUCKETS:
        cur = scored["current"][b]
        if not len(cur["y"]):
            continue
        row: dict[str, Any] = {"n": int(len(cur["y"])), "battles": int(len(np.unique(cur["battle"]))),
                               "floor": floor(cur["p"], cur["battle"], rng, args.sims, ece(cur["p"], cur["y"])),
                               "observed_95ci": cluster_ci(cur["p"], cur["y"], cur["battle"], rng, args.sims)}
        for name, s in scored.items():
            m = metrics(s[b]["p"], s[b]["y"])
            row[name] = {"ece": m["ece"], "logloss": m["logloss"]}
        # Which way it is wrong: mean prediction against win rate, in the bins that hold rows.
        row["reliability_current"] = [(r["mean_p"], r["win_rate"], r["n"])
                                      for r in metrics(cur["p"], cur["y"])["reliability"]]
        result["by_bucket"][b] = row

    worst = lambda arm: max(IN_BATTLE_BUCKETS, key=lambda b: result["by_bucket"][b][arm]["ece"])  # noqa: E731
    result["worst"] = {arm: {"bucket": worst(arm), "ece": result["by_bucket"][worst(arm)][arm]["ece"],
                             "passes": result["by_bucket"][worst(arm)][arm]["ece"] < ECE_GATE}
                       for arm in arms}

    out = paths.DATA / "analysis" / f"closed_calibration_{args.version}.json"
    out.write_text(json.dumps(result, indent=1) + "\n")

    print(f"{args.version} on eval_human_closed: {result['eval']['rows']} rows, {result['eval']['battles']} battles; "
          f"train closed rows {result['train_rows']} over {result['train_battles']} battles")
    print(f"temperatures  current {t_current}  train {result['temperatures']['train']}")
    print(f"{'bucket':7} {'n':>5} {'bat':>4}  {'floor indep':>12} {'floor shared':>13}  {'P(cal worse)':>12}  "
          f"{'P(cal<gate)':>11}  {'obs 95% CI':>15}  " + "  ".join(f"{a:>13}" for a in arms))
    for b, r in result["by_bucket"].items():
        f = r["floor"]
        fi, fs = f["independent_median_p95"], f["shared_median_p95"]
        pw, pg = f["p_calibrated_worse"], f["p_below_gate"]
        print(f"{b:7} {r['n']:5} {r['battles']:4}  {fi[0]:.3f}/{fi[1]:.3f}  {fs[0]:.3f}/{fs[1]:.3f}   "
              f"{pw['independent']:.2f}/{pw['shared']:.2f}    {pg['independent']:.2f}/{pg['shared']:.2f}    "
              f"[{r['observed_95ci'][0]:.3f},{r['observed_95ci'][1]:.3f}]  "
              + "  ".join(f"{r[a]['ece']:.3f} {r[a]['logloss']:.3f}" for a in arms))
    print("worst in-battle bucket: " + ", ".join(
        f"{a} {w['bucket']} {w['ece']:.3f}{' PASS' if w['passes'] else ''}" for a, w in result["worst"].items()))
    print(f"wrote {out.relative_to(paths.ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
