"""The leaf above two a side: the race with the back, fitted and scored on human games (PLAN-policy,
stage 3).

Each open-sheet human game at the first turn of each state kind with more than two Pokémon left on
a side (as `policy_bar.py`), read from the stands:
  - both sides' backs guessed from bring rates (`vgc.policy.view.backs`), never from what the
    replay shows later; the move orders on the field from `doubles.speed_classes`;
  - the heaviest `--positions` (backs × move order) raced by the solver at depth 0
    (`race_bench`, raw), and averaged by weight.

Then, as `race_calibration.py` did for two or fewer a side, maps from the raw race to P(p1 wins)
are fitted on training-split games and scored on held-out ones, against the served model and the
floor of `policy_bar.py` (HP share, count and stages), by state kind, cluster bootstrap by group:

  bench_calibrated   sigmoid(a · logit(race) + d)
  bench_blend        sigmoid(a · logit(race) + b · logit(HP share) + c · count lead + e · stages + d)

    .venv/bin/python scripts/analysis/bench_race.py              # collect and fit, ~15 min on 8 workers
    .venv/bin/python scripts/analysis/bench_race.py --fit-only   # refit the saved rows
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import subprocess
from collections import defaultdict
from multiprocessing import Pool
from typing import Any

import numpy as np

from vgc import paths

OUT = paths.DATA / "analysis" / "reg_mc" / "bench_race.json"
ROWS = paths.DATA / "analysis" / "reg_mc" / "bench_race_rows.jsonl"
BAR_ROWS = paths.DATA / "analysis" / "reg_mc" / "policy_bar_rows.jsonl"
EPS = 1 / 128
LL_EPS = 1e-3
SEARCH = {"depth": 0, "race": True, "race_doubles": True, "race_bench": True, "fast_race": True, "fast_dice": True}
_W: dict[str, Any] = {}


def logit(p: float) -> float:
    q = min(1 - EPS, max(EPS, p))
    return math.log(q / (1 - q))


def _init() -> None:
    from vgc.data.splits import load_rules
    from vgc.regulation import load_regulation

    _W["reg"] = load_regulation("reg_mc")
    _W["rules"] = load_rules(_W["reg"])
    _W["solver"] = subprocess.Popen(["node", str(paths.SIDECAR / "showdown" / "endgame-solver.js"), str(paths.SHOWDOWN),
                                     "--serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)


def _solve(pos: dict[str, Any]) -> dict[str, Any] | None:
    p = _W["solver"]
    p.stdin.write(json.dumps({"id": 0, "position": pos}) + "\n")
    p.stdin.flush()
    return json.loads(p.stdout.readline()).get("result")


def spectator_positions(reg, view, search: dict[str, Any], top: int) -> tuple[list[dict[str, Any]], str | None]:
    """The heaviest `top` positions of a game watched from the stands, both backs guessed."""
    from vgc.policy import view as V
    from vgc.regulation import to_id
    from vgc.wp import doubles, endgame, solver

    state = view.o
    for sid in ("p1", "p2"):
        if not state.sides[sid].sheet:
            return [], "a closed sheet"
        on = doubles.actives(state, sid)
        if len(on) < 2 and reg.bring - sum(m.state == "fainted" for m in state.sides[sid].mons) > len(on):
            return [], "a slot waiting for a replacement"
        for m in on:
            if m.volatiles:
                return [], "a volatile"
        for m in V._left(state, sid):
            if m.status and m.status not in endgame.STATUSES:
                return [], "sleep or bad poison"
    on = {sid: doubles.actives(state, sid) for sid in ("p1", "p2")}
    seen_back = {sid: [m for m in state.sides[sid].mons if m.state == "bench"] for sid in ("p1", "p2")}
    seen_mons = {sid: on[sid] + seen_back[sid] for sid in ("p1", "p2")}
    seen_sets = {sid: [V._sheet_set(reg, m) | {"mega": m.forme if m.mega else None} for m in seen_mons[sid]]
                 for sid in ("p1", "p2")}
    if any(endgame.UNSOLVABLE_MOVES & {to_id(x) for x in s.get("moves") or []} for ss in seen_sets.values() for s in ss):
        return [], "Revival Blessing"
    f0 = doubles.facts(reg, view, seen_sets, mons=seen_mons)
    orders, _, _, _ = doubles.speed_classes(reg, view, seen_sets, f0, top=3, ordered={s: len(on[s]) for s in ("p1", "p2")})
    if not orders:
        return [], "no Speed order"
    jobs = []
    backs = {sid: V.backs(reg, state, sid)[:3] for sid in ("p1", "p2")}
    for (b1, w1), (b2, w2) in itertools.product(backs["p1"], backs["p2"]):
        back = {"p1": list(b1), "p2": list(b2)}
        extra = {sid: [] for sid in ("p1", "p2")}
        for sid in ("p1", "p2"):
            for m in back[sid]:
                s = V._sheet_set(reg, m) | {"mega": None}
                s["sp"] = solver.spread(reg, s, V._commonest_speed(reg, s))
                extra[sid].append(s)
        mons = {sid: seen_mons[sid] + back[sid] for sid in ("p1", "p2")}
        sets_ = {sid: seen_sets[sid] + extra[sid] for sid in ("p1", "p2")}
        f = doubles.facts(reg, view, sets_, mons=mons)
        f["bench"] = {sid: len(mons[sid]) - len(on[sid]) for sid in ("p1", "p2")}
        f["megaUsed"] = {sid: True for sid in ("p1", "p2") if any(m.mega for m in state.sides[sid].mons)}
        for o in orders:
            sides = {sid: [{**s, "sp": sp} for s, sp in zip(sets_[sid], o["sp"][sid])] + sets_[sid][len(o["sp"][sid]):]
                     for sid in ("p1", "p2")}
            jobs.append({"weight": w1 * w2 * o["weight"], "position": V.compose(reg, sides, f, search)})
    jobs.sort(key=lambda j: -j["weight"])
    return jobs[:top], None


def _game(task: tuple[dict[str, Any], int]) -> dict[str, Any] | None:
    from vgc.data.snapshots import human_snapshots, replay_group
    from vgc.policy import view as V
    from vgc.web.endgames import is_bot

    replay, top = task
    reg = _W["reg"]
    group = replay_group(replay)
    split = _W["rules"].split_of("human", replay["id"], [], group)
    if split not in ("train", "heldout_human") or any(is_bot(p) for p in replay.get("players") or []):
        return None
    recs = [r for r in human_snapshots(replay, reg) if r["obs"]["perspective"] == "spectator" and not r["meta"]["approx"]]
    if not recs or not recs[0]["meta"]["ots"] or recs[0]["label"]["winner"] not in ("p1", "p2"):
        return None
    view = V.PlayerView(reg, "spectator", None)
    seen_kinds: set[str] = set()
    states, dropped = [], defaultdict(int)
    for line in replay["log"].split("\n"):
        view.feed([line])
        if not line.startswith("|turn|"):
            continue
        st = view.o
        left = {sid: reg.bring - sum(m.state == "fainted" for m in st.sides[sid].mons) for sid in ("p1", "p2")}
        a, b = left["p1"], left["p2"]
        kind = f"{max(a, b)}v{min(a, b)}"
        if max(a, b) <= 2 or min(a, b) == 0 or kind in seen_kinds:
            continue
        seen_kinds.add(kind)
        jobs, why = spectator_positions(reg, view, SEARCH, top)
        if why:
            dropped[why] += 1
            continue
        vals = [(j["weight"], (_solve(j["position"]) or {}).get("value")) for j in jobs]
        vals = [(w, v) for w, v in vals if v is not None]
        if not vals:
            dropped["no answer"] += 1
            continue
        tw = sum(w for w, _ in vals)
        states.append({"kind": kind, "turn": st.turn, "race": sum(w * v for w, v in vals) / tw, "positions": len(vals)})
    return {"id": replay["id"], "group": group, "split": split, "winner": recs[0]["label"]["winner"],
            "states": states, "dropped": dict(dropped)}


def collect(workers: int, top: int) -> list[dict[str, Any]]:
    from vgc.meta import pool, replays
    from vgc.regulation import load_regulation

    reg = load_regulation("reg_mc")
    reps = [rp for fmt in pool.formats_for(reg) for rp in replays.cached(fmt)]
    with Pool(workers, initializer=_init) as p:
        return [g for g in p.imap_unordered(_game, [(r, top) for r in reps], chunksize=4) if g]


def report(games: list[dict[str, Any]], boots: int = 4000, seed: int = 11) -> dict[str, Any]:
    from sklearn.linear_model import LogisticRegression

    # The floor's features and the model's number, from stage 0's rows of the same states.
    bar = {}
    for line in BAR_ROWS.read_text().splitlines():
        g = json.loads(line)
        if g["regime"] == "open":
            for s in g["states"]:
                bar[(g["id"], s["kind"])] = s
    rows, other_turn = [], 0
    for g in games:
        for s in g["states"]:
            b = bar.get((g["id"], s["kind"]))
            if b is None:
                continue
            other_turn += b["turn"] != s["turn"]
            rows.append({"split": g["split"], "group": g["group"], "kind": s["kind"], "y": g["winner"] == "p1",
                         "race": s["race"], "share": b["share"], "lead": b["lead"], "boosts": b["boosts"],
                         "model": b.get("model")})
    feats = {"bench_calibrated": lambda r: [logit(r["race"])],
             "bench_blend": lambda r: [logit(r["race"]), logit(r["share"]), r["lead"], r["boosts"]],
             "floor": lambda r: [logit(r["share"]), r["lead"], r["boosts"]]}
    train = [r for r in rows if r["split"] == "train"]
    held = [r for r in rows if r["split"] == "heldout_human" and r["model"] is not None]
    fits = {}
    for name, fx in feats.items():
        lr = LogisticRegression(C=1e6, max_iter=2000).fit(np.array([fx(r) for r in train]), np.array([r["y"] for r in train]))
        fits[name] = lr
        for r in held:
            r[name] = float(lr.predict_proba(np.array([fx(r)]))[0, 1])
    for r in held:
        r["raw"] = r["race"]
    rng = np.random.default_rng(seed)

    def score(rs: list[dict[str, Any]]) -> dict[str, Any]:
        y = np.array([r["y"] for r in rs], float)
        groups = sorted({r["group"] for r in rs})
        gi = np.array([groups.index(r["group"]) for r in rs])
        n = np.bincount(gi, minlength=len(groups)).astype(float)
        idx = rng.integers(0, len(groups), (boots, len(groups)))
        out: dict[str, Any] = {"states": len(rs), "groups": len(groups)}
        ll = {}
        for k in ("model", "floor", "raw", "bench_calibrated", "bench_blend"):
            p = np.clip(np.array([r[k] for r in rs]), LL_EPS, 1 - LL_EPS)
            ll[k] = -(y * np.log(p) + (1 - y) * np.log(1 - p))
            out[f"ll_{k}"] = round(float(ll[k].mean()), 4)
        for a, b in (("bench_blend", "model"), ("bench_blend", "floor")):
            d = np.bincount(gi, ll[a] - ll[b], minlength=len(groups))
            boot = d[idx].sum(1) / n[idx].sum(1)
            lo, hi = np.percentile(boot, [2.5, 97.5])
            out[f"{a}_minus_{b}"] = {"diff": round(float((ll[a] - ll[b]).mean()), 4),
                                     "ci95": [round(float(lo), 4), round(float(hi), 4)],
                                     "verdict": f"{a} better" if hi < 0 else f"{b} better" if lo > 0 else "not distinguishable"}
        return out

    by_kind = defaultdict(list)
    for r in held:
        by_kind[r["kind"]].append(r)
    dropped: dict[str, int] = defaultdict(int)
    for g in games:
        for k, v in g["dropped"].items():
            dropped[k] += v
    coef = {name: {"coef": [round(float(c), 4) for c in lr.coef_[0]], "intercept": round(float(lr.intercept_[0]), 4)}
            for name, lr in fits.items()}
    return {"train_states": len(train), "joined_at_another_turn": other_turn, "dropped": dict(dropped), "fits": coef, "all": score(held),
            "by_kind": {k: score(v) for k, v in sorted(by_kind.items(), reverse=True) if len(v) >= 30}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--positions", type=int, default=4)
    ap.add_argument("--fit-only", action="store_true")
    a = ap.parse_args()
    if a.fit_only:
        games = [json.loads(x) for x in ROWS.read_text().splitlines()]
    else:
        games = collect(a.workers, a.positions)
        ROWS.write_text("".join(json.dumps(g) + "\n" for g in games))
    res = report(games)
    OUT.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
