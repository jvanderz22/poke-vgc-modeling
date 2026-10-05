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
    # True when this ability announces only *sometimes* in this exact situation — Static is a 30%
    # chance, Supreme Overlord needs a fallen ally. Such an ability is still worth offering,
    # because picking it pins the ability; what it must never do is let *silence* rule it out.
    conditional: bool = False

    def to_json(self) -> dict[str, Any]:
        return {"label": self.label, "effects": self.effects, "ability": self.ability,
                "excludes": sorted(self.excludes), "item": self.item, "consumed": self.consumed,
                "conditional": self.conditional}


def _name(reg: Regulation, ability: str) -> str:
    return (reg.dex.abilities.get(ability) or {}).get("name", ability)


def _collect(reg: Regulation, species: str, known: str | Iterable[str] | None,
             describe) -> list[Outcome]:
    """Build one outcome per ability that could announce, plus the silence that rules them out.

    `describe(ability)` returns `(what the cartridge prints, effects)`, or a third element that is
    True when the ability announces only *sometimes* here, or None when it never announces here.

    The three-way split is the whole soundness argument. Silence rules out only what would
    *definitely* have announced: a Pokémon that did not shock its attacker may still have Static,
    because Static is a 30% chance, and reading that silence as proof of Lightning Rod excludes
    the truth outright. So a conditional ability is offered — picking it still pins the ability,
    which is pure gain — and survives the silence alongside the abilities that never announce.
    """
    loud: list[Outcome] = []
    certain: list[str] = []      # would definitely have announced, so silence rules it out
    survives: list[str] = []     # consistent with silence: never announces here, or only might
    for ability in candidate_abilities(reg, species, known):
        got = describe(ability)
        if got is None:
            survives.append(ability)
            continue
        text, effects, *rest = got
        conditional = bool(rest and rest[0])
        loud.append(Outcome(f"{_name(reg, ability)} — {text}", effects, ability=ability,
                            conditional=conditional))
        (survives if conditional else certain).append(ability)
    if not survives:
        return loud
    names = " or ".join(_name(reg, a) for a in survives)
    if not loud:
        out = Outcome("nothing announced")
    elif len(survives) == 1:
        # Everything else would have said so and did not, so silence names this one outright.
        out = Outcome(f"nothing announced — so {names}", ability=survives[0])
    else:
        out = Outcome(f"nothing announced — so {names}", excludes=frozenset(certain))
    loud.append(out)
    return loud


def candidate_abilities(reg: Regulation, species: str,
                        known: str | Iterable[str] | None = None) -> list[str]:
    """What this Pokémon's ability could still be.

    One entry once it is known — from a sheet, or because you watched it fire — and otherwise
    everything the species is allowed, which at team preview is two or three.

    A *sequence* narrows it without settling it, which is what `still_possible` returns after
    something that would have announced did not: three candidates become two, and the next pop-up
    offers two options instead of three. Passing the species' full list is the same as passing
    nothing, so a caller that has not narrowed anything loses nothing by asking.
    """
    if isinstance(known, str):
        return [to_id(known)] if known else _allowed(reg, species)
    if known is not None:
        narrowed = sorted({to_id(a) for a in known})
        return narrowed or _allowed(reg, species)
    return _allowed(reg, species)


def _allowed(reg: Regulation, species: str) -> list[str]:
    entry = reg.dex.get_species(species) or {}
    return sorted({to_id(a) for a in (entry.get("abilities") or {}).values()})


def opposing_slots(sid: str) -> str:
    return "p2" if sid == "p1" else "p1"


def on_switch_in(reg: Regulation, species: str,
                 known: str | Iterable[str] | None = None, *,
                 weather: str | None = None, terrain: str | None = None) -> list[Outcome]:
    """What could fire when this Pokémon arrives — one outcome per ability still possible.

    A weather or terrain setter arriving to find its own effect already up says nothing: the pinned
    build's `setWeather` and `setTerrain` fail without a message when the effect is the same. So
    there it counts with the abilities that never announce, and if that leaves nothing to tell
    apart, nothing is asked.
    """

    def describe(ability: str):
        if WEATHER_ON_START.get(ability, "-") == weather or TERRAIN_ON_START.get(ability, "-") == terrain:
            return None
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
        if ability in SWITCH_IN_ABILITIES:
            # It announces on arrival and does something this state model does not carry — Frisk
            # reads an item, Pressure doubles PP, Trace copies an ability. Offered anyway, because
            # picking it pins the ability and puts the arrival in the Speed order, which is two
            # things the belief wants for one tap. Conditional, because several of them announce
            # only in the right circumstances: Supreme Overlord needs a fallen ally, Screen
            # Cleaner a screen to clear, Trace something worth tracing. Establishing which are
            # unconditional is a per-ability reading of the pinned build, and until that is done
            # the sound default is that silence proves nothing about them.
            return ("announced, nothing this model carries changed", [], True)
        return None

    return _collect(reg, species, known, describe)


def stat_drop_outcomes(reg: Regulation, species: str, stat: str, stages: int,
                       known: str | Iterable[str] | None = None) -> list[Outcome]:
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
        # Every branch above is unconditional: a blocker always blocks, Mirror Armor always
        # reflects, Defiant and Competitive always answer. Silence here really is proof.

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
                    known: str | Iterable[str] | None = None) -> list[Outcome]:
    """What the Pokémon you just hit could announce, one outcome per ability still possible.

    Stamina matters twice over: it is a reveal, and it moves the Defence the bulk channel is
    about to read the next hit against.
    """
    entry = reg.dex.get_move(move) or {}
    mtype, category = entry.get("type"), entry.get("category")
    contact = "contact" in (entry.get("flags") or [])   # the export gives flags as a list

    def describe(ability: str):
        effects: list[dict[str, Any]] = []
        for table in (HIT_BOOST_SELF, HIT_DROP_SELF):
            if ability in table:
                # Weak Armor reads the move's category, not just that a hit landed: a special
                # move leaves it silent, which makes its silence informative rather than useless.
                if ability == "weakarmor" and category != "Physical":
                    return None
                stat, stages = table[ability]
                effects.append({"kind": "boost", "stat": stat, "stages": stages})
        if ability in HIT_BOOST_SELF_IF_TYPE:
            want, stat, stages = HIT_BOOST_SELF_IF_TYPE[ability]
            want = (want,) if isinstance(want, str) else want
            if mtype in want:
                effects.append({"kind": "boost", "stat": stat, "stages": stages})
        if ability in HIT_DROP_ATTACKER:
            # Gooey and Tangling Hair need contact. Against a move that makes none they cannot
            # fire at all, which is a stronger statement than "might not have".
            if not contact:
                return None
            stat, stages = HIT_DROP_ATTACKER[ability]
            effects.append({"kind": "boost_attacker", "stat": stat, "stages": stages})
        if ability in HIT_WEATHER:
            effects.append({"kind": "weather", "value": HIT_WEATHER[ability]})
        if ability in HIT_TERRAIN:
            effects.append({"kind": "terrain", "value": HIT_TERRAIN[ability]})
        if effects:
            return (", ".join(_describe(e) for e in effects), effects)
        if ability in ANNOUNCES_ON_HIT:
            if ability in CONTACT_ON_HIT and not contact:
                return None             # it could not have fired, so its silence says nothing
            # It announces and does something this model does not carry — a status, recoil, an
            # ability swap. Worth offering, because picking it still pins the ability. Always
            # conditional: most of this set is a 30% chance (Static, Flame Body, Effect Spore,
            # Poison Point, Cute Charm, Cursed Body) and the rest need contact or a KO. Silence
            # therefore proves nothing about any of them, which is the bug this flag fixes —
            # a Pikachu that did not paralyse you is not thereby a Lightning Rod Pikachu.
            return ("announced, no stat or field change modelled", [], True)
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

# ...and of those, the ones that need the move to make contact. Against Earthquake, Rough Skin
# could not have fired at all — which is a stronger statement than "might not have", and the
# difference decides whether silence is allowed to rule the other candidates in. Derived from
# `checkMoveMakesContact` in the pinned build and re-derived by `tests/test_battle_rules.py`
# rather than trusted, like every other table here.
CONTACT_ON_HIT = {"aftermath", "cutecharm", "effectspore", "flamebody", "gooey", "mummy",
                  "poisonpoint", "roughskin", "static", "tanglinghair", "wanderingspirit"}


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
        # Setting the weather or terrain already up fails in the game and leaves its turns as they
        # were (Seed Sower hit again under its own terrain), so it is not set again here.
        elif kind == "weather":
            if state.weather != effect["value"]:
                state.set_weather(effect["value"])
        elif kind == "terrain":
            if state.terrain != effect["value"]:
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


# --- what a move does to stats -------------------------------------------------------------
#
# The cartridge names the move, and the rest follows: Close Combat lowers its user's Defence and
# Special Defence once it connects, Snarl lowers each target's Special Attack, Swords Dance raises
# Attack. Three tables, re-derived from the pinned build's `moves.ts` in
# `tests/test_battle_rules.py`, because the dex export carries none of `self`, `boosts` or
# `secondary`:
#
# * `MOVE_SELF`: what a move does to its user (`self.boosts`, or `boosts` on a self-target move);
# * `MOVE_TARGET`: what a status move does to its target (`boosts`, or an `onHit` boost);
# * `MOVE_SECONDARY`: a damaging move's added effect on a target it hit, or on its user, with its
#   chance. At 100 it is certain and follows on its own; below that it happened only if you say so.
#
# Flinch and confusion are left out: they are volatiles this state model does not carry.

MOVE_SELF = {
    "acidarmor": {"def": 2}, "agility": {"spe": 2}, "amnesia": {"spd": 2},
    "armorcannon": {"def": -1, "spd": -1}, "bulkup": {"atk": 1, "def": 1},
    "calmmind": {"spa": 1, "spd": 1}, "charge": {"spd": 1},
    "clangoroussoul": {"atk": 1, "def": 1, "spa": 1, "spd": 1, "spe": 1},
    "closecombat": {"def": -1, "spd": -1}, "coil": {"accuracy": 1, "atk": 1, "def": 1},
    "cosmicpower": {"def": 1, "spd": 1}, "cottonguard": {"def": 3}, "doubleteam": {"evasion": 1},
    "dracometeor": {"spa": -2}, "dragondance": {"atk": 1, "spe": 1},
    "growth": {"atk": 1, "spa": 1}, "hammerarm": {"spe": -1},
    "headlongrush": {"def": -1, "spd": -1}, "icehammer": {"spe": -1}, "irondefense": {"def": 2},
    "leafstorm": {"spa": -2}, "makeitrain": {"spa": -1}, "minimize": {"evasion": 2},
    "nastyplot": {"spa": 2}, "noretreat": {"atk": 1, "def": 1, "spa": 1, "spd": 1, "spe": 1},
    "overheat": {"spa": -2}, "quiverdance": {"spa": 1, "spd": 1, "spe": 1},
    "rockpolish": {"spe": 2}, "shellsmash": {"atk": 2, "def": -1, "spa": 2, "spd": -1, "spe": 2},
    "shelter": {"def": 2}, "shiftgear": {"atk": 1, "spe": 2}, "superpower": {"atk": -1, "def": -1},
    "swordsdance": {"atk": 2},
}
MOVE_TARGET = {
    "aromaticmist": {"spd": 1}, "babydolleyes": {"atk": -1}, "bellydrum": {"atk": 12},
    "charm": {"atk": -2}, "coaching": {"atk": 1, "def": 1}, "cottonspore": {"spe": -2},
    "decorate": {"atk": 2, "spa": 2}, "eerieimpulse": {"spa": -2}, "faketears": {"spd": -2},
    "featherdance": {"atk": -2}, "flatter": {"spa": 1}, "howl": {"atk": 1},
    "magneticflux": {"def": 1, "spd": 1}, "memento": {"atk": -2, "spa": -2},
    "metalsound": {"spd": -2}, "nobleroar": {"atk": -1, "spa": -1},
    "partingshot": {"atk": -1, "spa": -1}, "scaryface": {"spe": -2}, "screech": {"def": -2},
    "spicyextract": {"atk": 2, "def": -2}, "stockpile": {"def": 1, "spd": 1},
    "strengthsap": {"atk": -1}, "stringshot": {"spe": -2}, "swagger": {"atk": 2},
    "sweetscent": {"evasion": -2}, "tearfullook": {"atk": -1, "spa": -1},
    "tickle": {"atk": -1, "def": -1}, "toxicthread": {"spe": -1},
}
MOVE_SECONDARY = {
    "acidspray": [{"chance": 100, "target": {"spd": -2}}],
    "ancientpower": [{"chance": 10, "self": {"atk": 1, "def": 1, "spa": 1, "spd": 1, "spe": 1}}],
    "appleacid": [{"chance": 100, "target": {"spd": -1}}],
    "aquastep": [{"chance": 100, "self": {"spe": 1}}],
    "aurawheel": [{"chance": 100, "self": {"spe": 1}}],
    "barbbarrage": [{"chance": 50, "status": "psn"}],
    "bittermalice": [{"chance": 100, "target": {"atk": -1}}],
    "blazekick": [{"chance": 10, "status": "brn"}], "blizzard": [{"chance": 10, "status": "frz"}],
    "bodyslam": [{"chance": 30, "status": "par"}], "bounce": [{"chance": 30, "status": "par"}],
    "breakingswipe": [{"chance": 100, "target": {"atk": -1}}],
    "bugbuzz": [{"chance": 10, "target": {"spd": -1}}],
    "bulldoze": [{"chance": 100, "target": {"spe": -1}}],
    "chargebeam": [{"chance": 70, "self": {"spa": 1}}],
    "chillingwater": [{"chance": 100, "target": {"atk": -1}}],
    "crosspoison": [{"chance": 10, "status": "psn"}],
    "crunch": [{"chance": 20, "target": {"def": -1}}],
    "crushclaw": [{"chance": 50, "target": {"def": -1}}],
    "discharge": [{"chance": 30, "status": "par"}],
    "drumbeating": [{"chance": 100, "target": {"spe": -1}}],
    "earthpower": [{"chance": 10, "target": {"spd": -1}}],
    "electroweb": [{"chance": 100, "target": {"spe": -1}}],
    "energyball": [{"chance": 10, "target": {"spd": -1}}],
    "fierydance": [{"chance": 50, "self": {"spa": 1}}],
    "fireblast": [{"chance": 10, "status": "brn"}], "firefang": [{"chance": 10, "status": "brn"}],
    "firelash": [{"chance": 100, "target": {"def": -1}}],
    "firepunch": [{"chance": 10, "status": "brn"}],
    "flamecharge": [{"chance": 100, "self": {"spe": 1}}],
    "flamethrower": [{"chance": 10, "status": "brn"}],
    "flareblitz": [{"chance": 10, "status": "brn"}],
    "flashcannon": [{"chance": 10, "target": {"spd": -1}}],
    "focusblast": [{"chance": 10, "target": {"spd": -1}}],
    "freezedry": [{"chance": 10, "status": "frz"}],
    "gravapple": [{"chance": 100, "target": {"def": -1}}],
    "gunkshot": [{"chance": 30, "status": "psn"}], "heatwave": [{"chance": 10, "status": "brn"}],
    "icebeam": [{"chance": 10, "status": "frz"}], "icefang": [{"chance": 10, "status": "frz"}],
    "icepunch": [{"chance": 10, "status": "frz"}],
    "icywind": [{"chance": 100, "target": {"spe": -1}}],
    "infernalparade": [{"chance": 30, "status": "brn"}],
    "inferno": [{"chance": 100, "status": "brn"}],
    "irontail": [{"chance": 30, "target": {"def": -1}}],
    "lavaplume": [{"chance": 30, "status": "brn"}],
    "liquidation": [{"chance": 20, "target": {"def": -1}}],
    "lowsweep": [{"chance": 100, "target": {"spe": -1}}],
    "luminacrash": [{"chance": 100, "target": {"spd": -2}}],
    "lunge": [{"chance": 100, "target": {"atk": -1}}],
    "matchagotcha": [{"chance": 20, "status": "brn"}],
    "meteormash": [{"chance": 20, "self": {"atk": 1}}],
    "moonblast": [{"chance": 30, "target": {"spa": -1}}],
    "mortalspin": [{"chance": 100, "status": "psn"}],
    "muddywater": [{"chance": 30, "target": {"accuracy": -1}}],
    "mudshot": [{"chance": 100, "target": {"spe": -1}}],
    "mudslap": [{"chance": 100, "target": {"accuracy": -1}}],
    "mysticalfire": [{"chance": 100, "target": {"spa": -1}}],
    "nightdaze": [{"chance": 40, "target": {"accuracy": -1}}],
    "nuzzle": [{"chance": 100, "status": "par"}],
    "playrough": [{"chance": 10, "target": {"atk": -1}}],
    "poisonfang": [{"chance": 50, "status": "tox"}],
    "poisonjab": [{"chance": 30, "status": "psn"}],
    "pounce": [{"chance": 100, "target": {"spe": -1}}],
    "psychic": [{"chance": 10, "target": {"spd": -1}}],
    "psyshieldbash": [{"chance": 100, "self": {"def": 1}}],
    "pyroball": [{"chance": 10, "status": "brn"}],
    "rapidspin": [{"chance": 100, "self": {"spe": 1}}],
    "razorshell": [{"chance": 50, "target": {"def": -1}}],
    "rocktomb": [{"chance": 100, "target": {"spe": -1}}],
    "scald": [{"chance": 30, "status": "brn"}],
    "scorchingsands": [{"chance": 30, "status": "brn"}],
    "shadowball": [{"chance": 20, "target": {"spd": -1}}],
    "shellsidearm": [{"chance": 20, "status": "psn"}],
    "skittersmack": [{"chance": 100, "target": {"spa": -1}}],
    "sludgebomb": [{"chance": 30, "status": "psn"}],
    "sludgewave": [{"chance": 10, "status": "psn"}],
    "snarl": [{"chance": 100, "target": {"spa": -1}}],
    "spiritbreak": [{"chance": 100, "target": {"spa": -1}}],
    "steelwing": [{"chance": 10, "self": {"def": 1}}],
    "strugglebug": [{"chance": 100, "target": {"spa": -1}}],
    "thunder": [{"chance": 30, "status": "par"}], "thunderbolt": [{"chance": 10, "status": "par"}],
    "thunderfang": [{"chance": 10, "status": "par"}],
    "thunderpunch": [{"chance": 10, "status": "par"}],
    "torchsong": [{"chance": 100, "self": {"spa": 1}}],
    "trailblaze": [{"chance": 100, "self": {"spe": 1}}],
    "triplearrows": [{"chance": 50, "target": {"def": -1}}],
    "tropkick": [{"chance": 100, "target": {"atk": -1}}],
    "volttackle": [{"chance": 10, "status": "par"}],
    "zapcannon": [{"chance": 100, "status": "par"}],
}


# Abilities in `BLOCKS_DROP` that refuse only Intimidate. A move's drop goes straight through them.
INTIMIDATE_ONLY = {"innerfocus", "oblivious", "owntempo", "scrappy", "guarddog"}
# Items that answer a drop: White Herb puts lowered stats back and is used up, Clear Amulet
# refuses a drop another Pokémon caused. Offered for an unseen item when at least this share of
# the sets still possible hold it.
DROP_ITEMS = {"whiteherb": "White Herb", "clearamulet": "Clear Amulet"}
DROP_ITEM_SHARE = 0.03


def _stats(boosts: dict[str, int]) -> str:
    return ", ".join(f"{k} {v:+d}" for k, v in boosts.items())


def drop_outcomes(reg: Regulation, species: str, drops: dict[str, int],
                  known: str | Iterable[str] | None = None, *, by_other: bool,
                  item: str | None = None, items: Iterable[str] = ()) -> list[Outcome]:
    """What a move's stat drops could do to this Pokémon: land, or be answered.

    A drop the Pokémon did to itself (Close Combat) is answered by nothing but a White Herb. One
    another Pokémon caused (Snarl, Parting Shot) can also be refused by Clear Body and its kind,
    sent back by Mirror Armor, or answered by Defiant and Competitive — but not by the abilities
    that refuse only Intimidate. `item` is the held item where known (None: unseen), and `items`
    the reacting items an unseen one could plausibly be.
    """
    plain = Outcome(f"{_stats(drops)}, nothing else", [{"kind": "boost", "stat": k, "stages": v} for k, v in drops.items()])
    if not by_other:
        out = [plain]
    else:
        def describe(ability: str):
            if ability in INTIMIDATE_ONLY:
                return None
            if ability in REFLECTS_DROP:
                return ("the drops are sent back to whoever caused them",
                        [{"kind": "reflect", "stat": k, "stages": v} for k, v in drops.items()])
            guarded = BLOCKS_DROP.get(ability, "missing")
            if guarded is None or (guarded in drops and len(drops) == 1):
                return ("the drops are refused", [])
            if guarded in drops:
                rest = {k: v for k, v in drops.items() if k != guarded}
                return (f"{guarded} kept, {_stats(rest)}",
                        [{"kind": "boost", "stat": k, "stages": v} for k, v in rest.items()])
            if ability in REBOUND:
                rstat, rstages = REBOUND[ability]
                return (f"{_stats(drops)}, then {rstat} {rstages:+d}",
                        [{"kind": "boost", "stat": k, "stages": v} for k, v in drops.items()]
                        + [{"kind": "boost", "stat": rstat, "stages": rstages}])
            return None

        out = _collect(reg, species, known, describe)
        for o in out:
            if o.label.startswith("nothing announced"):
                o.label = plain.label + o.label.removeprefix("nothing announced")
                o.effects = list(plain.effects)
        if not out:
            out = [plain]
    return with_drop_items(out, item=item, items=items, by_other=by_other)


def with_drop_items(outcomes: list[Outcome], *, item: str | None, items: Iterable[str],
                    by_other: bool) -> list[Outcome]:
    """Let a held item answer a drop. A known White Herb puts back whatever lands and is used up;
    a known Clear Amulet refuses another Pokémon's drop outright; an unseen item that could well be
    either adds that as one more thing that might have happened, and picking it is the reveal."""
    item = to_id(item or "") if item is not None else None
    if item == "clearamulet" and by_other:
        return [Outcome("Clear Amulet: the drops are refused", [], item="clearamulet")]

    def herb(o: Outcome) -> Outcome | None:
        kept = [e for e in o.effects if not (e["kind"] == "boost" and e["stages"] < 0)]
        if len(kept) == len(o.effects):
            return None
        return Outcome(f"{o.label.split(' — ')[0].removesuffix(', nothing else')}, then White Herb puts them back (used up)",
                       kept, ability=o.ability, excludes=o.excludes, item="whiteherb", consumed=True)

    if item == "whiteherb":
        return [herb(o) or o for o in outcomes]
    if item is not None:
        return outcomes
    out = list(outcomes)
    if "clearamulet" in items and by_other:
        out.insert(len(out) - 1 if len(out) > 1 else len(out),
                   Outcome("Clear Amulet: the drops are refused", [], item="clearamulet"))
    if "whiteherb" in items:
        plain = next((o for o in outcomes if o.label.split(" — ")[0].endswith("nothing else")), outcomes[-1])
        h = herb(plain)
        if h is not None:
            h.ability, h.excludes = None, frozenset()   # the herb says nothing about the ability
            out.append(h)
    return out


# --- the end of the turn ------------------------------------------------------------------
#
# What could change HP once everyone has moved, in the order the pinned build resolves it
# (`onResidualOrder`): weather first, then Grassy Terrain's heal, then held items, then poison,
# then burn. Each is offered for a Pokémon it could apply to and confirmed by a person, who saw
# whether it did — so this list is a set of possibilities with the HP each would leave, never a
# prediction applied on their behalf. Within one effect the cartridge goes in Speed order, which is
# for the person to report and not for this list to suggest.

SAND_IMMUNE_TYPES = {"Rock", "Ground", "Steel"}
SAND_IMMUNE_ABILITIES = {"overcoat", "sandveil", "sandrush", "sandforce", "magicguard"}
SAND_IMMUNE_ITEMS = {"safetygoggles"}
# Heals and drains that come from a held item, as a fraction of max HP.
RESIDUAL_ITEMS = {"leftovers": ("heal", 1 / 16)}
# Offer an unseen item's heal when at least this share of the sets still possible hold it. A
# confirm is then both the heal and the reveal.
UNSEEN_ITEM_SHARE = 0.1


def end_of_turn(reg: Regulation, state: Any, toxic: dict[tuple[str, str], int] | None = None
                ) -> list[dict[str, Any]]:
    """Every end-of-turn HP change that could apply now, with the HP it would leave.

    Each is `{key, effect, side, slot, species, kind: damage | heal, expect: {pct} | {hp}, reveal}`:
    `expect` is a percentage for a side whose HP is shown that way and a real number for your own,
    and `reveal` names an item the confirm would show (an unseen Leftovers). `toxic` is how many
    turns each badly poisoned Pokémon has been poisoned for, keyed `(side, species)`.
    """
    from vgc.regulation import is_grounded

    toxic = toxic or {}
    mons = [(sid, slot, m) for sid in ("p1", "p2") for slot in (0, 1)
            if (m := state.at(sid, slot)) is not None and m.hp > 0]
    out: list[dict[str, Any]] = []

    def types(m: Any) -> list[str]:
        return (reg.dex.get_species(m.forme) or {}).get("types") or []

    def add(effect: str, sid: str, slot: int, m: Any, kind: str, frac: float, reveal: str | None = None) -> None:
        if kind == "heal" and m.hp >= 1:
            return
        sign = 1 if kind == "heal" else -1
        if m.hp_max and state._own(sid):
            now = round(m.hp * m.hp_max)
            expect = {"hp": max(0, min(m.hp_max, now + sign * max(1, int(m.hp_max * frac))))}
        else:
            expect = {"pct": max(0, min(100, round(100 * m.hp + sign * 100 * frac)))}
        out.append({"key": f"{to_id(effect)}:{sid}{slot}", "effect": effect, "side": sid, "slot": slot,
                    "species": m.forme, "kind": kind, "expect": expect, "reveal": reveal})

    def ability(m: Any) -> str:
        return to_id(state._active_ability(m) or "")

    if state.weather == "sandstorm":
        for sid, slot, m in mons:
            if (SAND_IMMUNE_TYPES & set(types(m)) or ability(m) in SAND_IMMUNE_ABILITIES
                    or to_id(m.item or "") in SAND_IMMUNE_ITEMS):
                continue
            add("Sandstorm", sid, slot, m, "damage", 1 / 16)
    if state.terrain == "grassyterrain":
        for sid, slot, m in mons:
            if is_grounded(reg.dex, m.forme, state._active_ability(m), m.item):
                add("Grassy Terrain", sid, slot, m, "heal", 1 / 16)
    for sid, slot, m in mons:
        item = to_id(m.item or "")
        if item == "blacksludge":
            poison = "Poison" in types(m)
            add("Black Sludge", sid, slot, m, "heal" if poison else "damage", 1 / 16 if poison else 1 / 8)
        elif item in RESIDUAL_ITEMS:
            kind, frac = RESIDUAL_ITEMS[item]
            add(reg.dex.get_item(item)["name"], sid, slot, m, kind, frac)
        elif m.item is None:
            for unseen in _likely_items(reg, m):
                kind, frac = RESIDUAL_ITEMS[unseen]
                add(reg.dex.get_item(unseen)["name"], sid, slot, m, kind, frac, reveal=unseen)
    for sid, slot, m in mons:
        if ability(m) == "magicguard":
            continue
        if m.status == "psn":
            if ability(m) == "poisonheal":
                add("Poison Heal", sid, slot, m, "heal", 1 / 8)
            else:
                add("Poison", sid, slot, m, "damage", 1 / 8)
        elif m.status == "tox":
            if ability(m) == "poisonheal":
                add("Poison Heal", sid, slot, m, "heal", 1 / 8)
            else:
                n = toxic.get((sid, m.species), 1)
                add("Bad poison", sid, slot, m, "damage", min(15, max(1, n)) / 16)
    for sid, slot, m in mons:
        if m.status == "brn" and ability(m) != "magicguard":
            add("Burn", sid, slot, m, "damage", 1 / 16)
    return out


def _likely_items(reg: Regulation, mon: Any) -> list[str]:
    """Residual items an unseen item could well be, by the share of sets still possible."""
    from vgc.belief import sets as set_belief

    try:
        p = set_belief.given(reg, mon).item()
    except Exception:                    # no corpus built: nothing to go on, so offer nothing
        return []
    return [i for i in RESIDUAL_ITEMS if p.get(i, 0.0) >= UNSEEN_ITEM_SHARE]
