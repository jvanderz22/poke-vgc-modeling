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


# Five things a spectator can know about one of the six on a team sheet, worst-informed last.
# The split that matters is the bottom two: a Pokémon nobody has seen yet is either being held
# back or was never brought, and which one it is only becomes knowable when the fourth of its
# side's four has shown itself. Calling both "unrevealed" throws away the moment that flips.
SEEN = ("active", "bench", "fainted")
ORDER = {"active": 0, "bench": 1, "fainted": 2, "unknown": 3, "unselected": 4}


def _state(mon: dict[str, Any], settled: bool) -> str:
    if mon["state"] in SEEN:
        return mon["state"]
    return "unselected" if settled or mon["state"] == "not_brought" else "unknown"


def _board(obs: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for sid in ("p1", "p2"):
        side = obs["sides"][sid]
        # Once as many distinct Pokémon have appeared as the side gets to bring, the rest of the
        # sheet is known to be sitting this game out.
        settled = sum(m["state"] in SEEN for m in side["mons"]) >= (side["team_size"] or 4)
        mons = [{"species": m["species"], "forme": m["forme"], "state": _state(m, settled),
                 "position": m["position"], "hp": m["hp"], "status": m["status"],
                 "boosts": m["boosts"]} for m in side["mons"]]
        # Active first in slot order, then the rest in order of how much is known about them.
        mons.sort(key=lambda m: (ORDER[m["state"]],
                                 m["position"] if m["position"] is not None else 9, m["species"]))
        out[sid] = {"left": _left(side), "mons": mons, "brought_known": settled,
                    "conditions": sorted(side["conditions"])}
    return out


def _active(obs: dict[str, Any], sid: str) -> dict[int, dict[str, Any]]:
    return {m["position"]: m for m in obs["sides"][sid]["mons"]
            if m["state"] == "active" and m["position"] is not None}


def _mon(m: dict[str, Any] | None) -> dict[str, Any] | None:
    if m is None:
        return None
    return {"species": m["species"], "forme": m["forme"], "state": m["state"],
            "hp": m["hp"], "status": m["status"]}


def _slots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Per side, who stood in each active slot when the step began and who stood there when it
    ended — with the one that left and the one that replaced it on the same entry.

    A slot whose occupant changed is a switch, whether it was chosen mid-turn or forced by a
    faint, and to a reader that is one event: *this* went out, *that* came in. Reporting the two
    boards separately and leaving the join to the eye is what makes a replay hard to follow.

    `started` carries the leaver's state at the *end* of the step, so a Pokémon that was knocked
    out reads as knocked out rather than at the HP it had before the hit landed.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for sid in ("p1", "p2"):
        was, now = _active(before, sid), _active(after, sid)
        final = {m["species"]: m for m in after["sides"][sid]["mons"]}
        rows = []
        for slot in sorted(set(was) | set(now)):
            left, arrived = was.get(slot), now.get(slot)
            rows.append({
                "slot": slot,
                # The leaver as it ended up — fainted, or benched at whatever HP it kept.
                "started": _mon(final.get(left["species"], left) if left else None),
                "ended": _mon(arrived),
                "changed": bool(left and arrived and left["species"] != arrived["species"]),
            })
        out[sid] = rows
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
    """One game as a sequence of steps, each a decision point: the position it was taken in, the
    log that followed it, and the position that produced.

    The step is anchored on the decision *before* the action, not after it, because that is the
    only arrangement in which the win probability describes something the player could still
    change. `before` is the board the model was asked about and `wp_p1` is its answer; `events`
    and `after` are what actually happened. The last step's `events` therefore include the KO and
    the win line — the game finishing is what followed its last decision.

    The decision points are `human_decision_points`' points, the same ones the snapshots are
    taken at, so a WP shown here is a WP the model would have been asked for rather than an
    interpolation between two it was.
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
    # Where the track ends. The model is never asked about a finished game, so the last step's
    # closing number is the result itself — 1 or 0, flagged as an outcome so the UI can say that
    # it is one. Without it the game's own ending is the one thing the trajectory leaves out,
    # and a turn that swung from 8% to a win reads as if it never resolved.
    settled = {"p1": 1.0, "p2": 0.0}.get(label.get("winner"))
    names = species_by_nickname(replay["log"].split("\n"))
    steps = []
    for i, p in enumerate(decided):
        # What followed this decision: the next point's lines, and the board they produced. The
        # walk appends an `end` point for whatever trails the last decision, so this is present
        # for every step except in the degenerate case of a log that stops on a decision point.
        nxt = points[i + 1] if i + 1 < len(points) else p
        before, after = p["obs"], nxt["obs"]
        steps.append({
            "point": i,
            "kind": p["kind"],
            "turn": before["turn"],
            "wp_p1": scored[i] if i < len(scored) else None,
            "before": _board(before),
            "events": [e for e in (narrate_line(line, names) for line in nxt["lines"]) if e],
            "after": _board(after),
            "slots": _slots(before, after),
            "weather": before["field"].get("weather"),
            "terrain": before["field"].get("terrain"),
            # True on the step the game ended on: its `after` is the final board.
            "final": nxt["kind"] == "end",
            # The number on the far side of this step: the model's call at the next decision
            # point, or — once there is no next decision — what actually happened.
            "wp_after": settled if nxt["kind"] == "end" else (
                scored[i + 1] if i + 1 < len(scored) else None),
            "outcome": nxt["kind"] == "end" and settled is not None,
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
