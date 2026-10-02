"""The EWP policy (`vgc.policy.ewp`, PLAN-policy stage 4): the guesses combined as one game, the
solver's choices written as the battle's, and a battle that is a function of its seed."""

from __future__ import annotations

import json

import numpy as np
import pytest

from vgc import paths
from vgc.engine.runner import BattleRunner, battle_seed, play_battle
from vgc.policy import ewp as E
from vgc.policy.heuristic import HeuristicPolicy

TEAMS = json.loads((paths.ROOT / "tests" / "fixtures" / "teams" / "policy_view_teams.json").read_text())


def test_matching_pennies_is_a_coin_flip():
    out = E.combine([(1.0, np.array([[1.0, 0.0], [0.0, 1.0]]), None, None)], "nash", 4)
    assert np.allclose(out["strategy"], [0.5, 0.5], atol=1e-6)
    assert abs(out["value"] - 0.5) < 1e-6


@pytest.mark.parametrize("seed", range(5))
def test_the_guesses_are_one_game_where_the_opponent_knows_which_is_true(seed):
    rng = np.random.default_rng(seed)
    Ms = [rng.random((2, 3)), rng.random((2, 4))]
    w = [0.7, 0.3]
    out = E.combine([(wj, M, None, None) for wj, M in zip(w, Ms)], "nash", 4)
    # Two rows: the best mix by brute force, each guess's opponent answering it on its own.
    grid = np.linspace(0, 1, 20001)
    best = max(sum(wj * min(p * M[0] + (1 - p) * M[1]) for wj, M in zip(w, Ms)) for p in grid)
    assert abs(out["value"] - best) < 1e-3
    x = out["strategy"]
    got = sum(wj * min(x[0] * M[0] + x[1] * M[1]) for wj, M in zip(w, Ms))
    assert abs(got - best) < 1e-3


def test_people_play_the_best_row_against_their_softmax():
    M1, M2 = np.array([[0.9, 0.1], [0.5, 0.5]]), np.array([[0.2, 0.8], [0.6, 0.4]])
    s1, s2 = [2.0, 0.0], [0.0, 0.0]
    out = E.combine([(0.5, M1, s1, None), (0.5, M2, s2, None)], "people", 4)
    p1 = np.exp(s1) / np.exp(s1).sum()
    want = 0.5 * M1 @ p1 + 0.5 * M2 @ np.array([0.5, 0.5])
    assert np.allclose(out["ewp"], want)
    assert int(np.argmax(out["strategy"])) == int(np.argmax(want))


def test_a_choice_is_written_in_the_battles_slots_team_and_move_order():
    # p2 has one Pokémon left, in its second slot, and so has p1: the solver has each in its first.
    plan = {"slots": {"p1": [1, 0], "p2": [1, 0]}, "team": ["Al", "Bo"],
            "moves": [["protect", "flamethrower"], ["tackle"]]}
    req = {"side": {"id": "p2", "pokemon": [{"ident": "p2: Cy"}, {"ident": "p2: Al"}, {"ident": "p2: Bo"}]},
           "active": [{"moves": []}, {"moves": [{"id": "flamethrower"}, {"id": "protect"}]}]}
    assert E.to_battle("move 2 1 mega, pass", plan, req) == "pass, move 1 2 mega"
    assert E.to_battle("move 1, pass", plan, req) == "pass, move 2"
    assert E.to_battle("move 2 -1, pass", plan, req) == "pass, move 1 -2"
    # Locked into a charging move: the battle wants it without a target, whatever the solver chose.
    locked = {**req, "active": [{"moves": []}, {"moves": [{"move": "Electro Shot", "id": "electroshot"}]}]}
    assert E.to_battle("move 2 1, pass", plan, locked) == "pass, move 1"
    # A replacement: both slots the battle's, the one coming in found by its nickname.
    rep = {"slots": {"p1": [0, 1], "p2": [0, 1]}, "team": [None, "Al", "Bo"], "moves": [None, [], []]}
    req = {"side": {"id": "p2", "pokemon": [{"ident": "p2: Cy"}, {"ident": "p2: Al"}, {"ident": "p2: Bo"}]},
           "forceSwitch": [True, False]}
    assert E.to_battle("switch 3, pass", rep, req) == "switch 3, pass"


@pytest.fixture(scope="module")
def two_runs(reg):
    out = []
    for _ in range(2):
        pol = E.EWPPolicy(reg, "nash")
        with BattleRunner() as r:
            rec = play_battle(r, "ewp", battle_seed(7, 0), reg.showdown_format, (TEAMS["p1"], TEAMS["p2"]),
                              (pol, HeuristicPolicy(reg)), ots=True)
        pol.close()
        out.append((rec, pol.decisions))
    return out


def test_a_battle_is_a_function_of_its_seed(two_runs):
    (a, da), (b, db) = two_runs
    assert a.input_log == b.input_log
    assert [d.get("choice") for d in da] == [d.get("choice") for d in db]


def test_every_choice_is_legal_and_replacements_are_searched(two_runs):
    rec, decisions = two_runs[0]
    assert rec.invalid_choices == 0
    assert any(d.get("replacing") and not d.get("fallback") for d in decisions)
    for d in decisions:
        if not d.get("fallback"):
            assert len(d["ewp"]) == len(d["rows"]) and abs(sum(d["strategy"]) - 1) < 1e-6


def test_the_held_out_opponent_plays_legal_choices_of_its_own(reg):
    sc = E.ScorerPolicy(reg)
    with BattleRunner() as r:
        rec = play_battle(r, "sc", battle_seed(9, 0), reg.showdown_format, (TEAMS["p1"], TEAMS["p2"]),
                          (sc, HeuristicPolicy(reg)), ots=True)
    sc.close()
    assert rec.invalid_choices == 0
    assert sc.decisions and all(not d.get("fallback") for d in sc.decisions)
    assert any(d.get("replacing") for d in sc.decisions)
