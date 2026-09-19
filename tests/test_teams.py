from __future__ import annotations

import pytest

from vgc.teams import calc_stats, export_team, is_legal, parse_team, validate_team
from vgc.teams.showdown_text import TeamParseError, parse_set


def codes(problems, severity="error"):
    return {p.code for p in problems if p.severity == severity}


# --- parsing --------------------------------------------------------------------------

def test_parse_valid_team(team_text):
    team = parse_team(team_text("valid_basic"))
    assert len(team) == 6
    rilla = team.members[0]
    assert (rilla.species, rilla.item, rilla.ability, rilla.nature) == ("Rillaboom", "Miracle Seed", "Grassy Surge", "Adamant")
    assert rilla.sp.as_dict() == {"hp": 32, "atk": 32, "def": 0, "spa": 0, "spd": 0, "spe": 2}
    assert rilla.moves == ["Fake Out", "Grassy Glide", "Wood Hammer", "U-turn"]


def test_round_trip(team_text):
    team = parse_team(team_text("valid_basic"))
    again = parse_team(export_team(team))
    assert [m.__dict__ for m in again] == [m.__dict__ for m in team]


def test_parse_header_variants():
    mon = parse_set("Chompy (Garchomp) (F) @ Life Orb\nAbility: Rough Skin\nSPs: 32 Atk / 32 Spe / 2 HP\n- Earthquake")
    assert (mon.nickname, mon.species, mon.gender, mon.item) == ("Chompy", "Garchomp", "F", "Life Orb")
    assert mon.sp.total == 66


def test_parse_rejects_garbage_stats():
    with pytest.raises(TeamParseError):
        parse_set("Garchomp\nEVs: lots Atk")


# --- validation -----------------------------------------------------------------------

def test_valid_team_is_legal(reg, team_text):
    problems = validate_team(parse_team(team_text("valid_basic")), reg)
    assert is_legal(problems), problems


@pytest.mark.parametrize(
    "fixture, code",
    [
        ("bad_dup_item", "item_clause"),
        ("bad_67_sp", "sp_budget"),
        ("bad_33_in_stat", "sp_stat_cap"),
        ("bad_tera", "tera_disabled"),
        ("bad_illegal_species", "species_illegal"),
    ],
)
def test_bad_fixtures(reg, team_text, fixture, code):
    assert code in codes(validate_team(parse_team(team_text(fixture)), reg))


def _mutate(team_text, old, new):
    text = team_text("valid_basic")
    assert old in text
    return parse_team(text.replace(old, new, 1))


def test_unlearnable_move(reg, team_text):
    # Champions learnsets differ from SV: Incineroar has no Knock Off here.
    assert "move_unlearnable" in codes(validate_team(_mutate(team_text, "- Snarl", "- Knock Off"), reg))


def test_illegal_item(reg, team_text):
    assert "item_illegal" in codes(validate_team(_mutate(team_text, "Miracle Seed", "Choice Band"), reg))


def test_wrong_ability(reg, team_text):
    assert "ability_illegal" in codes(validate_team(_mutate(team_text, "Ability: Grassy Surge", "Ability: Intimidate"), reg))


def test_species_clause_counts_formes(reg, team_text):
    text = team_text("valid_basic").replace(
        "Kingambit @ Black Glasses\nAbility: Defiant", "Indeedee @ Black Glasses\nAbility: Psychic Surge"
    ).replace("Sinistcha @ Leftovers\nAbility: Hospitality", "Indeedee-F @ Leftovers\nAbility: Psychic Surge")
    assert "species_clause" in codes(validate_team(parse_team(text), reg))


def test_team_size(reg, team_text):
    team = parse_team(team_text("valid_basic"))
    team.members.pop()
    assert "team_size" in codes(validate_team(team, reg))


def test_ivs_fixed(reg, team_text):
    assert "ivs_fixed" in codes(validate_team(_mutate(team_text, "Adamant Nature", "Adamant Nature\nIVs: 0 Spe"), reg))


def test_mega_forme_listed_directly(reg, team_text):
    problems = validate_team(_mutate(team_text, "Salamence @ Salamencite", "Salamence-Mega @ Salamencite"), reg)
    assert is_legal(problems) and "battle_only_forme" in codes(problems, "warning")


def test_mega_stone_on_wrong_holder_warns(reg, team_text):
    text = team_text("valid_basic").replace("Sinistcha @ Leftovers", "Sinistcha @ Garchompite Z")
    problems = validate_team(parse_team(text), reg)
    assert "mega_stone_holder" in codes(problems, "warning")


def test_unspent_sp_warns(reg, team_text):
    problems = validate_team(_mutate(team_text, "EVs: 32 HP / 32 Atk / 2 Spe", "EVs: 32 HP / 32 Atk"), reg)
    assert is_legal(problems) and "sp_unspent" in codes(problems, "warning")


def test_duplicate_move(reg, team_text):
    assert "move_duplicate" in codes(validate_team(_mutate(team_text, "- Wood Hammer", "- Fake Out"), reg))


# --- stats ----------------------------------------------------------------------------

def test_stats_champions_formula(reg, team_text):
    team = parse_team(team_text("valid_basic"))
    rilla = team.members[0]  # base 100/125/90/60/70/85, 32 HP / 32 Atk / 2 Spe, Adamant
    assert calc_stats(rilla, reg.dex, reg) == {"hp": 207, "atk": 194, "def": 110, "spa": 72, "spd": 90, "spe": 107}


def test_stats_mega_forme(reg, team_text):
    mence = parse_team(team_text("valid_basic")).members[3]
    mega = calc_stats(mence, reg.dex, reg, species_id="salamencemega")
    assert mega["spe"] == 189 and mega["hp"] == 172
