"""Perspective-limited battle state from a Showdown protocol stream.

`Observer` reads one channel of a battle (the spectator channel, or one player's channel plus
that player's requests) and can produce, at any moment, a JSON-able **observation**: exactly
what that perspective could know then. The same code reads self-play re-simulations and
public human replays (whose log *is* the spectator channel), so both yield one format.

Knowledge rules:
  - spectator: preview species; under Open Team Sheets, both sheets; everything publicly
    revealed. HP is a fraction (the public line is out of 100).
  - player: the same for the opponent; for its own side the request adds exact HP/stats,
    items, abilities and moves, and after team preview which 4 were brought.
A Pokémon's `state` is one of: active, bench, fainted, unrevealed (not yet seen — may or
may not have been brought), not_brought (own side only, or known after the fact).

Observations are deterministic: same stream in → byte-identical JSON out (`dumps`).
"""

from __future__ import annotations

import json
from typing import Any

from vgc.regulation import Dex, to_id

BOOSTS = ("atk", "def", "spa", "spd", "spe", "accuracy", "evasion")
PERSPECTIVES = ("spectator", "p1", "p2")
# Field effects that are started by -fieldstart but are not terrains.
PSEUDO_WEATHER = {"trickroom", "gravity", "magicroom", "wonderroom", "fairylock"}
_IGNORE = {"", "t:", "j", "J", "l", "L", "c", "raw", "html", "uhtml", "uhtmlchange", "inactive", "inactiveoff",
           "chat", "n", "debug", "bigerror", "gen", "tier", "rule", "rated", "gametype", "seed", "badge",
           "message", "-message", "-hint", "-center", "-combine", "-notarget", "-nothing", "-anim", "-fail",
           "-block", "-miss", "-immune", "-crit", "-supereffective", "-resisted", "-hitcount", "-waiting",
           "-ohko", "-primal", "-burst", "-zpower", "-zbroken", "-prepare", "-mustrecharge", "-singleturn",
           "-singlemove", "-activate", "-fieldactivate", "cant", "upkeep", "done", "start", "clearpoke",
           "teampreview", "split", "timer", "request", "error", "sentchoice", "uhtml", "-candynamax", "askreg",
           "bestof", "-terastallize", "title", "rename"}


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


class Mon:
    __slots__ = ("species", "forme", "nickname", "state", "position", "hp", "hp_max", "status", "boosts",
                 "volatiles", "item", "item_source", "lost_item", "ability", "ability_source", "moves",
                 "moves_used", "nature", "stats", "mega", "gender")

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


class Observer:
    """Feed protocol lines (`feed`) and, for a player perspective, requests (`request`)."""

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

    # --- requests (player perspective only) ----------------------------------------------

    def request(self, req: dict[str, Any]) -> None:
        sid = req.get("side", {}).get("id")
        if sid != self.perspective:
            return
        side = self.sides[sid]
        pokemon = req["side"]["pokemon"]
        seen = []
        for p in pokemon:
            m = self._find(side, _details_species(p["details"]))
            seen.append(m)
            m.nickname = p["ident"].split(": ", 1)[1]
            m.forme = _details_species(p["details"])
            cur, mx, status = _hp(p["condition"])
            m.hp_max = mx or m.hp_max
            m.hp = cur / mx if mx else 0.0
            if status == "fnt" or cur == 0:
                m.state, m.position, m.status = "fainted", None, None
            else:
                m.status = status
            m.item, m.item_source = (to_id(p["item"]) if p.get("item") else ""), "own"
            m.ability, m.ability_source = to_id(p.get("ability") or p.get("baseAbility") or ""), "own"
            m.moves = [to_id(x) for x in p["moves"]]
            m.stats = {k: int(v) for k, v in p["stats"].items()}
        if not req.get("teamPreview"):
            # The request is authoritative for our own active slots (Illusion fools the log,
            # not its owner): actives come first, in slot order.
            for idx, (p, m) in enumerate(zip(pokemon, seen)):
                if p.get("active") and m.state != "fainted" and idx < 2:
                    m.state, m.position = "active", idx
                elif m.state == "active":
                    m.state, m.position = "bench", None
        if not req.get("teamPreview") and len(pokemon) < len(side.mons):
            # After team preview the request lists only the brought Pokémon.
            side.brought_known = True
            for m in side.mons:
                if m not in seen:
                    m.state = "not_brought"
                elif m.state in ("unrevealed", "not_brought"):
                    m.state = "bench"

    # --- protocol ---------------------------------------------------------------------------

    def feed_many(self, lines: list[str]) -> None:
        for line in lines:
            self.feed(line)

    def feed(self, line: str) -> None:
        if not line.startswith("|"):
            return
        parts = line.split("|")
        kind = parts[1] if len(parts) > 1 else ""
        args = parts[2:]
        if kind in _IGNORE:
            if kind == "upkeep":
                self.fainted_this_turn = set()
            return
        fn = getattr(self, "_on_" + kind.lstrip("-").replace("-", "_"), None)
        if fn is not None:
            fn(args)
        # Generic reveals carried by tags on any line.
        tags = _tags(args)
        src = tags.get("from", "")
        if src.startswith(("item:", "ability:")) and args:
            whom = tags.get("of") or args[0]
            m = self._mon(whom) if ": " in whom else None
            if m is not None:
                kind_, name = src.split(":", 1)
                self._reveal(m, kind_, to_id(name))

    def _reveal(self, m: Mon, kind: str, value: str) -> None:
        """First public sighting of an item/ability. Sheet and own knowledge already cover it."""
        if kind == "item" and m.item is None:
            m.item, m.item_source = value, "revealed"
        elif kind == "ability" and m.ability is None:
            m.ability, m.ability_source = value, "revealed"

    def _on_player(self, a: list[str]) -> None:
        if len(a) >= 2 and a[1]:
            self.sides[a[0]].name = a[1]

    def _on_poke(self, a: list[str]) -> None:
        side = self.sides[a[0]]
        m = Mon(_details_species(a[1]))
        g = [x.strip() for x in a[1].split(",")[1:]]
        m.gender = next((x for x in g if x in ("M", "F")), None)
        side.mons.append(m)

    def _on_teamsize(self, a: list[str]) -> None:
        self.sides[a[0]].team_size = int(a[1])

    def _on_showteam(self, a: list[str]) -> None:
        side = self.sides[a[0]]
        side.sheet = True
        for packed in "|".join(a[1:]).split("]"):
            f = packed.split("|")
            if len(f) < 6:
                continue
            m = self._find(side, f[1] or f[0])
            if m.item_source != "own":
                m.item, m.item_source = to_id(f[2]), "sheet"
            if m.ability_source != "own":
                m.ability, m.ability_source = to_id(f[3]), "sheet"
            if not m.moves:
                m.moves = [to_id(x) for x in f[4].split(",") if x]
            m.nature = f[5] or None

    def _switch_in(self, a: list[str]) -> None:
        pos = a[0].split(":")[0]
        side, slot = self.sides[pos[:2]], "ab".index(pos[2])
        m = self._mon(a[0], a[1])
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
        m.forme = _details_species(a[1])
        self._set_hp(m, a[2] if len(a) > 2 else "100/100")

    _on_switch = _switch_in
    _on_drag = _switch_in

    def _on_replace(self, a: list[str]) -> None:  # Illusion broken: the slot's real occupant
        pos = a[0].split(":")[0]
        side, slot = self.sides[pos[:2]], "ab".index(pos[2])
        fake = next((m for m in side.mons if m.position == slot and m.state == "active"), None)
        real = self._find(side, _details_species(a[1]))
        if fake is not None and fake is not real:
            fake.state, fake.position = ("bench" if fake.state == "active" else fake.state), None
            real.hp, real.status = fake.hp, fake.status
        real.nickname = a[0].split(": ", 1)[1]
        real.state, real.position, real.forme = "active", slot, _details_species(a[1])

    def _on_detailschange(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is not None:
            m.forme = _details_species(a[1])

    def _on_formechange(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is not None:
            m.forme = a[1]

    def _on_mega(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is not None:
            m.mega = True
            if len(a) > 2 and a[2]:
                self._reveal(m, "item", to_id(a[2]))

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

    def _on_damage(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is not None:
            self._set_hp(m, a[1])

    _on_heal = _on_damage
    _on_sethp = _on_damage

    def _on_faint(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is not None:
            m.hp, m.state, m.position, m.status = 0.0, "fainted", None, None
            m.boosts, m.volatiles = {}, set()
            self.fainted_this_turn.add(a[0][:2])

    def _on_status(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is not None:
            m.status = a[1]

    def _on_curestatus(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is not None:
            m.status = None

    def _on_cureteam(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is not None:
            for x in self.sides[self._side_of(m)].mons:
                x.status = None

    def _boost(self, a: list[str], sign: int) -> None:
        m = self._mon(a[0])
        if m is not None and a[1] in BOOSTS:
            m.boosts[a[1]] = max(-6, min(6, m.boosts.get(a[1], 0) + sign * int(a[2])))

    def _on_boost(self, a: list[str]) -> None:
        self._boost(a, 1)

    def _on_unboost(self, a: list[str]) -> None:
        self._boost(a, -1)

    def _on_setboost(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is not None and a[1] in BOOSTS:
            m.boosts[a[1]] = int(a[2])

    def _on_clearboost(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is not None:
            m.boosts = {}

    def _on_clearallboost(self, a: list[str]) -> None:
        for side in self.sides.values():
            for m in side.mons:
                m.boosts = {}

    def _on_clearnegativeboost(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is not None:
            m.boosts = {k: v for k, v in m.boosts.items() if v > 0}

    def _on_clearpositiveboost(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is not None:
            m.boosts = {k: v for k, v in m.boosts.items() if v < 0}

    def _on_invertboost(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is not None:
            m.boosts = {k: -v for k, v in m.boosts.items()}

    def _on_copyboost(self, a: list[str]) -> None:
        src, dst = self._mon(a[0]), self._mon(a[1])
        if src is not None and dst is not None:
            dst.boosts = dict(src.boosts)

    def _on_swapboost(self, a: list[str]) -> None:
        x, y = self._mon(a[0]), self._mon(a[1])
        if x is None or y is None:
            return
        stats = a[2].split(", ") if len(a) > 2 and a[2] and not a[2].startswith("[") else list(BOOSTS)
        for s in stats:
            x.boosts[s], y.boosts[s] = y.boosts.get(s, 0), x.boosts.get(s, 0)

    def _on_item(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is None:
            return
        tags = _tags(a[2:])
        if tags.get("from", "").startswith("move:"):  # Trick / Switcheroo / Bestow: now holds it
            m.item, m.item_source = to_id(a[1]), "own" if m.item_source == "own" else "revealed"
        else:
            self._reveal(m, "item", to_id(a[1]))

    def _on_enditem(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is not None:
            m.lost_item = to_id(a[1])
            m.item = ""
            if m.item_source is None:
                m.item_source = "revealed"

    def _on_ability(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is not None and len(a) > 1:
            if "from" not in _tags(a[2:]):  # not Trace/Skill Swap/…: its own ability
                self._reveal(m, "ability", to_id(a[1]))

    def _on_move(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is None:
            return
        tags = _tags(a[3:])
        src = tags.get("from", "")
        if src and src not in ("lockedmove",) and not src.startswith("move: Sleep Talk"):
            return  # called by another effect (Magic Bounce, Copycat…): not its own move
        mv = to_id(a[1])
        if mv not in m.moves_used:
            m.moves_used.append(mv)

    def _volatile(self, a: list[str], on: bool) -> None:
        m = self._mon(a[0])
        if m is None or len(a) < 2:
            return
        eff = to_id(a[1].replace("move: ", "").replace("ability: ", ""))
        if on:
            m.volatiles.add(eff)
        else:
            m.volatiles.discard(eff)

    def _on_start(self, a: list[str]) -> None:
        self._volatile(a, True)

    def _on_end(self, a: list[str]) -> None:
        self._volatile(a, False)

    def _on_weather(self, a: list[str]) -> None:
        w = a[0]
        if w == "none":
            self.weather = self.weather_since = None
        elif "[upkeep]" not in a:
            self.weather, self.weather_since = to_id(w), self.turn

    def _on_fieldstart(self, a: list[str]) -> None:
        eff = to_id(a[0].replace("move: ", ""))
        if eff in PSEUDO_WEATHER:
            self.pseudo[eff] = self.turn
        else:
            self.terrain, self.terrain_since = eff, self.turn

    def _on_fieldend(self, a: list[str]) -> None:
        eff = to_id(a[0].replace("move: ", ""))
        if eff in PSEUDO_WEATHER:
            self.pseudo.pop(eff, None)
        elif eff == self.terrain:
            self.terrain = self.terrain_since = None

    def _on_sidestart(self, a: list[str]) -> None:
        side = self.sides[a[0][:2]]
        side.conditions[to_id(a[1].replace("move: ", ""))] = self.turn

    def _on_sideend(self, a: list[str]) -> None:
        self.sides[a[0][:2]].conditions.pop(to_id(a[1].replace("move: ", "")), None)

    def _on_turn(self, a: list[str]) -> None:
        self.turn = int(a[0])
        self.started = True

    def _on_win(self, a: list[str]) -> None:
        self.ended = True
        self.winner = next((sid for sid, s in self.sides.items() if s.name == a[0]), None)

    def _on_tie(self, a: list[str]) -> None:
        self.ended = True

    # --- output ---------------------------------------------------------------------------

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
