"""What being on the field says about a Pokémon's next move, and how long each condition has left
(`live.on_field`, `live.timers`): the watch page's field reads them.

The turn a thing counts from is the part that is quietly wrong if it is wrong. A lead's Drizzle and
a replacement's Psychic Surge go up after the end of a turn has been counted, so their five turns
start with the next one; a Pokémon that came in at any point gets its first turn out the turn
after. Five is what the game assumes, and eight only once the holder is known to carry the item.
"""

from __future__ import annotations

import pytest

from vgc.battle import entry
from vgc.web import live

THEIRS = [
    {"species": "Pelipper", "ability": "drizzle", "item": "Damp Rock", "nature": "Bold",
     "moves": ["hurricane", "weatherball", "tailwind", "protect"]},
    {"species": "Grimmsnarl", "ability": "prankster", "item": "Light Clay", "nature": "Careful",
     "moves": ["spiritbreak", "reflect", "lightscreen", "partingshot"]},
    {"species": "Indeedee-F", "ability": "psychicsurge", "item": "Rocky Helmet", "nature": "Relaxed",
     "moves": ["followme", "helpinghand", "psychic", "protect"]},
    {"species": "Kingambit", "ability": "defiant", "item": "Choice Scarf", "nature": "Adamant",
     "moves": ["kowtowcleave", "suckerpunch", "ironhead", "lowkick"]},
    {"species": "Milotic", "ability": "competitive", "item": "Leftovers", "nature": "Bold",
     "moves": ["scald", "icebeam", "recover", "protect"]},
    {"species": "Archaludon", "ability": "stamina", "item": "Leftovers", "nature": "Modest",
     "moves": ["electroshot", "dracometeor", "flashcannon", "protect"]},
]


@pytest.fixture
def battle(reg, team_text):
    return entry.Battle(reg, {"perspective": "p1", "mine": entry.from_team(reg, team_text("valid_basic")),
                              "theirs": THEIRS})


def lead(b, *pairs):
    for side, slot, species in pairs:
        b.append({"kind": "lead", "side": side, "slot": slot, "species": species})
    announce(b)


def announce(b):
    """Every arrival's ability goes off, where it has one that does something."""
    for q in list(b.rp.questions):
        fired = next((i for i, o in enumerate(q.outcomes) if o.effects), 0)
        b.append({"kind": "answer", "question": q.id, "option": fired})


def field(b, side, slot):
    return live.on_field(b.rp, side, b.rp.state.at(side, slot))


def timer(b, cid):
    return next((t for t in live.timers(b.rp) if t["id"] == cid), None)


def move(b, side, slot, name, target=None):
    b.append({"kind": "move", "side": side, "slot": slot, "move": name, "target": target, "result": "hit"})


def test_fake_out_works_on_the_first_turn_out_and_no_other(battle):
    lead(battle, ("p1", 0, "Rillaboom"), ("p1", 1, "Incineroar"), ("p2", 0, "Kingambit"), ("p2", 1, "Milotic"))
    assert field(battle, "p1", 0)["first_turn_out"]            # leads: turn 1 is their first
    battle.append({"kind": "turn", "n": 1})
    assert field(battle, "p1", 0)["first_turn_out"]
    move(battle, "p1", 0, "Fake Out", {"side": "p2", "slot": 0})
    assert not field(battle, "p1", 0)["first_turn_out"]        # it has had it: next is turn 2
    assert field(battle, "p1", 1)["first_turn_out"]            # its partner has not moved yet
    battle.append({"kind": "turn", "n": 2})
    assert not field(battle, "p1", 1)["first_turn_out"]


def test_a_replacement_gets_its_first_turn_out_next_turn(battle):
    lead(battle, ("p1", 0, "Rillaboom"), ("p1", 1, "Incineroar"), ("p2", 0, "Kingambit"), ("p2", 1, "Milotic"))
    battle.append({"kind": "turn", "n": 1})
    battle.append({"kind": "damage", "side": "p1", "slot": 1, "pct": 0, "fainted": True})
    battle.append({"kind": "switch", "side": "p1", "slot": 1, "species": "Garchomp"})
    announce(battle)
    assert field(battle, "p1", 1)["first_turn_out"]
    assert not field(battle, "p1", 0)["first_turn_out"]


def test_a_protect_that_held_last_turn_is_flagged_until_it_moves_again(battle):
    lead(battle, ("p1", 0, "Garchomp"), ("p1", 1, "Kingambit"), ("p2", 0, "Kingambit"), ("p2", 1, "Milotic"))
    battle.append({"kind": "turn", "n": 1})
    move(battle, "p1", 0, "Protect")
    assert field(battle, "p1", 0)["protected_last"]            # choosing for turn 2 now
    battle.append({"kind": "turn", "n": 2})
    assert field(battle, "p1", 0)["protected_last"]
    move(battle, "p1", 0, "Rock Slide")
    assert not field(battle, "p1", 0)["protected_last"]


def test_a_choice_item_locks_the_last_move_until_a_switch(battle):
    lead(battle, ("p1", 0, "Rillaboom"), ("p1", 1, "Incineroar"), ("p2", 0, "Kingambit"), ("p2", 1, "Milotic"))
    battle.append({"kind": "turn", "n": 1})
    assert field(battle, "p2", 0)["locked"] is None
    move(battle, "p2", 0, "Kowtow Cleave", {"side": "p1", "slot": 0})
    assert field(battle, "p2", 0)["locked"] == "kowtowcleave"
    assert field(battle, "p2", 1)["locked"] is None            # Leftovers: nothing to lock it
    battle.append({"kind": "turn", "n": 2})
    battle.append({"kind": "switch", "side": "p2", "slot": 0, "species": "Archaludon"})
    battle.append({"kind": "switch", "side": "p2", "slot": 0, "species": "Kingambit"})
    assert field(battle, "p2", 0)["locked"] is None


def test_a_leads_weather_counts_from_turn_one_and_its_rock_makes_it_eight(battle):
    lead(battle, ("p1", 0, "Garchomp"), ("p1", 1, "Kingambit"), ("p2", 0, "Pelipper"), ("p2", 1, "Milotic"))
    rain = timer(battle, "raindance")
    assert rain["from"] == 1 and rain["by"] == "Pelipper"
    assert rain["last"] == 8 and rain["left"] == 8 and rain["extended_by"] == "damprock"


def test_a_terrain_from_the_ground_up_lasts_five(battle):
    lead(battle, ("p1", 0, "Rillaboom"), ("p1", 1, "Kingambit"), ("p2", 0, "Kingambit"), ("p2", 1, "Milotic"))
    grassy = timer(battle, "grassyterrain")
    assert grassy["by"] == "Rillaboom" and grassy["last"] == 5 and grassy["extended_by"] is None
    battle.append({"kind": "turn", "n": 3})
    assert timer(battle, "grassyterrain")["left"] == 3


def test_a_replacements_terrain_counts_from_the_next_turn(battle):
    lead(battle, ("p1", 0, "Garchomp"), ("p1", 1, "Kingambit"), ("p2", 0, "Kingambit"), ("p2", 1, "Milotic"))
    battle.append({"kind": "turn", "n": 3})
    battle.append({"kind": "damage", "side": "p2", "slot": 1, "pct": 0, "fainted": True})
    battle.append({"kind": "switch", "side": "p2", "slot": 1, "species": "Indeedee-F"})
    announce(battle)
    psychic = timer(battle, "psychicterrain")
    assert psychic["from"] == 4 and psychic["last"] == 8 and psychic["left"] == 5


def test_a_screen_nobody_was_named_for_goes_to_the_one_that_could_have_and_its_clay(battle):
    lead(battle, ("p1", 0, "Garchomp"), ("p1", 1, "Kingambit"), ("p2", 0, "Grimmsnarl"), ("p2", 1, "Milotic"))
    battle.append({"kind": "turn", "n": 1})
    battle.append({"kind": "side", "side": "p2", "condition": "Light Screen", "on": True})
    screen = timer(battle, "lightscreen")
    assert screen["side"] == "p2" and screen["by"] == "Grimmsnarl"
    assert screen["last"] == 8 and screen["extended_by"] == "lightclay"


def test_tailwind_lasts_four_and_ends_off_the_list(battle):
    lead(battle, ("p1", 0, "Garchomp"), ("p1", 1, "Kingambit"), ("p2", 0, "Pelipper"), ("p2", 1, "Milotic"))
    battle.append({"kind": "turn", "n": 1})
    battle.append({"kind": "side", "side": "p2", "condition": "Tailwind", "on": True})
    assert timer(battle, "tailwind")["last"] == 4
    battle.append({"kind": "turn", "n": 5})
    assert timer(battle, "tailwind") is None
