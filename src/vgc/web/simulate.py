"""Play one battle between two teams and narrate it turn by turn, with win probability.

This is the simulator the whole project is built on, not an approximation of it: the same seeded
runner that generates training data, the same observer, the same WP model. A battle is a pure
function of (seed, teams, policies), so the same seed replays exactly — which is what makes a
turn worth pointing at and discussing.

The policies are the heuristic from Phase 2, not strong play. It beats random 98.4% of the time
and that is all that is claimed for it; a line it takes is not evidence that the line is good.
Phase 6's search is what makes played-out battles worth reading as advice.
"""

from __future__ import annotations

from typing import Any

from vgc.regulation import Regulation
from vgc.web.narrate import narrate_turns


def _make_policy(name: str, reg: Regulation):
    """`selfplay._policy` caches into per-worker globals that only exist inside a pool worker, so
    the web process builds its own."""
    if name == "random":
        from vgc.engine.runner import RandomPolicy

        return RandomPolicy()
    if name == "heuristic":
        from vgc.policy.heuristic import HeuristicPolicy

        return HeuristicPolicy(reg)
    raise ValueError(f"unknown policy {name!r}")


def simulate(reg: Regulation, team_a: str, team_b: str, seed: int = 1,
             policy_a: str = "heuristic", policy_b: str = "heuristic",
             version: str | None = None) -> dict[str, Any]:
    """One battle, narrated, with spectator WP at every decision point it has one for."""
    from vgc.data.snapshots import trace_snapshots
    from vgc.engine.runner import BattleRunner, play_battle
    from vgc.wp.features import featurize
    from vgc.wp.models import symmetrize
    from vgc.wp.tools import _load

    battle_id = f"web-{seed}"
    with BattleRunner() as runner:
        rec = play_battle(runner, battle_id, [seed, seed + 1, seed + 2, seed + 3], reg.showdown_format,
                          teams=(team_a, team_b), policies=(_make_policy(policy_a, reg), _make_policy(policy_b, reg)))
        trace = runner.request({"op": "trace", "id": battle_id, "inputLog": rec.input_log, "ots": rec.ots})

    turns = narrate_turns(rec.log)
    wp_by_turn: dict[int, float] = {}
    wp_error = None
    if version:
        try:
            # trace_snapshots expects a self-play row, which is exactly what summary() produces.
            recs = [r for r in trace_snapshots(trace, rec.summary(), reg)
                    if r["obs"]["perspective"] == "spectator" and r["kind"] in ("turn", "switch", "preview")]
            if recs:
                model, fz = _load(reg, version)
                d = featurize(recs, fz)
                p = symmetrize(d, model.predict(d)[0])["p"]
                for r, wp in zip(recs, p):
                    # One WP per turn: the first decision point in it, so the number describes the
                    # position you were looking at when you chose, not the aftermath.
                    wp_by_turn.setdefault(int(r["obs"]["turn"] or 0), float(wp))
        except Exception as e:  # a WP failure must not cost you the battle log
            wp_error = f"{type(e).__name__}: {e}"

    for t in turns:
        t["wp_p1"] = wp_by_turn.get(t["turn"])

    return {
        "battle_id": battle_id, "seed": seed, "format": rec.format,
        "winner": rec.winner, "turns": rec.turns, "score": rec.score,
        "policies": {"p1": policy_a, "p2": policy_b},
        "invalid_choices": rec.invalid_choices, "seconds": round(rec.seconds, 2),
        "version": version, "wp_error": wp_error,
        "timeline": turns,
    }
