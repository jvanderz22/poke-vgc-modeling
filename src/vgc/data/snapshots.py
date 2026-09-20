"""Decision-point snapshots: one observation per perspective per decision point, labelled.

Two sources, one format:
  - **self-play**: a recorded battle is re-simulated from its `inputLog` (`trace` op), which
    yields the spectator channel and each player's channel and requests. Decision points are
    exactly the requests that ask a side to choose (team preview, a turn, a forced switch),
    and the choice that side then made is recorded in the label.
  - **bring** snapshots (both sources): a player's view the moment it has committed its 4
    and its 2 leads, before seeing the opponent's leads. This is the state "WP of this
    bring/lead choice" is asked about at team preview.
  - **human replays**: the public log is the spectator channel. Decision points are team
    preview, each turn start, and end-of-turn replacements (inferred from public info).
    Where all 4 of a side's Pokémon appeared, a reconstructed player snapshot is added for
    that side (`approx`: its HP is still the public percentage).

Record (one JSON line each; `dumps` makes equal records byte-identical):
  {"v", "battle", "source", "point", "kind", "phase", "deciding", "choice_index"
   (self-play: how many choices each deciding side had made before this one — joins a
   snapshot to the live run's k-th decision),
   "obs":   {perspective, turn, field, sides},          ← the only model input
   "label": {winner, turns, ended_by, brought, brought_complete, choice},
   "meta":  {teams, group, rating, ots, format, policies, approx}}
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterator

from vgc.data.observe import PERSPECTIVES, Observer, dumps
from vgc.regulation import Regulation

VERSION = 3


def _short(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _record(battle: str, source: str, point: int, kind: str, phase: str | None, deciding: list[str],
            obs: dict, label: dict, meta: dict, choice_index: dict | None = None) -> dict[str, Any]:
    return {"v": VERSION, "battle": battle, "source": source, "point": point, "kind": kind, "phase": phase,
            "deciding": deciding, "choice_index": choice_index or {}, "obs": obs, "label": label, "meta": meta}


def bring_view(obs: dict[str, Any], sid: str, order: list[str]) -> dict[str, Any]:
    """`sid`'s view right after choosing its team at preview. `order` lists the chosen species,
    leads first. Leads show as active in slots a/b, the rest as bench, the others not brought."""
    obs = json.loads(dumps(obs))
    side = obs["sides"][sid]
    side["brought_known"] = True
    for m in side["mons"]:
        if m["species"] in order[:2]:
            m["state"], m["position"] = "active", order.index(m["species"])
        elif m["species"] in order:
            m["state"], m["position"] = "bench", None
        else:
            m["state"], m["position"] = "not_brought", None
    return obs


# --- self-play ------------------------------------------------------------------------

def _team_choice(input_log: list[str], sid: str) -> list[int]:
    line = next(line for line in input_log if line.startswith(f">{sid} team "))
    return [int(x) - 1 for x in line[len(f">{sid} team "):].replace(" ", "").split(",")]


def _preview_species(steps: list[dict], sid: str) -> list[str]:
    out = []
    for step in steps:
        for line in step["spectator"]:
            if line.startswith(f"|poke|{sid}|"):
                out.append(line.split("|")[3].split(",")[0])
        if out:
            return out
    return out


def trace_snapshots(trace: dict, battle: dict, reg: Regulation) -> list[dict[str, Any]]:
    """Snapshots for a self-play battle. `trace`: the runner's `trace` response for the
    battle's inputLog; `battle`: its self-play record (`vgc.sim.selfplay` row)."""
    steps = trace["steps"]
    input_log = [s["input"] for s in steps]
    obs = {p: Observer(p, reg.dex) for p in PERSPECTIVES}
    brought = {}
    for sid in ("p1", "p2"):
        species = _preview_species(steps, sid)
        brought[sid] = [species[i] for i in _team_choice(input_log, sid)]
    label_base = {
        "winner": trace["winner"] if trace["winner"] in ("p1", "p2") else None,
        "turns": trace["turns"], "ended_by": "normal",
        "brought": brought, "brought_complete": {"p1": True, "p2": True},
    }
    meta = {
        "teams": {"p1": battle["p1"].get("team") or None, "p2": battle["p2"].get("team") or None},
        "group": None, "rating": None, "ots": bool(battle.get("ots", True)), "format": battle.get("format"),
        "policies": {"p1": battle["p1"].get("policy"), "p2": battle["p2"].get("policy")}, "approx": False,
    }
    out: list[dict[str, Any]] = []
    point = 0
    for i, step in enumerate(steps):
        for p in PERSPECTIVES:
            obs[p].feed_many(step[p])
        reqs = {sid: json.loads(r) for sid, r in step["requests"].items()}
        for sid, r in reqs.items():
            obs[sid].request(r)
        deciding = [sid for sid in ("p1", "p2") if sid in reqs and not reqs[sid].get("wait")]
        if not deciding:
            continue
        if any(reqs[s].get("teamPreview") for s in deciding):
            kind, phase = "preview", None
        elif any(reqs[s].get("forceSwitch") for s in deciding):
            kind = "switch"
            phase = "end" if any(line == "|upkeep" for line in step["spectator"]) else "mid"
        else:
            kind, phase = "turn", None
        choice: dict[str, str] = {}
        made = {sid: sum(line.startswith(f">{sid} ") for line in input_log[: i + 1]) for sid in deciding}
        for line in input_log[i + 1 :]:
            sid = line[1:3]
            if sid in deciding and sid not in choice and line.startswith(f">{sid} "):
                choice[sid] = line[4:]
            if len(choice) == len(deciding):
                break
        label = label_base | {"choice": choice}
        views = {p: obs[p].observation() for p in PERSPECTIVES}
        for p in PERSPECTIVES:
            out.append(_record(battle["battle_id"], "selfplay", point, kind, phase, deciding, views[p], label, meta, made))
        if kind == "preview":
            for sid in deciding:
                out.append(_record(battle["battle_id"], "selfplay", point, "bring", None, [sid],
                                   bring_view(views[sid], sid, brought[sid]), label, meta, {sid: made[sid]}))
        point += 1
    return out


# --- human replays ----------------------------------------------------------------------

_BESTOF = re.compile(r"game-bestof\d-([a-z0-9-]+)")


def replay_group(replay: dict) -> str:
    """Games that must share a split: a Bo3 series, else the same pair of players."""
    m = _BESTOF.search(replay.get("log", ""))
    key = m.group(1) if m else "players:" + "|".join(sorted(p.lower() for p in replay.get("players", [])))
    return _short(key)


def player_view(obs: dict[str, Any], sid: str, brought: list[str]) -> dict[str, Any]:
    """Approximate `sid`'s own view from a spectator observation, given the 4 it brought
    (known after the game). HP stays the public fraction; exact stats are unknown."""
    obs = json.loads(dumps(obs))
    obs["perspective"] = sid
    side = obs["sides"][sid]
    side["brought_known"] = True
    for m in side["mons"]:
        if m["species"] not in brought:
            m["state"] = "not_brought"
        elif m["state"] == "unrevealed":
            m["state"] = "bench"
    return obs


def human_decision_points(replay: dict, reg: Regulation) -> tuple[Observer, list[dict[str, Any]], bool]:
    """Walk a public replay once: the spectator observer, its decision points, and whether
    someone forfeited.

    Every point carries the protocol `lines` consumed since the previous one, so a caller that
    wants to *show* the replay (the endgame browser) reads the same points, in the same places,
    as the caller that turns them into training snapshots. The trigger rules live here and only
    here — two copies of "what counts as a decision point" would drift.
    """
    o = Observer("spectator", reg.dex)
    points: list[dict[str, Any]] = []
    buffer: list[str] = []
    started = False
    forfeit = False

    def point(kind: str, phase: str | None, deciding: list[str]) -> None:
        nonlocal buffer
        points.append({"kind": kind, "phase": phase, "deciding": deciding,
                       "obs": o.observation(), "lines": buffer})
        buffer = []

    for line in replay["log"].split("\n"):
        kind = line.split("|")[1] if line.startswith("|") and line.count("|") >= 1 else ""
        if kind == "start" and not started:
            started = True
            point("preview", None, ["p1", "p2"])  # before |start|: the lines so far are the sheets
        if kind == "-message" and "forfeited" in line:
            forfeit = True
        buffer.append(line)
        o.feed(line)
        if kind == "turn":
            point("turn", None, ["p1", "p2"])
        elif kind == "upkeep" and not o.ended:
            need = [sid for sid in ("p1", "p2") if o.empty_slot_needs_switch(sid)]
            if need:
                point("switch", "end", need)
    if buffer:  # whatever followed the last decision point: the KO, the win line
        points.append({"kind": "end", "phase": None, "deciding": [],
                       "obs": o.observation(), "lines": buffer})
    return o, points, forfeit


def human_snapshots(replay: dict, reg: Regulation, team_ids: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Snapshots for one public replay (spectator, plus approximate player views where a
    side's full 4 appeared)."""
    o, walked, forfeit = human_decision_points(replay, reg)
    pending = [(p["kind"], p["phase"], p["deciding"], p["obs"]) for p in walked if p["kind"] != "end"]
    appeared = {sid: [m.species for m in o.sides[sid].mons if m.state != "unrevealed"] for sid in ("p1", "p2")}
    first_turn = next((obs for kind, _, _, obs in pending if kind == "turn"), None)
    leads = {sid: [m["species"] for m in sorted((m for m in first_turn["sides"][sid]["mons"] if m["state"] == "active"),
                                                key=lambda m: m["position"])] if first_turn else []
             for sid in ("p1", "p2")}
    size = {sid: o.sides[sid].team_size or 4 for sid in ("p1", "p2")}
    complete = {sid: len(appeared[sid]) >= size[sid] for sid in ("p1", "p2")}
    label = {
        "winner": o.winner, "turns": o.turn, "ended_by": "forfeit" if forfeit else "normal",
        "brought": appeared, "brought_complete": complete, "choice": {},
    }
    base_meta = {
        "teams": {sid: (team_ids or {}).get(sid) for sid in ("p1", "p2")}, "group": replay_group(replay),
        "rating": replay.get("rating"), "ots": o.sides["p1"].sheet and o.sides["p2"].sheet,
        "format": replay.get("formatid"), "policies": {"p1": "human", "p2": "human"},
    }
    out = []
    for point, (kind, phase, deciding, spec) in enumerate(pending):
        out.append(_record(replay["id"], "human", point, kind, phase, deciding, spec, label, base_meta | {"approx": False}))
        for sid in ("p1", "p2"):
            if complete[sid]:
                view = player_view(spec, sid, appeared[sid])
                out.append(_record(replay["id"], "human", point, kind, phase, deciding, view, label, base_meta | {"approx": True}))
                if kind == "preview" and len(leads[sid]) == 2:
                    order = leads[sid] + [x for x in appeared[sid] if x not in leads[sid]]
                    out.append(_record(replay["id"], "human", point, "bring", None, [sid], bring_view(view, sid, order),
                                       label, base_meta | {"approx": True}))
    return out


def iter_lines(records: list[dict[str, Any]]) -> Iterator[str]:
    for r in records:
        yield dumps(r)
