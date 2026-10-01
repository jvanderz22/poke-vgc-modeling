"""The doubles race's calibration, fitted on training-split games (PLAN-endgame-doubles, stage 3).

Every training-split open-sheet game at its first turn with two or fewer a side and not a 1v1 is
valued by the raw race (`race_doubles: true`, depth 0) over its move orders, and two maps from
that to P(p1 wins) are fitted and compared out of fold (grouped 5-fold, by player group):

  calibrated   sigmoid(a · logit(race) + b), the solver's `MELEE_CALIBRATION`
  blend        sigmoid(a · logit(race) + b · logit(HP share) + c · count lead + d), `MELEE_BLEND`

The held-out games the check scores (`solver_vs_humans.py`) are never read here. The solver's
constants are copied from this script's output by hand, under a new `race_doubles` name when the
answers change.

    .venv/bin/python scripts/analysis/race_calibration.py              # collect, solve, fit
    .venv/bin/python scripts/analysis/race_calibration.py --fit-only   # refit the saved rows
"""

from __future__ import annotations

import argparse
import json
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np

from vgc import paths
from vgc.regulation import load_regulation
from vgc.wp import doubles

OUT = paths.DATA / "analysis" / "reg_mc" / "race_calibration.json"
EPS = 1 / 128           # as the solver clips before a logit


def logit(p: float) -> float:
    q = min(1 - EPS, max(EPS, p))
    return math.log(q / (1 - q))


def collect(reg, workers: int) -> list[dict[str, Any]]:
    from solver_vs_humans import run_capped
    from vgc.data.snapshots import human_snapshots, replay_group
    from vgc.data.splits import load_rules
    from vgc.meta import pool, replays
    from vgc.web.endgames import is_bot

    rules = load_rules(reg)
    search = {**doubles.SEARCH, "depth": 0, "race_doubles": True}
    games = []
    for fmt in pool.formats_for(reg):
        for replay in replays.cached(fmt):
            if rules.split_of("human", replay["id"], [], replay_group(replay)) != "train":
                continue
            if any(is_bot(p) for p in replay.get("players") or []):
                continue
            recs = [r for r in human_snapshots(replay, reg)
                    if r["obs"]["perspective"] == "spectator" and not r["meta"]["approx"]]
            if not recs or not recs[0]["meta"]["ots"] or recs[0]["label"]["winner"] not in ("p1", "p2"):
                continue
            battle = doubles.from_replay(reg, replay)
            if battle is None:
                continue
            plan = doubles.plan(reg, battle, search)
            if plan.get("eligible"):
                games.append((replay, recs[0]["label"]["winner"], plan))
    todo = [(i, j) for i, (_, _, plan) in enumerate(games) for j in plan["jobs"]]
    print(f"{len(games)} games, {len(todo)} positions", flush=True)
    with ThreadPoolExecutor(workers) as ex:
        values = list(ex.map(lambda t: run_capped(t[1]["position"], 120), todo))
    rows = []
    for i, (replay, winner, plan) in enumerate(games):
        js = [(j, v["value"]) for (k, j), v in zip(todo, values) if k == i and v is not None]
        w = sum(j["weight"] for j, _ in js)
        if not w:
            continue
        pos = plan["jobs"][0]["position"]
        hp = pos["setup"]["hp"]
        rows.append({"id": replay["id"], "group": replay_group(replay), "winner": winner, "kind": plan["kind"],
                     "race": round(sum(j["weight"] * v for j, v in js) / w, 4),
                     "share": round(sum(hp["p1"]) / max(1, sum(hp["p1"]) + sum(hp["p2"])), 4),
                     "count": pos["active"]["p1"] - pos["active"]["p2"]})
    return rows


def fit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import GroupKFold

    y = np.array([r["winner"] == "p1" for r in rows], int)
    groups = np.array([r["group"] for r in rows])
    is2v2 = np.array([r["kind"] == "2v2" for r in rows])
    designs = {"calibrated": [[logit(r["race"])] for r in rows],
               "blend": [[logit(r["race"]), logit(r["share"]), r["count"]] for r in rows]}
    out: dict[str, Any] = {"games": len(rows), "2v2": int(is2v2.sum()), "maps": {}}
    for name, X in designs.items():
        X = np.array(X)
        p = np.zeros(len(y))
        for tr, te in GroupKFold(5).split(X, y, groups):
            p[te] = LogisticRegression(C=1e6, max_iter=5000).fit(X[tr], y[tr]).predict_proba(X[te])[:, 1]
        m = LogisticRegression(C=1e6, max_iter=5000).fit(X, y)
        q = np.clip(p, 1e-3, 1 - 1e-3)
        ll = -(y * np.log(q) + (1 - y) * np.log(1 - q))
        out["maps"][name] = {"coef": [round(float(c), 4) for c in m.coef_[0]], "intercept": round(float(m.intercept_[0]), 4),
                             "cv_logloss": {"all": round(float(ll.mean()), 4), "2v2": round(float(ll[is2v2].mean()), 4),
                                            "2v1|1v2": round(float(ll[~is2v2].mean()), 4)}}
    return out


def main() -> None:
    import sys
    sys.path.insert(0, str(paths.ROOT / "scripts" / "analysis"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--fit-only", action="store_true", help="refit the saved rows")
    args = ap.parse_args()
    reg = load_regulation("reg_mc")
    rows = json.loads(OUT.read_text())["rows"] if args.fit_only else collect(reg, args.workers)
    result = fit(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"result": result, "rows": rows}) + "\n")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
