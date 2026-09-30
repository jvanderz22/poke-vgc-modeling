"""Does the engine's answer in a 1v1 predict how human 1v1s end? (PLAN-v3 step 3.6)

Every held-out, human-vs-human, open-sheet replay that reaches a turn with one Pokémon left a side
is stopped there and asked twice, from the stands, which is how a replay sees it:

  model    the served open-sheet WP (`live.wp`), P(player 1 wins), as the app computes it
  engine   `vgc.wp.endgame` over the replay's own Observer, both spreads integrated, solved by
           `sidecar/showdown/endgame-solver.js` at depth 2 for every position and then depth 3 for
           each position that finishes inside `--cap` seconds (depth 2 gets twice that); a game takes the deepest depth at
           which all of its positions finished

and both are scored against who won. The unit is the replay's group (a Bo3 series or a player
pair), so intervals are cluster bootstraps over groups (principle 5). Played-out games and
forfeits are reported apart; neither is known at the decision point, so neither selects it.

The engine assumes best play from both sides, and this corpus is ~1100-rated, so it can come out
either way. That is the point of running it.

    .venv/bin/python scripts/analysis/solver_vs_humans.py --workers 6 --cap 600
    .venv/bin/python scripts/analysis/solver_vs_humans.py --score-only
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import numpy as np

from vgc import paths
from vgc.regulation import load_regulation
from vgc.wp import endgame, solver

OUT = paths.DATA / "analysis" / "reg_mc" / "solver_vs_humans.json"
DEPTHS = (2, 3)
EPS = 1e-3            # log loss clips here: an engine that says 1.0 and loses scores ~6.9, not inf


def collect(reg, version: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Each qualifying game at its first 1v1: the positions per depth, the model's number, the label."""
    from vgc.data.snapshots import replay_group
    from vgc.data.splits import load_rules
    from vgc.meta import pool, replays
    from vgc.web.endgames import _eligible
    from vgc.web.live import wp

    rules = load_rules(reg)
    counts = {"heldout_ots_human": 0, "no_1v1": 0, "not_built": 0, "unfinished": 0, "games": 0}
    dropped: dict[str, int] = {}
    rows = []
    for fmt in pool.formats_for(reg):
        for replay in replays.cached(fmt):
            records = _eligible(reg, replay, rules)
            if records is None:
                continue
            counts["heldout_ots_human"] += 1
            label = records[0]["label"]
            if label["winner"] not in ("p1", "p2"):
                counts["unfinished"] += 1
                continue
            battle = endgame.from_replay(reg, replay)
            if battle is None:
                counts["no_1v1"] += 1
                continue
            plans = {d: endgame.plan(reg, battle, {**solver.SEARCH, "depth": d}) for d in DEPTHS}
            if not plans[DEPTHS[0]]["eligible"]:
                counts["not_built"] += 1
                why = plans[DEPTHS[0]]["reason"] or ""
                key = "a volatile" if "cannot set up" in why else why.split(" is ")[-1] if " is " in why else why
                dropped[key] = dropped.get(key, 0) + 1
                continue
            counts["games"] += 1
            state = battle.rp.state
            rows.append({
                "id": replay["id"], "group": replay_group(replay), "turn": state.turn,
                "winner": label["winner"], "ended_by": label.get("ended_by"),
                "model": round(wp(reg, state, version, k=4)["wp"], 4),
                "mons": {sid: endgame._left(state, sid)[0].species for sid in ("p1", "p2")},
                "jobs": {d: [{"class": j["class"], "weight": j["weight"], "position": j["position"]}
                             for j in plans[d]["jobs"]] for d in DEPTHS},
            })
    return rows, counts | {"dropped": dropped}


def run_capped(pos: dict[str, Any], cap: float) -> dict[str, Any] | None:
    """One solve through the cache, or None if it runs past `cap` seconds."""
    hit = solver._cached().get(solver.position_key(pos))
    if hit is not None:
        return hit
    p = subprocess.Popen(["node", str(solver.SOLVER), str(paths.SHOWDOWN)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = p.communicate(json.dumps(pos), timeout=cap)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        return None
    if p.returncode:
        raise RuntimeError(f"solver failed: {err.strip()[-300:]}")
    result = json.loads(out)
    solver.remember(pos, result)
    return result


def solve(rows: list[dict[str, Any]], workers: int, cap: float) -> None:
    """Every game's engine answer at the deepest depth all its positions finished at."""
    for d in DEPTHS:
        todo = [(r, i, j["position"]) for r in rows for i, j in enumerate(r["jobs"][d])]
        t0, done = time.time(), 0
        with ThreadPoolExecutor(workers) as pool:
            futures = {pool.submit(run_capped, pos, 2 * cap if d == DEPTHS[0] else cap): (r, i)
                       for r, i, pos in todo}
            for f in as_completed(futures):
                r, i = futures[f]
                r["jobs"][d][i]["result"] = f.result()
                done += 1
                if done % 25 == 0 or done == len(todo):
                    print(f"  depth {d}: {done}/{len(todo)} positions, {time.time() - t0:.0f}s", flush=True)
    for r in rows:
        for d in sorted(DEPTHS, reverse=True):
            jobs = r["jobs"][d]
            if all(j.get("result") for j in jobs):
                total = sum(j["weight"] for j in jobs)
                r["engine"] = round(sum(j["weight"] * j["result"]["value"] for j in jobs) / total, 4)
                r["leaf_mass"] = round(sum(j["weight"] * j["result"]["leaf_mass"] for j in jobs) / total, 4)
                r["depth"] = d
                break


def score(rows: list[dict[str, Any]], boots: int = 4000, seed: int = 11) -> dict[str, Any]:
    """Brier and log loss of each, the engine minus the model with a cluster bootstrap over groups,
    and the engine's calibration."""
    rng = np.random.default_rng(seed)
    unsolved = sum(r.get("engine") is None for r in rows)
    rows = [r for r in rows if r.get("engine") is not None]
    groups = sorted({r["group"] for r in rows})
    gi = {g: i for i, g in enumerate(groups)}

    def per_game(pred: str) -> tuple[np.ndarray, np.ndarray]:
        y = np.array([r["winner"] == "p1" for r in rows], float)
        p = np.array([r[pred] for r in rows], float)
        brier = (p - y) ** 2
        pc = np.clip(p, EPS, 1 - EPS)
        ll = -(y * np.log(pc) + (1 - y) * np.log(1 - pc))
        return brier, ll

    def by_group(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        s, n = np.zeros(len(groups)), np.zeros(len(groups))
        for r, v in zip(rows, x):
            s[gi[r["group"]]] += v
            n[gi[r["group"]]] += 1
        return s, n

    out: dict[str, Any] = {"games": len(rows), "groups": len(groups), "unsolved": unsolved}
    idx = rng.integers(0, len(groups), (boots, len(groups)))
    for metric, k in (("brier", 0), ("logloss", 1)):
        e, m = per_game("engine")[k], per_game("model")[k]
        (se, n), (sm, _) = by_group(e), by_group(m)
        diff = (se[idx].sum(1) - sm[idx].sum(1)) / n[idx].sum(1)
        lo, hi = np.percentile(diff, [2.5, 97.5])
        out[metric] = {"engine": round(float(e.mean()), 4), "model": round(float(m.mean()), 4),
                       "engine_minus_model": round(float(e.mean() - m.mean()), 4),
                       "ci95": [round(float(lo), 4), round(float(hi), 4)],
                       "verdict": "engine better" if hi < 0 else "model better" if lo > 0 else "not distinguishable"}
    bins = [(0, 0.05), (0.05, 0.35), (0.35, 0.65), (0.65, 0.95), (0.95, 1.0001)]
    for pred in ("engine", "model"):
        cal = []
        for lo, hi in bins:
            sel = [r for r in rows if lo <= r[pred] < hi]
            if sel:
                cal.append({"bin": [lo, round(min(hi, 1), 2)], "n": len(sel),
                            "mean_pred": round(float(np.mean([r[pred] for r in sel])), 3),
                            "p1_won": round(float(np.mean([r["winner"] == "p1" for r in sel])), 3)})
        out[f"calibration_{pred}"] = cal
    # "Called" games: the engine says one side wins outright, at least 95% either way.
    called = [r for r in rows if r["engine"] >= 0.95 or r["engine"] <= 0.05]
    if called:
        right = [((r["engine"] >= 0.5) == (r["winner"] == "p1")) for r in called]
        out["engine_called"] = {"n": len(called), "called_side_won": round(float(np.mean(right)), 3),
                                "model_mean_on_called_side": round(float(np.mean(
                                    [r["model"] if r["engine"] >= 0.5 else 1 - r["model"] for r in called])), 3)}
    out["depths"] = {str(d): sum(r.get("depth") == d for r in rows) for d in DEPTHS}
    out["mean_leaf_mass"] = round(float(np.mean([r["leaf_mass"] for r in rows])), 4)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--cap", type=float, default=600, help="seconds a depth-3 position may take")
    ap.add_argument("--score-only", action="store_true", help="re-score the saved rows")
    args = ap.parse_args()
    reg = load_regulation("reg_mc")

    if args.score_only:
        blob = json.loads(OUT.read_text())
        rows, counts = blob["rows"], blob["counts"]
    else:
        from vgc.wp.models import OPEN, in_battle_version

        version = in_battle_version(reg.id, OPEN)
        rows, counts = collect(reg, version)
        print(f"{counts['games']} games to solve", counts, flush=True)
        solve(rows, args.workers, args.cap)
        counts["version"] = version
    result = {"all": score(rows),
              "played_out": score([r for r in rows if r["ended_by"] == "normal"]),
              "forfeit": score([r for r in rows if r["ended_by"] != "normal"])}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"counts": counts, "result": result, "cap": args.cap,
                               "rows": [{k: v for k, v in r.items() if k != "jobs"} |
                                        {"positions": {str(d): [{"class": j["class"], "weight": round(j["weight"], 4),
                                                                 "value": (j.get("result") or {}).get("value")}
                                                                for j in r["jobs"][d]] for d in DEPTHS}}
                                        if "jobs" in r else r for r in rows]}, indent=1) + "\n")
    print(json.dumps({"counts": counts, "result": result}, indent=1))


if __name__ == "__main__":
    main()
