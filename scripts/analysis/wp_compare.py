"""Several WP models on one dataset's held-out sets, without touching their cards.

`vgc wp eval` records what it scores on the model's card, and on its baselines' cards too, so
pointing it at a served model with a newer dataset would overwrite the verdicts it was pinned on.
This reads models and writes only `data/analysis/<regulation>/wp_compare_<dataset>.json`:

  gates      `evaluate.gates` per model, from `evaluate_set` on every held-out set, as `vgc wp eval`
             would compute them (in-battle, closed-sheet, preview), in memory
  paired     each model against the first named, spectator rows from turn 1 on, per regime (open
             sheets: every held-out Bo3 game; closed: the ladder's), as `evaluate.paired_logloss`
             (resampling battles). Split by when the game was played: before the cache's edge of
             2026-09-20 (`fresh_corpus.json`), and after it, which no model trained before
             2026-10-05 has seen

    .venv/bin/python scripts/analysis/wp_compare.py --dataset wp-v1g wp-v1f-idp5 wp-v1f-ens5 wp-v1g-idp5
"""

from __future__ import annotations

import argparse
import gzip
import json
from typing import Any

import numpy as np

from vgc import paths

REGIMES = {"open": ("human_ots", "human_ots_team"), "closed": ("human_closed", "human_closed_team")}


def uploaded() -> dict[str, tuple[str, int]]:
    """Each cached replay's format (its folder: an id may carry a server prefix, `smogtours-…`) and
    upload time."""
    from vgc.meta.replays import REPLAYS

    out = {}
    for f in REPLAYS.glob("*/*.json.gz"):
        r = json.loads(gzip.decompress(f.read_bytes()))
        out[r["id"]] = (f.parent.name, r.get("uploadtime") or 0)
    return out


def main() -> None:
    from vgc.regulation import load_regulation
    from vgc.wp import evaluate, models
    from vgc.wp.dataset import FEATURES, load, merge
    from vgc.wp.models import symmetrize

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("versions", nargs="+", help="the first is the one the others are compared with")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--regulation", "-r", default="reg_mc")
    a = ap.parse_args()
    reg = load_regulation(a.regulation)
    base = FEATURES / reg.id / a.dataset
    data = {p.stem[len("eval_"):]: load(reg, a.dataset, p.stem) for p in sorted(base.glob("eval_*.npz"))}
    data = {"human_ots_all": merge([data[k] for k in REGIMES["open"] if k in data])} | data
    usage = evaluate.usage_rates(load(reg, a.dataset, "train"))
    edges = {fmt: v["edge"] for fmt, v in json.loads(
        (paths.DATA / "analysis" / reg.id / "fresh_corpus.json").read_text()).items()}
    when = uploaded()

    def fresh(names: np.ndarray) -> np.ndarray:
        """Per battle index: uploaded after its format's edge."""
        return np.array([n in when and when[n][1] > edges[when[n][0]] for n in names])

    regime_sets = {r: merge([data[k] for k in ks if k in data]) for r, ks in REGIMES.items()}
    spec_rows: dict[str, dict[str, Any]] = {}
    res: dict[str, Any] = {"dataset": a.dataset, "versions": a.versions, "gates": {}, "calibration": {},
                           "headline": {}, "paired": {}}
    constant = {name: evaluate.evaluate_set(models.load_model(reg.id, "constant"), d, usage) for name, d in data.items()}
    for v in a.versions:
        model = models.load_model(reg.id, v)
        results = {name: evaluate.evaluate_set(model, d, usage) for name, d in data.items()}
        g = evaluate.gates(results, {"constant": constant})
        res["gates"][v] = {k: (x.get("pass") if isinstance(x, dict) else x) for k, x in g.items()}
        res["calibration"][v] = {k: {f: g[k].get(f) for f in ("power", "rejected_buckets", "by_bucket")}
                                 for k in ("in_battle_ece", "closed_in_battle_ece") if isinstance(g.get(k), dict)}
        res["headline"][v] = evaluate.headline(results)
        spec_rows[v] = {}
        for r, d in regime_sets.items():
            p, _ = model.predict(d)
            s = symmetrize(d, p)
            m = (s["perspective"] == 0) & np.isin(evaluate._bucket(s["kind"], s["turn"]), evaluate.IN_BATTLE_BUCKETS)
            spec_rows[v][r] = {"p": s["p"][m], "y": s["y"][m], "battle": s["battle"][m],
                               "fresh": fresh(d["battle_names"])[s["battle"][m]]}
        print(v, json.dumps(res["gates"][v]), flush=True)
    first = a.versions[0]
    for v in a.versions[1:]:
        res["paired"][v] = {}
        for r in REGIMES:
            A, B = spec_rows[first][r], spec_rows[v][r]
            out = {}
            for label, mask in (("all", np.ones_like(A["fresh"])), ("before_edge", ~A["fresh"]),
                                ("after_edge", A["fresh"])):
                if mask.any():
                    out[label] = evaluate.paired_logloss(A["p"][mask], B["p"][mask], A["y"][mask], A["battle"][mask]) | {
                        "logloss": {first: round(float(evaluate.row_logloss(A["p"][mask], A["y"][mask]).mean()), 5),
                                    v: round(float(evaluate.row_logloss(B["p"][mask], B["y"][mask]).mean()), 5)}}
            res["paired"][v][r] = out
            print(v, "vs", first, r, json.dumps({k: (x["delta"], x["ci95"], x["battles"]) for k, x in out.items()}), flush=True)
    out_path = paths.DATA / "analysis" / reg.id / f"wp_compare_{a.dataset}.json"
    out_path.write_text(json.dumps(res, indent=1, default=float) + "\n")
    print(out_path)


if __name__ == "__main__":
    main()
