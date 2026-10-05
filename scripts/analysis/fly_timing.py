"""The deployed app timed through the MCP server's own tools (`vgc.mcp.server`), as an agent calls
them: the reads, a battle's create/tap/read round trips, and `solve` at every kind the page leads
with an engine (1v1, 1v2, 2v2, and the model + policy above two a side).

The positions come from the fixture games, tapped from p1's seat with p1's four marked: for each
game, the first turn at which each solver applies, plus the benchmark's F2/B 1v1. Each battle is
deleted after it is timed.

    .venv/bin/python scripts/analysis/fly_timing.py
    .venv/bin/python scripts/analysis/fly_timing.py --rounds 3 --gap 10

The solver's cache lives on the volume, so a position the deployed app has already solved answers
from it in about 0.4 s, even after a redeploy. Only a round's first sight of a position is the
uncached time; `cached` in the output marks the rest (a `solve` that settles within 0.6 s).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from vgc import paths

OUT = paths.DATA / "analysis" / "reg_mc" / "fly_timing.json"
URL = "https://vgc-live-battle-calculator.fly.dev"
REPLAYS = ["gen9championsvgc2026regmcbo3-2682837890", "gen9championsvgc2026regmcbo3-2683090653"]
READS = ["health", "models", "pool", "list_teams", "list_battles", "endgames"]


def positions(reg) -> dict[str, dict[str, Any]]:
    """`{name: {my_team, their_team, entries}}`: what create_battle and add_entries are given."""
    from vgc.battle import from_log
    from vgc.battle.entry import Battle
    from vgc.web import solving
    from vgc.wp import benchmark, solver

    sys.path.insert(0, str(paths.ROOT / "tests"))
    import test_policy_entry as T

    out = {}
    for rid in REPLAYS:
        replay = json.loads((paths.ROOT / "tests" / "fixtures" / "replays" / f"{rid}.json").read_text())
        lines = replay["log"].split("\n")
        mine = T._team_text(reg, lines, "p1")
        setup = from_log.setup(reg, lines, "p1", mine)
        full = from_log.journal(reg, lines, setup)
        sent = list(dict.fromkeys(e["species"] for e in full if e.get("side") == "p1"
                                  and e.get("kind") in ("lead", "switch") and e.get("species")))
        bring = [{"kind": "bring", "side": "p1", "species": sent[:4]}] if len(sent) >= 4 else []
        theirs = "\n\n".join(benchmark._set_text(s) for s in setup["theirs"])
        for i, e in enumerate(full):
            if e.get("kind") != "turn":
                continue
            entries = full[:i + 1] + bring
            why, kind = solving.reason(reg, Battle(reg, setup, entries))
            name = f"{rid.rsplit('-', 1)[1]} {getattr(kind, '__name__', kind)}"
            if why is None and name not in out:
                out[name] = {"my_team": mine, "their_team": theirs, "entries": entries}

    setup, journal = benchmark.build(reg, benchmark.load(reg), "F2", "B")
    out["F2/B Solve"] = {"my_team": "\n\n".join(solver._set_text(m) for m in setup["mine"]),
                         "their_team": "\n\n".join(benchmark._set_text(s) for s in setup["theirs"]),
                         "entries": journal}
    return out


def main() -> None:
    from vgc.mcp.server import Api, build
    from vgc.regulation import load_regulation

    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=URL)
    ap.add_argument("--rounds", type=int, default=1, help="times through every position")
    ap.add_argument("--gap", type=float, default=10.0, help="seconds to wait between positions")
    ap.add_argument("--reads", type=int, default=5, help="times each read is called")
    ap.add_argument("--timeout", type=float, default=60.0, help="`solve`'s wait for the answer to settle")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    api = Api(args.url)
    if api.client.auth is None and not args.url.startswith("http://127."):
        sys.exit("no password: set VGC_WEB_PASSWORD or put it in .env")
    server = build(api)

    def call(tool: str, **kw: Any) -> tuple[dict[str, Any], float]:
        t = time.perf_counter()
        r = asyncio.run(server.call_tool(tool, kw))
        dt = time.perf_counter() - t
        if r.is_error:
            raise RuntimeError(f"{tool}: {r.content}")
        return r.structured_content, dt

    reads = {}
    for tool in READS:
        times = [call(tool)[1] for _ in range(args.reads)]
        reads[tool] = {"median": round(statistics.median(times), 3), "max": round(max(times), 3)}
        print(f"{tool:14s} median {1000 * reads[tool]['median']:.0f} ms  max {1000 * reads[tool]['max']:.0f} ms",
              flush=True)

    todo = positions(load_regulation("reg_mc"))
    rows = []
    for rnd in range(1, args.rounds + 1):
        for name, fx in todo.items():
            b, t_create = call("create_battle", my_team=fx["my_team"], their_team=fx["their_team"],
                               name=f"timing {name}")
            bid = b["id"]
            try:
                v, t_add = call("add_entries", battle_id=bid, entries=fx["entries"])
                _, t_get = call("get_battle", battle_id=bid)
                _, t_traj = call("trajectory", battle_id=bid)
                row = {"round": rnd, "position": name, "taps": len(fx["entries"]),
                       "tap_errors": len(v.get("errors") or []), "create": round(t_create, 3),
                       "add": round(t_add, 3), "get": round(t_get, 3), "trajectory": round(t_traj, 3)}
                try:
                    s, t_solve = call("solve", battle_id=bid, wait=True, timeout=args.timeout)
                except Exception as e:                      # a 500 is a result here, not a stop
                    row["solve_error"] = str(e.__cause__ or e)[:200]
                else:
                    row |= {"solve": round(t_solve, 2), "cached": t_solve < 0.6, "mode": s.get("mode"),
                            "kind": s.get("kind"), "depth": s.get("depth"), "elapsed": s.get("elapsed"),
                            "settled": s.get("settled"), "error": s.get("error"), "timeline": s.get("timeline")}
            finally:
                call("delete_battle", battle_id=bid)
            rows.append(row)
            steps = " ".join(f"{t['wall']}s:d{t['depth']}/g{t['guesses']}" for t in row.get("timeline") or [])
            print(f"{rnd} {name:24s} create {row['create']:.2f} add {row['add']:.2f} | "
                  f"solve {row.get('solve_error') or row.get('solve')} {row.get('kind') or ''} "
                  f"{'(cached) ' if row.get('cached') else ''}{steps}", flush=True)
            time.sleep(args.gap)

    Path(args.out).write_text(json.dumps({"url": args.url, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                          "reads": reads, "positions": rows}, indent=1))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
