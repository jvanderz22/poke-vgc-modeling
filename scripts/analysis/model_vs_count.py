"""Does the served model know anything the count does not? (PLAN-policy, stage 3.)

Stage 0 found the model 0.008 nats better than HP share and the count above two a side on open
sheets, not distinguishable, at each state kind's first turn. Here, at every turn above two a side:

  floor   sigmoid(a · logit(HP share) + c · count lead + e · net stat stages + d)
  model   the served model for the regime, calibrated
  stack   the floor's terms and logit(model) together

The floor and the stack are fitted on the validation split (20% of training groups, which the
model did not train on: its early stopping and calibration read them), and all three are scored on
held-out games, cluster bootstrap by group. The stack's coefficient on logit(model), and its gain
over the floor, say what the model adds to the count.

    .venv/bin/python scripts/analysis/model_vs_count.py      # a few minutes on 8 workers
"""

from __future__ import annotations

import argparse
import json
import math
from multiprocessing import Pool
from typing import Any

import numpy as np

from vgc import paths

OUT = paths.DATA / "analysis" / "reg_mc" / "model_vs_count.json"
EPS = 1 / 128
LL_EPS = 1e-3
REGIMES = ("open", "closed")
_W: dict[str, Any] = {}


def logit(p: float) -> float:
    q = min(1 - EPS, max(EPS, p))
    return math.log(q / (1 - q))


def _init() -> None:
    from vgc.data.splits import load_rules
    from vgc.regulation import load_regulation

    _W["reg"] = load_regulation("reg_mc")
    _W["rules"] = load_rules(_W["reg"])


def _game(replay: dict[str, Any]) -> dict[str, Any] | None:
    from policy_bar import _side
    from vgc.data.snapshots import human_snapshots, replay_group
    from vgc.web.endgames import is_bot
    from vgc.wp.dataset import VAL_RATE, _is_val

    reg = _W["reg"]
    group = replay_group(replay)
    split = _W["rules"].split_of("human", replay["id"], [], group)
    if split == "train":
        if not _is_val(group, VAL_RATE["human"]):
            return None
        split = "val"
    elif split != "heldout_human":
        return None
    if any(is_bot(p) for p in replay.get("players") or []):
        return None
    recs = [r for r in human_snapshots(replay, reg)
            if r["obs"]["perspective"] == "spectator" and not r["meta"]["approx"] and r["kind"] == "turn"]
    if not recs or recs[0]["label"]["winner"] not in ("p1", "p2"):
        return None
    rows, keep = [], []
    for r in recs:
        s = {sid: _side(r["obs"]["sides"][sid], reg.bring) for sid in ("p1", "p2")}
        a, b = s["p1"]["left"], s["p2"]["left"]
        if max(a, b) <= 2 or min(a, b) == 0:
            continue
        tot = s["p1"]["hp"] + s["p2"]["hp"]
        rows.append({"kind": f"{max(a, b)}v{min(a, b)}", "share": s["p1"]["hp"] / tot if tot else 0.5,
                     "lead": a - b, "boosts": s["p1"]["boosts"] - s["p2"]["boosts"]})
        keep.append(r)
    if not rows:
        return None
    return {"id": replay["id"], "group": group, "split": split, "regime": "open" if recs[0]["meta"]["ots"] else "closed",
            "y": recs[0]["label"]["winner"] == "p1", "rows": rows, "_recs": keep}


def collect(workers: int) -> list[dict[str, Any]]:
    from vgc.meta import pool, replays
    from vgc.regulation import load_regulation
    from vgc.wp.models import in_battle_version, predict_records
    from vgc.wp.tools import _load

    reg = load_regulation("reg_mc")
    reps = [rp for fmt in pool.formats_for(reg) for rp in replays.cached(fmt)]
    with Pool(workers, initializer=_init) as p:
        games = [g for g in p.imap_unordered(_game, reps, chunksize=16) if g]
    for regime in REGIMES:
        model, fz = _load(reg, in_battle_version(reg.id, regime))
        sel = [g for g in games if g["regime"] == regime]
        preds = iter(predict_records(model, [r for g in sel for r in g["_recs"]], fz))
        for g in sel:
            for row in g["rows"]:
                row["model"] = float(next(preds))
    for g in games:
        g.pop("_recs")
    return games


def _x(row: dict[str, Any], with_model: bool) -> list[float]:
    x = [logit(row["share"]), row["lead"], row["boosts"]]
    return x + [logit(row["model"])] if with_model else x


def report(games: list[dict[str, Any]], boots: int = 4000, seed: int = 11) -> dict[str, Any]:
    from sklearn.linear_model import LogisticRegression

    out: dict[str, Any] = {}
    rng = np.random.default_rng(seed)
    for regime in REGIMES:
        val = [(r, g["y"]) for g in games if g["regime"] == regime and g["split"] == "val" for r in g["rows"]]
        held = [(r, g["y"], g["group"]) for g in games if g["regime"] == regime and g["split"] == "heldout_human"
                for r in g["rows"]]
        fits = {}
        for name, wm in (("floor", False), ("stack", True)):
            lr = LogisticRegression(C=1e6, max_iter=2000).fit(np.array([_x(r, wm) for r, _ in val]),
                                                                np.array([y for _, y in val]))
            fits[name] = lr
        y = np.array([yy for _, yy, _ in held], float)
        p = {"model": np.array([r["model"] for r, _, _ in held]),
             "floor": fits["floor"].predict_proba(np.array([_x(r, False) for r, _, _ in held]))[:, 1],
             "stack": fits["stack"].predict_proba(np.array([_x(r, True) for r, _, _ in held]))[:, 1]}
        ll = {k: -(y * np.log(np.clip(v, LL_EPS, 1 - LL_EPS)) + (1 - y) * np.log(np.clip(1 - v, LL_EPS, 1 - LL_EPS)))
              for k, v in p.items()}
        groups = sorted({g for _, _, g in held})
        gi = np.array([groups.index(g) for _, _, g in held])
        n = np.bincount(gi, minlength=len(groups)).astype(float)
        idx = rng.integers(0, len(groups), (boots, len(groups)))

        def diff(a: str, b: str) -> dict[str, Any]:
            d = np.bincount(gi, ll[a] - ll[b], minlength=len(groups))
            boot = d[idx].sum(1) / n[idx].sum(1)
            lo, hi = np.percentile(boot, [2.5, 97.5])
            return {"diff": round(float((ll[a] - ll[b]).mean()), 4), "ci95": [round(float(lo), 4), round(float(hi), 4)],
                    "verdict": f"{a} better" if hi < 0 else f"{b} better" if lo > 0 else "not distinguishable"}
        coef = fits["stack"].coef_[0]
        out[regime] = {
            "val_rows": len(val), "held_rows": len(held), "held_groups": len(groups),
            "logloss": {k: round(float(v.mean()), 4) for k, v in ll.items()},
            "floor_minus_model": diff("floor", "model"), "stack_minus_floor": diff("stack", "floor"),
            "stack_minus_model": diff("stack", "model"),
            "stack_coef": {"logit_share": round(float(coef[0]), 3), "lead": round(float(coef[1]), 3),
                           "boosts": round(float(coef[2]), 3), "logit_model": round(float(coef[3]), 3)},
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    res = report(collect(a.workers))
    OUT.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
