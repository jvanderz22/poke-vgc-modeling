"""The policy's gates and the step 3 pilot (PLAN-policy, stage 5).

Teams are human-corpus pairings, open sheets. Each pairing is played with the policies swapped
between the teams, sides alternating, so team strength cancels, and the unit is the pairing.

    # a run: export it, then play it here or on Kaggle
    policy_gate.py export --name G1 --policy ewp-people --opponent heuristic --pairs 250
    policy_gate.py local  --name G1 [--workers 4]
    policy_gate.py pack   --name G1                      # for scripts/cloud/kaggle_selfplay.sh
    policy_gate.py merge  --name G1 OUT/battles.jsonl... # checks a sample replays here first
    # the verdicts
    policy_gate.py score  --name G1 [--name G2 ...]

The gates (judged, per π_opp reading):
  heuristic  the 95% interval of the pairing win rate above 0.5, and the point at least 0.60
  scorer     the held-out opponent (`vgc.policy.ewp.ScorerPolicy`): the interval above 0.5
  random     the point at least 0.95
  latency    a decision's median at most 1 s on one core, p99 under 45 s
Reported with them: the chosen choice's EWP binned against what happened (calibration), battles a
second, turns a battle, fallbacks.

The pilot (`--opponent self --per-pairing 16`): the policy against itself, 100 pairings. Its
split-half reliability and the spread `s` of the pairings' win rates size step 3.
"""

from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import math
import random
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

import numpy as np

from vgc import paths

JOBS = paths.ROOT / ".vgc" / "jobs"
OUT = paths.DATA / "analysis" / "reg_mc"
REG = "reg_mc"
# What a battle reads besides the `vgc` package (traced on an EWP and a scorer battle): packed with
# the code for a run elsewhere, and hashed with it, so a merge knows the code that played matches.
FILES = ["sidecar/showdown/battle-runner.js", "sidecar/showdown/endgame-solver.js", "sidecar/calc/index.js",
         "sidecar/calc/package.json", "configs/regulations/reg_mc.yaml", "data/regulations/reg_mc/dex.json",
         "data/teams/reg_mc/usage_2026-09-20.json", "data/teams/reg_mc/usage_2026-09-20_p50.json",
         "data/analysis/reg_mc/bring_rates.json"]
PINS = ("poke-env", "orjson", "websockets", "numpy", "scipy", "tabulate")
PYTHONS = ("3.11", "3.12", "3.13")


# --- a run -------------------------------------------------------------------------------------

def run_dir(name: str) -> Path:
    return JOBS / f"selfplay-{name}"


def export(name: str, policy: str, opponent: str, pairs: int, seed: int, per_pairing: int,
           ewp: dict[str, Any], part: str = "0/1") -> Path:
    """The run's battles, `per_pairing` a pairing, consecutive: half with the policy on each team,
    sides alternating. `part` i/n keeps every n-th of the sampled pairings from the i-th, so a run
    split over kernels has disjoint pairings (the battle seed is the run's seed and its index)."""
    from vgc.meta.pool import load_pool
    from vgc.regulation import load_regulation
    from vgc.sim.validity import human_games, select_pairings

    reg = load_regulation(REG)
    pool = {t.id: t for t in load_pool(reg)}
    chosen = [p for p in select_pairings(human_games(reg), 0) if p[0] in pool and p[1] in pool]
    chosen = sorted(random.Random(seed).sample(chosen, pairs))
    i, n = map(int, part.split("/"))
    chosen = chosen[i::n]
    pairs = len(chosen)
    other = policy if opponent == "self" else opponent
    rows = []
    for i, (a, b) in enumerate(chosen):
        for k in range(per_pairing):
            ta, tb = (a, b) if k % 2 == 0 else (b, a)        # the policy on each team in turn
            rows.append({"index": len(rows), "pairing": i, "team_a": pool[ta].text, "team_b": pool[tb].text,
                         "team_a_id": ta, "team_b_id": tb, "policy_a": policy, "policy_b": other,
                         "swap_sides": bool((k // 2 + i) % 2), "ots": True})
    d = run_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "matchups.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    run = {"run_id": f"gate-{name}", "name": name, "reg": REG, "seed": seed + 1000 * i, "part": part,
           "policy": policy, "opponent": opponent,
           "pairs": pairs, "per_pairing": per_pairing, "ewp": ewp, "max_minutes": 690,
           "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()}
    (d / "run.json").write_text(json.dumps(run, indent=1) + "\n")
    print(f"{len(rows)} battles: {pairs} pairings x {per_pairing}, {policy} against {opponent} -> {d}")
    return d


def _load(name: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    d = run_dir(name)
    return json.loads((d / "run.json").read_text()), [json.loads(x) for x in open(d / "matchups.jsonl")]


def _matchup(r: dict[str, Any]):
    from vgc.sim.selfplay import Matchup

    return Matchup(r["team_a"], r["team_b"], r["policy_a"], r["policy_b"], r["team_a_id"], r["team_b_id"],
                   swap_sides=r["swap_sides"], ots=r["ots"], preview_a=r.get("preview_a"), preview_b=r.get("preview_b"))


def local(name: str, workers: int) -> None:
    from vgc.sim.selfplay import run as play

    run, rows = _load(name)
    summary = play([_matchup(r) for r in rows], reg_id=run["reg"], seed=run["seed"], workers=workers,
                   run_id=run["run_id"], ewp=run["ewp"])
    print(json.dumps({k: v for k, v in summary.items() if k != "error_examples"}, indent=1))


def code_sha() -> str:
    h = hashlib.sha256()
    for p in sorted((paths.ROOT / "src" / "vgc").rglob("*.py")) + [paths.ROOT / f for f in FILES]:
        h.update(str(p.relative_to(paths.ROOT)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def wheels() -> str:
    """The wheels a Kaggle image lacks, at the laptop's versions, for each Python Kaggle may run: one
    shared dataset (`.vgc/jobs/selfplay-wheels/upload`), rebuilt and pushed only when a version moves.
    Returns its stamp."""
    import importlib.metadata as md

    versions = {p: md.version(p) for p in PINS}
    stamp = "wheels-" + hashlib.sha256(json.dumps(versions, sort_keys=True).encode()).hexdigest()[:12]
    d = JOBS / "selfplay-wheels"
    up = d / "upload"
    if (up / f"{stamp}.json").exists():
        return stamp
    import shutil

    shutil.rmtree(d, ignore_errors=True)
    (d / "wheels").mkdir(parents=True)
    up.mkdir()
    for py in PYTHONS:
        cmd = [sys.executable, "-m", "pip", "download", "-q", "--no-deps", "--only-binary=:all:", "-d", str(d / "wheels"),
               "--python-version", py, "--implementation", "cp", "--platform", "manylinux2014_x86_64",
               "--platform", "manylinux_2_17_x86_64", "--platform", "manylinux_2_28_x86_64"]
        for p, v in versions.items():
            if subprocess.run(cmd + [f"{p}=={v}"], capture_output=True, text=True).returncode:
                print(f"  no {p}=={v} wheel for Python {py}: there it runs on the image's own")
    # A dataset's subdirectories are not uploaded, so the wheels go as one archive.
    with tarfile.open(up / "wheels.tar.gz", "w:gz") as t:
        for w in sorted((d / "wheels").iterdir()):
            t.add(w, w.name)
    (up / f"{stamp}.json").write_text(json.dumps(versions) + "\n")
    return stamp


def pack(name: str) -> Path:
    """The run as `kaggle_selfplay.sh` pushes it: the code, the matchups and the run (the wheels are
    their own dataset, `wheels`)."""
    import importlib.metadata as md
    import shutil

    run, _ = _load(name)
    d = run_dir(name)
    up = d / "upload"
    shutil.rmtree(up, ignore_errors=True)
    up.mkdir()
    with tarfile.open(up / "code.tar.gz", "w:gz") as t:
        t.add(paths.ROOT / "src" / "vgc", "src/vgc", filter=lambda ti: None if "__pycache__" in ti.name else ti)
        for f in FILES:
            t.add(paths.ROOT / f, f)
        t.add(paths.ROOT / "sidecar" / "calc" / "node_modules", "sidecar/calc/node_modules")
    # The commit the code is, so a merge after later edits can check it out (`git worktree`).
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "src/vgc", *FILES], capture_output=True, text=True).stdout
    if dirty.strip():
        print("  the code has uncommitted edits: a merge after later edits cannot check this code out")
    run = {**run, "versions": {p: md.version(p) for p in PINS}, "wheels": wheels(), "code_sha256": code_sha(),
           "packed_commit": None if dirty.strip() else head}
    (d / "run.json").write_text(json.dumps(run, indent=1) + "\n")
    (up / "run.json").write_text(json.dumps(run, indent=1) + "\n")
    # The file a push is awaited by: first in Kaggle's listing, which is by path and paged, and which
    # lists the code archive unpacked.
    (up / f"0-{run['run_id']}-{int(time.time())}.json").write_text(json.dumps(run) + "\n")
    shutil.copy(d / "matchups.jsonl", up / "matchups.jsonl")
    print(f"packed {up}: code {run['code_sha256'][:12]}, wheels {run['wheels']}")
    return up


def merge(name: str, outputs: list[Path], check: int = 3) -> Path:
    """Battles played elsewhere, into `data/selfplay/<run_id>/` as `selfplay.run` writes them, but
    only if the code that played them is this code and the `check` shortest replay here to the same
    inputs, choice by choice."""
    from vgc.sim import selfplay

    run, rows = _load(name)
    got: dict[int, dict[str, Any]] = {}
    for out in outputs:
        summ = json.loads((out.parent / "summary.json").read_text())
        if summ["run"].get("code_sha256") != code_sha():
            raise SystemExit(f"{out}: played by other code ({summ['run'].get('code_sha256', '?')[:12]})")
        print(f"{out}: {summ['where']}, {summ['elapsed_s']}s, versions {summ['versions']}, {summ['installed']}")
        for x in open(out):
            if x.strip():
                r = json.loads(x)
                got[r["index"]] = r
    ok = sorted((r for r in got.values() if "error" not in r), key=lambda r: r["turns"])[:check]
    selfplay._init_worker(run["reg"], run["ewp"])
    from vgc.engine.runner import battle_seed

    for r in ok:
        m = rows[r["index"]]
        again = selfplay._play((r["index"], run["run_id"], battle_seed(run["seed"], r["index"]), _matchup(m)))
        if again.get("input_log") != r["input_log"]:
            raise SystemExit(f"battle {r['index']} does not replay here to the same inputs: nothing merged")
        print(f"  battle {r['index']} replays the same ({r['turns']} turns)")
    for pol in selfplay._W["policies"].values():
        getattr(pol, "close", lambda: None)()
    results = [got[i] for i in sorted(got)]
    d = selfplay.SELFPLAY / run["run_id"]
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / "battles.jsonl.gz", "wt") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    missing = sorted(set(range(len(rows))) - set(got))
    (d / "summary.json").write_text(json.dumps({"run_id": run["run_id"], "where": "kaggle", "merged_from": [str(o) for o in outputs],
                                                "battles": len(results), "missing": missing}, indent=1) + "\n")
    print(f"merged {len(results)} battles into {d}; {len(missing)} missing")
    return d


# --- the verdicts --------------------------------------------------------------------------------

def _battles(run: dict[str, Any]) -> list[dict[str, Any]]:
    from vgc.sim.selfplay import SELFPLAY, load_battles

    return load_battles(SELFPLAY / run["run_id"])


def _boot(per: np.ndarray, reps: int = 4000, seed: int = 11) -> list[float]:
    rng = np.random.default_rng(seed)
    b = per[rng.integers(0, len(per), (reps, len(per)))].mean(1)
    return [round(float(x), 4) for x in np.percentile(b, [2.5, 97.5])]


def calibration(decisions: list[tuple[float, float, int]]) -> dict[str, Any]:
    """(the chosen choice's EWP, the result, pairing): binned, each bin's interval by pairing."""
    if not decisions:
        return {}
    p = np.array([d[0] for d in decisions])
    y = np.array([d[1] for d in decisions])
    g = np.array([d[2] for d in decisions])
    bins = []
    edges = np.linspace(0, 1, 11)
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (p >= lo) & ((p < hi) | (hi == 1))
        if not sel.any():
            continue
        groups = sorted(set(g[sel]))
        per = np.array([y[sel & (g == k)].mean() for k in groups])
        bins.append({"bin": [round(lo, 1), round(hi, 1)], "n": int(sel.sum()), "pairings": len(groups),
                     "ewp": round(float(p[sel].mean()), 3), "won": round(float(y[sel].mean()), 3),
                     "won_ci95": _boot(per) if len(groups) > 1 else None})
    ece = sum(b["n"] * abs(b["ewp"] - b["won"]) for b in bins) / len(p)
    return {"decisions": len(p), "brier": round(float(((p - y) ** 2).mean()), 4), "ece": round(ece, 4), "bins": bins}


def score(names: list[str]) -> dict[str, Any]:
    """One run's verdicts, or several runs' pooled (a run split over kernels: same policy, opponent
    and battles a pairing, different pairings)."""
    loaded = [_load(n) for n in names]
    run = loaded[0][0]
    assert all((r["policy"], r["opponent"], r["per_pairing"], r["ewp"]) ==
               (run["policy"], run["opponent"], run["per_pairing"], run["ewp"]) for r, _ in loaded), "not one run"
    policy, opponent = run["policy"], run["opponent"]
    by_pairing: dict[int, list[float]] = collections.defaultdict(list)
    turns, ms, fallbacks, calib = [], [], collections.Counter(), []
    team_a: dict[int, list[float]] = collections.defaultdict(list)     # the pilot: the first team's results
    errors = 0
    everything = [(k, r, rows, battle) for k, (r, rows) in enumerate(loaded) for battle in _battles(r)]
    battles = [b for *_, b in everything]
    for k, rr, rows, r in everything:
        if "error" in r:
            errors += 1
            continue
        m = {**rows[r["index"]], "pairing": k * 100_000 + rows[r["index"]]["pairing"]}
        a_side = r["a_side"]
        ewp_side = a_side if m["policy_a"] == policy else ("p2" if a_side == "p1" else "p1")
        res = 0.5 if r["winner"] is None else float(r["winner"] == ewp_side)
        by_pairing[m["pairing"]].append(res)
        first = rows[rows[r["index"]]["pairing"] * run["per_pairing"]]["team_a_id"]
        a_team_side = a_side if m["team_a_id"] == first else ("p2" if a_side == "p1" else "p1")
        team_a[m["pairing"]].append(0.5 if r["winner"] is None else float(r["winner"] == a_team_side))
        turns.append(r["turns"])
        for d in r.get("decisions", []):
            # Only this battle's: a battle that errored left its records to the next one (fixed in
            # `selfplay._play`, after the gates were played).
            if d.get("battle") != r["battle_id"]:
                continue
            if opponent != "self" and d["side"] != ewp_side:
                continue
            ms.append(d["ms"] + d.get("ms_fallback", 0))
            if d.get("fallback"):
                fallbacks[d["fallback"].split(":")[0] if d["fallback"].startswith("error") else d["fallback"]] += 1
            elif d.get("ewp") is not None:
                won = 0.5 if r["winner"] is None else float(r["winner"] == d["side"])
                calib.append((d["ewp"][d["chosen"]], won, m["pairing"]))
    per = np.array([np.mean(v) for _, v in sorted(by_pairing.items())])
    out: dict[str, Any] = {"run": [r["run_id"] for r, _ in loaded], "policy": policy, "opponent": opponent, "settings": run["ewp"],
                           "battles": len(battles), "errors": errors, "pairings": len(per),
                           "complete_pairings": sum(len(v) == run["per_pairing"] for v in by_pairing.values()),
                           "mean_turns": round(float(np.mean(turns)), 2) if turns else None}
    if opponent != "self":
        point, ci = round(float(per.mean()), 4), _boot(per)
        rule = {"heuristic": ci[0] > 0.5 and point >= 0.60, "scorer": ci[0] > 0.5, "random": point >= 0.95}[opponent]
        out["win_rate"] = {"point": point, "ci95": ci, "passes": bool(rule)}
    else:
        a = [np.mean(v) for _, v in sorted(team_a.items()) if len(v) == run["per_pairing"]]
        h1 = [np.mean(v[0::2]) for _, v in sorted(team_a.items()) if len(v) == run["per_pairing"]]
        h2 = [np.mean(v[1::2]) for _, v in sorted(team_a.items()) if len(v) == run["per_pairing"]]
        r_half = float(np.corrcoef(h1, h2)[0, 1]) if len(a) > 2 else None
        n = run["per_pairing"]
        noise = float(np.mean([x * (1 - x) / n for x in a])) if a else 0.0
        out["pilot"] = {"pairings": len(a), "split_half_r": round(r_half, 3) if r_half is not None else None,
                        "spearman_brown": round(2 * r_half / (1 + r_half), 3) if r_half is not None else None,
                        "spread_s": round(math.sqrt(max(0.0, float(np.var(a)) - noise)), 3) if a else None,
                        "beyond_85_15": round(float(np.mean([x >= 0.85 or x <= 0.15 for x in a])), 3) if a else None}
    from vgc.sim.selfplay import SELFPLAY

    wheres = {json.loads((SELFPLAY / r["run_id"] / "summary.json").read_text()).get("where", "laptop") for r, _ in loaded}
    where = wheres.pop() if len(wheres) == 1 else "mixed"
    # Judged on the laptop's core only: a Kaggle CPU is about twice as slow (cloud-compute.md, C).
    out["latency_ms"] = {"where": where, "decisions": len(ms), "median": round(float(np.median(ms))) if ms else None,
                         "p99": round(float(np.percentile(ms, 99))) if ms else None, "max": max(ms) if ms else None,
                         "passes": (bool(ms) and float(np.median(ms)) <= 1000 and float(np.percentile(ms, 99)) < 45000)
                         if where == "laptop" else None}
    out["fallbacks"] = dict(fallbacks.most_common())
    out["calibration"] = calibration(calib)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--name", required=True)
    e.add_argument("--policy", default="ewp-people")
    e.add_argument("--opponent", default="heuristic", choices=["heuristic", "scorer", "random", "self"])
    e.add_argument("--pairs", type=int, default=250)
    e.add_argument("--per-pairing", type=int, default=2)
    e.add_argument("--seed", type=int, default=5)
    e.add_argument("--ewp", default="{}", help="EWPPolicy settings, as JSON")
    e.add_argument("--part", default="0/1", help="i/n: the i-th of n disjoint shares of the pairings")
    lo = sub.add_parser("local")
    lo.add_argument("--name", required=True)
    lo.add_argument("--workers", type=int, default=4)
    pk = sub.add_parser("pack")
    pk.add_argument("--name", required=True)
    mg = sub.add_parser("merge")
    mg.add_argument("--name", required=True)
    mg.add_argument("outputs", nargs="+", type=Path)
    sc = sub.add_parser("score")
    sc.add_argument("--name", action="append", required=True)
    sc.add_argument("--pool", help="score the named runs as one, under this name")
    a = ap.parse_args()
    if a.cmd == "export":
        export(a.name, a.policy, a.opponent, a.pairs, a.seed, a.per_pairing, json.loads(a.ewp), a.part)
    elif a.cmd == "local":
        local(a.name, a.workers)
    elif a.cmd == "pack":
        pack(a.name)
    elif a.cmd == "merge":
        merge(a.name, a.outputs)
    else:
        res = {a.pool: score(a.name)} if a.pool else {n: score([n]) for n in a.name}
        (OUT / "policy_gate.json").write_text(json.dumps(
            {**(json.loads((OUT / "policy_gate.json").read_text()) if (OUT / "policy_gate.json").exists() else {}), **res},
            indent=1) + "\n")
        print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
