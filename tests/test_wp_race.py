"""The solver's damage race (`search.race`): a 1v1 valued by who wins the trade of blows, checked
against positions whose exact answer is known (PLAN-endgame-doubles, stage 1)."""

from __future__ import annotations

import json

import pytest

from vgc import paths
from vgc.wp import solver

FIXTURES = paths.ROOT / "tests" / "fixtures" / "solver"


def _race(name: str) -> float:
    pos = json.loads((FIXTURES / name).read_text())
    pos["search"] = {**pos["search"], "depth": 0, "race": True}
    return solver.solve_uncached(pos)["value"]


@pytest.mark.parametrize("name, exact", [
    # Mega Salamence's Aerilate Double-Edge is a Flying move: read as Normal, it did nothing to a
    # Ghost, and the race gave the game to Basculegion.
    ("race_aerilate.json", 0.0),
    # Primarina's Liquid Voice makes Hyper Voice a Water move, four times effective on Camerupt.
    ("race_liquid_voice.json", 0.0334),
])
def test_the_race_sees_a_move_as_the_simulator_uses_it(name, exact):
    assert abs(_race(name) - exact) < 0.1


def test_without_race_the_horizon_is_still_hp_share():
    pos = json.loads((FIXTURES / "race_aerilate.json").read_text())
    pos["search"] = {**pos["search"], "depth": 0}
    hp = pos["setup"]["hp"]
    share = hp["p1"] / (hp["p1"] + hp["p2"])
    assert abs(solver.solve_uncached(pos)["value"] - share) < 0.05
