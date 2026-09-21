"""Forward derivation for the adapter whose input does not report consequences.

Two kinds of test. The first re-derives every ability table from the pinned Showdown build, because
the dex export has now been caught missing `multihit`, `overrideDefensiveStat`,
`overrideOffensiveStat` and `onModifyType` — a hand-written list of abilities is the same trap with
a longer fuse. The second checks the thing the tables are *for*: that an outcome carries what it
would imply, so picking it is both applying an effect and learning an ability.
"""

from __future__ import annotations

import re

import pytest

from vgc import paths
from vgc.battle import rules
from vgc.regulation import to_id


def _hooked(reg, hook: str) -> set[str]:
    """Legal abilities whose pinned implementation uses `hook`."""
    src = (paths.SHOWDOWN / "data" / "abilities.ts").read_text()
    out = set()
    for m in re.finditer(r"^\t(\w+): \{\n(.*?)^\t\},", src, re.S | re.M):
        name, body = m.group(1), m.group(2)
        if name in reg.dex.abilities and (f"{hook}(" in body or f"{hook}:" in body):
            out.add(name)
    return out


# `onStart` abilities that announce themselves but change nothing this state model carries —
# Frisk reads an item, Pressure doubles PP, Trace copies an ability. Named rather than silently
# absent, so the difference between "not applicable" and "not done" stays visible.
START_NOT_MODELLED = {
    "anticipation", "cloudnine", "curiousmedicine", "embodyaspectcornerstone",
    "embodyaspecthearthflame", "embodyaspectteal", "embodyaspectwellspring", "fairyaura",
    "flashfire", "forecast", "forewarn", "frisk", "gluttony", "hospitality", "iceface", "klutz",
    "mimicry", "moldbreaker", "pressure", "screencleaner", "shieldsdown", "supremeoverlord",
    "trace", "unnerve",
}


def test_every_switch_in_ability_is_either_modelled_or_named(reg):
    modelled = set(rules.WEATHER_ON_START) | set(rules.TERRAIN_ON_START) | set(rules.DROP_ON_START)
    hooked = _hooked(reg, "onStart")
    missing = hooked - modelled - START_NOT_MODELLED
    assert not missing, f"the pinned build starts these and nothing here knows: {sorted(missing)}"
    assert modelled <= hooked | {"mistysurge"}, "a table names an ability that does not do this"


def test_the_stat_drop_answers_are_exactly_the_pinned_ones(reg):
    assert set(rules.REBOUND) == _hooked(reg, "onAfterEachBoost")
    blockers = _hooked(reg, "onTryBoost")
    assert blockers <= set(rules.BLOCKS_DROP), \
        f"these refuse a drop and nothing here knows: {sorted(blockers - set(rules.BLOCKS_DROP))}"
    assert rules.REFLECTS_DROP <= blockers


def test_every_on_hit_ability_is_covered(reg):
    modelled = (set(rules.HIT_BOOST_SELF) | set(rules.HIT_DROP_SELF)
                | set(rules.HIT_BOOST_SELF_IF_TYPE) | set(rules.HIT_DROP_ATTACKER)
                | set(rules.HIT_WEATHER) | set(rules.HIT_TERRAIN) | rules.ANNOUNCES_ON_HIT)
    hooked = _hooked(reg, "onDamagingHit")
    assert not hooked - modelled, f"unhandled on-hit abilities: {sorted(hooked - modelled)}"


# --- what the pop-up is for --------------------------------------------------------------

def test_team_preview_offers_every_ability_the_species_could_have(reg):
    assert rules.candidate_abilities(reg, "Incineroar") == ["blaze", "intimidate"]
    # ...and exactly one once you have watched it fire.
    assert rules.candidate_abilities(reg, "Incineroar", known="Intimidate") == ["intimidate"]


def test_an_arriving_pokemon_offers_what_it_could_announce(reg):
    out = rules.on_switch_in(reg, "Incineroar")
    loud = next(o for o in out if "Intimidate" in o.label)
    assert loud.ability == "intimidate" and "atk -1" in loud.label
    # Seeing nothing is an option and it is informative: Intimidate would have said so, and with
    # only Blaze left the silence pins the ability outright rather than merely excluding one.
    silence = next(o for o in out if o.label.startswith("nothing announced"))
    assert silence.ability == "blaze" and silence.effects == []


def test_a_drop_is_not_a_foregone_conclusion_against_an_unknown(reg):
    """The heart of it. Against Corviknight the same Intimidate has two possible endings, and
    which one you see decides the ability — that is a reveal bought with the tap that logs it."""
    out = {o.label: o for o in rules.stat_drop_outcomes(reg, "Corviknight", "atk", -1)}
    assert any("Mirror Armor" in x for x in out)
    reflect = next(o for k, o in out.items() if "Mirror Armor" in k)
    assert reflect.effects == [{"kind": "reflect", "stat": "atk", "stages": -1}]
    plain = next(o for k, o in out.items() if k.startswith("atk -1, nothing else"))
    assert plain.effects == [{"kind": "boost", "stat": "atk", "stages": -1}]
    assert "mirrorarmor" in plain.excludes, "the drop landing proves it is not Mirror Armor"

    answered = {o.label: o for o in rules.stat_drop_outcomes(reg, "Milotic", "atk", -1)}
    rebound = next(o for k, o in answered.items() if "Competitive" in k)
    assert rebound.effects == [{"kind": "boost", "stat": "atk", "stages": -1},
                               {"kind": "boost", "stat": "spa", "stages": 2}]


def test_a_blocked_drop_does_nothing_at_all(reg):
    """Clear Body refuses it outright, which is a different answer from taking it and answering."""
    out = {o.label: o for o in rules.stat_drop_outcomes(reg, "Torkoal", "atk", -1)}
    refused = next(o for k, o in out.items() if "White Smoke" in k)
    assert refused.effects == []


def test_being_hit_can_change_the_defence_the_next_read_depends_on(reg):
    """Stamina matters twice: it is a reveal, and it moves the Defence the bulk channel is about
    to read the next hit against."""
    out = [o for o in rules.on_damaging_hit(reg, "Archaludon", "Close Combat")]
    stamina = next(o for o in out if "Stamina" in o.label)
    assert stamina.effects == [{"kind": "boost", "stat": "def", "stages": 1}]


def test_an_on_hit_answer_can_depend_on_the_move(reg):
    """Justified only answers a Dark move, so the move is an argument and not an afterthought."""
    dark = [o.label for o in rules.on_damaging_hit(reg, "Ceruledge", "Knock Off")]
    assert any("Weak Armor" in x for x in dark)
    fire = [o.label for o in rules.on_damaging_hit(reg, "Ceruledge", "Flamethrower")]
    assert any("Flash Fire" in x or "nothing announced" in x for x in fire)


# --- applying one, and the chain it starts ------------------------------------------------

def _two_v_two(reg, mine, theirs):
    from vgc.battle.state import BattleState, Mon

    st = BattleState("p1", reg.dex)
    for sid, names in (("p1", mine), ("p2", theirs)):
        st.sides[sid].team_size = len(names)
        for n in names:
            st.sides[sid].mons.append(Mon(n))
    st.begin_turn(1)
    for slot, _ in enumerate(mine):
        st.switch_in("p1", slot, st.sides["p1"].mons[slot], mine[slot])
    st.switch_in("p2", 0, st.sides["p2"].mons[0], theirs[0])
    return st


def test_intimidate_asks_rather_than_assumes(reg):
    """The drop is not applied when Intimidate fires, because what it *does* depends on an ability
    nobody has established. Applying it eagerly and again through the answer is the double-count
    this design exists to prevent."""
    st = _two_v_two(reg, ["Rillaboom", "Milotic"], ["Incineroar", "Kingambit"])
    fired = next(o for o in rules.on_switch_in(reg, "Incineroar") if o.ability == "intimidate")
    pending = rules.apply(st, fired, st.at("p2", 0))

    assert st.at("p2", 0).ability == "intimidate"
    assert [p.mon.species for p in pending] == ["Rillaboom", "Milotic"]
    assert st.at("p1", 0).boosts == {} and st.at("p1", 1).boosts == {}, "nothing applied yet"

    for p in pending:
        options = rules.stat_drop_outcomes(reg, p.mon.species, p.stat, p.stages,
                                           known=p.mon.ability)
        rules.apply(st, options[0], p.mon, source=p.source)
    assert st.at("p1", 0).boosts == {"atk": -1}                      # Rillaboom, nothing special
    assert st.at("p1", 1).boosts == {"atk": -1, "spa": 2}            # Milotic answers
    assert st.at("p1", 1).ability == "competitive"


def test_mirror_armor_sends_it_back_to_the_source(reg):
    st = _two_v_two(reg, ["Corviknight", "Rillaboom"], ["Incineroar", "Kingambit"])
    fired = next(o for o in rules.on_switch_in(reg, "Incineroar") if o.ability == "intimidate")
    pending = rules.apply(st, fired, st.at("p2", 0))
    corv = next(p for p in pending if p.mon.species == "Corviknight")
    reflect = next(o for o in rules.stat_drop_outcomes(reg, "Corviknight", "atk", -1)
                   if o.ability == "mirrorarmor")
    rules.apply(st, reflect, corv.mon, source=corv.source)
    assert corv.mon.boosts == {}, "the drop never lands on the holder"
    assert st.at("p2", 0).boosts == {"atk": -1}, "it lands on whoever caused it"
    assert corv.mon.ability == "mirrorarmor"


def test_silence_narrows_what_will_be_offered_next_time(reg):
    """Once an ability has failed to announce itself it should stop being offered, and when that
    leaves one candidate the app knows the ability without ever being told."""
    st = _two_v_two(reg, ["Rillaboom", "Milotic"], ["Archaludon", "Kingambit"])
    them = st.at("p2", 0)
    assert rules.still_possible(reg, them) == ["stalwart", "stamina", "sturdy"]

    quiet = next(o for o in rules.on_damaging_hit(reg, "Archaludon", "Close Combat")
                 if o.label.startswith("nothing announced"))
    rules.apply(st, quiet, them)
    assert rules.still_possible(reg, them) == ["stalwart", "sturdy"]
    assert them.ability is None, "two candidates left is not knowing"


def test_an_entered_ability_is_recorded_as_revealed_not_assumed(reg):
    st = _two_v_two(reg, ["Rillaboom", "Milotic"], ["Incineroar", "Kingambit"])
    fired = next(o for o in rules.on_switch_in(reg, "Incineroar") if o.ability == "intimidate")
    rules.apply(st, fired, st.at("p2", 0))
    assert st.at("p2", 0).ability_source == "revealed"


# --- items ---------------------------------------------------------------------------------

def test_the_seed_table_matches_the_pinned_build(reg):
    """Derived, like every other table here. The boost is a separate `boosts:` field rather than
    an inline call, which is why reading it off the trigger body finds nothing."""
    src = (paths.SHOWDOWN / "data" / "items.ts").read_text()
    found = {}
    for m in re.finditer(r"^\t(\w+): \{\n(.*?)^\t\},", src, re.S | re.M):
        name, body = m.group(1), m.group(2)
        if "onTerrainChange" not in body or name not in reg.dex.items:
            continue
        terrain = re.search(r"isTerrain\('(\w+)'\)", body).group(1)
        boost = re.search(r"boosts: \{\s*(\w+): (-?\d)", body)
        found[terrain] = (name, boost.group(1), int(boost.group(2)))
    assert found == rules.SEEDS


def test_a_terrain_asks_one_question_however_large_the_item_space_is(reg):
    """An item's hypothesis space is the whole legal list, not the two or three an ability has —
    so the trigger is keyed the other way round. Nothing asks *which item*; it asks whether the
    one item that could respond to this terrain fired."""
    out = rules.on_terrain_set(reg, "grassyterrain")
    assert len(out) == 2
    fired = out[0]
    assert fired.item == "grassyseed" and fired.consumed
    assert fired.effects == [{"kind": "boost", "stat": "def", "stages": 1}]
    # Nothing to ask when the terrain has no seed, or when you already know it holds something else.
    assert [o.label for o in rules.on_terrain_set(reg, "grassyterrain", held="Assault Vest")] \
        == ["nothing announced"]


def test_a_consumed_item_is_known_and_known_to_be_gone(reg):
    """Both halves matter. A Sitrus Berry already eaten cannot heal again, and the damage channel
    reads `item` — so leaving a spent seed in place would have it applying a boost forever."""
    st = _two_v_two(reg, ["Rillaboom", "Milotic"], ["Incineroar", "Kingambit"])
    them = st.at("p2", 0)
    assert them.item is None
    rules.apply(st, rules.on_terrain_set(reg, "grassyterrain")[0], them)
    assert them.boosts == {"def": 1}
    assert them.item == "" and them.lost_item == "grassyseed"
    assert them.item_source == "revealed"


def test_the_switch_in_ability_set_is_exactly_the_pinned_one(reg):
    """The set that decides whether an announcement is Speed evidence. Getting it wrong is not a
    missing inference but a wrong one: a response ability read as a race says Incineroar outran a
    Kingambit it did not outrun."""
    assert rules.SWITCH_IN_ABILITIES == _hooked(reg, "onStart") - rules.AMBIGUOUS_TRIGGER
    # No response ability may be in it — that is the whole point.
    assert not rules.SWITCH_IN_ABILITIES & set(rules.REBOUND)
    assert not rules.SWITCH_IN_ABILITIES & _hooked(reg, "onDamagingHit")
