"""The doubles answer as the Battle page gets it: held-out open-sheet games through
`vgc.web.solving.request` with warm solver processes and nothing cached, timed and scored.

What the check in `solver_vs_humans.py` cannot see: the 5-second deadline (a move order whose
search is late keeps its quick value) and the temperature applied to whatever the page shows. So
this records, for every `--every`-th held-out game: when the first answer and the final answer
landed, how many move orders were searched in time, and the final value; and scores that value
against the same games' no-deadline answer (`--tag`'s saved rows, tempered) and the model.

    .venv/bin/python scripts/analysis/page_path.py --every 6
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

import numpy as np

from vgc import paths
from vgc.regulation import load_regulation
from vgc.wp import doubles, solver

OUT = paths.DATA / "analysis" / "reg_mc" / "page_path_doubles.json"


def games(reg, every: int) -> list[tuple[dict[str, Any], Any]]:
    from vgc.data.splits import load_rules
    from vgc.meta import pool, replays
    from vgc.web.endgames import _eligible

    rules = load_rules(reg)
    out, n = [], 0
    for fmt in pool.formats_for(reg):
        for replay in replays.cached(fmt):
            records = _eligible(reg, replay, rules, ots=True)
            if records is None or records[0]["label"]["winner"] not in ("p1", "p2"):
                continue
            battle = doubles.from_replay(reg, replay)
            if battle is None or doubles.reason(reg, battle.rp.state):
                continue
            if n % every == 0:
                out.append((replay, battle))
            n += 1
    return out


def ask(reg, gid: str, battle) -> dict[str, Any]:
    from vgc.web import solving

    t0 = time.time()
    first = None
    got = solving.request(reg, gid, battle)
    while got.get("eligible") and got.get("searching") is not None:
        if first is None and got.get("value") is not None:
            first = time.time() - t0
        time.sleep(0.05)
        got = solving.request(reg, gid, battle)
    total = time.time() - t0
    if first is None and got.get("value") is not None:
        first = total
    return {"eligible": got.get("eligible"), "first_s": round(first, 2) if first is not None else None,
            "final_s": round(total, 2), "value": got.get("value"), "depth": got.get("depth"),
            "searched": got.get("searched"), "orders": len(got.get("positions") or []), "error": got.get("error")}


def main() -> None:
    from vgc.web import solving

    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=6, help="every Nth eligible held-out game")
    ap.add_argument("--tag", default="live_blend_boosts", help="the saved no-deadline run to compare with")
    args = ap.parse_args()
    reg = load_regulation("reg_mc")
    # Nothing cached: the page's first sight of a position is a fresh solve.
    solver._cached = lambda: {}
    solver.remember = lambda *a, **k: None
    saved = {r["id"]: r for r in json.loads((paths.DATA / "analysis" / "reg_mc" /
                                             f"solver_vs_humans_doubles_{args.tag}.json").read_text())["rows"]}
    todo = games(reg, args.every)
    print(f"{len(todo)} games", flush=True)
    solving.warm()
    time.sleep(3)                       # the processes start with the app, not with the question
    rows = []
    try:
        for replay, battle in todo:
            r = ask(reg, replay["id"], battle)
            s = saved.get(replay["id"])
            if s is None or s.get("engine") is None or r["value"] is None:
                continue
            rows.append({"id": replay["id"], "group": s["group"], "kind": s["kind"], "winner": s["winner"],
                         "model": s["model"], "no_deadline": round(doubles.temper(s["engine"]), 4), **r})
            if len(rows) % 10 == 0:
                print(f"  {len(rows)} done", flush=True)
    finally:
        solving.cancel()
    y = np.array([r["winner"] == "p1" for r in rows], float)
    ll = lambda p: float(np.mean(-(y * np.log(np.clip(p, 1e-3, 1 - 1e-3)) + (1 - y) * np.log(np.clip(1 - p, 1e-3, 1 - 1e-3)))))
    col = lambda k: np.array([r[k] for r in rows], float)
    first, final = col("first_s"), col("final_s")
    result = {"games": len(rows),
              "first_s": {"median": round(float(np.median(first)), 2), "max": round(float(first.max()), 2)},
              "final_s": {"median": round(float(np.median(final)), 2), "p90": round(float(np.percentile(final, 90)), 2),
                          "max": round(float(final.max()), 2), "over_5s": int((final > solving.DEADLINE).sum())},
              "every_order_searched": int(sum(r["searched"] == r["orders"] for r in rows)),
              "page_vs_no_deadline": {"mean_abs": round(float(np.mean(np.abs(col("value") - col("no_deadline")))), 4),
                                      "max_abs": round(float(np.max(np.abs(col("value") - col("no_deadline")))), 4)},
              "logloss": {"page": round(ll(col("value")), 4), "no_deadline": round(ll(col("no_deadline")), 4),
                          "model": round(ll(col("model")), 4)},
              "errors": sum(bool(r["error"]) for r in rows)}
    OUT.write_text(json.dumps({"result": result, "rows": rows}, indent=1) + "\n")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
