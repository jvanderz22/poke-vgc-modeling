"""Does the engine's answer in a 1v1 predict how human 1v1s end? (PLAN-v3 step 3.6)

Every held-out, human-vs-human, open-sheet replay that reaches a turn with one Pokémon left a side
is stopped there and asked twice, from the stands, which is how a replay sees it:

  model    the served open-sheet WP (`live.wp`), P(player 1 wins), as the app computes it
  engine   `vgc.wp.endgame` over the replay's own Observer, both spreads integrated, solved by
           `sidecar/showdown/endgame-solver.js` at each of `--depths` (2,3 is about 20 min and
           80 min on 6 workers since PLAN-v3 step 8), each position capped at `--cap` seconds; a
           game takes the deepest depth at which all of its positions finished

and both are scored against who won. The unit is the replay's group (a Bo3 series or a player
pair), so intervals are cluster bootstraps over groups (principle 5). Played-out games and
forfeits are reported apart; neither is known at the decision point, so neither selects it.

The engine assumes best play from both sides, and this corpus is ~1100-rated, so it can come out
either way. That is the point of running it.

    .venv/bin/python scripts/analysis/solver_vs_humans.py --workers 6 --cap 180 --depths 2,3
    .venv/bin/python scripts/analysis/solver_vs_humans.py --score-only
    .venv/bin/python scripts/analysis/solver_vs_humans.py --sheets closed ...   # the same, on closed sheets

`--endgame doubles` asks the same of the first turn where neither side has more than two Pokémon
left and it is not a 1v1 (2v2, 2v1, 1v2: `vgc.wp.doubles`, PLAN-endgame-doubles stage 3), at
depth 1 by default, reported by kind as well. A 2v1 and a 1v2 are one state with p1 and p2 named
the other way round, so `2v1|1v2` pools them, and that pool is what decides a kind with three left;
the halves are only for reading.

    .venv/bin/python scripts/analysis/solver_vs_humans.py --endgame doubles --depths 1 --cap 600

Solved somewhere else (`vgc.wp.offload`, `scripts/cloud/kaggle_solve.sh`): export the positions, solve
them remotely, merge, and the run above then finds them cached.

    .venv/bin/python scripts/analysis/solver_vs_humans.py --depths 2,3 --export .vgc/jobs/humans.jsonl
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
from vgc.wp import doubles, endgame, solver

OUT = {("1v1", "open"): paths.DATA / "analysis" / "reg_mc" / "solver_vs_humans.json",
       ("1v1", "closed"): paths.DATA / "analysis" / "reg_mc" / "solver_vs_humans_closed.json",
       ("doubles", "open"): paths.DATA / "analysis" / "reg_mc" / "solver_vs_humans_doubles.json",
       ("doubles", "closed"): paths.DATA / "analysis" / "reg_mc" / "solver_vs_humans_doubles_closed.json"}
# On a closed sheet the answer covers only the set pairs solved; under this much left unsolved
# it covers most of the belief (PLAN-endgame-doubles, stage 0).
COVERED = 0.2
DEPTHS = (2, 3)
# Under this much of the answer resting on HP share, the engine has settled the position rather
# than guessed it; that subset is the test of best play (PLAN-v3 step 3.6).
SETTLED = 0.1
# With --top N, only the heaviest N positions of each game are solved (the page's doubles answer
# solves three).
TOP = 0
EPS = 1e-3            # log loss clips here: an engine that says 1.0 and loses scores ~6.9, not inf


# `--bulk`: every guessed spread in a position rewritten, its Speed kept (loose end, PLAN-v4 step 5).
# The engine assumes the main attacking stat first, then HP, then the defences (`solver.spread`);
# nothing shows what people build instead, so these are the two other shapes builds take.
BULK = {"hp_first": ("hp", "main", "def", "spd"), "defences": ("hp", "def", "spd", "main")}


def rebulk(reg, text: str, mode: str) -> str:
    """A side's set texts with each spread's non-Speed points reallocated in `BULK[mode]`'s order."""
    from vgc.regulation import to_id

    label = {"hp": "HP", "atk": "Atk", "def": "Def", "spa": "SpA", "spd": "SpD", "spe": "Spe"}
    back = {v: k for k, v in label.items()}
    out = []
    for block in text.split("\n\n"):
        lines = block.split("\n")
        ev = next((i for i, x in enumerate(lines) if x.startswith("EVs: ")), None)
        if ev is None:
            out.append(block)
            continue
        sp = {back[part.split()[1]]: int(part.split()[0]) for part in lines[ev][5:].split(" / ")}
        moves = [to_id(x[2:]) for x in lines if x.startswith("- ")]
        kinds = [(reg.dex.get_move(m) or {}).get("category") for m in moves]
        main = "spa" if kinds.count("Special") > kinds.count("Physical") else "atk"
        left, cap = reg.sp_budget - sp.get("spe", 0), reg.sp_per_stat_cap
        new = {k: 0 for k in label} | {"spe": sp.get("spe", 0)}
        for stat in BULK[mode]:
            stat = main if stat == "main" else stat
            new[stat] = min(cap, left)
            left -= new[stat]
        lines[ev] = "EVs: " + " / ".join(f"{v} {label[k]}" for k, v in new.items() if v)
        out.append("\n".join(lines))
    return "\n\n".join(out)


def collect(reg, version: str, sheets: str = "open", endgame_kind: str = "1v1",
            depths: tuple[int, ...] = DEPTHS, extra: dict[str, Any] | None = None, bulk: str | None = None
            ) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Each qualifying game at its first 1v1: the positions per depth, the model's number, the label.
    `sheets` picks the regime; a closed-sheet game is solved over pairs of likely sets
    (`endgame.pair_candidates`), and what those leave out is the row's `unsolved`."""
    from vgc.data.snapshots import replay_group
    from vgc.data.splits import load_rules
    from vgc.meta import pool, replays
    from vgc.web.endgames import _eligible
    from vgc.web.live import wp

    rules = load_rules(reg)
    counts = {"heldout_human": 0, "sheets": sheets, "no_1v1": 0, "not_built": 0, "unfinished": 0, "games": 0}
    dropped: dict[str, int] = {}
    rows = []
    for fmt in pool.formats_for(reg):
        for replay in replays.cached(fmt):
            records = _eligible(reg, replay, rules, ots=sheets == "open")
            if records is None:
                continue
            counts["heldout_human"] += 1
            label = records[0]["label"]
            if label["winner"] not in ("p1", "p2"):
                counts["unfinished"] += 1
                continue
            adapter = doubles if endgame_kind == "doubles" else endgame
            battle = adapter.from_replay(reg, replay)
            if battle is None:
                counts["no_1v1"] += 1
                continue
            # Planned once: only the search depth differs between depths, and weighing a closed
            # sheet's set pairs is the slow part.
            base = doubles.SEARCH if endgame_kind == "doubles" else solver.SEARCH
            plan = adapter.plan(reg, battle, {**base, **(extra or {}), "depth": depths[0]})
            if TOP and plan.get("eligible") and len(plan["jobs"]) > TOP:
                # As the page solves it: the heaviest move orders, their weight renormalized.
                kept = plan["jobs"][:TOP]
                w = sum(j["weight"] for j in kept)
                plan = {**plan, "jobs": [{**j, "weight": j["weight"] / w} for j in kept]}
            if not plan["eligible"]:
                counts["not_built"] += 1
                why = plan["reason"] or ""
                key = ("a volatile" if "cannot set up" in why else "Revival Blessing" if "Revival Blessing" in why
                       else why.split(" is ")[-1] if " is " in why else why)
                dropped[key] = dropped.get(key, 0) + 1
                continue
            counts["games"] += 1
            if bulk:
                plan = {**plan, "jobs": [{**j, "position": {**j["position"], "p1": rebulk(reg, j["position"]["p1"], bulk),
                                                            "p2": rebulk(reg, j["position"]["p2"], bulk)}}
                                         for j in plan["jobs"]]}
            state = battle.rp.state
            rows.append({
                "id": replay["id"], "group": replay_group(replay), "turn": state.turn,
                "winner": label["winner"], "ended_by": label.get("ended_by"),
                "model": round(wp(reg, state, version, k=4)["wp"], 4),
                "mons": {sid: ([m.species for m in doubles.actives(state, sid)] if endgame_kind == "doubles"
                               else endgame._left(state, sid)[0].species) for sid in ("p1", "p2")},
                "kind": plan.get("kind", "1v1"),
                "unsolved": plan["unsolved"],
                "jobs": {d: [{"class": j.get("class") or j.get("order"), "weight": j["weight"],
                              "position": {**j["position"], "search": {**j["position"]["search"], "depth": d}}}
                             for j in plan["jobs"]] for d in depths},
            })
    return rows, counts | {"dropped": dropped}


def run_capped(pos: dict[str, Any], cap: float) -> dict[str, Any] | None:
    """One solve through the cache, or None if it runs past `cap` seconds."""
    key = solver.position_key(pos)
    hit = solver._cached().get(key)
    if hit is not None:
        return hit
    if solver.timed_out().get(key, 0.0) >= cap:
        return None
    p = subprocess.Popen(["node", str(solver.SOLVER), str(paths.SHOWDOWN)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = p.communicate(json.dumps(pos), timeout=cap)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        with solver._cache_lock:
            with solver.TIMEOUTS.open("a") as fh:
                fh.write(json.dumps({"key": key, "cap": cap}) + "\n")
        return None
    if p.returncode:
        raise RuntimeError(f"solver failed: {err.strip()[-1500:]}")
    result = json.loads(out)
    solver.remember(pos, result)
    return result


def solve(rows: list[dict[str, Any]], workers: int, cap: float, depths: tuple[int, ...]) -> None:
    """Every game's engine answer at the deepest depth all its positions finished at."""
    for d in depths:
        todo = [(r, i, j["position"]) for r in rows for i, j in enumerate(r["jobs"][d])]
        t0, done = time.time(), 0
        with ThreadPoolExecutor(workers) as pool:
            futures = {pool.submit(run_capped, pos, cap): (r, i)
                       for r, i, pos in todo}
            for f in as_completed(futures):
                r, i = futures[f]
                try:
                    r["jobs"][d][i]["result"] = f.result()
                except RuntimeError as e:
                    # One simulator crash is one unsolved position, not the run; the error is kept
                    # so the position can be reproduced and the adapter or solver fixed.
                    r["jobs"][d][i]["result"] = None
                    r.setdefault("errors", []).append({"depth": d, "class": r["jobs"][d][i]["class"],
                                                       "error": str(e)[-600:]})
                done += 1
                if done % 25 == 0 or done == len(todo):
                    print(f"  depth {d}: {done}/{len(todo)} positions, {time.time() - t0:.0f}s", flush=True)
    for r in rows:
        for d in sorted(depths, reverse=True):
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
    if not rows:
        return {"games": 0, "unsolved": unsolved}
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
    out["depths"] = {str(d): n for d in sorted({r.get("depth") for r in rows if r.get("depth")})
                     if (n := sum(r.get("depth") == d for r in rows))}
    out["mean_leaf_mass"] = round(float(np.mean([r["leaf_mass"] for r in rows])), 4)
    return out


def j_ok(r: dict[str, Any], d: int) -> bool:
    return any(j.get("result") is not None for j in r["jobs"][d])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--cap", type=float, default=180, help="seconds a position may take")
    ap.add_argument("--depths", default="2", help="depths to solve, comma-separated (of 2,3)")
    ap.add_argument("--score-only", action="store_true", help="re-score the saved rows")
    ap.add_argument("--export", metavar="JOBS", help="write the uncached positions to solve elsewhere, and stop")
    ap.add_argument("--sheets", choices=("open", "closed"), default="open",
                    help="the regime: open-sheet games, or closed-sheet games with both sides' sets from the belief")
    ap.add_argument("--endgame", choices=("1v1", "doubles"), default="1v1",
                    help="the first 1v1, or the first turn with two or fewer a side (2v2, 2v1, 1v2)")
    ap.add_argument("--search", default="{}", help="search settings over the default, as JSON")
    ap.add_argument("--tag", help="written beside the default output as <name>_<tag>.json")
    ap.add_argument("--bulk", choices=tuple(BULK), help="every guessed spread rebuilt this way, Speed kept")
    ap.add_argument("--top", type=int, default=0, help="solve only each game's heaviest N positions")
    ap.add_argument("--temper", action="store_true",
                    help="score each answer through `doubles.temper`, as the page shows it; written as <name>_tempered.json")
    args = ap.parse_args()
    global TOP
    TOP = args.top
    reg = load_regulation("reg_mc")
    extra = json.loads(args.search)
    out_path = OUT[(args.endgame, args.sheets)]
    if args.tag:
        out_path = out_path.with_name(f"{out_path.stem}_{args.tag}.json")
    depths = tuple(int(x) for x in args.depths.split(","))

    if args.export:
        from pathlib import Path

        from vgc.wp import offload
        from vgc.wp.models import in_battle_version

        rows, counts = collect(reg, in_battle_version(reg.id, args.sheets), args.sheets, args.endgame, depths, extra,
                               args.bulk)
        # Shallow first, so a remote run that hits its deadline has finished what the deeper
        # search needs least and the scoring needs most.
        positions = [j["position"] for d in depths for r in rows for j in r["jobs"][d]]
        print(json.dumps({"games": counts["games"], **offload.export(positions, Path(args.export))}))
        return
    if args.score_only:
        blob = json.loads(out_path.read_text())
        rows, counts = blob["rows"], blob["counts"]
    else:
        from vgc.wp.models import in_battle_version

        version = in_battle_version(reg.id, args.sheets)
        rows, counts = collect(reg, version, args.sheets, args.endgame, depths, extra, args.bulk)
        counts["bulk"] = args.bulk
        print(f"{counts['games']} games to solve", counts, flush=True)
        solve(rows, args.workers, args.cap, depths)
        counts["version"], counts["depths"], counts["search"] = version, list(depths), extra
        counts["crashed_positions"] = sum(len(r.get("errors", [])) for r in rows)
    raw = rows
    if args.temper:
        rows = [r | {"engine": round(doubles.temper(r["engine"]), 4)} if r.get("engine") is not None else r for r in rows]
    result = {"all": score(rows),
              "settled": score([r for r in rows if r.get("leaf_mass") is not None and r["leaf_mass"] <= SETTLED]),
              "unsettled": score([r for r in rows if r.get("leaf_mass") is not None and r["leaf_mass"] > SETTLED]),
              "played_out": score([r for r in rows if r["ended_by"] == "normal"]),
              "forfeit": score([r for r in rows if r["ended_by"] != "normal"])}
    if args.endgame == "doubles":
        for kind in ("2v2", "2v1|1v2", "2v1", "1v2"):
            result[kind] = score([r for r in rows if r.get("kind") in kind.split("|")])
    if args.sheets == "closed":
        result["covered"] = score([r for r in rows if r.get("unsolved", 0) <= COVERED])
        result["partly_covered"] = score([r for r in rows if r.get("unsolved", 0) > COVERED])
    rows = raw                      # saved as solved; a tempered score is written beside them
    if args.temper:
        result["tempered"] = list(doubles.TEMPER)
        out_path = out_path.with_name(f"{out_path.stem}_tempered.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"counts": counts, "result": result, "cap": args.cap,
                               "rows": [{k: v for k, v in r.items() if k != "jobs"} |
                                        {"positions": {str(d): [{"class": j["class"], "weight": round(j["weight"], 4),
                                                                 "value": (j.get("result") or {}).get("value")}
                                                                for j in r["jobs"][d]] for d in depths if j_ok(r, d)}}
                                        if "jobs" in r else r for r in rows]}, indent=1) + "\n")
    print(json.dumps({"counts": counts, "result": result}, indent=1))


if __name__ == "__main__":
    main()
