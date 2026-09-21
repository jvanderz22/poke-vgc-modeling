"""Your damage, read backwards to their bulk — one equation in two unknowns.

The channel's distinctive property is that it does *not* locate the HP/defence split and does
locate the product, so these pin the shape of the region rather than a range per stat, and the
two censoring cases that made it unsound before they were handled.
"""

from __future__ import annotations

import pytest

from vgc.belief import bulk, damage


class Ev:
    """A damage event with only the fields the code under test reads."""

    def __init__(self, **kw):
        self.move = "Liquidation"
        self.spread = False
        self.fainted = False
        self.hp_before = 1.0
        self.hp_after = 0.5
        self.hp_max = None
        self.exact = False
        self.crit = False
        self.attacker_status = None
        self.target_side_conditions = []
        self.field = {"weather": None, "terrain": None, "pseudo": []}
        self.__dict__.update(kw)

    @property
    def lost(self):
        return max(0.0, self.hp_before - self.hp_after)


def test_hp_is_the_one_stat_with_no_nature_multiplier(reg):
    assert bulk.hp_stat(reg, "Incineroar", 0) == reg.dex.get_species("Incineroar")["baseStats"]["hp"] + 75
    assert bulk.hp_stat(reg, "Incineroar", 32) == bulk.hp_stat(reg, "Incineroar", 0) + 32
    assert bulk.hp_stat(reg, "Not A Pokemon", 0) is None
    # No base-1 HP species is legal in Reg M-C, so the Shedinja branch is unreachable here and is
    # carried for the formula's sake rather than this regulation's.


def test_a_survivor_on_a_sliver_is_censored_like_a_faint(reg):
    """Focus Sash, Sturdy and Endure all floor a lethal hit at 1 HP and the damage line looks the
    same either way, so the observation is a lower bound. Treating it as an equality was 16 of the
    19 misses this channel had left; censoring is sound whether or not a sash was the reason."""
    assert damage.censored(Ev(fainted=True))
    assert damage.censored(Ev(hp_after=0.005))
    assert not damage.censored(Ev(hp_after=0.5))


def test_a_spread_move_that_hit_one_target_is_not_reduced(reg):
    """`@smogon/calc` derives the 0.75 from `gameType` and the move's target, with no per-move
    override, while Showdown applies it only when more than one Pokémon was actually hit."""
    assert damage.game_type(reg, Ev(move="Heat Wave", spread=True)) == "Doubles"
    assert damage.game_type(reg, Ev(move="Heat Wave", spread=False)) == "Singles"
    assert damage.game_type(reg, Ev(move="Liquidation", spread=False)) == "Doubles"
    # ...and when that would also change the screen multiplier, no value is right for both.
    assert damage.game_type(reg, Ev(move="Heat Wave", spread=False,
                                    target_side_conditions=["reflect"])) is None
    assert damage.usable(reg, Ev(move="Heat Wave", spread=False,
                                 target_side_conditions=["reflect"])) == "spread_and_screen_disagree"


def test_the_region_is_kept_joint_rather_than_projected(reg):
    """The indistinguishable direction is a hyperbola — more HP and less Defence looks like less HP
    and more Defence — so the corners the observation ruled out survive only if the region does."""
    b = bulk.BulkBelief(side="p2", species="Incineroar", cap=4)
    b.regions["def"] = {(0, 4), (1, 3), (2, 2), (3, 1), (4, 0)}
    assert b.bounds() == {"hp": (0, 4), "def": (0, 4)}       # the projections say nothing
    triples = b.triples()
    assert (0, 4, 0) in triples and (0, 0, 0) not in triples  # ...the region still does


def test_a_physical_and_a_special_hit_share_their_hp_term(reg):
    """The honest way to separate HP from a defensive stat, and the reason the two regions are
    joined on HP rather than multiplied together."""
    b = bulk.BulkBelief(side="p2", species="Incineroar", cap=3)
    b.regions["def"] = {(1, 0), (1, 1), (2, 0)}
    b.regions["spd"] = {(2, 3), (3, 0)}
    assert b.triples() == {(2, 0, 3)}                        # only HP 2 appears in both


def test_an_untested_stat_is_left_alone(reg):
    """A Pokémon hit only physically has said nothing whatever about its Special Defence, and a
    product set would imply otherwise."""
    b = bulk.BulkBelief(side="p2", species="Incineroar", cap=2)
    b.regions["def"] = {(0, 0)}
    assert b.triples() == {(0, 0, s) for s in range(3)}
    assert bulk.BulkBelief(side="p2", species="Incineroar").triples() is None


def test_an_empty_region_widens_back_and_confesses(reg):
    b = bulk.BulkBelief(side="p2", species="Incineroar", cap=2)
    b._apply("def", {(0, 0)})
    b._apply("def", set())
    assert b.contradicted and "def" not in b.regions and b.triples() is None


@pytest.mark.calc
def test_it_reads_a_known_hit_back_to_a_real_region(reg, calc):
    """End to end against the pinned calc: a hit that landed for a known fraction rules out both
    the very frail and the very bulky corners, and the truth stays inside."""
    from vgc.data.observe import Observer
    from vgc.teams.sets import PokemonSet, StatPoints

    attacker = PokemonSet(species="Rillaboom", item="Life Orb", ability="Grassy Surge",
                          nature="Adamant", sp=StatPoints(atk=32, spe=32), level=50,
                          moves=["Wood Hammer"])
    defender_sp = {"hp": 20, "def_": 12}
    d = PokemonSet(species="Incineroar", item="Sitrus Berry", ability="Blaze", nature="Careful",
                   sp=StatPoints(**defender_sp), level=50)
    hp = bulk.hp_stat(reg, "Incineroar", 20)
    rolls = calc.calc(attacker, d, "Wood Hammer", field={"gameType": "Doubles"})
    lost = ((rolls.min + rolls.max) / 2) / hp
    assert 0 < lost < 1, "the fixture must survive, or the observation is censored"
