"""What follows from what you saw — for the adapter whose input does not say.

A Showdown log reports consequences directly: Intimidate arrives as `|-ability|` *and*
`|-unboost|`, so `vgc.data.observe` must never derive anything or it applies the drop twice. A
cartridge is the other case. It names the cause and the effect in one line — *"Incineroar's
Intimidate cut Rillaboom's Attack!"* — so a person logs one trigger and everything downstream is
arithmetic on the rules.

**Derivation runs forward from a named cause, never backward from an effect.** That is what the
cartridge naming the ability buys, and it removes the hard half of the problem: no abduction, no
"what could have caused this", just a table.

**A prediction that does not come true is evidence.** At team preview an opposing ability is one of
the two or three its species can have, so Intimidate against an unknown Pokémon has several
possible outcomes — it lands, or Clear Body and eleven others refuse it, or Mirror Armor sends it
back, or Defiant and Competitive answer it with +2. Each outcome implies a different ability. So
`outcomes()` returns them all with what each would imply, the UI shows what happened, and the
choice *pins the ability*. The reveal is free: it is the same tap that logs the effect.

Every table here is derived from the pinned build in `tests/test_battle_rules.py` rather than
trusted — the dex export has now been found missing `multihit`, `overrideDefensiveStat`,
`overrideOffensiveStat` and `onModifyType`, so a hand-written list of abilities is exactly the kind
of thing that is quietly wrong two regulations from now.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from vgc.regulation import Regulation, to_id

# --- what an ability does when its holder arrives ----------------------------------------

WEATHER_ON_START = {"drizzle": "raindance", "drought": "sunnyday", "sandstream": "sandstorm",
                    "snowwarning": "snow"}
TERRAIN_ON_START = {"grassysurge": "grassyterrain", "electricsurge": "electricterrain",
                    "psychicsurge": "psychicterrain", "mistysurge": "mistyterrain"}
# Intimidate is the one that lowers a stat on both opposing actives; Supersweet Syrup does the
# same to evasion. Both are `onStart` in the pinned build.
DROP_ON_START = {"intimidate": ("atk", -1), "supersweetsyrup": ("evasion", -1)}

# --- and what the other side can do about a stat drop ------------------------------------

# `onAfterEachBoost`: answer a *lowered* stat with a raise of its own.
REBOUND = {"competitive": ("spa", 2), "defiant": ("atk", 2)}

# `onTryBoost`: refuse the drop outright. Several are stat-specific — Hyper Cutter and Big Pecks
# and Keen Eye each guard one stat — so the table says which, and `None` means all of them.
BLOCKS_DROP: dict[str, str | None] = {
    "clearbody": None, "whitesmoke": None, "fullmetalbody": None, "guarddog": "atk",
    "hypercutter": "atk", "bigpecks": "def", "keeneye": "accuracy", "illuminate": "accuracy",
    "innerfocus": "atk", "oblivious": "atk", "owntempo": "atk", "scrappy": "atk",
    "mirrorarmor": None,
}
# Mirror Armor does not merely refuse it — it sends the drop back to whoever caused it.
REFLECTS_DROP = {"mirrorarmor"}


@dataclass
class Outcome:
    """One thing that could happen next, and what it would tell you if it did."""

    label: str                                   # what the cartridge would print
    effects: list[dict[str, Any]] = field(default_factory=list)
    implies: dict[tuple[str, int], str] = field(default_factory=dict)   # slot → ability id

    def to_json(self) -> dict[str, Any]:
        return {"label": self.label, "effects": self.effects,
                "implies": {f"{s}:{i}": a for (s, i), a in self.implies.items()}}


def candidate_abilities(reg: Regulation, species: str, known: str | None = None) -> list[str]:
    """What this Pokémon's ability could still be.

    One entry once it is known — from a sheet, or because you watched it fire — and otherwise
    everything the species is allowed, which at team preview is two or three.
    """
    if known:
        return [to_id(known)]
    entry = reg.dex.get_species(species) or {}
    return sorted({to_id(a) for a in (entry.get("abilities") or {}).values()})


def opposing_slots(sid: str) -> str:
    return "p2" if sid == "p1" else "p1"


def on_switch_in(reg: Regulation, species: str, known: str | None = None) -> list[Outcome]:
    """What could fire when this Pokémon arrives — one outcome per ability still possible.

    Includes "nothing visible", because most abilities announce nothing on arrival and seeing
    nothing is itself informative: it rules out every ability that would have.
    """
    quiet = []
    out: list[Outcome] = []
    for ability in candidate_abilities(reg, species, known):
        name = (reg.dex.abilities.get(ability) or {}).get("name", ability)
        if ability in WEATHER_ON_START:
            out.append(Outcome(f"{name} — weather turns to {WEATHER_ON_START[ability]}",
                               [{"kind": "weather", "value": WEATHER_ON_START[ability]}]))
        elif ability in TERRAIN_ON_START:
            out.append(Outcome(f"{name} — {TERRAIN_ON_START[ability]} covers the field",
                               [{"kind": "terrain", "value": TERRAIN_ON_START[ability]}]))
        elif ability in DROP_ON_START:
            stat, stages = DROP_ON_START[ability]
            out.append(Outcome(f"{name} — {stat} {stages:+d} on both opposing Pokémon",
                               [{"kind": "drop_opposing", "stat": stat, "stages": stages}]))
        else:
            quiet.append(name)
    if quiet:
        out.append(Outcome("nothing announced" + (f" (so not {', '.join(sorted(quiet))})"
                                                  if len(quiet) < 3 else "")))
    return out


def stat_drop_outcomes(reg: Regulation, species: str, stat: str, stages: int,
                       known: str | None = None) -> list[Outcome]:
    """What a drop aimed at this Pokémon could do, given what its ability might be.

    This is the second half of the Intimidate chain and the reason it is worth deriving rather
    than assuming: against an unknown Pokémon the drop is not a foregone conclusion. Twelve legal
    abilities refuse it, one of them throws it back, and two answer it with +2 — and which of
    those you see pins the ability on the spot.
    """
    out: list[Outcome] = []
    plain = []
    for ability in candidate_abilities(reg, species, known):
        name = (reg.dex.abilities.get(ability) or {}).get("name", ability)
        guarded = BLOCKS_DROP.get(ability, "missing")
        if ability in REFLECTS_DROP:
            out.append(Outcome(f"{name} — the drop is sent back to whoever caused it",
                               [{"kind": "reflect", "stat": stat, "stages": stages}]))
        elif guarded is None or guarded == stat:
            out.append(Outcome(f"{name} — the drop is refused", []))
        elif ability in REBOUND:
            rstat, rstages = REBOUND[ability]
            out.append(Outcome(f"{name} — {stat} {stages:+d}, then {rstat} {rstages:+d}",
                               [{"kind": "boost", "stat": stat, "stages": stages},
                                {"kind": "boost", "stat": rstat, "stages": rstages}]))
        else:
            plain.append(name)
    if plain:
        out.append(Outcome(f"{stat} {stages:+d}",
                           [{"kind": "boost", "stat": stat, "stages": stages}]))
    return out


# --- and what fires when a Pokémon is hit ------------------------------------------------

# `onDamagingHit`. Split by what they do to state this models: a stat change on the holder, a
# stat change on the attacker, or a field effect. Several are conditional on the move's type,
# which is why the move is an argument and not an afterthought.
HIT_BOOST_SELF = {"stamina": ("def", 1), "weakarmor": ("spe", 2)}
HIT_DROP_SELF = {"weakarmor": ("def", -1)}
HIT_BOOST_SELF_IF_TYPE = {"justified": ("Dark", "atk", 1),
                          "thermalexchange": ("Fire", "atk", 1),
                          "rattled": (("Bug", "Dark", "Ghost"), "spe", 1)}
HIT_DROP_ATTACKER = {"gooey": ("spe", -1), "tanglinghair": ("spe", -1)}
HIT_WEATHER = {"sandspit": "sandstorm"}
HIT_TERRAIN = {"seedsower": "grassyterrain"}


def on_damaging_hit(reg: Regulation, species: str, move: str,
                    known: str | None = None) -> list[Outcome]:
    """What the Pokémon you just hit could announce, one outcome per ability still possible.

    The same shape as the other two triggers, and the same payoff: Stamina announcing itself is
    both a Defence boost to apply and proof of which ability that Pokémon has — which matters
    twice over, because a Defence that just went up is a Defence the bulk channel has to know
    about before it reads the next hit backwards.
    """
    entry = reg.dex.get_move(move) or {}
    mtype = entry.get("type")
    out: list[Outcome] = []
    quiet = []
    for ability in candidate_abilities(reg, species, known):
        name = (reg.dex.abilities.get(ability) or {}).get("name", ability)
        effects: list[dict[str, Any]] = []
        if ability in HIT_BOOST_SELF:
            stat, stages = HIT_BOOST_SELF[ability]
            effects.append({"kind": "boost", "stat": stat, "stages": stages})
        if ability in HIT_DROP_SELF:
            stat, stages = HIT_DROP_SELF[ability]
            effects.append({"kind": "boost", "stat": stat, "stages": stages})
        if ability in HIT_BOOST_SELF_IF_TYPE:
            want, stat, stages = HIT_BOOST_SELF_IF_TYPE[ability]
            want = (want,) if isinstance(want, str) else want
            if mtype in want:
                effects.append({"kind": "boost", "stat": stat, "stages": stages})
        if ability in HIT_DROP_ATTACKER:
            stat, stages = HIT_DROP_ATTACKER[ability]
            effects.append({"kind": "boost_attacker", "stat": stat, "stages": stages})
        if ability in HIT_WEATHER:
            effects.append({"kind": "weather", "value": HIT_WEATHER[ability]})
        if ability in HIT_TERRAIN:
            effects.append({"kind": "terrain", "value": HIT_TERRAIN[ability]})
        if effects:
            out.append(Outcome(f"{name} — " + ", ".join(_describe(e) for e in effects), effects))
        elif ability in ANNOUNCES_ON_HIT:
            # It announces and does something this model does not carry (a status, recoil, an
            # ability swap). Worth offering, because picking it still pins the ability.
            out.append(Outcome(f"{name} — announced, no stat or field change modelled", []))
        else:
            quiet.append(name)
    if quiet:
        out.append(Outcome("nothing announced" + (f" (so not {', '.join(sorted(quiet))})"
                                                  if len(quiet) < 3 else "")))
    return out


def _describe(effect: dict[str, Any]) -> str:
    if effect["kind"] == "weather":
        return f"weather turns to {effect['value']}"
    if effect["kind"] == "terrain":
        return f"{effect['value']} covers the field"
    who = "the attacker's " if effect["kind"] == "boost_attacker" else ""
    return f"{who}{effect['stat']} {effect['stages']:+d}"


# Announce themselves on being hit without changing a stat or the field. Listed so that picking
# one still records which ability it was, which is the point of the pop-up.
ANNOUNCES_ON_HIT = {"aftermath", "cursedbody", "cutecharm", "effectspore", "electromorphosis",
                    "flamebody", "gulpmissile", "illusion", "innardsout", "mummy", "poisonpoint",
                    "roughskin", "spicyspray", "static", "toxicdebris", "wanderingspirit"}
