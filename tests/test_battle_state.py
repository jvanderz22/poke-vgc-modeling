"""A battle entered by a person, with no Showdown log anywhere in sight.

This is the case the split exists for. On cartridge there is no protocol stream — you see a health
bar at roughly two thirds, an Attack arrow, rain — so the state has to be reachable by naming what
happened. The verbs are the same ones `Observer` calls, so these tests are also a check that the
manual path and the log path cannot drift apart: there is one implementation, not two.
"""

from __future__ import annotations

import pytest

from vgc.battle.state import BattleState, Mon
from vgc.belief import speed


def _battle(reg, *, mine=("Rillaboom", "Incineroar"), theirs=("Garchomp", "Kingambit")):
    """Team preview: six species a side, and nothing else known about theirs."""
    st = BattleState("p1", reg.dex)
    for sid, names in (("p1", mine), ("p2", theirs)):
        st.sides[sid].team_size = len(names)
        for n in names:
            st.sides[sid].mons.append(Mon(n))
    return st


def test_a_battle_can_be_built_without_a_single_protocol_line(reg):
    st = _battle(reg)
    st.begin_turn(1)
    st.switch_in("p1", 0, st.sides["p1"].mons[0], "Rillaboom")
    st.switch_in("p2", 0, st.sides["p2"].mons[0], "Garchomp")

    obs = st.observation()
    assert obs["turn"] == 1
    active = [m for m in obs["sides"]["p1"]["mons"] if m["state"] == "active"]
    assert [m["species"] for m in active] == ["Rillaboom"]
    # Their side is what team preview gives and nothing more.
    theirs = {m["species"]: m for m in obs["sides"]["p2"]["mons"]}
    assert theirs["Kingambit"]["state"] == "unrevealed"
    assert theirs["Garchomp"]["item"] is None and theirs["Garchomp"]["ability"] is None


def test_the_ui_addresses_pokemon_by_slot_not_by_ident_string(reg):
    """`p1a: Nickname` is protocol vocabulary. A person points at a slot."""
    st = _battle(reg)
    mon = st.sides["p1"].mons[0]
    st.switch_in("p1", 0, mon, "Rillaboom")
    assert st.at("p1", 0) is mon
    assert st.at("p1", 1) is None
    assert [m.species for m in st.bench("p1")] == ["Incineroar"]
    st.switch_in("p1", 0, st.sides["p1"].mons[1], "Incineroar")
    assert st.at("p1", 0).species == "Incineroar"
    assert [m.species for m in st.bench("p1")] == ["Rillaboom"]


def test_hp_is_entered_the_way_each_side_is_actually_seen(reg):
    """A cartridge gives you real numbers for your own Pokémon and a percentage for theirs, which
    is the same split the perspective already models — so the damage channel reads your defender
    at full precision and only the bulk channel pays the percentage."""
    st = _battle(reg)
    mine, theirs = st.sides["p1"].mons[0], st.sides["p2"].mons[0]
    st.switch_in("p1", 0, mine, "Rillaboom")
    st.switch_in("p2", 0, theirs, "Garchomp")

    st.set_hp(mine, 143, 167, None)
    st.set_hp(theirs, 64, 100, None)
    assert mine.hp == pytest.approx(143 / 167) and mine.hp_max == 167
    assert theirs.hp == pytest.approx(0.64) and theirs.hp_max is None


def test_what_you_saw_becomes_what_is_known(reg):
    """Team preview says nothing about an ability. Watching one fire does."""
    st = _battle(reg, theirs=("Incineroar", "Kingambit"))
    them = st.sides["p2"].mons[0]
    st.switch_in("p2", 0, them, "Incineroar")
    assert them.ability is None
    st.reveal(them, "ability", "intimidate")
    assert them.ability == "intimidate" and them.ability_source == "revealed"
    # A second sighting does not overwrite the first.
    st.reveal(them, "ability", "blaze")
    assert them.ability == "intimidate"


def test_a_hand_entered_turn_is_readable_by_the_speed_channel(reg):
    """The point of the whole split: a battle nobody logged still produces evidence. Entering
    that their Garchomp moved after your Rillaboom, at equal priority, bounds their Speed."""
    st = _battle(reg)
    mine, theirs = st.sides["p1"].mons[0], st.sides["p2"].mons[0]
    st.begin_turn(1)
    st.switch_in("p1", 0, mine, "Rillaboom")
    st.switch_in("p2", 0, theirs, "Garchomp")
    st.begin_turn(2)
    st.record_move(mine, "woodhammer")
    st.record_move(theirs, "earthquake")

    assert [e.species for e in st.moves_log] == ["Rillaboom", "Garchomp"]
    assert speed.pairs(reg, st.moves_log), "the two moves should be read as having raced"

    # You know your own spread; theirs is what is being inferred.
    beliefs = speed.infer(reg, st, {("p1", "Rillaboom"): 32})
    them = beliefs[("p2", "Garchomp")]
    assert not them.contradicted and them.used == 1
    # Rillaboom is base 85 and maxed, so it outran Garchomp's base 102 — which is only possible if
    # Garchomp is not far off uninvested. That is a real bound, entered by hand and nothing else.
    assert them.bounds[1] < 32
    assert them.narrowed > 0


def test_the_two_adapters_are_one_implementation(reg):
    """Not "they agree today" — there is one body of code. If a verb is ever reimplemented in the
    protocol adapter instead of delegated, this catches it."""
    import inspect

    from vgc.data.observe import Observer

    for verb in ("switch_in", "set_hp", "record_move", "apply_boost", "begin_turn", "faint",
                 "set_weather", "set_terrain", "set_side_condition"):
        assert verb not in vars(Observer), f"Observer overrides {verb} instead of calling it"
        assert inspect.isfunction(vars(BattleState)[verb])
