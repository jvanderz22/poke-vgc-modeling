"""User-facing WP tools: team-preview advice and replay trajectories.

  preview(my_team, their_team)   the player's view at team preview: WP for all 15 brings × 6
                                 lead pairs, plus the bring head's guess at which 4 the opponent
                                 brings. With `hidden`, their sets are left unknown, as a closed
                                 sheet shows them
  replay_trajectory(replay)      spectator WP for p1 at every decision point of a human replay
"""

from __future__ import annotations

import itertools
import json
from typing import Any

from vgc.data.observe import Observer
from vgc.data.snapshots import bring_view
from vgc.regulation import Regulation
from vgc.wp.features import Featurizer, Vocab, check_featurizer, featurize
from vgc.wp.models import WPModel, load_model, model_dir, symmetrize


def _record(obs: dict, kind: str, context: str, battle: str = "live",
            evidence: dict | None = None) -> dict[str, Any]:
    """A record shaped like a snapshot, for inference. `evidence` is what the battle has shown
    beyond `obs` (`vgc.data.snapshots.evidence`); at team preview there is none yet."""
    from vgc.data.snapshots import NO_EVIDENCE, VERSION

    pol = "human" if context == "human" else "heuristic"
    return {"v": VERSION, "battle": battle, "source": "human" if pol == "human" else "selfplay", "point": 0, "kind": kind,
            "obs": obs, "label": {"winner": "p1", "ended_by": "normal", "brought": {}, "brought_complete": {"p1": False, "p2": False}},
            "meta": {"teams": {"p1": None, "p2": None}, "policies": {"p1": pol, "p2": pol}, "approx": False},
            "evidence": evidence or NO_EVIDENCE}


def _load(reg: Regulation, version: str) -> tuple[WPModel, Featurizer]:
    fv = Featurizer.VERSION
    if version != "constant":
        card = json.loads((model_dir(reg.id, version) / "card.json").read_text())
        fv = check_featurizer(card.get("dataset") or {}, version)
    model = load_model(reg.id, version)
    # The columns the model was trained on, not the newest ones.
    fz = Featurizer(reg, Vocab.load(model_dir(reg.id, version) / "vocab.json"), version=fv)
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


def hide(obs: dict[str, Any], sid: str) -> dict[str, Any]:
    """`sid`'s side as Team Preview Only shows it: species, and no item, ability, moves or nature."""
    out = json.loads(json.dumps(obs))
    for m in out["sides"][sid]["mons"]:
        m["item"], m["item_source"] = None, None
        m["ability"], m["ability_source"] = None, None
        m["moves"], m["nature"] = [], None
    out["sides"][sid]["sheet"] = False
    return out


def per_record(recs: list[dict[str, Any]], fz: Featurizer, p: Any) -> list[float]:
    """One number per record: P(the player wins) for a player record, P(p1 wins) for a spectator
    one, which featurizes as two rows (one per seat) paired as `symmetrize` pairs them.
    `symmetrize` keys on battle and point, which every draw of one position shares."""
    out, at = [], 0
    for rec in recs:
        n = len(fz.orientations(rec))
        out.append(float(p[at]) if n == 1 else (float(p[at]) + 1 - float(p[at + 1])) / 2)
        at += n
    return out


def preview(reg: Regulation, my_team: str, their_team: str, version: str, context: str = "human",
            hidden: bool = False) -> dict[str, Any]:
    """Rank the brings.

    `hidden` is a closed sheet. `their_team` is then only a legal stand-in the simulator needs, and
    their sets are hidden again before the model sees the position, as Team Preview Only shows it
    (and a spectator of such a game sees neither side's). On 1,025 held-out closed-sheet games that
    beat averaging over sets drawn from the belief by 0.013 nats at preview, and guessing one set
    was worse than either (docs/phase8-findings.md, "which number leads on a closed sheet")."""
    model, fz = _load(reg, version)
    obs = preview_observations(reg, my_team, their_team)
    mine = [m["species"] for m in obs["player"]["sides"]["p1"]["mons"]]
    theirs = [m["species"] for m in obs["player"]["sides"]["p2"]["mons"]]
    if hidden:
        obs = {"player": hide(obs["player"], "p2"), "spectator": hide(hide(obs["spectator"], "p2"), "p1")}
    options = []
    for four in itertools.combinations(mine, 4):
        for leads in itertools.combinations(four, 2):
            order = list(leads) + [x for x in four if x not in leads]
            options.append({"bring": list(four), "leads": list(leads), "back": order[2:]})
    recs = [_record(bring_view(obs["player"], "p1", o["leads"] + o["back"]), "bring", context) for o in options]
    p, _ = model.predict(featurize(recs, fz))
    for o, wp in zip(options, p):
        o["wp"] = float(wp)
    options.sort(key=lambda o: -o["wp"])
    base_recs = [_record(obs["player"], "preview", context), _record(obs["spectator"], "preview", context)]
    base = featurize(base_recs, fz)
    bp, bring = model.predict(base)
    per = per_record(base_recs, fz, bp)
    out = {"version": version, "context": context, "mine": mine, "theirs": theirs, "hidden": hidden,
           "preview_wp_player": per[0], "preview_wp_spectator": per[1], "options": options}
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
