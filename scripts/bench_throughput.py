"""Phase 0 throughput benchmark: random-vs-random doubles on a local Showdown server.

Runs `--workers` independent player pairs, each in its own OS process, splitting
`--battles` between them, and reports battles/sec. Assumes a server is already
running on localhost:8000 (`node pokemon-showdown start --no-security`).

    python scripts/bench_throughput.py --battles 200 --workers 1 4 8
"""

from __future__ import annotations

import argparse
import asyncio
import multiprocessing as mp
import time
import uuid
from pathlib import Path

FORMAT = "gen9championsvgc2026regmc"
DEFAULT_TEAM = Path(__file__).resolve().parents[1] / "tests/fixtures/teams/valid_basic.txt"


def _run_pair(args: tuple[int, int, str]) -> tuple[int, int, float]:
    """Play `n` battles between two RandomPlayers; return (finished, errors, seconds)."""
    idx, n, team = args
    from poke_env import AccountConfiguration
    from poke_env.player import RandomPlayer

    tag = uuid.uuid4().hex[:6]
    p1 = RandomPlayer(
        AccountConfiguration(f"b{idx}a{tag}", None),
        battle_format=FORMAT,
        team=team,
        max_concurrent_battles=1,
        log_level=40,
    )
    p2 = RandomPlayer(
        AccountConfiguration(f"b{idx}b{tag}", None),
        battle_format=FORMAT,
        team=team,
        max_concurrent_battles=1,
        log_level=40,
    )
    start = time.perf_counter()
    asyncio.run(p1.battle_against(p2, n_battles=n))
    elapsed = time.perf_counter() - start
    finished = sum(1 for b in p1.battles.values() if b.finished)
    return finished, n - finished, elapsed


def bench(battles: int, workers: int, team: str) -> dict:
    per = [battles // workers + (1 if i < battles % workers else 0) for i in range(workers)]
    start = time.perf_counter()
    with mp.get_context("spawn").Pool(workers) as pool:
        results = pool.map(_run_pair, [(i, n, team) for i, n in enumerate(per)])
    wall = time.perf_counter() - start
    finished = sum(r[0] for r in results)
    return {
        "workers": workers,
        "battles": finished,
        "errors": sum(r[1] for r in results),
        "wall_s": round(wall, 1),
        "battles_per_s": round(finished / wall, 2),
        "per_worker_per_s": round(finished / wall / workers, 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--battles", type=int, default=200)
    ap.add_argument("--workers", type=int, nargs="+", default=[1, 4, 8])
    ap.add_argument("--team", type=Path, default=DEFAULT_TEAM)
    a = ap.parse_args()
    team = a.team.read_text()
    for w in a.workers:
        print(bench(a.battles, w, team), flush=True)


if __name__ == "__main__":
    main()
