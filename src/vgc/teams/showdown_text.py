"""Showdown team text ⇄ `Team`.

This is the only place Stat Points meet the `EVs:` label: Showdown's Champions mod stores
SP in its `evs` field, so `EVs: 32 HP / 32 Atk / 2 Spe` means 32/32/2 **SP**. We also accept
`SPs:` on import for readability. Grammar follows `sim/teams.ts` `parseExportedTeamLine`.
"""

from __future__ import annotations

import re

from vgc.regulation import to_id
from vgc.teams.sets import STAT_LABELS, PokemonSet, StatPoints, Team

_STAT_ALIASES = {
    "hp": "hp", "atk": "atk", "attack": "atk", "def": "def", "defense": "def",
    "spa": "spa", "spatk": "spa", "specialattack": "spa", "spd": "spd", "spdef": "spd",
    "specialdefense": "spd", "spe": "spe", "speed": "spe",
}


class TeamParseError(ValueError):
    pass


def _parse_stat_line(body: str, default: int) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in body.split("/"):
        m = re.fullmatch(r"\s*(\d+)\s+([A-Za-z. ]+?)\s*", part)
        if not m:
            raise TeamParseError(f"can't read stat entry {part.strip()!r}")
        stat = _STAT_ALIASES.get(to_id(m.group(2)))
        if not stat:
            raise TeamParseError(f"unknown stat {m.group(2)!r}")
        out[stat] = int(m.group(1))
    return {s: out.get(s, default) for s in STAT_LABELS}


def _parse_header(line: str, mon: PokemonSet) -> None:
    if " @ " in line:
        line, item = line.rsplit(" @ ", 1)
        mon.item = item.strip() or None
    line = line.strip()
    for tag, gender in ((" (M)", "M"), (" (F)", "F")):
        if line.endswith(tag):
            mon.gender, line = gender, line[: -len(tag)]
    if line.endswith(")") and "(" in line:
        nick, species = line[:-1].rsplit("(", 1)
        mon.nickname, mon.species = nick.strip() or None, species.strip()
    else:
        mon.species = line


def parse_set(block: str) -> PokemonSet:
    lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
    if not lines:
        raise TeamParseError("empty set")
    mon = PokemonSet(species="")
    _parse_header(lines[0], mon)
    for line in lines[1:]:
        if line.startswith(("Ability: ", "Trait: ")):
            mon.ability = line.split(": ", 1)[1].strip()
        elif line.startswith("Level: "):
            mon.level = int(line[7:])
        elif line.startswith(("EVs: ", "SPs: ")):
            mon.sp = StatPoints.from_dict(_parse_stat_line(line[5:], 0))
        elif line.startswith("IVs: "):
            mon.ivs = _parse_stat_line(line[5:], 31)
        elif line.startswith("Tera Type: "):
            mon.tera_type = line[11:].strip()
        elif line == "Shiny: Yes":
            mon.shiny = True
        elif re.match(r"^[A-Za-z]+ [Nn]ature", line):
            mon.nature = line.split(" ", 1)[0]
        elif line.startswith(("-", "~")):
            mon.moves.append(line[1:].strip())
        else:
            mon.unknown_lines.append(line)
    return mon


def parse_team(text: str) -> Team:
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    # Tolerate teambuilder "=== [format] name ===" headers.
    blocks = [b for b in blocks if not b.strip().startswith("===")]
    return Team([parse_set(b) for b in blocks])


def export_set(mon: PokemonSet) -> str:
    head = f"{mon.nickname} ({mon.species})" if mon.nickname else mon.species
    if mon.gender:
        head += f" ({mon.gender})"
    if mon.item:
        head += f" @ {mon.item}"
    out = [head]
    if mon.ability:
        out.append(f"Ability: {mon.ability}")
    if mon.level is not None:
        out.append(f"Level: {mon.level}")
    if mon.shiny:
        out.append("Shiny: Yes")
    if mon.tera_type:
        out.append(f"Tera Type: {mon.tera_type}")
    sp = [f"{v} {STAT_LABELS[s]}" for s, v in mon.sp.as_dict().items() if v]
    if sp:
        out.append("EVs: " + " / ".join(sp))  # SP values; see module docstring
    if mon.nature:
        out.append(f"{mon.nature} Nature")
    if mon.ivs and any(v != 31 for v in mon.ivs.values()):
        out.append("IVs: " + " / ".join(f"{v} {STAT_LABELS[s]}" for s, v in mon.ivs.items() if v != 31))
    out += [f"- {m}" for m in mon.moves]
    return "\n".join(out)


def export_team(team: Team) -> str:
    return "\n\n".join(export_set(m) for m in team) + "\n"
