"""The engine's answer to a live 1v1 (`vgc.web.solving`): it starts on the first request, deepens
in the background, and is dropped as soon as the battle moves on."""

from __future__ import annotations

import time

import pytest

from vgc.battle.entry import Battle
from vgc.web import solving
from vgc.wp import benchmark


def _battle(reg, fid, vid, upto=None):
    setup, journal = benchmark.build(reg, benchmark.load(reg), fid, vid)
    return Battle(reg, setup, journal[:upto] if upto else journal)


def test_a_position_that_is_not_a_1v1_starts_nothing(reg):
    """Above two a side after a faint, nothing starts until the player's four are known, and the
    page says that is what it is waiting for (the policy's value needs the back)."""
    got = solving.request(reg, "early", _battle(reg, "F2", "B", upto=8))
    assert got == {"eligible": False, "reason": "mark the four you brought to get the policy's value"}


@pytest.mark.showdown
def test_it_deepens_until_every_line_ends(reg):
    """F2-B: Annihilape is locked into Close Combat and cannot touch Gholdengo, so best play wins.
    Every line has ended in a KO by three turns, and the search stops there."""
    battle = _battle(reg, "F2", "B")
    first = solving.request(reg, "f2b", battle)
    assert first["eligible"] and first["max_depth"] == solving.DEPTHS[-1]
    deadline = time.time() + 240
    got = first
    while got["searching"] is not None and time.time() < deadline:
        time.sleep(0.5)
        got = solving.request(reg, "f2b", battle)
    assert got["error"] is None and got["searching"] is None
    assert got["value"] == 1 and got["leaf_mass"] == 0 and got["depth"] <= 3


@pytest.mark.showdown
def test_a_new_position_cancels_the_old_one(reg):
    old = _battle(reg, "F6", "A")                 # hours at four turns deep
    solving.request(reg, "f6a", old)
    running = solving._current
    solving.request(reg, "f2a", _battle(reg, "F2", "A"))
    assert running.cancelled and solving._current is not running
    solving._current.cancel()


@pytest.mark.showdown
def test_cancelling_kills_the_search(reg):
    """A deleted battle's search, or all of them at shutdown: node processes do not end with the
    server that started them."""
    solving.request(reg, "f6a", _battle(reg, "F6", "A"))
    running = solving._current
    time.sleep(1)
    solving.cancel("someone-else")
    assert solving._current is running
    solving.cancel("f6a")
    assert running.cancelled and solving._current is None
    deadline = time.time() + 10
    while running._procs and time.time() < deadline:
        time.sleep(0.2)
    assert not running._procs


@pytest.mark.showdown
def test_a_doubles_endgame_is_answered_within_the_deadline(reg):
    """A held-out 2v2 from the stands, open sheets: a quick answer, then the searched one, and both
    inside `DEADLINE` (PLAN-endgame-doubles, stage 4)."""
    import json

    from vgc import paths
    from vgc.wp import doubles

    replay = json.loads((paths.ROOT / "tests" / "fixtures" / "replays" /
                         "gen9championsvgc2026regmcbo3-2683090653.json").read_text())
    battle = doubles.from_replay(reg, replay)
    solving.warm()
    time.sleep(1.5)                     # the processes start with the app, not with the question
    try:
        got = solving.request(reg, "d2v2", battle)
        assert got["eligible"] and got["kind"] == "2v2" and got["max_depth"] == 1
        while got["searching"] is not None:
            time.sleep(0.2)
            got = solving.request(reg, "d2v2", battle)
        assert got["elapsed"] <= solving.DEADLINE + 1.0
        assert got["depth"] in (0, 1) and 0 <= got["value"] <= 1
        assert 1 <= len(got["positions"]) <= solving.TOP_ORDERS

        # Then the answer again under the two other guesses at their bulk, on a budget of their own;
        # the answer itself is the first of the three and does not move.
        answer = got["value"]
        while got["guesses"]["pending"]:
            time.sleep(0.2)
            got = solving.request(reg, "d2v2", battle)
        guesses = got["guesses"]["values"]
        assert [g["key"] for g in guesses][0] == "assumed" and guesses[0]["value"] == answer == got["value"]
        assert {g["key"] for g in guesses} <= {k for k, _ in solving.GUESSES}
        assert all(0 <= g["value"] <= 1 for g in guesses)
        assert got["elapsed"] <= 2 * solving.DEADLINE + 2.0
    finally:
        solving.cancel()


def test_a_refill_keeps_the_speed_and_the_budget(reg):
    """The two other guesses at a hidden spread: Speed as it was, the rest refilled in order to the
    per-stat cap, and a set with no spread (a fainted filler) left alone."""
    from vgc.wp import solver

    text = ("Incineroar @ Sitrus Berry\nAbility: Intimidate\nEVs: 24 HP / 32 Atk / 10 Spe\n- Fake Out\n"
            "- Flare Blitz\n\nSinistcha\nAbility: Hospitality\n- Protect")
    hp = solver.refill(reg, text, "hp_first")
    assert hp.split("\n")[2] == "EVs: 32 HP / 24 Atk / 10 Spe"
    assert solver.refill(reg, text, "defences").split("\n")[2] == "EVs: 32 HP / 24 Def / 10 Spe"
    assert hp.split("\n\n")[1] == text.split("\n\n")[1]


@pytest.mark.showdown
def test_above_two_a_side_the_model_and_the_policy_combine(reg):
    """A fixture game tapped from p1's seat, with p1's four marked: before a faint the page waits,
    and after the first faint (a 3v3) it answers with the model and the policy's value combined,
    within `DEADLINE`, the model still underneath."""
    import json
    import math
    import sys

    from vgc import paths
    from vgc.battle import from_log
    from vgc.battle.entry import Battle

    sys.path.insert(0, str(paths.ROOT / "tests"))
    import test_policy_entry as T

    replay = json.loads((paths.ROOT / "tests" / "fixtures" / "replays" /
                         "gen9championsvgc2026regmcbo3-2682837890.json").read_text())
    lines = replay["log"].split("\n")
    text = T._team_text(reg, lines, "p1")
    setup = from_log.setup(reg, lines, "p1", text)
    full = from_log.journal(reg, lines, setup)
    four = ["Annihilape", "Metagross", "Salamence", "Sylveon"]     # everyone p1 sent out
    turn = lambda n: next(i for i, e in enumerate(full) if e.get("kind") == "turn" and e["n"] == n)

    early = Battle(reg, setup, full[:turn(1) + 1] + [{"kind": "bring", "side": "p1", "species": four}])
    assert solving.reason(reg, early)[1] is not solving.PolicySolve           # 4v4: the model alone
    unmarked = Battle(reg, setup, full[:turn(2) + 1])
    assert solving.reason(reg, unmarked)[0].startswith("mark the four")

    battle = Battle(reg, setup, full[:turn(2) + 1] + [{"kind": "bring", "side": "p1", "species": four}])
    assert not battle.rp.errors
    assert solving.reason(reg, battle) == (None, solving.PolicySolve)
    solving.warm()
    time.sleep(1.5)
    try:
        got = solving.request(reg, "pv", battle)
        assert got["eligible"] and got["mode"] == "policy" and got["kind"] == "3v3"
        while got["searching"] is not None:
            time.sleep(0.2)
            got = solving.request(reg, "pv", battle)
        assert got["error"] is None and got["elapsed"] <= solving.DEADLINE + 1.0
        s = solving.POLICY_STACK
        logit = lambda p: math.log(p / (1 - p))
        z = s["intercept"] + s["wp"] * logit(got["model"]) + s["value"] * logit(got["policy"])
        assert abs(got["value"] - 1 / (1 + math.exp(-z))) < 2e-3
    finally:
        solving.cancel()
