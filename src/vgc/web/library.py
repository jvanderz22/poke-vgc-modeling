"""The team library: teams you save, stored as one JSON file per regulation.

Plain JSON on disk rather than a database — a team is a few hundred bytes of Showdown text and
the whole library is meant to be readable, diffable and hand-editable if something goes wrong.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from vgc import paths

LIBRARY = paths.DATA / "library"


@dataclass
class SavedTeam:
    id: str
    name: str
    text: str
    notes: str = ""
    archived: bool = False
    created: str = ""
    updated: str = ""
    # Validation is recomputed on save, never trusted from the file: the regulation's legality
    # snapshot can change under a team that was legal when it was written.
    legal: bool | None = None
    problems: list[dict[str, Any]] = field(default_factory=list)


def _path(reg_id: str):
    return LIBRARY / f"{reg_id}.json"


def load(reg_id: str) -> list[SavedTeam]:
    p = _path(reg_id)
    if not p.exists():
        return []
    return [SavedTeam(**t) for t in json.loads(p.read_text())["teams"]]


def _save_all(reg_id: str, teams: list[SavedTeam]) -> None:
    p = _path(reg_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"regulation": reg_id, "teams": [asdict(t) for t in teams]}, indent=1) + "\n")


def upsert(reg_id: str, team: SavedTeam) -> SavedTeam:
    now = dt.datetime.now().isoformat(timespec="seconds")
    teams = load(reg_id)
    if not team.id:
        team.id = uuid.uuid4().hex[:12]
        team.created = now
    team.updated = now
    teams = [t for t in teams if t.id != team.id] + [team]
    teams.sort(key=lambda t: t.updated, reverse=True)
    _save_all(reg_id, teams)
    return team


def delete(reg_id: str, team_id: str) -> bool:
    teams = load(reg_id)
    kept = [t for t in teams if t.id != team_id]
    if len(kept) == len(teams):
        return False
    _save_all(reg_id, kept)
    return True


def get(reg_id: str, team_id: str) -> SavedTeam | None:
    return next((t for t in load(reg_id) if t.id == team_id), None)
