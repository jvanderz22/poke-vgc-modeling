"""L2 tier 1 — damage-calc-aware heuristic doubles player.

Deterministic and training-free: the workhorse for bulk simulation and the floor every later
policy is measured against. Each turn it
  1. batches real damage calcs (our actives → foes and ally; foes' known moves → our actives),
  2. scores every legal single-slot order: expected damage and KO chance, ally damage,
     outspeeding a KO, Protect against incoming threat, Fake Out, speed control, redirection,
     status and a handful of common support moves,
  3. picks the best *joint* order, correcting for overkill when both slots hit one foe.
Mega Evolution is taken at the first opportunity. Team preview brings the four with the best
calc'd matchup against the opposing six.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import mean
from typing import Any

from poke_env.battle.double_battle import DoubleBattle
from poke_env.battle.move import Move
from poke_env.battle.pokemon import Pokemon
from poke_env.battle.side_condition import SideCondition
from poke_env.player.battle_order import (
    BattleOrder,
    DefaultBattleOrder,
    DoubleBattleOrder,
    PassBattleOrder,
    SingleBattleOrder,
)

from vgc.engine.calc import DamageCalc
from vgc.engine.runner import legal_orders
from vgc.policy.state import StateView
from vgc.regulation import Regulation

PROTECT = {"protect", "detect", "spikyshield", "kingsshield", "banefulbunker", "silktrap", "burningbulwark", "obstruct"}
REDIRECT = {"followme", "ragepowder"}
SLEEP = {"spore", "sleeppowder", "hypnosis", "yawn", "darkvoid", "lovelykiss", "sing"}
SETUP = {"swordsdance", "nastyplot", "calmmind", "dragondance", "quiverdance", "bulkup", "coil", "shellsmash", "tidyup"}
HEAL = {"recover", "roost", "strengthsap", "slackoff", "synthesis", "moonlight", "morningsun", "softboiled", "junglehealing", "lifedew"}
SCREEN_MOVES = {"reflect": SideCondition.REFLECT, "lightscreen": SideCondition.LIGHT_SCREEN, "auroraveil": SideCondition.AURORA_VEIL}
SPREAD_TARGETS = {"allAdjacentFoes", "allAdjacent"}
FOE_POS = (1, 2)
ALLY_POS = (-1, -2)


@dataclass
class Scored:
    order: SingleBattleOrder
    value: float
    # expected HP fraction removed per foe slot, for the joint overkill correction
    damage: dict[int, float]


class HeuristicPolicy:
    name = "heuristic"

    def __init__(self, reg: Regulation, calc: DamageCalc | None = None):
        self.reg = reg
        self.calc = calc or DamageCalc()
        _DEX_MOVES.clear()
        _DEX_MOVES.update(reg.dex.moves)

    # ---------------------------------------------------------------- team preview
    def teampreview(self, battle: DoubleBattle, rng: random.Random) -> str:
        view = StateView(battle, self.reg)
        mine = list(battle.team.values())
        theirs = list(battle.teampreview_opponent_team)
        if not theirs:
            return "team " + "".join(str(i) for i in range(1, min(len(mine), self.reg.bring) + 1))
        reqs, keys = [], []
        for i, me in enumerate(mine):
            for j, foe in enumerate(theirs):
                for mv in _damaging(me):
                    reqs.append(self._req(view, me, foe, mv, mega=self._mega_name(view, me)))
                    keys.append(("off", i, j))
                for mv in _damaging(foe):
                    reqs.append(self._req(view, foe, me, mv, mega=self._mega_name(view, foe)))
                    keys.append(("def", i, j))
        best: dict[tuple, float] = {}
        for key, res in zip(keys, self.calc.batch(reqs)):
            if res["ok"]:
                frac = min(1.0, mean(res["damage"]) / res["defenderHP"])
                best[key] = max(best.get(key, 0.0), frac)
        score = []
        for i, me in enumerate(mine):
            off = mean(best.get(("off", i, j), 0.0) for j in range(len(theirs)))
            dfn = mean(best.get(("def", i, j), 0.35) for j in range(len(theirs)))
            fake_out = 0.08 if "fakeout" in me.moves else 0.0
            score.append((off - 0.7 * dfn + fake_out, -i, i))
        chosen = [i for *_, i in sorted(score, reverse=True)[: self.reg.bring]]
        return "team " + "".join(str(i + 1) for i in chosen)

    # ---------------------------------------------------------------- turns
    def choose_move(self, battle: DoubleBattle, rng: random.Random) -> BattleOrder:
        valid = legal_orders(battle)
        if not any(valid[0] + valid[1]):
            return DoubleBattleOrder(DefaultBattleOrder(), DefaultBattleOrder())
        view = StateView(battle, self.reg)
        calcs = self._run_calcs(view)
        threat = self._threats(view, calcs)
        scored = [[self._score(view, calcs, threat, slot, o) for o in valid[slot]] for slot in (0, 1)]
        for slot in (0, 1):
            scored[slot].sort(key=lambda s: s.value, reverse=True)
        best, best_value = None, float("-inf")
        for a in scored[0][:8] or [None]:
            for b in scored[1][:8] or [None]:
                joint = DoubleBattleOrder.join_orders([a.order] if a else [], [b.order] if b else [])
                if not joint:
                    continue
                value = (a.value if a else 0) + (b.value if b else 0) - self._overkill(view, a, b)
                if value > best_value:
                    best, best_value = joint[0], value
        if best is None:
            joint = DoubleBattleOrder.join_orders(*valid)
            return joint[rng.randrange(len(joint))] if joint else DoubleBattleOrder(DefaultBattleOrder(), DefaultBattleOrder())
        return best

    # ---------------------------------------------------------------- calcs
    def _mega_name(self, view: StateView, mon: Pokemon) -> str | None:
        mega = view.dex.mega_forme(mon.species, mon.item or "") if mon.item else None
        return mega["name"] if mega else None

    def _req(self, view: StateView, atk: Pokemon, dfn: Pokemon, move: Move, mega: str | None = None) -> dict[str, Any]:
        name = (view.dex.moves.get(move.id) or {}).get("name", move.id)
        return {
            "attacker": view.payload(atk, species_name=mega),
            "defender": view.payload(dfn),
            "move": {"name": name},
            "field": view.field(attacker_ours=view.is_ours(atk)),
        }

    def _run_calcs(self, view: StateView) -> dict[tuple, dict]:
        """Keys: ("us", slot, move_id, target_pos, mega) and ("them", foe_slot, move_id, my_slot)."""
        b = view.battle
        reqs, keys = [], []
        for slot, me in enumerate(b.active_pokemon):
            if me is None or me.fainted:
                continue
            megas = [False] + ([True] if b.can_mega_evolve[slot] and self._mega_name(view, me) else [])
            for mv in b.available_moves[slot]:
                if not _is_damaging(mv):
                    continue
                for mega in megas:
                    for pos, target in _targets(b, slot):
                        reqs.append(self._req(view, me, target, mv, self._mega_name(view, me) if mega else None))
                        keys.append(("us", slot, mv.id, pos, mega))
        for fslot, foe in enumerate(b.opponent_active_pokemon):
            if foe is None or foe.fainted:
                continue
            for mv in _damaging(foe):
                for mslot, me in enumerate(b.active_pokemon):
                    if me is not None and not me.fainted:
                        reqs.append(self._req(view, foe, me, mv))
                        keys.append(("them", fslot, mv.id, mslot))
        out = {}
        for k, res in zip(keys, self.calc.batch(reqs)):
            if res["ok"]:
                out[k] = res
        return out

    def _threats(self, view: StateView, calcs: dict[tuple, dict]) -> list[float]:
        """Worst-case expected fraction of each of our actives' current HP one foe can remove."""
        threat = [0.0, 0.0]
        for (kind, fslot, move_id, mslot), res in ((k, v) for k, v in calcs.items() if k[0] == "them"):
            cur = max(1, res["defenderCurHP"])
            frac = min(1.0, mean(res["damage"]) / cur)
            ko = sum(d >= cur for d in res["damage"]) / 16
            threat[mslot] = max(threat[mslot], min(1.0, frac + 0.3 * ko))
        return threat

    # ---------------------------------------------------------------- scoring
    def _score(self, view: StateView, calcs: dict, threat: list[float], slot: int, order: SingleBattleOrder) -> Scored:
        b = view.battle
        if isinstance(order, (PassBattleOrder, DefaultBattleOrder)) or order.order is None:
            return Scored(order, 0.0, {})
        me = b.active_pokemon[slot]
        if isinstance(order.order, Pokemon):
            return Scored(order, self._switch_value(view, slot, order.order, forced=b.force_switch[slot]), {})
        mv: Move = order.order
        mega_bonus = 0.3 if order.mega else 0.0
        if _is_damaging(mv):
            value, dmg = self._attack_value(view, calcs, slot, me, mv, order.move_target, order.mega)
            if mv.id == "fakeout":
                value = value + 0.5 if me.first_turn else -1.0
            return Scored(order, value + mega_bonus, dmg)
        return Scored(order, self._status_value(view, threat, slot, me, mv, order.move_target) + mega_bonus, {})

    def _attack_value(self, view: StateView, calcs: dict, slot: int, me: Pokemon, mv: Move, target: int, mega: bool) -> tuple[float, dict[int, float]]:
        b = view.battle
        _, acc, priority, tgt = _facts(mv)
        if tgt in SPREAD_TARGETS:
            hit = [p for p, _ in _targets(b, slot) if p in FOE_POS or tgt == "allAdjacent"]
        else:
            hit = [target]
        value, dmg = 0.0, {}
        my_speed = view.speed(me)
        for pos in hit:
            res = calcs.get(("us", slot, mv.id, pos, mega))
            if res is None:
                continue
            cur, full = max(1, res["defenderCurHP"]), res["defenderHP"]
            removed = min(mean(res["damage"]), cur) / full
            ko = sum(d >= cur for d in res["damage"]) / 16
            if pos in FOE_POS:
                foe = b.opponent_active_pokemon[pos - 1]
                faster = priority > 0 or (my_speed > view.speed(foe)) != view.trick_room
                value += acc * (removed + 0.6 * ko + (0.15 * ko if faster else 0.0))
                dmg[pos] = acc * min(mean(res["damage"]), cur) / cur
            else:  # our partner, caught in an all-adjacent move
                value -= 1.2 * removed + 1.0 * ko
        return value, dmg

    def _status_value(self, view: StateView, threat: list[float], slot: int, me: Pokemon, mv: Move, target: int) -> float:
        b, mid = view.battle, mv.id
        partner = b.active_pokemon[1 - slot]
        partner_up = partner is not None and not partner.fainted
        foes = [f for f in b.opponent_active_pokemon if f is not None and not f.fainted]
        foe = b.opponent_active_pokemon[target - 1] if target in FOE_POS else None
        if mid in PROTECT:
            return (0.9 if me.protect_counter == 0 else 0.1) * threat[slot] - 0.1
        if mid == "tailwind":
            if SideCondition.TAILWIND in b.side_conditions or not foes:
                return -0.5
            ours = max(view.speed(m) for m in b.active_pokemon if m is not None and not m.fainted)
            return 0.55 if ours < max(view.speed(f) for f in foes) else 0.1
        if mid == "trickroom":
            if view.trick_room:
                return -1.0
            ours = [view.speed(m) for m in b.active_pokemon if m is not None and not m.fainted]
            theirs = [view.speed(f) for f in foes] or [0]
            return 0.7 if mean(ours) < mean(theirs) else -0.3
        if mid in REDIRECT:
            return 0.6 * threat[1 - slot] if partner_up else 0.0
        if mid == "helpinghand":
            return 0.15 if partner_up else -0.5
        if mid in SLEEP or mid in {"willowisp", "thunderwave", "toxic", "glare", "nuzzle"}:
            if foe is None or foe.status is not None:
                return 0.0
            types = {t.name for t in foe.types if t is not None}
            if mid in SLEEP:
                return 0.0 if (mv.flags and "powder" in mv.flags and "GRASS" in types) else 0.45
            if mid == "willowisp":
                return 0.4 if foe.base_stats["atk"] > foe.base_stats["spa"] and "FIRE" not in types else 0.05
            if mid in {"thunderwave", "glare"}:
                return 0.3 if not ({"GROUND", "ELECTRIC"} & types) or mid == "glare" else 0.0
            return 0.2
        if mid in SETUP:
            return 0.35 if threat[slot] < 0.4 and me.current_hp_fraction > 0.7 else 0.0
        if mid in HEAL:
            return 0.8 * (1 - me.current_hp_fraction) if me.current_hp_fraction < 0.6 else 0.0
        if mid in SCREEN_MOVES:
            return 0.35 if SCREEN_MOVES[mid] not in b.side_conditions else -0.2
        if mid == "partingshot":
            return 0.35
        if mid == "wideguard":
            return 0.2
        return 0.05

    def _switch_value(self, view: StateView, slot: int, bench: Pokemon, forced: bool) -> float:
        foes = [f for f in view.battle.opponent_active_pokemon if f is not None and not f.fainted]
        match = mean(_type_matchup(bench, f) for f in foes) if foes else 0.0
        if forced:
            return 1.0 + 0.3 * match + 0.2 * bench.current_hp_fraction
        me = view.battle.active_pokemon[slot]
        # Voluntary switches only out of hopeless positions.
        if me is not None and me.current_hp_fraction < 0.25:
            return -0.1 + 0.1 * match
        return -0.4 + 0.15 * match

    def _overkill(self, view: StateView, a: Scored | None, b: Scored | None) -> float:
        if a is None or b is None:
            return 0.0
        waste = 0.0
        for pos in set(a.damage) & set(b.damage):
            waste += max(0.0, a.damage[pos] + b.damage[pos] - 1.0) * 0.8
        return waste


# poke-env's move data is mainline Gen 9; Champions changes some powers, accuracies and
# targets. Read move facts from the regulation's Showdown export where it has them.
_DEX_MOVES: dict[str, dict] = {}


def _facts(mv: Move) -> tuple[str, float, int, str]:
    """(category, accuracy 0-1, priority, target) for a move, Champions data first."""
    d = _DEX_MOVES.get(mv.id)
    if d is None:
        # Pseudo-moves (recharge, struggle) have no data entry in poke-env either.
        try:
            acc = mv.accuracy if isinstance(mv.accuracy, float) else 1.0
            return mv.category.name.capitalize(), acc, mv.priority, (mv.target.name if mv.target else "")
        except (KeyError, AttributeError):
            return "Status", 1.0, 0, ""
    acc = 1.0 if d["accuracy"] is True else d["accuracy"] / 100
    return d["category"], acc, d["priority"], d["target"]


def _is_damaging(mv: Move) -> bool:
    cat = _facts(mv)[0]
    return cat != "Status" and (mv.base_power > 0 or mv.id in _DEX_MOVES or mv.id in {"seismictoss", "nightshade", "superfang", "ruination"})


def _damaging(mon: Pokemon) -> list[Move]:
    return [m for m in mon.moves.values() if _is_damaging(m)]


def _targets(b: DoubleBattle, slot: int) -> list[tuple[int, Pokemon]]:
    out = [(pos, f) for pos, f in zip(FOE_POS, b.opponent_active_pokemon) if f is not None and not f.fainted]
    partner = b.active_pokemon[1 - slot]
    if partner is not None and not partner.fainted:
        out.append((ALLY_POS[1 - slot], partner))
    return out


def _type_matchup(mine: Pokemon, foe: Pokemon) -> float:
    off = max((foe.damage_multiplier(t) for t in mine.types if t is not None), default=1.0)
    dfn = max((mine.damage_multiplier(t) for t in foe.types if t is not None), default=1.0)
    return off - dfn
