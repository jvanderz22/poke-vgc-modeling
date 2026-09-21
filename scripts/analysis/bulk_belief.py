"""Is *your* damage, read backwards to their bulk, sound — and does it separate HP from defence?

The third channel and the first that reads your own moves. The soundness question is the same one
the other two answer and is checked the same way: on self-play the target's real spread is in the
run's `input_log`, so the region either contains it or does not.

The second question is specific to this channel. The fraction of health a hit takes goes roughly as
`1 / (hp × defence)`, so one observation locates the product and not the split — the feasible set
is a band around a hyperbola. This reports how much of the 33×33 grid survives, and separately how
much of each axis does, because a channel that halves the grid while leaving both axes at 0..32 has
learned something real that per-stat bounds cannot show.

    .venv/bin/python scripts/analysis/bulk_belief.py --run data/selfplay/<run> --battles 1000
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vgc.belief import bulk, damage                       # noqa: E402
from vgc.data.observe import Observer                     # noqa: E402
from vgc.engine.calc import DamageCalc                    # noqa: E402
from vgc.regulation import defensive_stat, load_regulation  # noqa: E402

from scripts.analysis.damage_belief import sets_from_log  # noqa: E402
from scripts.analysis.speed_belief import battles, true_spreads  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--regulation", default="reg_mc")
    ap.add_argument("--battles", type=int, default=1000)
    args = ap.parse_args()

    reg = load_regulation(args.regulation)
    path = Path(args.run) / "battles.jsonl.gz"
    cap = reg.sp_per_stat_cap
    whole = (cap + 1) ** 2

    checked = wrong = contradicted = both = used = censored = 0
    grid: list[float] = []
    axis: dict[str, list[float]] = {"hp": [], "def": [], "spd": []}
    reasons: Counter = Counter()
    offenders: list[dict] = []
    n = 0

    with DamageCalc() as dc:
        for rec in battles(path, args.battles):
            n += 1
            obs = Observer("spectator", reg.dex)
            obs.feed_many(rec["log"] if isinstance(rec["log"], list) else rec["log"].split("\n"))
            sets = sets_from_log(rec, reg)
            truth = true_spreads(rec, reg)
            known = {k: v for k, v in sets.items() if k[0] == "p1"}
            if not known:
                continue

            for ev in obs.damage_log:
                if (ev.attacker_side, ev.attacker) not in known:
                    continue
                why = damage.usable(reg, ev)
                if why is None and defensive_stat(reg.dex, ev.move) is None:
                    why = "not_a_damaging_move"
                reasons[why or "usable"] += 1

            for key, belief in bulk.infer(reg, obs, known, dc).items():
                real = truth.get(key)
                if real is None:
                    continue
                checked += 1
                contradicted += belief.contradicted
                both += len(belief.regions) == 2
                used += belief.used
                censored += belief.censored
                miss = [s for s, r in belief.regions.items() if (real["hp"], real[s]) not in r]
                if miss:
                    wrong += 1
                    if len(offenders) < 5:
                        offenders.append({"battle": rec["battle_id"], "mon": list(key),
                                          "stat": miss, "true": real,
                                          "bounds": belief.bounds(), "used": belief.used})
                    continue
                for stat, region in belief.regions.items():
                    grid.append(1 - len(region) / whole)
                    axis["hp"].append(1 - len({h for h, _ in region}) / (cap + 1))
                    axis[stat].append(1 - len({v for _, v in region}) / (cap + 1))

    mean = lambda xs: sum(xs) / max(len(xs), 1)  # noqa: E731
    print(json.dumps({
        "run": str(path.parent),
        "battles": n,
        "pokemon_checked": checked,
        "soundness": {"silently_wrong": wrong, "rate": wrong / max(checked, 1),
                      "contradicted": contradicted,
                      "contradicted_rate": contradicted / max(checked, 1),
                      "examples": offenders},
        "power": {
            # The grid is the answer; the axes are what a per-stat report would have shown, and
            # the gap between them is what keeping the region joint is worth.
            "grid_ruled_out": mean(grid),
            "axis_ruled_out": {s: mean(v) for s, v in axis.items()},
            "observations_per_pokemon": used / max(checked, 1),
            "censored_share": censored / max(used, 1),
            "hit_both_ways": both / max(checked, 1),
        },
        "events_by_reason": dict(reasons),
    }, indent=1))


if __name__ == "__main__":
    main()
