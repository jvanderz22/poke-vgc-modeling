"""Does averaging over what they might be holding beat guessing one set?

The WP models are open-sheet models: every row they were trained on had the opponent's item,
ability, moves and spread visible. A Team Preview Only position is not such a row, and there are
two ways to hand them one anyway.

  **mode**       fill each of the opponent's six with its single most common set. One guess,
                 stated as fact. This is what `/api/preview` and the app did before.
  **particles**  draw `k` complete opponents from the belief and average the answers. Every draw
                 is a fully-known row, so every draw is in distribution, and the mean estimates
                 `E_belief[WP]` — the thing PLAN-v2 calls WP_v2.

Scored on **held-out human replays**, against what actually happened. Two baselines bound the
result from either side: `true` is the model given the real sheets, which is the best it can do
and not available in a real game, and `open` is the masked position handed over unfilled, which
is the off-distribution row nobody has trained on.

The set prior is counted from **training teams only**. Building it over the whole corpus and then
scoring held-out games with it would be scoring the prior on its own sheets.

    .venv/bin/python scripts/analysis/wp_closed_sheet.py --version <v> --replays 300
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vgc.belief import sets as set_belief                     # noqa: E402
from vgc.data.snapshots import human_snapshots                # noqa: E402
from vgc.data.splits import load_rules                        # noqa: E402
from vgc.meta import replays, usage                           # noqa: E402
from vgc.meta.pool import formats_for                         # noqa: E402
from vgc.regulation import load_regulation, to_id             # noqa: E402
from vgc.wp.features import featurize                         # noqa: E402
from vgc.wp.models import symmetrize                          # noqa: E402
from vgc.wp.tools import _load                                # noqa: E402

def training_corpus(reg, rules) -> dict[str, list[set_belief.Sheet]]:
    """The set prior, counted from teams on the training side of the frozen split only."""
    per: dict[str, Counter] = {}
    for sheet in usage.sheets(reg):
        if rules.team_heldout(replays.team_id(replays.team_key(sheet.team))):
            continue
        for mon in sheet.team.members:
            per.setdefault(mon.species_id, Counter())[
                (to_id(mon.item or ""), to_id(mon.ability or ""), mon.nature or "",
                 tuple(sorted(to_id(m) for m in dict.fromkeys(mon.moves))))] += 1
    return {sid: sorted((set_belief.Sheet(i, a, n, mv, c) for (i, a, n, mv), c in cnt.items()),
                        key=lambda s: -s.count)
            for sid, cnt in per.items()}


def mask(obs: dict[str, Any], them: str) -> dict[str, Any]:
    """The position as Team Preview Only shows it: six species and nothing else."""
    out = json.loads(json.dumps(obs))
    for m in out["sides"][them]["mons"]:
        m["item"], m["item_source"] = None, None
        m["ability"], m["ability_source"] = None, None
        m["moves"], m["nature"], m["stats"] = [], None, None
    out["sides"][them]["sheet"] = False
    return out


def fill(obs: dict[str, Any], them: str, corpus, reg, rng, *, mode: bool) -> dict[str, Any]:
    out = json.loads(json.dumps(obs))
    for m in out["sides"][them]["mons"]:
        pool = corpus.get(to_id(m["species"]))
        if not pool:
            continue
        if mode:
            pick = pool[0]
        else:
            total = sum(s.count for s in pool)
            r, acc = rng.random() * total, 0
            pick = pool[-1]
            for s in pool:
                acc += s.count
                if r <= acc:
                    pick = s
                    break
        m["item"], m["item_source"] = pick.item, "belief"
        m["ability"], m["ability_source"] = pick.ability, "belief"
        m["moves"] = list(pick.moves)
        m["nature"] = pick.nature or None
        # `stats` is deliberately left alone, and the first version of this gate got it wrong.
        # An opponent's stats are None in *every* training row — player rows carry your own and
        # nobody else's, spectator rows carry none — so filling them flips a feature the model has
        # never seen set, and the filled arms were being scored on a difference that had nothing
        # to do with the belief. Sheets carry no Stat Points anyway; the spread is what
        # `vgc.belief.sp` is for, and it is read on screen rather than fed to a model that cannot
        # use it.
    out["sides"][them]["sheet"] = True
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regulation", default="reg_mc")
    ap.add_argument("--version", default="")
    ap.add_argument("--replays", type=int, default=250)
    ap.add_argument("-k", type=int, default=24, help="particles per position")
    args = ap.parse_args()

    reg = load_regulation(args.regulation)
    rules = load_rules(reg)
    version = args.version or json.loads(
        (Path("models/wp") / reg.id / "registry.json").read_text())["default"] if False else args.version
    if not version:
        from vgc.wp.models import REGISTRY
        entries = json.loads(REGISTRY.read_text())["wp"]
        version = entries[-1]["version"]
    model, fz = _load(reg, version)
    corpus = training_corpus(reg, rules)

    arms = ("true", "mode", "particles", "open")
    loss = {a: [] for a in arms}
    brier = {a: [] for a in arms}
    preds: dict[str, list[float]] = {a: [] for a in arms}
    truth: list[float] = []
    seen_replays = positions = 0

    for fmt in formats_for(reg):
        for rep in replays.cached(fmt):
            if seen_replays >= args.replays:
                break
            group = None
            try:
                from vgc.data.snapshots import replay_group
                group = replay_group(rep)
            except Exception:
                pass
            if rules.split_of("human", rep.get("id", ""), [], group) != "heldout_human":
                continue
            try:
                recs = [r for r in human_snapshots(rep, reg)
                        if r["obs"]["perspective"] == "spectator" and not r["meta"].get("approx")]
            except Exception:
                continue
            recs = [r for r in recs if r["meta"].get("ots") and r["label"]["winner"] in ("p1", "p2")]
            if not recs:
                continue
            seen_replays += 1
            rng = random.Random(seen_replays)
            batch, index = [], []
            for rec in recs:
                obs, them = rec["obs"], "p2"
                masked = mask(obs, them)
                variants = {
                    "true": [obs],
                    "mode": [fill(masked, them, corpus, reg, rng, mode=True)],
                    "particles": [fill(masked, them, corpus, reg, rng, mode=False)
                                  for _ in range(args.k)],
                    "open": [masked],
                }
                for arm in arms:
                    for v in variants[arm]:
                        # `symmetrize` pairs a spectator row with its mirror by
                        # (battle, point, kind, perspective). Every variant of one position would
                        # share that key, so each arm's p1 view would be averaged against some
                        # *other* arm's p2 view and the arms would blur into each other. A unique
                        # `point` per variant keeps each one paired with its own mirror.
                        batch.append(dict(rec, obs=v, point=len(batch)))
                        index.append((len(index), arm, rec["label"]["winner"]))
                positions += 1
            if not batch:
                continue
            d = featurize(batch, fz)
            p, _ = model.predict(d)
            s = symmetrize(d, p)["p"]
            # `symmetrize` returns one row per snapshot in order; walk the arms back out.
            at = 0
            for rec in recs:
                y = 1.0 if rec["label"]["winner"] == "p1" else 0.0
                truth.append(y)
                for arm in arms:
                    n = args.k if arm == "particles" else 1
                    q = sum(float(x) for x in s[at:at + n]) / n
                    at += n
                    q = min(max(q, 1e-6), 1 - 1e-6)
                    preds[arm].append(q)
                    loss[arm].append(-(y * math.log(q) + (1 - y) * math.log(1 - q)))
                    brier[arm].append((q - y) ** 2)
        if seen_replays >= args.replays:
            break

    mean = lambda xs: round(sum(xs) / max(len(xs), 1), 4)  # noqa: E731

    # Hiding information moves every prediction toward 0.5, and on a corpus where the model is
    # overconfident that improves log-loss for a reason that has nothing to do with the belief.
    # So the confidence of each arm is reported, and `true` is shrunk toward 0.5 until it is as
    # confident as the particle arm and scored again. If that shrunken arm matches the particles,
    # the particles bought calibration rather than information — which is worth knowing, because
    # calibration is far cheaper to buy on purpose.
    conf = {a: mean([abs(q - 0.5) for q in preds[a]]) for a in arms}
    want, have = conf["particles"], conf["true"]
    t = want / have if have else 1.0
    shrunk = [0.5 + t * (q - 0.5) for q in preds["true"]]
    sl, sb = [], []
    for q, y in zip(shrunk, truth):
        q = min(max(q, 1e-6), 1 - 1e-6)
        sl.append(-(y * math.log(q) + (1 - y) * math.log(1 - q)))
        sb.append((q - y) ** 2)

    print(json.dumps({
        "version": version, "replays": seen_replays, "positions": positions, "k": args.k,
        "note": "held-out human OTS replays; the set prior is counted from training teams only",
        "logloss": {a: mean(loss[a]) for a in arms} | {"true_shrunk": mean(sl)},
        "brier": {a: mean(brier[a]) for a in arms} | {"true_shrunk": mean(sb)},
        "confidence": conf | {"true_shrunk": round(t * have, 4)},
        "shrink_factor": round(t, 4),
        "base_rate_p1": round(sum(truth) / max(len(truth), 1), 4),
    }, indent=1))


if __name__ == "__main__":
    main()
