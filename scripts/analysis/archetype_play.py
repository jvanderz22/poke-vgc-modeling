"""How Trick Room and Fake Out are played, by people and in self-play (PLAN-v5 step 3a's first look).

The diagnostics found self-play rating Trick Room and Fake Out teams lower than human results do
(phase6-findings §10). Before changing the search, this reads the battles already played and asks
whether the policy plays the two the way people do, or plays them and gets less from them:

  Fake Out     of the Pokémon that lead with Fake Out on their set, the share that use it on turn 1;
               and of all Fake Outs used, the share that land (not blocked, not failed)
  Trick Room   of the sides with a Trick Room user on the field at some point, the share that use it,
               that set it, and the turn they first set it; and how often the other side reverses it
  results      the win rate of a side that set Trick Room, and of a side whose turn-1 Fake Out landed

People: the cached open-sheet replays, battles that ended normally. Self-play: the Phase 9 policy's
Phase 6 runs (`gate-step3-*`). The heuristic's run kept no logs. Each rate's interval is a
bootstrap by battle.

    .venv/bin/python scripts/analysis/archetype_play.py
"""

from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from typing import Any

import numpy as np

from vgc import paths
from vgc.regulation import to_id

OUT = paths.DATA / "analysis" / "reg_mc" / "archetype_play.json"
RUNS = {"policy": [f"gate-step3-{i}" for i in range(4)]}       # the heuristic's Phase 6 run kept no logs


def _packed_moves(packed: str) -> dict[str, set[str]]:
    """Species → move ids, from a packed team (`|showteam|` or a `>player` line)."""
    out = {}
    for mon in packed.split("]"):
        f = mon.split("|")
        if len(f) < 5:
            continue
        species = to_id(f[1] or f[0])
        out[species] = {to_id(m) for m in f[4].split(",") if m}
    return out


def read(lines: list[str], teams: dict[str, dict[str, set[str]]], winner: str | None) -> dict[str, Any] | None:
    """One battle's Fake Out and Trick Room facts, per side."""
    ident: dict[str, str] = {}                      # "p1a: Nick" -> species id
    turn = 0
    out = {sid: {"fo_leads": 0, "fo_turn1": 0, "fo_used": 0, "fo_landed": 0, "tr_user_seen": False,
                 "tr_used": False, "tr_set": False, "tr_first": None, "fo_turn1_landed": False}
           for sid in ("p1", "p2")}
    pending_fo: dict[str, str] = {}
    for line in lines:
        p = line.split("|")
        kind = p[1] if len(p) > 1 else ""
        if kind in ("switch", "drag") and len(p) > 3:
            sp = to_id(p[3].split(",")[0])
            ident[p[2].split(":")[0][:3] + ":" + p[2].split(": ", 1)[-1]] = sp
            sid = p[2][:2]
            moves = next((v for k, v in teams[sid].items() if sp.startswith(k) or k.startswith(sp)), set())
            if "trickroom" in moves:
                out[sid]["tr_user_seen"] = True
            if turn == 0 and "fakeout" in moves:
                out[sid]["fo_leads"] += 1
        elif kind == "turn":
            turn = int(p[2])
        elif kind == "move" and len(p) > 3 and re.match(r"p[12][ab]", p[2]):
            pending_fo.clear()                      # a Fake Out lands before the next move, or not at all
            sid, move = p[2][:2], to_id(p[3])
            if move == "fakeout":
                out[sid]["fo_used"] += 1
                out[sid]["fo_turn1"] += turn == 1
                pending_fo[sid] = "t1" if turn == 1 else "later"
            elif move == "trickroom":
                out[sid]["tr_used"] = True
        elif kind == "-damage" and pending_fo and len(p) > 2 and "[from]" not in line:
            for sid, when in list(pending_fo.items()):
                if p[2][:2] != sid:
                    out[sid]["fo_landed"] += 1
                    out[sid]["fo_turn1_landed"] |= when == "t1"
                    del pending_fo[sid]
        elif kind == "-fieldstart" and "Trick Room" in line:
            of = next((x[5:7] for x in p if x.startswith("[of] ")), None)
            if of in out:
                out[of]["tr_set"] = True
                if out[of]["tr_first"] is None:
                    out[of]["tr_first"] = turn
    if winner not in ("p1", "p2"):
        return None
    for sid in ("p1", "p2"):
        out[sid]["won"] = winner == sid
    return out


def humans(reg) -> list[dict[str, Any]]:
    from vgc.meta import pool, replays

    rows = []
    for fmt in pool.formats_for(reg):
        for r in replays.cached(fmt):
            lines = r["log"].split("\n")
            show = {x.split("|")[2]: x.split("|", 3)[3] for x in lines if x.startswith("|showteam|")}
            if set(show) != {"p1", "p2"} or any(x.startswith("|-message|") and "forfeit" in x for x in lines):
                continue
            names = {x.split("|")[2]: x.split("|")[3] for x in lines if x.startswith("|player|") and len(x.split("|")) > 3}
            win = next((x.split("|")[2] for x in lines if x.startswith("|win|")), None)
            winner = next((sid for sid, n in names.items() if n == win), None)
            got = read(lines, {sid: _packed_moves(t) for sid, t in show.items()}, winner)
            if got:
                rows.append(got)
    return rows


def selfplay(run_ids: list[str]) -> list[dict[str, Any]]:
    from vgc.sim.selfplay import SELFPLAY, load_battles

    rows = []
    for rid in run_ids:
        for b in load_battles(SELFPLAY / rid):
            if "log" not in b or "error" in b:
                continue
            log = b["log"] if isinstance(b["log"], list) else ast.literal_eval(b["log"])
            inp = b["input_log"] if isinstance(b["input_log"], list) else ast.literal_eval(b["input_log"])
            teams = {}
            for x in inp:
                if x.startswith(">player "):
                    sid, blob = x.split(" ", 2)[1], json.loads(x.split(" ", 2)[2])
                    teams[sid] = _packed_moves(blob["team"])
            if set(teams) != {"p1", "p2"}:
                continue
            got = read(log, teams, b.get("winner"))
            if got:
                rows.append(got)
    return rows


def summary(rows: list[dict[str, Any]], seed: int = 0) -> dict[str, Any]:
    sides = [s for r in rows for s in (r["p1"], r["p2"])]
    battle = np.repeat(np.arange(len(rows)), 2)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(rows), (1000, len(rows)))

    def rate(num: np.ndarray, den: np.ndarray) -> dict[str, Any]:
        n_b = np.bincount(battle, num, minlength=len(rows))
        d_b = np.bincount(battle, den, minlength=len(rows))
        if d_b.sum() == 0:
            return {"n": 0}
        b = n_b[idx].sum(1) / np.maximum(d_b[idx].sum(1), 1)
        return {"n": int(d_b.sum()), "rate": round(float(n_b.sum() / d_b.sum()), 4),
                "ci95": [round(float(x), 4) for x in np.percentile(b, [2.5, 97.5])]}

    f = lambda k: np.array([float(s[k]) for s in sides])
    tr_seen = f("tr_user_seen")
    firsts = [s["tr_first"] for s in sides if s["tr_first"] is not None]
    reversed_ = np.array([float(r[o]["tr_set"] and r[s]["tr_set"]) for r in rows for s, o in (("p1", "p2"), ("p2", "p1"))])
    return {
        "battles": len(rows),
        "fake_out": {"used_turn1_of_leads": rate(f("fo_turn1"), f("fo_leads")),
                     "landed_of_used": rate(f("fo_landed"), f("fo_used")),
                     "win_rate_when_turn1_landed": rate(f("won") * f("fo_turn1_landed"), f("fo_turn1_landed"))},
        "trick_room": {"used_of_sides_with_a_user": rate(f("tr_used") * tr_seen, tr_seen),
                       "set_of_sides_with_a_user": rate(f("tr_set") * tr_seen, tr_seen),
                       "first_set_turn": {"median": float(np.median(firsts)) if firsts else None,
                                          "share_turn1": round(float(np.mean([t == 1 for t in firsts])), 4) if firsts else None},
                       "opponent_also_set_it": rate(reversed_ * f("tr_set"), f("tr_set")),
                       "win_rate_when_set": rate(f("won") * f("tr_set"), f("tr_set"))},
    }


def main() -> None:
    from vgc.regulation import load_regulation

    reg = load_regulation("reg_mc")
    res = {"people": summary(humans(reg))}
    for name, ids in RUNS.items():
        res[name] = summary(selfplay(ids))
    OUT.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
