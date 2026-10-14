from __future__ import annotations

import pytest

from vgc.regulation import load_regulation
from vgc.video.said import Vocabulary, Who, banner, parse


def _sheet(*rows: tuple[str, str, list[str]]) -> list[dict]:
    return [{"species": s, "item": i, "moves": m} for s, i, m in rows]


# Louisville round 5, game 1, as the sheet reader read the two sheets off the broadcast.
LEFT = _sheet(("Gardevoir", "Gardevoirite", ["Hyper Voice", "Expanding Force", "Trick Room", "Protect"]),
              ("Gengar", "Gengarite", ["Sludge Bomb", "Shadow Ball", "Focus Blast", "Protect"]),
              ("Arcanine-Hisui", "Focus Sash", ["Flare Blitz", "Head Smash", "Rock Slide", "Protect"]),
              ("Indeedee-F", "Rocky Helmet", ["Helping Hand", "Follow Me", "Psychic", "Trick Room"]),
              ("Milotic", "Leftovers", ["Scald", "Ice Beam", "Haze", "Protect"]),
              ("Sneasler", "Psychic Seed", ["Close Combat", "Rock Slide", "Throat Chop", "Protect"]))
RIGHT = _sheet(("Charizard", "Charizardite Y", ["Heat Wave", "Weather Ball", "Protect", "Hurricane"]),
               ("Raichu", "Raichunite X", ["Fake Out", "Volt Switch", "Feint", "Volt Tackle"]),
               ("Archaludon", "Leftovers", ["Dragon Pulse", "Electro Shot", "Protect", "Snarl"]),
               ("Grimmsnarl", "Light Clay", ["Spirit Break", "Reflect", "Light Screen", "Parting Shot"]),
               ("Politoed", "Mystic Water", ["Weather Ball", "Protect", "Ice Beam", "Muddy Water"]),
               ("Venusaur", "Focus Sash", ["Sleep Powder", "Protect", "Earth Power", "Sludge Bomb"]))


@pytest.fixture(scope="module")
def dex():
    return load_regulation("reg_mc").dex


@pytest.fixture(scope="module")
def vocab(dex):
    return Vocabulary.from_sheets(dex, LEFT, RIGHT)


def test_a_move_names_its_user_and_side(vocab):
    s = parse("The opposing Archaludon used Snarl!", vocab)
    assert (s.kind, s.who, s.data) == ("move", (Who("R", "Archaludon"),), {"move": "Snarl"})
    s = parse("Gardevoir used Hyper Voice!", vocab)
    assert (s.kind, s.who, s.data) == ("move", (Who("L", "Gardevoir"),), {"move": "Hyper Voice"})


def test_the_screen_prints_the_base_species(vocab):
    assert parse("Indeedee used Follow Me!", vocab).who == (Who("L", "Indeedee-F"),)
    assert parse("Arcanine's Sp. Atk fell!", vocab).who == (Who("L", "Arcanine-Hisui"),)


def test_send_outs_with_titles_and_pairs(vocab):
    s = parse("The Trainer sent out Raichu and Grimmsnarl the Treasure Hunter!", vocab)
    assert (s.kind, s.who) == ("sendout", (Who("R", "Raichu"), Who("R", "Grimmsnarl")))
    assert parse("The Trainer sent out Archaludon the Great Friend!", vocab).who == (Who("R", "Archaludon"),)
    assert parse("Go! Sneasler and Gardevoir!", vocab).who == (Who("L", "Sneasler"), Who("L", "Gardevoir"))
    assert parse("Indeedee, come back!", vocab).kind == "withdraw"


def test_one_line_for_two_stat_drops(vocab):
    s = parse("Gardevoir and Indeedee's Sp. Atk fell!", vocab)
    assert (s.kind, s.who, s.data) == ("boost", (Who("L", "Gardevoir"), Who("L", "Indeedee-F")),
                                       {"stats": ["spa"], "stages": -1})
    assert parse("The opposing Archaludon's Defense rose!", vocab).data == {"stats": ["def"], "stages": 1}
    # Two stats in one line, and OCR dropped the "!".
    assert parse("Sneasler's Defense and Sp. Def fell", vocab).data == {"stats": ["def", "spd"], "stages": -1}


def test_effectiveness_faints_and_the_rest(vocab):
    assert parse("It's super effective on the opposing Grimmsnarl!", vocab).who == (Who("R", "Grimmsnarl"),)
    assert parse("It's extremely effective on Sneasler!", vocab).data == {"how": "extremely"}
    assert parse("Gardevoir fainted!", vocab).kind == "faint"
    assert parse("Gardevoir protected itself!", vocab).kind == "protected"
    assert parse("The opposing Raichu was damaged by the recoil!", vocab).who == (Who("R", "Raichu"),)
    assert parse("A critical hit!", vocab).kind == "crit"
    assert parse("The battlefield got weird!", vocab).data == {"terrain": "Psychic Terrain", "on": True}


def test_mega_evolution(vocab):
    s = parse("The opposing Raichu's Raichunite X is reacting to Trainer's Omni Ring!", vocab)
    assert (s.kind, s.who, s.data) == ("mega_stone", (Who("R", "Raichu"),), {"item": "Raichunite X"})
    # The game says "Mega Raichu" whichever stone it was: the forme is the sheet's to say.
    s = parse("The opposing Raichu has Mega Evolved into Mega Raichu!", vocab)
    assert (s.kind, s.who, s.data) == ("mega", (Who("R", "Raichu"),), {})


def test_screens_go_up_on_a_side(vocab):
    s = parse("Light Screen made the opposing side stronger against special moves!", vocab)
    assert (s.kind, s.data) == ("side", {"side": "R", "condition": "Light Screen", "on": True})


def test_an_ocr_slip_in_a_name_still_resolves(vocab):
    assert parse("The opposing Archaludom used Snarl!", vocab).who == (Who("R", "Archaludon"),)


def test_what_does_not_match_stays_unknown(vocab):
    # A move neither sheet has, a name on neither side, and a phrasing the parser doesn't know.
    assert parse("Gardevoir used Moonblast!", vocab).kind == "unknown"
    assert parse("The opposing Gardevoir used Snarl!", vocab).kind == "unknown"
    assert parse("Indeedee's Sp. Atk fel", vocab).kind == "unknown"
    assert parse("Trainers are choosing actions", vocab).kind == "unknown"


def test_banners_name_an_item_or_an_ability(vocab, dex):
    assert banner("Indeedee's Rocky Helmet", "L", vocab, dex).data == {"item": "Rocky Helmet"}
    s = banner("Archaludon's Stamina", "R", vocab, dex)
    assert (s.kind, s.who, s.data) == ("ability", (Who("R", "Archaludon"),), {"ability": "Stamina"})


# --- confirmed and provisional phrasings ----------------------------------------------------

def test_a_phrasing_footage_has_shown_is_confirmed_and_one_it_has_not_is_provisional(vocab):
    assert not parse("Gardevoir used Hyper Voice!", vocab).provisional
    assert not parse("The opposing Raichu was damaged by the recoil!", vocab).provisional
    burn = parse("Sneasler was burned!", vocab)
    assert (burn.kind, burn.data, burn.pattern, burn.provisional) == ("status", {"status": "brn"}, "status:brn", True)


def test_the_main_series_phrasings_read_provisionally(vocab):
    assert parse("Sneasler flinched and couldn't move!", vocab).data == {"reason": "flinch"}
    assert parse("Gardevoir is hurt by its burn!", vocab).data == {"cause": "brn"}
    s = parse("Gardevoir and Sneasler switched places!", vocab)
    assert (s.kind, s.who) == ("swap", (Who("L", "Gardevoir"), Who("L", "Sneasler")))
    assert parse("The opposing Raichu is switched out with the Eject Button!", vocab).kind == "withdraw"
    assert parse("But it failed!", vocab).kind == "failed"
    assert parse("Gardevoir twisted the dimensions!", vocab).data == {"pseudo": "Trick Room", "on": True}


def test_stat_words_ocr_ran_together(vocab):
    assert parse("The opposing Raichu's Sp. Atk harshlyfell!", vocab).data == {"stats": ["spa"], "stages": -2}


def test_a_move_off_a_sheet_read_short_is_read_from_the_learnset(dex, vocab):
    # Round 6: the sheet reader left Knock Off blank on Tyranitar's card.
    right = [{"species": "Tyranitar", "item": "Tyranitarite", "moves": ["Protect", "Rock Slide", "Low Kick"]}]
    v = Vocabulary.from_sheets(dex, LEFT, right)
    s = parse("The opposing Tyranitar used Knock Off!", v)
    assert (s.kind, s.data, s.pattern, s.provisional) == ("move", {"move": "Knock Off"}, "move:off-sheet", True)
    # A sheet with all four moves read keeps to them.
    assert parse("The opposing Raichu used Knock Off!", vocab).kind == "unknown"


def test_how_a_forfeit_reads(vocab):
    assert parse("The battle has ended due to a forfeit.", vocab).kind == "forfeit_notice"
    s = parse("The Trainer lost!", vocab)
    assert (s.kind, s.data, s.provisional) == ("lost", {"winner": "L"}, False)
