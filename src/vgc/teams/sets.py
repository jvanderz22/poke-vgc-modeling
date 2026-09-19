"""Team data model. Stat Points are first-class; EVs never appear in this layer."""

from __future__ import annotations

from dataclasses import dataclass, field

from vgc.regulation import STAT_IDS, Dex, Regulation, to_id

STAT_LABELS = {"hp": "HP", "atk": "Atk", "def": "Def", "spa": "SpA", "spd": "SpD", "spe": "Spe"}


@dataclass
class StatPoints:
    """Champions Stat Points. At level 50 each SP adds exactly 1 to the stat."""

    hp: int = 0
    atk: int = 0
    def_: int = 0
    spa: int = 0
    spd: int = 0
    spe: int = 0

    def get(self, stat: str) -> int:
        return getattr(self, "def_" if stat == "def" else stat)

    def as_dict(self) -> dict[str, int]:
        return {s: self.get(s) for s in STAT_IDS}

    @property
    def total(self) -> int:
        return sum(self.as_dict().values())

    @classmethod
    def from_dict(cls, d: dict[str, int]) -> "StatPoints":
        return cls(**{("def_" if k == "def" else k): v for k, v in d.items()})


@dataclass
class PokemonSet:
    species: str
    item: str | None = None
    ability: str | None = None
    nature: str | None = None
    moves: list[str] = field(default_factory=list)
    sp: StatPoints = field(default_factory=StatPoints)
    nickname: str | None = None
    gender: str | None = None
    level: int | None = None
    shiny: bool = False
    # Present only so the validator can reject them for formats without the mechanic.
    tera_type: str | None = None
    ivs: dict[str, int] | None = None
    unknown_lines: list[str] = field(default_factory=list)

    @property
    def species_id(self) -> str:
        return to_id(self.species)


@dataclass
class Team:
    members: list[PokemonSet]

    def __iter__(self):
        return iter(self.members)

    def __len__(self) -> int:
        return len(self.members)


def calc_stats(pokemon: PokemonSet, dex: Dex, reg: Regulation, species_id: str | None = None) -> dict[str, int]:
    """Actual stats at the regulation level. Champions formula (IVs fixed at 31, level 50):
    HP = base + SP + 75; other = floor((base + SP + 20) × nature). `species_id` overrides the
    forme, e.g. to get a Mega's stats."""
    if reg.level != 50 or reg.fixed_iv != 31:
        raise NotImplementedError("Champions stat formula assumes level 50 and 31 IVs")
    species = dex.species[species_id or pokemon.species_id]
    nature = dex.get_nature(pokemon.nature or "Serious") or {}
    out = {}
    for s in STAT_IDS:
        base, sp = species["baseStats"][s], pokemon.sp.get(s)
        if s == "hp":
            out[s] = 1 if base == 1 else base + sp + 75
            continue
        # Integer math, as Showdown does it — float 1.1 multiplies can land one point off.
        pct = 110 if nature.get("plus") == s else 90 if nature.get("minus") == s else 100
        out[s] = (base + sp + 20) * pct // 100
    return out
