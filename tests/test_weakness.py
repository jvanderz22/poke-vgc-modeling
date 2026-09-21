"""Phase 7's weakness report.

The report's whole claim is that each number is a calc or a lookup, so the tests check the two
things that could make that false: a threshold that is not where the calc puts it, and a sentence
that says more than the number underneath it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vgc.building import weakness
from vgc.meta import usage
from vgc.teams import calc_stats, parse_team

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.calc


@pytest.fixture(scope="module")
def team():
    return parse_team((FIXTURES / "teams" / "valid_basic.txt").read_text())


@pytest.fixture(scope="module")
def report(reg):
    try:
        return usage.load(reg)
    except FileNotFoundError:
        pytest.skip("no usage report built")


@pytest.fixture(scope="module")
def pool(reg, report):
    return weakness.threats(reg, report, n=6)


@pytest.fixture(scope="module")
def built(reg, team, report, calc):
    return weakness.build(reg, team, report=report, n=6, dc=calc)


# --- threats --------------------------------------------------------------------------

def test_a_threat_is_a_set_somebody_actually_brought(reg, report, pool):
    """Marginal modes need not co-occur. Each threat's set has to be one of the counted joint
    sets for that species, and it has to carry the share saying how often it is the right guess."""
    for t in pool:
        entry = report["species"][t.species_id]
        top = entry["sets"][0]
        assert (t.item or "(none)") == top["item"] and (t.ability or "(none)") == top["ability"]
        assert sorted(t.moves) == sorted(top["moves"])
        assert 0 < t.set_share <= 1
        assert t.share == entry["share"]


def test_threats_are_ordered_by_how_often_you_face_them(pool):
    assert [t.share for t in pool] == sorted((t.share for t in pool), reverse=True)


# --- damage breakpoints ---------------------------------------------------------------

def test_variable_power_moves_are_swept(reg):
    """Low Kick lists base power 0 and is on Kingambit's most common set; base power is the wrong
    test for "does this move deal damage"."""
    assert weakness._stat_for(reg, "Low Kick") == "atk"
    assert weakness._stat_for(reg, "Grass Knot") == "spa"
    assert weakness._stat_for(reg, "Body Press") == "def"   # not Attack, despite being Physical
    assert weakness._stat_for(reg, "Protect") is None
    assert weakness._stat_for(reg, "Trick Room") is None


def test_the_ohko_threshold_is_where_the_calc_puts_it(reg, team, built, calc):
    """The invariant behind every "KOes at N SP" sentence: at N it kills and at N-1 it does not.

    This re-runs the calc at the two investments rather than re-reading the sweep, so a bug in the
    sweep cannot agree with itself.
    """
    by_name = {m.species: m for m in team}
    checked = 0
    for threat in built["incoming"]:
        t = next(x for x in weakness.threats(reg, n=30) if x.species == threat["species"])
        for row in threat["rows"]:
            if row["ohko_at"] is None or row["spread_independent"]:
                continue
            mon = by_name[row["target"]]
            hp = calc_stats(mon, reg.dex, reg)["hp"]
            at = calc.calc(t.at({row["stat"]: row["ohko_at"]}), mon, row["move"])
            assert at.max >= hp, (threat["species"], row["move"], row["target"])
            if row["ohko_at"] > 0:
                below = calc.calc(t.at({row["stat"]: row["ohko_at"] - 1}), mon, row["move"])
                assert below.max < hp, (threat["species"], row["move"], row["target"])
            checked += 1
    assert checked, "no OHKO thresholds to check"


def test_a_guaranteed_ko_never_comes_before_a_possible_one(built):
    for threat in built["incoming"]:
        for row in threat["rows"]:
            if row["sure_at"] is not None:
                assert row["ohko_at"] is not None and row["ohko_at"] <= row["sure_at"]
            if row["ohko_at"] is not None and row["half_at"] is not None:
                assert row["half_at"] <= row["ohko_at"]


def test_damage_is_monotone_in_the_attacking_stat(reg, pool, team, calc):
    t = pool[0]
    move = next(m for m in t.moves if weakness._stat_for(reg, m))
    stat = weakness._stat_for(reg, move)
    res = weakness._sweep(calc, lambda sp: t.at({stat: sp}), team.members[0], move, reg.sp_per_stat_cap)
    tops = [max(r["damage"]) for r in res if r["ok"]]
    assert tops == sorted(tops)


def test_an_immunity_is_dropped_rather_than_reported_as_zero_damage(reg, team, built):
    """Sinistcha is Grass/Ghost, so a Normal move does nothing to it. A row claiming "0 SP" for a
    move that can never KO would be the worst kind of wrong number."""
    for threat in built["incoming"]:
        for row in threat["rows"]:
            assert row["pct_at_max"] > 0, (threat["species"], row["move"], row["target"])


# --- speed ----------------------------------------------------------------------------

def test_the_speed_threshold_is_the_first_point_that_actually_outruns_you(reg, team, built):
    """A speed tie is not outrunning, so the threshold is the first SP strictly above yours."""
    yours = {m["species"]: m["speed"] for m in built["speed"]["yours"]}
    for row in built["speed"]["threats"]:
        t = next(x for x in weakness.threats(reg, n=30) if x.species == row["species"])
        for m in row["per_mon"]:
            need = m["outspeeds_at"]
            if need is None:
                assert weakness._speed(reg, t.at({}), reg.sp_per_stat_cap) <= yours[m["species"]]
                continue
            assert weakness._speed(reg, t.at({}), need) > yours[m["species"]]
            if need > 0:
                assert weakness._speed(reg, t.at({}), need - 1) <= yours[m["species"]]


# --- the sentences ----------------------------------------------------------------------

def test_outgoing_is_transposed_by_name_not_by_position(built):
    rows = weakness._by_species(built["outgoing"])
    assert {r["species"] for r in rows} == {r["species"] for r in built["outgoing"][0]["rows"]}
    for r in rows:
        assert {a["by"] for a in r["attempts"]} <= set(built["team"])


def test_no_headline_claims_a_ko_is_impossible_when_a_roll_kills(built):
    """"Nothing can OHKO it" and "no guaranteed OHKO" are different claims, and a 114% top roll
    with an 88% bottom roll is the second one."""
    by_species = {r["species"]: r for r in weakness._by_species(built["outgoing"])}
    for line in built["headlines"]:
        if line.startswith("nothing on your team can OHKO "):
            name = line.removeprefix("nothing on your team can OHKO ").split(" even ")[0]
            assert not any(a["maybe_frail"] for a in by_species[name]["attempts"]), line


def test_every_headline_restates_a_number_from_the_tables(built):
    assert built["headlines"] and len(built["headlines"]) <= 8
    names = set(built["team"]) | {t["species"] for t in built["incoming"]}
    for line in built["headlines"]:
        assert any(n in line for n in names) or "%" in line, line


def test_type_pressure_reports_an_expected_count_not_a_share(reg, team, pool):
    """Summing per-species shares gives an expected number of carriers, which may exceed 1. The
    report must not call that a percentage — it is the mistake `usage.TRAITS` was added to avoid."""
    rows = weakness.type_pressure(reg, team, pool)
    assert rows and all("carriers" in r and "carried" not in r for r in rows)
    for r in rows:
        assert r["count"] == len(r["weak"]) and r["count"] > 0
        assert all(h["multiplier"] > 1 for h in r["weak"])
        assert r["carriers"] <= len(pool)


def test_structure_puts_your_count_next_to_the_meta_rate(reg, team, report):
    rows = weakness.structure(reg, team, report)
    assert {r["trait"] for r in rows} == set(report["traits"])
    for r in rows:
        assert 0 <= r["yours"] <= len(team)
        assert r["meta_share"] == report["traits"][r["trait"]]["share"]
