"""What their 66 Stat Points were probably doing before the battle said anything.

Uniform over 0..32 is the wrong prior, and wrong in a way that would quietly flatter every gate
built on it. Spreads are not drawn at random: they are *chosen*, against goals, and two kinds of
structure follow from that. Both are computed from public information.

**Structural zeros.** A Pokémon whose moves never scale off Attack gains nothing from Attack. That
is not a heuristic — across the 47,928 weighted Pokémon-sheets in the corpus, **90.9% have exactly
one offensive stat that no move of theirs uses**, 0.1% have neither, and 9.0% genuinely use both.
Of the 90.9%, **95.1% have the nature confirming it**: the sheet says Modest, the movepool is all
special, and Attack is dead weight twice over. So for nine Pokémon in ten, one of six dimensions
is gone before the battle starts, and the remaining budget has one fewer place to hide.

**Benchmarks.** Speed points are bought to clear something — to outrun a max-Speed Jolly base 100,
or to sit under a Trick Room setter. Against conventional builds the 33 values collapse: an
Adamant Rillaboom has 16 distinct outcomes, a Careful Incineroar 13, a Jolly Garchomp 13. And a
builder takes the *cheapest* point in each class, because the rest is worth more elsewhere. So the
prior is not smooth — it has mass on class boundaries, on 0, and on 32.

**Benchmarks are circular and that is not fixable here.** What is worth outrunning depends on what
everyone else runs, which depends on what is worth outrunning. Solving it means an independent
ranking of what is good and a model of how the meta moves toward it, which is a different and much
harder project. This iterates once from the conventional extremes and says so. The circularity is
also why the prior is *tiered*: the good tier needs a usage corpus for the regulation, and a
regulation that has just rotated does not have one yet.

    TIERS, best first — every result says which one produced it
      usage       benchmarks from the measured meta (`vgc meta usage`). Needs a corpus.
      pool        benchmarks from the legal species list at conventional spreads. Needs the dex.
      structural  no benchmarks at all: structural zeros and a flat Speed prior. Needs nothing.

`speed_prior` degrades on its own and never fails for want of data, because the alternative is a
regulation rotation silently taking the belief layer offline.

**What the evidence says about all of this.** A human's Stat Points are not recoverable, so a prior
cannot be scored against the truth — but it can be scored against its consequences. A prior implies
a distribution over who moves first, and 19,255 observed racing pairs in 2,500 cached replays are
exactly that consequence. Paired bootstrap over games, nats per pair against a flat prior
(`scripts/analysis/spread_prior.py`, [findings](../../../docs/phase8-findings.md)):

    prior                      all pairs                  close pairs only
    impute_sp (a point mass)   +1.123 [1.039, 1.215]      +2.925 [2.689, 3.165]   flat is better
    structural (no params)     +0.004 [0.003, 0.005]      +0.002 [-0.000, 0.003]  flat is better / —
    benchmark (usage tier)     -0.011 [-0.016, -0.007]    +0.010 [0.005, 0.015]   beats flat / loses

Three things follow, and the second is why `flat_prior` is still what the belief layer uses.

1. **`impute_sp` is catastrophic as a prior** — a point mass assigns near-zero probability to every
   turn order it did not predict, and it is 2.9 nats a pair worse than assuming nothing. It is also
   what every spread in the self-play corpus was generated from.
2. **The benchmark prior beats flat overall and loses on close pairs**, both intervals clearing zero
   in opposite directions. It is a better description of the population and a worse one of the
   individual: the broad shape is right, and the specific concentration on class minima is wrong
   exactly where the prior's detail decides the answer. Gate 1 again — a single pooled number would
   have reported the win and hidden the loss.
3. **Counting allocations underrates the extremes.** Real builds sit at 0 and at 32 far more often
   than a uniform distribution over legal spreads implies, which is the one thing every version of
   this agrees on.

So `speed_classes` is kept for what it is — a true statement about which investments are
distinguishable, and useful output for a team builder — while the *weighting* over those classes
stays unvalidated and opt-in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from vgc.belief.speed import speed_stat
from vgc.regulation import Regulation, offensive_stat, to_id

TIERS = ("usage", "pool", "structural")

# The Speed investments a benchmark is measured against. These are an *assumption*, and the same
# one that makes `impute_sp` useless: that builds sit at the extremes. It is roughly true of the
# corpus in hand — but the corpus is 58% unrated with a median rating of 1101, and at higher level
# a spread is chosen to hit a specific number, so "maxed or nothing" is one option among many
# rather than the rule. `benchmarks()` takes these as an argument so a better set can replace them
# without touching anything else, and the tier recorded on every result says where they came from.
CONVENTIONAL = (0, 32)


# --- structural zeros -------------------------------------------------------------------

def dead_stats(reg: Regulation, moves: Iterable[str], nature: str | None) -> dict[str, str]:
    """Stats this Pokémon's own moves can never use, and why.

    Reads `offensive_stat`, not the move's category, so Body Press counts towards Defence and
    Foul Play — which scales off the *target's* Attack — does not rescue a dead Attack.
    """
    used = {offensive_stat(reg.dex, m) for m in moves}
    used.discard(None)
    out = {}
    for stat in ("atk", "spa"):
        if stat not in used:
            out[stat] = "no move scales off it"
    n = reg.dex.get_nature(nature or "Serious") or {}
    if n.get("minus") in out:
        out[n["minus"]] += ", and the nature lowers it"
    return out


# --- benchmarks -------------------------------------------------------------------------

def benchmarks(reg: Regulation, report: dict[str, Any] | None = None, n: int = 30,
               anchors: tuple[int, ...] = CONVENTIONAL) -> tuple[list[tuple[int, float]], str]:
    """`[(speed, weight)]` worth clearing, and the tier that produced them.

    `weight` is how much of the meta sits at that speed, so a breakpoint past a Pokémon nobody
    brings is worth less than one past Rillaboom. At the `pool` tier every legal species counts
    the same, which is the honest thing to say when nothing has been measured yet.
    """
    if report is None:
        try:
            from vgc.meta import usage
            report = usage.load(reg)
        except (FileNotFoundError, ImportError):
            report = None

    out: dict[int, float] = {}
    if report:
        for s in usage.top_species(report, n) if report else []:
            nature = s["natures"][0]["name"] if s["natures"] else "Serious"
            for sp in anchors:
                v = speed_stat(reg, s["species"], nature, sp)
                if v:
                    out[v] = out.get(v, 0.0) + s["share"]
        if out:
            return sorted(out.items()), "usage"

    # Fallback: the legal pool, every species equally, at a neutral nature. No corpus needed.
    species = list(reg.dex.species.values())
    for entry in species:
        for sp in anchors:
            v = speed_stat(reg, entry["name"], "Serious", sp)
            if v:
                out[v] = out.get(v, 0.0) + 1.0 / max(len(species), 1)
    return sorted(out.items()), "pool"


def speed_classes(reg: Regulation, species: str, nature: str | None,
                  marks: list[tuple[int, float]], cap: int = 32) -> list[dict[str, Any]]:
    """Group 0..cap by which benchmark speeds they clear.

    Two investments in the same class outrun exactly the same things, so they are the same choice
    wearing different price tags — and the cheapest member is the one a builder picks, because the
    difference is worth more in another stat. `gain` is the meta weight the class newly clears.
    """
    tiers = [(sp, speed_stat(reg, species, nature, sp)) for sp in range(cap + 1)]
    classes: list[dict[str, Any]] = []
    for sp, stat in tiers:
        if stat is None:
            continue
        cleared = sum(w for v, w in marks if v < stat)
        if classes and abs(classes[-1]["clears"] - cleared) < 1e-12:
            classes[-1]["members"].append(sp)
            continue
        # The first class is what you get for free, so its `gain` is zero: clearing something at
        # 0 investment is not a reason to *choose* 0. Only what a class buys over the one below it
        # is a reason to pay for it.
        classes.append({"min_sp": sp, "members": [sp], "clears": cleared,
                        "gain": (cleared - classes[-1]["clears"]) if classes else 0.0})
    return classes


# --- how many legal allocations put `s` in one stat --------------------------------------

import functools


@functools.lru_cache(maxsize=None)
def _allocations(stats: int, budget: int, cap: int) -> int:
    """Ways to hand out exactly `budget` points over `stats` stats, none above `cap`."""
    if stats == 0:
        return 1 if budget == 0 else 0
    return sum(_allocations(stats - 1, budget - v, cap)
               for v in range(0, min(cap, budget) + 1))


def structural_mass(live: int, budget: int, cap: int) -> list[float]:
    """The Speed marginal under a uniform distribution over legal spreads — and no free parameters.

    This is what the structural zeros actually buy. Every legal way of spending the 66 points is
    counted once, and a Pokémon whose Attack is dead weight is spending them over five stats
    rather than six. That is not a smaller space with the same shape: with one fewer competitor
    for the budget, high Speed gets cheaper, and the marginal shifts up on its own. Nothing is
    tuned here, so nothing can be tuned *to* the test that scores it.
    """
    total = _allocations(live, budget, cap)
    if not total:
        return [1.0] * (cap + 1)
    return [_allocations(live - 1, budget - s, cap) / total for s in range(cap + 1)]


def live_stats(reg: Regulation, moves: Iterable[str], nature: str | None) -> int:
    """How many of the six stats this Pokémon could sensibly spend on."""
    return 6 - len(dead_stats(reg, moves, nature))


# --- the prior --------------------------------------------------------------------------

@dataclass
class SpeedPrior:
    """A distribution over Speed Stat Points for one Pokémon, and what it was built from."""

    species: str
    nature: str | None
    tier: str
    mass: list[float]          # index = Stat Points
    classes: int
    dead: dict[str, str]

    def __post_init__(self) -> None:
        total = sum(self.mass) or 1.0
        self.mass = [m / total for m in self.mass]

    def probability(self, sp: int) -> float:
        return self.mass[sp] if 0 <= sp < len(self.mass) else 0.0

    def mass_in(self, values: Iterable[int]) -> float:
        return sum(self.probability(v) for v in values)

    def to_json(self) -> dict[str, Any]:
        return {"species": self.species, "nature": self.nature, "tier": self.tier,
                "classes": self.classes, "dead": self.dead,
                "mass": [round(m, 6) for m in self.mass]}


def speed_prior(reg: Regulation, species: str, nature: str | None, moves: Iterable[str],
                *, marks: list[tuple[int, float]] | None = None, tier: str | None = None,
                report: dict[str, Any] | None = None, floor: float = 0.25,
                extremes: float = 0.35, cap: int | None = None) -> SpeedPrior:
    """Prior over their Speed investment, given the sheet.

    Three components, and the two free weights are deliberately blunt because the turn-order
    likelihood test decides whether any of this beats a flat prior:

    - a `floor` of the structural prior, so nothing legal is ever ruled out by the prior alone;
    - mass on each class minimum, in proportion to the meta weight that class newly clears;
    - `extremes` split between 0 and the cap, which are choices in their own right — fully
      uninvested under Trick Room, and maxed for the speed race — and are not benchmarks.

    `tier="structural"` skips the benchmarks entirely and returns the floor alone, which is the
    fallback for a regulation with no corpus yet.
    """
    cap = reg.sp_per_stat_cap if cap is None else cap
    dead = dead_stats(reg, moves, nature)

    base = structural_mass(6 - len(dead), reg.sp_budget, cap)
    if tier == "structural":
        return SpeedPrior(species, nature, "structural", list(base), 0, dead)
    if marks is None:
        marks, found = benchmarks(reg, report)
        tier = tier or found
    tier = tier or "usage"

    classes = speed_classes(reg, species, nature, marks, cap)
    # The benchmark tier *reweights* the structural prior rather than replacing it, so it can only
    # be judged on what the benchmarks add.
    mass = [floor * b for b in base]
    gains = sum(max(c["gain"], 0.0) for c in classes) or 1.0
    share = 1.0 - floor - extremes
    for c in classes:
        mass[c["min_sp"]] += share * max(c["gain"], 0.0) / gains
    mass[0] += extremes / 2
    mass[cap] += extremes / 2
    return SpeedPrior(species, nature, tier, mass, len(classes), dead)


def flat_prior(reg: Regulation, species: str, nature: str | None,
               cap: int | None = None) -> SpeedPrior:
    """Uniform over the legal range — the baseline, and still the one the belief layer uses.

    Not for want of trying: it is the baseline every other prior here was built to beat, and on
    close pairs none of them does. See the module docstring for the numbers.
    """
    cap = reg.sp_per_stat_cap if cap is None else cap
    return SpeedPrior(species, nature, "flat", [1.0] * (cap + 1), 0, {})


def imputed_prior(reg: Regulation, species: str, nature: str | None, moves: Iterable[str],
                  cap: int | None = None) -> SpeedPrior:
    """`impute_sp` as a distribution: a point mass, because that is what it is.

    It is the prior the whole corpus was built with, so it is the second baseline — and a point
    mass that lands wrong assigns probability zero to what actually happened, which is exactly the
    failure a distribution is supposed to avoid.
    """
    from vgc.meta.replays import impute_sp
    from vgc.teams.sets import PokemonSet

    cap = reg.sp_per_stat_cap if cap is None else cap
    mon = PokemonSet(species=species, nature=nature, moves=list(moves), level=reg.level)
    sp = impute_sp(mon, reg.dex, reg.sp_budget, cap)
    mass = [1e-9] * (cap + 1)          # not zero: a log-likelihood has to stay finite
    mass[min(sp.spe, cap)] += 1.0
    return SpeedPrior(species, nature, "imputed", mass, 1, {})


# --- sampling a spread, for a corpus that can actually gate an inference -----------------

def sample_spread(reg: Regulation, species: str, nature: str | None, moves: Iterable[str],
                  rng: Any, cap: int | None = None, budget: int | None = None) -> dict[str, int]:
    """A legal allocation of the 66 points, for generating a corpus to gate inference against.

    **This does not try to imitate the meta, and it should not.** The corpus it feeds exists to
    measure whether a channel can infer a hidden quantity, and the hardest honest test of that is a
    truth drawn as close to uniform as the rules allow: if the generated spreads matched the human
    prior, a channel could score well by echoing the prior back rather than by reading the battle.
    So the two stats the belief channels infer — Speed, and the offensive stat that is actually
    live — get flat marginals, which is also the one marginal shape that beat every alternative on
    19,255 real turn orders.

    What it does keep from reality is the part that is not a guess: a stat no move of theirs uses
    gets nothing, because 90.9% of real Pokémon-sheets are built that way and investing there is
    strictly wasted.

    The consequence is worth stating plainly, because it decides what these runs may be used for:
    teams built this way are *not* realistic teams. Win rates over them mean nothing, and they must
    not be manifested into WP training. They are an instrument for gating the belief layer, where
    the spread has to vary or the gate measures nothing.
    """
    cap = reg.sp_per_stat_cap if cap is None else cap
    budget = reg.sp_budget if budget is None else budget
    dead = set(dead_stats(reg, moves, nature))
    live_offence = next((s for s in ("atk", "spa") if s not in dead), None)

    out = {s: 0 for s in ("hp", "atk", "def", "spa", "spd", "spe")}
    left = budget
    for stat in (s for s in ("spe", live_offence) if s):
        take = int(rng.integers(0, min(cap, left) + 1))
        out[stat] = take
        left -= take

    # Whatever is left goes to the stats that are neither dead nor already drawn, one at a time in
    # random order, so no single stat systematically absorbs the remainder.
    order = [s for s in ("hp", "def", "spd") if s not in dead]
    rng.shuffle(order)
    for i, stat in enumerate(order):
        # A stat has to take enough that the ones after it can still absorb the rest under the
        # cap, or the budget ends up unspent — real sheets always total exactly 66, and a team
        # quietly spending 32 of its points would be a weaker opponent, not a differently built
        # one.
        remaining_after = len(order) - i - 1
        lo = max(0, left - cap * remaining_after)
        hi = min(cap, left)
        take = hi if remaining_after == 0 else int(rng.integers(lo, hi + 1))
        out[stat] = take
        left -= take
    if left:                                     # unreachable given 3 × 32 ≥ 66, but cheap to say
        raise ValueError(f"{left} Stat Points could not be spent for {species}")
    return out


def resample_team(reg: Regulation, text: str, rng: Any) -> str:
    """A pool team's Showdown export with every spread redrawn. Everything else is untouched."""
    from vgc.teams.sets import StatPoints
    from vgc.teams.showdown_text import export_set, parse_team

    team = parse_team(text)
    for mon in team:
        mon.sp = StatPoints.from_dict(sample_spread(reg, mon.species, mon.nature, mon.moves, rng))
    return "\n\n".join(export_set(m) for m in team) + "\n"
