"""The bar a leaf must clear above two a side, and what a battle costs to play (PLAN-policy, stage 0).

**The bar.** Each human game at the first turn of each state kind with more than two Pokémon left
on a side (4v4, 4v3, 3v3, 4v2, 3v2, 4v1, 3v1; a 4v3 and a 3v4 are one kind, practice 9), from
the stands:

  model   the served in-battle model for the game's regime, calibrated, as the page shows it
  floor   sigmoid(a · logit(HP share) + c · count lead + e · net stat stages + d), fitted per
          regime on training-split games at the same turns. HP share is over every Pokémon left,
          the ones not yet seen counted at full HP (they have not been out), so nothing comes
          from later in the replay (principle 7)

scored on held-out games by log loss and Brier, floor minus model with a cluster bootstrap by
group, per regime and state kind. The leaf the policy searches with (the race with reinforcements,
stage 3) has to beat the model, and the floor says how much of the model's number is the count.

**The budget.** Per regime, over held-out games that were played out: turns a game, decisions a
game (turns plus forced replacements, `switch` records), and how a game's turns fall between the
state kinds, so the cost of a battle can be weighted by where its decisions are.

    .venv/bin/python scripts/analysis/policy_bar.py              # about 10 minutes on 8 workers
    .venv/bin/python scripts/analysis/policy_bar.py --fit-only   # rescore the saved rows
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from multiprocessing import Pool
from typing import Any

import numpy as np

from vgc import paths

OUT = paths.DATA / "analysis" / "reg_mc" / "policy_bar.json"
ROWS = paths.DATA / "analysis" / "reg_mc" / "policy_bar_rows.jsonl"
EPS = 1 / 128           # as the solver's blends clip before a logit
LL_EPS = 1e-3           # as solver_vs_humans clips log loss
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


def _side(obs_side: dict[str, Any], bring: int) -> dict[str, float]:
    mons = obs_side["mons"]
    fainted = sum(m["state"] == "fainted" for m in mons)
    seen = [m for m in mons if m["state"] in ("active", "bench", "fainted")]
    # Brought but not yet seen: never out, so at full HP.
    unseen = max(0, bring - len(seen))
    hp = sum(m["hp"] for m in seen if m["state"] != "fainted") + unseen
    boosts = sum(sum(v for v in (m.get("boosts") or {}).values()) for m in mons if m["state"] == "active")
    return {"left": bring - fainted, "hp": hp, "boosts": boosts}


def _game(replay: dict[str, Any]) -> dict[str, Any] | None:
    from vgc.data.snapshots import human_snapshots, replay_group
    from vgc.web.endgames import is_bot

    reg, rules = _W["reg"], _W["rules"]
    split = rules.split_of("human", replay["id"], [], replay_group(replay))
    if split not in ("train", "heldout_human"):
        return None
    if any(is_bot(p) for p in replay.get("players") or []):
        return None
    recs = [r for r in human_snapshots(replay, reg)
            if r["obs"]["perspective"] == "spectator" and not r["meta"]["approx"]]
    if not recs or recs[0]["label"]["winner"] not in ("p1", "p2"):
        return None
    label = recs[0]["label"]
    regime = "open" if recs[0]["meta"]["ots"] else "closed"
    first: dict[str, int] = {}
    kinds: Counter = Counter()
    states = []
    for i, r in enumerate(recs):
        if r["kind"] not in ("turn", "switch"):
            continue
        s = {sid: _side(r["obs"]["sides"][sid], reg.bring) for sid in ("p1", "p2")}
        a, b = s["p1"]["left"], s["p2"]["left"]
        kind = f"{max(a, b)}v{min(a, b)}"
        if r["kind"] == "turn":
            kinds[kind] += 1
        if r["kind"] == "turn" and max(a, b) > 2 and min(a, b) > 0 and kind not in first:
            first[kind] = i
            tot = s["p1"]["hp"] + s["p2"]["hp"]
            states.append({"kind": kind, "turn": r["obs"]["turn"],
                           "share": s["p1"]["hp"] / tot if tot else 0.5,
                           "lead": a - b, "boosts": s["p1"]["boosts"] - s["p2"]["boosts"], "rec": i})
    out = {"id": replay["id"], "group": replay_group(replay), "split": split, "regime": regime,
           "winner": label["winner"], "ended_by": label.get("ended_by"), "turns": label.get("turns"),
           "decisions": sum(r["kind"] in ("turn", "switch") for r in recs), "kinds": dict(kinds),
           "states": states}
    if split == "heldout_human" and states:
        out["_recs"] = [recs[st["rec"]] for st in states]
    return out


def collect(workers: int) -> list[dict[str, Any]]:
    from vgc.meta import pool, replays
    from vgc.regulation import load_regulation
    from vgc.wp.models import in_battle_version, predict_records
    from vgc.wp.tools import _load

    reg = load_regulation("reg_mc")
    reps = [rp for fmt in pool.formats_for(reg) for rp in replays.cached(fmt)]
    with Pool(workers, initializer=_init) as p:
        games = [g for g in p.imap_unordered(_game, reps, chunksize=16) if g]
    # The model, per regime, on the held-out states only, in one batch each.
    for regime in REGIMES:
        version = in_battle_version(reg.id, regime)
        model, fz = _load(reg, version)
        sel = [g for g in games if g["regime"] == regime and "_recs" in g]
        recs = [r for g in sel for r in g["_recs"]]
        preds = iter(predict_records(model, recs, fz)) if recs else iter(())
        for g in sel:
            for st in g["states"]:
                st["model"] = round(float(next(preds)), 4)
            g["model_version"] = version
    for g in games:
        g.pop("_recs", None)
        for st in g["states"]:
            st.pop("rec", None)
    return games


def features(st: dict[str, Any]) -> list[float]:
    return [logit(st["share"]), st["lead"], st["boosts"]]


def fit_floor(games: list[dict[str, Any]], regime: str) -> dict[str, float]:
    from sklearn.linear_model import LogisticRegression

    rows = [(features(st), g["winner"] == "p1") for g in games
            if g["split"] == "train" and g["regime"] == regime for st in g["states"]]
    x, y = np.array([r[0] for r in rows]), np.array([r[1] for r in rows])
    lr = LogisticRegression(C=1e6, max_iter=1000).fit(x, y)
    a, c, e = lr.coef_[0]
    return {"a": round(float(a), 4), "c": round(float(c), 4), "e": round(float(e), 4),
            "d": round(float(lr.intercept_[0]), 4), "rows": len(rows),
            "games": len({g["id"] for g in games if g["split"] == "train" and g["regime"] == regime})}


def floor_p(f: dict[str, float], st: dict[str, Any]) -> float:
    z = f["a"] * logit(st["share"]) + f["c"] * st["lead"] + f["e"] * st["boosts"] + f["d"]
    return 1 / (1 + math.exp(-z))


def score(rows: list[dict[str, Any]], boots: int = 4000, seed: int = 11) -> dict[str, Any]:
    """Each predictor's log loss and Brier, and floor minus model with a cluster bootstrap by group."""
    rng = np.random.default_rng(seed)
    groups = sorted({r["group"] for r in rows})
    gi = np.array([groups.index(r["group"]) for r in rows])
    y = np.array([r["y"] for r in rows], float)

    def losses(p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        pc = np.clip(p, LL_EPS, 1 - LL_EPS)
        return (p - y) ** 2, -(y * np.log(pc) + (1 - y) * np.log(1 - pc))

    preds = {k: np.array([r[k] for r in rows], float) for k in ("model", "floor")}
    preds["constant"] = np.full(len(rows), 0.5)
    out: dict[str, Any] = {"states": len(rows), "groups": len(groups)}
    idx = rng.integers(0, len(groups), (boots, len(groups)))
    n = np.bincount(gi, minlength=len(groups)).astype(float)
    for metric, k in (("brier", 0), ("logloss", 1)):
        vals = {name: losses(p)[k] for name, p in preds.items()}
        out[metric] = {name: round(float(v.mean()), 4) for name, v in vals.items()}
        d = np.bincount(gi, vals["floor"] - vals["model"], minlength=len(groups))
        diff = d[idx].sum(1) / n[idx].sum(1)
        lo, hi = np.percentile(diff, [2.5, 97.5])
        out[metric]["floor_minus_model"] = round(float(vals["floor"].mean() - vals["model"].mean()), 4)
        out[metric]["ci95"] = [round(float(lo), 4), round(float(hi), 4)]
        out[metric]["verdict"] = "floor better" if hi < 0 else "model better" if lo > 0 else "not distinguishable"
    return out


def budget(games: list[dict[str, Any]], regime: str) -> dict[str, Any]:
    played = [g for g in games if g["split"] == "heldout_human" and g["regime"] == regime and g["ended_by"] == "normal"]
    kinds: Counter = Counter()
    for g in played:
        kinds.update(g["kinds"])
    total = sum(kinds.values())
    return {"games": len(played),
            "turns_mean": round(float(np.mean([g["turns"] for g in played])), 2),
            "decisions_mean": round(float(np.mean([g["decisions"] for g in played])), 2),
            "turn_share_by_kind": {k: round(v / total, 3) for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])}}


def report(games: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for regime in REGIMES:
        f = fit_floor(games, regime)
        held = [g for g in games if g["split"] == "heldout_human" and g["regime"] == regime]
        rows = [{"group": g["group"], "y": g["winner"] == "p1", "kind": st["kind"], "model": st["model"],
                 "floor": floor_p(f, st)} for g in held for st in g["states"]]
        by_kind = defaultdict(list)
        for r in rows:
            by_kind[r["kind"]].append(r)
        out[regime] = {
            "model": next((g["model_version"] for g in held if "model_version" in g), None),
            "floor": f, "heldout_games": len(held),
            "all": score(rows),
            "by_kind": {k: score(v) for k, v in sorted(by_kind.items(), reverse=True) if len(v) >= 30},
            "budget": budget(games, regime),
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--fit-only", action="store_true")
    a = ap.parse_args()
    if a.fit_only:
        games = [json.loads(line) for line in ROWS.read_text().splitlines()]
    else:
        games = collect(a.workers)
        ROWS.parent.mkdir(parents=True, exist_ok=True)
        ROWS.write_text("".join(json.dumps(g) + "\n" for g in games))
    res = report(games)
    OUT.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
