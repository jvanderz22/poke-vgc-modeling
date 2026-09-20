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

import re
from typing import Any

from vgc.regulation import Regulation

# Protocol lines worth showing. Everything else (|upkeep|, |t:|, request payloads) is noise here.
_IGNORE = {"", "t:", "upkeep", "request", "uhtml", "html", "player", "teamsize", "gametype", "gen",
           "tier", "rule", "start", "teampreview", "seed", "choice", "j", "l", "c", "inactive",
           "split", "sentchoices", "clearpoke", "poke", "showteam", "done", "-hint", "raw"}


def _name(ident: str) -> str:
    """`p1a: Rillaboom` → `Rillaboom`; the nickname is what the log carries."""
    return ident.split(": ", 1)[1] if ": " in ident else ident


def _side_of(ident: str) -> str | None:
    m = re.match(r"(p[12])[ab]?:", ident)
    return m.group(1) if m else None


def _narrate(line: str) -> dict[str, Any] | None:
    """One protocol line as something readable, or None to drop it."""
    if not line.startswith("|"):
        return None
    parts = line[1:].split("|")
    kind, args = parts[0], parts[1:]
    if kind in _IGNORE:
        return None

    def ev(text: str, side: str | None = None, key: str = kind) -> dict[str, Any]:
        return {"kind": key, "text": text, "side": side}

    match kind:
        case "move":
            return ev(f"{_name(args[0])} used {args[1]}!", _side_of(args[0]))
        case "switch" | "drag":
            hp = args[2].split(" ")[0] if len(args) > 2 else ""
            return ev(f"{_name(args[0])} came in ({hp})", _side_of(args[0]))
        case "faint":
            return ev(f"{_name(args[0])} fainted!", _side_of(args[0]))
        case "-damage" | "-heal":
            who, hp = _name(args[0]), args[1].split(" ")[0]
            verb = "took damage" if kind == "-damage" else "healed"
            return ev(f"{who} {verb} → {hp}", _side_of(args[0]))
        case "-status":
            return ev(f"{_name(args[0])} was {args[1].upper()}'d", _side_of(args[0]))
        case "-curestatus":
            return ev(f"{_name(args[0])} shook off {args[1].upper()}", _side_of(args[0]))
        case "-boost" | "-unboost":
            arrow = "rose" if kind == "-boost" else "fell"
            return ev(f"{_name(args[0])}'s {args[1]} {arrow} ({args[2]})", _side_of(args[0]))
        case "-crit":
            return ev("A critical hit!", _side_of(args[0]))
        case "-supereffective":
            return ev("It's super effective!", _side_of(args[0]))
        case "-resisted":
            return ev("It's not very effective…", _side_of(args[0]))
        case "-immune":
            return ev(f"{_name(args[0])} is immune.", _side_of(args[0]))
        case "-miss":
            return ev(f"{_name(args[0])}'s attack missed.", _side_of(args[0]))
        case "-fail":
            return ev(f"{_name(args[0])}: it failed.", _side_of(args[0]))
        case "-item":
            return ev(f"{_name(args[0])}'s {args[1]}", _side_of(args[0]))
        case "-enditem":
            return ev(f"{_name(args[0])} used up its {args[1]}", _side_of(args[0]))
        case "-ability":
            return ev(f"{_name(args[0])}: {args[1]}", _side_of(args[0]))
        case "-mega":
            return ev(f"{_name(args[0])} Mega Evolved into {args[1]}!", _side_of(args[0]))
        case "-weather":
            return None if len(args) > 1 and args[1] == "[upkeep]" else ev(f"Weather: {args[0]}")
        case "-fieldstart" | "-fieldend":
            return ev(("" if kind == "-fieldstart" else "End: ") + args[0].replace("move: ", ""))
        case "-sidestart":
            return ev(f"{args[1].replace('move: ', '')} on {_side_of(args[0]) or args[0]}", _side_of(args[0]))
        case "-sideend":
            return ev(f"{args[1].replace('move: ', '')} faded", _side_of(args[0]))
        case "-start" | "-end":
            return ev(f"{_name(args[0])}: {'' if kind == '-start' else 'no longer '}{args[1].replace('move: ', '')}",
                      _side_of(args[0]))
        case "-activate":
            return ev(f"{_name(args[0])}: {args[1].replace('move: ', '')}", _side_of(args[0]))
        case "cant":
            return ev(f"{_name(args[0])} couldn't move ({args[1]})", _side_of(args[0]))
        case "win":
            return ev(f"{args[0]} wins.", None, "win")
        case "tie":
            return ev("Tie.", None, "tie")
        case _:
            return None


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


def _turns(log: list[str]) -> list[dict[str, Any]]:
    """The log split at `|turn|N`. Everything before turn 1 is the opening (leads, abilities)."""
    turns: list[dict[str, Any]] = [{"turn": 0, "events": []}]
    for line in log:
        if line.startswith("|turn|"):
            turns.append({"turn": int(line.split("|")[2]), "events": []})
            continue
        e = _narrate(line)
        if e:
            turns[-1]["events"].append(e)
    return [t for t in turns if t["events"]]


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

    turns = _turns(rec.log)
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
