"""Is the turn-order → Speed bound sound, and is it worth anything?

Soundness is the claim the channel makes: the true Stat Point allocation must never leave the
feasible set. It is checked on self-play, where the spread is in the run's `input_log` and so is
actually known — on human replays it is not recoverable at all, which is the whole reason this
phase exists.

Power is the second question and a separate one. A bound that is always right and never narrows
anything is sound and useless, so this reports how much of the 0..32 prior each observation rules
out, how many racing pairs a game yields, and how often the module abstained.

    .venv/bin/python scripts/analysis/speed_belief.py --run data/selfplay/<run> --battles 500
"""

from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

from vgc.belief import speed
from vgc.data.observe import Observer
from vgc.regulation import Regulation, load_regulation, to_id


def battles(path: Path, limit: int) -> Iterator[dict]:
    with gzip.open(path, "rt") as f:
        for i, line in enumerate(f):
            if i >= limit:
                return
            yield json.loads(line)


def true_spreads(record: dict, reg: Regulation) -> dict[tuple[str, str], dict[str, int]]:
    """Stat Points per Pokémon, from the packed teams the run actually started with.

    Packed format is `nick|species|item|ability|moves|nature|evs|...`, and in Champions the `evs`
    field holds Stat Points. This is ground truth, not `impute_sp`'s guess — though the pool the
    run drew from was itself built with imputed spreads, which is why the *power* number below is
    a floor and is labelled as one.
    """
    out: dict[tuple[str, str], dict[str, int]] = {}
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
            out[(sid, name)] = dict(zip(("hp", "atk", "def", "spa", "spd", "spe"), vals))
    return out


def pair_diagnosis(reg: Regulation, moves: list) -> Counter:
    """Why pairs were dropped, so the abstention rate is a number and not a silence."""
    c: Counter = Counter()
    by_turn: dict[int, list] = {}
    for ev in moves:
        by_turn.setdefault(ev.turn, []).append(ev)
    for evs in by_turn.values():
        if any(to_id(e.move) in speed.REORDERING_MOVES for e in evs):
            c["turn_reordered"] += len(evs) * (len(evs) - 1) // 2
            continue
        evs = sorted(evs, key=lambda e: e.seq)
        for i, a in enumerate(evs):
            for b in evs[i + 1:]:
                if a.side == b.side and a.slot == b.slot:
                    continue
                c["considered"] += 1
                if not (speed._stable(a) and speed._stable(b)):
                    c["state_moved_mid_turn"] += 1
                elif not (speed._usable(reg, a) and speed._usable(reg, b)):
                    c["unmodelled_mover"] += 1
                elif speed.effective_priority(reg, a) != speed.effective_priority(reg, b):
                    c["priority_differs"] += 1
                else:
                    c["usable"] += 1
    return c


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="a self-play run directory")
    ap.add_argument("--regulation", default="reg_mc")
    ap.add_argument("--battles", type=int, default=500)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    reg = load_regulation(args.regulation)
    path = Path(args.run) / "battles.jsonl.gz"

    diag: Counter = Counter()
    checked = violations = contradicted = 0
    narrowed: list[float] = []
    informative = 0
    bounds_width: list[int] = []
    per_battle_pairs: list[int] = []
    truth_values: Counter = Counter()
    n_battles = 0
    offenders: list[dict[str, Any]] = []

    for rec in battles(path, args.battles):
        n_battles += 1
        obs = Observer("spectator", reg.dex)
        obs.feed_many(rec["log"] if isinstance(rec["log"], list) else rec["log"].split("\n"))
        truth = true_spreads(rec, reg)
        diag += pair_diagnosis(reg, obs.moves_log)
        per_battle_pairs.append(len(speed.pairs(reg, obs.moves_log)))

        # Stand in for "I know my own team": p1's Stat Points are given, p2's are inferred.
        known = {k: sp["spe"] for k, sp in truth.items() if k[0] == "p1"}

        for key, belief in speed.infer(reg, obs, known).items():
            real = truth.get(key)
            if real is None:
                continue
            checked += 1
            truth_values[real["spe"]] += 1
            narrowed.append(belief.narrowed)
            informative += belief.narrowed > 0
            if belief.feasible:
                bounds_width.append(belief.bounds[1] - belief.bounds[0] + 1)
            contradicted += belief.contradicted
            if real["spe"] not in belief.feasible:
                violations += 1
                if len(offenders) < 5:
                    offenders.append({"battle": rec["battle_id"], "mon": list(key),
                                      "true_spe": real["spe"], "bounds": belief.bounds,
                                      "used": belief.used})

    out = {
        "run": str(path.parent),
        "battles": n_battles,
        "pokemon_checked": checked,
        "soundness": {
            # Two different failures, and pooling them would hide which one is shipping.
            # `silently_wrong` is a belief that excluded the truth and did not know it; that is
            # the number the channel lives or dies on. `contradicted` is one that proved itself
            # wrong and widened back to the prior, which costs power and states nothing false.
            "silently_wrong": violations,
            "rate": violations / max(checked, 1),
            "contradicted": contradicted,
            "contradicted_rate": contradicted / max(checked, 1),
            "examples": offenders,
        },
        "power": {
            "narrowed_mean": sum(narrowed) / max(len(narrowed), 1),
            "any_narrowing": informative / max(checked, 1),
            "bounds_width_mean": sum(bounds_width) / max(len(bounds_width), 1),
            "pairs_per_battle": sum(per_battle_pairs) / max(n_battles, 1),
        },
        "pairs": dict(diag),
        "abstention_rate": 1 - diag["usable"] / max(diag["considered"], 1),
        "truth_distribution": dict(sorted(truth_values.items())),
        "caveat": "the pool's spreads are impute_sp's output, so the truth here takes few distinct "
                  "values and the power number is a floor, not an estimate of the real thing",
    }
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
