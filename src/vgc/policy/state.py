"""Turn poke-env battle state into damage-calc inputs.

Own Pokémon: Showdown's request carries exact stats, from which the SP spread and nature are
recovered (`infer_spread`), so our side is calculated exactly. Opponents: Open Team Sheets
reveal items/abilities/moves but never stats, so their spread is a guess (`guess_spread`).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from poke_env.battle.double_battle import DoubleBattle
from poke_env.battle.field import Field
from poke_env.battle.pokemon import Pokemon
from poke_env.battle.side_condition import SideCondition
from poke_env.battle.weather import Weather

from vgc.regulation import STAT_IDS, Dex, Regulation

WEATHER = {
    Weather.SUNNYDAY: "Sun", Weather.RAINDANCE: "Rain", Weather.SANDSTORM: "Sand",
    Weather.SNOWSCAPE: "Snow", Weather.HAIL: "Hail",
    Weather.DESOLATELAND: "Harsh Sunshine", Weather.PRIMORDIALSEA: "Heavy Rain",
}
TERRAIN = {
    Field.ELECTRIC_TERRAIN: "Electric", Field.GRASSY_TERRAIN: "Grassy",
    Field.PSYCHIC_TERRAIN: "Psychic", Field.MISTY_TERRAIN: "Misty",
}
SCREENS = {SideCondition.REFLECT: "isReflect", SideCondition.LIGHT_SCREEN: "isLightScreen", SideCondition.AURORA_VEIL: "isAuroraVeil"}

Spread = tuple[str, dict[str, int]]  # (nature name, SP)


def _stat(base: int, sp: int, pct: int) -> int:
    return (base + sp + 20) * pct // 100


@lru_cache(maxsize=4096)
def _infer(base: tuple[int, ...], stats: tuple[int, ...], natures: tuple[tuple[str, str | None, str | None], ...], budget: int, cap: int) -> Spread | None:
    hp_sp = stats[0] - base[0] - 75
    if not 0 <= hp_sp <= cap:
        return None
    for name, plus, minus in natures:
        sp = {"hp": hp_sp}
        for i, s in enumerate(STAT_IDS[1:], start=1):
            pct = 110 if plus == s else 90 if minus == s else 100
            found = next((v for v in range(cap + 1) if _stat(base[i], v, pct) == stats[i]), None)
            if found is None:
                break
            sp[s] = found
        else:
            if sum(sp.values()) <= budget:
                return name, sp
    return None


def infer_spread(species: dict, stats: dict[str, int], dex: Dex, reg: Regulation) -> Spread | None:
    """Recover (nature, SP) from exact level-50 stats. Natures with identical effect are
    interchangeable for damage, so the first consistent one is returned."""
    natures = tuple((n["name"], n["plus"], n["minus"]) for n in dex.natures.values())
    return _infer(
        tuple(species["baseStats"][s] for s in STAT_IDS), tuple(stats[s] for s in STAT_IDS),
        natures, reg.sp_budget, reg.sp_per_stat_cap,
    )


def guess_spread(species: dict) -> Spread:
    """Default opponent spread: max HP and the better attacking stat, boosting nature."""
    b = species["baseStats"]
    if b["atk"] >= b["spa"]:
        return "Adamant", {"hp": 32, "atk": 32, "spe": 2}
    return "Modest", {"hp": 32, "spa": 32, "spe": 2}


def speed(species: dict, spread: Spread, dex: Dex, boost: int = 0) -> float:
    nature = dex.get_nature(spread[0]) or {}
    pct = 110 if nature.get("plus") == "spe" else 90 if nature.get("minus") == "spe" else 100
    s = _stat(species["baseStats"]["spe"], spread[1].get("spe", 0), pct)
    return s * (2 + boost) / 2 if boost >= 0 else s * 2 / (2 - boost)


class StateView:
    """Per-decision cache mapping poke-env Pokémon to calc payloads."""

    def __init__(self, battle: DoubleBattle, reg: Regulation):
        self.battle, self.reg, self.dex = battle, reg, reg.dex
        self._spreads: dict[int, Spread] = {}

    def species(self, mon: Pokemon) -> dict:
        return self.dex.species.get(mon.species) or self.dex.get_species(mon.base_species) or {
            "name": mon.species, "baseStats": dict(mon.base_stats), "types": [], "abilities": {}, "num": 0,
        }

    def is_ours(self, mon: Pokemon) -> bool:
        return any(m is mon for m in self.battle.team.values())

    def spread(self, mon: Pokemon) -> Spread:
        key = id(mon)
        if key not in self._spreads:
            sp = None
            if self.is_ours(mon) and mon.stats and all(mon.stats.get(s) for s in STAT_IDS[1:]):
                stats = {"hp": mon.max_hp, **{s: mon.stats[s] for s in STAT_IDS[1:]}}
                sp = infer_spread(self.species(mon), stats, self.dex, self.reg)  # type: ignore[arg-type]
            self._spreads[key] = sp or guess_spread(self.species(mon))
        return self._spreads[key]

    def speed(self, mon: Pokemon) -> float:
        if self.is_ours(mon) and mon.stats.get("spe"):
            base, b = float(mon.stats["spe"]), mon.boosts.get("spe", 0)  # type: ignore[arg-type]
            s = base * (2 + b) / 2 if b >= 0 else base * 2 / (2 - b)
        else:
            s = speed(self.species(mon), self.spread(mon), self.dex, mon.boosts.get("spe", 0))
        if mon.status is not None and mon.status.name == "PAR":
            s /= 2
        conds = self.battle.side_conditions if self.is_ours(mon) else self.battle.opponent_side_conditions
        if SideCondition.TAILWIND in conds:
            s *= 2
        return s

    def payload(self, mon: Pokemon, species_name: str | None = None) -> dict[str, Any]:
        nature, sp = self.spread(mon)
        item = self.dex.items.get(mon.item or "")
        ability = self.dex.abilities.get(mon.ability or "")
        p: dict[str, Any] = {
            "species": species_name or self.species(mon)["name"],
            "item": item["name"] if item else None,
            "ability": ability["name"] if ability and not species_name else None,
            "nature": nature,
            "sp": sp,
            "boosts": {k: v for k, v in mon.boosts.items() if k in STAT_IDS and v},
            "status": mon.status.name.lower() if mon.status is not None and mon.status.name != "FNT" else "",
        }
        if self.is_ours(mon):
            p["curHP"] = max(1, mon.current_hp)
        else:
            p["curHPFraction"] = max(0.01, mon.current_hp_fraction)
        return p

    def field(self, attacker_ours: bool) -> dict[str, Any]:
        b = self.battle
        f: dict[str, Any] = {"gameType": "Doubles"}
        w = next((WEATHER[x] for x in b.weather if x in WEATHER), None)
        t = next((TERRAIN[x] for x in b.fields if x in TERRAIN), None)
        if w:
            f["weather"] = w
        if t:
            f["terrain"] = t
        mine = {v: True for k, v in SCREENS.items() if k in b.side_conditions}
        theirs = {v: True for k, v in SCREENS.items() if k in b.opponent_side_conditions}
        f["attackerSide"], f["defenderSide"] = (mine, theirs) if attacker_ours else (theirs, mine)
        return f

    @property
    def trick_room(self) -> bool:
        return Field.TRICK_ROOM in self.battle.fields
