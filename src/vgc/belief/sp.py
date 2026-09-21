"""The two channels, joined by the thing that actually connects them: the budget.

`speed` bounds one stat and `damage` bounds another, and reported side by side they are two facts
about the same Pokémon that never speak to each other. They are not independent. A spread is an
allocation of **66 points over six stats, at most 32 each**, so every point the speed channel
proves they bought is a point the damage channel's stat cannot also have, and — the part no single
channel can see — a point that is not in their bulk either.

That is where the joint belief earns its keep. Neither channel says anything at all about HP,
Defence or Special Defence; together, under the budget, they bound all three. A Pokémon shown to
be holding ≥24 Speed and ≥28 Attack has spent 52 of 66 and cannot be holding more than 14 points
of bulk across three stats, which is a conclusion drawn from two offensive observations and no
defensive one. It is also the conclusion a player draws instinctively — *they're fast and they hit
hard, so they're frail* — and it is arithmetic, not intuition.

**The budget is a maximum, not an equation.** `validate_team` errors above 66 and only *warns*
below it, so `sum = 66` is a statement about how people build and `sum ≤ 66` is the rule. This
module constrains the rule and takes `spend_all` as an argument, because the difference is exactly
the kind of assumption that produced five wrong answers in `vgc.belief.speed` — and because the
self-play corpus cannot referee it: `prior.sample_spread` spends every point by construction, so a
gate run against that corpus would be marking its own generator's homework. The same caveat
applies, for the same reason, to `dead_zero`.

**Representation.** A belief is a list of blocks, each block being a set of allowed combinations
for one or more stats, plus a slack block absorbing anything unspent. Everything else — how many
allocations survive, the exact per-stat marginal, K particles drawn without rejection — is one
dynamic program over blocks × budget, which is small enough (six blocks, 67 budget values, 33
values each) to run exactly rather than approximately. Blocks rather than six independent stats
because a defensive observation constrains HP and Defence *jointly* and cannot be factored into
the two without throwing information away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from vgc.belief import damage as damage_channel
from vgc.belief import speed as speed_channel
from vgc.belief.prior import dead_stats
from vgc.data.observe import Observer
from vgc.regulation import STAT_IDS, Regulation
from vgc.teams.sets import PokemonSet

SLACK = "_unspent"


@dataclass(frozen=True)
class Block:
    """Allowed combinations for one or more stats, as `(values, total)` pairs.

    A one-stat block is the ordinary case. A two-stat block is what a defensive observation
    produces: the damage a known move did as a *fraction* of their HP constrains HP and the
    defensive stat together, and the pair of one-dimensional projections is strictly weaker than
    the region itself.
    """

    stats: tuple[str, ...]
    entries: tuple[tuple[tuple[int, ...], int], ...]

    @classmethod
    def over(cls, stat: str, values: Iterable[int]) -> "Block":
        return cls((stat,), tuple(((v,), v) for v in sorted(values)))

    @classmethod
    def joint(cls, stats: Sequence[str], values: Iterable[Sequence[int]]) -> "Block":
        return cls(tuple(stats), tuple((tuple(v), sum(v)) for v in sorted(map(tuple, values))))

    def values_of(self, stat: str) -> list[int]:
        i = self.stats.index(stat)
        return sorted({v[i] for v, _ in self.entries})


# --- the dynamic program ------------------------------------------------------------------

def _prefix(blocks: Sequence[Block], budget: int) -> list[list[int]]:
    """`pre[i][b]` = ways `blocks[:i]` can sum to exactly `b`."""
    pre = [[0] * (budget + 1) for _ in range(len(blocks) + 1)]
    pre[0][0] = 1
    for i, blk in enumerate(blocks):
        row, prev = pre[i + 1], pre[i]
        for _, t in blk.entries:
            if t > budget:
                continue
            for b in range(t, budget + 1):
                if prev[b - t]:
                    row[b] += prev[b - t]
    return pre


def _suffix(blocks: Sequence[Block], budget: int) -> list[list[int]]:
    """`suf[i][r]` = ways `blocks[i:]` can sum to exactly `r`."""
    n = len(blocks)
    suf = [[0] * (budget + 1) for _ in range(n + 1)]
    suf[n][0] = 1
    for i in range(n - 1, -1, -1):
        row, nxt = suf[i], suf[i + 1]
        for _, t in blocks[i].entries:
            if t > budget:
                continue
            for r in range(t, budget + 1):
                if nxt[r - t]:
                    row[r] += nxt[r - t]
    return suf


def count(blocks: Sequence[Block], budget: int) -> int:
    """How many allocations satisfy every block and the budget. 0 means the constraints conflict."""
    return _suffix(blocks, budget)[0][budget]


def entry_counts(blocks: Sequence[Block], budget: int) -> list[dict[tuple[int, ...], int]]:
    """For each block, how many whole allocations each of its entries appears in."""
    pre, suf = _prefix(blocks, budget), _suffix(blocks, budget)
    out = []
    for i, blk in enumerate(blocks):
        counts: dict[tuple[int, ...], int] = {}
        for v, t in blk.entries:
            counts[v] = sum(pre[i][b] * suf[i + 1][budget - b - t]
                            for b in range(0, budget - t + 1))
        out.append(counts)
    return out


def tighten(blocks: Sequence[Block], budget: int) -> list[Block]:
    """Drop every entry that no legal allocation can use.

    This is where the budget does its work. An entry can satisfy its own block and still be
    impossible, because the rest of the spread cannot be completed around it — 30 Speed is
    unreachable for a Pokémon already shown to hold 32 Attack and 12 Defence. Removing those is
    what turns two separate bounds into one belief.
    """
    counts = entry_counts(blocks, budget)
    return [Block(blk.stats, tuple((v, t) for v, t in blk.entries if counts[i][v]))
            for i, blk in enumerate(blocks)]


def marginals(blocks: Sequence[Block], budget: int) -> dict[str, list[float]]:
    """Exact per-stat distribution, uniform over the surviving allocations.

    Uniform over *allocations*, not over values: the two differ, and the difference is the one
    thing every version of the spread prior agreed on (`vgc.belief.prior.structural_mass`). With
    five live stats instead of six there is one fewer competitor for the budget and high values
    get cheaper on their own, with nothing tuned.
    """
    counts = entry_counts(blocks, budget)
    total = count(blocks, budget)
    out: dict[str, list[float]] = {}
    for i, blk in enumerate(blocks):
        for j, stat in enumerate(blk.stats):
            mass = [0.0] * (budget + 1)
            for v, c in counts[i].items():
                mass[v[j]] += c / total if total else 0.0
            out[stat] = mass
    return out


def total_mass(blocks: Sequence[Block], budget: int, stats: Iterable[str]) -> list[int]:
    """How many allocations put exactly `t` points across `stats`, for every `t`.

    The budget does not bound HP, Defence and Special Defence one at a time — it bounds what is
    left for all three together, which is the shape of the inference and not a lossy summary of
    it. Reading it off the per-stat bounds would report nothing: each can still be anything up to
    32, and it is their sum that has moved.
    """
    want = set(stats)
    inside = [b for b in blocks if want & set(b.stats)]
    outside = [b for b in blocks if not want & set(b.stats)]
    if any(set(b.stats) - want for b in inside):
        raise ValueError("a block straddles the subset; group the stats with it")
    ins, outs = _suffix(inside, budget)[0], _suffix(outside, budget)[0]
    return [ins[t] * outs[budget - t] for t in range(budget + 1)]


def sample(blocks: Sequence[Block], budget: int, rng: Any, k: int = 1) -> list[dict[str, int]]:
    """`k` allocations drawn uniformly from the survivors — exactly, with no rejection.

    Rejection sampling would be the obvious way and it degrades exactly when the belief is most
    informative: the tighter the constraints, the more draws are thrown away. Walking the same
    dynamic program backwards costs one pass per particle whatever the belief looks like, which is
    what Phase 9's determinization needs of it.
    """
    suf = _suffix(blocks, budget)
    if not suf[0][budget]:
        return []
    out = []
    for _ in range(k):
        left, drawn = budget, {}
        for i, blk in enumerate(blocks):
            weights = [(v, suf[i + 1][left - t]) for v, t in blk.entries
                       if t <= left and suf[i + 1][left - t]]
            total = sum(w for _, w in weights)
            if not total:   # unreachable: every prefix drawn so far had a completion by construction
                raise AssertionError(f"no completion for {blk.stats} with {left} points left")
            pick = int(rng.integers(0, total)) if hasattr(rng, "integers") else int(rng.random() * total)
            for v, w in weights:
                pick -= w
                if pick < 0:
                    break
            for j, stat in enumerate(blk.stats):
                drawn[stat] = v[j]
            left -= sum(v)
        out.append(drawn)
    return out


# --- the belief ---------------------------------------------------------------------------

@dataclass
class SPBelief:
    """What is still possible for one Pokémon's whole 66-point allocation."""

    side: str
    species: str
    nature: str | None
    blocks: list[Block]
    prior: list[Block]
    budget: int
    dead: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    speed_used: int = 0
    damage_used: int = 0
    contradicted: str | None = None     # which stage had no solution left

    @property
    def allocations(self) -> int:
        return count(self.blocks, self.budget)

    @property
    def narrowed(self) -> float:
        """Fraction of the prior's *allocations* ruled out — not of any one stat's range.

        A bound that halves the Speed range and a bound that halves the Attack range do not rule
        out the same amount of spread, because the budget makes some values much more common than
        others. Counting allocations is the only measure that adds up across channels.
        """
        before = count(self.prior, self.budget)
        return 1 - self.allocations / before if before else 0.0

    def bounds(self) -> dict[str, tuple[int, int]]:
        out = {}
        for blk in self.blocks:
            if blk.stats == (SLACK,):
                continue
            for stat in blk.stats:
                vals = blk.values_of(stat)
                if vals:
                    out[stat] = (min(vals), max(vals))
        return out

    def marginals(self) -> dict[str, list[float]]:
        return {s: m for s, m in marginals(self.blocks, self.budget).items() if s != SLACK}

    def particles(self, rng: Any, k: int = 16) -> list[dict[str, int]]:
        return [{s: v for s, v in p.items() if s != SLACK}
                for p in sample(self.blocks, self.budget, rng, k)]

    def spent_on(self, stats: Iterable[str]) -> tuple[int, int]:
        """The range of totals still possible across `stats` — how bulk actually gets bounded."""
        mass = total_mass(self.blocks, self.budget, stats)
        live = [t for t, n in enumerate(mass) if n]
        return (min(live), max(live)) if live else (0, 0)

    def contains(self, truth: dict[str, int]) -> bool:
        """Is this true spread still possible? The soundness question, asked of the joint object."""
        spent = sum(truth.get(s, 0) for s in STAT_IDS)
        if spent > self.budget:
            return False
        for blk in self.blocks:
            # The slack block is where `spend_all` lives, so it has to be checked like any other:
            # skipping it would silently accept a thrifty spread under a belief that excludes one.
            key = ((self.budget - spent,) if blk.stats == (SLACK,)
                   else tuple(truth.get(s, 0) for s in blk.stats))
            if key not in {v for v, _ in blk.entries}:
                return False
        return True

    def credible(self, stat: str, level: float = 0.8, flat: bool = False) -> list[int]:
        """The smallest set of values for `stat` holding `level` of the marginal.

        The plan's calibration test is stated on this set — the truth should land in the 80% set
        about 80% of the time — so it is built here rather than by whoever scores it, and built
        highest-mass-first so "smallest" is meant literally.
        """
        mass = self.marginals().get(stat) or []
        if flat:
            # Uniform over the values still possible, rather than over the allocations that reach
            # them. `vgc.belief.prior` measured flat as the best of the priors it scored, and the
            # two readings separate two different questions: whether the feasible set is the right
            # size, and whether the weight inside it is in the right place.
            mass = [1.0 if m else 0.0 for m in mass]
            total = sum(mass) or 1.0
            mass = [m / total for m in mass]
        order = sorted(range(len(mass)), key=lambda i: -mass[i])
        out, got = [], 0.0
        for i in order:
            if got >= level:
                break
            out.append(i)
            got += mass[i]
        return sorted(out)

    def to_json(self) -> dict[str, Any]:
        return {"side": self.side, "species": self.species, "nature": self.nature,
                "bounds": self.bounds(), "allocations": self.allocations,
                "narrowed": round(self.narrowed, 4), "dead": self.dead,
                "sources": self.sources, "speed_used": self.speed_used,
                "damage_used": self.damage_used, "contradicted": self.contradicted}


def structural_blocks(reg: Regulation, moves: Iterable[str], nature: str | None,
                      dead_zero: bool = True, spend_all: bool = False) -> tuple[list[Block], dict[str, str]]:
    """The prior: one block per stat, plus slack, before any observation.

    `dead_zero` pins a stat no move of theirs scales off to 0. 90.9% of weighted Pokémon-sheets
    have exactly one such stat, and spending there is strictly wasted — but *strictly wasted* is an
    argument about play, not a rule, and the corpus that would test it was generated under the same
    assumption. It is on by default because it is what a player would assume, and it is a parameter
    because the belief's whole claim is that it does not quietly assume things.
    """
    dead = dead_stats(reg, moves, nature) if dead_zero else {}
    blocks = [Block.over(s, [0] if s in dead else range(reg.sp_per_stat_cap + 1))
              for s in STAT_IDS]
    blocks.append(Block.over(SLACK, [0] if spend_all else range(reg.sp_budget + 1)))
    return blocks, dead


def infer(reg: Regulation, obs: Observer, known: dict[tuple[str, str], PokemonSet],
          dc: Any | None = None, *, speed_known: dict[tuple[str, str], int] | None = None,
          dead_zero: bool = True, spend_all: bool = False) -> dict[tuple[str, str], SPBelief]:
    """One belief per opposing Pokémon, over the whole allocation.

    `known` is the side whose spreads you wrote — your own team, in a real game. Both channels need
    it for the same reason: an observation with unknowns on both sides constrains a pair jointly
    and is counted as deferred rather than used.

    `dc` is a live `DamageCalc`; without one only the turn-order channel runs, which is the cheap
    mode and the one the web app can afford on every turn.

    `speed_known` covers the case where you know how fast your own team is but not the rest of its
    spread — reading a replay you did not play, with an assumption about the Speed of the side you
    can see. It is enough for the turn-order channel and not for the damage channel, so the two
    are given separately rather than one being faked from the other.
    """
    yours = {k: v.sp.spe for k, v in known.items()} | dict(speed_known or {})
    speeds = speed_channel.infer(reg, obs, yours)
    damages = damage_channel.infer(reg, obs, known, dc) if dc is not None else {}

    sheets = {(sid, mon.species): mon
              for sid, side in obs.sides.items() for mon in side.mons}

    out: dict[tuple[str, str], SPBelief] = {}
    for key in sorted(set(speeds) | set(damages)):
        mon = sheets.get(key)
        if mon is None or key in yours:
            continue
        out[key] = combine(reg, key, mon.nature, mon.moves or [],
                           speeds.get(key), damages.get(key),
                           dead_zero=dead_zero, spend_all=spend_all)
    return out


def combine(reg: Regulation, key: tuple[str, str], nature: str | None, moves: Iterable[str],
            speed_belief: Any = None, damage_belief: Any = None, *,
            dead_zero: bool = True, spend_all: bool = False) -> SPBelief:
    """One Pokémon's channel results, assembled into one belief under the budget.

    Split out from `infer` because the channels are the expensive part and the assembly is not:
    the gate scores several settings of `dead_zero` and `spend_all` over the same battles, and
    re-running the calc sweep once per setting would be measuring the same evidence three times.
    """
    blocks, dead = structural_blocks(reg, moves, nature, dead_zero, spend_all)
    prior = list(blocks)
    belief = SPBelief(side=key[0], species=key[1], nature=nature,
                      blocks=blocks, prior=prior, budget=reg.sp_budget, dead=dead)

    narrow: dict[str, set[int]] = {}
    if speed_belief is not None and not speed_belief.contradicted:
        narrow["spe"] = set(speed_belief.feasible)
        belief.speed_used = speed_belief.used
        belief.sources["spe"] = "turn order"
    if damage_belief is not None and not damage_belief.contradicted:
        narrow[damage_belief.stat] = set(damage_belief.feasible)
        belief.damage_used = damage_belief.used
        belief.sources[damage_belief.stat] = "damage"

    belief.blocks = _narrow(blocks, narrow)
    if not count(belief.blocks, belief.budget):
        # Each channel was satisfiable alone and the two cannot both be true under the budget.
        # That is a new detection, not a channel bug — and the only sound response is the one the
        # channels already make: drop back to what was assumed and say so.
        belief.contradicted = "budget"
        belief.blocks = prior
    belief.blocks = tighten(belief.blocks, belief.budget)
    return belief


def _narrow(blocks: Sequence[Block], limits: dict[str, set[int]]) -> list[Block]:
    out = []
    for blk in blocks:
        keep = [(v, t) for v, t in blk.entries
                if all(s not in limits or v[i] in limits[s] for i, s in enumerate(blk.stats))]
        out.append(Block(blk.stats, tuple(keep)))
    return out


def summary(beliefs: dict[tuple[str, str], SPBelief]) -> dict[str, Any]:
    vals = list(beliefs.values())
    bulk = ("hp", "def", "spd")
    defensive = [b for b in vals
                 if b.spent_on(bulk) != SPBelief(b.side, b.species, b.nature, b.prior, b.prior,
                                                 b.budget).spent_on(bulk)]
    return {
        "pokemon": len(vals),
        "narrowed_mean": sum(b.narrowed for b in vals) / len(vals) if vals else 0.0,
        "any_narrowed": sum(1 for b in vals if b.narrowed > 0),
        # The claim this module makes that neither channel can: a bound on bulk, from offence.
        "bulk_bounded": len(defensive),
        "contradicted": sum(1 for b in vals if b.contradicted),
    }
