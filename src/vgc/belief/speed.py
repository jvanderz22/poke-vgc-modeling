"""Turn order → a bound on their Speed Stat Points.

At open sheets the opponent's nature, item and ability are all on the sheet, so their Speed stat
is a known function of one unknown integer in 0..32. Every turn in which one of their Pokémon
moved before or after one of yours, at equal priority, is an inequality on that integer — and
observing it repeatedly, or under different modifiers, tightens it. A speed tie is itself
informative, which is why the comparison is `>=` rather than `>`.

**Soundness over power.** A bound that excludes the truth is worse than no bound, so a pair is
used only when the whole order can be accounted for: equal effective priority (including the
abilities that change it, which the dex's `priority` field does not carry), no move that reorders
the turn, and no ability outside the handled table. Everything else is dropped and counted, so the
abstention rate is a number in the output rather than a silence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from vgc.data.observe import MoveEvent, Observer
from vgc.regulation import CONDITIONAL_PRIORITY, Regulation, is_grounded, to_id

# Standard boost stages, as a fraction.
STAGE = {i: ((2 + i) / 2 if i >= 0 else 2 / (2 - i)) for i in range(-6, 7)}

# Items that scale Speed. Lagging Tail and Full Incense do not scale it — they force the holder
# last — so they are in ABSTAIN_ITEMS instead of here.
ITEM_MULT = {"choicescarf": 1.5, "ironball": 0.5, "machobrace": 0.5, "quickpowder": 2.0}

# Items that decide the order without touching the stat, so no arithmetic can account for them.
# Lagging Tail and Full Incense force the holder last; Quick Claw and Custap Berry put it first,
# Quick Claw at random 20% of the time — which is what made a Torkoal with 0 Speed points appear
# to outrun a Mega Salamence with 32. The holder is abstained on whenever it moved, not only when
# the item fired: the log prints a line when Quick Claw activates and nothing when it does not, so
# a tighter rule is available and is left for when the power is worth the extra state.
ABSTAIN_ITEMS = {"laggingtail", "fullincense", "quickclaw", "custapberry"}

# Abilities that double Speed under a weather this Observer tracks.
WEATHER_ABILITY = {
    "swiftswim": {"raindance", "primordialsea"},
    "chlorophyll": {"sunnyday", "desolateland"},
    "sandrush": {"sandstorm"},
    "slushrush": {"snow", "hail"},
}
TERRAIN_ABILITY = {"surgesurfer": {"electricterrain"}}

# Abilities that change *priority*, which the exported dex does not record — the same class of
# omission as Grassy Glide's conditional +1. Prankster is on 12.1% of sheets and Gale Wings on
# 1.8%, so ignoring them would silently compare a +1 move against a 0 move as though they raced.
PRIORITY_ABILITY = {"prankster", "galewings"}

# Abilities whose effect on order this module does not model. A mover with one of these is
# abstained on rather than guessed at. Quick Draw is random; Stall and Mycelium Might reorder;
# Protosynthesis and Quark Drive boost whichever stat is highest, which couples the multiplier to
# the very spread being inferred — none appear in the Reg M-C corpus, but the guard is cheap.
ABSTAIN_ABILITIES = {"quickdraw", "stall", "myceliummight", "protosynthesis", "quarkdrive",
                     "slowstart", "quickfeet", "unburden"}

# Moves that reorder the turn. If one resolved this turn, the turn is dropped entirely: they act
# on somebody else's slot, so it is not enough to skip the mover that used them.
REORDERING_MOVES = {"afteryou", "quash", "instruct"}


# Returned when a mover's forme has no base stats in the dex, which is neither "known" nor a
# belief to narrow.
UNMODELLED = object()


@dataclass
class SpeedBelief:
    """What is still possible for one Pokémon's Speed investment."""

    side: str
    species: str
    nature: str | None
    feasible: list[int]
    prior: list[int]           # what was possible before any observation
    used: int = 0              # pairs that produced a constraint
    abstained: int = 0         # pairs dropped, by this Pokémon's side of the comparison
    deferred: int = 0          # pairs where neither speed was known, so neither could be pinned
    contradicted: bool = False # the constraints had no solution, so the model is wrong here

    @property
    def narrowed(self) -> float:
        """Fraction of the prior ruled out. 0.0 means nothing was learned."""
        return 1 - len(self.feasible) / max(len(self.prior), 1)

    @property
    def bounds(self) -> tuple[int, int] | None:
        return (min(self.feasible), max(self.feasible)) if self.feasible else None

    def to_json(self) -> dict[str, Any]:
        return {"side": self.side, "species": self.species, "nature": self.nature,
                "feasible": list(self.feasible), "bounds": self.bounds,
                "narrowed": round(self.narrowed, 4), "used": self.used,
                "abstained": self.abstained, "deferred": self.deferred,
                "contradicted": self.contradicted}

    def _apply(self, keep: list[int]) -> None:
        """Narrow to `keep`, unless that empties the set.

        An empty feasible set is not a discovery about the opponent — it is a proof that the
        model of this battle is wrong, because the opponent did have *some* spread. Shipping it
        would be the one failure mode worse than staying wide, so the belief falls back to the
        prior and says so. The fallback is a confession, not a fix: `contradicted` is reported,
        and the rate it fires at is a gate number.
        """
        if keep:
            self.feasible = keep
        else:
            self.contradicted = True
            self.feasible = list(self.prior)


def base_speed(reg: Regulation, species: str) -> int | None:
    entry = reg.dex.get_species(species)
    return entry["baseStats"]["spe"] if entry else None


def speed_stat(reg: Regulation, species: str, nature: str | None, sp: int) -> int | None:
    """The Champions formula, as `calc_stats` does it: floor((base + SP + 20) × nature)."""
    base = base_speed(reg, species)
    if base is None:
        return None
    n = reg.dex.get_nature(nature or "Serious") or {}
    pct = 110 if n.get("plus") == "spe" else 90 if n.get("minus") == "spe" else 100
    return (base + sp + 20) * pct // 100


def effective_speed(stat: int, ev: MoveEvent) -> float:
    """The stat after everything that scaled it *when the turn's order was decided*.

    Not when the move went off: a Speed boost gained earlier in the same turn is visible on the
    move line but did not reorder the turn it was gained in, and treating it as though it did
    produced contradictions on Weak Armor Pokémon.

    Multipliers are applied to the stat, so this is monotone non-decreasing in the Stat Points —
    which is what makes the feasible set an interval and lets the caller test all 33 values
    directly instead of reasoning about which way an inequality points.
    """
    out = float(stat) * STAGE.get((ev.order_boosts or {}).get("spe", 0), 1.0)
    out *= ITEM_MULT.get(to_id(ev.item or ""), 1.0)
    if "tailwind" in (ev.order_side_conditions or []):
        out *= 2.0
    if ev.order_status == "par":
        out *= 0.5
    ability = to_id(ev.ability or "")
    if ability in WEATHER_ABILITY and to_id(ev.order_weather or "") in WEATHER_ABILITY[ability]:
        out *= 2.0
    if ability in TERRAIN_ABILITY and to_id(ev.order_terrain or "") in TERRAIN_ABILITY[ability]:
        out *= 2.0
    return out


def effective_priority(reg: Regulation, ev: MoveEvent) -> int | None:
    """Priority as it actually applied. `None` means "not modelled" — abstain on this mover.

    The dex's `priority` field is the unconditional one, so two things have to be added back. The
    abilities that change it are invisible to the export entirely, and `CONDITIONAL_PRIORITY`
    covers Grassy Glide, whose +1 under Grassy Terrain reaches only a grounded user. Both matter
    more here than in a usage count: comparing a +1 move against a 0 move as though they raced
    does not lose an inference, it produces a wrong one, and a wrong one can rule out the truth.
    """
    entry = reg.dex.get_move(ev.move) or {}
    prio = ev.priority if ev.priority is not None else (entry.get("priority") or 0)
    if ev.move in CONDITIONAL_PRIORITY and to_id(ev.terrain or "") == "grassyterrain":
        if is_grounded(reg.dex, ev.forme or ev.species, ev.ability, ev.item):
            prio += 1
    ability = to_id(ev.ability or "")
    if ability == "prankster" and entry.get("category") == "Status":
        return prio + 1
    if ability == "galewings":
        return None  # +1 only at full HP, and the log does not say what its HP was when it moved
    return prio


def _stable(ev: MoveEvent) -> bool:
    """Did this mover's Speed stay put across the turn it moved in?

    The order was decided at the turn mark, so the `order_*` state is the one that decided it —
    but when the state moved during the turn, the two readings disagree and there is no way to
    tell from the log which one Showdown sorted on for this particular action. Both readings were
    tried against 3,000 self-play battles and each produced contradictions the other did not, so
    neither is right on its own and the pair is dropped. Weak Armor's +2 on being hit is the case
    that motivates it, and a mid-turn Speed drop is the case that motivates not simply preferring
    the turn-start reading. Weather counts as state here: a Swift Swim Pokémon whose rain arrived
    partway through the turn was not fast when the order was decided.
    """
    if not ev.order_known:
        return False
    return ((ev.order_boosts or {}).get("spe", 0) == (ev.boosts or {}).get("spe", 0)
            and ev.order_status == ev.status
            and ev.order_weather == ev.weather and ev.order_terrain == ev.terrain
            and ("tailwind" in (ev.order_side_conditions or [])) ==
                ("tailwind" in (ev.side_conditions or [])))


def _usable(reg: Regulation, ev: MoveEvent) -> bool:
    if ev.called_by:
        return False
    if to_id(ev.item or "") in ABSTAIN_ITEMS:
        return False
    if to_id(ev.ability or "") in ABSTAIN_ABILITIES:
        return False
    if not _stable(ev):
        return False
    return effective_priority(reg, ev) is not None


def pairs(reg: Regulation, moves: Iterable[MoveEvent]) -> list[tuple[MoveEvent, MoveEvent, int]]:
    """Ordered pairs that raced: same turn, equal effective priority, nothing reordering it.

    Returns `(first, second, sign)`, where `sign` is +1 when the first mover was the faster and
    −1 under Trick Room, which inverts the whole comparison. The fixture replay has Trick Room up
    from turn 2, so this is not a corner case.
    """
    by_turn: dict[int, list[MoveEvent]] = {}
    for ev in moves:
        by_turn.setdefault(ev.turn, []).append(ev)
    out = []
    for turn, evs in sorted(by_turn.items()):
        if any(to_id(e.move) in REORDERING_MOVES for e in evs):
            continue
        evs = sorted(evs, key=lambda e: e.seq)
        for i, a in enumerate(evs):
            for b in evs[i + 1:]:
                if a.side == b.side and a.slot == b.slot:
                    continue
                if not (_usable(reg, a) and _usable(reg, b)):
                    continue
                if effective_priority(reg, a) != effective_priority(reg, b):
                    continue
                out.append((a, b, -1 if a.trick_room else 1))
    return out


def ability_pairs(reg: Regulation, log: Iterable[Any]) -> list[tuple[Any, Any, int]]:
    """Switch-in ability announcements that raced, as `(first, second, sign)`.

    A second evidence source, and the only one that lands on **turn 1** — before a move has been
    used, which is exactly when the belief is widest and the advice least informed. Abilities that
    fire because their holder arrived do so in Speed order, so a batch of simultaneous arrivals is
    a set of Speed comparisons for free.

    Two rules carry all the soundness, and the first was found by measuring rather than reasoning:

    - **A response is not a race.** Reading every `-ability` line as an ordering agrees with the
      truth on only 91.3% of pairs, and every counterexample is the same shape — Incineroar at 86
      Speed announcing before Kingambit at 97, which is Intimidate and then *Defiant answering
      it*. A trigger and its response are adjacent in the log and causally ordered. Filtered to
      `rules.SWITCH_IN_ABILITIES`, it is 453 of 453 over 3,000 battles.
    - **Only within one batch.** Switches print before the abilities they trigger, so everything
      announced after the last switch arrived together. Announcements separated by a switch or a
      turn did not race.

    Trick Room inverts it, as it inverts move order: `Pokemon.getActionSpeed` subtracts from
    10000 under it and `eachEvent` sorts on that. Confirmed against play as well as the source,
    because this corpus contains no Trick Room pair to measure it with.
    """
    usable = [e for e in log if e.switch_in and e.order_known]
    out = []
    for a, b in zip(usable, usable[1:]):
        if a.batch != b.batch:
            continue
        if a.side == b.side and a.slot == b.slot:
            continue
        if to_id(a.item or "") in ABSTAIN_ITEMS or to_id(b.item or "") in ABSTAIN_ITEMS:
            continue
        if to_id(a.ability or "") in ABSTAIN_ABILITIES or to_id(b.ability or "") in ABSTAIN_ABILITIES:
            continue
        out.append((a, b, -1 if a.trick_room else 1))
    return out


def infer(reg: Regulation, obs: Observer, known: dict[tuple[str, str], int] | None = None,
          cap: int | None = None) -> dict[tuple[str, str], SpeedBelief]:
    """Feasible Speed SP for every Pokémon that moved, given the ones whose spread you know.

    `known` maps `(side, species)` to **Speed Stat Points**, not to a stat. That distinction is
    load-bearing: a stat is only true of one forme, and a Pokémon that Mega Evolves mid-battle
    changes base Speed without changing its investment — Salamence is base 100 and Mega Salamence
    120. Passing points and resolving the forme per event keeps both sides of every comparison on
    the same footing, and it is what you actually know about your own team anyway.

    A pair in which neither side is known constrains two unknowns jointly; that is real
    information and this does not yet use it, so it is counted as `deferred` rather than dropped
    silently.
    """
    cap = reg.sp_per_stat_cap if cap is None else cap
    known = known or {}
    natures = {}
    for sid, side in obs.sides.items():
        for mon in side.mons:
            natures[(sid, mon.species)] = mon.nature

    beliefs: dict[tuple[str, str], SpeedBelief] = {}

    def belief_for(ev: MoveEvent) -> SpeedBelief | None | object:
        """The belief to narrow, `None` when this side's spread is already known, or `UNMODELLED`
        when the forme has no base stats to work from. The last case has to be its own answer:
        folding it into `None` reads as "known" and sends the caller looking up a spread it was
        never given."""
        key = (ev.side, ev.species)
        if key in known:
            return None
        if base_speed(reg, ev.forme or ev.species) is None:
            return UNMODELLED
        if key not in beliefs:
            beliefs[key] = SpeedBelief(side=ev.side, species=ev.species, nature=natures.get(key),
                                       feasible=list(range(cap + 1)), prior=list(range(cap + 1)))
        return beliefs[key]

    def speed_of(ev: MoveEvent, sp: int | None) -> float | None:
        key = (ev.side, ev.species)
        points = known[key] if sp is None else sp
        # The base stats come from the forme that was on the field, not the preview identity.
        stat = speed_stat(reg, ev.forme or ev.species, natures.get(key), points)
        return None if stat is None else effective_speed(stat, ev)

    evidence = pairs(reg, obs.moves_log) + ability_pairs(reg, getattr(obs, "ability_log", []))
    for first, second, sign in evidence:
        fb, sb = belief_for(first), belief_for(second)
        if fb is UNMODELLED or sb is UNMODELLED:
            continue
        if fb is None and sb is None:
            continue
        if fb is not None and sb is not None:
            fb.deferred += 1
            sb.deferred += 1
            continue
        # Exactly one side is unknown. `sign` folds Trick Room in: the first mover was the faster
        # normally, and the slower under it.
        if sb is None:                      # the *first* mover is the unknown one
            other = speed_of(second, None)
            fb._apply([sp for sp in fb.feasible if _ordered(speed_of(first, sp), other, sign)])
            fb.used += 1
        else:                               # the *second* mover is the unknown one
            other = speed_of(first, None)
            sb._apply([sp for sp in sb.feasible if _ordered(other, speed_of(second, sp), sign)])
            sb.used += 1
    return beliefs


def _ordered(first: float | None, second: float | None, sign: int) -> bool:
    """Did the first mover's speed permit it to go first? Ties are allowed, because Showdown
    breaks them at random — so observing an order never rules out equality."""
    if first is None or second is None:
        return True
    return first >= second if sign > 0 else first <= second


def summary(beliefs: dict[tuple[str, str], SpeedBelief]) -> dict[str, Any]:
    vals = list(beliefs.values())
    used = sum(b.used for b in vals)
    return {
        "pokemon": len(vals),
        "constraints_used": used,
        "constraints_deferred": sum(b.deferred for b in vals) // 2,
        "narrowed_mean": sum(b.narrowed for b in vals) / len(vals) if vals else 0.0,
        "any_narrowed": sum(1 for b in vals if b.narrowed > 0),
        "contradicted": sum(1 for b in vals if b.contradicted),
    }
