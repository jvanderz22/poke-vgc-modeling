"""How people choose, per Pokémon, among its legal choices (PLAN-policy, stage 3).

A conditional logit: each legal choice of a Pokémon gets a score w · x(choice), and the choice is made
with probability ∝ exp(score). `x` is what the solver says a choice is (`features` in
`endgame-solver.js`: damage and KO, the kind of move, for a switch how much less the one coming in
stands to lose, Mega or not). Fitted on training-split human turns (`prune_vs_people.py`); never on
the held-out ones it is scored on.

It is what realistic play keeps (the K a side the search is given, ranked by the two Pokémon's
summed scores), and the opponent model fitted to people (PLAN-policy, "the opponent").
"""

from __future__ import annotations

import math

import numpy as np

FEATURES = ("attack", "damage", "ko", "priority", "spread", "at_ally",
            "protect", "protect_danger", "protect_again", "guard",
            "fakeout", "fakeout_works",
            "speed_control", "already_up", "trickroom_up",
            "status", "redirect", "helpinghand", "screen", "disrupt", "inflict", "setup", "setup_danger",
            "heal", "missing_hp", "other_status",
            "switch", "switch_saved", "switch_danger",
            "mega")


def vector(f: dict[str, float]) -> np.ndarray:
    return np.array([float(f.get(k, 0.0)) for k in FEATURES])


def fit(cases: list[tuple[np.ndarray, int]], l2: float = 1e-3) -> np.ndarray:
    """Weights maximising the likelihood of the choices made: `cases` are (X, chosen) with X one
    row per legal choice of one Pokémon."""
    from scipy.optimize import minimize

    def nll(w: np.ndarray) -> tuple[float, np.ndarray]:
        tot, grad = 0.0, np.zeros_like(w)
        for X, i in cases:
            z = X @ w
            m = z.max()
            e = np.exp(z - m)
            p = e / e.sum()
            tot -= z[i] - m - math.log(e.sum())
            grad -= X[i] - p @ X
        n = len(cases)
        return tot / n + l2 * (w @ w), grad / n + 2 * l2 * w
    res = minimize(nll, np.zeros(len(FEATURES)), jac=True, method="L-BFGS-B")
    return res.x


def log_probs(X: np.ndarray, w: np.ndarray) -> np.ndarray:
    z = X @ w
    m = z.max()
    return z - m - math.log(np.exp(z - m).sum())


def weights_table(w: np.ndarray) -> dict[str, float]:
    return {k: round(float(v), 4) for k, v in zip(FEATURES, w)}


def as_cases(features: dict[str, dict[str, float]], chosen: str) -> tuple[np.ndarray, int] | None:
    parts = list(features)
    if chosen not in parts or len(parts) < 2:
        return None
    return np.stack([vector(features[p]) for p in parts]), parts.index(chosen)


def describe(w: np.ndarray) -> list[tuple[str, float]]:
    return sorted(weights_table(w).items(), key=lambda kv: -abs(kv[1]))
