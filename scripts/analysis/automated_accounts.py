"""Ladder accounts that play too much to be people (PLAN-v5 step 0, found by `fresh_corpus.py`).

The bot pattern (`vgc.meta.replays.is_bot`) catches names like `pcrlbot12d159c39a`. The scrape of
2026-10-05 found others it does not: accounts named like the arms of an experiment
(`scorecard-pokemon`, `sc-sme`, `sc-control`) and a family of numbered alts (`kevdan42`, `kvdn53`,
`kvdn64`). The five busiest played Bo1 at 165–241 games a day for 12–26 days, and with the
smaller ones they were in about 40% of the new Bo1 games.

The rule, applied to every cached and staged replay of the regulation, all formats together:

    automated  ⇔  ≥ MIN_GAMES games  and  games / max(1, active days) > MAX_PER_DAY

where the active days run from the account's first game to its last. The rate is set from the
cache before 2026-09-20, when there was no surge: there, of accounts with 100 games or more, the
busiest played 31 a day (krimzus, 113 games over 3.6 days), and short bursts by people reach 50
(50 games in a day). The threshold is twice the first. It flags only accounts at 65 a day or
more, and leaves dedicated people (streamers, weekend grinders at 25–45 a day) in. Names that only
*sound* automated (`mizuki-ai`, 53 a day) or that belong to a flagged family (`kvdn53`, 31 a day)
are kept when under the rate: a name is not evidence. The list is written to
`data/teams/<regulation>/automated_accounts.json` (tracked), which `vgc.meta.replays.is_automated`
reads, and the extraction leaves those games out of every split.

    .venv/bin/python scripts/analysis/automated_accounts.py [--staged data/replays-new]
"""

from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from pathlib import Path

from vgc import paths

MIN_GAMES = 100
MAX_PER_DAY = 60.0


def main() -> None:
    from vgc.meta.pool import formats_for
    from vgc.meta.replays import REPLAYS, automated_path, is_bot
    from vgc.regulation import load_regulation

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regulation", "-r", default="reg_mc")
    ap.add_argument("--staged", type=Path, default=paths.DATA / "replays-new",
                    help="also read staged replays not yet in the cache")
    a = ap.parse_args()
    reg = load_regulation(a.regulation)

    times: dict[str, list[int]] = defaultdict(list)
    fmts: dict[str, set[str]] = defaultdict(set)
    for fmt in formats_for(reg):
        for d in (REPLAYS / fmt, a.staged / fmt):
            for f in d.glob("*.json.gz"):
                r = json.loads(gzip.decompress(f.read_bytes()))
                for p in r.get("players") or []:
                    times[p.lower()].append(r.get("uploadtime") or 0)
                    fmts[p.lower()].add("bo3" if fmt.endswith("bo3") else "bo1")
    out = {}
    for p, ts in times.items():
        days = max(1.0, (max(ts) - min(ts)) / 86400)
        rate = len(ts) / days
        if len(ts) >= MIN_GAMES and rate > MAX_PER_DAY:
            out[p] = {"games": len(ts), "active_days": round(days, 1), "per_day": round(rate, 1),
                      "formats": sorted(fmts[p]), "bot_pattern": is_bot(p)}
    path = automated_path(reg)
    path.write_text(json.dumps({
        "regulation": reg.id, "rule": f">= {MIN_GAMES} games and > {MAX_PER_DAY:g} a day over the active span",
        "read_from": ["data/replays", str(a.staged.relative_to(paths.ROOT))],
        "accounts": dict(sorted(out.items(), key=lambda kv: -kv[1]["games"]))}, indent=1) + "\n")
    for p, v in sorted(out.items(), key=lambda kv: -kv[1]["games"]):
        print(f"{p:28} {v['games']:6} games  {v['per_day']:6.1f}/day over {v['active_days']:5.1f} days  "
              f"{','.join(v['formats'])}{'  (bot pattern)' if v['bot_pattern'] else ''}")
    print(f"{len(out)} accounts -> {path}")


if __name__ == "__main__":
    main()
