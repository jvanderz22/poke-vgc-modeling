"""Phase 8's spread prior: what their 66 points were probably doing before the battle spoke.

The prior cannot be tested against the truth — that is the premise of the phase — so these tests
pin the things that *are* checkable: that the structural facts are read off the dex correctly, that
the tiers degrade instead of failing, and that every prior is a proper distribution which never
rules out something legal.
"""

from __future__ import annotations

import pytest

from pathlib import Path

from vgc.belief import prior

FIXTURES = Path(__file__).parent / "fixtures"


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


# --- sampling a corpus that can gate an inference ---------------------------------------

@pytest.fixture
def rng():
    import numpy as np
    return np.random.default_rng(0)


def test_a_sampled_spread_spends_every_point(reg, rng):
    """Real sheets total exactly 66. An early draft let the last stats hit the cap with budget
    left over, which does not produce a differently built team — it produces a weaker one."""
    for _ in range(300):
        sp = prior.sample_spread(reg, "Salamence", "Timid",
                                 ["Hyper Voice", "Draco Meteor", "Flamethrower"], rng)
        assert sum(sp.values()) == reg.sp_budget
        assert all(0 <= v <= reg.sp_per_stat_cap for v in sp.values())


def test_a_sampled_spread_never_invests_in_a_dead_stat(reg, rng):
    for _ in range(200):
        sp = prior.sample_spread(reg, "Salamence", "Timid", ["Hyper Voice", "Draco Meteor"], rng)
        assert sp["atk"] == 0
        sp = prior.sample_spread(reg, "Rillaboom", "Adamant", ["Wood Hammer", "Fake Out"], rng)
        assert sp["spa"] == 0


def test_the_inferable_stats_come_out_flat(reg, rng):
    """The point of the sampler. A corpus whose hidden truth is uniform is the hardest honest test
    of a channel that has to infer it — if the spreads matched the human prior, a channel could
    score well by echoing the prior instead of reading the battle."""
    moves = ["Hyper Voice", "Draco Meteor", "Flamethrower", "Protect"]
    draws = [prior.sample_spread(reg, "Salamence", "Timid", moves, rng) for _ in range(6000)]
    cap = reg.sp_per_stat_cap
    for stat in ("spe", "spa"):
        vals = [d[stat] for d in draws]
        assert min(vals) == 0 and max(vals) == cap
        assert abs(sum(vals) / len(vals) - cap / 2) < 1.0


def test_a_resampled_team_is_still_legal(reg, rng):
    from vgc.teams import is_legal, parse_team, validate_team

    text = (FIXTURES / "teams" / "valid_basic.txt").read_text()
    for _ in range(25):
        team = parse_team(prior.resample_team(reg, text, rng))
        problems = validate_team(team, reg)
        assert is_legal(problems), problems
        assert all(m.sp.total == reg.sp_budget for m in team)


def test_resampling_changes_the_spread_and_nothing_else(reg, rng):
    from vgc.teams import parse_team

    text = (FIXTURES / "teams" / "valid_basic.txt").read_text()
    before, after = parse_team(text), parse_team(prior.resample_team(reg, text, rng))
    for a, b in zip(before, after):
        assert (a.species, a.item, a.ability, a.nature, a.moves) == \
               (b.species, b.item, b.ability, b.nature, b.moves)
    assert [m.sp.as_dict() for m in before] != [m.sp.as_dict() for m in after]


def test_a_sampled_corpus_does_not_repeat_impute_sps_constant(reg, rng):
    """The pathology this sampler exists to remove, guarded directly.

    All 20,082 Pokémon in the `impute_sp` pool have exactly 32 points in their offensive stat, so
    offensive investment is a constant there and nothing that infers it can be gated. That is also
    not how people build: at higher level a spread is chosen to hit a benchmark, and maxing the
    attacking stat is one option among many. A generated corpus has to show the whole range.
    """
    text = (FIXTURES / "teams" / "valid_basic.txt").read_text()
    from vgc.teams import parse_team

    seen = {"offence": set(), "spe": set()}
    for _ in range(120):
        for mon in parse_team(prior.resample_team(reg, text, rng)):
            seen["offence"].add(max(mon.sp.atk, mon.sp.spa))
            seen["spe"].add(mon.sp.spe)
    for stat, values in seen.items():
        assert len(values) > reg.sp_per_stat_cap * 0.8, (stat, sorted(values))
        assert min(values) == 0 and max(values) == reg.sp_per_stat_cap, stat
