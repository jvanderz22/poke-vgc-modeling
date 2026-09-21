"""The two channels joined by the budget: does the whole 66-point allocation stay sound?

The channels have their own tests. These pin the thing that only exists once they are put
together — that a bound on Speed and a bound on Attack are bounds on the *same* 66 points, so the
pair says something about bulk that neither said alone — and the arithmetic underneath it, which
is exact and therefore checkable against brute force rather than against a tolerance.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from vgc.belief import prior, sp
from vgc.belief.damage import DamageBelief
from vgc.belief.speed import SpeedBelief

STATS = ("hp", "atk", "def", "spa", "spd", "spe")


def _speed(feasible, used=1):
    return SpeedBelief(side="p2", species="X", nature=None, feasible=list(feasible),
                       prior=list(range(33)), used=used)


def _damage(stat, feasible, used=1):
    return DamageBelief(side="p2", species="X", stat=stat, feasible=list(feasible),
                        prior=list(range(33)), used=used)


def _combine(reg, moves=("Flare Blitz", "Protect"), nature="Adamant", **kw):
    return sp.combine(reg, ("p2", "Rillaboom"), nature, list(moves), **kw)


# --- the dynamic program, against brute force -------------------------------------------

def _brute(ranges, budget):
    return [c for c in itertools.product(*ranges) if sum(c) <= budget]


def test_counting_marginals_and_sampling_all_match_an_exhaustive_enumeration():
    """Small enough to enumerate, so the DP is checked against the answer rather than itself."""
    ranges = [range(4), range(2), range(4)]
    budget = 4
    blocks = [sp.Block.over("a", ranges[0]), sp.Block.over("b", ranges[1]),
              sp.Block.over("c", ranges[2]), sp.Block.over(sp.SLACK, range(budget + 1))]
    truth = _brute(ranges, budget)

    assert sp.count(blocks, budget) == len(truth)

    mass = sp.marginals(blocks, budget)
    for i, stat in enumerate("abc"):
        for v in ranges[i]:
            want = sum(1 for c in truth if c[i] == v) / len(truth)
            assert mass[stat][v] == pytest.approx(want)

    drawn = sp.sample(blocks, budget, np.random.default_rng(0), 4000)
    assert all(tuple(p[s] for s in "abc") in set(truth) for p in drawn)
    seen = sum(1 for p in drawn if p["a"] == 0) / len(drawn)
    assert seen == pytest.approx(mass["a"][0], abs=0.03)


def test_the_joint_marginal_reproduces_the_structural_prior(reg):
    """`prior.structural_mass` counts allocations of a spent budget over the live stats; this
    counts them one block at a time. They are the same statement, so they have to agree exactly —
    and if they ever stop, one of the two has changed its mind about what a legal spread is."""
    blocks, dead = sp.structural_blocks(reg, ["Hyper Voice", "Protect"], "Timid",
                                        dead_zero=True, spend_all=True)
    assert set(dead) == {"atk"}
    mass = sp.marginals(blocks, reg.sp_budget)
    want = prior.structural_mass(5, reg.sp_budget, reg.sp_per_stat_cap)
    for v, w in enumerate(want):
        assert mass["spe"][v] == pytest.approx(w)


def test_a_block_may_hold_two_stats_at_once():
    """A defensive observation constrains HP and Defence together, and the pair of projections is
    strictly weaker than the region — so the representation has to carry regions."""
    budget = 6
    pairs = [(h, d) for h in range(4) for d in range(4) if h + d == 3]
    blocks = [sp.Block.joint(("hp", "def"), pairs), sp.Block.over(sp.SLACK, range(budget + 1))]
    assert sp.count(blocks, budget) == len(pairs)
    drawn = sp.sample(blocks, budget, np.random.default_rng(1), 50)
    assert all(p["hp"] + p["def"] == 3 for p in drawn)


# --- what the budget adds ----------------------------------------------------------------

def test_two_offensive_bounds_bound_their_bulk(reg):
    """The claim of this module. Neither channel observes HP, Defence or Special Defence at all;
    shown 24+ Speed and 28+ Attack, the budget leaves at most 14 points for all three."""
    belief = _combine(reg, speed_belief=_speed(range(24, 33)),
                      damage_belief=_damage("atk", range(28, 33)))
    bulk = ("hp", "def", "spd")
    cap = reg.sp_per_stat_cap
    assert belief.spent_on(bulk) == (reg.sp_budget - cap - cap, reg.sp_budget - 24 - 28)
    # Either bound alone already says something, through the same budget — which is itself a
    # statement neither channel's own report makes. Together they compound, and the joint bound is
    # strictly tighter than both.
    speed_only = _combine(reg, speed_belief=_speed(range(24, 33)))
    damage_only = _combine(reg, damage_belief=_damage("atk", range(28, 33)))
    assert speed_only.spent_on(bulk)[1] == reg.sp_budget - 24
    assert damage_only.spent_on(bulk)[1] == reg.sp_budget - 28
    assert belief.spent_on(bulk)[1] < min(speed_only.spent_on(bulk)[1],
                                          damage_only.spent_on(bulk)[1])
    # Nothing observed, and only the budget's own floor is left.
    assert _combine(reg).spent_on(bulk) == (reg.sp_budget - cap - cap, reg.sp_budget)


def test_bulk_is_all_the_budget_can_narrow_from_these_two_channels(reg):
    """Worth pinning because it is not obvious and it decides what to build next: the cap is 32 a
    stat and the budget is 66, so Speed and one offensive stat can *both* be maxed (64 ≤ 66) and
    no observation of the two can ever rule out a value of either. Everything the budget adds
    lands on bulk. A channel that bounds bulk from below is what would make it point the other
    way."""
    belief = _combine(reg, speed_belief=_speed([32]), damage_belief=_damage("atk", [32]),
                      spend_all=True)
    assert belief.bounds()["spe"] == (32, 32) and belief.bounds()["atk"] == (32, 32)
    assert belief.spent_on(("hp", "def", "spd")) == (2, 2)
    assert belief.contradicted is None


def test_the_two_channels_cannot_contradict_each_other_under_the_budget_yet(reg):
    """The guard in `combine` exists for the belief's stated rule — never ship an empty feasible
    set — and it cannot fire from Speed and one offensive stat alone, for the reason above: at
    most 64 of 66 points are ever claimed and the three bulk stats hold 96. It is reachable as
    soon as something bounds bulk from below, so it stays, and this test says why it is silent
    rather than leaving it looking like dead code."""
    worst = _combine(reg, speed_belief=_speed([32]), damage_belief=_damage("atk", [32]),
                     spend_all=True, moves=["Flare Blitz"], nature="Adamant")
    assert worst.contradicted is None and worst.allocations > 0
    # Forced by hand, the guard does what the channels do: widen back and confess.
    blocks, _ = sp.structural_blocks(reg, ["Flare Blitz"], "Adamant", spend_all=True)
    impossible = sp._narrow(blocks, {s: {0} for s in STATS})
    assert sp.count(impossible, reg.sp_budget) == 0


def test_a_channel_that_contradicted_itself_is_not_used_at_all(reg):
    """A channel that widened back to its prior has said nothing, and folding its prior in as
    though it were evidence would let a known-wrong inference reach the joint belief."""
    bad = _speed(range(33))
    bad.contradicted = True
    belief = _combine(reg, speed_belief=bad)
    assert "spe" not in belief.sources
    assert belief.narrowed == 0.0


# --- soundness ---------------------------------------------------------------------------

def test_the_budget_is_spent_by_assumption_and_capped_by_rule(reg):
    """`validate_team` errors above 66 and only *warns* below it, so `sum ≤ 66` is what the format
    enforces and `sum = 66` is a fact about how people build — every point can always be moved into
    a defensive stat, so none is left behind. The default assumes it and `spend_all=False` restores
    the rule alone, because the assumption comes from outside anything here can measure."""
    thrifty = {"hp": 10, "atk": 10, "def": 10, "spa": 0, "spd": 10, "spe": 10}
    assert sum(thrifty.values()) < reg.sp_budget
    assert not _combine(reg).contains(thrifty)
    assert _combine(reg, spend_all=False).contains(thrifty)
    over = dict.fromkeys(STATS, 32)
    assert not _combine(reg).contains(over) and not _combine(reg, spend_all=False).contains(over)


def test_the_truth_survives_every_bound_that_contains_it(reg):
    real = {"hp": 20, "atk": 30, "def": 6, "spa": 0, "spd": 4, "spe": 6}
    belief = _combine(reg, speed_belief=_speed(range(0, 12)),
                      damage_belief=_damage("atk", range(28, 33)))
    assert belief.contains(real)
    assert belief.narrowed > 0


def test_every_particle_is_a_legal_spread_the_belief_allows(reg):
    """Drawn from the same dynamic program rather than by rejection, so a tight belief costs no
    more than a wide one — which is what Phase 9's determinization needs of it."""
    belief = _combine(reg, speed_belief=_speed(range(24, 33)),
                      damage_belief=_damage("atk", range(28, 33)), spend_all=True)
    for p in belief.particles(np.random.default_rng(2), 200):
        assert sum(p.values()) == reg.sp_budget
        assert all(0 <= v <= reg.sp_per_stat_cap for v in p.values())
        assert 24 <= p["spe"] <= 32 and 28 <= p["atk"] <= 32
        assert belief.contains(p)


def test_a_dead_stat_is_an_assumption_with_a_switch(reg):
    """90.9% of sheets have an offensive stat no move of theirs uses, and spending there is
    wasted — but wasted is an argument about play, not a rule, so it is a parameter."""
    special = ["Hyper Voice", "Protect"]
    assert _combine(reg, moves=special, nature="Timid").bounds()["atk"] == (0, 0)
    assert _combine(reg, moves=special, nature="Timid", dead_zero=False).bounds()["atk"][1] == 32
    wasteful = {"hp": 2, "atk": 20, "def": 0, "spa": 22, "spd": 0, "spe": 22}
    assert sum(wasteful.values()) == reg.sp_budget       # legal, and nobody builds it
    assert not _combine(reg, moves=special, nature="Timid").contains(wasteful)
    assert _combine(reg, moves=special, nature="Timid", dead_zero=False).contains(wasteful)


# --- reporting ---------------------------------------------------------------------------

def test_the_credible_set_is_the_smallest_one_holding_the_level(reg):
    belief = _combine(reg)
    cred = belief.credible("spe", 0.8)
    mass = belief.marginals()["spe"]
    assert sum(mass[v] for v in cred) >= 0.8
    assert sum(sorted(mass, reverse=True)[:len(cred) - 1]) < 0.8


def test_narrowing_is_measured_in_allocations_not_in_one_stats_range(reg):
    """A bound on Speed and a bound on Attack do not rule out the same amount of spread, because
    the budget makes some values far more common than others. Counting allocations is the only
    measure that adds across channels."""
    wide = _combine(reg, speed_belief=_speed(range(0, 33)))
    assert wide.narrowed == 0.0
    tight = _combine(reg, speed_belief=_speed(range(30, 33)))
    assert 0.0 < tight.narrowed < 1.0
