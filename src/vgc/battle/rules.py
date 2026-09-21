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

# Abilities that fire *because the holder arrived*, and therefore in Speed order. Derived from
# `onStart` in the pinned build (`tests/test_battle_rules.py` re-derives it), minus the handful
# that also fire on something else — Forecast and Mimicry react to weather and terrain, Ice Face
# and Flash Fire to being hit, Shields Down to its own HP. Those announce the same `-ability`
# line from a trigger that is not an arrival, so including them would let a mid-turn announcement
# be read as a race. Soundness over power, as everywhere else in this package.
AMBIGUOUS_TRIGGER = {"forecast", "mimicry", "iceface", "shieldsdown", "flashfire"}

SWITCH_IN_ABILITIES = {
    "anticipation", "cloudnine", "curiousmedicine", "drizzle", "drought", "electricsurge",
    "embodyaspectcornerstone", "embodyaspecthearthflame", "embodyaspectteal",
    "embodyaspectwellspring", "fairyaura", "forewarn", "frisk", "gluttony", "grassysurge",
    "hospitality", "intimidate", "klutz", "moldbreaker", "pressure", "psychicsurge",
    "sandstream", "screencleaner", "snowwarning", "supersweetsyrup", "supremeoverlord",
    "trace", "unnerve",
}


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
    """One thing that could happen next, and what it would tell you if it did.

    `ability` is what picking this outcome *pins*, and `excludes` is what it rules out — the
    second matters as much as the first. Nothing happening when Incineroar arrives is not an
    absence of information: Intimidate would have announced itself, so silence leaves Blaze.
    """

    label: str                                   # what the cartridge would print
    effects: list[dict[str, Any]] = field(default_factory=list)
    ability: str | None = None                   # picking this proves the ability is this
    excludes: frozenset[str] = frozenset()       # ...or that it is none of these
    item: str | None = None                      # or proves it was holding this
    consumed: bool = False                       # ...and has now used it up

    def to_json(self) -> dict[str, Any]:
        return {"label": self.label, "effects": self.effects, "ability": self.ability,
                "excludes": sorted(self.excludes), "item": self.item, "consumed": self.consumed}


def _name(reg: Regulation, ability: str) -> str:
    return (reg.dex.abilities.get(ability) or {}).get("name", ability)


def _collect(reg: Regulation, species: str, known: str | None,
             describe) -> list[Outcome]:
    """Build one outcome per ability that would announce, plus the silence that rules them out.

    `describe(ability)` returns `(what the cartridge prints, effects)` or None when that ability
    says nothing here.
    """
    loud: list[Outcome] = []
    quiet: list[str] = []
    for ability in candidate_abilities(reg, species, known):
        got = describe(ability)
        if got is None:
            quiet.append(ability)
        else:
            text, effects = got
            loud.append(Outcome(f"{_name(reg, ability)} — {text}", effects, ability=ability))
    if quiet:
        names = " or ".join(_name(reg, a) for a in quiet)
        if not loud:
            loud.append(Outcome("nothing announced"))
        elif len(quiet) == 1:
            loud.append(Outcome(f"nothing announced — so {names}", ability=quiet[0]))
        else:
            loud.append(Outcome(f"nothing announced — so {names}",
                                excludes=frozenset(o.ability for o in loud if o.ability)))
    return loud


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
    """What could fire when this Pokémon arrives — one outcome per ability still possible."""

    def describe(ability: str):
        if ability in WEATHER_ON_START:
            return (f"weather turns to {WEATHER_ON_START[ability]}",
                    [{"kind": "weather", "value": WEATHER_ON_START[ability]}])
        if ability in TERRAIN_ON_START:
            return (f"{TERRAIN_ON_START[ability]} covers the field",
                    [{"kind": "terrain", "value": TERRAIN_ON_START[ability]}])
        if ability in DROP_ON_START:
            stat, stages = DROP_ON_START[ability]
            return (f"{stat} {stages:+d} on both opposing Pokémon",
                    [{"kind": "drop_opposing", "stat": stat, "stages": stages}])
        return None

    return _collect(reg, species, known, describe)


def stat_drop_outcomes(reg: Regulation, species: str, stat: str, stages: int,
                       known: str | None = None) -> list[Outcome]:
    """What a drop aimed at this Pokémon could do, given what its ability might be.

    The second half of the Intimidate chain, and the reason it is worth deriving rather than
    assuming: against an unknown Pokémon the drop is not a foregone conclusion. Twelve legal
    abilities refuse it, one throws it back, and two answer it with +2 — and which of those you
    see pins the ability on the spot.
    """

    def describe(ability: str):
        if ability in REFLECTS_DROP:
            return ("the drop is sent back to whoever caused it",
                    [{"kind": "reflect", "stat": stat, "stages": stages}])
        guarded = BLOCKS_DROP.get(ability, "missing")
        if guarded is None or guarded == stat:
            return ("the drop is refused", [])
        if ability in REBOUND:
            rstat, rstages = REBOUND[ability]
            return (f"{stat} {stages:+d}, then {rstat} {rstages:+d}",
                    [{"kind": "boost", "stat": stat, "stages": stages},
                     {"kind": "boost", "stat": rstat, "stages": rstages}])
        return None

    out = _collect(reg, species, known, describe)
    # The plain case is not "nothing announced" — the drop lands and the cartridge says so — so it
    # is relabelled rather than left reading as silence.
    for o in out:
        if o.label.startswith("nothing announced"):
            rest = o.label.removeprefix("nothing announced")
            o.label = f"{stat} {stages:+d}, nothing else{rest}"
            o.effects = [{"kind": "boost", "stat": stat, "stages": stages}]
    return out


# --- items that announce themselves ------------------------------------------------------
#
# An item firing is worth as much as an ability firing and sometimes more: the damage channel
# needs the attacker's item and the bulk channel needs the defender's, and at team preview
# neither is known. The hypothesis space for an item is the whole legal list rather than the two
# or three an ability has — so these are keyed the other way round. Nothing asks *which item is
# it*; the trigger asks *did the one item that could respond to this fire*, which is one question
# with two answers however large the space is.
#
# Reg M-C narrows it further than expected: Clear Amulet, Covert Cloak, Weakness Policy, Room
# Service and Booster Energy are all illegal here, so the terrain seeds and the contact items are
# most of what is left.

SEEDS = {"grassyterrain": ("grassyseed", "def", 1), "electricterrain": ("electricseed", "def", 1),
         "mistyterrain": ("mistyseed", "spd", 1), "psychicterrain": ("psychicseed", "spd", 1)}


def on_terrain_set(reg: Regulation, terrain: str, held: str | None = None) -> list[Outcome]:
    """What a Pokémon could announce when this terrain comes up — the seeds, and nothing else.

    A seed fires on the terrain *and* on arriving into it, and is consumed either way, so the
    same two options serve a switch-in under standing terrain.
    """
    entry = SEEDS.get(to_id(terrain))
    if entry is None:
        return [Outcome("nothing announced")]
    item, stat, stages = entry
    if held is not None and to_id(held) != item:
        return [Outcome("nothing announced")]        # you already know it holds something else
    name = (reg.dex.items.get(item) or {}).get("name", item)
    return [
        Outcome(f"{name} — {stat} {stages:+d}, and it is used up",
                [{"kind": "boost", "stat": stat, "stages": stages}], item=item, consumed=True),
        Outcome("nothing announced"),
    ]


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

    Stamina matters twice over: it is a reveal, and it moves the Defence the bulk channel is
    about to read the next hit against.
    """
    mtype = (reg.dex.get_move(move) or {}).get("type")

    def describe(ability: str):
        effects: list[dict[str, Any]] = []
        for table in (HIT_BOOST_SELF, HIT_DROP_SELF):
            if ability in table:
                stat, stages = table[ability]
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
            return (", ".join(_describe(e) for e in effects), effects)
        if ability in ANNOUNCES_ON_HIT:
            # It announces and does something this model does not carry (a status, recoil, an
            # ability swap). Worth offering, because picking it still pins the ability.
            return ("announced, no stat or field change modelled", [])
        return None

    return _collect(reg, species, known, describe)


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


# --- applying one -------------------------------------------------------------------------

@dataclass
class Pending:
    """A decision this outcome created — the next pop-up, not a thing already done."""

    mon: Any
    stat: str
    stages: int
    source: Any = None


def apply(state: Any, outcome: Outcome, on: Any, source: Any = None) -> list[Pending]:
    """Run an outcome's effects, record what picking it proved, and return what to ask next.

    `on` is the Pokémon the outcome is about — the one arriving, the one the drop is aimed at,
    the one that was hit — and `source` is the other party, the Intimidate user or the attacker.
    Both the effect and the reveal come from the same call, because they came from the same tap.

    Intimidate does **not** apply its own drop here, and that is the whole shape of the chain: it
    aims a drop at each opposing Pokémon, and what that drop *does* depends on an ability nobody
    has established yet. So it comes back as a `Pending` — one pop-up per target — and applying
    it twice, once eagerly and once through the answer, is the mistake this prevents.

    **Only an entry adapter should call this.** A Showdown log already reports every one of these
    as its own line, so deriving them from a log applies them twice.
    """
    pending: list[Pending] = []
    for effect in outcome.effects:
        kind = effect["kind"]
        if kind == "boost":
            state.apply_boost(on, effect["stat"], effect["stages"])
        elif kind == "boost_attacker" and source is not None:
            state.apply_boost(source, effect["stat"], effect["stages"])
        elif kind == "reflect" and source is not None:
            state.apply_boost(source, effect["stat"], effect["stages"])
        elif kind == "drop_opposing":
            them = opposing_slots(state._side_of(on))
            pending += [Pending(mon, effect["stat"], effect["stages"], source=on)
                        for mon in state.sides[them].mons if mon.state == "active"]
        elif kind == "weather":
            state.set_weather(effect["value"])
        elif kind == "terrain":
            state.set_terrain(effect["value"])
    if outcome.ability:
        state.reveal(on, "ability", outcome.ability)
    on.ability_ruled_out |= set(outcome.excludes)
    if outcome.item:
        if outcome.consumed:
            state.consume_item(on, outcome.item)
        else:
            state.reveal(on, "item", outcome.item)
    return pending


def still_possible(reg: Regulation, mon: Any) -> list[str]:
    """What this Pokémon's ability could still be, after everything that has been ruled out.

    The narrowing half of the pop-up: once Intimidate has failed to announce itself, it should
    not be offered again, and if that leaves one candidate the app knows the ability without
    ever having been told.
    """
    if mon.ability:
        return [to_id(mon.ability)]
    return [a for a in candidate_abilities(reg, mon.species) if a not in mon.ability_ruled_out]
