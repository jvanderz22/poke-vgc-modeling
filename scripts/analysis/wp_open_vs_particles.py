"""Which number should a closed-sheet position lead with: the particle average or the unknowns left
unknown? (PLAN-v3 step 6, item 1.)

The Battle page leads with `wp`, the served model averaged over `k` opponents drawn from the set
belief, and shows `wp_open` beside it: the same position with what has not been revealed left
unrevealed. `wp_closed_sheet.py` compared the two on open-sheet replays masked to look closed,
where `open` won by 0.011. That mask holds the turn-0 information fixed for the whole game, and a
real Team Preview Only game reveals items and abilities as it goes. So this asks the genuine
closed-sheet games instead. They carry no truth to compare a draw against, but they carry who won,
and that is the only thing either number claims.

  wp       the served closed-sheet model over `k` draws, each filling only what the game has not
           revealed, conditioned on what it has (`vgc.belief.sets.given`), as `live._particle` does
  wp_open  the same model on the position as observed

From the stands, with both sides filled, and scored on every held-out, human, closed-sheet
spectator snapshot. The unit is the replay's group, and intervals are a paired cluster bootstrap.
The set prior is the app's own, counted over every sheet in the pool. This measures what the app
would show, and is not a clean held-out test of the prior.

    .venv/bin/python scripts/analysis/wp_open_vs_particles.py -k 8
"""

from __future__ import annotations

import argparse
import json
import random
from types import SimpleNamespace
from typing import Any

import numpy as np

from vgc import paths
from vgc.regulation import load_regulation

OUT = paths.DATA / "analysis" / "reg_mc" / "wp_open_vs_particles.json"
BUCKETS = (("preview", None, None), ("t1-2", 1, 2), ("t3-4", 3, 4), ("t5-6", 5, 6), ("t7+", 7, 999))


def fill(obs: dict[str, Any], rng: random.Random, beliefs: dict) -> dict[str, Any]:
    """One complete position: what was not revealed, drawn from the set belief given what was."""
    from vgc.belief import sets as set_belief

    out = json.loads(json.dumps(obs))
    for sid in ("p1", "p2"):
        for m in out["sides"][sid]["mons"]:
            key = (sid, m["species"])
            if key not in beliefs:
                beliefs[key] = set_belief.given(reg_, SimpleNamespace(**{"ability_ruled_out": (), **m}))
            drawn = beliefs[key].particles(rng, 1)
            if not drawn:
                continue
            pick = drawn[0]
            if m["item"] is None:
                m["item"], m["item_source"] = pick.item, "belief"
            if m["ability"] is None:
                m["ability"], m["ability_source"] = pick.ability, "belief"
            if not m["moves"]:
                m["moves"] = list(pick.moves)
            if not m["nature"]:
                m["nature"] = pick.nature or None
        out["sides"][sid]["sheet"] = True
    return out


def bucket(rec: dict[str, Any]) -> str:
    if rec["kind"] != "turn":
        return "preview"
    t = rec["obs"]["turn"]
    return next(name for name, lo, hi in BUCKETS[1:] if lo <= t <= hi)


def score(rows: list[dict[str, Any]], boots: int, seed: int) -> dict[str, Any]:
    if not rows:
        return {"positions": 0}
    rng = np.random.default_rng(seed)
    y = np.array([r["y"] for r in rows])
    out: dict[str, Any] = {"positions": len(rows), "groups": len({r["group"] for r in rows})}
    groups = sorted({r["group"] for r in rows})
    gi = np.array([groups.index(r["group"]) for r in rows])
    n = np.bincount(gi, minlength=len(groups)).astype(float)
    idx = rng.integers(0, len(groups), (boots, len(groups)))
    ll, br = {}, {}
    for arm in ("wp", "wp_open"):
        p = np.clip(np.array([r[arm] for r in rows]), 1e-6, 1 - 1e-6)
        ll[arm] = -(y * np.log(p) + (1 - y) * np.log(1 - p))
        br[arm] = (p - y) ** 2
        out[arm] = {"logloss": round(float(ll[arm].mean()), 4), "brier": round(float(br[arm].mean()), 4),
                    "confidence": round(float(np.abs(p - 0.5).mean()), 4)}
    for name, m in (("logloss", ll), ("brier", br)):
        s = np.bincount(gi, weights=m["wp_open"] - m["wp"], minlength=len(groups))
        diff = s[idx].sum(1) / n[idx].sum(1)
        lo, hi = np.percentile(diff, [2.5, 97.5])
        out[f"open_minus_wp_{name}"] = {
            "mean": round(float((m["wp_open"] - m["wp"]).mean()), 5), "ci95": [round(float(lo), 5), round(float(hi), 5)],
            "verdict": "open better" if hi < 0 else "wp better" if lo > 0 else "not distinguishable"}
    out["mean_abs_gap"] = round(float(np.mean([abs(r["wp"] - r["wp_open"]) for r in rows])), 4)
    return out


reg_ = None


def main() -> None:
    global reg_
    from vgc.data.snapshots import human_snapshots, replay_group
    from vgc.data.splits import load_rules
    from vgc.meta import pool, replays
    from vgc.web.endgames import is_bot
    from vgc.web.live import _per_record
    from vgc.wp.features import featurize
    from vgc.wp.models import CLOSED, in_battle_version
    from vgc.wp.tools import _load

    ap = argparse.ArgumentParser()
    ap.add_argument("-k", type=int, default=8, help="draws per position")
    ap.add_argument("--replays", type=int, default=0, help="cap on held-out games (0: all)")
    ap.add_argument("--boots", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=5)
    args = ap.parse_args()

    reg = reg_ = load_regulation("reg_mc")
    rules = load_rules(reg)
    version = in_battle_version(reg.id, CLOSED)
    model, fz = _load(reg, version)
    rows: list[dict[str, Any]] = []
    games = 0
    for fmt in pool.formats_for(reg):
        for rep in replays.cached(fmt):
            if args.replays and games >= args.replays:
                break
            group = replay_group(rep)
            if rules.split_of("human", rep["id"], [], group) != "heldout_human":
                continue
            if any(is_bot(p) for p in rep.get("players") or []):
                continue
            recs = [r for r in human_snapshots(rep, reg)
                    if r["obs"]["perspective"] == "spectator" and not r["meta"].get("approx")
                    and not r["meta"].get("ots") and r["label"]["winner"] in ("p1", "p2")]
            if not recs:
                continue
            games += 1
            rng = random.Random(games)
            batch = []
            for rec in recs:
                beliefs: dict = {}
                batch += [dict(rec, obs=fill(rec["obs"], rng, beliefs)) for _ in range(args.k)] + [rec]
            d = featurize(batch, fz)
            p, _ = model.predict(d)
            per = _per_record(batch, fz, p)
            for i, rec in enumerate(recs):
                chunk = per[i * (args.k + 1):(i + 1) * (args.k + 1)]
                rows.append({"group": group, "bucket": bucket(rec), "y": float(rec["label"]["winner"] == "p1"),
                             "forfeit": rec["label"]["ended_by"] == "forfeit",
                             "wp": sum(chunk[:-1]) / args.k, "wp_open": chunk[-1]})
            if games % 100 == 0:
                print(f"  {games} games, {len(rows)} positions", flush=True)
        if args.replays and games >= args.replays:
            break

    result = {"version": version, "k": args.k, "games": games,
              "all": score(rows, args.boots, args.seed),
              "played_out": score([r for r in rows if not r["forfeit"]], args.boots, args.seed),
              **{name: score([r for r in rows if r["bucket"] == name], args.boots, args.seed) for name, _, _ in BUCKETS}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
