"""Is it the missing information, or is it a different corpus?

The closed-sheet gate says the served WP model degrades late in a Team Preview Only game — log
loss rises from 0.507 at t5–6 to 0.581 at t7+, and ECE triples — while on Open Team Sheets the
same model improves monotonically to the end. The explanation offered is PLAN-v2 finding 8: late
in a closed-sheet game the opponent is *partly* known (39% of items, 31% of abilities by t7+,
every one revealed mid-battle), and no training row is ever partly known, so the model is furthest
off-distribution exactly where a real game gives it the most to work with.

That explanation has a confound the gate cannot rule out. `human_closed` is the ladder shard and
`human_ots_all` is the Bo3 shard: different formats, different players, different ratings,
different forfeit rates. The degradation could be the population rather than the information.

So the same question is asked on **one corpus**. Held-out OTS games are masked and scored against
themselves. Same games, same players, same labels; only what is known changes.

The axis is the fraction of the opponent's six that is hidden, hiding whole Pokémon rather than
scattering fields, because that is how a real game reveals: you learn about the Pokémon you have
seen. Hiding a Pokémon takes its item, ability, sheet move list and nature together — everything
an Open Team Sheet carries and a cartridge does not. `moves_used` is never touched, since a move
that was used is revealed in either regime and the featurizer reads `moves or moves_used`.

  --mode uniform   hide each of the opponent's six independently with probability `f`, swept.
                   `f=0` is the unmasked OTS row; `f=1` is what `wp_closed_sheet.py`'s `mask()`
                   does for the whole battle.
  --mode curve     hide at the rate a real closed-sheet game hides at, per turn bucket, measured
                   by `training_mix.py --by-turn`. One point, but the one that matters.

If ECE peaks at partial masking rather than at `f=1`, the mechanism is partial information and
not the corpus. If it rises monotonically to `f=1`, the model simply dislikes ignorance and the
late-game shape needs another explanation.

    .venv/bin/python scripts/analysis/partial_information.py --version wp-v1c-gbt
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vgc import paths                                          # noqa: E402
from vgc.data.splits import read_shard                         # noqa: E402
from vgc.regulation import load_regulation                     # noqa: E402
from vgc.wp.evaluate import BUCKETS, _bucket, metrics          # noqa: E402
from vgc.wp.features import featurize                         # noqa: E402
from vgc.wp.models import symmetrize                           # noqa: E402
from vgc.wp.tools import _load                                 # noqa: E402

# Measured on the held-out closed-sheet shard by `training_mix.py --by-turn`: the fraction of the
# opponent's six whose item / ability is known, per turn bucket. Averaged into one hide-rate,
# since this axis hides a Pokémon whole.
CLOSED_KNOWN = {"t1-2": 0.092, "t3-4": 0.215, "t5-6": 0.272, "t7+": 0.349}


def turn_bucket(turn: int | None) -> str | None:
    if turn is None:
        return None
    return "t1-2" if turn <= 2 else "t3-4" if turn <= 4 else "t5-6" if turn <= 6 else "t7+"


def hide(rec: dict, f: float, rng: random.Random) -> dict:
    """A copy of `rec` with that fraction of the opponent's six hidden, whole.

    Only the opponent's side, and only on a player row — a spectator row has no opponent, and
    hiding from both sides would ask a different question. `stats` is already None on every
    opposing mon in every row, which is finding 8's other half and is left alone.
    """
    out = json.loads(json.dumps(rec))
    obs = out["obs"]
    them = "p2" if obs["perspective"] == "p1" else "p1"
    hidden = 0
    for m in obs["sides"][them]["mons"]:
        if rng.random() >= f:
            continue
        hidden += 1
        m["item"], m["item_source"] = None, None
        m["ability"], m["ability_source"] = None, None
        m["moves"], m["nature"] = [], None
    if hidden:
        obs["sides"][them]["sheet"] = False
    return out


def score(model, fz, recs: list[dict]) -> dict:
    d = featurize(recs, fz)
    p, _ = model.predict(d)
    s = symmetrize(d, p)
    buckets = _bucket(s["kind"], s["turn"])
    out = {"all": {k: v for k, v in metrics(s["p"], s["y"]).items() if k != "reliability"}}
    for b in BUCKETS:
        m = buckets == b
        if m.any():
            out[b] = {k: v for k, v in metrics(s["p"][m], s["y"][m]).items() if k != "reliability"}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regulation", default="reg_mc")
    ap.add_argument("--version", default="wp-v1c-gbt")
    ap.add_argument("--mode", choices=["uniform", "curve", "both"], default="both")
    ap.add_argument("--fractions", type=float, nargs="+", default=[0.0, 0.25, 0.5, 0.75, 0.9, 1.0])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    reg = load_regulation(args.regulation)
    # The model's own vocabulary and featurizer version, not the newest defaults.
    model, fz = _load(reg, args.version)
    shard = (paths.ROOT / "data" / "snapshots" / reg.id / "human"
             / (reg.showdown_format + "bo3") / "heldout_human.jsonl.gz")
    # Player rows only: "the opponent" is undefined for a spectator, and the app's user is a player.
    recs = [r for r in read_shard(shard) if r["obs"]["perspective"] in ("p1", "p2")]
    print(f"{len(recs)} held-out OTS player rows from {shard.name}", file=sys.stderr)

    results: dict[str, dict] = {}
    if args.mode in ("uniform", "both"):
        for f in args.fractions:
            rng = random.Random(args.seed)
            results[f"hidden_{f:g}"] = score(model, fz, [hide(r, f, rng) for r in recs])
            print(f"  hidden {f:g}: ece {results[f'hidden_{f:g}']['all']['ece']}", file=sys.stderr)
    if args.mode in ("curve", "both"):
        rng = random.Random(args.seed)
        curved = [hide(r, 1 - CLOSED_KNOWN.get(turn_bucket(r["obs"].get("turn")) or "", 1.0), rng)
                  for r in recs]
        results["closed_curve"] = score(model, fz, curved)

    out = {"version": args.version, "rows": len(recs), "seed": args.seed,
           "note": "held-out OTS games masked against themselves; only information varies",
           "closed_known_rates": CLOSED_KNOWN, "results": results}
    text = json.dumps(out, indent=1)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
