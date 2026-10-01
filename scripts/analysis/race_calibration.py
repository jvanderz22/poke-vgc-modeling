"""The doubles race's calibration, fitted on training-split games (PLAN-endgame-doubles, stage 3).

Every training-split open-sheet game at its first turn with two or fewer a side and not a 1v1 is
valued by the raw race (`race_doubles: true`, depth 0) over its move orders, and maps from that
to P(p1 wins) are fitted and compared out of fold (grouped 5-fold, by player group):

  calibrated    sigmoid(a · logit(race) + b), the solver's `MELEE_CALIBRATION`
  blend         sigmoid(a · logit(race) + b · logit(HP share) + c · count lead + d), `MELEE_BLEND`
  blend_boosts  the same plus e · (net stat stages, p1's minus p2's), `MELEE_BLEND_BOOSTS`

`--answers N` instead runs N training games exactly as the page does (`vgc.web.solving.LIVE`, the
heaviest three move orders) and fits the temperature on the answer, `doubles.TEMPER`:

  temper        sigmoid(a · logit(answer) + b)

The held-out games the check scores (`solver_vs_humans.py`) are never read here. The solver's
constants are copied from this script's output by hand, under a new `race_doubles` name when the
answers change.

    .venv/bin/python scripts/analysis/race_calibration.py              # collect, solve, fit
    .venv/bin/python scripts/analysis/race_calibration.py --fit-only   # refit the saved rows
    .venv/bin/python scripts/analysis/race_calibration.py --search '{"race_stages": true}' --tag stages
    .venv/bin/python scripts/analysis/race_calibration.py --answers 1500   # about an hour on 6 workers
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


def collect(reg, workers: int, search: dict[str, Any], limit: int = 0, top: int = 0) -> list[dict[str, Any]]:
    """Each game's value over its move orders (the heaviest `top`, renormalized, if given), with
    what the maps read beside it."""
    from solver_vs_humans import run_capped
    from vgc.data.snapshots import human_snapshots, replay_group
    from vgc.data.splits import load_rules
    from vgc.meta import pool, replays
    from vgc.web.endgames import is_bot

    rules = load_rules(reg)
    games = []
    for fmt in pool.formats_for(reg):
        for replay in replays.cached(fmt):
            if limit and len(games) >= limit:
                break
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
                if top:
                    kept = plan["jobs"][:top]
                    w = sum(j["weight"] for j in kept)
                    plan = {**plan, "jobs": [{**j, "weight": j["weight"] / w} for j in kept]}
                games.append((replay, recs[0]["label"]["winner"], plan))
    todo = [(i, j) for i, (_, _, plan) in enumerate(games) for j in plan["jobs"]]
    print(f"{len(games)} games, {len(todo)} positions", flush=True)
    with ThreadPoolExecutor(workers) as ex:
        values = list(ex.map(lambda t: run_capped(t[1]["position"], 180), todo))
    rows = []
    for i, (replay, winner, plan) in enumerate(games):
        js = [(j, v) for (k, j), v in zip(todo, values) if k == i and v is not None]
        w = sum(j["weight"] for j, _ in js)
        if not w:
            continue
        rows.append({"id": replay["id"], "group": replay_group(replay), "winner": winner, "kind": plan["kind"],
                     "value": round(sum(j["weight"] * v["value"] for j, v in js) / w, 4),
                     "forced": any(v.get("forced") for _, v in js), **reads(plan["jobs"][0]["position"])})
    return rows


def reads(pos: dict[str, Any]) -> dict[str, Any]:
    """What the blends read from a position besides the race: HP share, count lead, net stages."""
    s = pos["setup"]
    hp, boosts = s["hp"], s.get("boosts", {})
    stages = lambda sid: sum(sum(b.values()) for b in boosts.get(sid, []))
    return {"share": round(sum(hp["p1"]) / max(1, sum(hp["p1"]) + sum(hp["p2"])), 4),
            "count": pos["active"]["p1"] - pos["active"]["p2"], "stages": stages("p1") - stages("p2")}


def fit(rows: list[dict[str, Any]], answers: bool = False) -> dict[str, Any]:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import GroupKFold

    y = np.array([r["winner"] == "p1" for r in rows], int)
    groups = np.array([r["group"] for r in rows])
    is2v2 = np.array([r["kind"] == "2v2" for r in rows])
    if answers:
        designs = {"temper": [[logit(r["value"])] for r in rows]}
    else:
        designs = {"calibrated": [[logit(r["value"])] for r in rows],
                   "blend": [[logit(r["value"]), logit(r["share"]), r["count"]] for r in rows],
                   "blend_boosts": [[logit(r["value"]), logit(r["share"]), r["count"], r["stages"]] for r in rows]}
    out: dict[str, Any] = {"games": len(rows), "2v2": int(is2v2.sum()), "maps": {}}
    if answers:
        q = np.clip([r["value"] for r in rows], 1e-3, 1 - 1e-3)
        ll = -(y * np.log(q) + (1 - y) * np.log(1 - q))
        out["maps"]["as is"] = {"cv_logloss": {"all": round(float(ll.mean()), 4), "2v2": round(float(ll[is2v2].mean()), 4),
                                               "2v1|1v2": round(float(ll[~is2v2].mean()), 4)}}
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
    ap.add_argument("--search", default="{}", help="race settings over the raw race's, as JSON")
    ap.add_argument("--tag", help="written beside the default output as <name>_<tag>.json")
    ap.add_argument("--answers", type=int, metavar="N", help="the live answer on N games, and its temperature")
    args = ap.parse_args()
    reg = load_regulation("reg_mc")
    tag = args.tag or ("answers" if args.answers else None)
    out = OUT.with_name(f"{OUT.stem}_{tag}.json") if tag else OUT
    extra = json.loads(args.search)
    if args.answers:
        from vgc.web.solving import LIVE
        search, limit, top = {**LIVE, **extra}, args.answers, 3
    else:
        search, limit, top = {**doubles.SEARCH, "depth": 0, "race_doubles": True, **extra}, 0, 0
    rows = json.loads(out.read_text())["rows"] if args.fit_only else collect(reg, args.workers, search, limit, top)
    result = fit(rows, bool(args.answers)) | {"search": extra}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"result": result, "rows": rows}) + "\n")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
