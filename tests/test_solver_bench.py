"""The solver with a bench (PLAN-policy, stage 1): Pokémon in the back, switches, Mega Evolution
and replacements, all behind search settings that are off for every endgame position."""

from __future__ import annotations

import json

import pytest

from vgc import paths
from vgc.wp import solver

TEAMS = json.loads((paths.ROOT / "tests" / "fixtures" / "solver" / "bench_teams.json").read_text())
FLAGS = {"switches": True, "mega": True}
# The policy's search, at its smallest: one turn, two choices a Pokémon, four a side, four draws.
SEARCH = {"depth": 1, "prune": 2, "side_k": 4, "sample": 4, "sample_only": True, "ko_extend": False,
          "race": True, "race_doubles": "blend_boosts", "fast_race": True, "fast_dice": True, **FLAGS}


def _position(left: tuple[int, int], **extra):
    hp = {sid: [100, 70, 100, 45][:n] for sid, n in zip(("p1", "p2"), left)}
    return {**TEAMS, "active": {"p1": 2, "p2": 2}, "bench": {"p1": left[0] - 2, "p2": left[1] - 2},
            "setup": {"hp": hp, "fresh": {"p1": [True, True], "p2": [True, True]}}, **extra}


@pytest.mark.parametrize("left", [(4, 4), (3, 3), (4, 2)])
def test_every_listed_choice_is_one_the_simulator_accepts(left):
    got = solver.solve_uncached(_position(left, search=FLAGS, walk={"games": 12, "turns": 30, "seed": 1}))
    assert got["rejected"] == []
    assert got["ended"] >= 10
    assert got["switches"] > 0 and got["replacements"] > 0
    # p1's Mega (Floette) is its fourth Pokémon, fainted when it has three left.
    assert (got["megas"] > 0) == (left[0] == 4)


def test_a_search_from_a_full_team_offers_switches_and_answers_the_same_twice():
    pos = _position((4, 4), search=SEARCH)
    a, b = solver.solve_uncached(pos), solver.solve_uncached(pos)
    assert 0 <= a["value"] <= 1 and a["value"] == b["value"] and a["matrix"] == b["matrix"]
    assert len(a["moves"]["p1"]) <= SEARCH["side_k"] and len(a["moves"]["p2"]) <= SEARCH["side_k"]


def test_without_the_flags_a_full_team_is_offered_moves_only():
    got = solver.solve_uncached(_position((4, 4), search={**SEARCH, "switches": False, "mega": False}))
    assert not any("switch" in c or "mega" in c for side in got["moves"].values() for c in side)


def test_a_pokemon_in_the_back_needs_two_on_the_field():
    pos = _position((3, 3), search=SEARCH)
    pos["active"] = {"p1": 1, "p2": 2}
    with pytest.raises(RuntimeError):  # "p1 has a Pokémon in the back but not two on the field"
        solver.solve_uncached(pos)
