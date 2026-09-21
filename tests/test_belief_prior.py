"""Phase 8's spread prior: what their 66 points were probably doing before the battle spoke.

The prior cannot be tested against the truth — that is the premise of the phase — so these tests
pin the things that *are* checkable: that the structural facts are read off the dex correctly, that
the tiers degrade instead of failing, and that every prior is a proper distribution which never
rules out something legal.
"""

from __future__ import annotations

import pytest

from vgc.belief import prior


# --- structural zeros -------------------------------------------------------------------

def test_a_stat_no_move_uses_is_dead(reg):
    dead = prior.dead_stats(reg, ["Hyper Voice", "Draco Meteor", "Protect"], "Timid")
    assert set(dead) == {"atk"}
    assert "nature lowers it" in dead["atk"]           # Timid confirms it
    assert prior.dead_stats(reg, ["Flare Blitz", "Hyper Voice"], "Serious") == {}


def test_body_press_does_not_rescue_a_dead_attack(reg):
    """Body Press is a Physical move whose damage scales off Defence, so a Pokémon carrying only
    it still gains nothing from Attack. Reading the category instead of the stat would miss that."""
    assert "atk" in prior.dead_stats(reg, ["Body Press", "Protect"], "Bold")


def test_foul_play_does_not_rescue_a_dead_attack_either(reg):
    """Foul Play scales off the *target's* Attack. `offensive_stat` reports `atk` for it, so this
    is the case the sweep-is-flat detection covers rather than this one — pinned so a future
    change to either side is deliberate."""
    from vgc.regulation import offensive_stat
    assert offensive_stat(reg.dex, "Foul Play") == "atk"


def test_live_stat_count_drives_the_structural_prior(reg):
    assert prior.live_stats(reg, ["Hyper Voice", "Protect"], "Timid") == 5
    assert prior.live_stats(reg, ["Flare Blitz", "Hyper Voice"], "Serious") == 6


# --- the allocation count ---------------------------------------------------------------

def test_the_structural_prior_is_a_distribution_over_legal_spreads(reg):
    for live in (5, 6):
        mass = prior.structural_mass(live, reg.sp_budget, reg.sp_per_stat_cap)
        assert len(mass) == reg.sp_per_stat_cap + 1
        assert sum(mass) == pytest.approx(1.0)
        assert all(m > 0 for m in mass)


def test_a_dead_stat_makes_speed_cheaper(reg):
    """The whole point of the structural zeros: with one fewer competitor for the 66 points, high
    Speed is more affordable and the marginal shifts up on its own, with nothing tuned."""
    six = prior.structural_mass(6, reg.sp_budget, reg.sp_per_stat_cap)
    five = prior.structural_mass(5, reg.sp_budget, reg.sp_per_stat_cap)
    mean = lambda m: sum(i * x for i, x in enumerate(m))  # noqa: E731
    assert mean(five) > mean(six)
    assert five[reg.sp_per_stat_cap] > six[reg.sp_per_stat_cap]


def test_allocations_counts_what_it_claims(reg):
    # Two stats, budget 3, cap 32: (0,3) (1,2) (2,1) (3,0).
    assert prior._allocations(2, 3, 32) == 4
    # The cap has to bind: two stats, budget 10, cap 6 -> 4..6 each, five ways.
    assert prior._allocations(2, 10, 6) == 3


# --- benchmarks and classes -------------------------------------------------------------

def test_speed_classes_partition_the_budget(reg):
    marks, _ = prior.benchmarks(reg)
    classes = prior.speed_classes(reg, "Rillaboom", "Adamant", marks)
    members = [sp for c in classes for sp in c["members"]]
    assert sorted(members) == list(range(reg.sp_per_stat_cap + 1))
    assert all(c["min_sp"] == min(c["members"]) for c in classes)


def test_the_first_class_is_free(reg):
    """Clearing something at zero investment is not a reason to choose zero."""
    marks, _ = prior.benchmarks(reg)
    classes = prior.speed_classes(reg, "Incineroar", "Careful", marks)
    assert classes[0]["gain"] == 0.0
    assert all(c["gain"] >= 0 for c in classes)


def test_classes_are_fewer_than_the_values_that_make_them(reg):
    """33 investments, far fewer distinct outcomes — the compression the prior is built on."""
    marks, _ = prior.benchmarks(reg)
    for who, nat in (("Rillaboom", "Adamant"), ("Incineroar", "Careful"), ("Garchomp", "Jolly")):
        assert len(prior.speed_classes(reg, who, nat, marks)) < reg.sp_per_stat_cap + 1


# --- tiers, and the fallback a rotation needs -------------------------------------------

def test_the_usage_tier_is_used_when_a_corpus_exists(reg):
    marks, tier = prior.benchmarks(reg)
    assert tier in prior.TIERS and marks


def test_it_falls_back_to_the_legal_pool_with_no_corpus(reg):
    """A regulation that has just rotated has no usage report, and the belief layer must not go
    offline because of it — it degrades to benchmarks from the legal species list."""
    marks, tier = prior.benchmarks(reg, report={})
    assert tier == "pool" and marks
    assert sum(w for _, w in marks) == pytest.approx(2.0, abs=0.01)  # each species at 0 and at 32


def test_the_structural_tier_needs_nothing_at_all(reg):
    p = prior.speed_prior(reg, "Rillaboom", "Adamant", ["Wood Hammer"], tier="structural")
    assert p.tier == "structural" and p.classes == 0
    assert sum(p.mass) == pytest.approx(1.0)


def test_every_prior_is_proper_and_rules_nothing_out(reg):
    marks, tier = prior.benchmarks(reg)
    moves = ["Fake Out", "Grassy Glide", "Wood Hammer", "U-turn"]
    made = [
        prior.flat_prior(reg, "Rillaboom", "Adamant"),
        prior.speed_prior(reg, "Rillaboom", "Adamant", moves, tier="structural"),
        prior.speed_prior(reg, "Rillaboom", "Adamant", moves, marks=marks, tier=tier),
    ]
    for p in made:
        assert sum(p.mass) == pytest.approx(1.0)
        assert all(m > 0 for m in p.mass), p.tier
        assert p.mass_in(range(reg.sp_per_stat_cap + 1)) == pytest.approx(1.0)


def test_impute_sp_as_a_prior_is_a_point_mass(reg):
    """Recorded because it is what the entire self-play corpus was generated from, and because a
    point mass that lands wrong assigns near-zero probability to what actually happened."""
    p = prior.imputed_prior(reg, "Rillaboom", "Adamant", ["Fake Out", "Grassy Glide", "Wood Hammer"])
    assert max(p.mass) > 0.99
    assert sum(1 for m in p.mass if m > 0.01) == 1
    assert all(m > 0 for m in p.mass)  # floored, so a log-likelihood stays finite
