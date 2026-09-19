"""Team legality against a regulation.

Mirrors Showdown's validator for everything the simulator enforces (checked by
`tests/test_validate_parity.py`), and adds the rules Showdown doesn't: rejecting Tera in
formats without it (Showdown silently ignores the line) and warnings a player would want
(unspent SP, Mega Stones on the wrong holder).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from vgc.regulation import STAT_IDS, Regulation, to_id
from vgc.teams.sets import STAT_LABELS, PokemonSet, Team


@dataclass(frozen=True)
class Problem:
    severity: str  # "error" | "warning"
    code: str
    message: str
    pokemon: str | None = None

    def __str__(self) -> str:
        who = f"{self.pokemon}: " if self.pokemon else ""
        return f"[{self.severity}] {who}{self.message}"


def _err(code: str, msg: str, who: str | None = None) -> Problem:
    return Problem("error", code, msg, who)


def _warn(code: str, msg: str, who: str | None = None) -> Problem:
    return Problem("warning", code, msg, who)


def validate_set(mon: PokemonSet, reg: Regulation) -> list[Problem]:
    dex, who = reg.dex, mon.nickname or mon.species or "?"
    out: list[Problem] = []

    species = dex.get_species(mon.species)
    if species is None:
        return [_err("species_illegal", f"{mon.species} is not legal in {reg.name}", who)]
    if species["battleOnly"]:
        # e.g. "Salamence-Mega @ Salamencite": validate as the out-of-battle forme.
        required = species["requiredItem"] or ""
        if mon.item and to_id(mon.item) == to_id(required):
            out.append(_warn("battle_only_forme", f"{species['name']} is battle-only; listed as {species['battleOnly']}", who))
            species = dex.get_species(species["battleOnly"])
        else:
            return out + [_err("battle_only_forme", f"{species['name']} is a battle-only forme (needs {required or 'its trigger'})", who)]

    for line in mon.unknown_lines:
        out.append(_warn("unknown_line", f"ignored unrecognised line {line!r}", who))
    if mon.level is not None and mon.level != reg.level:
        out.append(_warn("level", f"level {mon.level} will be adjusted to {reg.level}", who))

    # Ability
    if not mon.ability:
        out.append(_err("ability_missing", "no ability", who))
    elif to_id(mon.ability) not in {to_id(a) for a in species["abilities"].values()}:
        out.append(_err("ability_illegal", f"{species['name']} can't have {mon.ability}", who))

    # Item
    if mon.item:
        item = dex.get_item(mon.item)
        if item is None:
            out.append(_err("item_illegal", f"{mon.item} is not legal in {reg.name}", who))
        elif item["megaStone"]:
            if not reg.mega:
                out.append(_err("mega_disabled", f"{reg.name} has no Mega Evolution ({mon.item})", who))
            elif dex.mega_forme(to_id(species["name"]), to_id(mon.item)) is None:
                out.append(_warn("mega_stone_holder", f"{mon.item} does nothing on {species['name']}", who))

    # Nature
    if mon.nature and dex.get_nature(mon.nature) is None:
        out.append(_err("nature_unknown", f"unknown nature {mon.nature}", who))

    # Moves
    if not mon.moves:
        out.append(_err("moves_none", "no moves", who))
    if len(mon.moves) > 4:
        out.append(_err("moves_too_many", f"{len(mon.moves)} moves (max 4)", who))
    for move_id, n in Counter(to_id(m) for m in mon.moves).items():
        if n > 1:
            out.append(_err("move_duplicate", f"{dex.moves.get(move_id, {}).get('name', move_id)} listed {n} times", who))
    learnable = set(species["moves"])
    for move in mon.moves:
        if to_id(move) not in dex.moves:
            out.append(_err("move_illegal", f"{move} is not legal in {reg.name}", who))
        elif to_id(move) not in learnable:
            out.append(_err("move_unlearnable", f"{species['name']} can't learn {move}", who))

    # Stat Points
    sp = mon.sp.as_dict()
    for stat in STAT_IDS:
        if sp[stat] < 0:
            out.append(_err("sp_negative", f"negative SP in {STAT_LABELS[stat]}", who))
        elif sp[stat] > reg.sp_per_stat_cap:
            out.append(_err("sp_stat_cap", f"{sp[stat]} SP in {STAT_LABELS[stat]} (max {reg.sp_per_stat_cap})", who))
    total = mon.sp.total
    if total > reg.sp_budget:
        out.append(_err("sp_budget", f"{total} total SP (max {reg.sp_budget})", who))
    elif total == 0 and (mon.nature or "Serious") == "Serious":
        # Showdown rejects this as a probable mistake; any other neutral nature opts out.
        out.append(_err("sp_zero", "0 SP invested (use a non-Serious neutral nature if intentional)", who))
    elif total < reg.sp_budget:
        out.append(_warn("sp_unspent", f"{reg.sp_budget - total} SP unspent", who))

    # IVs
    if reg.fixed_iv is not None and mon.ivs and any(v != reg.fixed_iv for v in mon.ivs.values()):
        out.append(_err("ivs_fixed", f"IVs are fixed at {reg.fixed_iv} in {reg.name}", who))

    # Mechanics this regulation doesn't have. Showdown accepts and ignores these lines.
    if mon.tera_type and not reg.tera:
        out.append(_err("tera_disabled", f"{reg.name} has no Terastallization (Tera Type: {mon.tera_type})", who))
    return out


def validate_team(team: Team, reg: Regulation) -> list[Problem]:
    dex = reg.dex
    out: list[Problem] = []
    if len(team) != reg.team_size:
        out.append(_err("team_size", f"team has {len(team)} Pokémon (need exactly {reg.team_size})"))

    for mon in team:
        out += validate_set(mon, reg)

    if reg.species_clause:
        by_num: dict[int, list[str]] = {}
        for mon in team:
            if (s := dex.get_species(mon.species)) is not None:
                by_num.setdefault(s["num"], []).append(mon.species)
        for names in by_num.values():
            if len(names) > 1:
                out.append(_err("species_clause", f"Species Clause: {' and '.join(names)} are the same species"))

    if reg.item_clause:
        items = Counter(to_id(m.item) for m in team if m.item)
        for item_id, n in items.items():
            if n > 1:
                name = (dex.items.get(item_id) or {}).get("name", item_id)
                out.append(_err("item_clause", f"Item Clause: {n} Pokémon hold {name}"))
    return out


def is_legal(problems: list[Problem]) -> bool:
    return not any(p.severity == "error" for p in problems)
