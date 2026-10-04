"""The ceiling on Phase 6 (PLAN-v5 step 1): can anything that sees only the two teams pass it here?

Both self-play runs failed as bounded nulls against the AUC of 0.55 they were sized for. That
reads as the policy's failure only if something team-only can reach it on these games. The best
team-only predictor available is the WP model's preview head, trained on this corpus with both
teams in view, so it is scored here by Phase 6's own code (`vgc.sim.validity`), on held-out games
only, beside the two self-play runs on the same games:

  preview     the served model's P(p1 wins) at team preview, and the seed ensemble's
  heuristic   Phase 6's self-play (`sim_validity.json`), every pairing
  policy      the Phase 9 policy's (`sim_validity_ewp.json`), the 1,500 pairings it played

With a paired cluster bootstrap of the AUC differences on the games each pair shares.

And a bound on team *plus* player from the series themselves: how often a Bo3's second game goes
to whoever won the first. If the two games were independent draws at the series' own p, that
agreement is E[p² + (1 − p)²], so the spread of p around 0.5 is (agree − 0.5) / 2; an oracle that
knew p would score the AUC simulated below. Players adapt between games, which lowers the
agreement, so this is a rough bound and not a measurement of team strength.

    .venv/bin/python scripts/analysis/phase6_ceiling.py
"""

from __future__ import annotations

import json
import re
from typing import Any

import numpy as np

from vgc import paths
from vgc.sim.validity import ANALYSIS, _recalibrate, _score, human_games, verdict

HELD = ("heldout_human", "heldout_team")
OUT = ANALYSIS / "reg_mc" / "phase6_ceiling.json"
BOOTS = 2000


def preview_p(reg, games, version: str) -> dict[str, float]:
    """P(p1 wins) at team preview, per held-out battle, as the model's spectator head reads it."""
    from vgc.data.splits import read_shard
    from vgc.wp.models import predict_records
    from vgc.wp.tools import _load

    want = {g.battle for g in games}
    recs = []
    for shard in sorted((paths.DATA / "snapshots" / reg.id / "human").glob("*/*.jsonl.gz")):
        if shard.stem.split(".")[0] not in HELD:
            continue
        recs += [r for r in read_shard(shard) if r.get("kind") == "preview"
                 and r["obs"].get("perspective") == "spectator" and r["battle"] in want]
    model, fz = _load(reg, version)
    return {r["battle"]: float(p) for r, p in zip(recs, predict_records(model, recs, fz))}


def sim_p(name: str) -> dict[tuple[str, str], dict[str, Any]]:
    d = json.loads((ANALYSIS / "reg_mc" / name).read_text())
    return {(x["a"], x["b"]) if x["a"] <= x["b"] else (x["b"], x["a"]): x for x in d["pairings"]}


def oriented(sim, g) -> float | None:
    x = sim.get(g.pairing)
    if not x or not x["n"]:
        return None
    return x["wp"] if x["a"] == g.a else 1 - x["wp"]


def scored(p: np.ndarray, y: np.ndarray, groups: np.ndarray, label: str) -> dict[str, Any]:
    s = _score(p, y, groups, label, BOOTS, 0)
    rec = _score(_recalibrate(p, y, groups, seed=0), y, groups, label + " (recalibrated)", BOOTS, 0)
    v = verdict({"subsets": [s | {"subset": "ended_normal"}], "recalibrated": rec})
    keep = ("games", "groups", "auc", "auc_95ci", "logloss_delta", "logloss_delta_95ci")
    return {k: s[k] for k in keep} | {"recalibrated_delta": rec["logloss_delta"],
                                      "recalibrated_delta_95ci": rec.get("logloss_delta_95ci"),
                                      "pass": v["pass"]}


def paired_auc(a: np.ndarray, b: np.ndarray, y: np.ndarray, groups: np.ndarray, seed: int = 0) -> dict[str, Any]:
    """AUC(a) − AUC(b) on the same games, resampling whole series."""
    from sklearn.metrics import roc_auc_score

    uniq, inv = np.unique(groups, return_inverse=True)
    members = [np.nonzero(inv == i)[0] for i in range(len(uniq))]
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(BOOTS):
        idx = np.concatenate([members[i] for i in rng.integers(0, len(uniq), len(uniq))])
        if len(np.unique(y[idx])) > 1:
            diffs.append(roc_auc_score(y[idx], a[idx]) - roc_auc_score(y[idx], b[idx]))
    point = roc_auc_score(y, a) - roc_auc_score(y, b)
    return {"games": int(len(y)), "groups": int(len(uniq)), "auc_diff": round(float(point), 4),
            "auc_diff_95ci": [round(float(np.percentile(diffs, q)), 4) for q in (2.5, 97.5)]}


def stacked(base: np.ndarray, extra: np.ndarray, y: np.ndarray, groups: np.ndarray, folds: int = 5,
            seed: int = 0) -> dict[str, Any]:
    """Does self-play add to the preview head? (PLAN-v5 step 3b.) A logistic over the preview logit
    alone, and over it and the self-play logit, both fitted out of fold by series on these held-out
    games (the preview head trained on the training series, so a stack fitted there would over-trust
    it), and their per-game log losses compared by a cluster bootstrap."""
    import random

    from sklearn.linear_model import LogisticRegression

    from vgc.sim.validity import _logit

    uniq = sorted(set(groups.tolist()))
    random.Random(seed).shuffle(uniq)
    fold = np.array([{g: i % folds for i, g in enumerate(uniq)}[g] for g in groups])
    xs = {"preview": _logit(base).reshape(-1, 1), "preview+selfplay": np.stack([_logit(base), _logit(extra)], 1)}
    oof = {k: np.zeros(len(y)) for k in xs}
    coef = {k: [] for k in xs}
    for f in range(folds):
        tr, te = fold != f, fold == f
        for k, x in xs.items():
            m = LogisticRegression(C=1e6, max_iter=1000).fit(x[tr], y[tr])
            oof[k][te] = m.predict_proba(x[te])[:, 1]
            coef[k].append(m.coef_[0].round(3).tolist())
    ll = {k: -(y * np.log(p) + (1 - y) * np.log(1 - p)) for k, p in oof.items()}
    d = ll["preview+selfplay"] - ll["preview"]
    uniq_g, inv = np.unique(groups, return_inverse=True)
    s_d = np.bincount(inv, d)
    n_g = np.bincount(inv).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(uniq_g), (BOOTS, len(uniq_g)))
    b = s_d[idx].sum(1) / n_g[idx].sum(1)
    return {"games": int(len(y)), "groups": int(len(uniq_g)),
            "logloss": {k: round(float(v.mean()), 5) for k, v in ll.items()},
            "selfplay_adds": round(float(d.mean()), 5),
            "selfplay_adds_95ci": [round(float(x), 5) for x in np.percentile(b, [2.5, 97.5])],
            "coefficients_by_fold": coef}


def series_bound(games, normal_only: bool, seed: int = 0) -> dict[str, Any]:
    """How often game 2 of a series goes to game 1's winner, and the oracle that implies."""
    from sklearn.metrics import roc_auc_score

    by: dict[str, list] = {}
    for g in games:
        if g.group != g.battle:
            by.setdefault(g.group, []).append(g)
    num = lambda g: int(re.findall(r"\d+", g.battle)[-1])
    agree = []
    for gs in by.values():
        gs = sorted(gs, key=num)
        if len(gs) < 2 or (normal_only and not all(g.ended_by == "normal" for g in gs[:2])):
            continue
        w = [g.a if g.a_won else g.b for g in gs[:2]]
        agree.append(float(w[0] == w[1]))
    a = np.array(agree)
    rng = np.random.default_rng(seed)
    boots = [a[rng.integers(0, len(a), len(a))].mean() for _ in range(BOOTS)]
    rate = float(a.mean())
    var = max((rate - 0.5) / 2, 0.0)
    out = {"series": len(a), "agree": round(rate, 4),
           "agree_95ci": [round(float(np.percentile(boots, q)), 4) for q in (2.5, 97.5)],
           "sd_of_series_p": round(var ** 0.5, 4)}
    if var > 0:
        # p ~ Beta(α, α) with that variance around 0.5; the oracle predicts p and the game draws y.
        alpha = (1 / (4 * var) - 1) / 2
        p = rng.beta(alpha, alpha, 200_000)
        y = (rng.random(len(p)) < p).astype(float)
        ll = -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))
        out |= {"oracle_auc": round(float(roc_auc_score(y, p)), 4),
                "oracle_logloss_delta": round(float(ll - np.log(2)), 4)}
    return out


def main() -> None:
    from vgc.regulation import load_regulation
    from vgc.wp.models import in_battle_version, served

    reg = load_regulation("reg_mc")
    games = human_games(reg)
    held = [g for g in games if g.shard in HELD and g.ended_by == "normal"]
    versions = {"preview": served(reg.id, "bring"), "preview_ensemble": in_battle_version(reg.id, "closed")}
    preds = {k: preview_p(reg, held, v) for k, v in versions.items()}
    sims = {"heuristic": sim_p("sim_validity.json"), "policy": sim_p("sim_validity_ewp.json")}

    cols: dict[str, dict[str, float]] = {k: v for k, v in preds.items()}
    for k, sim in sims.items():
        cols[k] = {g.battle: q for g in held if (q := oriented(sim, g)) is not None}
    y_of = {g.battle: float(g.a_won) for g in held}
    grp = {g.battle: g.group for g in held}

    def arrays(names: list[str]):
        bs = sorted(b for b in y_of if all(b in cols[n] for n in names))
        return ([np.array([cols[n][b] for b in bs]) for n in names], np.array([y_of[b] for b in bs]),
                np.array([grp[b] for b in bs]))

    alone = {}
    for n in cols:
        (p,), y, gr = arrays([n])
        alone[n] = scored(p, y, gr, n)
    paired = {}
    for a, b in (("preview", "heuristic"), ("preview", "policy"), ("preview_ensemble", "policy"),
                 ("policy", "heuristic")):
        (pa, pb), y, gr = arrays([a, b])
        paired[f"{a} - {b}"] = paired_auc(pa, pb, y, gr) | {
            a: scored(pa, y, gr, a)["auc"], b: scored(pb, y, gr, b)["auc"]}
    stack = {}
    for sp in ("heuristic", "policy"):
        (pa, pb), y, gr = arrays(["preview", sp])
        stack[sp] = stacked(pa, pb, y, gr)
    res = {"held_out_shards": list(HELD), "scored_on": "held-out games that ended normally",
           "versions": versions, "alone": alone, "paired": paired, "stacked": stack,
           "series_bound": {"ended_normal": series_bound(games, True), "all": series_bound(games, False)}}
    OUT.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
