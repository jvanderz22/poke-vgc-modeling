"""User-facing WP tools: team-preview advice, replay trajectories, and the preview-vs-simulation
check.

  preview(my_team, their_team)   the player's view at team preview (open team sheets): WP for
                                 all 15 brings × 6 lead pairs, plus the bring head's guess at
                                 which 4 the opponent brings
  replay_trajectory(replay)      spectator WP for p1 at every decision point of a human replay
  preview_vs_sim(...)            preview WP for team pairings vs their simulated win rate
"""

from __future__ import annotations

import itertools
import json
import random
from typing import Any

import numpy as np

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


def preview_vs_sim(reg: Regulation, version: str, pairs: int = 30, n: int = 200, seed: int = 0,
                   workers: int = 6) -> dict[str, Any]:
    """Preview WP (spectator, heuristic-play context) for team pairings vs a direct simulated
    heuristic-vs-heuristic win rate, an independent estimate of the same quantity. Half the
    pairings use two training teams; the other half include a held-out team."""
    from vgc.data.snapshots import trace_snapshots
    from vgc.data.splits import load_rules
    from vgc.engine.runner import BattleRunner
    from vgc.meta.pool import load_pool
    from vgc.sim.selfplay import Matchup, load_battles, run, wilson

    rules = load_rules(reg)
    pool = load_pool(reg)
    rng = random.Random(seed)
    train = [t for t in pool if not rules.team_heldout(t.id)]
    held = [t for t in pool if rules.team_heldout(t.id)]
    chosen = [tuple(rng.sample(train, 2)) for _ in range(pairs // 2)]
    chosen += [(rng.choice(held), rng.choice(train)) for _ in range(pairs - pairs // 2)]
    ms = [Matchup(a.text, b.text, "heuristic", "heuristic", a.id, b.id, swap_sides=bool(i % 2))
          for a, b in chosen for i in range(n)]
    run_id = f"wpcheck-{version}-s{seed}-p{pairs}-n{n}"
    summary = run(ms, reg_id=reg.id, seed=seed, workers=workers, run_id=run_id)
    rows = load_battles(__import__("pathlib").Path(summary["out_dir"]))
    model, fz = _load(reg, version)
    results = []
    with BattleRunner() as runner:
        for k, (a, b) in enumerate(chosen):
            block = rows[k * n : (k + 1) * n]
            ok = [r for r in block if "error" not in r and r["a_won"] is not None]
            wins = sum(r["a_won"] for r in ok)
            first = block[0]  # A as p1
            trace = runner.request({"op": "trace", "id": first["battle_id"], "inputLog": first["input_log"], "ots": first["ots"]})
            rec = next(r for r in trace_snapshots(trace, first, reg) if r["kind"] == "preview" and r["obs"]["perspective"] == "spectator")
            d = featurize([rec], fz)
            wp = float(symmetrize(d, model.predict(d)[0])["p"][0])
            lo, hi = wilson(wins, len(ok))
            results.append({"a": a.id, "b": b.id, "heldout": rules.team_heldout(a.id) or rules.team_heldout(b.id),
                            "wp": round(wp, 4), "sim": round(wins / len(ok), 4), "ci": [round(lo, 4), round(hi, 4)], "n": len(ok)})
    wp = np.array([r["wp"] for r in results])
    sim = np.array([r["sim"] for r in results])

    def stats(mask: np.ndarray) -> dict[str, Any]:
        w, s = wp[mask], sim[mask]
        return {"pairs": int(mask.sum()), "corr": round(float(np.corrcoef(w, s)[0, 1]), 3) if mask.sum() > 2 else None,
                "mae": round(float(np.abs(w - s).mean()), 4), "mae_constant_0.5": round(float(np.abs(0.5 - s).mean()), 4),
                "within_ci": round(float(np.mean([(r["ci"][0] <= r["wp"] <= r["ci"][1]) for r, m in zip(results, mask) if m])), 3),
                "sim_spread_sd": round(float(s.std()), 4), "wp_spread_sd": round(float(w.std()), 4)}

    held_mask = np.array([r["heldout"] for r in results])
    return {"version": version, "run_id": run_id, "battles_per_pair": n, "all": stats(np.ones(len(results), bool)),
            "train_teams": stats(~held_mask), "heldout_team": stats(held_mask), "pairs": results}
