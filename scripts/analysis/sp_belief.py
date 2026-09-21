"""Do the two channels, joined by the budget, bound the whole spread — and soundly?

The channels were gated one stat at a time. This asks the question the phase actually poses: over
the 66-point allocation, does the truth survive, and how much of the space is gone?

Three things it is built to report and not to flatter:

- **Soundness under assumptions it cannot test.** `prior.sample_spread` spends every point and
  zeroes every dead stat, so a run scored with `spend_all=True, dead_zero=True` is scoring two
  assumptions the corpus was built to satisfy. The `rules_only` row assumes nothing past the
  regulation (sum ≤ 66, ≤32 a stat) and is the only soundness figure that can be falsified here.
- **Bulk**, which the budget bounds as a total and `vgc.belief.bulk` bounds directly, so
  `bulk_bounded` is the number this module exists to produce.
- **Calibration**, as the plan states it: the truth lands in the 80% credible set ~80% of the time.

    .venv/bin/python scripts/analysis/sp_belief.py --run data/selfplay/<run> --battles 2000
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vgc.belief import bulk as bulk_channel             # noqa: E402
from vgc.belief import damage as damage_channel        # noqa: E402
from vgc.belief import speed as speed_channel          # noqa: E402
from vgc.belief import sp as sp_belief                 # noqa: E402
from vgc.data.observe import Observer                  # noqa: E402
from vgc.engine.calc import DamageCalc                 # noqa: E402
from vgc.regulation import STAT_IDS, load_regulation   # noqa: E402

from scripts.analysis.damage_belief import sets_from_log   # noqa: E402
from scripts.analysis.speed_belief import battles, true_spreads   # noqa: E402

BULK = ("hp", "def", "spd")

# `rules_only` assumes nothing the format does not enforce and is the only falsifiable soundness
# gate here. `shipped` is the default: both build conventions on, each confirmed against play
# rather than measured here. `dead_stat_only` isolates what assuming a spent budget adds.
# The settings are echoed into the result, because a label that drifts from what it names is worse
# than no label — this table was read wrong once already.
CONFIGS = {
    "rules_only": dict(dead_zero=False, spend_all=False),
    "dead_stat_only": dict(dead_zero=True, spend_all=False),
    "shipped": dict(dead_zero=True, spend_all=True),
}


class Tally:
    def __init__(self) -> None:
        self.checked = 0
        self.violations = 0
        self.contradicted = 0
        self.narrowed: list[float] = []
        self.bulk_bounded = 0
        self.bulk_width: list[int] = []
        self.in_credible: Counter = Counter()
        self.in_credible_flat: Counter = Counter()
        self.credible_size: Counter = Counter()
        self.stat_checked: Counter = Counter()
        self.offenders: list[dict[str, Any]] = []

    def add(self, belief, truth: dict[str, int], battle: str) -> None:
        self.checked += 1
        self.contradicted += bool(belief.contradicted)
        self.narrowed.append(belief.narrowed)
        b = belief.bounds()
        # Bulk is bounded through the budget as a *total*, not stat by stat: each of the three can
        # still be anything up to 32 while what they add to has moved a long way.
        lo, hi = belief.spent_on(BULK)
        prior_lo, prior_hi = sp_belief.SPBelief(
            belief.side, belief.species, belief.nature, belief.prior, belief.prior,
            belief.budget).spent_on(BULK)
        if (lo, hi) != (prior_lo, prior_hi):
            self.bulk_bounded += 1
        self.bulk_width.append(hi - lo)
        if not belief.contains(truth):
            self.violations += 1
            if len(self.offenders) < 5:
                self.offenders.append({"battle": battle, "mon": [belief.side, belief.species],
                                       "true": truth, "bounds": b,
                                       "sources": belief.sources,
                                       "speed_used": belief.speed_used,
                                       "damage_used": belief.damage_used})
            return
        for stat in STAT_IDS:
            cred = belief.credible(stat, 0.8)
            self.stat_checked[stat] += 1
            self.credible_size[stat] += len(cred)
            self.in_credible[stat] += truth.get(stat, 0) in cred
            self.in_credible_flat[stat] += truth.get(stat, 0) in belief.credible(stat, 0.8, flat=True)

    def report(self) -> dict[str, Any]:
        mean = lambda xs: sum(xs) / max(len(xs), 1)  # noqa: E731
        return {
            "pokemon_checked": self.checked,
            "silently_wrong": self.violations,
            "rate": self.violations / max(self.checked, 1),
            "contradicted_by_budget": self.contradicted,
            "narrowed_mean": mean(self.narrowed),
            "any_narrowing": sum(1 for x in self.narrowed if x > 0) / max(len(self.narrowed), 1),
            "bulk_bounded": self.bulk_bounded / max(self.checked, 1),
            "bulk_total_width_mean": mean(self.bulk_width),
            # The plan's calibration test. A set that holds 80% of the mass should hold the truth
            # 80% of the time; far above means the belief is wider than it claims to be.
            "in_80_credible": {s: round(self.in_credible[s] / max(self.stat_checked[s], 1), 4)
                               for s in STAT_IDS},
            # Split because `sample_spread` draws Speed and the live offensive stat from a *flat*
            # marginal on purpose, while a belief weighted by allocations is decreasing. Coverage
            # on those two stats therefore scores the generator's shape against the prior's, not
            # the channel; the flat reading asks only whether the feasible set is the right size.
            "in_80_credible_flat": {s: round(self.in_credible_flat[s] / max(self.stat_checked[s], 1), 4)
                                    for s in STAT_IDS},
            "credible_size_mean": {s: round(self.credible_size[s] / max(self.stat_checked[s], 1), 2)
                                   for s in STAT_IDS},
            "examples": self.offenders,
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--regulation", default="reg_mc")
    ap.add_argument("--battles", type=int, default=1000)
    ap.add_argument("--no-damage", action="store_true",
                    help="turn-order channel only — the mode the app can afford every turn")
    args = ap.parse_args()

    reg = load_regulation(args.regulation)
    path = Path(args.run) / "battles.jsonl.gz"
    tallies = {name: Tally() for name in CONFIGS}
    n_battles = 0

    with DamageCalc() as dc:
        for rec in battles(path, args.battles):
            n_battles += 1
            obs = Observer("spectator", reg.dex)
            obs.feed_many(rec["log"] if isinstance(rec["log"], list) else rec["log"].split("\n"))
            sets = sets_from_log(rec, reg)
            truth = true_spreads(rec, reg)
            known = {k: v for k, v in sets.items() if k[0] == "p1"}
            if not known:
                continue

            speeds = speed_channel.infer(reg, obs, {k: v.sp.spe for k, v in known.items()})
            damages = {} if args.no_damage else damage_channel.infer(reg, obs, known, dc)
            bulks = {} if args.no_damage else bulk_channel.infer(reg, obs, known, dc)
            sheets = {(sid, m.species): m for sid, s in obs.sides.items() for m in s.mons}

            for key in sorted(set(speeds) | set(damages) | set(bulks)):
                mon, real = sheets.get(key), truth.get(key)
                if mon is None or real is None or key in known:
                    continue
                for name, cfg in CONFIGS.items():
                    belief = sp_belief.combine(reg, key, mon.nature, mon.moves or [],
                                               speeds.get(key), damages.get(key), bulks.get(key),
                                               **cfg)
                    tallies[name].add(belief, real, rec["battle_id"])

    print(json.dumps({
        "run": str(path.parent),
        "battles": n_battles,
        "damage_channel": not args.no_damage,
        "note": "the corpus spends all 66 points and zeroes dead stats by construction, so nothing "
                "here can falsify either convention; only `rules_only` can falsify the channels",
        "configs": {name: dict(CONFIGS[name], **t.report()) for name, t in tallies.items()},
    }, indent=1))


if __name__ == "__main__":
    main()
