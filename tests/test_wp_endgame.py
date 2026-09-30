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
    assert blind["sets"]["p2"][0]["set"]["item"] == "Life Orb"
    assert {s["set"]["item"] for s in seen["sets"]["p2"]} == {"Choice Scarf"}
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


# --- observer mode: both spreads hidden (PLAN-v3 step 4) ------------------------------------------

def spectate(setup, journal):
    """A benchmark battle as someone watching it would enter it: both sheets, neither spread, and
    HP as a percentage on both sides."""
    sheet = lambda m: {k: m.get(k) for k in ("species", "item", "ability", "moves", "nature")}  # noqa: E731
    maxhp = {m["species"]: m["stats"]["hp"] for m in setup["mine"]}
    slots, out = {}, []
    for e in journal:
        if e.get("kind") in ("lead", "switch") and e["side"] == "p1":
            slots[e["slot"]] = e["species"]
        if e.get("kind") == "damage" and e["side"] == "p1" and e.get("hp") is not None:
            e = {k: v for k, v in e.items() if k != "hp"} | {"pct": round(100 * e["hp"] / maxhp[slots[e["slot"]]])}
        out.append(e)
    return {"perspective": "spectator", "p1": [sheet(m) for m in setup["mine"]],
            "p2": [sheet(m) for m in setup["theirs"]]}, out


def _watched(reg, spec, fid, vid):
    return Battle(reg, *spectate(*benchmark.build(reg, spec, fid, vid)))


def test_watching_integrates_both_speeds(reg, spec):
    """F1-B from the stands: the Mega Charizard's Speed is as hidden as the Basculegion's, so both
    are averaged, and a Scarf Basculegion is faster for most pairs."""
    battle = _watched(reg, spec, "F1", "B")
    assert not battle.rp.errors and battle.rp.state.perspective == "spectator"
    got = endgame.plan(reg, battle, solver.SEARCH)
    weights = {j["class"]: j["weight"] for j in got["jobs"]}
    assert abs(sum(weights.values()) - 1) < 1e-9 and weights["faster"] > 0.9
    assert {j["position"]["setup"]["hp"]["p1"] for j in got["jobs"]} == {100}
    assert len({j["position"]["p1"] for j in got["jobs"]}) > 1          # our spread varies too


def test_an_ordering_between_the_two_rules_out_pairs(reg, spec):
    """F8b-B: their Basculegion moved before our Gholdengo. With both hidden, no pair of Speed
    investments in which the Gholdengo is faster keeps any weight."""
    from vgc.belief import speed

    battle = _watched(reg, spec, "F8b", "B")
    state = battle.rp.state
    sets = {sid: endgame.candidates(reg, battle, sid, [])[0][0]["set"] for sid in ("p1", "p2")}
    js = endgame.speed_joint(reg, state, sets)
    assert js.used and not js.contradicted
    stat = lambda sid, sp: speed.speed_stat(reg, sets[sid]["species"], sets[sid]["nature"], sp)  # noqa: E731
    for i, a in enumerate(js.values[0]):
        for j, b in enumerate(js.values[1]):
            if js.mass[i][j] > 0:
                assert stat("p2", b) >= stat("p1", a)


# --- a replay, from the stands (PLAN-v3 step 3.6) --------------------------------------------------

def _replay(name):
    return json.loads((paths.ROOT / "tests" / "fixtures" / "replays" / f"{name}.json").read_text())


def test_a_replay_stops_at_its_first_1v1_and_plans_from_both_sheets(reg):
    """A held-out open-sheet game, fed from its log: stopped at the turn mark where each side first
    has one Pokémon left, with both sheets as the log showed them and both spreads integrated."""
    battle = endgame.from_replay(reg, _replay("gen9championsvgc2026regmcbo3-2682837890"))
    state = battle.rp.state
    assert state.perspective == "spectator" and endgame.reason(reg, state) is None
    assert all(len(endgame._left(state, sid)) == 1 for sid in ("p1", "p2"))
    assert all(len(battle.setup[sid]) == len(state.sides[sid].mons) for sid in ("p1", "p2"))
    got = endgame.plan(reg, battle, {**solver.SEARCH, "depth": 2})
    assert got["eligible"] and len(got["jobs"]) >= 2
    assert abs(sum(j["weight"] for j in got["jobs"]) - 1) < 1e-9 and got["unsolved"] == 0


def test_a_choice_lock_holds_from_the_first_turn_and_survives_a_copy(reg):
    """Annihilape locked into Phantom Force against a Raichu with Encore: the solver crashed once
    the lock's last move had been copied into a child battle, and at the root it offered the locked
    side all four moves. Mega Raichu Y's No Guard lands Zap Cannon through Phantom Force, so it wins."""
    battle = endgame.from_replay(reg, _replay("gen9championsvgc2026regmcbo3-2683090653"))
    jobs = endgame.plan(reg, battle, {**solver.SEARCH, "depth": 2})["jobs"]
    assert jobs and all(j["position"]["setup"]["choicelock"] == {"p2": "phantomforce"} for j in jobs)
    for j in jobs:
        r = solver.run(j["position"])
        assert r["moves"]["p2"] == ["move 2 1, pass"]
        assert r["value"] == 1


def test_every_option_is_one_the_simulator_accepts(reg):
    """Indeedee at 1% with Helping Hand, Rillaboom with Fake Out, a Speed tie in Psychic Terrain.
    Helping Hand was offered without a target, which the simulator rejects, and the search scored
    that unplayed turn as a certain win; Fake Out, disabled after the first turn in Champions, was
    offered at the root. The solver now throws on a rejected choice rather than scoring it."""
    battle = endgame.from_replay(reg, _replay("gen9championsvgc2026regmcbo3-2682994613"))
    jobs = {j["class"]: j for j in endgame.plan(reg, battle, {**solver.SEARCH, "depth": 2})["jobs"]}
    r = solver.run(jobs["tied"]["position"])
    assert "move 3 -2, pass" in r["moves"]["p1"]
    assert not any(m.startswith("move 1 ") for m in r["moves"]["p2"])
    assert r["value"] == 0.5


def test_a_replay_that_never_reaches_a_1v1_gives_none(reg):
    assert endgame.from_replay(reg, _replay("gen9championsvgc2026regmcbo3-2684057197")) is None


def test_the_model_from_the_stands_is_player_1s_chance(reg):
    """A spectator record featurizes as two rows, one per seat. The live number pairs them the way
    `vgc wp eval` scores spectator rows (`models.symmetrize`); read as two draws, every position
    from the stands averaged to about a half."""
    from vgc.data.snapshots import evidence
    from vgc.web.live import wp
    from vgc.wp.models import OPEN, in_battle_version, predict_records
    from vgc.wp.tools import _load, _record

    state = endgame.from_replay(reg, _replay("gen9championsvgc2026regmcbo3-2682837890")).rp.state
    version = in_battle_version(reg.id, OPEN)
    model, fz = _load(reg, version)
    want = predict_records(model, [_record(state.observation(), "turn", "human", evidence=evidence(reg, state))], fz)[0]
    got = wp(reg, state, version, k=4)
    # Both sheets are open, so every draw is the true position.
    assert got["wp"] == pytest.approx(want) and got["lo"] == pytest.approx(got["hi"])
    assert got["wp"] < 0.35       # Metagross at 41% against a full-health Arcanine-Hisui
