"""Does the policy choose what people chose? (PLAN-policy stage 5: reported, not gated.)

Held-out human games, open sheets, at every turn with more than two a side, from the stands as
`prune_vs_people.py` builds them: the player's own back as the replay says it brought, the
opponent's as the heaviest guess, both sides' spreads at their commonest Speed. Each position is
solved once with the policy's search, and the human's joint choice is looked for:

  kept       among the policy's K rows at all
  people     the row the policy plays under the people reading (the argmax of EWP)
  nash       the probability the Nash reading puts on it
  model      the people model's own first pair (the first row: the kept pairs are ranked by it)
  uniform    one over the number of legal joint choices

A choice the log does not show whole (a flinch, a full paralysis) is left out.

    .venv/bin/python scripts/analysis/policy_vs_people.py --games 400     # minutes on 8 workers
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
from multiprocessing import Pool
from typing import Any

import numpy as np

from vgc import paths

OUT = paths.DATA / "analysis" / "reg_mc" / "policy_vs_people.json"
_W: dict[str, Any] = {}


def _init() -> None:
    import prune_vs_people as P
    from vgc.data.splits import load_rules
    from vgc.regulation import load_regulation

    _W["reg"] = load_regulation("reg_mc")
    _W["rules"] = load_rules(_W["reg"])
    _W["solver"] = subprocess.Popen(["node", str(paths.SIDECAR / "showdown" / "endgame-solver.js"), str(paths.SHOWDOWN),
                                     "--serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    P._W.update(_W)


def _solve(pos: dict[str, Any]) -> dict[str, Any] | None:
    p = _W["solver"]
    p.stdin.write(json.dumps({"id": 0, "position": pos}) + "\n")
    p.stdin.flush()
    out = json.loads(p.stdout.readline())
    return out.get("result")


def positions(replay: dict[str, Any], split: str = "heldout_human"):
    """Each position this script asks about: a held-out open-sheet game's turn with more than two a
    side, from one side's view, with the choice the human made there shown whole. Yields the solver
    input's parts (`sets_`, `f`), the human's joint choice as the solver writes it, and where it was.
    `split="validation"` walks the WP model's validation games instead (training games in its 20% of
    groups, `vgc.wp.dataset`), for anything fitted beside the model."""
    import prune_vs_people as P
    from vgc.data.snapshots import human_snapshots, replay_group
    from vgc.policy import view as V
    from vgc.regulation import to_id
    from vgc.web.endgames import is_bot
    from vgc.wp import doubles, endgame, solver

    reg = _W["reg"]
    group = replay_group(replay)
    shard = _W["rules"].split_of("human", replay["id"], [], group)
    if split == "validation":
        from vgc.wp.dataset import VAL_RATE, _is_val
        ok = shard == "train" and _is_val(group, VAL_RATE["human"])
    else:
        ok = shard == split
    if not ok or any(is_bot(p) for p in replay.get("players") or []):
        return
    recs = [r for r in human_snapshots(replay, reg) if r["obs"]["perspective"] == "spectator" and not r["meta"]["approx"]]
    if not recs or not recs[0]["meta"]["ots"]:
        return
    label = recs[0]["label"]
    lines = replay["log"].split("\n")
    turn_at = [i for i, x in enumerate(lines) if x.startswith("|turn|")]
    view = V.PlayerView(reg, "spectator", None)
    fed = 0
    for n, at in enumerate(turn_at):
        view.feed(lines[fed: at + 1])
        fed = at + 1
        st = view.o
        left = {sid: reg.bring - sum(m.state == "fainted" for m in st.sides[sid].mons) for sid in ("p1", "p2")}
        if max(left.values()) <= 2 or min(left.values()) == 0:
            continue
        end = turn_at[n + 1] if n + 1 < len(turn_at) else len(lines)
        made = P.choices_in(lines[at + 1: end])
        bad = any(m.volatiles for sid in ("p1", "p2") for m in doubles.actives(st, sid)) or any(
            m.status and m.status not in endgame.STATUSES for sid in ("p1", "p2") for m in V._left(st, sid))
        if bad or any(len(doubles.actives(st, sid)) < min(2, left[sid]) for sid in ("p1", "p2")):
            continue
        for sid in ("p1", "p2"):
            them = "p2" if sid == "p1" else "p1"
            unseen, n_back = V._unseen(reg, st, sid)
            if n_back and not (label.get("brought_complete") or {}).get(sid):
                continue
            mine_back = [m for m in unseen if m.species in (label.get("brought") or {}).get(sid, [])][:n_back]
            backs = V.backs(reg, st, them)
            their_back = list(backs[0][0]) if backs else []
            mons, sets_ = {}, {}
            for s2, extra in ((sid, mine_back), (them, their_back)):
                on = doubles.actives(st, s2)
                seen_back = [m for m in st.sides[s2].mons if m.state == "bench"]
                mons[s2] = on + seen_back + list(extra)
                ss = []
                for m in mons[s2]:
                    x = V._sheet_set(reg, m) | {"mega": m.forme if m.mega else None}
                    x["sp"] = solver.spread(reg, x, V._commonest_speed(reg, x))
                    ss.append(x)
                sets_[s2] = ss
            if any(endgame.UNSOLVABLE_MOVES & {to_id(x) for x in s.get("moves") or []} for ss in sets_.values() for s in ss):
                continue
            f = doubles.facts(reg, view, sets_, mons=mons)
            on_n = {s2: len(doubles.actives(st, s2)) for s2 in ("p1", "p2")}
            f["bench"] = {s2: len(mons[s2]) - on_n[s2] for s2 in ("p1", "p2")}
            f["megaUsed"] = {s2: True for s2 in ("p1", "p2") if any(m.mega for m in st.sides[s2].mons)}
            parts = []
            for slot in range(2):
                parts.append("pass" if slot >= on_n[sid] else
                             P._part(reg, slot, sid, made.get(f"{sid}{'ab'[slot]}", {}), sets_[sid]))
            if any(p is None for p in parts):
                continue                                  # not shown whole
            yield {"id": replay["id"], "group": group, "kind": f"{left[sid]}v{left[them]}", "sid": sid, "them": them,
                   "turn": int(lines[at].split("|")[2]), "winner": label.get("winner"),
                   "sets": sets_, "f": f, "human": ", ".join(parts)}


def _game(replay: dict[str, Any]) -> list[dict[str, Any]]:
    from vgc.policy import ewp as E
    from vgc.policy import view as V

    reg = _W["reg"]
    search = {**V.SEARCH, "side_k": E.SIDE_K, "root_detail": True}
    rows = []
    for x in positions(replay):
        sid, them, sets_, f, human = x["sid"], x["them"], x["sets"], x["f"], x["human"]
        r = _solve(V.compose(reg, sets_, f, search))
        if r is None or not r.get("matrix"):
            continue
        M = np.array(r["matrix"], float)
        people = (r.get("people") or {}).get(them)
        if sid == "p2":
            M = 1 - M.T
        scores = None if people is None or any(x is None for x in people) else people
        rows_ = r["moves"][sid]
        pe = E.combine([(1.0, M, scores, None)], "people", 4)
        na = E.combine([(1.0, M, None, None)], "nash", 4)
        legal = _solve(V.compose(reg, sets_, f, {"list_only": True, "prune": 0, "switches": True, "mega": "both"}))
        hit = rows_.index(human) if human in rows_ else None
        rows.append({"id": x["id"], "group": x["group"], "kind": x["kind"],
                     "kept": hit is not None, "people": int(np.argmax(pe["ewp"])) == hit if hit is not None else False,
                     "nash": float(na["strategy"][hit]) if hit is not None else 0.0, "model": hit == 0,
                     "legal": len(legal["all"][sid]) if legal else None, "rows": len(rows_)})
    return rows


def report(rows: list[dict[str, Any]], boots: int = 4000, seed: int = 11) -> dict[str, Any]:
    groups = sorted({r["group"] for r in rows})
    gi = np.array([groups.index(r["group"]) for r in rows])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(groups), (boots, len(groups)))
    n = np.bincount(gi, minlength=len(groups)).astype(float)

    def rate(v: np.ndarray) -> dict[str, Any]:
        s = np.bincount(gi, v, minlength=len(groups))
        b = s[idx].sum(1) / n[idx].sum(1)
        return {"rate": round(float(v.mean()), 4), "ci95": [round(float(x), 4) for x in np.percentile(b, [2.5, 97.5])]}

    out = {"decisions": len(rows), "groups": len(groups)}
    for k in ("kept", "people", "model"):
        out[k] = rate(np.array([float(r[k]) for r in rows]))
    out["nash"] = rate(np.array([r["nash"] for r in rows]))
    out["uniform"] = rate(np.array([1 / r["legal"] if r["legal"] else 0.0 for r in rows]))
    out["mean_rows"] = round(float(np.mean([r["rows"] for r in rows])), 2)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", type=int, default=400)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=3)
    a = ap.parse_args()
    from vgc.data.snapshots import replay_group
    from vgc.data.splits import load_rules
    from vgc.meta import pool, replays
    from vgc.regulation import load_regulation

    reg = load_regulation("reg_mc")
    rules = load_rules(reg)
    held = [r for fmt in pool.formats_for(reg) for r in replays.cached(fmt)
            if rules.split_of("human", r["id"], [], replay_group(r)) == "heldout_human"]
    random.Random(a.seed).shuffle(held)
    with Pool(a.workers, initializer=_init) as p:
        rows = [x for rs in p.imap_unordered(_game, held[:a.games], chunksize=2) for x in rs]
    res = {"settings": {"games": a.games, "side_k": __import__("vgc.policy.ewp", fromlist=["SIDE_K"]).SIDE_K},
           **report(rows)}
    OUT.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
