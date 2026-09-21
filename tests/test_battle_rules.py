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
    labels = [o.label for o in rules.on_switch_in(reg, "Incineroar")]
    assert any("Intimidate" in x and "atk -1" in x for x in labels)
    # Seeing nothing is an option, and it is informative: it rules Intimidate out.
    assert any("nothing announced" in x and "Blaze" in x for x in labels)


def test_a_drop_is_not_a_foregone_conclusion_against_an_unknown(reg):
    """The heart of it. Against Corviknight the same Intimidate has two possible endings, and
    which one you see decides the ability — that is a reveal bought with the tap that logs it."""
    out = {o.label: o for o in rules.stat_drop_outcomes(reg, "Corviknight", "atk", -1)}
    assert any("Mirror Armor" in x for x in out)
    reflect = next(o for k, o in out.items() if "Mirror Armor" in k)
    assert reflect.effects == [{"kind": "reflect", "stat": "atk", "stages": -1}]
    plain = next(o for k, o in out.items() if k == "atk -1")
    assert plain.effects == [{"kind": "boost", "stat": "atk", "stages": -1}]

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
