"""Parallel, seeded self-play with structured battle logs.

A run is a list of `Matchup`s (team A vs team B under policies X and Y). Battle i of a run
gets its PRNG seed from (run seed, i), and every policy decision uses an RNG seeded from the
battle seed, so a run's results are identical for any worker count. Each worker process owns
one Showdown runner and, for the heuristic, one calc sidecar.

Output (`data/selfplay/<run>/`):
  battles.jsonl.gz   one record per battle: summary + Showdown inputLog (exact replay) +
                     omniscient protocol log
  summary.json       run config, win rates with 95% Wilson intervals, timing
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import math
import multiprocessing as mp
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from vgc import paths
from vgc.engine.runner import BattleRecord, BattleRunner, battle_seed, play_battle

SELFPLAY = paths.ROOT / "data" / "selfplay"


@dataclass(frozen=True)
class Matchup:
    team_a: str  # Showdown export text
    team_b: str
    policy_a: str  # "random" | "heuristic"
    policy_b: str
    team_a_id: str = ""
    team_b_id: str = ""
    swap_sides: bool = False  # play A as p2 (alternate sides to cancel any p1/p2 bias)


# --- worker ----------------------------------------------------------------------------

_W: dict[str, Any] = {}


def _init_worker(reg_id: str) -> None:
    from vgc.regulation import load_regulation

    _W["reg"] = load_regulation(reg_id)
    _W["runner"] = BattleRunner()
    _W["policies"] = {}


def _policy(name: str):
    if name not in _W["policies"]:
        if name == "random":
            from vgc.engine.runner import RandomPolicy

            _W["policies"][name] = RandomPolicy()
        elif name == "heuristic":
            from vgc.policy.heuristic import HeuristicPolicy

            _W["policies"][name] = HeuristicPolicy(_W["reg"])
        else:
            raise ValueError(f"unknown policy {name!r}")
    return _W["policies"][name]


def _play(task: tuple[int, str, list[int], Matchup]) -> dict[str, Any]:
    index, run_id, seed, m = task
    reg = _W["reg"]
    sides = [(m.team_a, m.policy_a, m.team_a_id), (m.team_b, m.policy_b, m.team_b_id)]
    if m.swap_sides:
        sides.reverse()
    try:
        rec: BattleRecord = play_battle(
            _W["runner"], f"{run_id}-{index}", seed, reg.showdown_format,
            teams=(sides[0][0], sides[1][0]), policies=(_policy(sides[0][1]), _policy(sides[1][1])),
            team_ids=(sides[0][2], sides[1][2]),
        )
    except Exception as e:  # keep the run going; record the failure
        return {"index": index, "seed": seed, "error": f"{type(e).__name__}: {e}"}
    a_side = "p2" if m.swap_sides else "p1"
    out = rec.summary() | {"index": index, "a_side": a_side, "a_won": None if rec.winner is None else rec.winner == a_side}
    out["input_log"], out["log"] = rec.input_log, rec.log
    return out


# --- run -------------------------------------------------------------------------------

def wilson(wins: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def run(
    matchups: list[Matchup],
    reg_id: str = "reg_mc",
    seed: int = 0,
    workers: int = 4,
    run_id: str | None = None,
    out_dir: Path | None = None,
    keep_logs: bool = True,
) -> dict[str, Any]:
    run_id = run_id or f"{dt.datetime.now():%Y%m%d-%H%M%S}-s{seed}"
    out_dir = out_dir or SELFPLAY / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    tasks = [(i, run_id, battle_seed(seed, i), m) for i, m in enumerate(matchups)]
    t0 = time.perf_counter()
    results: list[dict[str, Any]] = []
    with mp.get_context("spawn").Pool(workers, initializer=_init_worker, initargs=(reg_id,)) as pool:
        for r in pool.imap_unordered(_play, tasks, chunksize=4):
            results.append(r)
    wall = time.perf_counter() - t0
    results.sort(key=lambda r: r["index"])

    with gzip.open(out_dir / "battles.jsonl.gz", "wt") as f:
        for r in results:
            row = r if keep_logs else {k: v for k, v in r.items() if k not in ("input_log", "log")}
            f.write(json.dumps(row) + "\n")

    ok = [r for r in results if "error" not in r]
    decided = [r for r in ok if r["a_won"] is not None]
    wins = sum(r["a_won"] for r in decided) + 0.5 * (len(ok) - len(decided))
    lo, hi = wilson(wins, len(ok))
    summary = {
        "run_id": run_id,
        "regulation": reg_id,
        "seed": seed,
        "workers": workers,
        "battles": len(results),
        "errors": len(results) - len(ok),
        "error_examples": [r["error"] for r in results if "error" in r][:5],
        "ties": len(ok) - len(decided),
        "a_win_rate": round(wins / len(ok), 4) if ok else None,
        "a_win_rate_95ci": [round(lo, 4), round(hi, 4)],
        "mean_turns": round(sum(r["turns"] for r in ok) / len(ok), 2) if ok else None,
        "invalid_choices": sum(r["invalid_choices"] for r in ok),
        "trapped_retries": sum(r.get("trapped_retries", 0) for r in ok),
        "wall_seconds": round(wall, 1),
        "battles_per_second": round(len(results) / wall, 2),
        "policies": sorted({(m.policy_a, m.policy_b) for m in matchups}),
        # Fingerprint of outcomes: identical across reruns with the same seed and matchups.
        "outcome_digest": _digest([(r["index"], r.get("winner"), r.get("turns")) for r in results]),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    summary["out_dir"] = str(out_dir)
    return summary


def _digest(rows: list) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


def load_battles(out_dir: Path) -> list[dict[str, Any]]:
    with gzip.open(out_dir / "battles.jsonl.gz", "rt") as f:
        return [json.loads(line) for line in f]


def gauntlet_matchups(
    teams: list[tuple[str, str]], n: int, policy_a: str, policy_b: str, seed: int = 0
) -> list[Matchup]:
    """`n` battles between random pairs of (id, text) teams, alternating sides."""
    import random

    rng = random.Random(seed)
    out = []
    for i in range(n):
        (ia, ta), (ib, tb) = rng.sample(teams, 2)
        out.append(Matchup(ta, tb, policy_a, policy_b, ia, ib, swap_sides=bool(i % 2)))
    return out


def as_dict(m: Matchup) -> dict:
    return asdict(m)
