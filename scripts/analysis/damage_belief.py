"""Is the damage → offensive Stat Points bound sound, and is it worth anything?

Same two questions as `speed_belief.py`, and the same answer to "how would we know": the spread is
in a self-play run's `input_log`, so on generated battles the truth is available and the feasible
set can be checked against it. On human replays it never can be, which is the whole reason the
phase exists.

This has to be run on a corpus generated with `--spreads sampled`. All 20,082 Pokémon in the
`impute_sp` pool have exactly 32 points in their offensive stat, so on that corpus the quantity
being inferred is a constant and the measurement would be vacuous.

    .venv/bin/python scripts/analysis/damage_belief.py --run data/selfplay/<run> --battles 2000
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from vgc.belief import damage
from vgc.data.observe import Observer
from vgc.engine.calc import DamageCalc
from vgc.regulation import load_regulation, offensive_stat
from vgc.teams.sets import PokemonSet, StatPoints

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.analysis.speed_belief import battles, true_spreads  # noqa: E402


def sets_from_log(record: dict, reg) -> dict[tuple[str, str], PokemonSet]:
    """Complete sets, as the run actually started: species, item, ability, nature and spread."""
    out: dict[tuple[str, str], PokemonSet] = {}
    for line in record["input_log"]:
        if not line.startswith(">player "):
            continue
        _, sid, blob = line.split(" ", 2)
        for packed in json.loads(blob)["team"].split("]"):
            f = packed.split("|")
            if len(f) < 7:
                continue
            entry = reg.dex.get_species(f[1] or f[0])
            name = entry["name"] if entry else (f[1] or f[0])
            vals = [int(x or 0) for x in f[6].split(",")] if f[6] else [0] * 6
            out[(sid, name)] = PokemonSet(
                species=name,
                item=(reg.dex.get_item(f[2]) or {}).get("name") if f[2] else None,
                ability=(reg.dex.abilities.get(f[3]) or {}).get("name") or f[3] or None,
                nature=f[5] or "Serious",
                moves=[m for m in f[4].split(",") if m],
                sp=StatPoints.from_dict(dict(zip(("hp", "atk", "def", "spa", "spd", "spe"), vals))),
                level=reg.level,
            )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--regulation", default="reg_mc")
    ap.add_argument("--battles", type=int, default=2000)
    args = ap.parse_args()

    reg = load_regulation(args.regulation)
    path = Path(args.run) / "battles.jsonl.gz"

    checked = violations = contradicted = 0
    narrowed: list[float] = []
    censored_narrowed: list[float] = []
    clean_narrowed: list[float] = []
    truth_values: Counter = Counter()
    reasons: Counter = Counter()
    events = used = 0
    offenders: list[dict[str, Any]] = []
    n_battles = 0

    with DamageCalc() as dc:
        for rec in battles(path, args.battles):
            n_battles += 1
            obs = Observer("spectator", reg.dex)
            obs.feed_many(rec["log"] if isinstance(rec["log"], list) else rec["log"].split("\n"))
            sets = sets_from_log(rec, reg)
            truth = true_spreads(rec, reg)

            for ev in obs.damage_log:
                events += 1
                why = damage.usable(reg, ev)
                reasons[why or "usable"] += 1

            known = {k: v for k, v in sets.items() if k[0] == "p1"}
            for key, belief in damage.infer(reg, obs, known, dc).items():
                real = truth.get(key)
                if real is None:
                    continue
                checked += 1
                used += belief.used
                contradicted += belief.contradicted
                truth_values[real[belief.stat]] += 1
                narrowed.append(belief.narrowed)
                (censored_narrowed if belief.censored else clean_narrowed).append(belief.narrowed)
                if real[belief.stat] not in belief.feasible:
                    violations += 1
                    if len(offenders) < 5:
                        offenders.append({"battle": rec["battle_id"], "mon": list(key),
                                          "stat": belief.stat, "true": real[belief.stat],
                                          "bounds": belief.bounds, "used": belief.used,
                                          "censored": belief.censored})

    mean = lambda xs: sum(xs) / max(len(xs), 1)  # noqa: E731
    print(json.dumps({
        "run": str(path.parent),
        "battles": n_battles,
        "damage_events": events,
        "pokemon_checked": checked,
        "soundness": {
            "silently_wrong": violations,
            "rate": violations / max(checked, 1),
            "contradicted": contradicted,
            "contradicted_rate": contradicted / max(checked, 1),
            "examples": offenders,
        },
        "power": {
            "narrowed_mean": mean(narrowed),
            "any_narrowing": sum(1 for x in narrowed if x > 0) / max(len(narrowed), 1),
            "observations_per_pokemon": used / max(checked, 1),
            # A KO tells you the move did *at least* the remaining HP and nothing more, so it
            # should narrow far less than a hit that was survived. Reported apart to check that.
            "narrowed_mean_uncensored": mean(clean_narrowed),
            "narrowed_mean_with_a_ko": mean(censored_narrowed),
        },
        "events_by_reason": dict(reasons),
        "truth_distribution": dict(sorted(truth_values.items())),
    }, indent=1))


if __name__ == "__main__":
    main()
