"""Positions from a player's view (`vgc.policy.view`, PLAN-policy stage 2): built from what the
player can see and nothing else."""

from __future__ import annotations

import json

import pytest

from vgc import paths
from vgc.engine.runner import BattleRunner, battle_seed, play_battle
from vgc.policy import view as V
from vgc.policy.heuristic import HeuristicPolicy

TEAMS = json.loads((paths.ROOT / "tests" / "fixtures" / "teams" / "policy_view_teams.json").read_text())


@pytest.fixture(scope="module")
def played(reg):
    with BattleRunner() as r:
        pol = HeuristicPolicy(reg)
        rec = play_battle(r, "pv", battle_seed(1, 0), reg.showdown_format, (TEAMS["p1"], TEAMS["p2"]), (pol, pol), ots=True)
    return rec.input_log


def _trace(input_log):
    with BattleRunner() as r:
        return r.request({"op": "trace", "id": "pv", "inputLog": input_log, "ots": True, "partial": True})["steps"]


def _plans(reg, steps, sid, text):
    """(what the player has been shown so far, its plan) at each of its decisions."""
    v = V.PlayerView(reg, sid, text)
    shown: list[str] = []
    out = []
    for step in steps:
        v.feed(step[sid])
        shown += [x for x in step[sid] if not x.startswith("|t:|")]      # the clock, not the battle
        if sid not in step["requests"]:
            continue
        req = json.loads(step["requests"][sid])
        v.request(req)
        shown.append(step["requests"][sid])
        # Moves and replacements both (a replacement is a decision like any other, stage 4).
        if (req.get("active") or req.get("forceSwitch")) and not req.get("wait"):
            out.append((list(shown), V.plan(reg, v)))
    return out


def _with_p2(input_log, team=None, back=None):
    """The same inputs with p2's packed team changed by `team`, or its two in the back swapped for
    the two it left at home."""
    out = []
    for line in input_log:
        if team and line.startswith(">player p2 "):
            opts = json.loads(line[len(">player p2 "):])
            opts["team"] = team(opts["team"])
            line = ">player p2 " + json.dumps(opts)
        if back and line.startswith(">p2 team "):
            picks = [int(x) for x in line[len(">p2 team "):].replace(" ", "").split(",")]
            home = [i for i in range(1, 7) if i not in picks]
            line = ">p2 team " + ", ".join(str(x) for x in picks[:2] + home)
        out.append(line)
    return out


def _evs_of_back(input_log):
    """p2's packed team with the two it brought last given other Stat Points (all in HP)."""
    picks = next([int(x) - 1 for x in l[len(">p2 team "):].replace(" ", "").split(",")]
                 for l in input_log if l.startswith(">p2 team "))

    def change(packed: str) -> str:
        mons = packed.split("]")
        for i in picks[2:]:
            f = mons[i].split("|")
            f[6] = "32,0,0,0,0,0"           # the packed EV field: all of it in HP
            mons[i] = "|".join(f)
        return "]".join(mons)
    return change


@pytest.mark.parametrize("variant", ["spreads", "back"])
def test_what_the_player_was_not_shown_does_not_change_its_positions(reg, played, variant):
    other = _with_p2(played, team=_evs_of_back(played)) if variant == "spreads" else _with_p2(played, back=True)
    a = _plans(reg, _trace(played), "p1", TEAMS["p1"])
    b = _plans(reg, _trace(other), "p1", TEAMS["p1"])
    compared = 0
    for (shown_a, plan_a), (shown_b, plan_b) in zip(a, b):
        if shown_a != shown_b:
            break
        assert plan_a == plan_b
        compared += 1
    assert compared >= 1


def test_a_decision_plans_the_same_positions_twice_and_they_weigh_one(reg, played):
    steps = _trace(played)
    a, b = _plans(reg, steps, "p2", TEAMS["p2"]), _plans(reg, steps, "p2", TEAMS["p2"])
    assert [p for _, p in a] == [p for _, p in b]
    first = a[0][1]
    assert first["eligible"] and len(first["jobs"]) == V.POSITIONS
    assert abs(sum(j["weight"] for j in first["jobs"]) - 1) < 1e-9
    pos = first["jobs"][0]["position"]
    assert pos["active"] == {"p1": 2, "p2": 2} and pos["bench"] == {"p1": 2, "p2": 2}


def test_the_back_is_weighed_over_the_unseen_sheets(reg, played):
    first = _plans(reg, _trace(played), "p1", TEAMS["p1"])[0][1]
    # Turn 1: four of p2's six unseen, two of them in the back: six ways.
    assert first["considered"] % 6 == 0
    assert all(len(j["back"]) == 2 for j in first["jobs"])
