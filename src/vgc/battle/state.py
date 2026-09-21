"""L1b — what a battle *is*, separately from how you found out.

`Observer` used to be both: a Showdown protocol parser and the battle state that parsing produced.
That is the right shape when a log is the only input, and the wrong one as soon as a person is
watching a cartridge, where there is no log — you see state, not events, and forcing what you saw
back into protocol lines so a parser can decode them again is a round trip through a format that
exists for a different reason.

So the state lives here and knows nothing about Showdown. `vgc.data.observe.Observer` is one
adapter onto it, driven by protocol lines; manual entry is another, driven by a person. Both end
up with the same object, which is what lets win probability and all four belief channels work
without caring which one filled it in.

**`observation()` is a published interface.** Snapshots embed it and fingerprint it at VERSION 3,
so 433,052 frozen training rows depend on its exact bytes, and `tests/test_observe_golden.py`
holds it to them — over both the replay path and the request-fed path that built those rows. The
split above was made under that lock and moved no bytes.

Two things are deliberately *not* here, and both are about the same distinction. The evidence logs
(`moves_log`, `damage_log`) stay out of `observation()` for the fingerprint reason above. And
nothing here *derives* a consequence: an Intimidate that the log reports as `|-unboost|` must not
also be inferred from the switch-in, or it lands twice. Derivation belongs to whoever knows that
nobody told them — see `vgc.battle.rules`.
"""

from __future__ import annotations

import json
from typing import Any

from vgc.regulation import Dex, to_id

BOOSTS = ("atk", "def", "spa", "spd", "spe", "accuracy", "evasion")
PERSPECTIVES = ("spectator", "p1", "p2")
# Field effects started by -fieldstart that are not terrains.
PSEUDO_WEATHER = {"trickroom", "gravity", "magicroom", "wonderroom", "fairylock"}


def dumps(obj: Any) -> str:
    """Canonical JSON: sorted keys, no whitespace — equal state ⇒ equal bytes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hp(text: str) -> tuple[int, int, str | None]:
    """'172/185 brn' → (172, 185, 'brn'); '0 fnt' → (0, 0, 'fnt')."""
    parts = text.split()
    status = parts[1] if len(parts) > 1 else None
    if "/" in parts[0]:
        cur, mx = parts[0].split("/")
        return int(cur), int(mx.rstrip("gyr")), status  # "50/100y": Champions adds the HP-bar colour at 20% and 50%
    return int(parts[0]), 0, status


def _details_species(details: str) -> str:
    return details.split(",")[0].strip()


def _tags(args: list[str]) -> dict[str, str]:
    """Trailing `[from] item: Leftovers`, `[of] p1a: X` style tags."""
    out = {}
    for a in args:
        if a.startswith("[") and "]" in a:
            k, v = a[1:a.index("]")], a[a.index("]") + 1 :].strip()
            out[k] = v
    return out


class MoveEvent:
    """One move as it resolved, in the order it resolved.

    The Observer records the sequence and draws no conclusion from it; `vgc.belief` turns pairs of
    these into a bound on Speed. It carries the field and the mover's state because the order
    alone does not mean anything: under Trick Room the slower Pokémon moves first, so the same
    sequence implies the opposite bound, and Tailwind, a Speed boost and paralysis each move the
    comparison as well. The very first turn of the fixture replay has Trick Room up.

    The state comes in two flavours and the distinction is not cosmetic. `boosts`, `status` and
    `side_conditions` are as they stood when the move went off, which is what a damage calc wants.
    The `order_*` copies are as they stood when the turn *began*, which is what decided the order:
    a Weak Armor Pokémon hit earlier in the same turn is at +2 Speed by the time its own move
    line appears, and reading that as the reason it moved second turns a true observation into a
    contradiction. `order_known` is False when the Pokémon was not on the field at the turn mark.
    """

    __slots__ = ("turn", "seq", "side", "slot", "species", "forme", "move", "priority", "target",
                 "spread", "called_by", "trick_room", "weather", "terrain", "boosts", "status",
                 "side_conditions", "item", "ability", "order_boosts", "order_status",
                 "order_side_conditions", "order_weather", "order_terrain", "order_known")

    def __init__(self, **kw: Any):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def to_json(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}


class DamageEvent:
    """Damage a move did, with everything the calc needs to ask what spread could produce it.

    `hp_before`/`hp_after` are fractions, and for the opponent the public line is out of 100, so
    the loss is known to about a percentage point and no better — `exact` says which case this is.
    `fainted` marks a right-censored observation: the move did *at least* the remaining HP, and
    reading it as an equality would systematically underestimate the attacker's investment.

    Both sides' forme, and the attacker's item and ability, are recorded **as of this moment**
    rather than looked up afterwards. A Pokémon that Mega Evolves changes its base stats and its
    ability mid-battle, so reading them off the final state answers a question about a different
    Pokémon: Gengar is base 60 Defence and Mega Gengar is 80, which is enough to make a true
    observation look impossible.
    """

    __slots__ = ("turn", "seq", "attacker_side", "attacker_slot", "attacker", "attacker_forme",
                 "attacker_item", "attacker_ability", "move",
                 "target_side", "target_slot", "target", "target_forme",
                 "hp_before", "hp_after", "hp_max",
                 "exact", "fainted", "spread", "crit", "field", "attacker_boosts",
                 "attacker_status", "target_item", "target_ability", "target_boosts",
                 "target_status", "target_side_conditions")

    def __init__(self, **kw: Any):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def lost(self) -> float:
        return max(0.0, self.hp_before - self.hp_after)

    def to_json(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}


class Mon:
    __slots__ = ("species", "forme", "nickname", "state", "position", "hp", "hp_max", "status", "boosts",
                 "volatiles", "item", "item_source", "lost_item", "ability", "ability_source", "moves",
                 "moves_used", "nature", "stats", "mega", "gender", "ability_ruled_out")

    def __init__(self, species: str):
        self.species = species  # as shown at team preview
        self.forme = species
        self.nickname: str | None = None
        self.state = "unrevealed"
        self.position: int | None = None
        self.hp = 1.0
        self.hp_max: int | None = None  # set only when exact HP is known to this perspective
        self.status: str | None = None
        self.boosts: dict[str, int] = {}
        self.volatiles: set[str] = set()
        self.item: str | None = None  # None: unknown; "": known to hold nothing
        self.item_source: str | None = None  # sheet | revealed | own
        self.lost_item: str | None = None  # an item it had and lost (consumed, knocked off…)
        self.ability: str | None = None
        self.ability_source: str | None = None
        # Abilities this Pokémon has been *shown* not to have, by something that would have fired
        # and did not. Deliberately absent from `to_json`, for the same reason the evidence logs
        # are: `observation()` is fingerprinted at VERSION 3 across 433,052 frozen rows.
        self.ability_ruled_out: set[str] = set()
        self.moves: list[str] = []  # known moveset (sheet / own)
        self.moves_used: list[str] = []  # revealed by use, in order of first use
        self.nature: str | None = None
        self.stats: dict[str, int] | None = None
        self.mega = False
        self.gender: str | None = None

    def to_json(self, exact: bool) -> dict[str, Any]:
        hp_exact = None
        if exact and self.hp_max:
            hp_exact = [round(self.hp * self.hp_max), self.hp_max]
        return {
            "species": self.species, "forme": self.forme, "state": self.state, "position": self.position,
            "hp": round(self.hp, 4), "hp_exact": hp_exact, "status": self.status,
            "boosts": {k: v for k, v in sorted(self.boosts.items()) if v},
            "volatiles": sorted(self.volatiles), "item": self.item, "item_source": self.item_source,
            "lost_item": self.lost_item, "ability": self.ability, "ability_source": self.ability_source,
            "moves": list(self.moves), "moves_used": list(self.moves_used), "nature": self.nature,
            "stats": self.stats, "mega": self.mega,
        }


class Side:
    def __init__(self, sid: str):
        self.id = sid
        self.name: str | None = None
        self.mons: list[Mon] = []
        self.team_size: int | None = None
        self.conditions: dict[str, int] = {}  # id → turn it started
        self.sheet = False  # an open team sheet was shown for this side
        self.brought_known = False  # which 4 were brought is known to this perspective

    def to_json(self, exact: bool) -> dict[str, Any]:
        return {
            "name_known": self.name is not None, "team_size": self.team_size,
            "conditions": dict(sorted(self.conditions.items())), "sheet": self.sheet,
            "brought_known": self.brought_known, "mons": [m.to_json(exact) for m in self.mons],
        }


class BattleState:
    """The battle as it stands, for one perspective, however it was learned.

    Instantiate directly for manual entry; `vgc.data.observe.Observer` subclasses it to drive the
    same state from a Showdown protocol stream.
    """

    def __init__(self, perspective: str, dex: Dex):
        if perspective not in PERSPECTIVES:
            raise ValueError(perspective)
        self.perspective = perspective
        self.dex = dex
        self.sides = {"p1": Side("p1"), "p2": Side("p2")}
        self.turn = 0
        self.weather: str | None = None
        self.weather_since: int | None = None
        self.terrain: str | None = None
        self.terrain_since: int | None = None
        self.pseudo: dict[str, int] = {}
        self.winner: str | None = None
        self.ended = False
        self.started = False
        self.fainted_this_turn: set[str] = set()
        self._base_cache: dict[str, str] = {}
        # Evidence. Kept out of `observation()` on purpose: snapshots embed the observation and
        # are fingerprinted at VERSION 3, so adding fields there would invalidate 433,052 frozen
        # training rows and every manifest built on them. Evidence is read from a live stream.
        self.moves_log: list[MoveEvent] = []
        self.damage_log: list[DamageEvent] = []
        self._seq = 0              # position within the current turn, across both sides
        self._resolving: MoveEvent | None = None
        self._crit: set[str] = set()   # idents the resolving move crit against
        self._turn_start: dict[tuple[str, int], dict[str, Any]] = {}

    # --- the verbs ----------------------------------------------------------------------
    #
    # Everything below changes the battle. The protocol adapter parses a line and calls one of
    # these; manual entry calls the same one directly. They are shared by construction rather
    # than by a test that hopes two implementations agree — which is the only version of this
    # that stays true after somebody edits one of them.

    def at(self, sid: str, slot: int) -> "Mon | None":
        """Whoever is in that slot — how a UI addresses a Pokémon, with no ident string."""
        for m in self.sides[sid].mons:
            if m.state == "active" and m.position == slot:
                return m
        return None

    def bench(self, sid: str) -> list["Mon"]:
        """Who could still be sent out — the switch menu."""
        return [m for m in self.sides[sid].mons if m.state in ("bench", "unrevealed")]

    def begin_turn(self, n: int) -> None:
        self.turn = n
        self.started = True
        self._seq = 0
        self._resolving = None
        self._crit = set()
        self._snapshot_turn_start()

    def switch_in(self, sid: str, slot: int, m: "Mon", forme: str) -> None:
        """`m` takes `slot`, displacing whoever held it. Boosts and volatiles are left behind."""
        side = self.sides[sid]
        if m.state == "active" and m.position not in (None, slot):
            # Seen in two slots at once: the earlier sighting was an Illusion. Give that slot
            # to the side's Illusion user, if it has one that could be there.
            fake_slot = m.position
            z = next((x for x in side.mons if x is not m and x.state in ("unrevealed", "bench")
                      and (self._base(x.species) == "zoroark" or x.ability == "illusion")), None)
            if z is not None:
                z.state, z.position, z.hp, z.status = "active", fake_slot, m.hp, m.status
                z.boosts, z.volatiles = m.boosts, m.volatiles
        for other in side.mons:
            if other is not m and other.position == slot and other.state == "active":
                other.state, other.position = "bench", None
                other.boosts, other.volatiles = {}, set()
        if m.state != "active" or m.position != slot:
            m.boosts, m.volatiles = {}, set()
        m.state, m.position = "active", slot
        m.forme = forme

    def set_hp(self, m: "Mon", cur: int, mx: int, status: str | None) -> None:
        """`cur`/`mx` as the perspective sees them: out of 100 for the opponent and exact for your
        own side — the same split a cartridge gives you, a percentage for theirs and real numbers
        for yours."""
        if status == "fnt" or (cur == 0 and mx == 0):
            m.hp = 0.0
            return
        if mx:
            m.hp = cur / mx
            if mx != 100 or self._own(self._side_of(m)):
                m.hp_max = mx
        m.status = status if status != "fnt" else None

    def record_move(self, m: "Mon", move: str, *, target: str | None = None,
                    spread: bool = False, called_by: str | None = None) -> "MoveEvent":
        """Log a move as it resolves, with the state that decided the turn's order."""
        self._crit = set()
        side = self._side_of(m)
        self._resolving = MoveEvent(
            turn=self.turn, seq=self._seq, side=side, slot=m.position,
            # `species` is the team-preview identity and stays put — it is what the belief is
            # keyed on, because the Stat Points do not change when the forme does. `forme` is who
            # was actually on the field, and it is what the base stats have to come from: Mega
            # Salamence is base 120 Speed against Salamence's 100.
            species=m.species, forme=m.forme, move=move,
            priority=(self.dex.get_move(move) or {}).get("priority", 0),
            target=target, spread=spread, called_by=called_by,
            trick_room=("trickroom" in self.pseudo),
            weather=self.weather, terrain=self.terrain,
            boosts={k: v for k, v in sorted(m.boosts.items()) if v},
            status=m.status, side_conditions=sorted(self.sides[side].conditions),
            # The ability is resolved for the forme, not copied from the sheet.
            item=m.item, ability=self._active_ability(m),
            **self._order_state(side, m),
        )
        self.moves_log.append(self._resolving)
        self._seq += 1
        if called_by:
            return self._resolving   # called by another effect: not its own move
        if move not in m.moves_used:
            m.moves_used.append(move)
        return self._resolving

    def apply_boost(self, m: "Mon", stat: str, stages: int) -> None:
        """A stage back to 0 is *kept* as 0, not removed. `observation()` filters falsy boosts on
        the way out, so the difference is invisible there and very visible in `moves_log`, which
        does the same filtering itself."""
        if stat in BOOSTS:
            m.boosts[stat] = max(-6, min(6, m.boosts.get(stat, 0) + stages))

    def set_weather(self, weather: str | None) -> None:
        if weather is None:
            self.weather = self.weather_since = None
        else:
            self.weather, self.weather_since = weather, self.turn

    def set_terrain(self, terrain: str | None) -> None:
        if terrain is None:
            self.terrain = self.terrain_since = None
        else:
            self.terrain, self.terrain_since = terrain, self.turn

    def set_pseudo(self, effect: str, on: bool) -> None:
        if on:
            self.pseudo[effect] = self.turn
        else:
            self.pseudo.pop(effect, None)

    def set_side_condition(self, sid: str, condition: str, on: bool) -> None:
        if on:
            self.sides[sid].conditions[condition] = self.turn
        else:
            self.sides[sid].conditions.pop(condition, None)

    def faint(self, m: "Mon") -> None:
        m.hp, m.state, m.position, m.status = 0.0, "fainted", None, None
        m.boosts, m.volatiles = {}, set()
        self.fainted_this_turn.add(self._side_of(m))

    def reveal(self, m: "Mon", kind: str, value: str) -> None:
        """Public alias for a first sighting — what a UI calls when an item or ability shows."""
        self._reveal(m, kind, value)

    def consume_item(self, m: "Mon", item: str) -> None:
        """It used it and it is gone. Both halves matter: what it held is now known, and it is
        known not to be holding it any more — a Sitrus Berry already eaten cannot heal again, and
        `lost_item` is what tells the damage channel the difference."""
        m.lost_item = item
        m.item = ""
        if m.item_source is None:
            m.item_source = "revealed"

    # --- identity -----------------------------------------------------------------------

    def _base(self, name: str) -> str:
        if name not in self._base_cache:
            s = self.dex.get_species(name)
            self._base_cache[name] = to_id(s["baseSpecies"]) if s else to_id(name.split("-")[0])
        return self._base_cache[name]

    def _find(self, side: Side, species: str) -> Mon:
        base = self._base(species)
        for m in side.mons:
            if self._base(m.species) == base:
                return m
        m = Mon(species)  # not in preview (shouldn't happen in VGC); track it anyway
        side.mons.append(m)
        return m

    def _mon(self, ident: str, details: str | None = None) -> Mon | None:
        """'p1a: Nick' → Mon. A slot-qualified ident means whoever occupies that slot (under
        Illusion two Pokémon can share a name); otherwise match the nickname, then `details`."""
        if ": " not in ident:
            return None
        pos, nick = ident.split(": ", 1)
        side = self.sides[pos[:2]]
        if details is None and len(pos) == 3:
            slot = "ab".index(pos[2])
            for m in side.mons:
                if m.position == slot and m.state == "active":
                    return m
        for m in side.mons:
            if m.nickname == nick:
                return m
        if details is not None:
            m = self._find(side, _details_species(details))
            m.nickname = nick
            return m
        m = self._find(side, nick)
        m.nickname = nick
        return m

    def _own(self, sid: str) -> bool:
        return self.perspective == sid

    def _reveal(self, m: Mon, kind: str, value: str) -> None:
        """First public sighting of an item/ability. Sheet and own knowledge already cover it."""
        if kind == "item" and m.item is None:
            m.item, m.item_source = value, "revealed"
        elif kind == "ability" and m.ability is None:
            m.ability, m.ability_source = value, "revealed"

    def _order_state(self, side: str, m: Mon) -> dict[str, Any]:
        """What the mover's Speed depended on when the turn's order was decided."""
        start = self._turn_start.get((side, m.position))
        if start is None or start["species"] != m.species:
            return {"order_boosts": {k: v for k, v in sorted(m.boosts.items()) if v},
                    "order_status": m.status,
                    "order_side_conditions": sorted(self.sides[side].conditions),
                    "order_weather": self.weather, "order_terrain": self.terrain,
                    "order_known": False}
        return {"order_boosts": start["boosts"], "order_status": start["status"],
                "order_side_conditions": start["side_conditions"],
                "order_weather": start["weather"], "order_terrain": start["terrain"],
                "order_known": True}

    def _snapshot_turn_start(self) -> None:
        """Weather and terrain are part of it: a Swift Swim Pokémon whose rain arrived partway
        through the turn was not fast when the order was decided, and reading the weather off its
        own move line says it was."""
        self._turn_start = {}
        for sid, side in self.sides.items():
            conditions = sorted(side.conditions)
            for mon in side.mons:
                if mon.state == "active" and mon.position is not None:
                    self._turn_start[(sid, mon.position)] = {
                        "species": mon.species,
                        "boosts": {k: v for k, v in sorted(mon.boosts.items()) if v},
                        "status": mon.status, "side_conditions": conditions,
                        "weather": self.weather, "terrain": self.terrain,
                    }

    def _active_ability(self, m: Mon) -> str | None:
        """The ability that was actually in force, which after a Mega Evolution is not the one on
        the sheet: Mega Swampert has Swift Swim where Swampert had Torrent, and Mega Salamence has
        Aerilate where Salamence had Intimidate. A Mega forme has exactly one ability, and it
        becomes public the moment the Mega happens.

        `Mon.ability` deliberately still reports the sheet's. It is serialized into `observation()`,
        which snapshots embed and fingerprint at VERSION 3, so correcting it there would invalidate
        433,052 frozen training rows — a regeneration that belongs with the Phase 8 prerequisite,
        not with this. Recorded rather than quietly carried: anything reading `ability` off a
        Mega-Evolved Pokémon's observation is reading the pre-Mega ability.
        """
        if not m.mega or m.forme == m.species:
            return m.ability
        entry = self.dex.get_species(m.forme)
        mega_ability = (entry or {}).get("abilities", {}).get("0")
        return to_id(mega_ability) if mega_ability else m.ability

    def _set_hp(self, m: Mon, text: str) -> None:
        cur, mx, status = _hp(text)
        if status == "fnt" or (cur == 0 and mx == 0):
            m.hp = 0.0
            return
        if mx:
            m.hp = cur / mx
            if mx != 100 or self._own(self._side_of(m)):
                m.hp_max = mx
        m.status = status if status != "fnt" else None

    def _side_of(self, m: Mon) -> str:
        return "p1" if m in self.sides["p1"].mons else "p2"

    def _boosts_of(self, sid: str, slot: int | None) -> dict[str, int]:
        for mon in self.sides[sid].mons:
            if mon.state == "active" and mon.position == slot:
                return {k: v for k, v in mon.boosts.items() if v}
        return {}

    def observation(self) -> dict[str, Any]:
        return {
            "perspective": self.perspective,
            "turn": self.turn,
            "field": {
                "weather": self.weather, "weather_since": self.weather_since,
                "terrain": self.terrain, "terrain_since": self.terrain_since,
                "pseudo": dict(sorted(self.pseudo.items())),
            },
            "sides": {sid: s.to_json(exact=self._own(sid)) for sid, s in self.sides.items()},
        }

    def empty_slot_needs_switch(self, sid: str) -> bool:
        """A public-information guess that `sid` must replace a fainted active Pokémon:
        an active slot is empty and it has Pokémon left that aren't on the field."""
        side = self.sides[sid]
        active = sum(m.state == "active" for m in side.mons)
        fainted = sum(m.state == "fainted" for m in side.mons)
        size = side.team_size or 4
        return active < 2 and size - fainted - active > 0
