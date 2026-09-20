"""Does heuristic self-play predict *human* outcomes?

Everything above L2 assumes that a heuristic-vs-heuristic win rate for two teams says something
about how those teams fare against each other in real play. That assumption has never been tested,
and `docs/phase4-findings.md` finding 2 is a reason to doubt it: a team-strength model fit on
self-play preview rows scores *worse than 0.5* on held-out human preview.

The test here is direct, and it uses data already on disk. Take the human battles whose two open
team sheets both resolve to the pool, simulate those same two teams against each other under the
heuristic, and ask whether the simulated win rate predicts who actually won the human game — log
loss and AUC against a 0.5 constant, with intervals.

Three things this measures that a Pearson correlation on 30 pairings could not:

  * **Ordering** (AUC). Scale-free: does the simulator rank the right team higher?
  * **Usability** (log loss vs the constant). A correctly-ordered but wildly overconfident
    predictor loses to 0.5 and should not be shown to a user as a number.
  * **The difference between them** (`recalibrated`). One parameter fitted out-of-fold on
    logit(sim WP) separates "no signal" from "signal, wrong scale". PLAN.md records that 67% of
    pairings land ≥85/15 under heuristic-vs-heuristic; if that spread is the only problem, the
    recalibrated log loss beats the constant while the raw one does not.

**Power.** The unit of independence is the *group* (a Bo3 series), not the game: the corpus has
3,372 distinct pairings over 3,431 groups, and only 44 pairings recur across two groups. Games in a
series share both teams *and both players*, so all intervals here are cluster bootstraps over
groups, and `groups` is reported next to `games` on every verdict.

**Population.** Team ids come from `|showteam|`, so every game scored here is open-sheet Bo3 ladder
play at a median rating near 1100 (finding 5). A pass is evidence about that population.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from vgc import paths
from vgc.regulation import Regulation

ANALYSIS = paths.DATA / "analysis"


@dataclass(frozen=True)
class Game:
    """One human battle whose two teams both resolve to the pool."""

    battle: str
    group: str  # Bo3 series id — the unit of independence
    fmt: str
    shard: str  # train / heldout_team / heldout_human
    a: str  # p1's team id
    b: str  # p2's team id
    a_won: bool
    ended_by: str
    rating: int | None

    @property
    def pairing(self) -> tuple[str, str]:
        return (self.a, self.b) if self.a <= self.b else (self.b, self.a)


def human_games(reg: Regulation) -> list[Game]:
    """Every human battle with a preview snapshot, both team ids, and a winner.

    Mirrors are dropped: the simulator's answer for a team against itself is 0.5 by construction,
    so they can only dilute the measurement."""
    from vgc.data.splits import read_shard

    root = paths.DATA / "snapshots" / reg.id / "human"
    out: list[Game] = []
    for shard in sorted(root.glob("*/*.jsonl.gz")):
        for rec in read_shard(shard):
            if rec.get("kind") != "preview" or rec["obs"].get("perspective") != "spectator":
                continue
            m, lab = rec["meta"], rec["label"]
            a, b = m["teams"].get("p1"), m["teams"].get("p2")
            if not a or not b or a == b or lab.get("winner") not in ("p1", "p2"):
                continue
            out.append(Game(rec["battle"], m.get("group") or rec["battle"], m["format"],
                            shard.stem.split(".")[0], a, b, lab["winner"] == "p1",
                            lab.get("ended_by") or "unknown", m.get("rating")))
    return out


def select_pairings(games: Sequence[Game], pairs: int = 0, seed: int = 0,
                    order: str = "random") -> list[tuple[str, str]]:
    """Which pairings to spend simulation on. `pairs <= 0` takes all of them, which is the default
    and is also the best use of a fixed battle budget — see below.

    `random` is a uniform sample of distinct pairings, unbiased over the population of matchups.
    `most_played` takes the pairings with the most human games, which sounds like more evidence per
    simulated battle and is not: a pairing only reaches three games by going to a third Bo3 set, so
    ranking by game count selects the series that were *close* — exactly the matchups on which any
    predictor looks worst.

    **Breadth beats depth here.** The budget is battles; the choice is how many pairings to cover
    and how deeply. The precision of the *outcome* side is set by the number of independent human
    groups covered, which is linear in pairings (3,372 pairings span 3,431 groups, and only 44
    recur). Simulating a pairing more deeply only shrinks the noise in the *predictor*, which costs
    an attenuation factor of √(s²/(s² + p(1-p)/n)) where s ≈ 0.29 is the spread of true simulated
    WP across pairings. That spread is wide — the heuristic produces lopsided matchups — so the
    attenuation is mild and falls off fast: 0.93 at n=15 against 0.98 at n=50. Power goes as
    attenuation × √groups, which makes all-pairings-at-15 worth about 1.7× the 1,000-at-50 split
    that PLAN-v2 sketched, for the same wall clock."""
    counts: dict[tuple[str, str], int] = {}
    for g in games:
        counts[g.pairing] = counts.get(g.pairing, 0) + 1
    keys = sorted(counts)
    if pairs <= 0 or pairs >= len(keys):
        return keys if order == "random" else sorted(keys, key=lambda k: (-counts[k], k))
    if order == "most_played":
        return sorted(keys, key=lambda k: (-counts[k], k))[:pairs]
    if order != "random":
        raise ValueError(f"unknown order {order!r}")
    return sorted(random.Random(seed).sample(keys, pairs))


def simulate_pairings(reg: Regulation, pairings: Sequence[tuple[str, str]], n: int, seed: int = 0,
                      workers: int = 6, run_id: str | None = None,
                      out_dir: Path | None = None) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    """Play each pairing `n` times, heuristic vs heuristic, sides alternating."""
    from vgc.meta.pool import load_pool
    from vgc.sim.selfplay import Matchup, load_battles, run, wilson

    pool = {t.id: t for t in load_pool(reg)}
    missing = [p for pair in pairings for p in pair if p not in pool]
    if missing:
        raise KeyError(f"{len(missing)} team ids are not in the current pool (e.g. {missing[0]}); rebuild with `vgc meta pool`")
    ms = [Matchup(pool[a].text, pool[b].text, "heuristic", "heuristic", a, b, swap_sides=bool(i % 2))
          for a, b in pairings for i in range(n)]
    run_id = run_id or f"simvalid-s{seed}-p{len(pairings)}-n{n}"
    summary = run(ms, reg_id=reg.id, seed=seed, workers=workers, run_id=run_id, out_dir=out_dir, keep_logs=False)
    rows = load_battles(Path(summary["out_dir"]))

    sim: dict[tuple[str, str], dict[str, Any]] = {}
    for k, pair in enumerate(pairings):
        block = rows[k * n : (k + 1) * n]
        ok = [r for r in block if "error" not in r and r["a_won"] is not None]
        wins = sum(r["a_won"] for r in ok)
        lo, hi = wilson(wins, len(ok)) if ok else (0.0, 1.0)
        sim[pair] = {"a": pair[0], "b": pair[1], "wins": wins, "n": len(ok),
                     # Laplace, because a 0/50 sweep is the simulator saying "at most ~1 in 50",
                     # not "never": an unsmoothed 0.0 would score infinite loss on one upset.
                     "wp": (wins + 1) / (len(ok) + 2) if ok else 0.5,
                     "wp_raw": round(wins / len(ok), 4) if ok else None,
                     "ci": [round(lo, 4), round(hi, 4)],
                     "turns": round(float(np.mean([r["turns"] for r in ok])), 1) if ok else None}
    return sim, summary


# --- scoring --------------------------------------------------------------------------

def _logloss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _auc(p: np.ndarray, y: np.ndarray) -> float | None:
    if len(np.unique(y)) < 2:
        return None
    from sklearn.metrics import roc_auc_score

    return float(roc_auc_score(y, p))


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _recalibrate(p: np.ndarray, y: np.ndarray, groups: np.ndarray, folds: int = 5,
                 seed: int = 0) -> np.ndarray:
    """Out-of-fold Platt scaling on logit(p), folds split by group.

    In-sample recalibration would hand any predictor a free win, so the scale is always fitted
    without the rows it is scored on, and the split is by group so a Bo3 series cannot be in both
    halves."""
    from sklearn.linear_model import LogisticRegression

    x = _logit(p).reshape(-1, 1)
    out = np.full(len(y), float(y.mean()))
    uniq = sorted(set(groups.tolist()))
    rng = random.Random(seed)
    rng.shuffle(uniq)
    assign = {g: i % folds for i, g in enumerate(uniq)}
    fold = np.array([assign[g] for g in groups])
    for f in range(folds):
        tr, te = fold != f, fold == f
        if not te.any() or len(np.unique(y[tr])) < 2:
            continue
        m = LogisticRegression(C=1e6, max_iter=1000).fit(x[tr], y[tr])
        out[te] = m.predict_proba(x[te])[:, 1]
    return out


def _bootstrap(p: np.ndarray, y: np.ndarray, groups: np.ndarray, reps: int = 2000,
               seed: int = 0) -> dict[str, list[float]]:
    """Cluster bootstrap over groups: resample whole Bo3 series, not individual games.

    Resampling games independently would treat the three games of one series as three independent
    reads on the same two teams and the same two players, and report an interval that is too
    narrow — the error the gate rules call out as stating a threshold without its power."""
    from sklearn.metrics import roc_auc_score

    order = np.argsort(groups, kind="stable")
    sizes = np.unique(groups, return_counts=True)[1]
    starts = np.concatenate([[0], np.cumsum(sizes)[:-1]])
    ngroups = len(sizes)
    rng = np.random.default_rng(seed)
    const = np.float64(0.5)
    d_ll, aucs = [], []
    for _ in range(reps):
        pick = rng.integers(0, ngroups, ngroups)
        counts = sizes[pick]
        # Ragged gather: expand each picked group's slice of `order` without a Python loop.
        base = np.repeat(starts[pick] - np.concatenate([[0], np.cumsum(counts)[:-1]]), counts)
        idx = order[base + np.arange(counts.sum())]
        pb, yb = p[idx], y[idx]
        d_ll.append(_logloss(pb, yb) - _logloss(np.full(len(yb), const), yb))
        if len(np.unique(yb)) > 1:
            aucs.append(float(roc_auc_score(yb, pb)))

    def ci(v: list[float]) -> list[float] | None:
        return [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)] if v else None

    return {"logloss_delta_95ci": ci(d_ll), "auc_95ci": ci(aucs)}


def _score(p: np.ndarray, y: np.ndarray, groups: np.ndarray, label: str, boots: int,
           seed: int) -> dict[str, Any]:
    const = _logloss(np.full(len(y), 0.5), y)
    ll = _logloss(p, y)
    out: dict[str, Any] = {
        "subset": label,
        "games": int(len(y)),
        "groups": int(len(set(groups.tolist()))),
        "base_rate": round(float(y.mean()), 4),
        "logloss": round(ll, 5),
        "logloss_constant": round(const, 5),
        "logloss_delta": round(ll - const, 5),
        "auc": round(a, 4) if (a := _auc(p, y)) is not None else None,
        "accuracy": round(float(((p > 0.5) == (y > 0.5)).mean()), 4),
        "brier": round(float(np.mean((p - y) ** 2)), 5),
        "mean_p": round(float(p.mean()), 4),
    }
    if boots and len(y) > 1:
        out |= _bootstrap(p, y, groups, reps=boots, seed=seed)
    return out


def score(games: Sequence[Game], sim: dict[tuple[str, str], dict[str, Any]], boots: int = 2000,
          seed: int = 0) -> dict[str, Any]:
    """Simulated win rate as a predictor of the human result, overall and by subset."""
    covered = [g for g in games if g.pairing in sim and sim[g.pairing]["n"] > 0]
    if not covered:
        raise ValueError("no human games are covered by the simulated pairings")
    # Orient each pairing's simulated WP to p1's team, so p predicts `a_won` directly.
    p = np.array([sim[g.pairing]["wp"] if sim[g.pairing]["a"] == g.a else 1 - sim[g.pairing]["wp"]
                  for g in covered])
    y = np.array([float(g.a_won) for g in covered])
    groups = np.array([g.group for g in covered])

    subsets: list[dict[str, Any]] = [_score(p, y, groups, "all", boots, seed)]
    for label, mask in (("ended_normal", np.array([g.ended_by == "normal" for g in covered])),
                        ("forfeit", np.array([g.ended_by == "forfeit" for g in covered])),
                        ("rated", np.array([g.rating is not None for g in covered])),
                        ("heldout_human", np.array([g.shard == "heldout_human" for g in covered]))):
        if mask.sum() > 1:
            subsets.append(_score(p[mask], y[mask], groups[mask], label, boots, seed))

    recal = _recalibrate(p, y, groups, seed=seed)
    rec = _score(recal, y, groups, "all (recalibrated out-of-fold)", boots, seed)
    spread = np.array([s["wp"] for s in sim.values() if s["n"]])
    return {
        "subsets": subsets,
        "recalibrated": rec,
        "simulated_wp_spread": {
            "pairings": int(len(spread)),
            "sd": round(float(spread.std()), 4),
            "quantiles": {q: round(float(np.quantile(spread, v)), 3)
                          for q, v in (("p05", .05), ("p25", .25), ("p50", .5), ("p75", .75), ("p95", .95))},
            "share_beyond_85_15": round(float(np.mean((spread >= .85) | (spread <= .15))), 4),
        },
        # Kept only as the bridge to `check-preview`'s old headline; the gate does not read it.
        "human_wp_correlation": (round(float(np.corrcoef(p, y)[0, 1]), 4)
                                 if p.std() > 0 and y.std() > 0 else None),
    }


def verdict(scored: dict[str, Any]) -> dict[str, Any]:
    """The fork in the road, stated as a gate.

    Two questions, because they have different consumers and only one of them forks the roadmap:

    * **`pass` — does the simulator order team strength the way reality does?** This is what Phase
      10 and Phase 11 actually consume: a matchup matrix is used to rank and to choose, and any
      scale error in it can be fitted out downstream with the one parameter `recalibrated` fits
      here. So the fork turns on the scale-free evidence — AUC whose interval clears 0.5, *and* an
      out-of-fold recalibrated log loss whose interval clears the constant.
    * **`usable_as_is` — can the raw simulated win rate be shown to a user as a probability?** A
      stricter question, and a separate one. It is also partly a question about the simulation
      budget rather than about the simulator: at `n` battles per pairing the estimate cannot be
      more confident than 1/(n+2) from the ends, so a failure here is not on its own a reason to
      abandon the simulator.

    Both are scored on games that were played out. Forfeits reward quit detection, which nothing
    computed before turn 1 can be doing, and finding 5 records that the WP model scores better on
    them — pooling would flatter this number the same way."""
    by = {s["subset"]: s for s in scored["subsets"]}
    normal = by.get("ended_normal") or by["all"]
    ci = normal.get("logloss_delta_95ci") or [None, None]
    auc_ci = normal.get("auc_95ci") or [None, None]
    rec = scored["recalibrated"]
    rec_ci = rec.get("logloss_delta_95ci") or [None, None]
    raw_beats = bool(ci[1] is not None and ci[1] < 0)
    orders = bool(auc_ci[0] is not None and auc_ci[0] > 0.5)
    rec_beats = bool(rec_ci[1] is not None and rec_ci[1] < 0)
    passed = orders and rec_beats
    return {
        "scored_on": normal["subset"],
        "games": normal["games"],
        "groups": normal["groups"],
        "orders_correctly": orders,
        "auc": normal["auc"],
        "auc_95ci": auc_ci,
        "recalibrated_beats_constant": rec_beats,
        "recalibrated_logloss_delta": rec["logloss_delta"],
        "recalibrated_logloss_delta_95ci": rec_ci,
        "usable_as_is": raw_beats,
        "logloss_delta": normal["logloss_delta"],
        "logloss_delta_95ci": ci,
        "pass": passed,
        "reading": ("the simulator orders team strength the way real games do: Phase 10's matrix "
                    "form lives, and self-play generation resumes with coverage over replication"
                    if passed else
                    "the ordering is there but only one of the two scale-free tests clears its "
                    "interval: re-run with more groups before building on it"
                    if orders or rec_beats else
                    "no usable team-strength signal from heuristic self-play: Phase 10's matrix "
                    "form is dead and so is any team-strength model distilled from it; Phase 9 "
                    "becomes the prerequisite and this check re-runs against that policy"),
    }


def validate_simulator(reg: Regulation, pairs: int = 0, n: int = 15, seed: int = 0,
                       workers: int = 6, order: str = "random", boots: int = 2000,
                       run_id: str | None = None) -> dict[str, Any]:
    games = human_games(reg)
    pairings = select_pairings(games, pairs, seed=seed, order=order)
    sim, summary = simulate_pairings(reg, pairings, n, seed=seed, workers=workers, run_id=run_id)
    scored = score(games, sim, boots=boots, seed=seed)
    return {
        "regulation": reg.id,
        "policy": "heuristic vs heuristic",
        "showdown_sha": reg.showdown_sha,
        "config": {"pairs": len(pairings), "battles_per_pairing": n, "seed": seed, "order": order,
                   "bootstrap_reps": boots},
        "corpus": {"human_games": len(games), "distinct_pairings": len({g.pairing for g in games}),
                   "groups": len({g.group for g in games}),
                   "pairings_in_2plus_groups": sum(
                       1 for v in _groups_per_pairing(games).values() if len(v) >= 2)},
        "run": {k: summary[k] for k in ("run_id", "battles", "errors", "ties", "mean_turns",
                                        "wall_seconds", "battles_per_second", "outcome_digest")},
        **scored,
        "verdict": verdict(scored),
        "pairings": [sim[k] | {"human_games": sum(1 for g in games if g.pairing == k)} for k in pairings],
    }


def _groups_per_pairing(games: Iterable[Game]) -> dict[tuple[str, str], set[str]]:
    out: dict[tuple[str, str], set[str]] = {}
    for g in games:
        out.setdefault(g.pairing, set()).add(g.group)
    return out


def result_path(reg: Regulation, tag: str = "") -> Path:
    return ANALYSIS / reg.id / f"sim_validity{'_' + tag if tag else ''}.json"


def save(reg: Regulation, result: dict[str, Any], tag: str = "") -> Path:
    path = result_path(reg, tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1) + "\n")
    return path
