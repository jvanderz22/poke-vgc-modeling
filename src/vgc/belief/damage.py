"""Damage magnitude → a bound on their offensive Stat Points.

The second read every player makes: *that did 71% to my Incineroar, so it is not fully invested.*
It is the same arithmetic `vgc team weakness` runs forwards — there, "how many points would they
need for the KO"; here, "how many points did they have, given what actually landed" — against the
same pinned `@smogon/calc`.

At open sheets the attacker's species, item, ability and nature are all known, so the damage is a
function of one unknown integer in 0..32. Sweep it, keep the values whose 16-roll range could have
produced what was observed, and the feasible set is the answer.

**Soundness over power, as in `vgc.belief.speed`.** Three things make an observation unusable and
each is dropped and counted rather than guessed at:

- the defender's spread is not known, so there is nothing to measure the attacker against;
- something in the event is not reproducible by a single calc — a multi-hit move whose hit count
  the log does not give, an ability that scales with the battle rather than the spread, a move
  whose power depends on a quantity that is itself hidden;
- the target fainted, which is a *right-censored* observation: the move did at least the remaining
  HP and possibly far more, so reading it as an equality would systematically overstate how little
  they invested. Censored events are kept, but as a one-sided bound.

**HP precision is the binding constraint, not the arithmetic.** A spectator sees the opponent's HP
out of 100, so an observed loss is known to about a percentage point, and the feasible set is
widened to match. Self-play logs carry exact HP, which is why the gate can be tighter than the app
ever will be.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

from vgc.data.observe import DamageEvent, Observer
from vgc.engine.calc import DamageCalc
from vgc.regulation import UNMODELLED_PSEUDO, Regulation, offensive_stat, to_id
from vgc.teams.sets import PokemonSet, StatPoints

# Abilities whose damage contribution depends on the battle rather than on the spread, so no
# single calc reproduces the event. Supreme Overlord scales with fainted allies; Analytic and
# Stakeout depend on what the target did; Protosynthesis and Quark Drive boost whichever stat is
# highest, which couples the multiplier to the very spread being inferred.
ABSTAIN_ABILITIES = {"supremeoverlord", "analytic", "stakeout", "protosynthesis", "quarkdrive",
                     "battlebond", "beastboost", "moody"}

# Moves that hit a variable number of times. The dex export **does not carry `multihit` at all**,
# so the obvious `entry.get("multihit")` guard silently never fired — the fourth time in this phase
# that the export turned out not to carry a field the code assumed. These come from the pinned
# build instead, and `tests/test_belief_damage.py` re-derives them from `vendor/` so a Showdown
# bump that changes the set fails a test rather than quietly widening the error.
MULTI_HIT = {
    "armthrust", "barrage", "bonemerang", "bonerush", "bulletseed", "cometpunch", "doublehit",
    "doubleironbash", "doublekick", "doubleslap", "dragondarts", "dualchop", "dualwingbeat",
    "furyattack", "furyswipes", "geargrind", "iciclespear", "pinmissile", "populationbomb",
    "rockblast", "scaleshot", "spikecannon", "surgingstrikes", "tachyoncutter", "tailslap",
    "tripleaxel", "tripledive", "triplekick", "twinbeam", "twineedle", "watershuriken",
}

# Moves whose power Showdown computes at run time (`basePowerCallback`), from something a single
# calc cannot see: the attacker's remaining HP (Eruption, Water Spout), how many allies have
# fainted (Last Respects), how many times it has been hit (Rage Fist), whether the target has
# already moved (Avalanche, Payback), and so on. Abstained on wholesale, which costs real power —
# Low Kick and Grass Knot are on this list and are perfectly deterministic from the target's
# weight, and the HP-dependent ones could be recovered by passing `curHPFraction` to the sidecar.
# Soundness first; those are power left on the table on purpose, and recorded so it is findable.
RUNTIME_POWER = {
    "acrobatics", "assurance", "avalanche", "beatup", "boltbeak", "crushgrip", "dragonenergy",
    "echoedvoice", "electroball", "eruption", "firepledge", "fishiousrend", "flail",
    "frustration", "furycutter", "grassknot", "grasspledge", "gyroball", "hardpress",
    "heatcrash", "heavyslam", "hex", "iceball", "infernalparade", "lastrespects", "lowkick",
    "payback", "pikapapow", "powertrip", "punishment", "pursuit", "ragefist", "return",
    "revenge", "reversal", "risingvoltage", "rollout", "round", "smellingsalts", "spitup",
    "stompingtantrum", "storedpower", "temperflare", "terablast", "tripleaxel", "triplekick",
    "trumpcard", "veeveevolley", "wakeupslap", "waterpledge", "watershuriken", "waterspout",
    "wringout",
}

# Fixed-damage moves carry no signal about investment at all: the sweep is flat, and reporting
# "every value is possible" is true and useless.
FIXED_DAMAGE = {"seismictoss", "nightshade", "endeavor", "superfang", "finalgambit", "counter",
                "mirrorcoat", "metalburst", "comeuppance", "dragonrage", "sonicboom"}

# Two-turn and charge moves. Their self-boost lands on the charge turn and the damage on the
# next, and the log does not make the order of the two unambiguous from outside — an Electro Shot
# whose +1 Special Attack is applied when it should not be over-predicts by exactly that 1.5×,
# which is what made Archaludon the largest single source of contradictions here.
CHARGE_MOVES = {
    "bounce", "dig", "dive", "electroshot", "fly", "freezeshock", "geomancy", "iceburn",
    "meteorbeam", "phantomforce", "razorwind", "shadowforce", "skullbash", "skyattack",
    "skydrop", "solarbeam", "solarblade",
}

# Moves whose own stat boost `@smogon/calc` already applies for you. Its Champions mechanics do
# `if (move.named('Meteor Beam', 'Electro Shot'))` and add the +1 internally, so passing the boost
# the log reports counts it twice — measurably: a +1 passed to Electro Shot moves the damage by
# 1.32x where a normal move moves it by 1.50x, because the calc is really going from +1 to +2.
#
# This was very nearly mis-diagnosed as the mechanic being wrong. The battles looked like the
# damage was unboosted, which contradicts both the pinned `onTryMove` — it boosts Special Attack
# and then attacks, in rain immediately and otherwise next turn with the +1 still up — and what
# the log plainly shows, the boost line landing before the damage. Both were right; the double
# count was in the bridge. The boost also persists into later turns, so the correction is to
# subtract what the calc adds rather than to ignore the log.
CALC_APPLIES_SELF_BOOST = {"electroshot": ("spa", 1), "meteorbeam": ("spa", 1)}


def attacker_boosts_for(ev: DamageEvent) -> dict[str, int]:
    """The boosts to hand the calc: what was observed, minus whatever the calc adds itself."""
    boosts = dict(ev.attacker_boosts or {})
    entry = CALC_APPLIES_SELF_BOOST.get(to_id(ev.move))
    if entry:
        stat, amount = entry
        boosts[stat] = max(-6, boosts.get(stat, 0) - amount)
    return boosts


ABSTAIN_MOVES = MULTI_HIT | RUNTIME_POWER | FIXED_DAMAGE | (CHARGE_MOVES - set(CALC_APPLIES_SELF_BOOST))


@dataclass
class DamageBelief:
    """What is still possible for one Pokémon's investment in one offensive stat."""

    side: str
    species: str
    stat: str
    feasible: list[int]
    prior: list[int]
    used: int = 0
    censored: int = 0          # observations that only gave a lower bound (the target fainted)
    abstained: int = 0
    contradicted: bool = False

    @property
    def narrowed(self) -> float:
        return 1 - len(self.feasible) / max(len(self.prior), 1)

    @property
    def bounds(self) -> tuple[int, int] | None:
        return (min(self.feasible), max(self.feasible)) if self.feasible else None

    def _apply(self, keep: list[int]) -> None:
        """Narrow, unless that empties the set — see `vgc.belief.speed.SpeedBelief._apply`. An
        empty set is a proof that this battle's model is wrong, not a fact about the opponent."""
        if keep:
            self.feasible = keep
        else:
            self.contradicted = True
            self.feasible = list(self.prior)

    def to_json(self) -> dict[str, Any]:
        return {"side": self.side, "species": self.species, "stat": self.stat,
                "feasible": list(self.feasible), "bounds": self.bounds,
                "narrowed": round(self.narrowed, 4), "used": self.used,
                "censored": self.censored, "abstained": self.abstained,
                "contradicted": self.contradicted}


# Move targets that make a move a *spread* move. Showdown applies the 0.75 reduction only when
# more than one target was actually hit; `@smogon/calc` applies it whenever the game type is not
# Singles, with no per-move override — `isSpread` is derived from `field.gameType` and the move's
# target. So a Heat Wave that caught one Pokémon reads 0.75× in the calc and 1.0× in the battle,
# which is a 1.33× error in exactly the direction that makes an attacker look stronger and a
# defender look frailer than either is.
SPREAD_TARGETS = {"allAdjacentFoes", "allAdjacent"}
SCREENS = {"reflect", "lightscreen", "auroraveil"}


def game_type(reg: Regulation, ev: DamageEvent) -> str | None:
    """What to tell the calc, or None when no answer is right and the event must be dropped.

    `gameType` is overloaded in the calc: it gates the spread reduction *and* the screen strength,
    which is 1/3 in doubles and 1/2 in singles. Those usually do not conflict — the screen setting
    only matters when a screen is up, and the spread setting only matters for a spread move that
    hit one target. When they do conflict there is no value that is right for both, so the event is
    abstained on rather than being quietly wrong by a third in one term or the other.
    """
    entry = reg.dex.get_move(ev.move) or {}
    single_hit_spread = entry.get("target") in SPREAD_TARGETS and not ev.spread
    screened = bool(set(ev.target_side_conditions or []) & SCREENS)
    if not single_hit_spread:
        return "Doubles"
    return None if screened else "Singles"


def usable(reg: Regulation, ev: DamageEvent) -> str | None:
    """Why this event cannot be used, or None if it can."""
    if to_id(ev.move) in ABSTAIN_MOVES:
        return "move_power_hidden"
    if offensive_stat(reg.dex, ev.move) is None:
        return "not_a_damaging_move"
    if ev.lost <= 0:
        return "no_damage"
    if game_type(reg, ev) is None:
        return "spread_and_screen_disagree"
    if _field(reg, ev) is None:
        return "field_not_translatable"
    if set((ev.field or {}).get("pseudo") or []) & UNMODELLED_PSEUDO:
        return "field_effect_not_modelled"
    return None


# `@smogon/calc` names its weather and terrain differently from the protocol, and — like an
# unrecognised item — **silently ignores a string it does not know**. Grassy Glide is 29-34 under
# `terrain="grassyterrain"` and 38-45 under `terrain="Grassy"`, with no error either way. That
# silent drop has now cost three separate bugs here, so anything absent from these tables makes
# the event unusable instead of quietly becoming a neutral field.
CALC_TERRAIN = {"grassyterrain": "Grassy", "electricterrain": "Electric",
                "mistyterrain": "Misty", "psychicterrain": "Psychic"}
CALC_WEATHER = {"raindance": "Rain", "sunnyday": "Sun", "sandstorm": "Sand", "snow": "Snow",
                "snowscape": "Snow", "hail": "Hail", "primordialsea": "Heavy Rain",
                "desolateland": "Harsh Sunshine", "deltastream": "Strong Winds"}


def _field(reg: Regulation, ev: DamageEvent) -> dict[str, Any] | None:
    """The calc's view of the field, or None when something in it cannot be translated."""
    kind = game_type(reg, ev)
    if kind is None:
        return None
    f: dict[str, Any] = {"gameType": kind}
    weather = to_id((ev.field or {}).get("weather") or "")
    terrain = to_id((ev.field or {}).get("terrain") or "")
    if weather:
        if weather not in CALC_WEATHER:
            return None
        f["weather"] = CALC_WEATHER[weather]
    if terrain:
        if terrain not in CALC_TERRAIN:
            return None
        f["terrain"] = CALC_TERRAIN[terrain]
    conditions = set(ev.target_side_conditions or [])
    if "reflect" in conditions:
        f["defenderSide"] = {"isReflect": True}
    if "lightscreen" in conditions:
        f["defenderSide"] = {**f.get("defenderSide", {}), "isLightScreen": True}
    if "auroraveil" in conditions:
        f["defenderSide"] = {**f.get("defenderSide", {}), "isAuroraVeil": True}
    return f


# A Pokémon left on a sliver of HP did not necessarily take only that much: Focus Sash, Sturdy and
# Endure all floor a lethal hit at 1 HP, and the `|-damage|` line looks exactly the same either
# way. Measured on 60 sampled-spread battles, 16 of the bulk channel's 19 remaining misses were
# survivors sitting at ≤2% — the move did *at least* what it appeared to and possibly far more.
# Reading it as an equality is the same error as reading a KO as one, so it gets the same answer:
# a one-sided bound, which is sound whether or not a sash was the reason.
SURVIVED_ON_A_SLIVER = 0.02


def censored(ev: DamageEvent) -> bool:
    """Is this a lower bound rather than a measurement?"""
    return bool(ev.fainted) or (ev.hp_after is not None and ev.hp_after <= SURVIVED_ON_A_SLIVER)


def observed_loss(ev: DamageEvent, defender_hp: int) -> tuple[float, float]:
    """The damage the move did, in HP points, as an interval.

    Exact HP gives a point; a spectator's out-of-100 fraction gives a band about a percentage
    point wide, and the band is what the feasible set has to respect. Reporting the midpoint and
    calling it the damage is how a sound channel quietly stops being one.
    """
    lost = ev.lost * defender_hp
    if ev.exact and ev.hp_max:
        return (lost - 0.5, lost + 0.5)
    slack = defender_hp / 100.0 + 0.5          # one percentage point, plus rounding either way
    return (lost - slack, lost + slack)


def infer(reg: Regulation, obs: Observer, known: dict[tuple[str, str], PokemonSet],
          dc: DamageCalc, cap: int | None = None) -> dict[tuple[str, str], DamageBelief]:
    """Feasible offensive Stat Points for every attacker that hit a Pokémon you know.

    `known` maps `(side, species)` to the complete set — species, item, ability, nature *and*
    spread — for the Pokémon whose spreads you wrote, which in a real game is your own team. The
    defender has to be fully known: an unknown attacker hitting an unknown defender constrains the
    pair jointly and is counted as deferred rather than used.
    """
    cap = reg.sp_per_stat_cap if cap is None else cap
    sheets: dict[tuple[str, str], Any] = {}
    for sid, side in obs.sides.items():
        for mon in side.mons:
            sheets[(sid, mon.species)] = mon

    beliefs: dict[tuple[str, str], DamageBelief] = {}
    for ev in obs.damage_log:
        akey = (ev.attacker_side, ev.attacker)
        dkey = (ev.target_side, ev.target)
        if akey in known or dkey not in known:
            continue
        if usable(reg, ev) is not None:
            continue
        if to_id(ev.attacker_ability or "") in ABSTAIN_ABILITIES:
            continue
        stat = offensive_stat(reg.dex, ev.move)
        # Everything about the attacker is taken from the event, not from the Observer's live
        # state: by the end of the battle a Mega has a different forme and a different ability
        # than it had when it swung.
        forme = ev.attacker_forme or ev.attacker
        mon = sheets.get(akey)
        if reg.dex.get_species(forme) is None or mon is None:
            continue

        belief = beliefs.get(akey)
        if belief is None:
            belief = beliefs[akey] = DamageBelief(
                side=akey[0], species=akey[1], stat=stat,
                feasible=list(range(cap + 1)), prior=list(range(cap + 1)))
        elif belief.stat != stat:
            # A mixed attacker constrains two stats; this tracks one, so the second is dropped
            # rather than folded into the first and silently mislabelled.
            belief.abstained += 1
            continue

        defender = known[dkey]
        target_forme = ev.target_forme or defender.species
        if reg.dex.get_species(target_forme) is None:
            continue
        hp = _defender_hp(reg, defender, target_forme)
        lo, hi = observed_loss(ev, hp)
        rolls = _sweep(dc, reg, ev, forme, stat, mon.nature, defender, target_forme, cap)
        keep = []
        for sp, damage in enumerate(rolls):
            if not damage:
                continue
            if censored(ev):
                # Right-censored: the move did *at least* the remaining HP. Anything that could
                # reach it stays in, which is why a KO narrows far less than a survived hit.
                if max(damage) >= lo:
                    keep.append(sp)
            elif min(damage) <= hi and max(damage) >= lo:
                keep.append(sp)
        belief._apply(keep)
        belief.used += 1
        belief.censored += censored(ev)
    return beliefs


def proper(table: dict[str, dict], value: str | None) -> str | None:
    """A dex id back to the name `@smogon/calc` recognises.

    This is load-bearing and was the single largest source of wrong answers here. The Observer
    stores items and abilities as ids — `_on_showteam` does `to_id(...)` — and the calc **silently
    ignores** a string it does not recognise rather than failing: Kingambit's Kowtow Cleave is
    108-127 holding "Black Glasses" and 90-106 holding "blackglasses", with no error either way.
    Feeding ids into the sweep meant every item and ability quietly vanished, which is why a third
    of the beliefs contradicted themselves before this existed.
    """
    if not value:
        return None
    entry = table.get(to_id(value))
    return entry["name"] if entry else value


def _defender_hp(reg: Regulation, defender: PokemonSet, forme: str) -> int:
    from vgc.regulation import to_id as _id
    from vgc.teams.sets import calc_stats
    return calc_stats(defender, reg.dex, reg, species_id=_id(forme))["hp"]


def _sweep(dc: DamageCalc, reg: Regulation, ev: DamageEvent, forme: str, stat: str,
           attacker_nature: str | None, defender: PokemonSet, target_forme: str,
           cap: int, boosts: dict[str, int] | None = None) -> list[list[int]]:
    """Damage rolls at every legal investment, in one round trip."""
    reqs = []
    for sp in range(cap + 1):
        attacker = {"species": forme,
                    "item": proper(reg.dex.items, ev.attacker_item),
                    "ability": proper(reg.dex.abilities, ev.attacker_ability),
                    "nature": attacker_nature, "sp": {stat: sp},
                    "boosts": attacker_boosts_for(ev) if boosts is None else boosts,
                    "status": "", "curHP": None}
        target = {"species": target_forme,
                  "item": proper(reg.dex.items, defender.item),
                  "ability": proper(reg.dex.abilities, defender.ability),
                  "nature": defender.nature,
                  "sp": defender.sp.as_dict(), "boosts": ev.target_boosts or {},
                  "status": ev.target_status or "", "curHP": None}
        reqs.append({"attacker": attacker, "defender": target,
                     "move": {"name": ev.move, "isCrit": bool(ev.crit)},
                     "field": _field(reg, ev)})
    return [(r.get("damage") or []) if r.get("ok") else [] for r in dc.batch(reqs)]


def summary(beliefs: dict[tuple[str, str], DamageBelief]) -> dict[str, Any]:
    vals = list(beliefs.values())
    return {
        "pokemon": len(vals),
        "observations_used": sum(b.used for b in vals),
        "censored": sum(b.censored for b in vals),
        "narrowed_mean": sum(b.narrowed for b in vals) / len(vals) if vals else 0.0,
        "any_narrowed": sum(1 for b in vals if b.narrowed > 0),
        "contradicted": sum(1 for b in vals if b.contradicted),
    }
