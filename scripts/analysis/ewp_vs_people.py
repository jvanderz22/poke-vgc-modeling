"""Does an action's EWP say anything about how the game went? (PLAN-v5 step 4's gate.)

Move advice on the page would show the policy's rows with their EWP. Beating the heuristic in
self-play is not evidence those numbers mean anything for human games, so this asks it of human
games first. The positions are `policy_vs_people.py`'s: held-out open-sheet games, each turn with
more than two a side, from each side's view (its own back as the replay says it brought, theirs as
the heaviest guess), where the log shows the human's joint choice whole. Each is solved with the
policy's search. When the human's choice is not among the policy's rows (most of the time) it is
solved again with that choice kept at the root (`root_keep`), so every position has a value for
what the human did:

  wp         the served open-sheet WP model's number at that turn, from the stands, for this side
  chosen     the EWP of the human's joint choice under the people reading
  best       the EWP of the policy's own choice (the people reading's argmax)

Scored for this side's result, cluster bootstrap by series:

  gate       `chosen`, recalibrated out of fold, against `wp` recalibrated the same way: per-row log
             loss, the whole interval below zero. If the value of what was done says less about the
             result than the position's WP does, a table of action values is not shown.
  stacked    `wp` and `chosen` together against `wp` alone, out of fold: does the action's value
             add anything to the position's?
  regret     `best` − `chosen` beside `wp`: its coefficient, and what it adds.

    PYTHONPATH=scripts/analysis .venv/bin/python scripts/analysis/ewp_vs_people.py --games 800
"""

from __future__ import annotations

import argparse
import json
import random
from multiprocessing import Pool
from typing import Any

import numpy as np

from vgc import paths

OUT = paths.DATA / "analysis" / "reg_mc" / "ewp_vs_people.json"


def _game(replay: dict[str, Any]) -> list[dict[str, Any]]:
    import policy_vs_people as PV
    from vgc.data.snapshots import human_snapshots
    from vgc.policy import ewp as E
    from vgc.policy import view as V

    reg = PV._W["reg"]
    search = {**V.SEARCH, "side_k": E.SIDE_K, "root_detail": True}
    recs = None
    rows = []
    for x in PV.positions(replay):
        sid, them, sets_, f, human = x["sid"], x["them"], x["sets"], x["f"], x["human"]
        r = PV._solve(V.compose(reg, sets_, f, search))
        if r is None or not r.get("matrix"):
            continue
        kept = human in r["moves"][sid]
        if not kept:
            # The human's choice kept at the root beside the policy's own rows; theirs cut as before.
            keep = {sid: list(r["moves"][sid]) + [human]}
            r = PV._solve(V.compose(reg, sets_, f, {**search, "root_keep": keep}))
            if r is None or not r.get("matrix") or human not in r["moves"][sid]:
                continue
        M = np.array(r["matrix"], float)
        people = (r.get("people") or {}).get(them)
        if sid == "p2":
            M = 1 - M.T
        scores = None if people is None or any(v is None for v in people) else people
        pe = E.combine([(1.0, M, scores, None)], "people", 4)
        ewp = np.asarray(pe["ewp"], float)
        if recs is None:
            recs = {rc["obs"]["turn"]: rc for rc in human_snapshots(replay, reg)
                    if rc["kind"] == "turn" and rc["obs"]["perspective"] == "spectator"}
        rec = recs.get(x["turn"])
        if rec is None:
            continue
        rows.append({"id": x["id"], "group": x["group"], "kind": x["kind"], "sid": sid, "turn": x["turn"],
                     "won": float(x["winner"] == sid), "kept": kept,
                     "chosen": float(ewp[r["moves"][sid].index(human)]), "best": float(ewp.max()),
                     "_rec": rec})
    return rows


def _oof(xs: dict[str, np.ndarray], y: np.ndarray, groups: np.ndarray, folds: int = 5, seed: int = 0
         ) -> dict[str, np.ndarray]:
    from sklearn.linear_model import LogisticRegression

    uniq = sorted(set(groups.tolist()))
    random.Random(seed).shuffle(uniq)
    at = {g: i % folds for i, g in enumerate(uniq)}
    fold = np.array([at[g] for g in groups])
    out = {k: np.zeros(len(y)) for k in xs}
    for f in range(folds):
        tr, te = fold != f, fold == f
        for k, x in xs.items():
            out[k][te] = LogisticRegression(C=1e6, max_iter=1000).fit(x[tr], y[tr]).predict_proba(x[te])[:, 1]
    return out


def _ll(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def _paired(d: np.ndarray, groups: np.ndarray, boots: int = 4000, seed: int = 0) -> dict[str, Any]:
    uniq, inv = np.unique(groups, return_inverse=True)
    s, n = np.bincount(inv, d), np.bincount(inv).astype(float)
    idx = np.random.default_rng(seed).integers(0, len(uniq), (boots, len(uniq)))
    b = s[idx].sum(1) / n[idx].sum(1)
    return {"delta": round(float(d.mean()), 5), "ci95": [round(float(x), 5) for x in np.percentile(b, [2.5, 97.5])]}


def report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from sklearn.linear_model import LogisticRegression

    from vgc.sim.validity import _logit, _score

    y = np.array([r["won"] for r in rows])
    groups = np.array([r["group"] for r in rows])
    wp, chosen, best = (np.array([r[k] for r in rows]) for k in ("wp", "chosen", "best"))
    regret = best - chosen
    lw, lc = _logit(wp), _logit(chosen)
    oof = _oof({"wp": lw.reshape(-1, 1), "chosen": lc.reshape(-1, 1), "wp+chosen": np.stack([lw, lc], 1),
                "wp+regret": np.stack([lw, regret], 1)}, y, groups)
    ll = {k: _ll(v, y) for k, v in oof.items()}
    gate = _paired(ll["chosen"] - ll["wp"], groups)
    coef = LogisticRegression(C=1e6, max_iter=1000).fit(np.stack([lw, regret], 1), y).coef_[0]
    short = lambda s: {k: s[k] for k in ("games", "groups", "auc", "auc_95ci", "logloss", "logloss_delta",
                                         "logloss_delta_95ci")}
    bins = np.clip((chosen * 10).astype(int), 0, 9)
    return {
        "positions": len(rows), "groups": len(set(groups.tolist())),
        "kept_share": round(float(np.mean([r["kept"] for r in rows])), 4),
        "raw": {k: short(_score(v, y, groups, k, 2000, 0)) | {"positions": int(len(y))}
                for k, v in (("wp", wp), ("chosen", chosen), ("best", best))},
        "recalibrated_logloss": {k: round(float(v.mean()), 5) for k, v in ll.items()},
        "gate": gate | {"pass": bool(gate["ci95"][1] < 0),
                        "reading": "chosen beats wp" if gate["ci95"][1] < 0 else
                        "wp beats chosen" if gate["ci95"][0] > 0 else "not distinguishable"},
        "stacked": _paired(ll["wp+chosen"] - ll["wp"], groups),
        "regret": {"coef_beside_wp": round(float(coef[1]), 3), "adds": _paired(ll["wp+regret"] - ll["wp"], groups),
                   "mean": round(float(regret.mean()), 4), "share_zero": round(float(np.mean(regret < 1e-9)), 4)},
        "calibration_chosen": [{"bin": [b / 10, (b + 1) / 10], "n": int((bins == b).sum()),
                                "mean": round(float(chosen[bins == b].mean()), 3), "won": round(float(y[bins == b].mean()), 3)}
                               for b in range(10) if (bins == b).sum()],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", type=int, default=800)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=3)
    a = ap.parse_args()
    import policy_vs_people as PV
    from vgc.data.snapshots import replay_group
    from vgc.data.splits import load_rules
    from vgc.meta import pool, replays
    from vgc.regulation import load_regulation
    from vgc.wp.models import OPEN, in_battle_version, predict_records
    from vgc.wp.tools import _load

    reg = load_regulation("reg_mc")
    rules = load_rules(reg)
    held = [r for fmt in pool.formats_for(reg) for r in replays.cached(fmt)
            if rules.split_of("human", r["id"], [], replay_group(r)) == "heldout_human"]
    random.Random(a.seed).shuffle(held)
    with Pool(a.workers, initializer=PV._init) as p:
        rows = [x for rs in p.imap_unordered(_game, held[:a.games], chunksize=2) for x in rs]
    version = in_battle_version(reg.id, OPEN)
    model, fz = _load(reg, version)
    preds = predict_records(model, [r.pop("_rec") for r in rows], fz)
    for r, p1 in zip(rows, preds):
        r["wp"] = float(p1) if r["sid"] == "p1" else 1 - float(p1)
    res = {"settings": {"games": a.games, "seed": a.seed, "wp_model": version}, **report(rows)}
    OUT.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({k: v for k, v in res.items() if k != "calibration_chosen"}, indent=1))


if __name__ == "__main__":
    main()
