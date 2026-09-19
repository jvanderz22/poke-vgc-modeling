"""Dated, regulation-tagged team pools built from cached Open Team Sheet replays."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from vgc import paths
from vgc.meta import replays
from vgc.regulation import Regulation
from vgc.teams import export_team, parse_team
from vgc.teams.sets import Team

TEAMS = paths.ROOT / "data" / "teams"


@dataclass
class PoolTeam:
    key: str
    text: str
    count: int  # how many sheets in the corpus had this exact team
    max_rating: int | None
    replays: list[str]

    @property
    def team(self) -> Team:
        return parse_team(self.text)


def formats_for(reg: Regulation) -> list[str]:
    return [reg.showdown_format + "bo3", reg.showdown_format]


def build_pool(reg: Regulation) -> list[PoolTeam]:
    by_key: dict[str, PoolTeam] = {}
    for fmt in formats_for(reg):
        for rep in replays.cached(fmt):
            for _side, team in replays.teams_from_replay(rep, reg):
                key = replays.team_key(team)
                entry = by_key.get(key)
                if entry is None:
                    entry = by_key[key] = PoolTeam(key, export_team(team), 0, None, [])
                entry.count += 1
                entry.replays.append(rep["id"])
                if rep.get("rating"):
                    entry.max_rating = max(entry.max_rating or 0, rep["rating"])
    return sorted(by_key.values(), key=lambda t: (-t.count, -(t.max_rating or 0), t.key))


def pool_path(reg: Regulation, date: dt.date | None = None) -> Path:
    return TEAMS / reg.id / f"ots_pool_{(date or dt.date.today()).isoformat()}.json"


def save_pool(reg: Regulation, pool: list[PoolTeam], path: Path | None = None) -> Path:
    path = path or pool_path(reg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "regulation": reg.id,
        "showdown_sha": reg.showdown_sha,
        "built": dt.date.today().isoformat(),
        "source": "replay.pokemonshowdown.com open team sheets",
        "spreads": "imputed from nature + moves (sheets hide Stat Points); see vgc.meta.replays.impute_sp",
        "teams": [t.__dict__ for t in pool],
    }, indent=1) + "\n")
    return path


def load_pool(reg: Regulation, path: Path | None = None) -> list[PoolTeam]:
    if path is None:
        found = sorted((TEAMS / reg.id).glob("ots_pool_*.json"))
        if not found:
            raise FileNotFoundError(f"no team pool for {reg.id}; run `vgc meta scrape` then `vgc meta pool`")
        path = found[-1]
    return [PoolTeam(**t) for t in json.loads(path.read_text())["teams"]]
