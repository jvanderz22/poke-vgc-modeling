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
    def id(self) -> str:
        return replays.team_id(self.key)

    @property
    def team(self) -> Team:
        return parse_team(self.text)


def formats_for(reg: Regulation) -> list[str]:
    return [reg.showdown_format + "bo3", reg.showdown_format]


def build_pool(reg: Regulation, skill_percentile: float | None = None,
               min_rated_games: int = 1) -> list[PoolTeam]:
    """Every distinct legal team in the cache, optionally only from players worth learning from.

    `skill_percentile` is applied per sheet, by whoever brought it: a team belongs to one player,
    so a strong player's team counts even when their opponent is weak. See
    `vgc.meta.replays.player_skill` for why the statistic is a median and not a maximum.
    """
    keep = None
    if skill_percentile is not None:
        skill = replays.player_skill(formats_for(reg))
        keep = replays.qualified(skill, skill_percentile, min_rated_games)

    by_key: dict[str, PoolTeam] = {}
    for fmt in formats_for(reg):
        for rep in replays.cached(fmt):
            names = {f"p{i + 1}": pid for i, pid in enumerate(replays.players_in(rep))}
            for _side, team in replays.teams_from_replay(rep, reg):
                if keep is not None and names.get(_side) not in keep:
                    continue
                key = replays.team_key(team)
                entry = by_key.get(key)
                if entry is None:
                    entry = by_key[key] = PoolTeam(key, export_team(team), 0, None, [])
                entry.count += 1
                entry.replays.append(rep["id"])
                if rep.get("rating"):
                    entry.max_rating = max(entry.max_rating or 0, rep["rating"])
    return sorted(by_key.values(), key=lambda t: (-t.count, -(t.max_rating or 0), t.key))


def sampling_weights(pool: list[PoolTeam], alpha: float = 0.7, floor: float = 0.15,
                     min_rating: int | None = None) -> list[float]:
    """How often each team should be played in generated matchups.

    Uniform sampling over the pool trains on a meta that doesn't exist: a team seen once in 800
    replay sheets gets as many battles as the most popular team. Weighting by how often a team
    actually appeared (`count`) fixes that, but pure usage weighting starves the tail, and the
    model still has to generalise to teams it has barely seen (and the held-out-team evaluation is
    drawn from the whole pool). So: weight ∝ count**alpha, mixed with a uniform `floor`.

    alpha=0 is uniform, alpha=1 is usage-proportional. `min_rating` drops teams that never appeared
    in a replay at that rating or above (ratings are sparse: most replays are unrated)."""
    counts = [t.count for t in pool]
    raw = [c ** alpha for c in counts]
    if min_rating is not None:
        raw = [w if (t.max_rating or 0) >= min_rating else 0.0 for w, t in zip(raw, pool)]
    total = sum(raw) or 1.0
    n = sum(w > 0 for w in raw) or 1
    return [(1 - floor) * w / total + (floor / n if w > 0 else 0.0) for w in raw]


def pool_path(reg: Regulation, date: dt.date | None = None, tag: str = "") -> Path:
    """Pools are dated; `tag` keeps an earlier build of the same date (results recorded against it
    stay reproducible). `load_pool` takes the last by name, so tags sort after the untagged file."""
    return TEAMS / reg.id / f"ots_pool_{(date or dt.date.today()).isoformat()}{'_' + tag if tag else ''}.json"


def save_pool(reg: Regulation, pool: list[PoolTeam], path: Path | None = None, tag: str = "") -> Path:
    path = path or pool_path(reg, tag=tag)
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
