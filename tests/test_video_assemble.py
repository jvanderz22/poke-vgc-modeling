"""The battle reader's last layer, on event streams taken from Louisville round 5, game 1.

Each stream is what the screen layer reported there (docs/video-findings.md), so these check the
reading of a real sequence without needing the footage or OCR.
"""

from __future__ import annotations

import pytest

from vgc.battle import entry as journal
from vgc.regulation import load_regulation
from vgc.video.assemble import assemble
from vgc.video.screen import Event


def _set(species, ability, nature, item, moves):
    return {"species": species, "ability": ability, "nature": nature, "item": item, "moves": moves}


LEFT = [_set("Gardevoir", "Synchronize", "Modest", "Gardevoirite", ["Hyper Voice", "Expanding Force", "Trick Room", "Protect"]),
        _set("Gengar", "Cursed Body", "Timid", "Gengarite", ["Sludge Bomb", "Shadow Ball", "Focus Blast", "Protect"]),
        _set("Arcanine-Hisui", "Rock Head", "Jolly", "Focus Sash", ["Flare Blitz", "Head Smash", "Rock Slide", "Protect"]),
        _set("Indeedee-F", "Psychic Surge", "Relaxed", "Rocky Helmet", ["Helping Hand", "Follow Me", "Psychic", "Trick Room"]),
        _set("Milotic", "Competitive", "Modest", "Leftovers", ["Scald", "Ice Beam", "Haze", "Protect"]),
        _set("Sneasler", "Unburden", "Adamant", "Psychic Seed", ["Close Combat", "Rock Slide", "Throat Chop", "Protect"])]
RIGHT = [_set("Charizard", "Blaze", "Modest", "Charizardite Y", ["Heat Wave", "Weather Ball", "Protect", "Hurricane"]),
         _set("Raichu", "Lightning Rod", "Jolly", "Raichunite X", ["Fake Out", "Volt Switch", "Feint", "Volt Tackle"]),
         _set("Archaludon", "Stamina", "Bold", "Leftovers", ["Dragon Pulse", "Electro Shot", "Protect", "Snarl"]),
         _set("Grimmsnarl", "Prankster", "Sassy", "Light Clay", ["Spirit Break", "Reflect", "Light Screen", "Parting Shot"]),
         _set("Politoed", "Drizzle", "Modest", "Mystic Water", ["Weather Ball", "Protect", "Ice Beam", "Muddy Water"]),
         _set("Venusaur", "Chlorophyll", "Modest", "Focus Sash", ["Sleep Powder", "Protect", "Earth Power", "Sludge Bomb"])]
SETUP = {"perspective": "spectator", "p1": LEFT, "p2": RIGHT,
         "names": {"p1": "SAMUEL THOMPSON", "p2": "DORIAN KANG"}}

LEADS = [(174.8, "message", {"text": "The Trainer sent out Raichu and Grimmsnarl the Treasure Hunter!"}),
         (179.1, "message", {"text": "Go! Sneasler and Gardevoir!"}),
         (182.6, "choosing", {}),
         *[(182.8, "plate", {"slot": s, "name": n, "hp": 100})
           for s, n in (("L0", "Sneasler"), ("L1", "Gardevoir"), ("R0", "Grimmsnarl"), ("R1", "Raichu"))]]


@pytest.fixture(scope="module")
def reg():
    return load_regulation("reg_mc")


def _read(reg, rows):
    a = assemble(reg, SETUP, [Event(t, k, d) for t, k, d in rows])
    return [{k: v for k, v in e.items() if k != "t"} for e in a.entries], a


def test_leads_in_the_order_each_side_sent_them_out(reg):
    entries, a = _read(reg, LEADS)
    assert entries == [
        {"kind": "lead", "side": "p2", "slot": 0, "species": "Raichu"},
        {"kind": "lead", "side": "p2", "slot": 1, "species": "Grimmsnarl"},
        {"kind": "lead", "side": "p1", "slot": 0, "species": "Sneasler"},
        {"kind": "lead", "side": "p1", "slot": 1, "species": "Gardevoir"},
        {"kind": "turn", "n": 1}]
    assert a.missed == [] and a.errors == []


def test_what_comes_before_the_battle_is_not_read(reg):
    entries, _ = _read(reg, [(86.0, "message", {"text": "Sp. Atk  Attack st 6"}),
                             (90.0, "plate", {"slot": "L0", "name": "Muddy Water", "hp": None})] + LEADS)
    assert entries[0]["kind"] == "lead"


def test_a_move_finds_its_target_from_the_hp_it_changed(reg):
    entries, a = _read(reg, LEADS + [
        (249.9, "message", {"text": "Sneasler used Close Combat!"}),
        (253.6, "plate", {"slot": "R1", "name": "Raichu", "hp": 20}),
        # Close Combat's own drops: the rules make them, so the message adds nothing.
        (255.4, "message", {"text": "Sneasler's Defense and Sp. Def fell"}),
    ])
    assert entries[5:] == [
        {"kind": "move", "side": "p1", "slot": 0, "move": "Close Combat",
         "target": {"side": "p2", "slot": 0}, "spread": False},
        {"kind": "damage", "side": "p2", "slot": 0, "pct": 20, "fainted": False}]
    assert a.missed == []


def test_a_ko_and_the_recoil_the_rules_work_out(reg):
    entries, a = _read(reg, LEADS + [
        (249.9, "message", {"text": "Sneasler used Close Combat!"}),
        (253.6, "plate", {"slot": "R1", "name": "Raichu", "hp": 20}),
        (258.4, "message", {"text": "The opposing Raichu used Volt Tackle!"}),
        (262.4, "plate", {"slot": "L0", "name": "Sneasler", "hp": 0}),
        (266.1, "message", {"text": "Sneasler fainted!"}),
        (269.9, "message", {"text": "The opposing Raichu was damaged by the recoil!"}),
        (270.6, "plate", {"slot": "R1", "name": "Raichu", "hp": 0}),
        (271.9, "message", {"text": "The opposing Raichu fainted!"}),
        (283.9, "message", {"text": "The Trainer sent out Archaludon the Great Friend!"}),
        (287.6, "message", {"text": "Go! Gardevoir!"}),
    ])
    assert {"kind": "damage", "side": "p1", "slot": 0, "pct": 0, "fainted": True} in entries
    # Each replacement goes into the slot its side's Pokémon fainted in.
    assert entries[-2:] == [{"kind": "switch", "side": "p2", "slot": 0, "species": "Archaludon"},
                            {"kind": "switch", "side": "p1", "slot": 0, "species": "Gardevoir"}]
    assert a.errors == []


def test_a_switch_in_ability_is_answered_from_its_banner_and_its_terrain_not_set_twice(reg):
    entries, _ = _read(reg, LEADS + [
        (216.1, "message", {"text": "Gardevoir, come back!"}),
        (219.6, "message", {"text": "Go! Indeedee!"}),
        (223.1, "banner", {"side": "L", "text": "Indeedee's Psychic Surge"}),
        (223.6, "message", {"text": "The battlefield got weird!"}),
        (225.6, "banner", {"side": "L", "text": "Sneasler's Psychic Seed"}),
        (227.9, "message", {"text": "Sneasler's Sp. Def rose!"}),
    ])
    kinds = [e["kind"] for e in entries[5:]]
    assert kinds == ["switch", "answer", "answer"]
    assert entries[5] == {"kind": "switch", "side": "p1", "slot": 1, "species": "Indeedee-F"}
    assert all(e["option"] is not None for e in entries[6:])


def test_a_spread_move_into_a_protect(reg):
    entries, _ = _read(reg, LEADS + [
        (227.0, "message", {"text": "Gardevoir used Protect!"}),
        (237.1, "message", {"text": "The opposing Grimmsnarl used Snarl!"}),
        (239.6, "message", {"text": "Gardevoir protected itself!"}),
        (242.6, "plate", {"slot": "L0", "name": "Sneasler", "hp": 86}),
    ])
    move = next(e for e in entries if e.get("move") == "Snarl")
    assert move["spread"] and move["results"] == [{"side": "p1", "slot": 1, "result": "protected"}]
    assert {"kind": "damage", "side": "p1", "slot": 0, "pct": 86, "fainted": False} in entries


def test_a_number_nothing_explains_waits_for_the_still_plates(reg):
    # A plate sliding past during a switch read "Gardevoir 10%"; the decision point's plates say 100.
    entries, _ = _read(reg, LEADS + [
        (209.6, "plate", {"slot": "L1", "name": "Gardevoir", "hp": 10}),
        (290.9, "choosing", {}),
        (291.1, "plate", {"slot": "L1", "name": "Gardevoir", "hp": 100}),
    ])
    assert [e["kind"] for e in entries[5:]] == ["turn"]


def test_a_change_the_still_plates_confirm_is_written_before_the_turn(reg):
    entries, _ = _read(reg, LEADS + [
        (209.6, "plate", {"slot": "R0", "name": "Grimmsnarl", "hp": 88}),
        (290.9, "choosing", {}),
        (291.1, "plate", {"slot": "R0", "name": "Grimmsnarl", "hp": 88}),
    ])
    assert entries[5:] == [
        {"kind": "damage", "side": "p2", "slot": 1, "pct": 88, "fainted": False, "eot": "unexplained"},
        {"kind": "turn", "n": 2}]


def test_a_message_the_parser_does_not_know_is_missed_not_guessed(reg):
    entries, a = _read(reg, LEADS + [(271.4, "message", {"text": "Thie opposing Naichu tt damagod by the recoil"})])
    assert len(entries) == 5
    assert a.missed[-1]["why"] == "message not understood"


def test_the_winner_from_the_broadcasts_banner(reg):
    entries, _ = _read(reg, LEADS + [(745.0, "message", {"text": "GAME WINNER DORIAN KANG"})])
    assert entries[-1] == {"kind": "end", "winner": "p2"}


def test_a_faint_the_broadcast_did_not_show_is_worked_out_from_the_still_plates(reg):
    # Round 6: the stream cut away while sandstorm took the opposing side's last HP, and the next
    # thing shown was its replacement coming in. The plates then name the two on the field.
    entries, a = _read(reg, LEADS + [
        (595.0, "message", {"text": "The Trainer sent out Archaludon the Great Friend!"}),
        (600.0, "choosing", {}),
        *[(600.2, "plate", {"slot": s, "name": n, "hp": 100})
          for s, n in (("L0", "Sneasler"), ("L1", "Gardevoir"), ("R0", "Grimmsnarl"), ("R1", "Archaludon"))],
    ])
    assert entries[5:] == [{"kind": "faint", "side": "p2", "slot": 0},
                           {"kind": "switch", "side": "p2", "slot": 0, "species": "Archaludon"},
                           {"kind": "turn", "n": 2}]
    assert a.missed[-1]["why"] == "not shown: worked out from who was sent out"


def test_a_turn_shown_again_is_not_read_twice(reg):
    move = (249.9, "message", {"text": "Sneasler used Close Combat!"})
    entries, a = _read(reg, LEADS + [move, (253.6, "plate", {"slot": "R1", "name": "Raichu", "hp": 20}),
                                     (300.0, "message", {"text": "Sneasler used Close Combat!"}),
                                     (303.0, "plate", {"slot": "R1", "name": "Raichu", "hp": 20})])
    assert sum(e["kind"] == "move" for e in entries) == 1
    assert "instant replay" in a.missed[-1]["why"]


def test_a_status_and_a_turn_lost_to_it(reg):
    entries, a = _read(reg, LEADS + [
        (250.0, "message", {"text": "The opposing Raichu is paralyzed, so it may be unable to move!"}),
        (260.0, "message", {"text": "The opposing Raichu is paralyzed! It can't move!"}),
    ])
    assert entries[5:] == [{"kind": "status", "side": "p2", "slot": 0, "status": "par"},
                           {"kind": "cant", "side": "p2", "slot": 0, "reason": "par"}]
    assert [p["pattern"] for p in a.provisional] == ["cant:par"]


def test_a_forfeit_ends_the_game(reg):
    entries, _ = _read(reg, LEADS + [(830.0, "message", {"text": "The battle has ended due to a forfeit."}),
                                     (831.8, "message", {"text": "The Trainer lost!"})])
    assert entries[-1] == {"kind": "end", "winner": "p1", "by": "forfeit"}


def test_a_fragment_of_ocr_noise_does_not_cut_a_move_off_from_its_damage(reg):
    # Round 6: a stray character read between Rock Slide and the plate that showed its damage.
    entries, a = _read(reg, LEADS + [
        (720.6, "message", {"text": "The opposing Raichu used Volt Tackle!"}),
        (722.6, "message", {"text": "江"}),
        (723.1, "plate", {"slot": "L0", "name": "Sneasler", "hp": 51}),
    ])
    assert entries[5:] == [
        {"kind": "move", "side": "p2", "slot": 0, "move": "Volt Tackle", "target": {"side": "p1", "slot": 0},
         "spread": False},
        {"kind": "damage", "side": "p1", "slot": 0, "pct": 51, "fainted": False}]
    assert a.missed[-1]["why"] == "noise"


def test_the_sheets_given_the_other_way_round_are_put_right_by_the_leads(reg):
    # The right player's sheet was read first, so it is player 1's: the leads say so.
    flipped = {**SETUP, "p1": RIGHT, "p2": LEFT, "names": {"p1": "DORIAN KANG", "p2": "SAMUEL THOMPSON"}}
    a = assemble(reg, flipped, [Event(t, k, d) for t, k, d in LEADS + [
        (249.9, "message", {"text": "Sneasler used Close Combat!"}),
        (253.6, "plate", {"slot": "R1", "name": "Raichu", "hp": 20})]])
    entries = [{k: v for k, v in e.items() if k != "t"} for e in a.entries]
    assert entries[:4] == [
        {"kind": "lead", "side": "p1", "slot": 0, "species": "Raichu"},
        {"kind": "lead", "side": "p1", "slot": 1, "species": "Grimmsnarl"},
        {"kind": "lead", "side": "p2", "slot": 0, "species": "Sneasler"},
        {"kind": "lead", "side": "p2", "slot": 1, "species": "Gardevoir"}]
    assert entries[5:] == [
        {"kind": "move", "side": "p2", "slot": 0, "move": "Close Combat", "target": {"side": "p1", "slot": 0},
         "spread": False},
        {"kind": "damage", "side": "p1", "slot": 0, "pct": 20, "fainted": False}]
    assert a.missed == [] and a.errors == []


def test_the_winner_comes_from_the_board_when_the_names_differ(reg):
    # The broadcast's banner names the player as the broadcast does; the sheet had another name.
    renamed = {**SETUP, "names": {"p1": "sam t", "p2": "dk"}}
    faints = [(300.0 + i, "message", {"text": f"The opposing {sp} fainted!"}) for i, sp in enumerate(("Raichu", "Grimmsnarl"))]
    sendouts = [(310.0, "message", {"text": "The Trainer sent out Archaludon and Charizard!"})]
    more = [(320.0 + i, "message", {"text": f"The opposing {sp} fainted!"}) for i, sp in enumerate(("Archaludon", "Charizard"))]
    a = assemble(reg, renamed, [Event(t, k, d) for t, k, d in
                                LEADS + faints + sendouts + more + [(745.0, "message", {"text": "GAME WINNER SAMUEL THOMPSON"})]])
    assert a.entries[-1]["kind"] == "end" and a.entries[-1]["winner"] == "p1"


def test_intimidate_and_defiant_are_matched_to_their_own_messages(reg):
    # Seen in production: the Intimidate banner settles both Intimidate's drop and Defiant's
    # answer, so Kingambit is at +1 before either message. Neither message is a change of its own.
    setup = {"perspective": "spectator", "names": {"p1": "Enrnsque Grimaldo", "p2": "Alex Madara"},
             "p1": [_set("Floette-Eternal", "Flower Veil", "Modest", "Floettite", ["Moonblast", "Dazzling Gleam", "Calm Mind", "Protect"]),
                    _set("Incineroar", "Intimidate", "Adamant", "Sitrus Berry", ["Fake Out", "Flare Blitz", "Parting Shot", "Throat Chop"])],
             "p2": [_set("Raichu", "Lightning Rod", "Timid", "Raichunite Y", ["Fake Out", "Zap Cannon", "Focus Blast", "Protect"]),
                    _set("Kingambit", "Defiant", "Adamant", "Life Orb", ["Sucker Punch", "Kowtow Cleave", "Swords Dance", "Protect"])]}
    rows = [(26101.5, "message", {"text": "Alex™ sent out Raichu and Kingambit the Paldea Champion!"}),
            (26105.76, "message", {"text": "Go! Floette the Champion-Tiered and Incineroar the Feisty!"}),
            (26109.76, "banner", {"side": "L", "text": "Ancineroar's Intimidate"}),
            (26110.26, "message", {"text": "The opposing Raichu and the opposing Kingambit's Attack fell!"}),
            (26112.01, "banner", {"side": "R", "text": "Kingambit's Defiant"}),
            (26112.51, "message", {"text": "The opposing Kingambit's Attack rose sharply!"}),
            (26114.27, "choosing", {})]
    a = assemble(reg, setup, [Event(t, k, d) for t, k, d in rows])
    assert [e["kind"] for e in a.entries] == ["lead"] * 4 + ["answer", "turn"]
    raichu, kingambit = journal.replay(reg, setup, a.entries).state.sides["p2"].mons
    assert (raichu.boosts.get("atk"), kingambit.boosts.get("atk")) == (-1, 1)
