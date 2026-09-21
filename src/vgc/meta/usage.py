"""Descriptive usage over the cached Open Team Sheet corpus. No model; every number is a count.

This is the Pikalytics-shaped artefact for Champions: what people bring, what they hold, which
ability and nature they pick, which moves they run, and who they run it alongside. It is correct
by construction — the only way it can be wrong is by counting the wrong thing, so the three
counting decisions are recorded here rather than left implicit.

**It counts sheets, not pool entries.** `vgc.meta.pool` keys a team by `team_key`, which is
species + item + moves, and deliberately ignores ability and nature — two sheets that differ only
in a Modest/Timid choice collapse into one entry, and whichever was seen first supplies the text.
That is right for a pool of distinct teams to play and wrong for a distribution: measured against
the sheets, the pool's representative mis-assigns nature on 1.78% of Pokémon rows and ability on
0.57%, with at least one such row in 9.8% of sheets. So this module reads the replays.

**It reports two denominators.** `sheets` weights a player who played 115 games 115 times, which is
what you want for "what will I face this game". `players` counts each distinct name once, which is
what you want for "how many opponents own one". They disagree enough to matter — Basculegion is
17.9% of sheets and 22.2% of players — and they reorder the top five, so both are reported and
neither is called "usage" on its own.

**It reports no spreads.** Sheets do not carry Stat Points (`unpack_sheet`: "no stats"), and the
pool's spreads come from `impute_sp`, a deterministic function of nature and moves. Counting them
would publish our own guess as a measurement and would tell the reader nothing the nature and move
columns do not already say. Nature *is* on the sheet, so nature is counted.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from vgc.meta import replays
from vgc.meta.pool import TEAMS, formats_for
from vgc.regulation import CONDITIONAL_PRIORITY, Regulation, to_id
from vgc.teams.sets import Team


@dataclass
class Sheet:
    """One team, as one player brought it to one game."""

    replay_id: str
    fmt: str
    side: str
    player: str  # `to_id` of the Showdown name, or a per-replay placeholder when the log omits it
    rating: int | None
    team: Team

    @property
    def named(self) -> bool:
        return not self.player.startswith("?")


def _players(replay: dict) -> dict[str, str]:
    """side -> player id, from the log's `|player|` lines."""
    out = {}
    for line in replay.get("log", "").split("\n"):
        if line.startswith("|player|"):
            f = line.split("|")
            if len(f) > 4 and f[3]:
                out[f[2]] = to_id(f[3])
    return out


def sheets(reg: Regulation, source: Iterable[dict] | None = None) -> Iterator[Sheet]:
    """Every legal sheet in the cached replays, attributed to the player who brought it.

    A replay with no `|player|` name for a side yields a placeholder unique to that side of that
    replay, so an anonymous sheet is never merged with another one.
    """
    seen: set[str] = set()
    for replay in source if source is not None else _cached(reg):
        rid = replay.get("id", "")
        if rid in seen:
            continue
        seen.add(rid)
        names = _players(replay)
        rating = replay.get("rating") or None
        for side, team in replays.teams_from_replay(replay, reg):
            yield Sheet(rid, replay.get("formatid", ""), side,
                        names.get(side) or f"?{rid}:{side}", rating, team)


def _cached(reg: Regulation) -> Iterator[dict]:
    for fmt in formats_for(reg):
        yield from replays.cached(fmt)


def _counts(counter: Counter, total: int) -> list[dict[str, Any]]:
    return [{"name": name, "sheets": n, "share": n / total}
            for name, n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]


# Structural traits, as a team either carries one or it does not. A weakness report needs these
# at the *team* level — "45% of the teams you face carry Trick Room" is not recoverable from
# per-species move shares, because it is a question about the six together. Two of them are read
# off the dex rather than listed, so a new priority move or spread move is picked up for free.
TRAITS: dict[str, dict[str, set[str]]] = {
    "trick_room": {"moves": {"trickroom"}},
    "tailwind": {"moves": {"tailwind"}},
    "speed_drop": {"moves": {"icywind", "electroweb", "bulldoze", "rocktomb", "stringshot",
                             "thunderwave", "glaciate", "scaryface", "cottonspore"}},
    "redirection": {"moves": {"followme", "ragepowder", "spotlight"},
                    "abilities": {"lightningrod", "stormdrain"}},
    "fake_out": {"moves": {"fakeout"}},
    "intimidate": {"abilities": {"intimidate"}},
    "ally_switch": {"moves": {"allyswitch"}},
    "taunt": {"moves": {"taunt"}},
    "helping_hand": {"moves": {"helpinghand"}},
    "terrain": {"abilities": {"grassysurge", "psychicsurge", "electricsurge", "mistysurge"}},
    "weather": {"abilities": {"drizzle", "drought", "sandstream", "snowwarning",
                              "orichalcumpulse", "hadronengine"}},
}

SPREAD_TARGETS = {"allAdjacentFoes", "allAdjacent"}

def _derived_traits(reg: Regulation) -> dict[str, dict[str, set[str]]]:
    """`priority_attack` and `spread_move` are defined by what the dex says, not by a list."""
    moves = reg.dex.moves
    return {
        "priority_attack": {"moves": {mid for mid, m in moves.items()
                                      if ((m.get("priority") or 0) > 0 or mid in CONDITIONAL_PRIORITY)
                                      and (m.get("basePower") or 0) > 0}},
        "spread_move": {"moves": {mid for mid, m in moves.items()
                                  if m.get("target") in SPREAD_TARGETS and (m.get("basePower") or 0) > 0}},
    }


def _team_traits(team: Team, table: dict[str, dict[str, set[str]]]) -> dict[str, int]:
    """How many of the six carry each trait (0 means the team has no answer of that kind)."""
    out = {}
    for name, spec in table.items():
        want_m, want_a = spec.get("moves", set()), spec.get("abilities", set())
        out[name] = sum(
            bool({to_id(m) for m in mon.moves} & want_m) or to_id(mon.ability or "") in want_a
            for mon in team
        )
    return out


@dataclass
class _Species:
    name: str = ""
    sheets: int = 0
    players: set[str] = field(default_factory=set)
    items: Counter = field(default_factory=Counter)
    abilities: Counter = field(default_factory=Counter)
    natures: Counter = field(default_factory=Counter)
    moves: Counter = field(default_factory=Counter)
    partners: Counter = field(default_factory=Counter)
    sets: Counter = field(default_factory=Counter)  # the joint (item, ability, nature, moves)


def build(reg: Regulation, source: Iterable[dict] | None = None, *,
          min_rating: int | None = None, skill_percentile: float | None = None,
          min_rated_games: int = 1, top: int = 12, sets: int = 3) -> dict[str, Any]:
    """Count the corpus. `top` caps how many entries each distribution keeps, except items and
    abilities, which are short enough to report whole.

    `sets` caps the joint item/ability/nature/moves combinations kept per species.

    `skill_percentile` keeps only sheets brought by a player at or above that percentile of the
    observed player population. It is applied **per sheet, by whoever brought it** — a sheet
    belongs to one player, so a strong player's team still counts when their opponent is weak.
    (A *battle* corpus is the opposite case and needs both sides to qualify, because a trajectory
    is the product of both.) A percentile rather than a rating, because 1100 means one thing on
    this ladder and another on the next one, and the filter's job is "not the bottom half of
    whoever is here".

    `min_rating` keeps only sheets from replays carrying at least that rating. Ratings are sparse
    and belong to the *battle*, not the player, so the filter is reported alongside how many sheets
    carried a rating at all — a report built on the rated slice is a report on a different corpus.
    """
    per: dict[str, _Species] = {}
    all_players: set[str] = set()
    n_sheets = n_rated = 0
    n_replays: set[str] = set()
    teams: Counter = Counter()
    table = TRAITS | _derived_traits(reg)
    trait_sheets: Counter = Counter()  # sheets carrying it at all
    trait_slots: Counter = Counter()   # how many of the six, summed over sheets

    keep: set[str] | None = None
    floor = None
    if skill_percentile is not None:
        skill = replays.player_skill(formats_for(reg))
        keep = replays.qualified(skill, skill_percentile, min_rated_games)
        floor = replays.skill_floor(skill, skill_percentile)

    dropped_unplaceable = dropped_weak = 0
    for sheet in sheets(reg, source):
        if min_rating is not None and (sheet.rating or 0) < min_rating:
            continue
        if keep is not None and sheet.player not in keep:
            dropped_weak += 1
            continue
        n_sheets += 1
        n_rated += sheet.rating is not None
        n_replays.add(sheet.replay_id)
        all_players.add(sheet.player)
        teams[replays.team_key(sheet.team)] += 1
        for name, n in _team_traits(sheet.team, table).items():
            trait_sheets[name] += n > 0
            trait_slots[name] += n
        ids = [m.species_id for m in sheet.team]
        for mon in sheet.team:
            s = per.setdefault(mon.species_id, _Species(name=mon.species))
            s.sheets += 1
            s.players.add(sheet.player)
            s.items[mon.item or "(none)"] += 1
            s.abilities[mon.ability or "(none)"] += 1
            s.natures[mon.nature or "(none)"] += 1
            for move in dict.fromkeys(mon.moves):  # a sheet counts a move once
                s.moves[move] += 1
            # The marginals above are not a set. Item mode, ability mode and nature mode need not
            # co-occur on any real sheet, and a threat has to be something somebody actually
            # brought — so the joint combination is counted too, and it is also the object
            # Phase 8's set prior is P(·|species) *of*.
            s.sets[(mon.item or "(none)", mon.ability or "(none)", mon.nature or "(none)",
                    tuple(sorted(dict.fromkeys(mon.moves))))] += 1
            for other in ids:
                if other != mon.species_id:
                    s.partners[other] += 1

    n_players = len(all_players) or 1
    out_species = {}
    for sid, s in sorted(per.items(), key=lambda kv: (-kv[1].sheets, kv[0])):
        share = s.sheets / max(n_sheets, 1)
        out_species[sid] = {
            "species": s.name,
            "sheets": s.sheets,
            "share": share,
            "players": len(s.players),
            "player_share": len(s.players) / n_players,
            "items": _counts(s.items, s.sheets),
            "abilities": _counts(s.abilities, s.sheets),
            "natures": _counts(s.natures, s.sheets)[:top],
            "moves": _counts(s.moves, s.sheets)[:top],
            "partners": _partners(s, per, n_sheets, top),
            "sets": [{"item": i, "ability": a, "nature": nat, "moves": list(mv),
                      "sheets": n, "share": n / s.sheets}
                     for (i, a, nat, mv), n in s.sets.most_common(sets)],
        }
    return {
        "regulation": reg.id,
        "showdown_sha": reg.showdown_sha,
        "built": dt.date.today().isoformat(),
        "source": "replay.pokemonshowdown.com open team sheets",
        "counts": "one sheet = one team as one player brought it to one game; see vgc.meta.usage",
        "spreads": "not reported: sheets do not carry Stat Points, and the pool's are imputed",
        "sheets": n_sheets,
        "players": len(all_players),
        "replays": len(n_replays),
        "distinct_teams": len(teams),
        "rated_sheets": n_rated,
        "min_rating": min_rating,
        "skill_percentile": skill_percentile,
        "skill_floor": floor,
        "sheets_dropped_below_skill": dropped_weak,
        "traits": {
            name: {"sheets": trait_sheets[name],
                   "share": trait_sheets[name] / max(n_sheets, 1),
                   "per_team": trait_slots[name] / max(n_sheets, 1)}
            for name in sorted(table, key=lambda k: -trait_sheets[k])
        },
        "species": out_species,
    }


def _partners(s: _Species, per: dict[str, _Species], n_sheets: int, top: int) -> list[dict[str, Any]]:
    """Who it is brought with, and whether that is a preference or just both being popular.

    `lift` is the partner's share of *this* species' sheets over its share of all sheets. Without
    it the list is a restatement of the usage table: everything pairs with Rillaboom, because
    everything pairs with everything at 57%.
    """
    out = []
    for pid, n in sorted(s.partners.items(), key=lambda kv: (-kv[1], kv[0]))[:top]:
        base = per[pid].sheets / max(n_sheets, 1)
        out.append({"species": per[pid].name, "sheets": n, "share": n / s.sheets,
                    "lift": (n / s.sheets) / base if base else None})
    return out


def top_species(report: dict[str, Any], n: int = 30, by: str = "share") -> list[dict[str, Any]]:
    """The n most-used species, newest report first. `by` is "share" (per game) or "player_share"
    (per opponent) — Phase 7's threat list is per game, because that is what you play against."""
    return sorted(report["species"].values(), key=lambda s: -s[by])[:n]


def usage_path(reg: Regulation, date: dt.date | None = None) -> Path:
    return TEAMS / reg.id / f"usage_{(date or dt.date.today()).isoformat()}.json"


def save(reg: Regulation, report: dict[str, Any], path: Path | None = None) -> Path:
    path = path or usage_path(reg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1) + "\n")
    return path


def load(reg: Regulation, path: Path | None = None) -> dict[str, Any]:
    if path is None:
        found = sorted((TEAMS / reg.id).glob("usage_*.json"))
        if not found:
            raise FileNotFoundError(f"no usage report for {reg.id}; run `vgc meta usage`")
        path = found[-1]
    return json.loads(path.read_text())
