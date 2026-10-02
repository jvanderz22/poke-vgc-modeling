"""A policy over whole battles: the expected win probability of each choice, by search (PLAN-policy,
stage 4).

    EWP(a) = Σ_b π_opp(b | o) · E[WP(o′ | a, b)]

A decision is built from what the player can see (`vgc.policy.view`): `plan` gives the positions,
each a guess at what is hidden (the opponent's back, its spreads), with its weight. Each is solved
one turn deep by the endgame solver (one warm process a policy, `vgc.wp.pool.Worker`) as a matrix
over the pruned choices of both sides: the first, the heaviest, picks the player's rows, and the
rest solve those same rows (`root_keep`), so the guesses can be combined row by row.

The opponent, two ways (the gate chooses):
  nash    the opponent knows what the player has to guess, so the guesses are one Bayesian game:
          the player's rows against a column for every way the opponent could answer each guess,
          C[a, (b_1..b_D)] = Σ_j w_j · M_j[a, b_j]. Its minimax strategy is sampled with the policy's
          seeded rng, so a battle stays a function of its seed.
  people  each guess's opponent chooses as people do: P(b) ∝ exp(its people score) over the kept
          choices (`vgc.policy.people`, fitted on training-split human turns). The choice is the
          argmax of EWP.

A replacement is a decision like any other: the solver's root is the replacement (`setup.empty`),
and each way to fill the slots is valued by the turn after it. Where no position can be built (a
volatile the solver cannot set up, a switch in the middle of a turn, a closed sheet) the heuristic
chooses, and the decision's record says why. Team preview is the heuristic's.

Every decision leaves a record in `decisions` (the EWP table: each row's EWP, its sampling error and
its worst reply), which the gate's calibration check reads.
"""

from __future__ import annotations

import json
import math
import random
import time
from typing import Any

import numpy as np
from poke_env.battle.double_battle import DoubleBattle
from poke_env.player.battle_order import BattleOrder

from vgc.policy import view as V
from vgc.policy.heuristic import HeuristicPolicy
from vgc.regulation import Regulation, to_id

# The side's pairs searched (`side_k`). Measured on 100 battles against the heuristic, 4 at once on
# the laptop (policy_check.py): K 4 takes a median 0.96 s a decision and K 6 2.1 s, against the plan's
# 1 s; their win rates (0.69, 0.75) are not distinguishable at that size. The gate says whether K 4
# is enough.
SIDE_K = 4
# The search at two or fewer a side: the same, at one move order (PLAN-policy stage 0: those roots
# are about one turn in five, and one order carries most of their weight).
SMALL_POSITIONS = 1
# A solve that runs past this is abandoned and the heuristic chooses. Not a budget: the search's
# size is its budget, so a battle is a function of its seed. A timeout is the one exception, and the
# record says so.
TIMEOUT = 30.0
# The Bayesian game's columns are every combination of the guesses' replies; past this many, each
# guess is solved on its own and the strategies averaged by weight.
MAX_COLUMNS = 4096


def _other(sid: str) -> str:
    return "p2" if sid == "p1" else "p1"


def game(C: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """The row player's maximin strategy in the matrix game `C` (rows maximize), the column
    player's minimax strategy, and the value."""
    from scipy.optimize import linprog

    n, m = C.shape
    if n == 1 or m == 1:
        x = np.zeros(n)
        y = np.zeros(m)
        if n == 1:
            x[0] = 1.0
            y[int(np.argmin(C[0]))] = 1.0
        else:
            y[0] = 1.0
            x[int(np.argmax(C[:, 0]))] = 1.0
        return x, y, float(x @ C @ y)
    # max v: v <= x·C[:, c] for every column, x a distribution.
    r = linprog(np.r_[np.zeros(n), -1.0], A_ub=np.c_[-C.T, np.ones(m)], b_ub=np.zeros(m),
                A_eq=np.r_[np.ones(n), 0.0][None], b_eq=[1.0], bounds=[(0, None)] * n + [(None, None)],
                method="highs")
    # min u: u >= C[a, :]·y for every row, y a distribution.
    d = linprog(np.r_[np.zeros(m), 1.0], A_ub=np.c_[C, -np.ones(n)], b_ub=np.zeros(n),
                A_eq=np.r_[np.ones(m), 0.0][None], b_eq=[1.0], bounds=[(0, None)] * m + [(None, None)],
                method="highs")
    x = np.clip(r.x[:n], 0, None)
    y = np.clip(d.x[:m], 0, None)
    return x / x.sum(), y / y.sum(), float(-r.fun)


def softmax(z: np.ndarray) -> np.ndarray:
    e = np.exp(z - z.max())
    return e / e.sum()


def combine(solved: list[tuple[float, np.ndarray, np.ndarray | None, np.ndarray | None]], opponent: str,
            sample: int) -> dict[str, Any]:
    """Each guess as (weight, M, the opponent's people scores, cell variances), M the player's rows
    against the opponent's columns in the player's win probability: each row's EWP under the
    opponent model, its sampling error and worst reply, and the player's strategy."""
    w = np.array([s[0] for s in solved])
    w = w / w.sum()
    Ms = [s[1] for s in solved]
    n = Ms[0].shape[0]
    if opponent == "people":
        ys = [softmax(np.array(s[2])) if s[2] is not None else np.full(s[1].shape[1], 1 / s[1].shape[1])
              for s in solved]
        ewp = sum(wj * (M @ y) for wj, M, y in zip(w, Ms, ys))
        x = np.zeros(n)
        x[int(np.argmax(ewp))] = 1.0
        value = float(ewp.max())
    else:
        cols = math.prod(M.shape[1] for M in Ms)
        if cols <= MAX_COLUMNS:
            shape = [M.shape[1] for M in Ms]
            C = np.zeros((n, cols))
            for j, (wj, M) in enumerate(zip(w, Ms)):
                # Column c's reply to guess j: its j-th index in the product.
                idx = np.unravel_index(np.arange(cols), shape)[j]
                C += wj * M[:, idx]
            x, yc, value = game(C)
            ys = []
            for j, M in enumerate(Ms):
                idx = np.unravel_index(np.arange(cols), shape)[j]
                ys.append(np.bincount(idx, yc, minlength=M.shape[1]))
        else:
            parts = [game(M) for M in Ms]
            x = sum(wj * p[0] for wj, p in zip(w, parts))
            ys = [p[1] for p in parts]
            value = float(sum(wj * p[2] for wj, p in zip(w, parts)))
        ewp = sum(wj * (M @ y) for wj, M, y in zip(w, Ms, ys))
    var = np.zeros(n)
    for wj, (_, M, _, cv), y in zip(w, solved, ys):
        if cv is not None:
            var += wj ** 2 * (cv @ (y ** 2)) / max(1, sample)
    worst = sum(wj * M.min(axis=1) for wj, M in zip(w, Ms))
    return {"ewp": ewp, "se": np.sqrt(var), "worst": worst, "strategy": x, "value": value}


def to_battle(choice: str, plan: dict[str, Any], req: dict[str, Any]) -> str:
    """The solver's choice (its slots, its team order, its move numbers) as the battle's."""
    me = req["side"]["id"]
    them = _other(me)
    slots = len(req.get("forceSwitch") or req.get("active") or [None, None])
    out = ["pass"] * slots
    team = req["side"]["pokemon"]
    for i, part in enumerate(x.strip() for x in choice.split(",")):
        if part == "pass":
            continue
        real = plan["slots"][me][i]
        toks = part.split()
        if toks[0] == "switch":
            nick = plan["team"][int(toks[1]) - 1]
            k = next(j for j, p in enumerate(team) if p["ident"].split(": ", 1)[1] == nick)
            out[real] = f"switch {k + 1}"
            continue
        moves = req["active"][real]["moves"]
        if len(moves) == 1 and (moves[0]["id"] == "struggle" or "target" not in moves[0]):
            # Locked in (the second turn of Electro Shot, Outrage, a recharge), or out of PP: the
            # request lists the one move with no target, and the battle refuses one given.
            out[real] = "move 1"
            continue
        mid = plan["moves"][i][int(toks[1]) - 1]
        n = next(j for j, mv in enumerate(moves) if mv["id"] == mid) + 1
        text = f"move {n}"
        target = next((int(t) for t in toks[2:] if t.lstrip("-").isdigit()), None)
        if target is not None:
            text += f" {plan['slots'][them][target - 1] + 1}" if target > 0 else f" -{plan['slots'][me][-target - 1] + 1}"
        if "mega" in toks[2:]:
            text += " mega"
        out[real] = text
    return ", ".join(out)


class EWPPolicy:
    """`opponent` is "nash" or "people". `search` and `small` override the search at more than two a
    side and at two or fewer."""

    def __init__(self, reg: Regulation, opponent: str = "people", positions: int = V.POSITIONS,
                 search: dict[str, Any] | None = None, small: dict[str, Any] | None = None,
                 keep_decisions: bool = True):
        assert opponent in ("nash", "people")
        self.reg, self.opponent, self.positions = reg, opponent, positions
        self.name = f"ewp-{opponent}"
        self.search = {**V.SEARCH, "side_k": SIDE_K, **(search or {}), "root_detail": True}
        self.small = {**self.search, **(small or {})}
        self.heuristic = HeuristicPolicy(reg)
        self.views: dict[tuple[str, str], V.PlayerView] = {}
        self.pending: dict[tuple[str, str], dict[str, Any]] = {}
        self.decisions: list[dict[str, Any]] = []
        self.keep_decisions = keep_decisions
        self._worker = None

    # --- what the player is shown (`play_battle` calls these when a policy has them) ----------

    def begin(self, battle_id: str, side: str, team: str) -> None:
        for k in [k for k in self.views if k[1] == side]:
            del self.views[k]
            self.pending.pop(k, None)
        self.views[(battle_id, side)] = V.PlayerView(self.reg, side, team)

    def observe(self, battle_id: str, side: str, chunk: str) -> None:
        v = self.views.get((battle_id, side))
        if v is None:
            return
        lines = []
        for line in chunk.split("\n"):
            if line.startswith("|request|"):
                body = line[len("|request|"):]
                if body:
                    self.pending[(battle_id, side)] = json.loads(body)
            elif line.startswith("|"):
                lines.append(line)
        v.feed(lines)

    # --- choosing --------------------------------------------------------------------------

    def teampreview(self, battle: DoubleBattle, rng: random.Random) -> str:
        return self.heuristic.teampreview(battle, rng)

    def choose_move(self, battle: DoubleBattle, rng: random.Random) -> BattleOrder | str:
        key = (battle.battle_tag, battle.player_role)
        v = self.views.get(key)
        req = self.pending.pop(key, None)
        if v is not None and req is not None:
            v.request(req)
        t0 = time.perf_counter()
        rec: dict[str, Any] = {"battle": battle.battle_tag, "side": battle.player_role, "turn": battle.turn}
        choice = None
        if v is None or v.req is None:
            rec["fallback"] = "no view of the battle"
        else:
            try:
                choice = self._decide(v, rng, rec)
            except Exception as e:                  # recorded, and the heuristic plays the turn
                rec["fallback"] = f"error: {type(e).__name__}: {e}"
                choice = None
        rec["ms"] = round(1000 * (time.perf_counter() - t0))
        if choice is None:
            rec.setdefault("fallback", "no choice")
            out: BattleOrder | str = self.heuristic.choose_move(battle, rng)
            rec["ms_fallback"] = round(1000 * (time.perf_counter() - t0)) - rec["ms"]
        else:
            out = choice
            rec["choice"] = choice
        if self.keep_decisions:
            self.decisions.append(rec)
        return out

    def _decide(self, v: V.PlayerView, rng: random.Random, rec: dict[str, Any]) -> str | None:
        me = v.perspective
        state = v.o
        left = [self.reg.bring - sum(m.state == "fainted" for m in state.sides[s].mons) for s in ("p1", "p2")]
        small = max(left) <= 2
        search = self.small if small else self.search
        t0 = time.perf_counter()
        p = V.plan(self.reg, v, search, positions=SMALL_POSITIONS if small else self.positions)
        rec["plan_ms"] = round(1000 * (time.perf_counter() - t0))
        rec.update({"kind": p.get("kind"), "replacing": p.get("replacing") or []})
        if not p["eligible"]:
            rec["fallback"] = p["reason"]
            return None
        p["moves"] = self._moves(v, p)
        rec.update({"unsolved": p["unsolved"], "considered": p["considered"], "positions": len(p["jobs"])})
        solved, rows = [], None
        for j in p["jobs"]:
            pos = j["position"]
            if rows is not None:
                pos = {**pos, "search": {**pos["search"], "root_keep": {me: rows}}}
            t1 = time.perf_counter()
            r = self._solve(pos)
            rec.setdefault("solve_ms", []).append(round(1000 * (time.perf_counter() - t1)))
            if r is None:
                rec["fallback"] = "timeout"
                rec["timeout"] = True
                return None
            if not r.get("matrix"):
                rec["fallback"] = "the solver gave no matrix"
                return None
            if rows is None:
                rows = r["moves"][me]
            elif r["moves"][me] != rows:
                continue                            # cannot be combined row by row: left out
            M = np.array(r["matrix"], float)
            cv = np.array(r["cell_var"], float) if r.get("cell_var") is not None else None
            people = (r.get("people") or {}).get(_other(me))
            if me == "p2":
                M, cv = 1 - M.T, (cv.T if cv is not None else None)
            scores = None if people is None or any(x is None for x in people) else people
            solved.append((j["weight"], M, scores, cv))
        out = combine(solved, self.opponent, search.get("sample") or 1)
        if self.opponent == "nash":
            x = out["strategy"]
            u, a = rng.random(), 0
            acc = 0.0
            for a in range(len(x)):
                acc += x[a]
                if u < acc:
                    break
        else:
            a = int(np.argmax(out["strategy"]))
        rec.update({
            "rows": rows, "ewp": [round(float(e), 4) for e in out["ewp"]], "se": [round(float(e), 4) for e in out["se"]],
            "worst": [round(float(e), 4) for e in out["worst"]], "strategy": [round(float(e), 4) for e in out["strategy"]],
            "value": round(out["value"], 4), "chosen": a, "solved": len(solved),
        })
        return to_battle(rows[a], p, v.req)

    def _moves(self, v: V.PlayerView, p: dict[str, Any]) -> list[list[str] | None]:
        """The player's Pokémon in the solver's order, each as the move ids its set lists: the
        solver numbers moves by the set, the battle by its request."""
        out = []
        for nick in p["team"]:
            if nick is None:
                out.append(None)
                continue
            m = next(x for x in v.o.sides[v.perspective].mons if x.nickname == nick)
            out.append([to_id(x) for x in V._own_set(self.reg, v, m)["moves"]])
        return out

    def _solve(self, pos: dict[str, Any]) -> dict[str, Any] | None:
        from vgc.wp.pool import Worker

        if self._worker is None or not self._worker.alive():
            self._worker = Worker()
        r = self._worker.solve(pos, TIMEOUT)
        if r is None:
            self._worker.kill()
            self._worker = None
        return r

    def close(self) -> None:
        if self._worker is not None:
            self._worker.kill()
            self._worker = None


class ScorerPolicy(EWPPolicy):
    """The held-out opponent of the gate (PLAN-policy stage 5): no search, the pair the endgame's
    pruning rules score highest (each Pokémon's choices by `prune`'s score, the overkill rule, a
    switch only to save a threatened Pokémon, Mega taken where it can be). Different code and
    different weights from both the heuristic and the people model. Built from the same view of the
    battle, with the heaviest guess at what is hidden; a replacement takes the first Pokémon listed.
    Where no position can be built the heuristic chooses, and the record says so."""

    SEARCH = {"list_only": True, "prune": 2, "side_k": 1, "prune_by": None, "switches": True, "mega": True}

    def __init__(self, reg: Regulation, keep_decisions: bool = True):
        super().__init__(reg, "people", positions=1, search=self.SEARCH, keep_decisions=keep_decisions)
        self.name = "scorer"
        self.search = self.small = {**V.SEARCH, **self.SEARCH}

    def _decide(self, v: V.PlayerView, rng: random.Random, rec: dict[str, Any]) -> str | None:
        p = V.plan(self.reg, v, self.search, positions=1)
        rec.update({"kind": p.get("kind"), "replacing": p.get("replacing") or []})
        if not p["eligible"]:
            rec["fallback"] = p["reason"]
            return None
        p["moves"] = self._moves(v, p)
        pos = p["jobs"][0]["position"]
        if v.perspective in p["replacing"]:
            pos = {**pos, "search": {**pos["search"], "prune": 0}}   # a replacement is not scored
        r = self._solve(pos)
        if r is None:
            rec["fallback"] = "timeout"
            rec["timeout"] = True
            return None
        kept = r["kept"][v.perspective]
        return to_battle(kept[0], p, v.req)
