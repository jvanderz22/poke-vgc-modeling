"""Phase 6 again, against the Phase 9 policy (PLAN-v4 step 3).

Does the people policy's self-play win rate between two teams predict who won the human games
between them? The battles are `policy_gate.py`'s runs `step3-0` to `step3-3` (1,500 human-corpus
pairings x 8, the policy on both sides, the heuristic's team preview, played on Kaggle and merged
after replaying here). They are turned into the per-pairing win rates `vgc.sim.validity.score`
takes, so the verdict is the same code that judged the heuristic (`sim_validity.json`). The
heuristic is also scored on these same 1,500 pairings, from its Phase 6 run, for a paired reading.

    .venv/bin/python scripts/analysis/step3_validity.py
"""

from __future__ import annotations

import json
from typing import Any

from vgc.sim.selfplay import wilson
from vgc.sim.validity import ANALYSIS, human_games, score, verdict

NAMES = [f"step3-{i}" for i in range(4)]


def policy_sim() -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    from policy_gate import _battles, _load

    sim: dict[tuple[str, str], dict[str, Any]] = {}
    battles = errors = 0
    turns = []
    for name in NAMES:
        run, rows = _load(name)
        per = run["per_pairing"]
        for r in _battles(run):
            battles += 1
            if "error" in r or r["winner"] is None:
                errors += "error" in r
                continue
            m = rows[r["index"]]
            first = rows[m["pairing"] * per]
            a, b = first["team_a_id"], first["team_b_id"]
            pair = (a, b) if a <= b else (b, a)
            a_side = r["a_side"] if m["team_a_id"] == a else ("p2" if r["a_side"] == "p1" else "p1")
            s = sim.setdefault(pair, {"a": a, "b": b, "wins": 0, "n": 0, "turns": []})
            s["wins"] += r["winner"] == a_side
            s["n"] += 1
            s["turns"].append(r["turns"])
            turns.append(r["turns"])
    for s in sim.values():
        lo, hi = wilson(s["wins"], s["n"])
        s.update({"wp": (s["wins"] + 1) / (s["n"] + 2), "wp_raw": round(s["wins"] / s["n"], 4),
                  "ci": [round(lo, 4), round(hi, 4)], "turns": round(sum(s.pop("turns")) / s["n"], 1)})
    return sim, {"battles": battles, "errors": errors, "pairings": len(sim),
                 "mean_turns": round(sum(turns) / len(turns), 2)}


def main() -> None:
    from vgc.regulation import load_regulation

    reg = load_regulation("reg_mc")
    games = human_games(reg)
    sim, run = policy_sim()
    scored = score(games, sim)
    heur = json.loads((ANALYSIS / reg.id / "sim_validity.json").read_text())
    hsim = {(p["a"], p["b"]) if p["a"] <= p["b"] else (p["b"], p["a"]): p for p in heur["pairings"]}
    hsim = {k: v for k, v in hsim.items() if k in sim}
    hscored = score(games, hsim)
    v = verdict(scored)
    # `verdict`'s own reading is written for the heuristic.
    v["reading"] = ("the Phase 9 policy orders team strength the way real games do" if v["pass"] else
                    "one of the two scale-free tests clears its interval: more groups before building on it"
                    if v["orders_correctly"] or v["recalibrated_beats_constant"] else
                    "no usable team-strength signal from the Phase 9 policy's self-play either: the "
                    "deterministic stack stays the floor, and Phases 10-11 stay blocked")
    out = {"regulation": reg.id, "policy": "ewp-people vs ewp-people (PLAN-policy), heuristic team preview",
           "runs": NAMES, "run": run, **scored, "verdict": v,
           "heuristic_on_these_pairings": {"verdict": verdict(hscored), "spread": hscored["simulated_wp_spread"]},
           "pairings": [sim[k] | {"human_games": sum(1 for g in games if g.pairing == k)} for k in sorted(sim)]}
    path = ANALYSIS / reg.id / "sim_validity_ewp.json"
    path.write_text(json.dumps(out, indent=1) + "\n")
    show = {k: out[k] for k in ("run", "verdict", "simulated_wp_spread")}
    show["heuristic_on_these_pairings"] = out["heuristic_on_these_pairings"]
    show["subsets"] = [{k: s.get(k) for k in ("subset", "games", "groups", "auc", "auc_95ci", "logloss_delta",
                                               "logloss_delta_95ci")} for s in scored["subsets"]]
    print(json.dumps(show, indent=1))


if __name__ == "__main__":
    main()
