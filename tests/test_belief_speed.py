"""Phase 8's first channel: turn order → a bound on their Speed Stat Points.

The channel's whole claim is soundness — the truth must never leave the feasible set — so these
tests pin the five things that broke it while it was being built, each of which produced a
*wrong* inference rather than a missing one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vgc.belief import speed
from vgc.data.observe import Observer

FIXTURES = Path(__file__).parent / "fixtures"
REPLAYS = sorted((FIXTURES / "replays").glob("*.json"))


@pytest.fixture(scope="module")
def replay():
    return json.loads(REPLAYS[0].read_text())


@pytest.fixture(scope="module")
def obs(reg, replay):
    o = Observer("spectator", reg.dex)
    o.feed_many(replay["log"].split("\n"))
    return o


# --- the evidence log -------------------------------------------------------------------

def test_moves_are_recorded_in_the_order_they_resolved(obs):
    assert obs.moves_log
    for turn in {e.turn for e in obs.moves_log}:
        seqs = [e.seq for e in obs.moves_log if e.turn == turn]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_damage_is_attributed_to_the_move_that_caused_it(obs):
    assert obs.damage_log
    for ev in obs.damage_log:
        assert ev.attacker and ev.move and ev.target
        assert (ev.attacker_side, ev.attacker_slot) != (ev.target_side, ev.target_slot)
        assert 0.0 <= ev.hp_after <= ev.hp_before <= 1.0
        assert ev.fainted == (ev.hp_after == 0.0)


def test_residual_damage_is_not_attributed_to_a_move(reg):
    """Life Orb, recoil and poison carry a `[from]` tag. Reading them as the move's own output
    would tell the belief the attacker hits far harder than it does."""
    o = Observer("spectator", reg.dex)
    o.feed_many([
        "|turn|1",
        "|move|p1a: Garchomp|Earthquake|p2a: Kingambit",
        "|-damage|p2a: Kingambit|60/100",
        "|-damage|p1a: Garchomp|90/100|[from] item: Life Orb",
        "|-damage|p2a: Kingambit|54/100|[from] psn",
    ])
    assert [(e.target, e.hp_after) for e in o.damage_log] == [("Kingambit", 0.6)]


def test_healing_is_not_damage(reg):
    o = Observer("spectator", reg.dex)
    o.feed_many(["|turn|1", "|move|p1a: Garchomp|Earthquake|p2a: Kingambit",
                 "|-damage|p2a: Kingambit|60/100", "|-heal|p2a: Kingambit|70/100"])
    assert len(o.damage_log) == 1 and o.damage_log[0].hp_after == 0.6


# --- what decided the order -------------------------------------------------------------

def test_the_mega_formes_base_stats_are_used_not_the_preview_species(reg):
    """Mega Salamence is base 120 Speed where Salamence is 100. Keying the arithmetic on the
    preview species made the module think it was slower than it was, and rule out the truth."""
    assert speed.base_speed(reg, "Salamence") == 100
    assert speed.base_speed(reg, "Salamence-Mega") == 120
    # The 20-point base gap arrives before the nature multiplier, so at Timid it is worth 22.
    assert speed.speed_stat(reg, "Salamence-Mega", "Timid", 32) == 189
    assert speed.speed_stat(reg, "Salamence", "Timid", 32) == 167


def test_a_megas_ability_is_the_megas_not_the_sheets(reg):
    """Mega Swampert has Swift Swim where Swampert had Torrent, and under rain that is a ×2 the
    arithmetic has to know about. `Mon.ability` still reports the sheet's, on purpose."""
    o = Observer("spectator", reg.dex)
    o.feed_many([
        "|turn|1",
        "|switch|p1a: Swampert|Swampert, L50, M|100/100",
        "|-mega|p1a: Swampert|Swampert|Swampertite",
        "|detailschange|p1a: Swampert|Swampert-Mega, L50, M",
        "|move|p1a: Swampert|Waterfall|p2a: Kingambit",
    ])
    mon = o.sides["p1"].mons[0]
    assert mon.forme == "Swampert-Mega" and mon.mega
    assert o.moves_log[-1].ability == "swiftswim"
    assert o._active_ability(mon) == "swiftswim"


def test_trick_room_inverts_the_comparison(reg, obs):
    """The fixture replay has Trick Room up from turn 2, so this is not a corner case."""
    signs = {a.turn: s for a, b, s in speed.pairs(reg, obs.moves_log)}
    assert signs.get(1) == 1 and signs.get(2) == -1


def test_grassy_glide_is_compared_at_the_priority_it_actually_had(reg):
    """The dex reports 0; the pinned build gives +1 under Grassy Terrain to a grounded user.
    Comparing it against a real 0 as though they raced produced contradictions."""
    o = Observer("spectator", reg.dex)
    o.feed_many(["|-fieldstart|move: Grassy Terrain", "|turn|1",
                 "|switch|p1a: Rillaboom|Rillaboom, L50, M|100/100",
                 "|move|p1a: Rillaboom|Grassy Glide|p2a: Kingambit"])
    ev = o.moves_log[-1]
    assert ev.priority == 0                                  # what the dex says
    assert speed.effective_priority(reg, ev) == 1            # what actually happened


def test_a_speed_change_inside_the_turn_drops_the_pair(reg):
    """Weak Armor's +2 is on the move line but did not reorder the turn it was gained in. Both
    readings were tried against 3,000 battles and each contradicted cases the other did not, so
    the pair is dropped rather than resolved by guess."""
    o = Observer("spectator", reg.dex)
    o.feed_many([
        "|switch|p1a: Armarouge|Armarouge, L50|100/100",
        "|switch|p2a: Garchomp|Garchomp, L50, M|100/100",
        "|turn|1",
        "|move|p2a: Garchomp|Earthquake|p1a: Armarouge",
        "|-damage|p1a: Armarouge|60/100",
        "|-boost|p1a: Armarouge|spe|2",
        "|move|p1a: Armarouge|Armor Cannon|p2a: Garchomp",
    ])
    boosted = o.moves_log[-1]
    assert boosted.boosts.get("spe") == 2 and boosted.order_boosts.get("spe", 0) == 0
    assert not speed._stable(boosted)
    assert speed.pairs(reg, o.moves_log) == []


def test_an_item_that_decides_the_order_is_abstained_on(reg):
    """Quick Claw moves first 20% of the time whatever the stats say — a 0-Speed Torkoal appeared
    to outrun a Mega Salamence with 32."""
    o = Observer("spectator", reg.dex)
    o.feed_many(["|switch|p1a: Torkoal|Torkoal, L50, F|100/100",
                 "|switch|p2a: Salamence|Salamence, L50, F|100/100",
                 "|-item|p1a: Torkoal|Quick Claw", "|turn|1",
                 "|move|p1a: Torkoal|Eruption|p2a: Salamence",
                 "|move|p2a: Salamence|Hyper Voice|p1a: Torkoal"])
    assert o.moves_log[0].item == "quickclaw"
    assert not speed._usable(reg, o.moves_log[0])
    assert speed.pairs(reg, o.moves_log) == []


def test_a_reordering_move_drops_the_whole_turn(reg):
    """After You acts on somebody else's slot, so skipping its user is not enough."""
    o = Observer("spectator", reg.dex)
    o.feed_many(["|turn|1",
                 "|move|p1a: Indeedee-F|After You|p1b: Rillaboom",
                 "|move|p1b: Rillaboom|Wood Hammer|p2a: Kingambit",
                 "|move|p2a: Kingambit|Sucker Punch|p1b: Rillaboom"])
    assert len(o.moves_log) == 3 and speed.pairs(reg, o.moves_log) == []


# --- the bound itself --------------------------------------------------------------------

def test_a_tie_is_never_ruled_out(reg):
    """Showdown breaks a speed tie at random, so observing an order can never exclude equality."""
    at = lambda x: (x, x)  # noqa: E731 — a known nature is a band of width zero
    assert speed._ordered(at(100.0), at(100.0), 1) and speed._ordered(at(100.0), at(100.0), -1)
    assert speed._ordered(at(120.0), at(100.0), 1) and not speed._ordered(at(100.0), at(120.0), 1)


def test_an_unknown_nature_widens_rather_than_defaults_to_neutral(reg):
    """The regime difference the gates never saw. Under Open Team Sheets the nature is printed
    and the band is one number; under Team Preview Only it is not, and reading it as neutral is
    an assumption that can exclude the truth — a Timid opponent really does outrun something the
    neutral reading says it cannot.
    """
    assert speed.speed_band(reg, "Pelipper", "Timid", 0) == (93, 93)
    assert speed.speed_band(reg, "Pelipper", None, 0) == (76, 93)
    # So an order that is impossible at a neutral nature is merely *possible* at an unknown one,
    # and the belief keeps the spread rather than throwing it away.
    known, unknown = (80.0, 80.0), speed.speed_band(reg, "Pelipper", None, 0)
    assert not speed._ordered(known, (85.0, 85.0), 1)     # neutral: it could not have gone first
    assert speed._ordered(known, unknown, 1)              # unknown: a minus nature allows it


def test_the_feasible_set_starts_as_the_whole_budget(reg, obs):
    beliefs = speed.infer(reg, obs, known={})
    for b in beliefs.values():
        assert b.prior == list(range(reg.sp_per_stat_cap + 1))
        assert set(b.feasible) <= set(b.prior)


def test_a_pair_of_unknowns_is_deferred_rather_than_dropped_silently(reg, obs):
    """Two hidden spreads constrain each other; that is real information this does not yet use,
    and counting it is the difference between "not implemented" and "not noticed"."""
    beliefs = speed.infer(reg, obs, known={})
    assert sum(b.deferred for b in beliefs.values()) > 0
    assert all(b.used == 0 for b in beliefs.values())


def test_knowing_your_own_side_turns_deferrals_into_constraints(reg, obs):
    known = {("p1", m.species): 32 for m in obs.sides["p1"].mons}
    beliefs = speed.infer(reg, obs, known)
    assert beliefs and all(k[0] == "p2" for k in beliefs)
    assert sum(b.used for b in beliefs.values()) > 0


def test_narrowing_is_reported_against_the_prior(reg, obs):
    for b in speed.infer(reg, obs, known={}).values():
        assert b.narrowed == pytest.approx(1 - len(b.feasible) / len(b.prior))
        assert 0.0 <= b.narrowed <= 1.0


def test_weather_that_arrived_mid_turn_did_not_decide_the_order(reg):
    """A Swift Swim Pokémon whose rain started partway through the turn was not fast when the
    order was set. Reading the weather off its own move line said it was, and that was the last
    cause of contradictions to fall — visible only at 25,000 battles, not at 3,000."""
    o = Observer("spectator", reg.dex)
    o.feed_many([
        "|switch|p1a: Swampert|Swampert-Mega, L50, M|100/100",
        "|switch|p2a: Absol|Absol-Mega, L50, M|100/100",
        "|turn|1",
        "|move|p2a: Absol|Night Slash|p1a: Swampert",
        "|-weather|RainDance",
        "|move|p1a: Swampert|Ice Punch|p2a: Absol",
    ])
    swampert = o.moves_log[-1]
    assert swampert.weather == "raindance" and swampert.order_weather is None
    assert not speed._stable(swampert)
    assert speed.pairs(reg, o.moves_log) == []
