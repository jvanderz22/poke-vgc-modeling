"""Solver positions solved somewhere else: exported here, solved there, merged back into the cache.

The solver is a pure function of its input and its own source. Chance is enumerated, not sampled, so
a position solved on a Kaggle box gives the answer the laptop would have given, and the cache key
(`solver.position_key`) is the solver's declared version (or its sha256) plus the position. Offloading is therefore only a
way to fill `.vgc/endgame-solver.jsonl` ahead of time. The commands that use the answers
(`vgc wp solve`, `scripts/analysis/solver_vs_humans.py`) run unchanged afterwards and find every
position already cached.

  export   positions → jobs.jsonl, `{key, position}` a line, skipping what is cached
  (remote) scripts/cloud/solve_batch.py → results.jsonl, `{key, result | timeout | error}` a line,
           and summary.json with the sha256 of the solver it ran
  merge    results.jsonl → the cache, refused unless the solver that ran behaves as the one here
           (the same declared version, or the same source), and checked by solving the smallest
           few again locally: same value, leaf mass and nodes

Scoring never leaves the laptop. What goes out is positions, which are public replays' teams at
their first 1v1, and what comes back is the engine's answer to each.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from vgc.wp import solver


def solver_sha() -> str:
    return hashlib.sha256(solver.SOLVER.read_bytes()).hexdigest()


def export(positions: Iterable[dict[str, Any]], path: Path) -> dict[str, int]:
    """Write every position not already cached, once each, in the order given (put the cheap ones
    first: a run that hits its deadline has then done the most positions it could)."""
    cached = solver._cached()
    timed_out = solver.timed_out()
    seen: set[str] = set()
    counts = {"positions": 0, "cached": 0, "duplicate": 0, "timed_out_before": 0, "written": 0}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for pos in positions:
            counts["positions"] += 1
            key = solver.position_key(pos)
            if key in cached:
                counts["cached"] += 1
                continue
            if key in seen:
                counts["duplicate"] += 1
                continue
            seen.add(key)
            # Written anyway: a remote box has its own budget, and the local cap says nothing
            # about what an uncapped or longer run can finish.
            counts["timed_out_before"] += key in timed_out
            fh.write(json.dumps({"key": key, "position": pos}, sort_keys=True) + "\n")
            counts["written"] += 1
    return counts


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def merge(results: Path, jobs: Path, summary: Path | None = None, verify: int = 3) -> dict[str, Any]:
    """Results into the cache. Raises, merging nothing, when the solver that ran does not behave as
    this one, a result is for a position that was not sent, or a re-solve here disagrees.

    Results are keyed here, by the positions sent, not by the keys they were sent under: a run
    exported before the solver declared a version still lands under the current key."""
    summary = summary or results.with_name("summary.json")
    info = json.loads(summary.read_text()) if summary.exists() else {}
    here, version = solver_sha(), solver.solver_version()
    same_source = info.get("solver_sha256") == here
    same_version = version is not None and info.get("solver_version") == version
    if not (same_source or same_version):
        raise ValueError(f"results came from solver {str(info.get('solver_sha256'))[:12]} "
                         f"(version {info.get('solver_version')}), and this one is {here[:12]} "
                         f"(version {version}): its answers need not be this solver's")
    sent = {j["key"]: j["position"] for j in _lines(jobs)}
    rows = _lines(results)
    unknown = [r["key"] for r in rows if r["key"] not in sent]
    if unknown:
        raise ValueError(f"{len(unknown)} results for positions that were not sent, e.g. {unknown[0][:12]}")

    solved = [r for r in rows if "result" in r]
    checked = []
    for r in sorted(solved, key=lambda r: r["result"]["nodes"])[:verify]:
        here_r = solver.solve_uncached(sent[r["key"]])
        same = all(here_r[k] == r["result"][k] for k in ("value", "leaf_mass", "nodes"))
        checked.append({"key": r["key"][:12], "nodes": r["result"]["nodes"], "same": same})
        if not same:
            raise ValueError(f"re-solved here, {r['key'][:12]} differs: {here_r} against {r['result']}")

    cached = solver._cached()
    keyed = {r["key"]: solver.position_key(sent[r["key"]]) for r in rows}
    new = [r for r in solved if keyed[r["key"]] not in cached]
    with solver._cache_lock:
        solver.CACHE.parent.mkdir(parents=True, exist_ok=True)
        with solver.CACHE.open("a") as fh:
            for r in new:
                fh.write(json.dumps({"key": keyed[r["key"]], "result": r["result"],
                                     "position": sent[r["key"]]}) + "\n")
        timeouts = [r for r in rows if "timeout" in r]
        with solver.TIMEOUTS.open("a") as fh:
            for r in timeouts:
                fh.write(json.dumps({"key": keyed[r["key"]], "cap": r["timeout"], "where": info.get("where")}) + "\n")
    return {"results": len(rows), "merged": len(new), "already_cached": len(solved) - len(new),
            "timeouts": len(timeouts), "errors": [r for r in rows if "error" in r],
            "unanswered": len(sent) - len(rows), "verified": checked,
            "where": info.get("where"), "elapsed_s": info.get("elapsed_s")}
