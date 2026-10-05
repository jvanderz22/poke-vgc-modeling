"""Does the assumed non-Speed spread move the doubles answer? (Loose end, PLAN-v5 step 6.)

The page's doubles answer guesses each Pokémon's spread at its commonest Speed and puts the rest
where the imputer does. `solver_vs_humans.py --bulk` re-solved the same held-out games with every
guessed spread rebuilt HP-first and then defences-first, the Speed kept, under the page's settings.
This joins the three runs game by game:

  shift     how far the answer moves (per game, |variant − baseline|)
  logloss   each run's as the page shows it (`doubles.temper`), the variants' paired against the
            baseline (cluster bootstrap by series)
  mean      the mean of the three solved answers, tempered: an answer integrated over the spreads

    .venv/bin/python scripts/analysis/bulk_spread_compare.py
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from vgc import paths

DIR = paths.DATA / "analysis" / "reg_mc"
RUNS = {"baseline": "solver_vs_humans_doubles_live_blend_boosts_tempered.json",
        "hp_first": "solver_vs_humans_doubles_live_blend_boosts_hp_first_tempered.json",
        "defences": "solver_vs_humans_doubles_live_blend_boosts_defences_tempered.json"}
OUT = DIR / "bulk_spread_doubles.json"
BOOTS = 4000


def _ll(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def compare(rows: dict[str, dict[str, dict[str, Any]]], ids: list[str], seed: int = 0) -> dict[str, Any]:
    from vgc.wp.doubles import temper

    base = rows["baseline"]
    y = np.array([float(base[i]["winner"] == "p1") for i in ids])
    groups = np.array([base[i]["group"] for i in ids])
    # The rows are saved as solved; the page shows them through `temper`. The mean is taken over
    # the solved values and then tempered, as the page would temper an answer integrated over spreads.
    raw = {k: np.array([rows[k][i]["engine"] for i in ids]) for k in rows}
    raw["mean"] = np.mean([raw[k] for k in RUNS], 0)
    p = {k: np.array([temper(float(x)) for x in v]) for k, v in raw.items()}
    uniq, inv = np.unique(groups, return_inverse=True)
    n_g = np.bincount(inv).astype(float)
    idx = np.random.default_rng(seed).integers(0, len(uniq), (BOOTS, len(uniq)))
    ll = {k: _ll(v, y) for k, v in p.items()}
    out: dict[str, Any] = {"games": len(ids), "groups": len(uniq),
                           "logloss": {k: round(float(v.mean()), 4) for k, v in ll.items()},
                           "model_logloss": round(float(_ll(np.array([base[i]["model"] for i in ids]), y).mean()), 4)}
    for k in ("hp_first", "defences", "mean"):
        d = ll[k] - ll["baseline"]
        b = np.bincount(inv, d)[idx].sum(1) / n_g[idx].sum(1)
        shift = np.abs(p[k] - p["baseline"])
        out[k] = {"logloss_minus_baseline": round(float(d.mean()), 4),
                  "ci95": [round(float(x), 4) for x in np.percentile(b, [2.5, 97.5])],
                  "shift_mean": round(float(shift.mean()), 4), "shift_p90": round(float(np.quantile(shift, .9)), 4),
                  "shift_over_0.05": round(float(np.mean(shift > .05)), 4),
                  "sides_flipped": int(np.sum((p[k] > .5) != (p["baseline"] > .5)))}
    return out


def main() -> None:
    rows = {k: {r["id"]: r for r in json.loads((DIR / f).read_text())["rows"] if r.get("engine") is not None}
            for k, f in RUNS.items()}
    ids = sorted(set.intersection(*(set(v) for v in rows.values())))
    kinds = sorted({rows["baseline"][i]["kind"] for i in ids})
    res = {"runs": RUNS, "all": compare(rows, ids)}
    for kind in kinds:
        res[kind] = compare(rows, [i for i in ids if rows["baseline"][i]["kind"] == kind])
    OUT.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
