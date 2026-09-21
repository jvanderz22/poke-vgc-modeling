"""Showdown protocol → `vgc.battle.state.BattleState`.

One adapter onto the battle state: it parses a protocol stream and applies what the stream says
happened. `Observer` is the perspective-limited reader of self-play re-simulations and public human
replays alike (a replay's log *is* the spectator channel), so both yield one format.

Knowledge rules:
  - spectator: preview species; under Open Team Sheets, both sheets; everything publicly
    revealed. HP is a fraction (the public line is out of 100).
  - player: the same for the opponent; for its own side the request adds exact HP/stats,
    items, abilities and moves, and after team preview which 4 were brought.
A Pokémon's `state` is one of: active, bench, fainted, unrevealed (not yet seen — may or
may not have been brought), not_brought (own side only, or known after the fact).

Observations are deterministic: same stream in → byte-identical JSON out (`dumps`), and
`tests/test_observe_golden.py` holds it to that.

**This adapter never derives.** The stream already says an Intimidate happened, as `|-unboost|`,
so inferring it a second time from the switch-in would apply it twice. Derivation is for the
adapter whose input does *not* tell it — manual entry.
"""

from __future__ import annotations

from typing import Any

from vgc.battle.state import (BOOSTS, PERSPECTIVES, PSEUDO_WEATHER, BattleState, DamageEvent,
                              Mon, MoveEvent, Side, _details_species, _hp, _tags, dumps)
from vgc.regulation import Dex, to_id

__all__ = ["Observer", "BattleState", "Mon", "Side", "MoveEvent", "DamageEvent", "dumps",
           "PERSPECTIVES", "PSEUDO_WEATHER", "BOOSTS"]

_IGNORE = {"", "t:", "j", "J", "l", "L", "c", "raw", "html", "uhtml", "uhtmlchange", "inactive", "inactiveoff",
           "chat", "n", "debug", "bigerror", "gen", "tier", "rule", "rated", "gametype", "seed", "badge",
           "message", "-message", "-hint", "-center", "-combine", "-notarget", "-nothing", "-anim", "-fail",
           "-block", "-miss", "-immune", "-supereffective", "-resisted", "-hitcount", "-waiting",
           "-ohko", "-primal", "-burst", "-zpower", "-zbroken", "-prepare", "-mustrecharge", "-singleturn",
           "-singlemove", "-activate", "-fieldactivate", "cant", "upkeep", "done", "start", "clearpoke",
           "teampreview", "split", "timer", "request", "error", "sentchoice", "uhtml", "-candynamax", "askreg",
           "bestof", "-terastallize", "title", "rename"}


class Observer(BattleState):
    """Feed protocol lines (`feed`) and, for a player perspective, requests (`request`)."""

    """Feed protocol lines (`feed`) and, for a player perspective, requests (`request`)."""

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

    def _on_swap(self, a: list[str]) -> None:
        """`|swap|POKEMON|newPosition` — Ally Switch and friends: two actives trade slots. Nothing
        else in the protocol restates their positions, so missing this leaves the side's slots
        crossed for the rest of the battle (and every target/speed read after it wrong)."""
        side = self.sides[a[0].split(":")[0][:2]]
        m = self._mon(a[0])
        try:
            new = int(a[1])
        except (IndexError, ValueError):
            return
        old = m.position
        if old is None or old == new:
            return
        other = next((x for x in side.mons if x is not m and x.position == new and x.state == "active"), None)
        m.position = new
        if other is not None:
            other.position = old

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

    def _on_damage(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is None:
            return
        before = m.hp
        self._set_hp(m, a[1])
        self._record_damage(m, a, before)

    def _on_heal(self, a: list[str]) -> None:
        m = self._mon(a[0])
        if m is not None:
            self._set_hp(m, a[1])

    _on_sethp = _on_heal

    def _record_damage(self, m: Mon, a: list[str], before: float) -> None:
        """Attribute this HP loss to the move now resolving, or to nothing.

        A `[from]` tag means something else did it — Life Orb, recoil, poison, Rocky Helmet — and
        those are excluded rather than mis-attributed: reading recoil as the move's own output
        would tell the belief the attacker hits far harder than it does. Self-damage is dropped
        for the same reason. What is left is `|move|` → `|-damage|`, which is the calc's own
        question asked backwards.
        """
        ev = self._resolving
        if ev is None or ev.called_by or _tags(a[1:]).get("from"):
            return
        side, slot = self._side_of(m), m.position
        if (side, slot) == (ev.side, ev.slot):
            return
        target_side = self.sides[side]
        self.damage_log.append(DamageEvent(
            turn=self.turn, seq=ev.seq,
            attacker_side=ev.side, attacker_slot=ev.slot, attacker=ev.species,
            attacker_forme=ev.forme, attacker_item=ev.item, attacker_ability=ev.ability,
            move=ev.move,
            target_side=side, target_slot=slot, target=m.species, target_forme=m.forme,
            hp_before=round(before, 4), hp_after=round(m.hp, 4), hp_max=m.hp_max,
            exact=self._own(side) and m.hp_max is not None,
            fainted=(m.hp == 0.0), spread=ev.spread, crit=(a[0] in self._crit),
            field={"weather": self.weather, "terrain": self.terrain,
                   "pseudo": sorted(self.pseudo)},
            attacker_boosts=dict(sorted(self._boosts_of(ev.side, ev.slot).items())),
            attacker_status=ev.status,
            # As of this moment, for the same reason the formes are: a Pokémon that has its item
            # knocked off, or Mega Evolves, is a different defender afterwards, and reading either
            # off the final state answers a question about a Pokémon that no longer existed.
            target_item=m.item, target_ability=self._active_ability(m),
            target_boosts={k: v for k, v in sorted(m.boosts.items()) if v},
            target_status=m.status,
            target_side_conditions=sorted(target_side.conditions),
        ))

    def _on_crit(self, a: list[str]) -> None:
        if a:
            self._crit.add(a[0])

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
        called = bool(src) and src not in ("lockedmove",) and not src.startswith("move: Sleep Talk")
        mv = to_id(a[1])
        self._crit = set()
        side = self._side_of(m)
        self._resolving = MoveEvent(
            turn=self.turn, seq=self._seq, side=side, slot=m.position,
            # `species` is the team-preview identity and stays put — it is what the belief is
            # keyed on, because the Stat Points do not change when the forme does. `forme` is who
            # was actually on the field, and it is what the base stats have to come from: Mega
            # Salamence is base 120 Speed against Salamence's 100.
            species=m.species, forme=m.forme, move=mv,
            priority=(self.dex.get_move(mv) or {}).get("priority", 0),
            target=(a[2] if len(a) > 2 and ": " in a[2] else None),
            spread=("spread" in tags), called_by=(src or None) if called else None,
            trick_room=("trickroom" in self.pseudo),
            weather=self.weather, terrain=self.terrain,
            boosts={k: v for k, v in sorted(m.boosts.items()) if v},
            status=m.status, side_conditions=sorted(self.sides[side].conditions),
            # The ability is resolved for the forme, not copied from the sheet — see below.
            item=m.item, ability=self._active_ability(m),
            **self._order_state(side, m),
        )
        self.moves_log.append(self._resolving)
        self._seq += 1
        if called:
            return  # called by another effect (Magic Bounce, Copycat…): not its own move
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
        self._seq = 0
        self._resolving = None
        self._crit = set()
        self._snapshot_turn_start()

    def _on_win(self, a: list[str]) -> None:
        self.ended = True
        self.winner = next((sid for sid, s in self.sides.items() if s.name == a[0]), None)

    def _on_tie(self, a: list[str]) -> None:
        self.ended = True
