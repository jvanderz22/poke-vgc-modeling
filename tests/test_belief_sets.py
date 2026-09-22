"""The ranking half of the belief, gated on different terms from every other channel.

`speed`, `damage`, `bulk` and `sp` produce **bounds**: the truth must never leave the feasible
set, and anything else is a bug. This one produces a **ranking** from 15,000 other people's
sheets, and it can be wrong — but only about the order, never about what is possible. So the
tests here pin one property above all the others:

**It may never make anything impossible.** Only a sound observation can do that. Every legal
option keeps a non-zero share however rare it is, because somebody is always running the thing
nobody runs — measured at 1.000 in support over 19,677 held-out Pokémon.
"""

from __future__ import annotations

import random

import pytest

from vgc.battle.state import Mon
from vgc.belief import sets


@pytest.fixture(scope="module")
def built(reg):
    if not sets.prior_path(reg).exists():
        pytest.skip("no set prior built (vgc belief sets --build)")
    return sets.corpus(reg.id)


def mon(species, **kw):
    m = Mon(species)
    for k, v in kw.items():
        setattr(m, k, v)
    return m


# --- the property that matters -----------------------------------------------------------------

def test_usage_never_makes_a_legal_ability_impossible(reg, built):
    """Blaze is 0.3% of Incineroar. It is not 0%, and the difference is the whole contract: a
    ranking that zeroed it would be a ranking that can be wrong about what is *possible*."""
    p = sets.for_species(reg, "Incineroar").ability()
    assert set(p) == {"intimidate", "blaze"}
    assert p["intimidate"] > 0.9 and 0 < p["blaze"] < 0.01
    assert abs(sum(p.values()) - 1.0) < 1e-9


def test_a_species_nobody_has_brought_is_flat_rather_than_empty(reg, built):
    """293 species are legal and 246 appear in the corpus. The rest are not impossible."""
    unseen = next((s["name"] for s in reg.dex.species.values()
                   if s.get("name") and not built.get(s["name"].lower().replace("-", "").replace(" ", ""))
                   and len(sets.legal_abilities(reg, s["name"])) > 1), None)
    if unseen is None:
        pytest.skip("every legal species appears in the corpus")
    b = sets.for_species(reg, unseen)
    p = b.ability()
    assert b.seen == 0 and not b.sheets
    assert len(set(p.values())) == 1 and all(v > 0 for v in p.values())


def test_only_a_sound_observation_removes_an_option(reg, built):
    """An ability that would have announced and did not is ruled out by the *rules*, and that is
    the only kind of thing allowed to zero anything here."""
    m = mon("Torkoal", ability_ruled_out={"drought"})
    p = sets.given(reg, m).ability()
    assert "drought" not in p
    assert set(p) == {"shellarmor", "whitesmoke"} and all(v > 0 for v in p.values())


# --- conditioning -------------------------------------------------------------------------------

def test_seeing_the_item_narrows_the_moves(reg, built):
    """Not independent: a Rocky Helmet Incineroar runs a different four from a Sitrus one, and
    that is the joint doing work a set of marginals could not."""
    flat = sets.for_species(reg, "Incineroar")
    held = sets.given(reg, mon("Incineroar", item="rockyhelmet", item_source="revealed"))
    assert held.entropy < flat.entropy
    assert len(held.sheets) < len(flat.sheets)
    assert "item is rockyhelmet" in held.evidence


def test_a_consumed_item_is_still_an_item_it_held(reg, built):
    """It ate the berry. That tells you as much about the rest of the set as still holding it."""
    eaten = sets.given(reg, mon("Incineroar", item="", lost_item="sitrusberry"))
    assert "item is sitrusberry" in eaten.evidence
    assert all(s.item == "sitrusberry" for s in eaten.sheets)


def test_a_move_you_watched_narrows_the_rest(reg, built):
    seen = sets.given(reg, mon("Incineroar", moves_used=["fakeout"]))
    assert seen.entropy < sets.for_species(reg, "Incineroar").entropy
    assert all("fakeout" in s.moves for s in seen.sheets)


def test_an_open_sheet_collapses_it_to_one_set_but_not_to_a_spread(reg, built):
    """Open Team Sheets gives the four moves, the item, the ability and the nature. It does not
    give the Stat Points, in any regime — which is why this module stops where it does."""
    best = sets.for_species(reg, "Incineroar").top()
    m = mon("Incineroar", item=best.item, ability=best.ability, nature=best.nature,
            moves=list(best.moves))
    b = sets.given(reg, m)
    assert len(b.sheets) == 1 and b.sheets[0] == best
    assert b.entropy == 0.0


# --- backing off --------------------------------------------------------------------------------

def test_a_set_nobody_has_run_backs_off_rather_than_emptying(reg, built):
    """An opponent running a move nobody has been recorded running is a real opponent. Reporting
    them as impossible is the failure mode this whole package exists to avoid, so the constraints
    are given up one at a time until something matches — and the belief says which."""
    odd = sets.given(reg, mon("Incineroar", moves_used=["knockoff"]))
    assert odd.off_meta and odd.sheets
    assert any("ignoring moves" in e for e in odd.evidence)
    # The support is never widened back by a backoff: the rules still bound it.
    ruled = sets.given(reg, mon("Incineroar", moves_used=["knockoff"], ability_ruled_out={"intimidate"}))
    assert "intimidate" not in ruled.ability()


# --- what it is for -------------------------------------------------------------------------------

def test_the_likeliest_option_is_usually_the_right_one(reg, built):
    """The pop-up's job. Held out by team over 19,677 Pokémon the prior had never seen, the top
    ability is right 94.3% of the time against 59.1% alphabetical
    (`scripts/analysis/set_belief.py`); this pins the shape rather than re-running it."""
    for species, want in [("Incineroar", "intimidate"), ("Torkoal", "drought"),
                          ("Rillaboom", "grassysurge")]:
        ranked = sets.for_species(reg, species).rank(sets.legal_abilities(reg, species))
        assert ranked[0][0] == want, species
        assert ranked[0][1] > 0.5


def test_particles_are_drawn_in_proportion_and_not_deduplicated(reg, built):
    """A sample, so its mean estimates an expectation. Deduplicating would turn it into a list of
    what is possible, which is a different object and not the one a WP average needs."""
    b = sets.for_species(reg, "Incineroar")
    drawn = b.particles(random.Random(3), 400)
    assert len(drawn) == 400
    top = b.top()
    share = sum(1 for s in drawn if s == top) / len(drawn)
    assert abs(share - b.concentration) < 0.06
    assert len(set(drawn)) > 1


def test_the_mode_is_a_minority_of_the_belief(reg, built):
    """The number that makes a point estimate the wrong object: Incineroar's single most common
    set is under a fifth of its sheets, so evaluating it answers a question about a set five
    opponents in six are not holding."""
    assert sets.for_species(reg, "Incineroar").concentration < 0.25


def test_no_spreads_anywhere(reg, built):
    """Sheets do not carry Stat Points and imputing them scored worse than assuming nothing, so
    nothing here may offer one. The spread comes from `vgc.belief.sp`, which reads this battle."""
    b = sets.for_species(reg, "Incineroar")
    assert not hasattr(b.top(), "sp")
    assert "sp" not in b.to_json() and "spread" not in b.to_json()
