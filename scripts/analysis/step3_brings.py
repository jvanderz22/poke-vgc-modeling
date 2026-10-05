"""PLAN-v4 step 3 again, with the brings the humans made (the one lever the first run left).

The first run (`step3_validity.py`) played each pairing with the heuristic's team preview and failed.
Here each human game is played with what its two players brought and led with: the four each side
brought (the snapshot's `brought`, both sides complete) and the two each sent out first (the replay's
first switch-ins). So the unit is the game, not the pairing: one game a series (series are the unit
of independence), games that ended normally (what the verdict is scored on), 8 battles each, the
policy on both sides, sides alternating. The human p1's team is team A throughout.

    step3_brings.py export [--games 1500] [--parts 4]   # then pack and kaggle_selfplay.sh each part
    step3_brings.py score                               # after merging every part
    step3_brings.py stack                               # beside the learned models, held-out games

Scored as Phase 6 scores (`vgc.sim.validity`: AUC, log loss against the constant, recalibrated out of
fold, cluster bootstrap by series), and against the first run on the games whose pairing it played.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
from typing import Any

import numpy as np

from vgc import paths

NAME = "step3b"
OUT = paths.DATA / "analysis" / "reg_mc" / "sim_validity_ewp_brings.json"
STACK = paths.DATA / "analysis" / "reg_mc" / "sim_validity_ewp_brings_stack.json"
PER_GAME = 8


def _games(reg) -> list[dict[str, Any]]:
    """Every human game, one a series, that ended normally, both teams in the pool, both sides'
    four known, with each side's preview as the players made it."""
    from vgc.battle.entry import from_team
    from vgc.data.splits import read_shard
    from vgc.meta import pool, replays
    from vgc.regulation import to_id

    teams = {t.id: t for t in pool.load_pool(reg)}
    logs = {r["id"]: r["log"] for fmt in pool.formats_for(reg) for r in replays.cached(fmt)}
    out = []
    for shard in sorted((paths.DATA / "snapshots" / reg.id / "human").glob("*/*.jsonl.gz")):
        for rec in read_shard(shard):
            if rec.get("kind") != "preview" or rec["obs"].get("perspective") != "spectator":
                continue
            m, lab = rec["meta"], rec["label"]
            ids = {sid: (m.get("teams") or {}).get(sid) for sid in ("p1", "p2")}
            if (not all(ids.values()) or ids["p1"] == ids["p2"] or any(i not in teams for i in ids.values())
                    or lab.get("winner") not in ("p1", "p2") or lab.get("ended_by") != "normal"
                    or not all((lab.get("brought_complete") or {}).get(sid) for sid in ("p1", "p2"))
                    or rec["battle"] not in logs):
                continue
            lines = logs[rec["battle"]].split("\n")
            first = next((i for i, x in enumerate(lines) if x.startswith("|turn|")), len(lines))
            preview = {}
            for sid in ("p1", "p2"):
                sets_ = [to_id(e["species"]) for e in from_team(reg, teams[ids[sid]].text)]

                def index(species: str) -> int | None:
                    s = to_id(species)
                    return next((i for i, x in enumerate(sets_) if x == s or s.startswith(x) or x.startswith(s)), None)
                leads = []
                for x in lines[:first]:
                    p = x.split("|")
                    if len(p) > 3 and p[1] == "switch" and p[2][:3] in (f"{sid}a", f"{sid}b"):
                        leads.append((p[2][2], p[3].split(",")[0]))
                leads = [s for _, s in sorted(leads)]
                order = [index(s) for s in leads] + [index(s) for s in lab["brought"][sid]]
                order = list(dict.fromkeys(i for i in order if i is not None))
                if len(leads) != 2 or len(order) != 4:
                    break
                preview[sid] = "team " + "".join(str(i + 1) for i in order)
            else:
                out.append({"battle": rec["battle"], "group": m.get("group") or rec["battle"],
                            "shard": shard.stem.split(".")[0], "a": ids["p1"], "b": ids["p2"],
                            "a_won": lab["winner"] == "p1", "preview": preview, "rating": m.get("rating")})
    return out


def export(games: int, parts: int, seed: int) -> None:
    from vgc.meta.pool import load_pool
    from vgc.regulation import load_regulation

    reg = load_regulation("reg_mc")
    teams = {t.id: t for t in load_pool(reg)}
    found = _games(reg)
    rng = random.Random(seed)
    by_group: dict[str, list] = {}
    for g in found:
        by_group.setdefault(g["group"], []).append(g)
    one = [rng.choice(sorted(v, key=lambda g: g["battle"])) for _, v in sorted(by_group.items())]
    chosen = sorted(rng.sample(one, min(games, len(one))), key=lambda g: g["battle"])
    print(f"{len(found)} games qualify, {len(one)} series; {len(chosen)} chosen")
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    for part in range(parts):
        rows = []
        for gi, g in enumerate(chosen[part::parts]):
            for k in range(PER_GAME):
                rows.append({"index": len(rows), "pairing": gi, "battle": g["battle"], "team_a": teams[g["a"]].text,
                             "team_b": teams[g["b"]].text, "team_a_id": g["a"], "team_b_id": g["b"],
                             "preview_a": g["preview"]["p1"], "preview_b": g["preview"]["p2"],
                             "policy_a": "ewp-people", "policy_b": "ewp-people", "swap_sides": bool(k % 2), "ots": True})
        d = paths.ROOT / ".vgc" / "jobs" / f"selfplay-{NAME}-{part}"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "matchups.jsonl", "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        run = {"run_id": f"gate-{NAME}-{part}", "name": f"{NAME}-{part}", "reg": "reg_mc", "seed": seed + 1000 * part,
               "part": f"{part}/{parts}", "policy": "ewp-people", "opponent": "self", "pairs": len(rows) // PER_GAME,
               "per_pairing": PER_GAME, "ewp": {}, "max_minutes": 690, "commit": commit, "unit": "game",
               "games": {g["battle"]: {k: g[k] for k in ("group", "shard", "a", "b", "a_won", "rating")}
                         for g in chosen[part::parts]}}
        (d / "run.json").write_text(json.dumps(run, indent=1) + "\n")
        print(f"  {NAME}-{part}: {len(rows)} battles -> {d}")


def _played(parts: int) -> dict[str, dict[str, Any]]:
    """Each game's record and its self-play tally: team A's wins of the battles that finished."""
    from policy_gate import _battles, _load

    games: dict[str, dict[str, Any]] = {}
    for part in range(parts):
        run, rows = _load(f"{NAME}-{part}")
        for r in _battles(run):
            if "error" in r or r["winner"] is None:
                continue
            m = rows[r["index"]]
            g = games.setdefault(m["battle"], {**run["games"][m["battle"]], "battle": m["battle"], "wins": 0, "n": 0})
            g["wins"] += r["winner"] == r["a_side"]       # team A is the human p1's
            g["n"] += 1
    return games


def score(parts: int, boots: int = 2000, seed: int = 0) -> dict[str, Any]:
    from vgc.sim.validity import _recalibrate, _score, verdict

    games = _played(parts)
    gs = sorted(games.values(), key=lambda g: g["group"])
    p = np.array([(g["wins"] + 1) / (g["n"] + 2) for g in gs])
    y = np.array([float(g["a_won"]) for g in gs])
    groups = np.array([g["group"] for g in gs])
    subsets = [_score(p, y, groups, "ended_normal", boots, seed)]
    for label, mask in (("rated", np.array([g["rating"] is not None for g in gs])),
                        ("heldout_human", np.array([g["shard"] == "heldout_human" for g in gs]))):
        if mask.sum() > 1:
            subsets.append(_score(p[mask], y[mask], groups[mask], label, boots, seed))
    rec = _score(_recalibrate(p, y, groups, seed=seed), y, groups, "ended_normal (recalibrated out-of-fold)", boots, seed)
    scored = {"subsets": subsets, "recalibrated": rec,
              "simulated_wp_spread": {"games": len(p), "sd": round(float(p.std()), 4),
                                      "share_beyond_85_15": round(float(np.mean((p >= .85) | (p <= .15))), 4)}}
    v = verdict(scored)
    v["reading"] = ("with the humans' own brings, the Phase 9 policy orders team strength the way real games do"
                    if v["pass"] else "one of the two scale-free tests clears its interval" if
                    v["orders_correctly"] or v["recalibrated_beats_constant"] else
                    "no usable signal with the humans' own brings either: the preview was not what hid it")
    # The first run on the same games, where it played their pairing (the heuristic's preview).
    first = json.loads((paths.DATA / "analysis" / "reg_mc" / "sim_validity_ewp.json").read_text())
    by_pair = {(x["a"], x["b"]) if x["a"] <= x["b"] else (x["b"], x["a"]): x for x in first["pairings"]}
    both = []
    for g, pg, yg, gr in zip(gs, p, y, groups):
        x = by_pair.get((g["a"], g["b"]) if g["a"] <= g["b"] else (g["b"], g["a"]))
        if x:
            q = x["wp"] if x["a"] == g["a"] else 1 - x["wp"]
            both.append((pg, q, yg, gr))
    paired = None
    if len(both) > 10:
        pb, qb, yb, gb = map(np.array, zip(*both))
        paired = {"games": len(both), "brings": _score(pb, yb, gb, "brings", boots, seed),
                  "heuristic_preview": _score(qb, yb, gb, "heuristic preview", boots, seed),
                  "correlation": round(float(np.corrcoef(pb, qb)[0, 1]), 3)}
    return {"policy": "ewp-people vs ewp-people, the humans' brings and leads", "battles_per_game": PER_GAME,
            **scored, "verdict": v, "against_the_first_run": paired}


def turn1_p(reg, battles: set[str], version: str) -> dict[str, float]:
    """P(p1 wins) at the start of turn 1, from the stands: both sheets and both leads, not the backs."""
    from phase6_ceiling import HELD
    from vgc.data.splits import read_shard
    from vgc.wp.models import predict_records
    from vgc.wp.tools import _load as load_model

    recs = []
    for shard in sorted((paths.DATA / "snapshots" / reg.id / "human").glob("*/*.jsonl.gz")):
        if shard.stem.split(".")[0] not in HELD:
            continue
        recs += [r for r in read_shard(shard) if r.get("kind") == "turn" and r["obs"].get("turn") == 1
                 and r["obs"].get("perspective") == "spectator" and r["battle"] in battles]
    model, fz = load_model(reg, version)
    return {r["battle"]: float(p) for r, p in zip(recs, predict_records(model, recs, fz))}


def stack(parts: int) -> dict[str, Any]:
    """Does the brings run add to what a learned model already knows (PLAN-v5 step 1, the review's
    caveat)? The brings carry the players' choices, so a pass against the constant does not show the
    simulator adds anything. On the held-out games only (the models trained on the rest):
      - alone and paired: the run's AUC beside the preview head's (both sheets) and the WP model's at
        the start of turn 1 (both sheets and both leads) on the same games;
      - stacked (3b): a logistic over the baseline's logit, against one over it and the self-play
        logit, fitted out of fold by series, the per-game log loss compared by a cluster bootstrap."""
    from phase6_ceiling import HELD, paired_auc, preview_p, scored, stacked
    from vgc.regulation import load_regulation
    from vgc.sim.validity import human_games
    from vgc.wp.models import in_battle_version, served

    reg = load_regulation("reg_mc")
    played = {b: g for b, g in _played(parts).items() if g["shard"] in HELD}
    held = [g for g in human_games(reg) if g.battle in played]
    versions = {"preview": served(reg.id, "bring"), "turn1": in_battle_version(reg.id, "open")}
    cols = {"selfplay": {b: (g["wins"] + 1) / (g["n"] + 2) for b, g in played.items()},
            "preview": preview_p(reg, held, versions["preview"]),
            "turn1": turn1_p(reg, set(played), versions["turn1"])}
    y_of = {g.battle: float(g.a_won) for g in held}
    grp = {g.battle: g.group for g in held}
    bs = sorted(b for b in y_of if all(b in c for c in cols.values()))
    y, gr = np.array([y_of[b] for b in bs]), np.array([grp[b] for b in bs])
    col = {k: np.array([c[b] for b in bs]) for k, c in cols.items()}
    return {"scored_on": f"held-out games of the brings run ({', '.join(HELD)}), ended normally",
            "versions": versions, "games": len(bs), "dropped": len(played) - len(bs),
            "alone": {k: scored(p, y, gr, k) for k, p in col.items()},
            "paired": {f"selfplay - {k}": paired_auc(col["selfplay"], col[k], y, gr) for k in ("preview", "turn1")},
            "stacked": {k: stacked(col[k], col["selfplay"], y, gr) for k in ("preview", "turn1")}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--games", type=int, default=1500)
    e.add_argument("--parts", type=int, default=4)
    e.add_argument("--seed", type=int, default=12)
    s = sub.add_parser("score")
    s.add_argument("--parts", type=int, default=4)
    t = sub.add_parser("stack")
    t.add_argument("--parts", type=int, default=4)
    a = ap.parse_args()
    if a.cmd == "export":
        export(a.games, a.parts, a.seed)
    elif a.cmd == "stack":
        res = stack(a.parts)
        STACK.write_text(json.dumps(res, indent=1) + "\n")
        print(json.dumps(res, indent=1))
    else:
        res = score(a.parts)
        OUT.write_text(json.dumps(res, indent=1) + "\n")
        print(json.dumps({k: res[k] for k in ("verdict", "simulated_wp_spread", "against_the_first_run")}, indent=1))


if __name__ == "__main__":
    main()
