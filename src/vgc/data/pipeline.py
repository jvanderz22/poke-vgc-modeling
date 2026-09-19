"""Snapshot datasets on disk.

  data/snapshots/<regulation>/selfplay/<run_id>/<split>.jsonl.gz
  data/snapshots/<regulation>/human/<format>/<split>.jsonl.gz
  data/snapshots/<regulation>/manifests/<name>.json

Self-play runs are re-simulated from their inputLogs in parallel (one battle runner per worker).
Records are written in battle order, so re-extracting the same run gives byte-identical files.
"""

from __future__ import annotations

import multiprocessing as mp
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from vgc import paths
from vgc.data.observe import dumps
from vgc.data.snapshots import human_snapshots, replay_group, trace_snapshots
from vgc.data.splits import SPLIT_NAMES, SplitRules, load_rules, write_shard
from vgc.regulation import Regulation

SNAPSHOTS = paths.ROOT / "data" / "snapshots"

# --- self-play --------------------------------------------------------------------------

_W: dict[str, Any] = {}


def _init(reg_id: str) -> None:
    from vgc.engine.runner import BattleRunner
    from vgc.regulation import load_regulation

    _W["reg"] = load_regulation(reg_id)
    _W["runner"] = BattleRunner()


def _extract_one(row: dict[str, Any]) -> tuple[int, list[str] | str]:
    try:
        trace = _W["runner"].request({"op": "trace", "id": row["battle_id"], "inputLog": row["input_log"], "ots": row["ots"]})
        if trace.get("winner") != row.get("winner") or trace.get("turns") != row.get("turns"):
            return row["index"], f"re-simulation diverged: {trace.get('winner')}/{trace.get('turns')} vs {row.get('winner')}/{row.get('turns')}"
        return row["index"], [dumps(r) for r in trace_snapshots(trace, row, _W["reg"])]
    except Exception as e:  # keep going; report
        return row["index"], f"{type(e).__name__}: {e}"


def extract_selfplay(run_dir: Path, reg: Regulation, workers: int = 4, out_dir: Path | None = None) -> dict[str, Any]:
    from vgc.sim.selfplay import load_battles

    rules = load_rules(reg)
    rows = [r for r in load_battles(run_dir) if "error" not in r]
    if rows and "input_log" not in rows[0]:
        raise ValueError(f"{run_dir} was run without logs (keep_logs=False); nothing to re-simulate")
    out_dir = out_dir or SNAPSHOTS / reg.id / "selfplay" / run_dir.name
    t0 = time.perf_counter()
    by_index: dict[int, list[str] | str] = {}
    with mp.get_context("spawn").Pool(workers, initializer=_init, initargs=(reg.id,)) as pool:
        for i, res in pool.imap_unordered(_extract_one, rows, chunksize=8):
            by_index[i] = res
    return _write(out_dir, rules, [(r, by_index[r["index"]]) for r in sorted(rows, key=lambda r: r["index"])],
                  lambda r: rules.split_of("selfplay", r["battle_id"], (r["p1"].get("team"), r["p2"].get("team"))),
                  source=str(run_dir), seconds=time.perf_counter() - t0)


def _write(out_dir: Path, rules: SplitRules, items: list[tuple[dict, list[str] | str]], split_fn, source: str,
           seconds: float) -> dict[str, Any]:
    shards: dict[str, list[str]] = defaultdict(list)
    battles: dict[str, int] = defaultdict(int)
    errors = []
    for row, res in items:
        if isinstance(res, str):
            errors.append(f"{row.get('battle_id') or row.get('id')}: {res}")
            continue
        split = split_fn(row)
        shards[split].extend(res)
        battles[split] += 1
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.jsonl.gz"):
        old.unlink()
    counts = {s: write_shard(out_dir / f"{s}.jsonl.gz", shards[s]) for s in SPLIT_NAMES if shards.get(s)}
    return {"source": source, "out_dir": str(out_dir), "battles": dict(battles), "records": counts,
            "errors": len(errors), "error_examples": errors[:5], "seconds": round(seconds, 1)}


# --- human replays ------------------------------------------------------------------------

def human_team_ids(replay: dict, reg: Regulation) -> dict[str, str]:
    from vgc.meta.replays import team_id, team_key, teams_from_replay

    return {side: team_id(team_key(team)) for side, team in teams_from_replay(replay, reg)}


def extract_human(fmt: str, reg: Regulation, out_dir: Path | None = None) -> dict[str, Any]:
    from vgc.meta import replays

    rules = load_rules(reg)
    out_dir = out_dir or SNAPSHOTS / reg.id / "human" / fmt
    t0 = time.perf_counter()
    items = []
    for rep in replays.cached(fmt):
        try:
            ids = human_team_ids(rep, reg)
            items.append((rep | {"_teams": ids}, [dumps(r) for r in human_snapshots(rep, reg, ids)]))
        except Exception as e:
            items.append((rep, f"{type(e).__name__}: {e}"))
    return _write(out_dir, rules, items,
                  lambda r: rules.split_of("human", r["id"], r["_teams"].values(), replay_group(r)),
                  source=fmt, seconds=time.perf_counter() - t0)


def snapshot_files(reg: Regulation, split: str = "train") -> list[Path]:
    return sorted((SNAPSHOTS / reg.id).glob(f"*/*/{split}.jsonl.gz"))
