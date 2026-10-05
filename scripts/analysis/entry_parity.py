"""The page's battle against a log's, as the policy reads them, on many games (PLAN-v5 step 4).

`tests/test_policy_entry.py` holds the two adapters to the same solver positions on the fixture
games. This runs the same comparison over cached open-sheet replays, at every turn mark and from
both seats, and sorts what differs into what taps cannot carry and what is left:

  volatile          the log path declines (a volatile the solver cannot set up, such as confusion
                    or Throat Chop); the page has no tap for volatiles and would answer
  speed evidence    the same positions with different move-order weights (or one move order more):
                    the log also reads Speed from the order of end-of-turn effects, which nobody taps
  both decline      neither answers, for different reasons (a volatile the log sees, a sleep counter
                    the page does not)
  tap failed        a tap the page refuses (Revival Blessing brings back a fainted Pokémon)
  other             anything else, listed

Illusion games are left out, as `vgc.battle.from_log` says.

    .venv/bin/python scripts/analysis/entry_parity.py --games 400
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter

from vgc import paths

OUT = paths.DATA / "analysis" / "reg_mc" / "entry_parity.json"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", type=int, default=400)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    sys.path.insert(0, str(paths.ROOT / "tests"))
    import test_policy_entry as T

    from vgc.battle import from_log
    from vgc.battle.entry import Battle
    from vgc.meta import pool, replays
    from vgc.policy import view as V
    from vgc.regulation import load_regulation, to_id

    reg = load_regulation("reg_mc")
    reps = [r for fmt in pool.formats_for(reg) for r in replays.cached(fmt) if r["log"].count("|showteam|") == 2]
    random.Random(a.seed).shuffle(reps)
    counts, other = Counter(), []

    def setups(p):
        return {json.dumps(j["position"]["setup"] | {"active": j["position"]["active"]}, sort_keys=True)
                for j in p.get("jobs", [])}

    for r in reps[:a.games]:
        lines = r["log"].split("\n")
        if any(to_id(s["ability"] or "") == "illusion" for ss in from_log.setup(reg, lines).values()
               if isinstance(ss, list) for s in ss):
            counts["illusion games left out"] += 1
            continue
        counts["games"] += 1
        for sid in ("p1", "p2"):
            text = T._team_text(reg, lines, sid)
            setup = from_log.setup(reg, lines, sid, text)
            full = from_log.journal(reg, lines, setup)
            for at in [i for i, x in enumerate(lines) if x.startswith("|turn|")]:
                n = int(lines[at].split("|")[2])
                idx = next(i for i, e in enumerate(full) if e.get("kind") == "turn" and e["n"] == n)
                pv = V.PlayerView(reg, sid, text)
                pv.feed(lines[:at + 1])
                battle = Battle(reg, setup, full[:idx + 1])
                ev = V.EntryView(reg, battle)
                x, y = T._comparable(V.plan(reg, pv), pv), T._comparable(V.plan(reg, ev), ev)
                counts["positions"] += 1
                if x == y:
                    counts["identical"] += 1
                elif battle.rp.errors:
                    counts["tap failed"] += 1
                elif x["eligible"] != y["eligible"] and "cannot set up" in (x.get("reason") or ""):
                    counts["volatile"] += 1
                elif not x["eligible"] and not y["eligible"]:
                    counts["both decline"] += 1
                elif x["eligible"] and y["eligible"] and (setups(x) <= setups(y) or setups(y) <= setups(x)):
                    counts["speed evidence"] += 1
                else:
                    counts["other"] += 1
                    other.append([r["id"], sid, n])
    res = {"settings": {"games": a.games, "seed": a.seed}, "counts": dict(counts),
           "shares": {k: round(counts[k] / counts["positions"], 4)
                      for k in ("identical", "speed evidence", "volatile", "both decline", "tap failed", "other")},
           "other": other}
    OUT.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
