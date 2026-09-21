"""Your damage, read backwards to *their* bulk.

The channels so far read what the opponent did. This one reads what you did to them: a move of
yours whose every term you know — species, item, ability, nature, spread — landed for a measured
fraction of their health, and the only unknowns left are their HP investment and their investment
in the stat that resisted it.

**It is one equation in two unknowns, and that is the interesting part.** The fraction lost is
`damage(defence) / hp`, and damage falls roughly as 1/defence, so the fraction is roughly
`1 / (hp × defence)`. The indistinguishable direction is therefore a hyperbola — more HP and less
Defence looks exactly like less HP and more Defence — while moving *both* up or both down changes
what you see. So a single observation does not locate the split and does locate the product, which
is the quantity a player actually means by "physically bulky". The feasible set is a band around
that curve, and reporting it as two independent ranges would throw away most of it: that is why
`vgc.belief.sp.Block` carries regions over several stats rather than one set per stat.

**The split is not assumed.** HP is conventionally bought before a defensive stat, but not always —
spreads are built defence-first, or to an exact HP/Defence pair chosen to survive one named move.
So the convention may weight a prior and must not prune the region. What does separate the two
honestly is a second observation of a different kind: a physical hit and a special hit on the same
Pokémon share their HP term and constrain it jointly.

**Cost.** Damage depends on the defensive stat and not on HP, so one sweep of 33 calc calls covers
the whole 33×33 grid and the HP axis is arithmetic — `hp = base + SP + 75` at level 50. Same
abstentions as `vgc.belief.damage`, for the same reasons, plus the field effects neither models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from vgc.belief.damage import (ABSTAIN_ABILITIES, attacker_boosts_for, censored, observed_loss,
                               proper, usable, _field)
from vgc.data.observe import DamageEvent, Observer
from vgc.engine.calc import DamageCalc
from vgc.regulation import Regulation, defensive_stat, to_id
from vgc.teams.sets import PokemonSet

BULK_STATS = ("hp", "def", "spd")


@dataclass
class BulkBelief:
    """What is still possible for one Pokémon's HP and defensive investment, as regions.

    One region per defensive stat that was ever tested, each a set of `(hp_sp, stat_sp)` pairs.
    They are kept apart until something needs them together, because a Pokémon hit only physically
    has said nothing whatever about its Special Defence and a product set would imply otherwise.
    """

    side: str
    species: str
    regions: dict[str, set[tuple[int, int]]] = field(default_factory=dict)
    cap: int = 32
    used: int = 0
    censored: int = 0
    abstained: int = 0
    contradicted: bool = False

    def _full(self) -> set[tuple[int, int]]:
        return {(h, d) for h in range(self.cap + 1) for d in range(self.cap + 1)}

    @property
    def narrowed(self) -> float:
        """Share of the HP × defence grid ruled out, averaged over the stats actually tested."""
        if not self.regions:
            return 0.0
        whole = (self.cap + 1) ** 2
        return sum(1 - len(r) / whole for r in self.regions.values()) / len(self.regions)

    def triples(self) -> set[tuple[int, int, int]] | None:
        """`(hp, def, spd)` still possible, or None when nothing was learned.

        The join is on HP, which is the term the two regions share and the only reason a physical
        and a special hit on the same Pokémon say more together than apart.
        """
        if not self.regions:
            return None
        rng = range(self.cap + 1)
        phys = self.regions.get("def")
        spec = self.regions.get("spd")
        by_hp_d: dict[int, set[int]] = {}
        by_hp_s: dict[int, set[int]] = {}
        for h, d in (phys or {(h, d) for h in rng for d in rng}):
            by_hp_d.setdefault(h, set()).add(d)
        for h, s in (spec or {(h, s) for h in rng for s in rng}):
            by_hp_s.setdefault(h, set()).add(s)
        out = set()
        for h in set(by_hp_d) & set(by_hp_s):
            for d in by_hp_d[h]:
                for s in by_hp_s[h]:
                    out.add((h, d, s))
        return out

    def bounds(self) -> dict[str, tuple[int, int]]:
        """Per-stat projections — a lossy view, kept for reporting and never for inference."""
        out: dict[str, tuple[int, int]] = {}
        for stat, region in self.regions.items():
            if not region:
                continue
            out["hp"] = (min(h for h, _ in region), max(h for h, _ in region))
            out[stat] = (min(v for _, v in region), max(v for _, v in region))
        return out

    def _apply(self, stat: str, keep: set[tuple[int, int]]) -> None:
        """Narrow, unless that empties the region — see `vgc.belief.speed.SpeedBelief._apply`."""
        if keep:
            self.regions[stat] = keep
        else:
            self.contradicted = True
            self.regions.pop(stat, None)

    def to_json(self) -> dict[str, Any]:
        return {"side": self.side, "species": self.species, "bounds": self.bounds(),
                "tested": sorted(self.regions), "narrowed": round(self.narrowed, 4),
                "used": self.used, "censored": self.censored, "abstained": self.abstained,
                "contradicted": self.contradicted}


def hp_stat(reg: Regulation, forme: str, sp: int) -> int | None:
    """`base + SP + 75` at level 50 — the one stat with no nature multiplier."""
    entry = reg.dex.get_species(forme)
    if entry is None:
        return None
    base = entry["baseStats"]["hp"]
    return 1 if base == 1 else base + sp + 75


def infer(reg: Regulation, obs: Observer, known: dict[tuple[str, str], PokemonSet],
          dc: DamageCalc, cap: int | None = None) -> dict[tuple[str, str], BulkBelief]:
    """Feasible HP × defence for every Pokémon that one of *your* moves landed on.

    The mirror of `vgc.belief.damage.infer`: there the attacker is unknown and the defender has to
    be known, here the attacker is known and the defender is the one being read. An event with
    unknowns on both sides constrains the pair jointly and is skipped by both.
    """
    cap = reg.sp_per_stat_cap if cap is None else cap
    sheets = {(sid, mon.species): mon
              for sid, side in obs.sides.items() for mon in side.mons}

    beliefs: dict[tuple[str, str], BulkBelief] = {}
    for ev in obs.damage_log:
        akey = (ev.attacker_side, ev.attacker)
        dkey = (ev.target_side, ev.target)
        if akey not in known or dkey in known:
            continue
        if usable(reg, ev) is not None:
            continue
        if to_id(ev.attacker_ability or "") in ABSTAIN_ABILITIES:
            continue
        stat = defensive_stat(reg.dex, ev.move)
        target = sheets.get(dkey)
        if stat is None or target is None:
            continue
        # The defender's own ability can scale what it takes in ways no spread explains, so the
        # same table applies to whichever side is being read.
        if to_id(target.ability or "") in ABSTAIN_ABILITIES:
            continue
        forme = ev.target_forme or ev.target
        attacker = known[akey]
        a_forme = ev.attacker_forme or ev.attacker
        if reg.dex.get_species(forme) is None or reg.dex.get_species(a_forme) is None:
            continue

        rolls = _sweep(dc, reg, ev, attacker, a_forme, target, forme, stat, cap)
        belief = beliefs.get(dkey)
        if belief is None:
            belief = beliefs[dkey] = BulkBelief(side=dkey[0], species=dkey[1], cap=cap)
        belief.regions.setdefault(stat, belief._full())

        keep = set()
        for hp_sp in range(cap + 1):
            hp = hp_stat(reg, forme, hp_sp)
            if hp is None:
                continue
            lo, hi = observed_loss(ev, hp)
            for d_sp, damage in enumerate(rolls):
                if not damage or (hp_sp, d_sp) not in belief.regions[stat]:
                    continue
                if censored(ev):
                    # Right-censored: the move did *at least* what was left, so anything that can
                    # reach it stays in. A KO — or a sash — says far less than a survived hit.
                    if max(damage) >= lo:
                        keep.add((hp_sp, d_sp))
                elif min(damage) <= hi and max(damage) >= lo:
                    keep.add((hp_sp, d_sp))
        belief._apply(stat, keep)
        belief.used += 1
        belief.censored += censored(ev)
    return beliefs


def _sweep(dc: DamageCalc, reg: Regulation, ev: DamageEvent, attacker: PokemonSet, a_forme: str,
           target: PokemonSet, forme: str, stat: str, cap: int) -> list[list[int]]:
    """Damage at every legal investment in the defending stat, in one round trip.

    HP is deliberately absent: it does not enter the damage roll at all, only the fraction it
    represents, so 33 calls cover the whole 33×33 grid.
    """
    reqs = []
    for sp in range(cap + 1):
        reqs.append({
            # Forme, item and ability come from the *event*, not from your team file: the
            # Observer has already resolved what a Mega Evolution changed, and a sheet has not.
            # Mega Golisopod has Tough Claws where Golisopod has Emergency Exit, which is 1.3x on
            # every contact move — enough on its own to put the truth outside the region.
            "attacker": {"species": a_forme,
                         "item": proper(reg.dex.items, ev.attacker_item or attacker.item),
                         "ability": proper(reg.dex.abilities, ev.attacker_ability or attacker.ability),
                         "nature": attacker.nature, "sp": attacker.sp.as_dict(),
                         "boosts": attacker_boosts_for(ev),
                         "status": ev.attacker_status or "", "curHP": None},
            # The target's item and ability as of this moment too: `obs.sides` holds the state at
            # the *end* of the battle, so a Pokémon whose item was knocked off reads as having
            # never held one — and Knock Off is 1.5x exactly when there was something to remove.
            "defender": {"species": forme,
                         "item": proper(reg.dex.items, ev.target_item if ev.target_item is not None
                                        else target.item),
                         "ability": proper(reg.dex.abilities, ev.target_ability or target.ability),
                         "nature": target.nature, "sp": {stat: sp},
                         "boosts": ev.target_boosts or {},
                         "status": ev.target_status or "", "curHP": None},
            "move": {"name": ev.move, "isCrit": bool(ev.crit)},
            "field": _field(reg, ev),
        })
    return [(r.get("damage") or []) if r.get("ok") else [] for r in dc.batch(reqs)]


def summary(beliefs: dict[tuple[str, str], BulkBelief]) -> dict[str, Any]:
    vals = list(beliefs.values())
    both = [b for b in vals if len(b.regions) == 2]
    return {
        "pokemon": len(vals),
        "observations_used": sum(b.used for b in vals),
        "censored": sum(b.censored for b in vals),
        "narrowed_mean": sum(b.narrowed for b in vals) / len(vals) if vals else 0.0,
        # The ones where HP is actually separable: a physical and a special hit share the HP term.
        "hit_both_ways": len(both),
        "contradicted": sum(1 for b in vals if b.contradicted),
    }
