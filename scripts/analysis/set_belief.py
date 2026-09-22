"""Is the set prior's first guess the right one — on teams it has never seen?

The set prior is the one object in `vgc.belief` that is a *ranking* rather than a bound, so it is
gated on different terms. A bound is asked "does the truth ever leave the feasible set"; a ranking
is asked "how often is the first option the right one", and the cost of being wrong is a tap
rather than a wrong answer.

Held out by **team**, using the frozen 15% in `data/splits/<reg>.json`. That is the right unit:
holding out sheets would leak, because one player's team appears in every game they played, and
the question is whether the prior generalises to somebody else's team rather than whether it can
recite its own corpus.

Four numbers, and the fourth is the one that would be a bug rather than a disappointment:

  ability@1 / item@1   how often the likeliest option is the true one, against alphabetical —
                       which is what the pop-up did before, and is the honest baseline because it
                       is what the person was actually tapping through
  set@1                the same for the whole joint set, which is what a WP particle draws
  support              whether the truth is in the support at all. A ranking may be wrong; it may
                       never be *impossible*, so anything but 1.000 here is a floor that is not
                       doing its job

    .venv/bin/python scripts/analysis/set_belief.py --regulation reg_mc
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vgc.belief import sets as set_belief          # noqa: E402
from vgc.data.splits import load_rules             # noqa: E402
from vgc.meta import replays, usage                # noqa: E402
from vgc.regulation import load_regulation, to_id  # noqa: E402


def legal_abilities(reg, species: str) -> list[str]:
    return set_belief.legal_abilities(reg, species)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regulation", default="reg_mc")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    reg = load_regulation(args.regulation)
    rules = load_rules(reg)

    # Split the corpus by team, exactly as every other evaluation in this project does.
    train: dict[str, Counter] = {}
    held: list[tuple[str, str, str, str, tuple[str, ...]]] = []
    n_train = n_held = 0
    for sheet in usage.sheets(reg):
        team_id = replays.team_id(replays.team_key(sheet.team))
        heldout = rules.team_heldout(team_id)
        if heldout:
            n_held += 1
        else:
            n_train += 1
        for mon in sheet.team.members:
            sid = mon.species_id
            key = (to_id(mon.item or ""), to_id(mon.ability or ""), (mon.nature or ""),
                   tuple(sorted(to_id(m) for m in dict.fromkeys(mon.moves))))
            if heldout:
                held.append((sid, mon.species, key[0], key[1], key[3]))
            else:
                train.setdefault(sid, Counter())[key] += 1

    tally = {k: [0, 0] for k in ("ability", "ability_alpha", "item", "item_alpha", "set", "support")}
    checked = 0
    unseen_species = 0
    logloss: list[float] = []
    for sid, name, true_item, true_ability, true_moves in held:
        counter = train.get(sid)
        if not counter:
            unseen_species += 1
            continue
        checked += 1
        sheets = [set_belief.Sheet(i, a, n, mv, c) for (i, a, n, mv), c in counter.items()]
        belief = set_belief.SetBelief(
            species=name, sheets=sheets,
            possible_abilities=legal_abilities(reg, name),
            legal_items=sorted({s.item for s in sheets}),
            legal_moves=[], seen=sum(s.count for s in sheets))

        ranked = belief.ability()          # a dict, likeliest first
        tally["ability"][0] += next(iter(ranked), None) == true_ability
        tally["ability"][1] += 1
        alpha = sorted(legal_abilities(reg, name))
        tally["ability_alpha"][0] += bool(alpha) and alpha[0] == true_ability
        tally["ability_alpha"][1] += 1
        # The floor's job: every legal option keeps a share, so the truth is never impossible.
        tally["support"][0] += ranked.get(true_ability, 0.0) > 0
        tally["support"][1] += 1
        logloss.append(-math.log(max(ranked.get(true_ability, 0.0), 1e-9)))

        items = belief.item()
        tally["item"][0] += next(iter(items), None) == true_item
        tally["item"][1] += 1
        tally["item_alpha"][0] += bool(items) and sorted(items)[0] == true_item
        tally["item_alpha"][1] += 1

        best = belief.top()
        tally["set"][0] += bool(best) and (best.item, best.ability, best.moves) == (
            true_item, true_ability, true_moves)
        tally["set"][1] += 1

    rate = lambda k: round(tally[k][0] / max(tally[k][1], 1), 4)  # noqa: E731
    out = {
        "regulation": reg.id,
        "sheets": {"train": n_train, "heldout": n_held},
        "pokemon_checked": checked,
        "species_never_seen_in_training": unseen_species,
        "ability_top1": rate("ability"),
        "ability_top1_alphabetical": rate("ability_alpha"),
        "ability_logloss": round(sum(logloss) / max(len(logloss), 1), 4),
        "ability_in_support": rate("support"),
        "item_top1": rate("item"),
        "item_top1_alphabetical": rate("item_alpha"),
        "set_top1": rate("set"),
    }
    if args.json:
        print(json.dumps(out, indent=1))
        return
    for k, v in out.items():
        print(f"{k:34s} {v}")


if __name__ == "__main__":
    main()
