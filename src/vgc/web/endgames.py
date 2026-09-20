"""Decided endgames: real human games the WP model called at 90%+ before they ended.

What this is for: a 90% number is only worth putting on screen if 90% of those positions are
actually won. The held-out human corpus is where that can be checked without marking our own
homework, and the honest way to check it is to *look* at the positions — so this builds a
browsable set of them and the web app lets you step through each game turn by turn.

Every game in the set satisfies all of:

  * **human vs human.** Both accounts are people, not the scripted ladder alts (`_BOT`), and
    both teams are ones those people built. Nothing here is self-play.
  * **held out.** The replay's group falls on the held-out side of the frozen split, recomputed
    from `data/splits/<reg>.json` rather than trusted from any file, so the model never saw the
    game or its series.
  * **open team sheets.** Every WP model is an OTS model; scoring a closed-sheet game with one
    would be scoring it on inputs it was never given.
  * **it ended with a verdict**, and the model held ≥ `min_wp` on the same side for the last
    `hold` decision points — a sustained call near the end, not a single spike.

`correct` on each row is simply whether that side went on to win. Summed over the set, that is
the number the UI leads with, and it is the claim this page is making.

The scan reads every cached replay and takes about a minute, so `vgc wp endgames` writes the
index to `data/analysis/<reg>/endgames.json` and the API serves it. Individual games are scored
on demand — one replay is milliseconds.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Callable

from vgc import paths
from vgc.regulation import Regulation

ANALYSIS = paths.DATA / "analysis"
REPLAY_URL = "https://replay.pokemonshowdown.com"

# Scripted ladder alts, which are not "a human-built team" on either side. The *shape* is what
# identifies them — "bot" followed by a hex serial or a `#n` — because the bare substring also
# matches people who called themselves robotarmadillo or bottomplayer.
_BOT = re.compile(r"bot(?:[0-9a-f]{4,}|#\d+)$", re.IGNORECASE)

DEFAULTS = {"min_wp": 0.90, "hold": 3, "min_turns": 4}


def index_path(reg: Regulation) -> Path:
    return ANALYSIS / reg.id / "endgames.json"


def load_index(reg: Regulation) -> dict[str, Any] | None:
    path = index_path(reg)
    return json.loads(path.read_text()) if path.exists() else None


def is_bot(player: str) -> bool:
    return bool(_BOT.search(player))


def _left(side: dict[str, Any]) -> int:
    """How many of that side's four are still standing."""
    return (side["team_size"] or 4) - sum(m["state"] == "fainted" for m in side["mons"])


def _spectator_wp(reg: Regulation, records: list[dict[str, Any]], model, fz) -> list[float]:
    from vgc.wp.features import featurize
    from vgc.wp.models import symmetrize

    d = featurize(records, fz)
    p, _ = model.predict(d)
    return [float(x) for x in symmetrize(d, p)["p"]]


def _eligible(reg: Regulation, replay: dict[str, Any], rules) -> list[dict[str, Any]] | None:
    """The spectator records of a replay that belongs in the set, or None with the reason dropped.

    Order matters for the counters the CLI prints, but the checks are independent.
    """
    from vgc.data.snapshots import human_snapshots, replay_group

    if rules.split_of("human", replay["id"], [], replay_group(replay)) != "heldout_human":
        return None
    if any(is_bot(p) for p in replay.get("players") or []):
        return None
    records = [r for r in human_snapshots(replay, reg)
               if r["obs"]["perspective"] == "spectator" and not r["meta"]["approx"]]
    if not records or not records[0]["meta"]["ots"]:
        return None
    return records


def scan(reg: Regulation, version: str, *, min_wp: float = 0.90, hold: int = 3,
         min_turns: int = 4, progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Every qualifying endgame in the cached replays, with the counts that were dropped and why.

    The counts are part of the result on purpose: "240 games" means nothing without "out of the
    1,114 held-out human games that could have qualified".
    """
    from vgc.data.splits import load_rules
    from vgc.meta import pool, replays
    from vgc.wp.tools import _load

    rules = load_rules(reg)
    model, fz = _load(reg, version)
    counts = {"cached": 0, "eligible": 0, "unfinished": 0, "too_short": 0, "selected": 0}
    rows: list[dict[str, Any]] = []

    for fmt in pool.formats_for(reg):
        for replay in replays.cached(fmt):
            counts["cached"] += 1
            if progress:
                progress(counts["cached"], counts["selected"])
            records = _eligible(reg, replay, rules)
            if records is None:
                continue
            label = records[0]["label"]
            if label["winner"] not in ("p1", "p2"):
                counts["unfinished"] += 1
                continue
            if (label["turns"] or 0) < min_turns or len(records) <= hold:
                counts["too_short"] += 1
                continue
            counts["eligible"] += 1

            wp = _spectator_wp(reg, records, model, fz)
            side = "p1" if wp[-1] >= 0.5 else "p2"
            conf = [w if side == "p1" else 1 - w for w in wp]
            if min(conf[-hold:]) < min_wp:
                continue
            counts["selected"] += 1

            # How long the call held: the first decision point after which it never dipped below
            # the threshold again. That is the turn the game stopped being in doubt, per the model.
            locked = len(conf) - 1
            while locked > 0 and conf[locked - 1] >= min_wp:
                locked -= 1
            final = records[-1]["obs"]["sides"]
            rows.append({
                "replay": replay["id"],
                "format": replay.get("formatid") or fmt,
                "url": f"{REPLAY_URL}/{replay['id']}",
                "players": {"p1": (replay.get("players") or ["?", "?"])[0],
                            "p2": (replay.get("players") or ["?", "?"])[1]},
                "rating": replay.get("rating"),
                "turns": label["turns"],
                "ended_by": label["ended_by"],
                "winner": label["winner"],
                "side": side,
                "wp": round(conf[-1], 4),
                "correct": side == label["winner"],
                "points": len(records),
                "locked_point": locked,
                "locked_turn": records[locked]["obs"]["turn"],
                "left": {sid: _left(final[sid]) for sid in ("p1", "p2")},
            })

    rows.sort(key=lambda r: (r["correct"], -r["wp"]))  # the misses first: they are the ones to look at
    return {
        "regulation": reg.id,
        "version": version,
        "built": dt.datetime.now().isoformat(timespec="seconds"),
        "criteria": {"min_wp": min_wp, "hold": hold, "min_turns": min_turns,
                     "held_out": True, "ots": True, "human_only": True},
        "counts": counts,
        "correct": sum(r["correct"] for r in rows),
        "games": rows,
    }


def write_index(reg: Regulation, result: dict[str, Any]) -> Path:
    path = index_path(reg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1) + "\n")
    return path


# --- one game, step by step -----------------------------------------------------------

def _sheet(obs: dict[str, Any], sid: str) -> list[dict[str, Any]]:
    """A side's open team sheet as the spectator saw it at preview."""
    return [{"species": m["species"], "item": m["item"], "ability": m["ability"],
             "moves": list(m["moves"])} for m in obs["sides"][sid]["mons"]]


def _board(obs: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for sid in ("p1", "p2"):
        side = obs["sides"][sid]
        mons = [{"species": m["species"], "forme": m["forme"], "state": m["state"],
                 "position": m["position"], "hp": m["hp"], "status": m["status"],
                 "boosts": m["boosts"]} for m in side["mons"]]
        # Active first, in slot order, then the bench: that is how the position reads on screen.
        mons.sort(key=lambda m: (m["state"] != "active", m["position"] if m["position"] is not None else 9,
                                 m["state"] == "fainted", m["species"]))
        out[sid] = {"left": _left(side), "mons": mons,
                    "conditions": sorted(side["conditions"])}
    return out


def find(reg: Regulation, replay_id: str) -> dict[str, Any]:
    """A cached replay by id, whichever of the regulation's formats holds it."""
    from vgc.meta import pool, replays

    for fmt in pool.formats_for(reg):
        path = replays.cache_path(replay_id, fmt)
        if path.exists():
            return replays.fetch(replay_id, fmt)
    raise FileNotFoundError(f"{replay_id} is not in the replay cache; run `vgc meta scrape`")


def detail(reg: Regulation, replay_id: str, version: str) -> dict[str, Any]:
    """One game as a sequence of positions: what the board looked like, what happened next, and
    what the model thought at each decision point.

    The points are `human_decision_points`' points — the same ones the snapshots are taken at —
    so a WP shown here is a WP the model would have been asked for, not an interpolation. The
    trailing step is everything after the last decision point: the KO that finished it, and the
    win line.
    """
    from vgc.data.snapshots import human_decision_points, human_snapshots
    from vgc.web.narrate import narrate_line, species_by_nickname
    from vgc.wp.tools import _load

    replay = find(reg, replay_id)
    _observer, points, _forfeit = human_decision_points(replay, reg)
    records = [r for r in human_snapshots(replay, reg)
               if r["obs"]["perspective"] == "spectator" and not r["meta"]["approx"]]
    decided = [p for p in points if p["kind"] != "end"]
    if len(decided) != len(records):  # the two walks disagree — refuse rather than mislabel
        raise ValueError(f"{replay_id}: {len(decided)} decision points but {len(records)} snapshots")

    wp_error = None
    try:
        model, fz = _load(reg, version)
        scored: list[float | None] = list(_spectator_wp(reg, records, model, fz))
    except Exception as e:  # a WP failure must not cost you the replay
        scored, wp_error = [None] * len(records), f"{type(e).__name__}: {e}"

    label = records[0]["label"] if records else {}
    names = species_by_nickname(replay["log"].split("\n"))
    steps = []
    for i, p in enumerate(points):
        events = [e for e in (narrate_line(line, names) for line in p["lines"]) if e]
        steps.append({
            "point": i if p["kind"] != "end" else None,
            "kind": p["kind"],
            "turn": p["obs"]["turn"],
            "wp_p1": scored[i] if i < len(scored) and p["kind"] != "end" else None,
            "events": events,
            "board": _board(p["obs"]),
            "weather": p["obs"]["field"].get("weather"),
            "terrain": p["obs"]["field"].get("terrain"),
        })
    return {
        "replay": replay_id,
        "format": replay.get("formatid"),
        "url": f"{REPLAY_URL}/{replay_id}",
        "players": {"p1": (replay.get("players") or ["?", "?"])[0],
                    "p2": (replay.get("players") or ["?", "?"])[1]},
        "rating": replay.get("rating"),
        "winner": label.get("winner"),
        "turns": label.get("turns"),
        "ended_by": label.get("ended_by"),
        "version": version,
        "wp_error": wp_error,
        "sheets": {sid: _sheet(points[0]["obs"], sid) for sid in ("p1", "p2")} if points else {},
        "steps": steps,
    }
