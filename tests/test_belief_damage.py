"""Phase 8's second channel: damage magnitude → a bound on their offensive Stat Points.

Four of the five bugs found building this were the same shape — something was handed to the calc
or read from the dex export that was silently not what it looked like — so most of these tests
pin that shape rather than the arithmetic.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from vgc import paths
from vgc.belief import damage
from vgc.data.observe import Observer

pytestmark = pytest.mark.calc


# --- the lists have to match the pinned build -------------------------------------------

def _moves_ts_entries():
    src = (paths.SHOWDOWN / "data" / "moves.ts").read_text()
    spans = [(m.start(), m.group(1)) for m in re.finditer(r"^\t([a-z0-9]+): \{", src, re.M)]
    for i, (pos, name) in enumerate(spans):
        yield name, src[pos:(spans[i + 1][0] if i + 1 < len(spans) else len(src))]


@pytest.mark.showdown
def test_the_multihit_list_matches_the_pinned_build():
    """The dex export carries no `multihit` field at all, so the obvious guard silently never
    fired. These come from the pinned source instead, and a Showdown bump that changes the set
    has to fail here rather than quietly widen the error."""
    found = {n for n, body in _moves_ts_entries() if re.search(r"^\t\tmultihit:", body, re.M)}
    assert found == damage.MULTI_HIT


@pytest.mark.showdown
def test_the_runtime_power_list_matches_the_pinned_build():
    found = {n for n, body in _moves_ts_entries() if "basePowerCallback" in body}
    assert found == damage.RUNTIME_POWER


@pytest.mark.showdown
def test_the_charge_list_matches_the_pinned_build():
    found = set()
    for name, body in _moves_ts_entries():
        m = re.search(r"^\t\tflags: \{(.*?)\},$", body, re.M | re.S)
        if m and re.search(r"\bcharge: 1", m.group(1)):
            found.add(name)
    assert found == damage.CHARGE_MOVES


def test_the_dex_export_still_lacks_multihit(reg):
    """Pinned because it is *why* the list above exists. If a future export starts carrying it,
    the list can go — and until then, code that reads it is reading None."""
    assert (reg.dex.get_move("populationbomb") or {}).get("multihit") is None


# --- the calc drops what it does not recognise, without saying so ------------------------

def test_an_id_and_a_name_are_not_the_same_item(reg, calc):
    """The single largest source of wrong answers here. `@smogon/calc` ignores a string it does
    not know rather than failing, and the Observer stores items and abilities as ids."""
    from vgc.teams.sets import PokemonSet, StatPoints

    mk = lambda item, ab: PokemonSet(species="Kingambit", item=item, ability=ab,  # noqa: E731
                                     nature="Adamant", sp=StatPoints(atk=32), level=50)
    target = PokemonSet(species="Rillaboom", item="Miracle Seed", ability="Grassy Surge",
                        nature="Adamant", sp=StatPoints(hp=32, atk=32), level=50)
    named = calc.calc(mk("Black Glasses", "Defiant"), target, "Kowtow Cleave")
    ided = calc.calc(mk("blackglasses", "defiant"), target, "Kowtow Cleave")
    assert named.max > ided.max, "if these ever agree, `proper()` has stopped being needed"
    assert damage.proper(reg.dex.items, "blackglasses") == "Black Glasses"
    assert damage.proper(reg.dex.abilities, "grassysurge") == "Grassy Surge"


def test_the_field_is_translated_and_refuses_what_it_cannot_translate(reg, calc):
    """Same failure again: `terrain="grassyterrain"` is silently neutral, `"Grassy"` is the 1.3×."""
    from vgc.teams.sets import PokemonSet, StatPoints

    a = PokemonSet(species="Rillaboom", item="Miracle Seed", ability="Grassy Surge",
                   nature="Adamant", sp=StatPoints(atk=32), level=50)
    d = PokemonSet(species="Incineroar", item="Sitrus Berry", ability="Intimidate",
                   nature="Careful", sp=StatPoints(hp=32, def_=16), level=50)
    raw = calc.calc(a, d, "Grassy Glide", field={"gameType": "Doubles", "terrain": "grassyterrain"})
    good = calc.calc(a, d, "Grassy Glide", field={"gameType": "Doubles", "terrain": "Grassy"})
    assert good.max > raw.max

    class Ev:
        move = "Grassy Glide"
        spread = False
        field = {"weather": None, "terrain": "grassyterrain"}
        target_side_conditions: list[str] = []
    assert damage._field(reg, Ev())["terrain"] == "Grassy"

    Ev.field = {"weather": "somethingnew", "terrain": None}
    assert damage._field(reg, Ev()) is None          # abstain, never a silently neutral field


# --- what the events carry ---------------------------------------------------------------

def test_the_event_records_both_formes_as_of_the_moment(reg):
    """Gengar is base 60 Defence and Mega Gengar is 80. Reading the forme off the battle's final
    state answers a question about a different Pokémon, and made true observations impossible."""
    o = Observer("spectator", reg.dex)
    o.feed_many([
        "|switch|p1a: Gengar|Gengar, L50, M|100/100",
        "|switch|p2a: Kingambit|Kingambit, L50, M|100/100",
        "|turn|1",
        "|move|p2a: Kingambit|Kowtow Cleave|p1a: Gengar",
        "|-damage|p1a: Gengar|60/100",
        "|-mega|p1a: Gengar|Gengar|Gengarite",
        "|detailschange|p1a: Gengar|Gengar-Mega, L50, M",
        "|turn|2",
        "|move|p2a: Kingambit|Kowtow Cleave|p1a: Gengar",
        "|-damage|p1a: Gengar|30/100",
    ])
    before, after = o.damage_log[0], o.damage_log[1]
    assert before.target_forme == "Gengar" and after.target_forme == "Gengar-Mega"
    assert o.sides["p1"].mons[0].forme == "Gengar-Mega"     # live state is the *later* one


def test_a_ko_is_recorded_as_censored(reg):
    o = Observer("spectator", reg.dex)
    o.feed_many(["|turn|1", "|move|p1a: Garchomp|Earthquake|p2a: Kingambit",
                 "|-damage|p2a: Kingambit|0 fnt"])
    assert o.damage_log[0].fainted and o.damage_log[0].lost == 1.0


def test_the_observed_loss_is_an_interval_not_a_number(reg):
    """A spectator sees HP out of 100, so the loss is known to about a point. Reporting the
    midpoint and calling it the damage is how a sound channel quietly stops being one."""
    class Ev:
        lost = 0.5
        exact = False
        hp_max = None
    lo, hi = damage.observed_loss(Ev(), 200)
    assert lo < 100 < hi and (hi - lo) >= 4        # ±1 percentage point of 200 HP, plus rounding
    Ev.exact, Ev.hp_max = True, 200
    lo, hi = damage.observed_loss(Ev(), 200)
    assert (hi - lo) == pytest.approx(1.0)


# --- abstention --------------------------------------------------------------------------

@pytest.mark.parametrize("move, reason", [
    ("Population Bomb", "move_power_hidden"),   # multi-hit
    ("Last Respects", "move_power_hidden"),     # power from fainted allies
    ("Eruption", "move_power_hidden"),          # power from the attacker's own HP
    ("Seismic Toss", "move_power_hidden"),      # fixed damage: no signal at all
    ("Protect", "not_a_damaging_move"),
])
def test_moves_that_cannot_be_reproduced_are_refused(reg, move, reason):
    class Ev:
        pass
    Ev.move = move
    Ev.lost = 0.5
    Ev.field = {"weather": None, "terrain": None}
    Ev.target_side_conditions = []
    assert damage.usable(reg, Ev()) == reason


def test_electro_shot_is_used_rather_than_refused(reg):
    """It charges and boosts itself, which is exactly why it was abstained on at first. Both are
    modelled now — the charge is harmless and the boost is subtracted — so refusing it would throw
    away the largest single block of evidence a two-turn move contributes."""
    class Ev:
        move = "Electro Shot"
        lost = 0.4
        field = {"weather": "raindance", "terrain": None}
        target_side_conditions: list[str] = []
    assert damage.usable(reg, Ev()) is None
    assert "electroshot" not in damage.ABSTAIN_MOVES
    assert "meteorbeam" not in damage.ABSTAIN_MOVES
    assert "solarbeam" in damage.ABSTAIN_MOVES      # charges, but has no self-boost to model


def test_an_ordinary_move_is_accepted(reg):
    class Ev:
        move = "Kowtow Cleave"
        lost = 0.4
        field = {"weather": "raindance", "terrain": None}
        target_side_conditions: list[str] = []
    assert damage.usable(reg, Ev()) is None


# --- the calc applies some moves' own boost for you --------------------------------------

def test_the_calc_matches_showdowns_stat_stages_on_an_ordinary_move(reg, calc):
    """`sim/pokemon.ts` uses boostTable [1, 1.5, 2, 2.5, 3, 3.5, 4] for stats, and the Champions
    mod does not override it. Pinned because a disagreement here would silently corrupt every
    damage inference that involves a boost."""
    from vgc.teams.sets import PokemonSet, StatPoints

    a = PokemonSet(species="Archaludon", ability="Stamina", nature="Modest",
                   sp=StatPoints(spa=32), level=50)
    d = PokemonSet(species="Incineroar", ability="Intimidate", nature="Careful",
                   sp=StatPoints(hp=32, spd=32), level=50)
    base = calc.calc(a, d, "Flash Cannon")
    for stage, expected in enumerate([1, 1.5, 2, 2.5, 3, 3.5, 4]):
        got = calc.calc(a, d, "Flash Cannon", attacker_boosts={"spa": stage})
        assert got.max / base.max == pytest.approx(expected, rel=0.02), stage


def test_electro_shot_self_boost_is_already_in_the_calc(reg, calc):
    """The bridge bug that nearly got read as a mechanic being wrong.

    `@smogon/calc`'s Champions mechanics do `if (move.named('Meteor Beam', 'Electro Shot'))` and
    add the +1 themselves, so handing them the boost the log reports counts it twice. Measured:
    a +1 passed to Electro Shot moves the damage by ~1.32x where a normal move moves it by 1.50x,
    because the calc is really going from +1 to +2.
    """
    from vgc.teams.sets import PokemonSet, StatPoints

    a = PokemonSet(species="Archaludon", ability="Stamina", nature="Modest",
                   sp=StatPoints(spa=32), level=50)
    d = PokemonSet(species="Incineroar", ability="Intimidate", nature="Careful",
                   sp=StatPoints(hp=32, spd=32), level=50)
    ordinary = calc.calc(a, d, "Flash Cannon", attacker_boosts={"spa": 1}).max / \
        calc.calc(a, d, "Flash Cannon").max
    selfing = calc.calc(a, d, "Electro Shot", attacker_boosts={"spa": 1}).max / \
        calc.calc(a, d, "Electro Shot").max
    assert ordinary == pytest.approx(1.5, rel=0.02)
    assert selfing == pytest.approx(4 / 3, rel=0.03), "the calc stopped self-boosting Electro Shot"


def test_the_self_boost_is_subtracted_before_the_calc_sees_it(reg):
    """Subtracted, not ignored: the boost persists into later turns, so a second Electro Shot at
    a logged +2 has to reach the calc as +1."""
    class Ev:
        move = "electroshot"
        attacker_boosts = {"spa": 1, "def": 2}
    assert damage.attacker_boosts_for(Ev()) == {"spa": 0, "def": 2}
    Ev.attacker_boosts = {"spa": 3}
    assert damage.attacker_boosts_for(Ev()) == {"spa": 2}
    Ev.move = "flashcannon"
    Ev.attacker_boosts = {"spa": 1}
    assert damage.attacker_boosts_for(Ev()) == {"spa": 1}
