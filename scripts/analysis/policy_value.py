"""The policy's value of a position as the number above two a side (PLAN-v5 step 4's candidate).

The EWP gate found that the policy's value of what was played, stacked with the WP model, predicts
human results better than the model alone (phase9-findings). The page's number above two a side
is the model's. This asks whether the policy's own value of the position (the people reading's
EWP of its own choice, what it would play) should join it, gated as the doubles engine was:

  fitted     a logistic over the WP logit and the policy-value logit, on the WP model's validation
             games (training games it did not train on), from each player's seat
  scored     on held-out games, against the WP model alone: per-row log loss, cluster bootstrap by
             series, overall and per state kind (a 4v3 and a 3v4 are one state seen from either
             seat, so they are pooled). Where the whole interval is below zero, the combined
             number leads there
  timed      each position's solve, on one core: what a page answer would cost

The positions are `policy_vs_people.positions`: a player's own back as the replay says it brought
(the page would need the player to mark their four), theirs as the heaviest guess.

    PYTHONPATH=scripts/analysis .venv/bin/python scripts/analysis/policy_value.py --games 800

`--confirm` then tests one hypothesis stated after that run, on the held-out games it did not touch:
"after the first faint, with more than two a side, the combined number beats the model". The
combination is refitted from the saved validation positions, so it is the same fit, frozen.

    PYTHONPATH=scripts/analysis .venv/bin/python scripts/analysis/policy_value.py --confirm
"""

from __future__ import annotations

import argparse
import json
import random
import time
from multiprocessing import Pool
from typing import Any

import numpy as np

from vgc import paths

OUT = paths.DATA / "analysis" / "reg_mc" / "policy_value.json"
CONFIRM = paths.DATA / "analysis" / "reg_mc" / "policy_value_confirm.json"
ROWS = paths.DATA / "analysis" / "reg_mc" / "policy_value_rows.json"


def _game(job: tuple[dict[str, Any], str]) -> list[dict[str, Any]]:
    import policy_vs_people as PV
    from vgc.data.snapshots import human_snapshots
    from vgc.policy import ewp as E
    from vgc.policy import view as V

    replay, split = job
    reg = PV._W["reg"]
    search = {**V.SEARCH, "side_k": E.SIDE_K, "root_detail": True}
    recs, rows = None, []
    for x in PV.positions(replay, split):
        sid, them = x["sid"], x["them"]
        t0 = time.process_time()
        t1 = time.time()
        r = PV._solve(V.compose(reg, x["sets"], x["f"], search))
        wall = time.time() - t1
        if r is None or not r.get("matrix"):
            continue
        M = np.array(r["matrix"], float)
        people = (r.get("people") or {}).get(them)
        if sid == "p2":
            M = 1 - M.T
        scores = None if people is None or any(v is None for v in people) else people
        ewp = np.asarray(E.combine([(1.0, M, scores, None)], "people", 4)["ewp"], float)
        if recs is None:
            recs = {rc["obs"]["turn"]: rc for rc in human_snapshots(replay, reg)
                    if rc["kind"] == "turn" and rc["obs"]["perspective"] == "spectator"}
        rec = recs.get(x["turn"])
        if rec is None:
            continue
        a, b = x["kind"].split("v")
        rows.append({"id": x["id"], "group": x["group"], "split": split, "kind": x["kind"],
                     "pooled": f"{max(a, b)}v{min(a, b)}" if a != b else x["kind"], "sid": sid, "turn": x["turn"],
                     "won": float(x["winner"] == sid), "value": float(ewp.max()), "seconds": round(wall, 3),
                     "cpu": round(time.process_time() - t0, 3), "_rec": rec})
    return rows


def _ll(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def _paired(d: np.ndarray, groups: np.ndarray, boots: int = 4000, seed: int = 0) -> dict[str, Any]:
    uniq, inv = np.unique(groups, return_inverse=True)
    s, n = np.bincount(inv, d), np.bincount(inv).astype(float)
    idx = np.random.default_rng(seed).integers(0, len(uniq), (boots, len(uniq)))
    b = s[idx].sum(1) / n[idx].sum(1)
    ci = [round(float(x), 5) for x in np.percentile(b, [2.5, 97.5])]
    return {"positions": int(len(d)), "groups": int(len(uniq)), "delta": round(float(d.mean()), 5), "ci95": ci,
            "verdict": "combined better" if ci[1] < 0 else "model better" if ci[0] > 0 else "not distinguishable"}


def confirm(fit_rows: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The stated hypothesis on fresh held-out positions: every kind but 4v4, pooled."""
    from sklearn.linear_model import LogisticRegression

    from vgc.sim.validity import _logit

    def arrays(rs):
        return (np.stack([_logit(np.array([r["wp"] for r in rs])), _logit(np.array([r["value"] for r in rs]))], 1),
                np.array([r["won"] for r in rs]), np.array([r["group"] for r in rs]))

    X, y, _ = arrays(fit_rows)
    both = LogisticRegression(C=1e6, max_iter=1000).fit(X, y)
    alone = LogisticRegression(C=1e6, max_iter=1000).fit(X[:, :1], y)

    def score(rs):
        Xt, yt, gt = arrays(rs)
        return _paired(_ll(both.predict_proba(Xt)[:, 1], yt) - _ll(alone.predict_proba(Xt[:, :1])[:, 1], yt), gt)

    after = [r for r in rows if r["pooled"] != "4v4"]
    return {"hypothesis": "after the first faint, more than two a side: combined beats the model",
            "coef": [round(float(c), 3) for c in both.coef_[0]],
            "after_first_faint": score(after), "4v4": score([r for r in rows if r["pooled"] == "4v4"]),
            "all": score(rows),
            "by_kind": {k: score([r for r in after if r["pooled"] == k]) for k in sorted({r["pooled"] for r in after})
                        if sum(r["pooled"] == k for r in after) >= 50}}


def report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from sklearn.linear_model import LogisticRegression

    from vgc.sim.validity import _logit

    def arrays(rs):
        return (np.stack([_logit(np.array([r["wp"] for r in rs])), _logit(np.array([r["value"] for r in rs]))], 1),
                np.array([r["won"] for r in rs]), np.array([r["group"] for r in rs]))

    fit_rows = [r for r in rows if r["split"] == "validation"]
    test = [r for r in rows if r["split"] == "heldout_human"]
    X, y, _ = arrays(fit_rows)
    both = LogisticRegression(C=1e6, max_iter=1000).fit(X, y)
    alone = LogisticRegression(C=1e6, max_iter=1000).fit(X[:, :1], y)

    def score(rs):
        Xt, yt, gt = arrays(rs)
        d = _ll(both.predict_proba(Xt)[:, 1], yt) - _ll(alone.predict_proba(Xt[:, :1])[:, 1], yt)
        raw = _ll(1 / (1 + np.exp(-Xt[:, 0])), yt)
        return _paired(d, gt) | {"model_logloss": round(float(raw.mean()), 4)}

    kinds = sorted({r["pooled"] for r in test}, key=lambda k: (-sum(map(int, k.split("v"))), k))
    secs = np.array([r["seconds"] for r in rows])
    return {
        "fitted_on": {"positions": len(fit_rows), "groups": len({r["group"] for r in fit_rows}),
                      "coef": [round(float(c), 3) for c in both.coef_[0]], "intercept": round(float(both.intercept_[0]), 3)},
        "all": score(test),
        "by_kind": {k: score([r for r in test if r["pooled"] == k]) for k in kinds
                    if sum(r["pooled"] == k for r in test) >= 50},
        "solve_seconds": {"median": round(float(np.median(secs)), 2), "p90": round(float(np.quantile(secs, .9)), 2),
                          "p99": round(float(np.quantile(secs, .99)), 2), "workers_at_once": "see settings"},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", type=int, default=800, help="held-out games (all validation games are used)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--confirm", action="store_true", help="the held-out games after the first --games, scored "
                    "on the stated hypothesis with the saved validation fit")
    a = ap.parse_args()
    import policy_vs_people as PV
    from vgc.data.snapshots import replay_group
    from vgc.data.splits import load_rules
    from vgc.meta import pool, replays
    from vgc.regulation import load_regulation
    from vgc.wp.dataset import VAL_RATE, _is_val
    from vgc.wp.models import OPEN, in_battle_version, predict_records
    from vgc.wp.tools import _load

    reg = load_regulation("reg_mc")
    rules = load_rules(reg)
    reps = [r for fmt in pool.formats_for(reg) for r in replays.cached(fmt) if "|showteam|" in r["log"]]
    split = {r["id"]: rules.split_of("human", r["id"], [], replay_group(r)) for r in reps}
    held = [r for r in reps if split[r["id"]] == "heldout_human"]
    random.Random(a.seed).shuffle(held)
    val = [r for r in reps if split[r["id"]] == "train" and _is_val(replay_group(r), VAL_RATE["human"])]
    jobs = [(r, "validation") for r in val] + [(r, "heldout_human") for r in held[:a.games]]
    if a.confirm:
        jobs = [(r, "heldout_human") for r in held[a.games:]]
    with Pool(a.workers, initializer=PV._init) as p:
        rows = [x for rs in p.imap_unordered(_game, jobs, chunksize=2) for x in rs]
    version = in_battle_version(reg.id, OPEN)
    model, fz = _load(reg, version)
    preds = predict_records(model, [r.pop("_rec") for r in rows], fz)
    for r, p1 in zip(rows, preds):
        r["wp"] = float(p1) if r["sid"] == "p1" else 1 - float(p1)
    if a.confirm:
        fit_rows = [r for r in json.loads(ROWS.read_text()) if r["split"] == "validation"]
        res = {"settings": {"heldout_games": len(jobs), "after_the_first": a.games, "seed": a.seed,
                            "wp_model": version}, **confirm(fit_rows, rows)}
        CONFIRM.with_name("policy_value_confirm_rows.json").write_text(json.dumps(rows) + "\n")
        CONFIRM.write_text(json.dumps(res, indent=1) + "\n")
        print(json.dumps(res, indent=1))
        return
    ROWS.write_text(json.dumps(rows) + "\n")
    res = {"settings": {"heldout_games": a.games, "validation_games": len(val), "seed": a.seed, "wp_model": version,
                        "workers": a.workers}, **report(rows)}
    OUT.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
