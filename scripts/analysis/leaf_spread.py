"""Does the leaf tell the outcomes of different choices apart? (PLAN-policy, stage 3, principle 9.)

Positions from pool teams (4v4 to 3v2, as stage 1's cost table), each searched one turn deep at the
policy's settings twice: with the race at the horizon ('policy') and with the floor (HP share, the
count and the stages, no race). For each: the spread of the root matrix, the gap between p1's best
and second-best choice against p2's equilibrium reply, and whether the two horizons choose alike.

    .venv/bin/python scripts/analysis/leaf_spread.py --positions 30
"""

from __future__ import annotations

import argparse
import json
import random
import statistics as st
import subprocess

import numpy as np

from vgc import paths
from vgc.meta import pool
from vgc.policy.view import SEARCH
from vgc.regulation import load_regulation

OUT = paths.DATA / "analysis" / "reg_mc" / "leaf_spread.json"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--positions", type=int, default=30)
    a = ap.parse_args()
    reg = load_regulation("reg_mc")
    teams = pool.load_pool(reg)
    rng = random.Random(17)

    def four(t):
        mons = [m for m in t.text.strip().split("\n\n") if m.strip()]
        rng.shuffle(mons)
        return "\n\n".join(mons[:4])
    proc = subprocess.Popen(["node", str(paths.SIDECAR / "showdown" / "endgame-solver.js"), str(paths.SHOWDOWN), "--serve"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def solve(pos):
        proc.stdin.write(json.dumps({"id": 0, "position": pos}) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())["result"]
    rows = []
    for i in range(a.positions):
        x, y = rng.sample(teams, 2)
        left = [(4, 4), (4, 4), (4, 3), (3, 3), (3, 2)][i % 5]
        setup = {"hp": {sid: [rng.choice([100, 100, 80, 55]) for _ in range(n)] for sid, n in zip(("p1", "p2"), left)},
                 "fresh": {"p1": [i % 5 < 2] * 2, "p2": [i % 5 < 2] * 2}}
        base = {"format": reg.showdown_format, "p1": four(x), "p2": four(y), "active": {"p1": 2, "p2": 2},
                "bench": {"p1": left[0] - 2, "p2": left[1] - 2}, "setup": setup}
        row = {"kind": f"{left[0]}v{left[1]}"}
        for name in ("policy", "floor"):
            r = solve({**base, "search": {**SEARCH, "race_doubles": name}})
            M = np.array(r["matrix"])
            y2 = np.array(r["strategy"]["p2"])
            rv = np.sort(M @ y2)[::-1]
            row[name] = {"spread": float(M.std()), "gap": float(rv[0] - rv[1]) if len(rv) > 1 else 0.0,
                         "choice": r["moves"]["p1"][int(np.argmax(M @ y2))], "value": r["value"]}
        rows.append(row)
    out = {"positions": len(rows)}
    for name in ("policy", "floor"):
        out[name] = {"spread_median": round(st.median(r[name]["spread"] for r in rows), 4),
                     "gap_median": round(st.median(r[name]["gap"] for r in rows), 4)}
    out["same_choice"] = sum(r["policy"]["choice"] == r["floor"]["choice"] for r in rows)
    out["rows"] = rows
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
