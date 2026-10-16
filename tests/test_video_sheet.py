from __future__ import annotations

from pathlib import Path

import pytest

from vgc.regulation import load_regulation
from vgc.video.sheet import Line, assemble, match, problems, vocabulary

FIXTURES = Path(__file__).parent / "fixtures" / "sheets"


@pytest.fixture(scope="module")
def reg():
    return load_regulation("reg_mc")


@pytest.fixture(scope="module")
def vocab(reg):
    return vocabulary(reg.dex)


# --- matching -------------------------------------------------------------------------------

def test_exact_names_match_their_kind(vocab):
    assert match("Emergency Exit", vocab).kind == "ability"
    assert match("Adamant", vocab).kind == "nature"
    assert match("Raichunite Y", vocab).name == "Raichunite Y"
    assert match("Sucker Punch", vocab).kind == "move"


def test_a_sheet_spells_formes_its_own_way(vocab):
    assert match("BASCULEGION (FEMALE)", vocab).name == "Basculegion-F"
    assert match("BASCULEGION", vocab).name == "Basculegion"
    # The base name, when only one of its formes is legal: not the nearest item (Floettite).
    assert (match("FLOETTE", vocab).kind, match("FLOETTE", vocab).name) == ("species", "Floette-Eternal")


def test_a_forme_named_in_brackets_is_that_forme(vocab):
    assert match("FLOETTE (ETERNAL FLOWER)", vocab).name == "Floette-Eternal"
    assert match("TAUROS (AQUA BREED)", vocab).name == "Tauros-Paldea-Aqua"
    assert match("ROTOM (WASH ROTOM)", vocab).name == "Rotom-Wash"
    assert match("LYCANROC (MIDNIGHT FORM)", vocab).name == "Lycanroc-Midnight"
    # The base's own forme name isn't in the dex: it reads as the base, as "(MALE)" does. Read
    # whole, "basculegionmale" was nearest to "basculegionfemale".
    assert match("LYCANROC (MIDDAY FORM)", vocab).name == "Lycanroc"
    assert match("BASCULEGION (MALE)", vocab).name == "Basculegion"
    # Named as well as each other: not guessed.
    assert match("TAUROS (PALDEAN FORM)", vocab) is None


def test_a_gender_is_read_only_when_spelled(vocab):
    # Male and female are different Pokémon. Each reads from its own word, and a misread of
    # either is left unread: never the other gender, and never the base by default.
    for species in ("Basculegion", "Indeedee", "Meowstic"):
        up = species.upper()
        assert match(f"{up} (MALE)", vocab).name == species
        assert match(f"{up} (FEMALE)", vocab).name == f"{species}-F"
        for garbled in ("FEMAIE", "EMALE", "MAIE", "FORM", ""):
            assert match(f"{up} ({garbled})", vocab) is None, (species, garbled)


def test_a_mega_forme_is_never_a_sheet_species(vocab):
    assert all(kind != "species" for kind, _ in vocab.get("raichumegay", []))


def test_a_misread_close_to_one_name_commits(vocab):
    m = match("Electro Shat", vocab)
    assert (m.name, m.kind) == ("Electro Shot", "move") and m.ratio < 1


def test_a_misread_is_never_guessed(vocab):
    # From the same screenshot downscaled to 360p: no name wins clearly, so nothing commits.
    for garbled in ("Aak", "5.Ak", "Pratedt", "Fecan Sask", "Zany lus", "A ltak"):
        assert match(garbled, vocab) is None, garbled


# --- layout ---------------------------------------------------------------------------------

def _card(x: float, y: float, species: str, ability: str, nature: str, moves: list[str], item: str) -> list[Line]:
    """A card laid out like the broadcast graphic: name, ability and nature row, moves, item."""
    out = [Line(species, 1, x, y, x + 110, y + 30), Line(ability, 1, x - 20, y + 48, x + 90, y + 74),
           Line(nature, 1, x + 110, y + 48, x + 180, y + 74), Line("Aak", 0.8, x + 200, y + 46, x + 265, y + 72)]
    out += [Line(m, 1, x + 25, y + 87 + 40 * i, x + 130, y + 113 + 40 * i) for i, m in enumerate(moves)]
    return out + [Line(item, 1, x + 260, y + 203, x + 325, y + 226)]


def test_lines_are_grouped_into_cards_by_position(reg):
    lines = [Line("CARSON CONFER", 1, 1158, 42, 1501, 106), Line("SECR", 1, 313, 220, 400, 249)]
    lines += _card(896, 139, "GOLISOPOD", "Emergency Exit", "Adamant",
                   ["Leech Life", "Iron Head", "Sucker Punch", "Protect"], "Golisopite")
    lines += _card(1295, 139, "RAICHU", "Lightning Rod", "Timid",
                   ["Zap Cannon", "Focus Blast", "Fake Out", "Protect"], "Raichunite Y")
    lines += _card(894, 404, "ARCHALUDON", "Stamina", "Bold",
                   ["Electro Shot", "Dragon Pulse", "Flash Cannon", "Protect"], "Leftovers")
    read = assemble(lines, reg.dex)
    assert read.title == "CARSON CONFER"
    assert [e.species for e in read.entries] == ["Golisopod", "Raichu", "Archaludon"]
    raichu = read.entries[1]
    assert (raichu.ability, raichu.nature, raichu.item) == ("Lightning Rod", "Timid", "Raichunite Y")
    assert raichu.moves == ["Zap Cannon", "Focus Blast", "Fake Out", "Protect"]
    # The stat labels after a nature are dropped; the facecam's text is off the sheet.
    assert read.unread == [] and read.notes == []


def test_a_card_printed_with_the_base_name_is_read_whole(reg):
    lines = [Line("SECR", 1, 313, 220, 400, 249)]
    lines += _card(896, 139, "FLOETTE", "Flower Veil", "Modest",
                   ["Light of Ruin", "Moonblast", "Calm Mind", "Protect"], "Floettite")
    read = assemble(lines, reg.dex)
    [floette] = read.entries
    assert (floette.species, floette.ability, floette.nature, floette.item) == \
        ("Floette-Eternal", "Flower Veil", "Modest", "Floettite")
    assert floette.moves == ["Light of Ruin", "Moonblast", "Calm Mind", "Protect"]


def test_an_unreadable_field_is_left_blank_and_reported(reg):
    lines = _card(896, 139, "GOLISOPOD", "Emergency Exit", "Adamant",
                  ["Leech Life", "Irsn ilead", "Sucker Punch", "Protect"], "Golisopite")
    read = assemble(lines, reg.dex)
    assert read.entries[0].moves == ["Leech Life", "Sucker Punch", "Protect"]
    assert [ln.text for ln in read.unread] == ["Irsn ilead"]
    assert read.notes == ["Golisopod: missing 1 move(s)"]


def test_no_species_means_no_sheet(reg):
    read = assemble([Line("Protect", 1, 0, 0, 60, 20)], reg.dex)
    assert read.entries == [] and read.notes


# --- a real screenshot ----------------------------------------------------------------------

BROADCAST_CONFER = """\
Golisopod @ Golisopite
Ability: Emergency Exit
Adamant Nature
- Leech Life
- Iron Head
- Sucker Punch
- Protect

Raichu @ Raichunite Y
Ability: Lightning Rod
Timid Nature
- Zap Cannon
- Focus Blast
- Fake Out
- Protect

Archaludon @ Leftovers
Ability: Stamina
Bold Nature
- Electro Shot
- Dragon Pulse
- Flash Cannon
- Protect

Basculegion-F @ Focus Sash
Ability: Swift Swim
Modest Nature
- Muddy Water
- Shadow Ball
- Ice Beam
- Protect

Pelipper @ Damp Rock
Ability: Drizzle
Bold Nature
- Weather Ball
- Hurricane
- Helping Hand
- Wide Guard

Sneasler @ White Herb
Ability: Unburden
Adamant Nature
- Dire Claw
- Close Combat
- Rock Slide
- Protect
"""


BROADCAST_GRIMALDO = """\
Floette-Eternal @ Floettite
Ability: Flower Veil
Modest Nature
- Calm Mind
- Moonblast
- Dazzling Gleam
- Protect

Archaludon @ Leftovers
Ability: Stamina
Modest Nature
- Electro Shot
- Dragon Pulse
- Protect
- Flash Cannon

Basculegion @ Choice Scarf
Ability: Adaptability
Jolly Nature
- Wave Crash
- Last Respects
- Phantom Force
- Flip Turn

Incineroar @ Sitrus Berry
Ability: Intimidate
Adamant Nature
- Flare Blitz
- Fake Out
- Parting Shot
- Throat Chop

Pelipper @ Focus Sash
Ability: Drizzle
Modest Nature
- Hurricane
- Weather Ball
- Tailwind
- Wide Guard

Sinistcha @ Colbur Berry
Ability: Hospitality
Calm Nature
- Matcha Gotcha
- Rage Powder
- Trick Room
- Protect
"""


def test_broadcast_team_list(reg):
    """A broadcast team-list frame, facecam included, against its sheet transcribed by hand."""
    pytest.importorskip("rapidocr")
    from vgc.video.sheet import read_sheet

    read = read_sheet(FIXTURES / "broadcast-confer.jpg", reg)
    assert read.title == "CARSON CONFER"
    assert read.showdown() == BROADCAST_CONFER
    assert read.unread == [] and read.notes == []
    assert problems(read, reg) == []


def test_broadcast_team_list_with_formes_in_brackets(reg):
    """The frame flagged in prod: "FLOETTE (ETERNAL FLOWER)" dropped its card, and "BASCULEGION
    (MALE)" read as Basculegion-F."""
    pytest.importorskip("rapidocr")
    from vgc.video.sheet import read_sheet

    read = read_sheet(FIXTURES / "broadcast-grimaldo.jpg", reg)
    assert read.title == "ENRIQUE GRIMALDO"
    assert read.showdown() == BROADCAST_GRIMALDO
    assert read.notes == []
    assert problems(read, reg) == []
