"""Positions from a player's view, checked against the truth self-play knows (PLAN-policy, stage 2).

Heuristic self-play battles on pool teams, open sheets. Each is traced again, and at every move
decision each player's view plans its positions (`vgc.policy.view.plan`). Self-play knows what the
view could not: which Pokémon the opponent brought and their spreads. Reported:

  back      whether the opponent's true back is among the guesses solved, and the weight it got
  order     whether the true move order on the field (true spreads) is among the solved
  value     on every `--solve-every`-th decision, the answer from the solved positions against the
            answer from the true position (true back, true spreads), at the same search
  time      planning per decision

    .venv/bin/python scripts/analysis/policy_view_check.py --battles 120     # about 10 minutes
"""

from __future__ import annotations

import argparse
import json
import random
import statistics as st
import subprocess
import time
from collections import defaultdict
from multiprocessing import Pool
from typing import Any

from vgc import paths

OUT = paths.DATA / "analysis" / "reg_mc" / "policy_view_check.json"
_W: dict[str, Any] = {}


def _init() -> None:
    from vgc.engine.runner import BattleRunner
    from vgc.regulation import load_regulation

    _W["reg"] = load_regulation("reg_mc")
    _W["runner"] = BattleRunner()
    _W["solver"] = subprocess.Popen(["node", str(paths.SIDECAR / "showdown" / "endgame-solver.js"), str(paths.SHOWDOWN),
                                     "--serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)


def _solve(pos: dict[str, Any]) -> dict[str, Any] | None:
    p = _W["solver"]
    p.stdin.write(json.dumps({"id": 0, "position": pos}) + "\n")
    p.stdin.flush()
    out = json.loads(p.stdout.readline())
    return out.get("result")


def _order_name(reg, sets_: dict[str, list[dict[str, Any]]], n_on: dict[str, int], f: dict[str, Any]) -> str:
    """The move order on the field with these spreads, named as `speed_classes` names it."""
    from vgc.wp import doubles, solver

    keys = [(sid, i) for sid in ("p1", "p2") for i in range(n_on[sid])]
    sp = {k: solver.position_speed(reg, sets_[k[0]][k[1]], sets_[k[0]][k[1]]["sp"], doubles._one(f, k[0], k[1]), k[0])
          for k in keys}
    order = sorted(keys, key=lambda k: -sp[k])
    ties = [sp[a] == sp[b] for a, b in zip(order, order[1:])] + [False]
    return "".join(f"{k[0]}{'ab'[k[1]]}" + ("=" if t else ">") for k, t in zip(order, ties))[:-1]


def _top(result: dict[str, Any] | None, sid: str) -> str | None:
    """The choice the side's equilibrium strategy plays most."""
    if not result or not result.get("strategy"):
        return None
    st_, moves = result["strategy"][sid], result["moves"][sid]
    return moves[max(range(len(st_)), key=lambda i: st_[i])]


def _battle(task: tuple[dict[str, Any], int]) -> list[dict[str, Any]]:
    from vgc.battle.entry import from_team
    from vgc.policy import view as V
    from vgc.regulation import to_id
    from vgc.wp import doubles

    rec, solve_every = task
    reg = _W["reg"]
    tr = _W["runner"].request({"op": "trace", "id": rec["battle_id"], "inputLog": rec["input_log"], "ots": True})
    texts = {"p1": rec["text_p1"], "p2": rec["text_p2"]}
    truth = {sid: from_team(reg, texts[sid]) for sid in ("p1", "p2")}
    picks = {}
    for line in rec["input_log"]:
        for sid in ("p1", "p2"):
            if line.startswith(f">{sid} team "):
                picks[sid] = [int(x) - 1 for x in line.split(" ", 2)[2].replace(" ", "").split(",")]
    brought = {sid: [truth[sid][i]["species"] for i in picks[sid]] for sid in ("p1", "p2")}
    rows = []
    n = 0
    for sid in ("p1", "p2"):
        them = "p2" if sid == "p1" else "p1"
        v = V.PlayerView(reg, sid, texts[sid])
        for step in tr["steps"]:
            v.feed(step[sid])
            if sid not in step["requests"]:
                continue
            req = json.loads(step["requests"][sid])
            v.request(req)
            if not req.get("active") or req.get("wait") or req.get("forceSwitch"):
                continue
            state = v.o
            left = {s: reg.bring - sum(m.state == "fainted" for m in state.sides[s].mons) for s in ("p1", "p2")}
            if max(left.values()) <= 2:
                continue
            t0 = time.perf_counter()
            pl = V.plan(reg, v)
            ms = (time.perf_counter() - t0) * 1000
            row = {"battle": rec["battle_id"], "side": sid, "turn": state.turn, "ms": round(ms, 1),
                   "kind": f"{left[sid]}v{left[them]}", "eligible": pl["eligible"], "reason": pl.get("reason")}
            if not pl["eligible"]:
                rows.append(row)
                continue
            seen = {m.species for m in state.sides[them].mons if m.state in ("active", "bench", "fainted")}
            true_back = sorted(s for s in brought[them] if s not in seen)
            solved_backs = [sorted(j["back"]) for j in pl["jobs"]]
            all_backs = V.backs(reg, state, them)
            w_true = next((w for c, w in all_backs if sorted(m.species for m in c) == true_back), 0.0)
            # The true sets of everything in the position, in the plan's order.
            on = {s: doubles.actives(state, s) for s in ("p1", "p2")}
            back_seen = {s: [m for m in state.sides[s].mons if m.state == "bench"] for s in ("p1", "p2")}
            back_true = {sid: back_seen[sid], them: back_seen[them] + [m for m in state.sides[them].mons
                                                                       if m.species in true_back]}
            mons = {s: on[s] + back_true[s] for s in ("p1", "p2")}

            def true_set(s, m):
                e = next(x for x in truth[s] if to_id(x["species"]) == to_id(m.species))
                return {"species": e["species"], **{k: e.get(k) for k in ("item", "ability", "nature")},
                        "moves": list(e["moves"]), "sp": {k: x for k, x in e["sp"].items() if x},
                        "mega": m.forme if m.mega else None}
            sets_ = {s: [true_set(s, m) for m in mons[s]] for s in ("p1", "p2")}
            f = doubles.facts(reg, v, sets_, mons=mons)
            f["bench"] = {s: len(mons[s]) - len(on[s]) for s in ("p1", "p2")}
            used = {s: any(m.mega for m in state.sides[s].mons) for s in ("p1", "p2")}
            f["megaUsed"] = {s: True for s, u in used.items() if u}
            true_order = _order_name(reg, sets_, {s: len(on[s]) for s in ("p1", "p2")}, f)
            row.update({"considered": pl["considered"], "unsolved": pl["unsolved"], "unseen_back": len(true_back),
                        "back_solved": true_back in solved_backs, "back_weight": round(w_true, 4),
                        "order_solved": true_order in {j["order"] for j in pl["jobs"]}})
            if solve_every and n % solve_every == 0:
                res = [_solve(j["position"]) for j in pl["jobs"]]
                ans = V.combine(pl["jobs"], res)
                search = pl["jobs"][0]["position"]["search"]
                tru = _solve(V.compose(reg, sets_, f, search))
                # The noise floor: the true position again with other dice.
                tru2 = _solve(V.compose(reg, sets_, f, {**search, "salt": "floor"}))
                if ans and tru and tru2:
                    row.update({"value": round(ans["value"], 4), "true_value": tru["value"], "true_value_b": tru2["value"],
                                "choice_same": _top(res[0], sid) == _top(tru, sid),
                                "choice_same_floor": _top(tru2, sid) == _top(tru, sid)})
            n += 1
            rows.append(row)
    return rows


def generate(n: int, seed: int) -> list[dict[str, Any]]:
    from vgc.meta import pool
    from vgc.regulation import load_regulation
    from vgc.sim import selfplay

    reg = load_regulation("reg_mc")
    teams = pool.load_pool(reg)
    rng = random.Random(seed)
    pairs = [rng.sample(teams, 2) for _ in range(n)]
    ms = [selfplay.Matchup(a.text, b.text, "heuristic", "heuristic", a.id, b.id, ots=True) for a, b in pairs]
    summary = selfplay.run(ms, seed=seed, workers=8, run_id=f"policy-view-check-s{seed}-n{n}")
    out = []
    for r in selfplay.load_battles(paths.ROOT / summary["out_dir"]):
        if "error" in r:
            continue
        a, b = pairs[r["index"]]
        r["text_p1"], r["text_p2"] = a.text, b.text
        r["battle_id"] = f"pvc-{r['index']}"
        out.append(r)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--battles", type=int, default=120)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--solve-every", type=int, default=3)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    battles = generate(a.battles, a.seed)
    with Pool(a.workers, initializer=_init) as p:
        rows = [r for rs in p.imap_unordered(_battle, [(b, a.solve_every) for b in battles]) for r in rs]
    ok = [r for r in rows if r["eligible"]]
    by_kind = defaultdict(list)
    for r in ok:
        by_kind[r["kind"]].append(r)

    def summary(rs: list[dict[str, Any]]) -> dict[str, Any]:
        hidden = [r for r in rs if r["unseen_back"]]
        solved = [r for r in rs if "value" in r]
        gaps = [abs(r["value"] - r["true_value"]) for r in solved]
        out = {"decisions": len(rs),
               "with_unseen_back": len(hidden),
               "true_back_solved": round(sum(r["back_solved"] for r in hidden) / len(hidden), 3) if hidden else None,
               "true_back_weight_mean": round(st.mean(r["back_weight"] for r in hidden), 3) if hidden else None,
               "true_order_solved": round(sum(r["order_solved"] for r in rs) / len(rs), 3) if rs else None,
               "unsolved_mean": round(st.mean(r["unsolved"] for r in rs), 3) if rs else None,
               "plan_ms_median": round(st.median(r["ms"] for r in rs), 1) if rs else None,
               "plan_ms_p90": round(sorted(r["ms"] for r in rs)[int(0.9 * len(rs))], 1) if rs else None}
        if gaps:
            floor = [abs(r["true_value_b"] - r["true_value"]) for r in solved]
            out.update({"noise_floor_gap_mean": round(st.mean(floor), 4), "noise_floor_gap_median": round(st.median(floor), 4),
                        "noise_floor_over_0.1": sum(g > 0.1 for g in floor),
                        "choice_same": sum(r["choice_same"] for r in solved),
                        "choice_same_floor": sum(r["choice_same_floor"] for r in solved)})
            out.update({"valued": len(gaps), "value_gap_mean": round(st.mean(gaps), 4),
                        "value_gap_median": round(st.median(gaps), 4),
                        "over_0.1": sum(g > 0.1 for g in gaps), "over_0.2": sum(g > 0.2 for g in gaps),
                        "same_side_favoured": sum((r["value"] - 0.5) * (r["true_value"] - 0.5) >= 0 for r in solved)})
        return out

    res = {"battles": len(battles), "decisions": len(rows), "eligible": len(ok),
           "not_built": dict(sorted(((k, sum(1 for r in rows if not r["eligible"] and r["reason"] == k))
                                     for k in {r["reason"] for r in rows if not r["eligible"]}), key=lambda kv: -kv[1])),
           "all": summary(ok), "by_kind": {k: summary(v) for k, v in sorted(by_kind.items(), reverse=True)}}
    OUT.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
