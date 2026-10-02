"""How often each species is brought when it is on the sheet (PLAN-policy, stage 2).

The opponent's back is unknown until it comes out: preview shows six, and which two of the unseen
were brought is a guess. `vgc.policy.view` weighs each guess by these rates. Counted over
training-split human games (never the held-out ones), from each side whose four brought are all
known by the end of the game (`brought_complete`), both regimes together: the six on the sheet
are shown in both.

    .venv/bin/python scripts/analysis/bring_rates.py      # about a minute on 8 workers
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from multiprocessing import Pool
from typing import Any

from vgc import paths

OUT = paths.DATA / "analysis" / "reg_mc" / "bring_rates.json"
_W: dict[str, Any] = {}


def _init() -> None:
    from vgc.data.splits import load_rules
    from vgc.regulation import load_regulation

    _W["reg"] = load_regulation("reg_mc")
    _W["rules"] = load_rules(_W["reg"])


def _game(replay: dict[str, Any]) -> list[tuple[list[str], list[str]]]:
    from vgc.data.snapshots import human_snapshots, replay_group
    from vgc.web.endgames import is_bot

    if _W["rules"].split_of("human", replay["id"], [], replay_group(replay)) != "train":
        return []
    if any(is_bot(p) for p in replay.get("players") or []):
        return []
    recs = [r for r in human_snapshots(replay, _W["reg"]) if r["obs"]["perspective"] == "spectator"]
    if not recs:
        return []
    label, obs = recs[0]["label"], recs[0]["obs"]
    out = []
    for sid in ("p1", "p2"):
        if not (label.get("brought_complete") or {}).get(sid):
            continue
        sheet = [m["species"] for m in obs["sides"][sid]["mons"]]
        if len(sheet) == 6:
            out.append((sheet, list(label["brought"][sid])))
    return out


def main() -> None:
    from vgc.meta import pool, replays
    from vgc.regulation import load_regulation

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    reg = load_regulation("reg_mc")
    reps = [rp for fmt in pool.formats_for(reg) for rp in replays.cached(fmt)]
    shown, brought = Counter(), Counter()
    sides = 0
    with Pool(a.workers, initializer=_init) as p:
        for rows in p.imap_unordered(_game, reps, chunksize=16):
            for sheet, took in rows:
                sides += 1
                shown.update(sheet)
                brought.update(s for s in took if s in sheet)
    out = {"sides": sides, "base": round(sum(brought.values()) / max(1, sum(shown.values())), 4),
           "species": {s: {"shown": shown[s], "brought": brought[s]} for s in sorted(shown)}}
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(f"{sides} sides, {len(shown)} species, base rate {out['base']}")


if __name__ == "__main__":
    main()
