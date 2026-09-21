"""L0 — regulation config. Every species list, mechanic flag and stat rule is read from here.

A regulation is a YAML file in `configs/regulations/` plus a legality snapshot exported
from the pinned Showdown build (`data/regulations/<id>/dex.json`, see `vgc regulation
export`). Rotating to a new regulation means adding those two files; nothing else in the
codebase names a species, item or rule.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

from vgc import paths

STAT_IDS = ("hp", "atk", "def", "spa", "spd", "spe")

# Moves whose priority the dex export understates because it is conditional: the pinned build
# applies the condition in an `onModifyPriority` hook, and the JSON export carries only the
# unconditional number. Grassy Glide is +1 under Grassy Terrain for a grounded user, and it is the
# only such move — grep finds exactly one `onModifyPriority` in the pinned `data/moves.ts`. It
# matters twice over: it is on 56% of sheets, and a move compared at the wrong priority produces a
# *wrong* turn-order inference rather than a missing one.
CONDITIONAL_PRIORITY = {"grassyglide": "grassy terrain"}

# Being airborne is what Grassy Terrain (and Ground moves) care about.
AIRBORNE_ABILITIES = {"levitate"}
AIRBORNE_ITEMS = {"airballoon"}



def to_id(text: str) -> str:
    """Showdown's `toID`: lowercase, alphanumerics only."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


class Dex:
    """Legal pool for one regulation, as exported from Showdown."""

    def __init__(self, data: dict[str, Any]):
        self.meta: dict[str, Any] = data["meta"]
        self.species: dict[str, dict] = data["species"]
        self.items: dict[str, dict] = data["items"]
        self.moves: dict[str, dict] = data["moves"]
        self.abilities: dict[str, dict] = data["abilities"]
        self.natures: dict[str, dict] = data["natures"]
        self.type_chart: dict[str, dict[str, float]] = data["typeChart"]

    @classmethod
    def load(cls, path: Path) -> "Dex":
        return cls(json.loads(path.read_text()))

    def get_species(self, name: str) -> dict | None:
        return self.species.get(to_id(name))

    def get_item(self, name: str) -> dict | None:
        return self.items.get(to_id(name))

    def get_move(self, name: str) -> dict | None:
        return self.moves.get(to_id(name))

    def get_nature(self, name: str) -> dict | None:
        return self.natures.get(to_id(name))

    def mega_forme(self, species_id: str, item_id: str) -> dict | None:
        """The Mega forme `species_id` becomes when holding `item_id`, if any."""
        item = self.items.get(item_id)
        if not item or not item.get("megaStone"):
            return None
        base = self.species.get(species_id)
        mega_name = item["megaStone"].get(base["name"]) if base else None
        return self.get_species(mega_name) if mega_name else None


# Which stat a move's damage scales with. Everything else follows the move's category; Body Press
# is the one that does not. Moves keyed off something other than the attacker's own investment —
# Foul Play off the target's Attack, Seismic Toss off nothing — get their category's stat here and
# are detected as flat by whoever sweeps them.
OFFENSIVE_STAT = {"bodypress": "def"}

# ...and which of the *target's* stats resists it. Special moves normally check Special Defence;
# Psyshock checks Defence. Neither this nor `OFFENSIVE_STAT` is in the dex export — the pinned
# build carries them as `overrideDefensiveStat` / `overrideOffensiveStat` and `vgc regulation
# export` drops both, the same omission that made `multihit` invisible to the damage channel.
# `tests/test_regulation.py` re-derives them from `vendor/` so a Showdown bump fails a test.
DEFENSIVE_STAT = {"psyshock": "def"}

# Field effects that change damage and that no channel here models. Wonder Room swaps Defence and
# Special Defence, Magic Room suppresses items, and Gravity removes Flying's Ground immunity. The
# first two never appear in this corpus and the third appears in 0.45% of human replays; the guard
# is one check either way, and the alternative is a silently wrong number.
UNMODELLED_PSEUDO = {"wonderroom", "magicroom", "gravity"}


def is_damaging(entry: dict | None) -> bool:
    """Category, not base power. 25 legal moves deal damage on a listed base power of 0 — Low
    Kick, Grass Knot, Gyro Ball, Seismic Toss and the rest compute it from weight, speed or a
    constant — and Low Kick is on Kingambit's most common set, so testing base power would drop a
    main attacking move from the most-used Pokémon."""
    return bool(entry) and entry.get("category") in ("Physical", "Special")


def offensive_stat(dex: Dex, move: str) -> str | None:
    """The stat this move's damage scales with, or None if it deals no damage."""
    entry = dex.get_move(move)
    if not is_damaging(entry):
        return None
    return OFFENSIVE_STAT.get(to_id(move)) or ("atk" if entry.get("category") == "Physical" else "spa")


def defensive_stat(dex: Dex, move: str) -> str | None:
    """The target's stat that resists this move, or None if it deals no damage."""
    entry = dex.get_move(move)
    if not is_damaging(entry):
        return None
    return DEFENSIVE_STAT.get(to_id(move)) or ("def" if entry.get("category") == "Physical" else "spd")


def is_grounded(dex: Dex, forme: str, ability: str | None, item: str | None) -> bool:
    """Grassy Terrain's +1 only reaches a grounded user, so the check is part of the priority."""
    entry = dex.get_species(forme) or {}
    if "Flying" in (entry.get("types") or []):
        return False
    return to_id(ability or "") not in AIRBORNE_ABILITIES and to_id(item or "") not in AIRBORNE_ITEMS


@dataclass(frozen=True)
class Regulation:
    id: str
    name: str
    showdown_format: str
    window: tuple[dt.date, dt.date]
    platform: str
    style: str
    team_size: int
    bring: int
    level: int
    species_clause: bool
    item_clause: bool
    mega: bool
    tera: bool
    dynamax: bool
    z_moves: bool
    stat_system: str
    sp_budget: int
    sp_per_stat_cap: int
    fixed_iv: int | None
    showdown_sha: str
    dataset_sha: str
    dataset_dir: str

    @property
    def dex_path(self) -> Path:
        return paths.REGULATION_DATA / self.id / "dex.json"

    @cached_property
    def dex(self) -> Dex:
        if not self.dex_path.exists():
            raise FileNotFoundError(
                f"No legality snapshot for {self.id} at {self.dex_path}. "
                f"Run `vgc regulation export --regulation {self.id}`."
            )
        dex = Dex.load(self.dex_path)
        if dex.meta.get("showdown_sha") != self.showdown_sha:
            raise ValueError(
                f"{self.dex_path} was exported from Showdown {dex.meta.get('showdown_sha')}, "
                f"but {self.id} pins {self.showdown_sha}. Re-run `vgc regulation export`."
            )
        return dex


def available_regulations() -> list[str]:
    return sorted(p.stem for p in paths.CONFIGS.glob("*.yaml"))


def load_regulation(reg_id: str = "reg_mc", config_dir: Path | None = None) -> Regulation:
    path = (config_dir or paths.CONFIGS) / f"{reg_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Unknown regulation {reg_id!r}; have {available_regulations()}")
    c = yaml.safe_load(path.read_text())
    stats, src = c["stats"], c["data_source"]
    if stats["system"] != "sp":
        raise NotImplementedError(f"stat system {stats['system']!r} (only 'sp' is modelled)")
    ivs = stats.get("ivs")
    return Regulation(
        id=c["id"],
        name=c["name"],
        showdown_format=c["showdown_format"],
        window=(c["window"][0], c["window"][1]),
        platform=c["platform"],
        style=c["battle"]["style"],
        team_size=c["battle"]["team_size"],
        bring=c["battle"]["bring"],
        level=c["battle"]["level"],
        species_clause=c["clauses"]["species"],
        item_clause=c["clauses"]["item"],
        mega=c["mechanics"]["mega"],
        tera=c["mechanics"]["tera"],
        dynamax=c["mechanics"]["dynamax"],
        z_moves=c["mechanics"]["z_moves"],
        stat_system=stats["system"],
        sp_budget=stats["budget"],
        sp_per_stat_cap=stats["per_stat_cap"],
        fixed_iv=int(ivs.removeprefix("fixed_")) if isinstance(ivs, str) and ivs.startswith("fixed_") else None,
        showdown_sha=src["showdown_sha"],
        dataset_sha=src["dataset_sha"],
        dataset_dir=src["dataset_dir"],
    )
