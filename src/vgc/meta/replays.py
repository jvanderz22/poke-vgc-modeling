"""Public replays from replay.pokemonshowdown.com, and team extraction from Open Team Sheets.

Replays are cached gzipped under `data/replays/<format>/` (one JSON per replay) so re-runs
never re-download. Open-team-sheet games (every Bo3 game, opt-in Bo1) print each player's
full sheet as a `|showteam|` line: species, item, ability, moves, nature — but **not** Stat
Points, which Champions sheets never show. `impute_sp` fills a spread from the nature and
moves; every extracted team records that its spreads are imputed.
"""

from __future__ import annotations

import gzip
import json
import statistics
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator

from vgc import paths
from vgc.regulation import Dex, Regulation, to_id
from vgc.teams.sets import PokemonSet, StatPoints, Team
from vgc.teams.validate import is_legal, validate_team

BASE = "https://replay.pokemonshowdown.com"
REPLAYS = paths.ROOT / "data" / "replays"
USER_AGENT = "vgc-advisor/0.1 (research; local cache)"


def _get_json(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def search(fmt: str, pages: int = 1, before: int | None = None, delay: float = 0.5) -> Iterator[dict]:
    """Newest-first replay metadata (id, uploadtime, rating, players), 51 per page."""
    for _ in range(pages):
        q = {"format": fmt} | ({"before": str(before)} if before else {})
        page = _get_json(f"{BASE}/search.json?{urllib.parse.urlencode(q)}")
        assert isinstance(page, list)
        yield from page[:50]  # the 51st item only signals that another page exists
        if len(page) <= 50:
            return
        before = page[49]["uploadtime"]
        time.sleep(delay)


def search_user(user: str, fmt: str | None = None, pages: int = 1, delay: float = 0.5) -> Iterator[dict]:
    """Newest-first replays for one player, optionally in one format.

    This is the endpoint that makes a skill-filtered corpus possible. A replay's own `rating` is
    missing 58% of the time, so filtering on it throws away most of a strong player's games —
    including, in the sample that motivated this, a 1412 and an unrated game from the same player
    on the same day. Their identity is the dense signal; the rating on any one battle is not.
    """
    before = None
    for _ in range(pages):
        q = {"user": to_id(user)} | ({"format": fmt} if fmt else {}) | (
            {"before": str(before)} if before else {})
        page = _get_json(f"{BASE}/search.json?{urllib.parse.urlencode(q)}")
        if not isinstance(page, list) or not page:
            return
        yield from page[:50]
        if len(page) <= 50:
            return
        before = page[49]["uploadtime"]
        time.sleep(delay)


def players_in(replay: dict) -> list[str]:
    """The two player ids, from the metadata rather than the log (cheaper, and always present)."""
    return [to_id(p) for p in replay.get("players", []) if p]


def player_skill(fmts: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Per-player skill, as the **median** of the ratings their battles carried.

    Not the maximum, which was the first thing tried here and is badly biased by how much of a
    player we happen to hold: across this cache the mean *best* rating climbs 1125 → 1162 → 1205 →
    1282 as the number of cached rated games goes 1 → 2-3 → 4-9 → 10+, while the mean *median*
    moves 1125 → 1130 → 1135 → 1166. Almost all of that first curve is sample size. A filter built
    on the maximum selects heavy uploaders and calls them strong.

    `rating` is None for a player whose games were all unrated — that is not the same as being bad,
    so it is not folded into a number.
    """
    seen: dict[str, list[int]] = {}
    games: Counter = Counter()
    for fmt in fmts:
        for rep in cached(fmt):
            rating = rep.get("rating") or None
            for pid in players_in(rep):
                games[pid] += 1
                if rating:
                    seen.setdefault(pid, []).append(rating)
    return {pid: {"player": pid, "games": n,
                  "rated_games": len(seen.get(pid, [])),
                  "rating": statistics.median(seen[pid]) if seen.get(pid) else None}
            for pid, n in games.items()}


def skill_floor(skill: dict[str, dict[str, Any]], percentile: float) -> float | None:
    """The rating at `percentile` of the *observed player population*.

    A percentile rather than a fixed number, because 1100 means one thing on this ladder and
    something else on the next one — and the point of the filter is "not the bottom half of
    whoever is here", which is a statement about the population and not about Elo.
    """
    vals = sorted(e["rating"] for e in skill.values() if e["rating"] is not None)
    if not vals:
        return None
    return vals[min(int(len(vals) * percentile / 100), len(vals) - 1)]


def qualified(skill: dict[str, dict[str, Any]], percentile: float = 50.0,
              min_rated: int = 1) -> set[str]:
    """Players at or above `percentile`, with at least `min_rated` rated games to say so.

    A player with no rated game at all cannot be placed and is excluded: keeping them would be
    assuming they are average, which is the assumption the filter exists to avoid making.
    """
    floor = skill_floor(skill, percentile)
    if floor is None:
        return set()
    return {p for p, e in skill.items()
            if e["rating"] is not None and e["rating"] >= floor and e["rated_games"] >= min_rated}


def cache_path(replay_id: str, fmt: str) -> Path:
    return REPLAYS / fmt / f"{replay_id}.json.gz"


def fetch(replay_id: str, fmt: str, delay: float = 0.3) -> dict:
    path = cache_path(replay_id, fmt)
    if path.exists():
        return json.loads(gzip.decompress(path.read_bytes()))
    data = _get_json(f"{BASE}/{replay_id}.json")
    assert isinstance(data, dict)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(data).encode()))
    time.sleep(delay)
    return data


def cached(fmt: str) -> Iterator[dict]:
    for p in sorted((REPLAYS / fmt).glob("*.json.gz")):
        yield json.loads(gzip.decompress(p.read_bytes()))


# --- Open Team Sheets ------------------------------------------------------------------

def _name(table: dict[str, dict], packed: str) -> str | None:
    entry = table.get(to_id(packed)) if packed else None
    return entry["name"] if entry else (packed or None)


def unpack_sheet(packed: str, dex: Dex) -> list[PokemonSet]:
    """Showdown packed team → sets (no stats: sheets don't carry them)."""
    out = []
    for mon in packed.split("]"):
        f = mon.split("|")
        if len(f) < 6:
            continue
        nickname, species = f[0], f[1] or f[0]
        s = dex.get_species(species)
        out.append(PokemonSet(
            species=s["name"] if s else species,
            item=_name(dex.items, f[2]),
            ability=_name(dex.abilities, f[3]),
            moves=[_name(dex.moves, m) or m for m in f[4].split(",") if m],
            nature=f[5] or None,
            gender=(f[7] or None) if len(f) > 7 else None,
            nickname=nickname if s and to_id(nickname) != to_id(s["name"]) else None,
            level=50,
        ))
    return out


def impute_sp(mon: PokemonSet, dex: Dex, budget: int = 66, cap: int = 32) -> StatPoints:
    """Spread from nature and moves: the boosted attacking stat (or the majority move
    category) at `cap`, Speed at `cap` — HP instead for Speed-lowering natures or Trick Room
    setters — and the remainder into HP."""
    nature = dex.get_nature(mon.nature or "") or {}
    cats = [(dex.get_move(m) or {}).get("category") for m in mon.moves]
    if nature.get("plus") in ("atk", "spa"):
        offense = nature["plus"]
    else:
        offense = "atk" if cats.count("Physical") >= cats.count("Special") else "spa"
    slow = nature.get("minus") == "spe" or "trickroom" in {to_id(m) for m in mon.moves}
    sp = {offense: cap, ("hp" if slow else "spe"): cap}
    rest = budget - sum(sp.values())
    tank = "hp" if "hp" not in sp else "def"
    sp[tank] = sp.get(tank, 0) + min(rest, cap)
    return StatPoints.from_dict(sp)


def teams_from_replay(replay: dict, reg: Regulation) -> list[tuple[str, Team]]:
    """(player side, legal team) for each `|showteam|` sheet in the replay."""
    out = []
    for line in replay.get("log", "").split("\n"):
        if not line.startswith("|showteam|"):
            continue
        _, _, side, packed = line.split("|", 3)
        sets = unpack_sheet(packed, reg.dex)
        for s in sets:
            s.sp = impute_sp(s, reg.dex, reg.sp_budget, reg.sp_per_stat_cap)
        team = Team(sets)
        if is_legal(validate_team(team, reg)):
            out.append((side, team))
    return out


def team_key(team: Team) -> str:
    """Identity of a sheet ignoring order and (imputed) spreads."""
    return "/".join(sorted(f"{to_id(m.species)}@{to_id(m.item or '')}:{','.join(sorted(to_id(x) for x in m.moves))}" for m in team))


def team_id(key: str) -> str:
    """Short stable id for a `team_key` (used in battle records, splits and manifests)."""
    import hashlib

    return "t" + hashlib.sha1(key.encode()).hexdigest()[:11]
