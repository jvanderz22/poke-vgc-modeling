"""Are the Speed orderings handed to the model true, in both information regimes?

`vgc.belief.speed.orderings` says "this Pokémon of theirs is at least as fast as that one of yours,
in persistent speed". It is a model input from featurizer version 3 on (PLAN-v2, Phase 8 step 8),
so a false one is a wrong fact the model is trained on. Soundness is checked the way the Speed
channel was: on self-play with sampled spreads, where the true nature, Stat Points and item of
every Pokémon are in the run's `input_log`.

Two regimes, because a filter that is sound in one has to be re-earned in the other (gate rule 7):

  open     the log as played — every sheet shown, so every ability and item is known;
  closed   the same log with the team-sheet lines removed, as a spectator sees a cartridge game:
           abilities and items are known only once revealed, which is where an unseen Unburden or
           Swift Swim could make a boosted order look like persistent speed.

    .venv/bin/python scripts/analysis/speed_orderings.py --run data/selfplay/<spreads run> --battles 2000
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vgc import paths                                          # noqa: E402
from vgc.belief import speed                                   # noqa: E402
from vgc.data.observe import Observer                          # noqa: E402
from vgc.regulation import Regulation, load_regulation, to_id  # noqa: E402


def battles(path: Path, limit: int) -> Iterator[dict]:
    with gzip.open(path, "rt") as f:
        for i, line in enumerate(f):
            if i >= limit:
                return
            yield json.loads(line)


def true_sets(record: dict, reg: Regulation) -> dict[tuple[str, str], dict[str, Any]]:
    """Nature, Speed Stat Points and item per Pokémon, from the packed teams the run started with.
    Packed format: `nick|species|item|ability|moves|nature|evs|...`, Stat Points in `evs`."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
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
            out[(sid, name)] = {"nature": f[5] or None, "spe": vals[5], "item": to_id(f[2])}
    return out


def persistent(reg: Regulation, truth: dict[str, Any], forme: str) -> float | None:
    stat = speed.speed_stat(reg, forme, truth["nature"], truth["spe"])
    # Showdown rounds the modified stat down, so a Scarf on 119 is 178, not 178.5.
    return None if stat is None else int(stat * speed.ITEM_MULT.get(truth["item"], 1.0))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="a self-play run directory with sampled spreads")
    ap.add_argument("--regulation", default="reg_mc")
    ap.add_argument("--battles", type=int, default=2000)
    args = ap.parse_args()

    reg = load_regulation(args.regulation)
    path = Path(args.run) / "battles.jsonl.gz"
    tally = {r: {"orderings": 0, "false": 0, "strict": 0, "unscored": 0, "battles_with_any": 0}
             for r in ("open", "closed")}
    offenders: dict[str, list] = {"open": [], "closed": []}
    n = 0
    for rec in battles(path, args.battles):
        n += 1
        truth = true_sets(rec, reg)
        log = rec["log"] if isinstance(rec["log"], list) else rec["log"].split("\n")
        for regime, lines in (("open", log), ("closed", [x for x in log if not x.startswith("|showteam|")])):
            obs = Observer("spectator", reg.dex)
            obs.feed_many(lines)
            found = speed.orderings(reg, obs)
            # Orderings for a Pokémon that lost its item are only kept from after the loss, so
            # they are scored against the item-less truth.
            lost = {(sid, m.species) for sid, side in obs.sides.items() for m in side.mons if m.lost_item}
            t = tally[regime]
            t["battles_with_any"] += bool(found)
            for a, b in found:
                t["orderings"] += 1
                ta, tb = truth.get((a[0], a[1])), truth.get((b[0], b[1]))
                ta = ta and (ta | {"item": ""} if (a[0], a[1]) in lost else ta)
                tb = tb and (tb | {"item": ""} if (b[0], b[1]) in lost else tb)
                va = persistent(reg, ta, a[2]) if ta else None
                vb = persistent(reg, tb, b[2]) if tb else None
                if va is None or vb is None:
                    t["unscored"] += 1
                    continue
                t["strict"] += va > vb
                if va < vb:
                    t["false"] += 1
                    if len(offenders[regime]) < 8:
                        offenders[regime].append({"battle": rec["battle_id"], "ahead": a, "behind": b,
                                                  "true": [va, vb]})

    out = {"run": str(path.parent), "battles": n, "by_regime": {}}
    for regime, t in tally.items():
        scored = t["orderings"] - t["unscored"]
        out["by_regime"][regime] = t | {
            "false_rate": round(t["false"] / scored, 5) if scored else None,
            "per_battle": round(t["orderings"] / n, 2) if n else None,
            "offenders": offenders[regime]}
        print(f"{regime:6}: {t['orderings']} orderings over {n} battles ({t['orderings'] / max(n, 1):.2f} a battle, "
              f"{t['battles_with_any']} battles with any); {t['false']} false of {scored} scored "
              f"({t['false'] / max(scored, 1):.3%}); {t['strict']} strictly faster, the rest ties")
        for o in offenders[regime][:3]:
            print(f"        false: {o}")
    dest = paths.DATA / "analysis" / "speed_orderings.json"
    dest.write_text(json.dumps(out, indent=1) + "\n")
    print(f"wrote {dest.relative_to(paths.ROOT)}")


if __name__ == "__main__":
    main()
