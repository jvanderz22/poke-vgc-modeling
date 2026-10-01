"""Endgames with at most two Pokémon a side as solver positions (`vgc.wp.doubles`), read from
held-out replays as the human check will read them."""

from __future__ import annotations

import json

from vgc import paths
from vgc.wp import doubles, solver


def _replay(name):
    return json.loads((paths.ROOT / "tests" / "fixtures" / "replays" / f"{name}.json").read_text())


TWO_V_TWO = "gen9championsvgc2026regmcbo3-2683090653"
ONE_V_TWO = "gen9championsvgc2026regmcbo3-2684171856"


def test_a_replay_stops_where_neither_side_has_more_than_two_left(reg):
    battle = doubles.from_replay(reg, _replay(TWO_V_TWO))
    state = battle.rp.state
    assert doubles.reason(reg, state) is None
    assert [len(doubles.actives(state, sid)) for sid in ("p1", "p2")] == [2, 2]
    # Slot order: the first Pokémon in the position is the one in the left slot.
    assert all(a.position < b.position for a, b in [doubles.actives(state, sid) for sid in ("p1", "p2")])


def test_a_2v2_plans_one_position_a_move_order_and_writes_the_same_ones_again(reg):
    battle = doubles.from_replay(reg, _replay(TWO_V_TWO))
    got = doubles.plan(reg, battle)
    assert got["eligible"] and got["kind"] == "2v2"
    assert len(got["jobs"]) <= doubles.MAX_ORDERS
    assert abs(sum(j["weight"] for j in got["jobs"]) + got["unsolved"] - 1) < 1e-4
    assert len({j["order"] for j in got["jobs"]}) == len(got["jobs"])
    for j in got["jobs"]:
        pos = j["position"]
        assert pos["active"] == {"p1": 2, "p2": 2}
        assert all(len(pos["setup"]["hp"][sid]) == 2 for sid in ("p1", "p2"))
        # Two Pokémon a side, then fainted fillers to four.
        assert all(pos[sid].count("\n\n") == 3 for sid in ("p1", "p2"))
    again = doubles.plan(reg, doubles.from_replay(reg, _replay(TWO_V_TWO)))
    assert [solver.position_key(j["position"]) for j in again["jobs"]] == \
        [solver.position_key(j["position"]) for j in got["jobs"]]


def test_the_solver_takes_every_position_planned(reg):
    """Each move order's position, valued by the race alone (depth 0), so the test stays quick:
    the simulator accepts the teams, the slots and the state."""
    for name in (TWO_V_TWO, ONE_V_TWO):
        got = doubles.plan(reg, doubles.from_replay(reg, _replay(name)), {"depth": 0})
        assert got["eligible"]
        for j in got["jobs"]:
            r = solver.solve_uncached(j["position"])
            assert 0 <= r["value"] <= 1


def test_a_1v2_searches_a_turn(reg):
    got = doubles.plan(reg, doubles.from_replay(reg, _replay(ONE_V_TWO)))
    assert got["kind"] in ("1v2", "2v1")
    r = solver.solve_uncached(got["jobs"][0]["position"])
    assert 0 <= r["value"] <= 1 and r["moves"]["p1"] and r["moves"]["p2"]


def test_the_protect_counter_counts_consecutive_turns_ending_with_the_last():
    journal = [{"kind": "turn", "n": 1}, {"kind": "move", "side": "p1", "species": "A", "move": "Protect"},
               {"kind": "turn", "n": 2}, {"kind": "move", "side": "p1", "species": "A", "move": "Protect"},
               {"kind": "move", "side": "p2", "species": "B", "move": "Protect"},
               {"kind": "turn", "n": 3}, {"kind": "move", "side": "p2", "species": "B", "move": "Tackle"},
               {"kind": "turn", "n": 4}]
    assert doubles.stall_counts(journal, 4, doubles.STALLING) == {}
    assert doubles.stall_counts(journal, 3, doubles.STALLING) == {("p1", "A"): 2, ("p2", "B"): 1}


def test_a_closed_sheet_is_not_built_yet(reg):
    closed = doubles.from_replay(reg, _replay("gen9championsvgc2026regmc-2678724403"))
    assert "closed sheet" in doubles.reason(reg, closed.rp.state)
