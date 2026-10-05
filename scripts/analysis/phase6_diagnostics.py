"""Why self-play misses: PLAN-v5 step 2's diagnostics (a)-(c), on the battles already played.

(a) Team level instead of pairing level. A Bradley-Terry strength per team is fitted from each
    self-play run (every simulated battle a team played, against every opponent) and used to
    predict human games, beside the per-pairing win rate Phase 6 scores. The run's pooled strength
    also reaches pairings it never simulated. As references, a strength per team and per player is
    fitted from the human training games and scored on held-out series, and the share of teams one
    player alone used says how far a team's human strength is its player's.
(b) Where the two disagree. What each archetype is worth, in logit units, to human results
    (games, cluster bootstrap by series) and to self-play (pairings, bootstrap by pairing), both as
    a logistic on which team has it (A has it minus B has it). An archetype self-play values and
    humans do not, or the reverse, is a candidate for what the policy cannot play.
(c) The players, held fixed. On games where both players' pre-game ratings are in the log: the
    rating difference alone against the rating difference plus the self-play logit, cross-fitted by
    series, per-game log loss compared by a cluster bootstrap.

All scored on games that ended normally. Result: data/analysis/reg_mc/phase6_diagnostics.json.

    .venv/bin/python scripts/analysis/phase6_diagnostics.py
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from vgc.sim.validity import ANALYSIS, _logit, _score, human_games

OUT = ANALYSIS / "reg_mc" / "phase6_diagnostics.json"
BOOTS = 1000
RUNS = {"heuristic": "sim_validity.json", "policy": "sim_validity_ewp.json"}

SETUP = {"swordsdance", "nastyplot", "dragondance", "calmmind", "bulkup", "quiverdance", "bellydrum",
         "shellsmash", "coil", "irondefense", "shiftgear", "victorydance", "tidyup", "growth", "workup",
         "geomancy", "curse", "agility", "rockpolish", "autotomize", "cosmicpower", "acidarmor"}
WEATHER_ABILITIES = {"drought", "drizzle", "sandstream", "snowwarning", "orichalcumpulse", "desolateland",
                     "primordialsea", "deltastream"}
WEATHER_MOVES = {"sunnyday", "raindance", "sandstorm", "snowscape", "chillyreception"}


def archetypes(reg, text: str) -> dict[str, bool]:
    """What a team is built around, read from its sheet by rule."""
    from vgc.battle.entry import from_team
    from vgc.regulation import to_id

    sets = from_team(reg, text)
    moves = {to_id(m) for s in sets for m in s["moves"]}
    abilities = {to_id(s.get("ability") or "") for s in sets}
    mega = any((reg.dex.get_item(to_id(s.get("item") or "")) or {}).get("megaStone") for s in sets)
    return {"trick_room": "trickroom" in moves, "tailwind": "tailwind" in moves,
            "weather": bool(abilities & WEATHER_ABILITIES or moves & WEATHER_MOVES),
            "setup": bool(moves & SETUP), "fake_out": "fakeout" in moves,
            "redirection": bool(moves & {"followme", "ragepowder", "spotlight"}),
            "intimidate": "intimidate" in abilities, "mega": bool(mega)}


def _folds(groups: np.ndarray, k: int = 5, seed: int = 0) -> np.ndarray:
    uniq = sorted(set(groups.tolist()))
    random.Random(seed).shuffle(uniq)
    at = {g: i % k for i, g in enumerate(uniq)}
    return np.array([at[g] for g in groups])


def _cluster_ci(v: np.ndarray, groups: np.ndarray, seed: int = 0) -> list[float]:
    uniq, inv = np.unique(groups, return_inverse=True)
    s, n = np.bincount(inv, v), np.bincount(inv).astype(float)
    idx = np.random.default_rng(seed).integers(0, len(uniq), (BOOTS * 2, len(uniq)))
    b = s[idx].sum(1) / n[idx].sum(1)
    return [round(float(x), 5) for x in np.percentile(b, [2.5, 97.5])]


def _ll(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def short(s: dict[str, Any]) -> dict[str, Any]:
    return {k: s[k] for k in ("games", "groups", "auc", "auc_95ci", "logloss_delta", "logloss_delta_95ci")}


# --- (a) team level ---------------------------------------------------------------------------

def bradley_terry(rows: list[tuple[str, str, float, float]], c: float = 1.0) -> dict[str, float]:
    """A strength per name from (a, b, a's wins, battles), an L2 penalty of 1/c on the strengths."""
    from scipy.sparse import csr_matrix
    from sklearn.linear_model import LogisticRegression

    names = sorted({x for a, b, _, _ in rows for x in (a, b)})
    at = {t: i for i, t in enumerate(names)}
    r, cidx, v, y, w = [], [], [], [], []
    for i, (a, b, wins, n) in enumerate(rows):
        for out, wt in ((1.0, wins), (0.0, n - wins)):
            if wt <= 0:
                continue
            k = len(y)
            r += [k, k]
            cidx += [at[a], at[b]]
            v += [1.0, -1.0]
            y.append(out)
            w.append(wt)
    X = csr_matrix((v, (r, cidx)), shape=(len(y), len(names)))
    m = LogisticRegression(C=c, fit_intercept=False, max_iter=2000).fit(X, np.array(y), sample_weight=np.array(w))
    return dict(zip(names, m.coef_[0]))


def team_level(games, sims, players) -> dict[str, Any]:
    normal = [g for g in games if g.ended_by == "normal"]
    out: dict[str, Any] = {}
    for run, sim in sims.items():
        strength = bradley_terry([(x["a"], x["b"], x["wins"], x["n"]) for x in sim.values()])
        both = [g for g in normal if g.a in strength and g.b in strength]
        simulated = [g for g in both if g.pairing in sim]
        y = lambda gs: np.array([float(g.a_won) for g in gs])
        grp = lambda gs: np.array([g.group for g in gs])
        bt = lambda gs: 1 / (1 + np.exp(-np.array([strength[g.a] - strength[g.b] for g in gs])))
        pair = np.array([sim[g.pairing]["wp"] if sim[g.pairing]["a"] == g.a else 1 - sim[g.pairing]["wp"]
                         for g in simulated])
        unsim = [g for g in both if g.pairing not in sim]
        out[run] = {"teams": len(strength),
                    "pairing_level": short(_score(pair, y(simulated), grp(simulated), "pairing", BOOTS, 0)),
                    "team_level_same_games": short(_score(bt(simulated), y(simulated), grp(simulated), "bt", BOOTS, 0))}
        if len(unsim) > 50:
            out[run]["team_level_unsimulated_pairings"] = short(_score(bt(unsim), y(unsim), grp(unsim), "bt", BOOTS, 0))
    # References from human games: strengths fitted on training series, scored on held-out series.
    train = [g for g in games if g.shard == "train"]
    size = Counter(g.group for g in train)
    held = [g for g in normal if g.shard == "heldout_human"]
    for name, key in (("human_team_strength", lambda g, s: (g.a, g.b)),
                      ("human_player_strength", lambda g, s: players.get(g.battle))):
        rows = [(*k, float(g.a_won) / size[g.group], 1.0 / size[g.group]) for g in train if (k := key(g, None))]
        strength = bradley_terry(rows)
        hs = [g for g in held if (k := key(g, None)) and k[0] in strength and k[1] in strength]
        p = 1 / (1 + np.exp(-np.array([strength[key(g, None)[0]] - strength[key(g, None)[1]] for g in hs])))
        out[name] = short(_score(p, np.array([float(g.a_won) for g in hs]), np.array([g.group for g in hs]),
                                 name, BOOTS, 0))
    # How far a team's results are its player's: of the teams in two or more series, how many had
    # one player only.
    who: dict[str, set] = defaultdict(set)
    series: dict[str, set] = defaultdict(set)
    for g in games:
        pl = players.get(g.battle)
        if pl:
            who[g.a].add(pl[0])
            who[g.b].add(pl[1])
        series[g.a].add(g.group)
        series[g.b].add(g.group)
    multi = [t for t in series if len(series[t]) >= 2 and t in who]
    out["teams_in_two_or_more_series"] = len(multi)
    out["of_those_one_player_only"] = round(float(np.mean([len(who[t]) == 1 for t in multi])), 4)
    return out


# --- (b) archetypes ---------------------------------------------------------------------------

def archetype_values(games, sims, feats) -> dict[str, Any]:
    from sklearn.linear_model import LogisticRegression

    names = list(next(iter(feats.values())))
    diff = lambda a, b: np.array([float(feats[a][k]) - float(feats[b][k]) for k in names])
    rng = np.random.default_rng(0)

    def fit(X, y, w):
        return LogisticRegression(C=1e4, max_iter=2000).fit(X, y, sample_weight=w).coef_[0]

    def boot(X, y, w, unit):
        uniq, inv = np.unique(unit, return_inverse=True)
        members = [np.nonzero(inv == i)[0] for i in range(len(uniq))]
        cs = []
        for _ in range(BOOTS // 2):
            idx = np.concatenate([members[i] for i in rng.integers(0, len(uniq), len(uniq))])
            cs.append(fit(X[idx], y[idx], w[idx]))
        return np.percentile(cs, [2.5, 97.5], axis=0), np.std(cs, axis=0)

    out: dict[str, Any] = {"features": names, "prevalence": {
        k: round(float(np.mean([feats[t][k] for t in feats])), 3) for k in names}}
    hs = [g for g in games if g.ended_by == "normal" and g.a in feats and g.b in feats]
    X = np.array([diff(g.a, g.b) for g in hs])
    y = np.array([float(g.a_won) for g in hs])
    coef, (ci, se) = fit(X, y, np.ones(len(y))), boot(X, y, np.ones(len(y)), np.array([g.group for g in hs]))
    human = (coef, se)
    out["humans"] = {"games": len(hs), **{k: {"coef": round(float(c), 3), "ci95": [round(float(lo), 3), round(float(hi), 3)]}
                                         for k, c, lo, hi in zip(names, coef, ci[0], ci[1])}}
    for run, sim in sims.items():
        ps = [x for x in sim.values() if x["a"] in feats and x["b"] in feats and x["n"]]
        Xs = np.array([diff(x["a"], x["b"]) for x in ps for _ in (0, 1)])
        ys = np.array([v for _ in ps for v in (1.0, 0.0)])
        ws = np.array([v for x in ps for v in (x["wins"], x["n"] - x["wins"])], float)
        unit = np.array([i for i in range(len(ps)) for _ in (0, 1)])
        coef, (ci, se) = fit(Xs, ys, ws), boot(Xs, ys, ws, unit)
        out[run] = {"pairings": len(ps), **{k: {"coef": round(float(c), 3), "ci95": [round(float(lo), 3), round(float(hi), 3)]}
                                           for k, c, lo, hi in zip(names, coef, ci[0], ci[1])}}
        # Humans minus self-play, per archetype. The two bootstraps are independent (games and
        # pairings), so the variances add; Bonferroni over the archetypes at 0.05.
        from scipy.stats import norm

        crit = float(norm.ppf(1 - 0.025 / len(names)))
        d, sd = human[0] - coef, np.sqrt(human[1] ** 2 + se ** 2)
        out[run]["humans_minus_selfplay"] = {
            k: {"diff": round(float(x), 3), "z": round(float(x / e), 2), "bonferroni": bool(abs(x / e) > crit)}
            for k, x, e in zip(names, d, sd)}
    return out


# --- (c) the players held fixed ---------------------------------------------------------------

def ratings_and_players(reg) -> tuple[dict[str, tuple[int, int]], dict[str, tuple[str, str]]]:
    """Each battle's pre-game ratings (p1, p2) where the log has both, and its two player names."""
    from vgc.meta import pool, replays
    from vgc.regulation import to_id

    rated, players = {}, {}
    pat = re.compile(r"\|raw\|(.+?)'s rating: (\d+) &rarr;")
    for fmt in pool.formats_for(reg):
        for r in replays.cached(fmt):
            names = r.get("players") or []
            if len(names) != 2:
                continue
            players[r["id"]] = (to_id(names[0]), to_id(names[1]))
            got = {to_id(n): int(x) for n, x in pat.findall(r["log"])}
            if all(to_id(n) in got for n in names):
                rated[r["id"]] = (got[to_id(names[0])], got[to_id(names[1])])
    return rated, players


def players_fixed(games, sims, rated) -> dict[str, Any]:
    from sklearn.linear_model import LogisticRegression

    out: dict[str, Any] = {}
    for run, sim in sims.items():
        gs = [g for g in games if g.ended_by == "normal" and g.battle in rated and g.pairing in sim
              and sim[g.pairing]["n"]]
        y = np.array([float(g.a_won) for g in gs])
        groups = np.array([g.group for g in gs])
        r = np.array([(rated[g.battle][0] - rated[g.battle][1]) / 100 for g in gs])
        s = _logit(np.array([sim[g.pairing]["wp"] if sim[g.pairing]["a"] == g.a else 1 - sim[g.pairing]["wp"] for g in gs]))
        fold = _folds(groups)
        oof = {"rating": np.zeros(len(y)), "rating+selfplay": np.zeros(len(y))}
        xs = {"rating": r.reshape(-1, 1), "rating+selfplay": np.stack([r, s], 1)}
        for f in range(5):
            tr, te = fold != f, fold == f
            for k, x in xs.items():
                oof[k][te] = LogisticRegression(C=1e6, max_iter=1000).fit(x[tr], y[tr]).predict_proba(x[te])[:, 1]
        d = _ll(oof["rating+selfplay"], y) - _ll(oof["rating"], y)
        out[run] = {"games": len(gs), "groups": len(set(groups.tolist())),
                    "rating_differs": round(float(np.mean(r != 0)), 3),
                    "rating_alone": short(_score(oof["rating"], y, groups, "rating", BOOTS, 0)),
                    "selfplay_adds": round(float(d.mean()), 5), "selfplay_adds_95ci": _cluster_ci(d, groups)}
    return out


def main() -> None:
    from vgc.meta.pool import load_pool
    from vgc.regulation import load_regulation

    reg = load_regulation("reg_mc")
    games = human_games(reg)
    sims = {}
    for run, f in RUNS.items():
        d = json.loads((ANALYSIS / reg.id / f).read_text())
        sims[run] = {(x["a"], x["b"]) if x["a"] <= x["b"] else (x["b"], x["a"]): x for x in d["pairings"]}
    rated, players = ratings_and_players(reg)
    feats = {t.id: archetypes(reg, t.text) for t in load_pool(reg)}
    res = {"team_level": team_level(games, sims, players),
           "archetypes": archetype_values(games, sims, feats),
           "players_fixed": players_fixed(games, sims, rated)}
    OUT.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
