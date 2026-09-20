"""User-facing WP tools: team-preview advice and replay trajectories.

  preview(my_team, their_team)   the player's view at team preview (open team sheets): WP for
                                 all 15 brings × 6 lead pairs, plus the bring head's guess at
                                 which 4 the opponent brings
  replay_trajectory(replay)      spectator WP for p1 at every decision point of a human replay
"""

from __future__ import annotations

import itertools
import json
from typing import Any

from vgc.data.observe import Observer
from vgc.data.snapshots import bring_view
from vgc.regulation import Regulation
from vgc.wp.features import Featurizer, Vocab, featurize
from vgc.wp.models import WPModel, load_model, model_dir, symmetrize


def _record(obs: dict, kind: str, context: str, battle: str = "live") -> dict[str, Any]:
    pol = "human" if context == "human" else "heuristic"
    return {"battle": battle, "source": "human" if pol == "human" else "selfplay", "point": 0, "kind": kind,
            "obs": obs, "label": {"winner": "p1", "ended_by": "normal", "brought": {}, "brought_complete": {"p1": False, "p2": False}},
            "meta": {"teams": {"p1": None, "p2": None}, "policies": {"p1": pol, "p2": pol}, "approx": False}}


def _load(reg: Regulation, version: str) -> tuple[WPModel, Featurizer]:
    model = load_model(reg.id, version)
    fz = Featurizer(reg, Vocab.load(model_dir(reg.id, version) / "vocab.json"))
    return model, fz


def preview_observations(reg: Regulation, my_team: str, their_team: str) -> dict[str, dict]:
    """Player (p1) and spectator observations at team preview, from the real simulator:
    both teams are validated and the open team sheets are shown, exactly as in a Bo3 game."""
    from vgc.engine.runner import BattleRunner

    with BattleRunner() as runner:
        res = runner.request({"op": "start", "id": "preview", "format": reg.showdown_format, "seed": [1, 2, 3, 4], "ots": True,
                              "p1": {"name": "p1", "team": my_team}, "p2": {"name": "p2", "team": their_team}})
        runner.request({"op": "close", "id": "preview"})
    if not res.get("ok", True):
        raise ValueError(res.get("error"))
    player, spectator = Observer("p1", reg.dex), Observer("spectator", reg.dex)
    for chunk in res["p1"]:
        for line in chunk.split("\n"):
            if line.startswith("|request|"):
                player.request(json.loads(line[len("|request|"):]))
            else:
                player.feed(line)
                if not line.startswith(("|uhtml", "|request")):
                    spectator.feed(line)
    return {"player": player.observation(), "spectator": spectator.observation()}


def preview(reg: Regulation, my_team: str, their_team: str, version: str, context: str = "human") -> dict[str, Any]:
    model, fz = _load(reg, version)
    obs = preview_observations(reg, my_team, their_team)
    mine = [m["species"] for m in obs["player"]["sides"]["p1"]["mons"]]
    theirs = [m["species"] for m in obs["player"]["sides"]["p2"]["mons"]]
    options = []
    for four in itertools.combinations(mine, 4):
        for leads in itertools.combinations(four, 2):
            order = list(leads) + [x for x in four if x not in leads]
            options.append({"bring": list(four), "leads": list(leads), "back": order[2:]})
    recs = [_record(bring_view(obs["player"], "p1", o["leads"] + o["back"]), "bring", context) for o in options]
    d = featurize(recs, fz)
    p, _ = model.predict(d)
    for o, wp in zip(options, p):
        o["wp"] = float(wp)
    options.sort(key=lambda o: -o["wp"])
    base = featurize([_record(obs["player"], "preview", context), _record(obs["spectator"], "preview", context)], fz)
    bp, bring = model.predict(base)
    sym = symmetrize(base, bp)["p"]
    out = {"version": version, "context": context, "mine": mine, "theirs": theirs,
           "preview_wp_player": float(bp[0]), "preview_wp_spectator": float(sym[1]), "options": options}
    if bring is not None:
        out["their_bring"] = {s: float(q) for s, q in zip(theirs, bring[0, 6:12])}
    by_bring: dict[tuple, dict] = {}
    for o in options:
        key = tuple(sorted(o["bring"]))
        if key not in by_bring:
            by_bring[key] = o
    out["best_by_bring"] = list(by_bring.values())
    return out


def replay_trajectory(reg: Regulation, replay: dict, version: str) -> list[dict[str, Any]]:
    from vgc.data.snapshots import human_snapshots

    model, fz = _load(reg, version)
    recs = [r for r in human_snapshots(replay, reg) if r["obs"]["perspective"] == "spectator"]
    d = featurize(recs, fz)
    p, _ = model.predict(d)
    s = symmetrize(d, p)
    out = []
    for rec, wp in zip(recs, s["p"]):
        sides = rec["obs"]["sides"]
        left = {sid: (sides[sid]["team_size"] or 4) - sum(m["state"] == "fainted" for m in sides[sid]["mons"]) for sid in ("p1", "p2")}
        active = {sid: [f"{m['forme']} {round(100 * m['hp'])}%" for m in sorted(
            (m for m in sides[sid]["mons"] if m["state"] == "active"), key=lambda m: m["position"])] for sid in ("p1", "p2")}
        out.append({"point": rec["point"], "kind": rec["kind"], "turn": rec["obs"]["turn"], "wp_p1": float(wp),
                    "left": left, "active": active})
    return out
