"""Phase 6: the scoring half of `vgc sim validate`.

The simulation half needs Showdown and forty minutes; what is worth pinning in a test is the
arithmetic around it — that a pairing's win rate is oriented to the right side, that a Bo3 series
counts once towards power, that the recalibration cannot see the rows it is scored on, and that the
verdict's two questions stay separate.
"""

from __future__ import annotations

import random

import numpy as np
import pytest

from vgc.sim.validity import Game, score, select_pairings, verdict


def _game(i: int, a: str, b: str, a_won: bool, group: str | None = None, ended: str = "normal") -> Game:
    return Game(f"b{i}", group or f"g{i}", "fmt", "train", a, b, a_won, ended, None)


def _sim(pairs: dict[tuple[str, str], float], n: int = 50) -> dict[tuple[str, str], dict]:
    return {k: {"a": k[0], "b": k[1], "wins": round(v * n), "n": n, "wp": v, "wp_raw": v,
                "ci": [0.0, 1.0], "turns": 12.0} for k, v in pairs.items()}


def test_pairing_is_order_independent():
    assert _game(0, "tb", "ta", True).pairing == _game(1, "ta", "tb", False).pairing == ("ta", "tb")


def test_select_all_pairings_by_default():
    games = [_game(i, f"t{i}", f"u{i}", True) for i in range(5)]
    assert len(select_pairings(games)) == 5
    assert select_pairings(games, 0) == select_pairings(games, 99)


def test_most_played_ranks_by_game_count():
    games = [_game(0, "ta", "tb", True), _game(1, "ta", "tb", False), _game(2, "tc", "td", True)]
    assert select_pairings(games, 1, order="most_played") == [("ta", "tb")]


def test_random_selection_is_seed_stable_and_a_subset():
    games = [_game(i, f"t{i}", f"u{i}", True) for i in range(20)]
    a, b = select_pairings(games, 5, seed=3), select_pairings(games, 5, seed=3)
    assert a == b and len(a) == 5
    assert set(a) <= set(select_pairings(games))
    assert select_pairings(games, 5, seed=4) != a


def test_orientation_a_perfect_simulator_scores_as_one():
    """`sim` is keyed on the sorted pairing; the game says which side is p1. Get that backwards and
    a perfect predictor reads as a perfectly inverted one, so test both signs."""
    rng = random.Random(0)
    games, sim = [], {}
    for i in range(200):
        a, b = f"t{i}", f"u{i}"
        strong_is_p1 = rng.random() < 0.5
        # Half the games list the stronger team as p2, so a mis-orientation cannot average out.
        g = _game(i, a if strong_is_p1 else b, b if strong_is_p1 else a, strong_is_p1)
        games.append(g)
        # Team `a` always wins in simulation, and team `a` always won the human game — whichever
        # side of the replay it was on.
        sim[g.pairing] = {"a": a, "b": b, "wins": 49, "n": 50, "wp": 0.98, "wp_raw": 0.98,
                          "ci": [0, 1], "turns": 12.0}
    s = score(games, sim, boots=200)
    allrows = s["subsets"][0]
    assert allrows["auc"] == pytest.approx(1.0)
    assert allrows["logloss_delta"] < 0
    assert allrows["accuracy"] == pytest.approx(1.0)

    flipped = [Game(g.battle, g.group, g.fmt, g.shard, g.a, g.b, not g.a_won, g.ended_by, g.rating)
               for g in games]
    inv = score(flipped, sim, boots=200)["subsets"][0]
    assert inv["auc"] == pytest.approx(0.0)
    assert inv["logloss_delta"] > 0


def test_a_bo3_series_counts_once_towards_power():
    """Three games of one series share both teams and both players; `groups` is the honest n."""
    games = [_game(i, "ta", "tb", i != 2, group="series") for i in range(3)]
    games += [_game(9, "tc", "td", True)]
    s = score(games, _sim({("ta", "tb"): 0.7, ("tc", "td"): 0.7}), boots=100)["subsets"][0]
    assert s["games"] == 4 and s["groups"] == 2


def test_uninformative_simulator_does_not_beat_the_constant():
    rng = random.Random(1)
    games = [_game(i, f"t{i}", f"u{i}", rng.random() < 0.5) for i in range(400)]
    sim = _sim({g.pairing: rng.choice([0.2, 0.5, 0.8]) for g in games})
    s = score(games, sim, boots=400)
    assert s["subsets"][0]["logloss_delta"] > 0
    assert not verdict(s)["pass"]
    # An out-of-fold scale fit on noise cannot manufacture a win either.
    assert s["recalibrated"]["logloss_delta_95ci"][1] > -0.02


def test_recalibration_rescues_ordering_without_rescuing_the_raw_number():
    """The overconfidence case: the simulator ranks correctly but answers 0.97/0.03, while the
    human games it is ranking go the predicted way 70% of the time. PLAN.md records that 67% of
    pairings land beyond 85/15 under heuristic-vs-heuristic, so this is the shape of failure the
    split verdict exists to name."""
    rng = random.Random(2)
    games, pairs = [], {}
    for i in range(800):
        a, b = f"t{i}", f"u{i}"
        favoured = rng.random() < 0.5
        games.append(_game(i, a, b, favoured == (rng.random() < 0.7)))
        pairs[(a, b)] = 0.97 if favoured else 0.03
    s = score(games, _sim(pairs), boots=400)
    v = verdict(s)
    assert v["orders_correctly"] and v["recalibrated_beats_constant"]
    assert not v["usable_as_is"], "a 0.97 answer to a 70/30 question must not read as usable"
    assert v["pass"], "ordering is what Phase 10 consumes"


def test_verdict_needs_both_scale_free_tests():
    s = score([_game(i, f"t{i}", f"u{i}", i % 2 == 0) for i in range(60)],
              _sim({(f"t{i}", f"u{i}"): 0.5 for i in range(60)}), boots=100)
    v = verdict(s)
    assert not v["pass"] and "no usable team-strength signal" in v["reading"]


def test_forfeits_are_scored_separately():
    games = [_game(i, f"t{i}", f"u{i}", True, ended="normal" if i % 2 else "forfeit") for i in range(40)]
    s = score(games, _sim({g.pairing: 0.6 for g in games}), boots=100)
    subsets = {x["subset"]: x for x in s["subsets"]}
    assert subsets["ended_normal"]["games"] == 20 and subsets["forfeit"]["games"] == 20
    assert verdict(s)["scored_on"] == "ended_normal"


def test_spread_reports_the_85_15_share():
    sim = _sim({(f"t{i}", f"u{i}"): (0.9 if i < 30 else 0.5) for i in range(100)})
    games = [_game(i, f"t{i}", f"u{i}", True) for i in range(100)]
    assert score(games, sim, boots=50)["simulated_wp_spread"]["share_beyond_85_15"] == pytest.approx(0.30)


def test_uncovered_and_empty_pairings_are_dropped():
    games = [_game(0, "ta", "tb", True), _game(1, "tx", "ty", False), _game(2, "tp", "tq", True)]
    sim = _sim({("ta", "tb"): 0.6})
    sim[("tp", "tq")] = {"a": "tp", "b": "tq", "wins": 0, "n": 0, "wp": 0.5, "wp_raw": None,
                         "ci": [0.0, 1.0], "turns": None}
    assert score(games, sim, boots=20)["subsets"][0]["games"] == 1
    with pytest.raises(ValueError, match="no human games"):
        score([_game(1, "tx", "ty", False)], sim, boots=20)


def test_cluster_bootstrap_matches_a_naive_resample():
    """The ragged gather in `_bootstrap` replaced a per-group `np.concatenate` loop that was too
    slow at 3,400 groups. Same draws, same rows."""
    from vgc.sim.validity import _bootstrap

    groups = np.array(["b", "a", "c", "a", "b", "a", "d", "c"])
    order = np.argsort(groups, kind="stable")
    uniq, sizes = np.unique(groups, return_counts=True)
    starts = np.concatenate([[0], np.cumsum(sizes)[:-1]])
    members = {g: np.nonzero(groups == g)[0] for g in uniq}
    rng = np.random.default_rng(0)
    for _ in range(200):
        pick = rng.integers(0, len(sizes), len(sizes))
        counts = sizes[pick]
        base = np.repeat(starts[pick] - np.concatenate([[0], np.cumsum(counts)[:-1]]), counts)
        fast = order[base + np.arange(counts.sum())]
        assert list(fast) == list(np.concatenate([members[uniq[i]] for i in pick]))
    # And it is deterministic for a given seed, so a recorded interval can be re-derived.
    p, y = np.linspace(0.05, 0.95, 8), np.array([0.0, 1, 0, 1, 1, 0, 1, 1])
    assert _bootstrap(p, y, groups, reps=100, seed=7) == _bootstrap(p, y, groups, reps=100, seed=7)
