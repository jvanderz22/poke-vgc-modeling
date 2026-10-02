"""The policy's own checks before any gate (PLAN-policy, stage 4).

Battles between the EWP policy and the heuristic on human-corpus pairings, open sheets, each pairing
played twice with the policies swapped between the teams. Not the gate (stage 5): its win rate is
shown, not judged. What is judged:

  invalid      choices the simulator rejected: must be none
  replay       battles played again from their seeds in a fresh process: the same inputs, choice by
               choice (a solve that timed out is the one way this can fail, and is reported)
  fallbacks    decisions the heuristic made instead, by reason
  time         a decision's wall time on one core, by state kind, the plan's 1 s median against it

    .venv/bin/python scripts/analysis/policy_check.py --pairs 50 --workers 4
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any

import numpy as np

from vgc import paths

OUT = paths.DATA / "analysis" / "reg_mc"


def matchups(reg, pairs: int, seed: int, policy: str) -> list:
    from vgc.meta.pool import load_pool
    from vgc.sim.selfplay import Matchup
    from vgc.sim.validity import human_games, select_pairings

    pool = {t.id: t for t in load_pool(reg)}
    chosen = [p for p in select_pairings(human_games(reg), 0) if p[0] in pool and p[1] in pool]
    import random

    chosen = sorted(random.Random(seed).sample(chosen, pairs))
    out = []
    for i, (a, b) in enumerate(chosen):
        # The same pairing twice, the policies swapped between the teams, sides alternating.
        out.append(Matchup(pool[a].text, pool[b].text, policy, "heuristic", a, b, swap_sides=bool(i % 2)))
        out.append(Matchup(pool[b].text, pool[a].text, policy, "heuristic", b, a, swap_sides=not i % 2))
    return out


def _q(xs: list[float], q: float) -> float | None:
    return round(float(np.percentile(xs, q))) if xs else None


def report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if "error" not in r]
    decisions = [d for r in ok for d in r.get("decisions", [])]
    fallback = collections.Counter(d["fallback"] for d in decisions if d.get("fallback"))
    by_kind: dict[str, list[float]] = collections.defaultdict(list)
    for d in decisions:
        k = ("replace " if d.get("replacing") else "") + (d.get("kind") or "none")
        by_kind[k].append(d["ms"] + d.get("ms_fallback", 0))
    ms = [d["ms"] + d.get("ms_fallback", 0) for d in decisions]
    searched = [d for d in decisions if not d.get("fallback")]
    decided = [r for r in ok if r["a_won"] is not None]
    return {
        "battles": len(rows), "errors": len(rows) - len(ok), "error_examples": [r["error"] for r in rows if "error" in r][:5],
        "invalid_choices": sum(r["invalid_choices"] for r in ok),
        "trapped_retries": sum(r.get("trapped_retries", 0) for r in ok),
        "ewp_win_rate_not_the_gate": round(sum(r["a_won"] for r in decided) / len(decided), 3) if decided else None,
        "mean_turns": round(float(np.mean([r["turns"] for r in ok])), 2) if ok else None,
        "decisions": len(decisions), "searched": len(searched),
        "fallbacks": {k: v for k, v in fallback.most_common()},
        "timeouts": sum(bool(d.get("timeout")) for d in decisions),
        "ms": {"median": _q(ms, 50), "p90": _q(ms, 90), "p99": _q(ms, 99), "max": max(ms) if ms else None},
        "ms_by_kind": {k: {"n": len(v), "median": _q(v, 50), "p90": _q(v, 90), "max": max(v)}
                       for k, v in sorted(by_kind.items(), key=lambda kv: -len(kv[1]))},
        "positions_solved": dict(collections.Counter(d.get("positions") for d in searched)),
        "rows_kept_by_all_guesses": sum(d.get("solved") == d.get("positions") for d in searched),
    }


def replay(reg, rows: list[dict[str, Any]], matches: list, n: int, ewp: dict[str, Any]) -> dict[str, Any]:
    """The first `n` battles played again here, from their seeds."""
    from vgc.engine.runner import BattleRunner, battle_seed, play_battle
    from vgc.policy.ewp import EWPPolicy
    from vgc.policy.heuristic import HeuristicPolicy

    same, checked = 0, []
    with BattleRunner() as runner:
        for r in [r for r in rows if "error" not in r][:n]:
            m = matches[r["index"]]
            pol = EWPPolicy(reg, m.policy_a.split("-")[1], **ewp)
            h = HeuristicPolicy(reg)
            sides = [(m.team_a, pol), (m.team_b, h)]
            if m.swap_sides:
                sides.reverse()
            rec = play_battle(runner, r["battle_id"], r["seed"], reg.showdown_format,
                              (sides[0][0], sides[1][0]), (sides[0][1], sides[1][1]), ots=m.ots)
            pol.close()
            hit = rec.input_log == r["input_log"]
            same += hit
            timed = any(d.get("timeout") for d in r.get("decisions", []))
            checked.append({"index": r["index"], "same": hit, "had_timeout": timed})
    return {"replayed": len(checked), "same": same, "battles": checked}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", type=int, default=50)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seed", type=int, default=4)
    ap.add_argument("--opponent", default="people", choices=["people", "nash"])
    ap.add_argument("--side-k", type=int, default=None)
    ap.add_argument("--positions", type=int, default=None)
    ap.add_argument("--replay", type=int, default=4)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()

    from vgc.regulation import load_regulation
    from vgc.sim.selfplay import load_battles, run

    reg = load_regulation("reg_mc")
    ewp: dict[str, Any] = {}
    if a.side_k:
        ewp["search"] = {"side_k": a.side_k}
    if a.positions:
        ewp["positions"] = a.positions
    ms = matchups(reg, a.pairs, a.seed, f"ewp-{a.opponent}")
    tag = a.tag or f"{a.opponent}" + (f"-k{a.side_k}" if a.side_k else "") + (f"-d{a.positions}" if a.positions else "")
    summary = run(ms, reg_id=reg.id, seed=a.seed, workers=a.workers, run_id=f"policy-check-{tag}-p{a.pairs}", ewp=ewp)
    rows = load_battles(Path(summary["out_dir"]))
    for r in rows:
        r.setdefault("battle_id", f"{summary['run_id']}-{r['index']}")
    out = {"settings": {"opponent": a.opponent, "pairs": a.pairs, "seed": a.seed, **ewp},
           "run": {k: summary[k] for k in ("run_id", "wall_seconds", "battles_per_second", "outcome_digest")},
           **report(rows), "replay": replay(reg, rows, ms, a.replay, ewp)}
    path = OUT / f"policy_check_{tag}.json"
    path.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
