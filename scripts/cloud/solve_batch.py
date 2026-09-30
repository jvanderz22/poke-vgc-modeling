#!/usr/bin/env python3
"""Solve a batch of endgame positions with a pool of workers, anywhere: on Kaggle, a rented box, or
this laptop. Standard library only.

    python3 solve_batch.py --jobs jobs.jsonl --out results.jsonl --solver endgame-solver.js \\
        --showdown vendor/pokemon-showdown [--node node] [--workers N] [--cap 180] [--max-minutes 690]
    python3 solve_batch.py              # on Kaggle: everything is found under /kaggle/input

Reads `{key, position}` lines (`vgc.wp.offload.export`) and writes one line per position to --out,
as each finishes:
  {key, result}          solved
  {key, timeout: cap}    ran past --cap seconds (0 means no cap)
  {key, error}           the solver failed on it
A position cut off by the deadline gets no line, so a rerun with the same --out picks it up again,
and so does the next export. `summary.json` beside --out records the sha256 of the solver that
ran, which `vgc wp merge-solves` insists matches its own before it caches anything.

The deadline is the point: Kaggle ends a session at 12 hours and keeps its output only if the
session ends normally, so the run has to stop itself first, with everything it finished written.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import platform
import queue
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
import time

KAGGLE_INPUT = "/kaggle/input"
KAGGLE_OUT = "/kaggle/working"
# Seconds kept back from --max-minutes to kill what is still running and write the summary.
GRACE = 120


def find(pattern: str) -> str | None:
    hits = sorted(glob.glob(os.path.join(KAGGLE_INPUT, "**", pattern), recursive=True))
    return hits[0] if hits else None


def unpack(archive: str, into: str) -> str:
    os.makedirs(into, exist_ok=True)
    with tarfile.open(archive) as t:
        t.extractall(into, filter="data") if sys.version_info >= (3, 12) else t.extractall(into)
    return into


def kaggle_setup() -> dict:
    """Locate this run's inputs under /kaggle/input. A dataset's archives may arrive unpacked or
    not, depending on how Kaggle processed them, so both are handled. Nothing is fetched: the
    kernel runs without internet, and the Node binary comes in the engine dataset."""
    work = "/kaggle/tmp" if os.path.isdir("/kaggle/tmp") else "/tmp/solve"
    run = json.load(open(find("run.json")))

    node = next((p for p in glob.glob(os.path.join(KAGGLE_INPUT, "**", "bin", "node"), recursive=True)), None)
    if node is None:
        node = os.path.join(unpack(find("node-*-linux-x64.tar.xz"), os.path.join(work, "node")),
                            os.listdir(os.path.join(work, "node"))[0], "bin", "node")
    else:  # an unpacked copy on a read-only mount may have lost its executable bit
        dst = os.path.join(work, "node-bin")
        os.makedirs(work, exist_ok=True)
        shutil.copy(node, dst)
        node = dst
    os.chmod(node, os.stat(node).st_mode | stat.S_IXUSR)

    sim = find(os.path.join("dist", "sim", "index.js"))
    showdown = (os.path.dirname(os.path.dirname(os.path.dirname(sim))) if sim
                else unpack(find("showdown.tar.gz"), os.path.join(work, "showdown")))
    return {"jobs": find("jobs.jsonl"), "solver": find("endgame-solver.js"), "showdown": showdown,
            "node": node, "out": os.path.join(KAGGLE_OUT, "results.jsonl"), "where": "kaggle",
            "cap": run.get("cap", 180), "max_minutes": run.get("max_minutes", 690),
            "workers": run.get("workers") or os.cpu_count(), "run": run}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs")
    ap.add_argument("--out")
    ap.add_argument("--solver")
    ap.add_argument("--showdown")
    ap.add_argument("--node", default="node")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--cap", type=float, default=180, help="seconds a position may take; 0 for none")
    ap.add_argument("--max-minutes", type=float, default=690, help="stop, with everything written, by then")
    ap.add_argument("--where", default=platform.node())
    args = ap.parse_args()

    if args.jobs is None and os.path.isdir(KAGGLE_INPUT):
        cfg = kaggle_setup()
    else:
        cfg = {k: getattr(args, k) for k in ("jobs", "out", "solver", "showdown", "node", "workers",
                                               "cap", "max_minutes", "where")} | {"run": {}}
        missing = [k for k in ("jobs", "out", "solver", "showdown") if not cfg[k]]
        if missing:
            ap.error("missing " + ", ".join("--" + k for k in missing))
    t0 = time.time()
    deadline = t0 + cfg["max_minutes"] * 60 - GRACE
    sha = hashlib.sha256(open(cfg["solver"], "rb").read()).hexdigest()
    node_version = subprocess.run([cfg["node"], "--version"], capture_output=True, text=True).stdout.strip()

    jobs = [json.loads(x) for x in open(cfg["jobs"]) if x.strip()]
    done = set()
    if os.path.exists(cfg["out"]):
        done = {json.loads(x)["key"] for x in open(cfg["out"]) if x.strip()}
    todo: queue.Queue = queue.Queue()
    for j in jobs:
        if j["key"] not in done:
            todo.put(j)
    print(f"{len(jobs)} jobs, {len(done)} already answered, {todo.qsize()} to solve; "
          f"{cfg['workers']} workers, cap {cfg['cap'] or 'none'}, stop by {cfg['max_minutes']:.0f} min; "
          f"node {node_version}, solver {sha[:12]}", flush=True)

    lock = threading.Lock()
    counts = {"solved": 0, "timeout": 0, "error": 0, "cut_by_deadline": 0}
    out = open(cfg["out"], "a")

    def write(row: dict, kind: str) -> None:
        with lock:
            out.write(json.dumps(row) + "\n")
            out.flush()
            counts[kind] += 1
            n = sum(counts.values()) - counts["cut_by_deadline"]
            if n % 25 == 0:
                print(f"  {n} answered, {time.time() - t0:.0f}s: {counts}", flush=True)

    def worker() -> None:
        while time.time() < deadline:
            try:
                job = todo.get_nowait()
            except queue.Empty:
                return
            left = deadline - time.time()
            limit = min(cfg["cap"], left) if cfg["cap"] else left
            p = subprocess.Popen([cfg["node"], cfg["solver"], cfg["showdown"]], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                so, se = p.communicate(json.dumps(job["position"]), timeout=max(limit, 1))
            except subprocess.TimeoutExpired:
                p.kill()
                p.communicate()
                if cfg["cap"] and limit >= cfg["cap"]:
                    write({"key": job["key"], "timeout": cfg["cap"]}, "timeout")
                else:
                    with lock:
                        counts["cut_by_deadline"] += 1
                continue
            if p.returncode:
                write({"key": job["key"], "error": se.strip()[-1500:]}, "error")
            else:
                write({"key": job["key"], "result": json.loads(so)}, "solved")

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(cfg["workers"])]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    out.close()
    counts["not_started"] = todo.qsize()
    summary = {"solver_sha256": sha, "node": node_version, "where": cfg["where"], "workers": cfg["workers"],
               "cap": cfg["cap"], "max_minutes": cfg["max_minutes"], "elapsed_s": round(time.time() - t0),
               "jobs": len(jobs), "answered_before": len(done), **counts, "run": cfg["run"]}
    with open(os.path.join(os.path.dirname(os.path.abspath(cfg["out"])), "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1)
    print(json.dumps(summary), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
