"""The page's battle and a log's give the policy the same positions (PLAN-v5 step 4).

Move advice on the page would come from the policy reading a battle built from taps
(`vgc.policy.view.EntryView`), and the policy was built and checked reading logs (`PlayerView`).
Each fixture game is turned into taps (`vgc.battle.from_log`), and at every turn mark where the
policy would answer, the two must plan the same solver positions with the same weights, from
each player's seat.
"""

from __future__ import annotations

import json

import pytest

from vgc import paths
from vgc.battle import from_log
from vgc.regulation import load_regulation, to_id

FIXTURES = sorted((paths.ROOT / "tests" / "fixtures" / "replays").glob("*.json"))


def _with_sheets():
    """The fixture games with both sheets shown. Illusion is left out: neither the page nor the
    converter follows it (`vgc.battle.from_log`)."""
    for f in FIXTURES:
        replay = json.loads(f.read_text())
        if replay["log"].count("|showteam|") != 2:
            continue
        sheets = from_log.setup(load_regulation("reg_mc"), replay["log"].split("\n"))
        if any(to_id(s["ability"] or "") == "illusion" for sid in ("p1", "p2") for s in sheets[sid]):
            continue
        yield pytest.param(replay, id=f.stem.split("-")[-1])


def _team_text(reg, lines, sid):
    """A player's six as the team library would hold them: the sheet, with a spread."""
    from vgc.policy.view import _commonest_speed
    from vgc.wp import solver

    out = []
    for s in from_log.setup(reg, lines)[sid]:
        s = {**s, "sp": solver.spread(reg, s, _commonest_speed(reg, s))}
        out.append(solver._set_text(s))
    return "\n\n".join(out)


def _comparable(plan, view):
    """A plan as the two can be held to it. Its `team` names the player's Pokémon the way the battle
    does, and a log carries the player's nicknames where the page carries the team library's, so
    those are compared by species."""
    if not plan.get("eligible"):
        return {"eligible": False, "reason": plan.get("reason")}
    nick = {m.nickname: m.species for m in view.o.sides[view.perspective].mons}
    team = [nick.get(t, t) if t is not None else None for t in plan["team"]]
    return {"eligible": True} | {k: plan[k] for k in ("kind", "replacing", "slots", "unsolved", "considered", "notes")} | {"team": team} | {
        "jobs": [{"weight": round(j["weight"], 6), "back": j["back"], "order": j["order"], "position": j["position"]}
                 for j in plan["jobs"]]}


@pytest.mark.parametrize("replay", list(_with_sheets()))
@pytest.mark.parametrize("sid", ["p1", "p2"])
def test_taps_and_the_log_plan_the_same_positions(reg, replay, sid):
    from vgc.battle.entry import Battle
    from vgc.policy import view as V

    lines = replay["log"].split("\n")
    text = _team_text(reg, lines, sid)
    setup = from_log.setup(reg, lines, sid, text)
    turns = [i for i, x in enumerate(lines) if x.startswith("|turn|")]
    checked = 0
    for at in turns:
        n = int(lines[at].split("|")[2])
        pv = V.PlayerView(reg, sid, text)
        pv.feed(lines[: at + 1])
        battle = Battle(reg, setup, from_log.journal(reg, lines, setup, upto_turn=n))
        assert not battle.rp.errors, battle.rp.errors
        ev = V.EntryView(reg, battle)
        a, b = _comparable(V.plan(reg, pv), pv), _comparable(V.plan(reg, ev), ev)
        assert a == b, f"turn {n}"
        checked += a.get("eligible", False)
    assert turns
