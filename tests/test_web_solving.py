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
    got = solving.request(reg, "early", _battle(reg, "F2", "B", upto=8))
    assert got == {"eligible": False, "reason": "the engine answers once neither side has more than two Pokémon left"}


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
    finally:
        solving.cancel()
