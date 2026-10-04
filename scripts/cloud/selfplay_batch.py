#!/usr/bin/env python3
"""Play a batch of self-play battles with a pool of workers, anywhere: on Kaggle, a rented box, or
this laptop (PLAN-policy stage 5).

    python3 selfplay_batch.py --root . --matchups matchups.jsonl --run run.json --out battles.jsonl
    python3 selfplay_batch.py              # on Kaggle: everything is found under /kaggle/input

`matchups.jsonl` has one battle a line, `{index, team_a, team_b, policy_a, policy_b, team_a_id,
team_b_id, swap_sides, ots}`; `run.json` has the run's `run_id`, `seed`, `reg`, the policy settings
(`ewp`) and `max_minutes`. Battle `index` is played from `battle_seed(seed, index)` exactly as
`vgc.sim.selfplay.run` plays it, so a run's battles do not depend on where or how it is split, and
`--out` gets one line a battle as each finishes (`vgc.sim.selfplay._play`'s row). A battle already
in `--out` is not played again, and one cut off by the deadline gets no line.

The deadline is the point, as in `solve_batch.py`: Kaggle keeps a session's output only if it ends
normally, so the run stops itself first with everything it finished written.
"""

from __future__ import annotations

import argparse
import glob
import json
import multiprocessing as mp
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import time

# Overridable only to try the Kaggle path on another machine.
KAGGLE_INPUT = os.environ.get("KAGGLE_INPUT", "/kaggle/input")
KAGGLE_OUT = os.environ.get("KAGGLE_OUT", "/kaggle/working")
# Seconds kept back from --max-minutes: a battle takes a while, so none starts this close to the end.
GRACE = 300
PINS = ("poke-env", "orjson", "websockets", "numpy", "scipy", "tabulate")


def find(pattern: str) -> str | None:
    hits = sorted(glob.glob(os.path.join(KAGGLE_INPUT, "**", pattern), recursive=True))
    return hits[0] if hits else None


def unpack(archive: str, into: str) -> str:
    os.makedirs(into, exist_ok=True)
    with tarfile.open(archive) as t:
        t.extractall(into, filter="data") if sys.version_info >= (3, 12) else t.extractall(into)
    return into


def kaggle_setup() -> dict:
    """The repository as the battles need it, built under /kaggle/tmp from the two datasets: the
    engine (Node and the Showdown slice, shared with `kaggle_solve.sh`) and this run's code (the
    `vgc` package, the sidecars, the few data files a battle reads, the wheels, the matchups)."""
    work = os.environ.get("KAGGLE_TMP") or ("/kaggle/tmp" if os.path.isdir("/kaggle/tmp") else "/tmp/selfplay")
    run = json.load(open(find("run.json")))

    node = next((p for p in glob.glob(os.path.join(KAGGLE_INPUT, "**", "bin", "node"), recursive=True)), None)
    if node is None:
        nd = unpack(find("node-*-linux-x64.tar.xz"), os.path.join(work, "node"))
        node = os.path.join(nd, os.listdir(nd)[0], "bin", "node")
    else:
        dst = os.path.join(work, "node-bin", "node")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy(node, dst)
        node = dst
    os.chmod(node, os.stat(node).st_mode | stat.S_IXUSR)
    os.environ["PATH"] = os.path.dirname(node) + os.pathsep + os.environ["PATH"]

    # The archives arrive as uploaded or unpacked by Kaggle, so both are handled.
    root = os.path.join(work, "root")
    unpacked = find(os.path.join("src", "vgc", "paths.py"))
    if unpacked:
        shutil.copytree(os.path.dirname(os.path.dirname(os.path.dirname(unpacked))), root, dirs_exist_ok=True)
    else:
        unpack(find("code.tar.gz"), root)
    sim = find(os.path.join("dist", "sim", "index.js"))
    showdown = os.path.join(root, "vendor", "pokemon-showdown")
    if sim:
        shutil.copytree(os.path.dirname(os.path.dirname(os.path.dirname(sim))), showdown, dirs_exist_ok=True)
    else:
        unpack(find("showdown.tar.gz"), showdown)

    wheel = find("*.whl")
    wheels = os.path.dirname(wheel) if wheel else unpack(find("wheels.tar.gz"), os.path.join(work, "wheels"))
    import importlib.metadata as md

    installed = {}
    for pkg in PINS:
        try:
            if md.version(pkg) == run["versions"][pkg]:
                installed[pkg] = "present"
                continue
        except md.PackageNotFoundError:
            pass
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "-q",
                            "--find-links", wheels, f"{pkg}=={run['versions'][pkg]}"], capture_output=True, text=True)
        installed[pkg] = "pinned" if r.returncode == 0 else f"not installed: {r.stderr.strip()[-200:]}"
    return {"root": root, "matchups": find("matchups.jsonl"), "run": run,
            "out": os.path.join(KAGGLE_OUT, "battles.jsonl"), "where": "kaggle", "installed": installed,
            "workers": run.get("workers") or os.cpu_count()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root")
    ap.add_argument("--matchups")
    ap.add_argument("--run")
    ap.add_argument("--out")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--where", default=platform.node())
    args = ap.parse_args()

    if args.matchups is None and os.path.isdir(KAGGLE_INPUT):
        cfg = kaggle_setup()
    else:
        cfg = {"root": args.root, "matchups": args.matchups, "run": json.load(open(args.run)), "out": args.out,
               "where": args.where, "installed": {}, "workers": args.workers}
    os.environ["VGC_ROOT"] = os.path.abspath(cfg["root"])
    sys.path.insert(0, os.path.join(os.path.abspath(cfg["root"]), "src"))
    from vgc.engine.runner import battle_seed
    from vgc.sim import selfplay

    run = cfg["run"]
    t0 = time.time()
    deadline = t0 + run.get("max_minutes", 690) * 60 - GRACE
    rows = [json.loads(x) for x in open(cfg["matchups"]) if x.strip()]
    done = set()
    if os.path.exists(cfg["out"]):
        done = {json.loads(x)["index"] for x in open(cfg["out"]) if x.strip()}
    todo = [r for r in rows if r["index"] not in done]
    import importlib.metadata as md
    versions = {p: md.version(p) for p in PINS}
    node_version = subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip()
    print(f"{len(rows)} battles, {len(done)} already played, {len(todo)} to play; {cfg['workers']} workers, "
          f"stop by {run.get('max_minutes', 690)} min; node {node_version}; {versions}", flush=True)

    def task(r: dict) -> tuple:
        m = selfplay.Matchup(r["team_a"], r["team_b"], r["policy_a"], r["policy_b"], r.get("team_a_id", ""),
                             r.get("team_b_id", ""), swap_sides=r.get("swap_sides", False), ots=r.get("ots", True),
                             preview_a=r.get("preview_a"), preview_b=r.get("preview_b"))
        return (r["index"], run["run_id"], battle_seed(run["seed"], r["index"]), m)

    counts = {"played": 0, "error": 0}
    out = open(cfg["out"], "a")
    ctx = mp.get_context("spawn")
    with ctx.Pool(cfg["workers"], initializer=selfplay._init_worker, initargs=(run["reg"], run.get("ewp"))) as pool:
        pending, it = [], iter(todo)
        while True:
            # Never more in flight than the workers can start: what is not started by the deadline
            # is left for the next run.
            while len(pending) < cfg["workers"] and time.time() < deadline:
                r = next(it, None)
                if r is None:
                    break
                pending.append(pool.apply_async(selfplay._play, (task(r),)))
            if not pending:
                break
            time.sleep(0.5)
            for p in [p for p in pending if p.ready()]:
                pending.remove(p)
                row = p.get()
                out.write(json.dumps(row) + "\n")
                out.flush()
                counts["error" if "error" in row else "played"] += 1
                n = counts["played"] + counts["error"]
                if n % 25 == 0:
                    print(f"  {n} battles, {time.time() - t0:.0f}s: {counts}", flush=True)
    out.close()
    summary = {"run": run, "where": cfg["where"], "workers": cfg["workers"], "node": node_version, "versions": versions,
               "installed": cfg["installed"], "elapsed_s": round(time.time() - t0), "battles": len(rows),
               "played_before": len(done), **counts, "not_started": len(todo) - counts["played"] - counts["error"]}
    with open(os.path.join(os.path.dirname(os.path.abspath(cfg["out"])), "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1)
    print(json.dumps({k: v for k, v in summary.items() if k != "run"}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
