"""A live 1v1, as the solver's positions (`vgc.wp.endgame`): the adapter has to write the position
the benchmark wrote for every solved variant, from nothing but the variant's hand-entry journal.
That is "the same answer twice" — positions whose answers are already known, reached the way the
app reaches a battle."""

from __future__ import annotations

import json
import subprocess

import pytest

from vgc import paths
from vgc.battle.entry import Battle
from vgc.wp import benchmark, endgame, solver


@pytest.fixture(scope="module")
def spec(reg):
    return benchmark.load(reg)


@pytest.fixture(scope="module")
def rows(reg):
    return endgame.check(reg)


def _battle(reg, spec, fid, vid, upto=None):
    setup, journal = benchmark.build(reg, spec, fid, vid)
    return Battle(reg, setup, journal[:upto] if upto else journal)


def _truth_set(spec, fid, vid):
    fam, var = benchmark._variant(spec, fid, vid)
    t = {**fam["build"]["theirs"], **(var.get("theirs") or {})}
    return {k: t.get(k) for k in ("species", "item", "ability", "nature", "moves")}


def test_every_solved_variant_is_the_same_position_through_a_battle(rows):
    assert len(rows) >= 30
    wrong = [r["variant"] for r in rows if not (r["positions_match"] and r["weights_match"])]
    assert not wrong, wrong


def test_the_same_positions_give_the_stored_answers(rows):
    """Where the solves are cached (`.vgc/`, not in git). Some cached values were rebuilt from a run
    log at three decimals, hence the half-thousandth beside `leaf_mass`."""
    have = [r for r in rows if r["value"] is not None]
    if len(have) < len(rows):
        pytest.skip("the benchmark's solves are not cached here (vgc wp solve)")
    for r in have:
        assert abs(r["value"] - r["solved"]) <= r["leaf_mass"] + 5e-4, r


def test_a_position_that_is_not_a_1v1_says_why(reg, spec):
    early = endgame.plan(reg, _battle(reg, spec, "F1", "A", upto=8), solver.SEARCH)
    assert early == {"eligible": False, "reason": "each side needs exactly one Pokémon left"}


def test_hp_is_written_the_way_the_benchmark_writes_it(reg, spec):
    """F2's Gholdengo is at 30%: exact HP in the journal, and a whole 30 in the position rather
    than the fraction that HP comes back as."""
    battle = _battle(reg, spec, "F2", "A")
    f = endgame.facts(reg, battle, {"p1": {}, "p2": _truth_set(spec, "F2", "A")})
    assert f["hp"] == {"p1": 30, "p2": 100}


def test_just_in_is_fresh_and_a_choice_holder_that_moved_is_locked(reg, spec):
    sets = {"p1": {}, "p2": _truth_set(spec, "F2", "A")}
    locked = endgame.facts(reg, _battle(reg, spec, "F2", "A"), sets)
    fresh = endgame.facts(reg, _battle(reg, spec, "F2", "C"), sets)
    assert locked["choicelock"] == {"p2": "ragefist"} and not locked["fresh"]
    assert fresh["fresh"] == {"p2": True} and not fresh["choicelock"]


def test_a_closed_sheet_weighs_its_sets_by_the_turn_order(reg, spec):
    """F1-D: their Basculegion outran a 149-Speed Gholdengo, which no set without a Choice Scarf can
    do. With no evidence (C) the likeliest set is Life Orb."""
    blind = endgame.plan(reg, _battle(reg, spec, "F1", "C"), solver.SEARCH)
    seen = endgame.plan(reg, _battle(reg, spec, "F1", "D"), solver.SEARCH)
    assert blind["sets"][0]["set"]["item"] == "Life Orb"
    assert {s["set"]["item"] for s in seen["sets"]} == {"Choice Scarf"}
    assert 0 < seen["unsolved"] < 1 and abs(sum(j["weight"] for j in seen["jobs"]) + seen["unsolved"] - 1) < 1e-3


def test_combine_waits_for_every_position(reg):
    jobs = [{"weight": 0.25}, {"weight": 0.5}]
    assert endgame.combine(jobs, [{"value": 1, "leaf_mass": 0}, None]) is None
    got = endgame.combine(jobs, [{"value": 1, "leaf_mass": 0}, {"value": 0.25, "leaf_mass": 0.3}])
    assert got == pytest.approx({"value": 0.5, "leaf_mass": 0.2})


@pytest.mark.showdown
def test_position_speed_ties_where_the_engine_ties(reg, spec):
    """A Scarf on 101 Speed is 151 in the engine, a tie with 151 rather than a win; and paralysis
    and Tailwind, which the solver now sets up, land where `position_speed` says they do."""
    theirs = {**_truth_set(spec, "F1", "B"), "hp": 60}
    ours = spec["families"][0]["build"]["ours"]
    sp = solver.spread(reg, theirs, 3)
    pos = solver.compose(reg, ours, {**theirs, "sp": sp}, solver.facts_of(spec, "F1", "B", theirs),
                         {"depth": 1})
    for extra in ({}, {"status": {"p2": "par"}, "sides": {"p1": {"tailwind": 3}}}):
        setup = {**pos["setup"], **extra}
        r = subprocess.run(["node", str(solver.SOLVER), str(paths.SHOWDOWN)], capture_output=True, text=True,
                           input=json.dumps({**pos, "setup": setup, "debug": {"p1": "move 4", "seed": 1}}),
                           check=True)
        out = json.loads(r.stdout)
        spe = {sid: side[0]["spe"] for sid, side in zip(("p1", "p2"), out["active"])}
        assert spe == {"p1": solver.position_speed(reg, ours, ours["sp"], setup, "p1"),
                       "p2": solver.position_speed(reg, theirs, sp, setup, "p2")}, (extra, out)
        if extra:
            assert out["active"][1][0]["status"] == "par" and "tailwind" in out["sides"][0]
