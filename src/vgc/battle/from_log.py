"""A Showdown log as the taps a careful person would have made (`vgc.battle.entry`).

The page is driven by taps and the policy was built and checked on logs (`vgc.policy.view`), so
anything the page shows from the policy rests on the two adapters onto `BattleState` agreeing.
This turns a log into a setup and a journal, for the parity test that checks they do
(`tests/test_policy_entry.py`).

It is written the way someone at the cartridge works, not as a line-by-line translation. The
structural taps come from the line that shows them: who led and who came in, each move with its
target, each turn mark, each Mega Evolution, each Ally Switch, and the answer to a switch-in question when the log
shows that ability firing (its timing is Speed evidence, so the page asks it as a confirm).
Everything else, the HP, statuses, stat stages, items, weather, terrain, rooms and side conditions,
is tapped as whatever differs between an `Observer` fed the same lines and the battle the taps
replay to, so an effect the entry rules already derive is not entered twice. HP lost to a move is
a `damage` tap. HP lost to anything else (recoil, Life Orb, weather) is set without being put
down to the move, as manual entry does (`BattleState.record_damage`); the entry kind that sets HP
alone is `heal`.

A fainted Pokémon brought back (Revival Blessing) has no tap, and the taps that follow it fail.
Illusion is not followed: the page cannot see through it either until it breaks, and
`vgc.data.parity` records the same divergence for the log adapter against poke-env. What the parity test then checks
is everything else, which is what a tap-driven battle could get wrong: the journal a solver
position is read from (who came in when, who used Protect last turn, who has not moved yet), the
sheets, and the adapter.
"""

from __future__ import annotations

import re
from typing import Any

from vgc.regulation import Regulation, to_id

_SLOT = re.compile(r"^(p[12])([ab])")


def _sheet(reg: Regulation, m: Any) -> dict[str, Any]:
    item = m.item or m.lost_item
    return {"species": m.species,
            "item": (reg.dex.get_item(item) or {}).get("name", item) if item else (m.item if m.item == "" else None),
            "ability": m.ability, "nature": m.nature,
            "moves": [(reg.dex.get_move(x) or {}).get("name", x) for x in m.moves]}


def setup(reg: Regulation, lines: list[str], perspective: str = "spectator",
          mine_text: str | None = None) -> dict[str, Any]:
    """The battle at team preview, from the sheets the log shows (`|showteam|`). A player's setup
    takes their own six from `mine_text`, as the page takes them from the team library."""
    from vgc.battle.entry import from_team
    from vgc.data.observe import Observer

    o = Observer("spectator", reg.dex)
    for line in lines:
        if line.startswith("|turn|"):
            break
        o.feed(line)
    sheets = {sid: [_sheet(reg, m) for m in o.sides[sid].mons] for sid in ("p1", "p2")}
    if perspective == "spectator":
        return {"perspective": "spectator", **sheets}
    them = "p2" if perspective == "p1" else "p1"
    return {"perspective": perspective, "mine": from_team(reg, mine_text) if mine_text else sheets[perspective],
            "theirs": sheets[them]}


def _hp_pct(m: Any) -> int:
    return int(round(100 * m.hp))


def journal(reg: Regulation, lines: list[str], setup_: dict[str, Any], upto_turn: int | None = None
            ) -> list[dict[str, Any]]:
    """The taps for `lines`, stopping after the `|turn|` mark numbered `upto_turn` if given."""
    from vgc.battle.entry import Battle
    from vgc.data.observe import Observer

    o = Observer(setup_["perspective"], reg.dex)
    battle = Battle(reg, setup_, [])
    started = False

    def tap(e: dict[str, Any]) -> None:
        battle.append(e)

    def mine(ident: str):
        """The entry state's Pokémon for a log ident, by side and species."""
        m = o._mon(ident)
        if m is None:
            return None, None
        side = ident[:2]
        want = battle.rp.state._base(m.species)
        e = next((x for x in battle.rp.state.sides[side].mons if battle.rp.state._base(x.species) == want), None)
        return m, e

    def answer(ident: str, ability: str) -> bool:
        """Answer the open switch-in question for `ident` with `ability`, if there is one."""
        m, e = mine(ident)
        if e is None:
            return False
        want = to_id(ability)
        for q in battle.rp.questions:
            if q.kind == "switch_in" and q.side == ident[:2] and q.species == e.species:
                hit = next((i for i, x in enumerate(q.outcomes) if to_id(x.ability or "") == want), None)
                if hit is not None:
                    tap({"kind": "answer", "question": q.id, "option": hit})
                    return True
        return False

    def sync(attack: bool) -> None:
        st = battle.rp.state
        if (o.weather or None) != (st.weather or None):
            tap({"kind": "field", "what": "weather", "value": o.weather, "on": bool(o.weather)})
        if (o.terrain or None) != (st.terrain or None):
            tap({"kind": "field", "what": "terrain", "value": o.terrain, "on": bool(o.terrain)})
        for p in set(o.pseudo) ^ set(st.pseudo):
            tap({"kind": "field", "what": "pseudo", "value": p, "on": p in o.pseudo})
        for sid in ("p1", "p2"):
            for c in set(o.sides[sid].conditions) ^ set(st.sides[sid].conditions):
                tap({"kind": "side", "side": sid, "condition": c, "on": c in o.sides[sid].conditions})
            for om in o.sides[sid].mons:
                em = next((x for x in st.sides[sid].mons if st._base(x.species) == st._base(om.species)), None)
                if em is None:
                    continue
                if om.lost_item and not em.lost_item:
                    tap({"kind": "consume", "side": sid, "species": em.species, "item": om.lost_item})
                if em.position is None or em.state != "active":
                    continue
                slot = em.position
                if om.state == "fainted":
                    tap({"kind": "faint", "side": sid, "slot": slot})
                    continue
                if _hp_pct(om) != _hp_pct(em):
                    hit = attack and om.hp < em.hp
                    tap({"kind": "damage" if hit else "heal", "side": sid, "slot": slot, "pct": _hp_pct(om)})
                if (om.status or None) != (em.status or None):
                    tap({"kind": "status", "side": sid, "slot": slot, "status": om.status})
                for stat in set(om.boosts) | set(em.boosts):
                    d = om.boosts.get(stat, 0) - em.boosts.get(stat, 0)
                    if d:
                        tap({"kind": "boost", "side": sid, "slot": slot, "stat": stat, "stages": d})

    for line in lines:
        parts = line.split("|")
        kind = parts[1] if len(parts) > 1 else ""
        o.feed(line)
        if kind in ("switch", "drag") and len(parts) > 2:
            m, e = mine(parts[2])
            if m is not None and e is not None:
                tap({"kind": "switch" if started else "lead", "side": parts[2][:2],
                     "slot": "ab".index(parts[2][2]), "species": e.species})
        elif kind == "move" and len(parts) > 3 and _SLOT.match(parts[2]):
            target = _SLOT.match(parts[4]) if len(parts) > 4 else None
            called = next((x[len("[from]move: "):] for x in parts[5:] if x.startswith("[from]move: ")), None)
            tap({"kind": "move", "side": parts[2][:2], "slot": "ab".index(parts[2][2]), "move": parts[3],
                 "target": {"side": target.group(1), "slot": "ab".index(target.group(2))} if target else None,
                 "spread": any(x.startswith("[spread]") for x in parts[5:]), "called_by": called})
        elif kind == "swap" and len(parts) > 3 and _SLOT.match(parts[2]):
            old = "ab".index(parts[2][2])
            if parts[3].isdigit() and int(parts[3]) != old:
                tap({"kind": "swap", "side": parts[2][:2], "slot": old})
        elif kind == "-mega" and len(parts) > 4:
            m, e = mine(parts[2])
            if e is not None:
                stone = reg.dex.get_item(to_id(parts[4])) or {}
                forme = next(iter((stone.get("megaStone") or {}).values()), None)
                tap({"kind": "mega", "side": parts[2][:2], "species": e.species, "forme": forme, "item": parts[4]})
        elif kind == "turn":
            sync(False)
            started = True
            tap({"kind": "turn", "n": int(parts[2])})
            if upto_turn is not None and int(parts[2]) >= upto_turn:
                break
            continue
        # A switch-in ability announcing itself, on its own line or as the cause of a weather or
        # terrain. Its effects arrive on the lines after, so they are not synced on this one.
        source = next((x[len("[from] ability: "):] for x in parts[3:] if x.startswith("[from] ability: ")), None)
        of = next((x[len("[of] "):] for x in parts[3:] if x.startswith("[of] ")), None)
        if kind == "-ability" and len(parts) > 3 and _SLOT.match(parts[2]) and answer(parts[2], parts[3]):
            continue
        if source and _SLOT.match(of or "") and answer(of, source):
            continue
        sync(kind == "-damage" and not any(x.startswith("[from]") for x in parts[3:]))
    return battle.journal
