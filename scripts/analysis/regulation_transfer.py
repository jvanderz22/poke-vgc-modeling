"""Does what a WP model learns in one regulation carry to the next? (PLAN.md, "Regulation-portable
models", item 4; PLAN-v3 "Deferred": worth having before the 2026-12-02 rotation.)

The question the rotation asks is not "does an M-B model work on M-C" but "on the day M-D opens,
with a few days of games, is the old regulation's data worth training on". So, all scored on M-C's
frozen held-out human games (spectator rows, the seat the in-battle gate is scored from):

  mb            trained on M-B human games only                     the zero-shot transfer
  mc_full       trained on every M-C human training game            the ceiling
  mc_<f>        trained on a fraction f of M-C's training battles    a regulation's first days
  mc_<f>+mb     that fraction plus all of M-B                        warm start by data

This script covers the two model families that read no identity ids — `logistic` (the global
columns) and `gbt` (hand features over the global and per-Pokémon numeric columns). Their inputs are
base stats, types, move summaries, HP and the field, so an M-B model reads M-C rows as they are, with
no vocabulary to map. The set encoder, which does read identities, needs the shared vocabulary and
is a separate run.

Differences are against `mc_full` and against `mc_<f>`, with a paired bootstrap over battles: the
held-out games are fixed, and every model is scored on the same rows.

    .venv/bin/python scripts/analysis/regulation_transfer.py --mb wp-mb1 --mc wp-v1f
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import numpy as np

from vgc import paths
from vgc.regulation import load_regulation
from vgc.wp.dataset import load
from vgc.wp.features import Featurizer, Vocab, featurizer_version
from vgc.wp.features import logistic_columns
from vgc.wp.models import GBTModel, LogisticModel
from vgc.wp.dataset import FEATURES

OUT = paths.DATA / "analysis" / "reg_mc" / "regulation_transfer.json"


def human(d: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    keep = np.nonzero(d["source"] == 1)[0]
    return subset(d, keep)


def subset(d: dict[str, np.ndarray], rows: np.ndarray) -> dict[str, np.ndarray]:
    return {k: (v[rows] if k != "battle_names" and len(v) == len(d["y"]) else v) for k, v in d.items()}


def by_battles(d: dict[str, np.ndarray], frac: float, seed: int) -> dict[str, np.ndarray]:
    """A fraction of the training *battles*, every row of each: a regulation's first days are
    fewer games, not fewer decision points of each game."""
    rng = np.random.default_rng(seed)
    battles = np.unique(d["battle"])
    take = set(rng.choice(battles, max(1, int(round(frac * len(battles)))), replace=False).tolist())
    return subset(d, np.nonzero(np.isin(d["battle"], list(take)))[0])


def concat(a: dict[str, np.ndarray], b: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    keys = [k for k in a if k != "battle_names" and k in b]
    out = {k: np.concatenate([a[k], b[k]]) for k in keys}
    out["battle"] = np.concatenate([a["battle"], b["battle"] + (a["battle"].max() + 1)])
    return out


def logloss(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    idx = np.minimum((p * bins).astype(int), bins - 1)
    return float(sum(abs(p[idx == b].mean() - y[idx == b].mean()) * (idx == b).mean()
                     for b in range(bins) if (idx == b).any()))


def paired(ll: dict[str, np.ndarray], battle: np.ndarray, base: str, boots: int, seed: int) -> dict[str, Any]:
    """Each model's log loss minus `base`'s, per row, bootstrapped over battles."""
    rng = np.random.default_rng(seed)
    ids, inv = np.unique(battle, return_inverse=True)
    n = np.bincount(inv).astype(float)
    idx = rng.integers(0, len(ids), (boots, len(ids)))
    out = {}
    for name, x in ll.items():
        if name == base:
            continue
        s = np.bincount(inv, weights=x - ll[base])
        diff = s[idx].sum(1) / n[idx].sum(1)
        lo, hi = np.percentile(diff, [2.5, 97.5])
        out[name] = {"minus_" + base: round(float((x - ll[base]).mean()), 5), "ci95": [round(float(lo), 5), round(float(hi), 5)],
                     "verdict": "better" if hi < 0 else "worse" if lo > 0 else "not distinguishable"}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mb", required=True, help="the M-B feature dataset (vgc wp featurize -r reg_mb)")
    ap.add_argument("--mc", default="wp-v1f", help="the M-C feature dataset")
    ap.add_argument("--fracs", default="0.03,0.1", help="fractions of M-C's training battles")
    ap.add_argument("--kinds", default="logistic,gbt")
    ap.add_argument("--boots", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=3)
    args = ap.parse_args()

    mc, mb = load_regulation("reg_mc"), load_regulation("reg_mb")
    for reg, name in ((mc, args.mc), (mb, args.mb)):
        info = json.loads((FEATURES / reg.id / name / "info.json").read_text())
        if featurizer_version(info) != Featurizer.VERSION:
            raise SystemExit(f"{reg.id}/{name} is featurizer v{featurizer_version(info)}, not v{Featurizer.VERSION}")
    tr = {"mc_full": human(load(mc, args.mc, "train")), "mb": human(load(mb, args.mb, "train"))}
    va = {"mc": human(load(mc, args.mc, "val")), "mb": human(load(mb, args.mb, "val"))}
    fracs = [float(f) for f in args.fracs.split(",") if f]
    for f in fracs:
        tr[f"mc_{f}"] = by_battles(tr["mc_full"], f, args.seed)
        tr[f"mc_{f}+mb"] = concat(tr[f"mc_{f}"], tr["mb"])
    # Validation for early stopping comes from what each condition would have: M-B's own when it
    # has no M-C, M-C's otherwise. The held-out games are never used to fit anything.
    val_of = {k: va["mb"] if k == "mb" else va["mc"] for k in tr}
    cols = logistic_columns(Featurizer(mc, Vocab.load(FEATURES / "reg_mc" / args.mc / "vocab.json")))

    result: dict[str, Any] = {"train_rows": {k: int(len(v["y"])) for k, v in tr.items()},
                              "train_battles": {k: int(len(np.unique(v["battle"]))) for k, v in tr.items()}}
    for set_name in ("eval_human_ots", "eval_human_closed"):
        ev = load(mc, args.mc, set_name)
        ev = subset(ev, np.nonzero(ev["perspective"] == 0)[0])        # spectator rows
        result[set_name] = {"rows": int(len(ev["y"])), "battles": int(len(np.unique(ev["battle"])))}
        for kind in args.kinds.split(","):
            preds = {}
            for name, d in tr.items():
                m = LogisticModel.fit(d, cols) if kind == "logistic" else GBTModel.fit(d, val_of[name])
                preds[name], _ = m.predict(ev)
                print(f"  {set_name} {kind} {name}: {logloss(preds[name], ev['y']).mean():.4f}", flush=True)
            ll = {k: logloss(p, ev["y"]) for k, p in preds.items()}
            result[set_name][kind] = {
                "logloss": {k: round(float(v.mean()), 5) for k, v in ll.items()},
                "ece": {k: round(ece(p, ev["y"]), 4) for k, p in preds.items()},
                "vs_mc_full": paired(ll, ev["battle"], "mc_full", args.boots, args.seed),
                **{f"vs_mc_{f}": paired({k: v for k, v in ll.items() if k in (f"mc_{f}", f"mc_{f}+mb")},
                                        ev["battle"], f"mc_{f}", args.boots, args.seed) for f in fracs},
            }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
