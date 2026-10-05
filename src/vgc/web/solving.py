"""The engine's answer to a live 1v1, found in the background and deepened as it goes.

A solve takes seconds at one turn deep and can take hours at four (F6 did), and the page cannot
wait for either. So a battle that reaches a 1v1 starts one here: every position `vgc.wp.endgame`
plans is solved at depth 1, then 2, 3 and 4, and the page is shown the deepest depth that has
finished, with its `leaf_mass` — the share of the answer that still rests on HP share rather than
on the engine. A shallow answer is mostly that guess: F1-B's two Speed classes both read 0.63 at
depth 1, and 0.99 and 0.02 at depth 3.

One solve at a time. The app is local and has one person at it, and a battle that moves on (a tap,
an undo) makes the old position worthless, so it is cancelled and its processes are killed rather
than left to finish. Every finished position lands in the solver's cache (`solver.CACHE`), so going
back to a position, or undoing a tap and making it again, answers at once.

With two or fewer Pokémon a side and more than one on a side (2v2, 2v1, 1v2: `vgc.wp.doubles`),
the answer has `DEADLINE` seconds, planning included (PLAN-endgame-doubles, stage 4). It comes in
two steps over warm solver processes (`vgc.wp.pool`): at once, each position's forced-win check
and the calibrated damage race (`QUICK`); then the one-turn search (`LIVE`), each position's value
replacing its quick one if it finishes before the deadline. Only the heaviest `TOP_ORDERS` move
orders are solved, and the weight of the rest is reported as unsolved. Open sheets only: on 674
held-out open-sheet games the search predicted who won better than the model did (log loss 0.384
against 0.513); on 349 closed-sheet games it was not distinguishable in any state kind, so there
the model leads (PLAN-endgame-doubles, stage 3).

A hidden spread's non-Speed points are a guess (`solver.spread`), and the guess moves the answer by
0.04-0.07 on average with no shape better than another on held-out games. So once the answer is in,
the same move orders are solved again with the guessed spreads refilled HP-first and then
defences-first (`solver.REFILLS`), on a budget of their own, and the page shows all three beside the
answer, which stays the first.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from vgc import paths
from vgc.regulation import Regulation
from vgc.wp import doubles, endgame, solver

# How deep a 1v1 is taken. Depth 4 can run for hours (F6 did), with the page polling all the while,
# so a cloud machine that bills by the second caps it (`VGC_SOLVE_MAX_DEPTH`, `deploy/`).
DEPTHS = tuple(d for d in (1, 2, 3, 4) if d <= int(os.environ.get("VGC_SOLVE_MAX_DEPTH") or 4))
# Solver processes at once. Each holds a copy of the simulator (about 220 MB, more mid-search), so
# a small cloud machine sets `VGC_SOLVER_WORKERS` to what its memory allows (`deploy/`).
WORKERS = int(os.environ.get("VGC_SOLVER_WORKERS") or max(1, min(6, (os.cpu_count() or 2) // 2)))

# Doubles: the live search (checked on held-out games), its quick first answer, the budget. The
# horizon is the race blended with HP share, the count and the net stat stages ('blend_boosts'),
# and the answer is tempered (`doubles.temper`): on the 674 held-out games, log loss 0.384 against
# 0.407 with 'blend' and no temperature, Brier 0.122 against 0.125.
LIVE = {**doubles.SEARCH, "ko_extend": False, "prune": 2, "sample": 8, "fast_race": True, "sample_only": True,
        "win_check": 0.1, "race_doubles": "blend_boosts"}
QUICK = {**LIVE, "depth": 0}
DEADLINE = 5.0
TOP_ORDERS = 3
DOUBLES_ASSUMPTIONS = [
    "Both sides play their best for one turn, from the two or three choices a Pokémon has that a "
    "player would consider; chance in that turn is sampled.",
    "After it, what is left is valued by a damage race between the Pokémon still standing, read "
    "together with the HP each side has left, how many Pokémon stand and their stat changes, "
    "weighed on past games; the answer is then made less sure, as past games say it should be.",
    "A win either side can force on this turn, against every reply and through Protect, is found "
    "first and is the answer when there is one.",
    "A hidden spread's Speed is averaged over how people build it, narrowed by this battle's turn "
    "order; the likeliest move orders are solved. Its other Stat Points are assumed: the main "
    "attacking stat maxed, then HP, then the defences. The answer is solved again with HP first and "
    "with the defences first, and those two are shown beside it.",
]
# The three guesses at a hidden spread's other points, as the page names them; the first is the answer.
GUESSES = (("assumed", "attacking stat first"), ("hp_first", "HP first"), ("defences", "defences first"))
_pool = None


def pool():
    """The warm solver processes, started on first use."""
    global _pool
    if _pool is None:
        from vgc.wp.pool import Pool
        _pool = Pool(WORKERS)
        _pool.warm()
    return _pool


def warm() -> None:
    """Start the solver processes ahead of the first doubles answer (in the background)."""
    threading.Thread(target=pool, daemon=True).start()

# What every engine answer assumes, beside what `endgame.plan` notes for this position.
ASSUMPTIONS = [
    "Both sides play their best, as the engine sees it: each turn is solved as a game over the "
    "four moves a side has.",
    "A hidden spread's Speed is averaged over how people build it, narrowed by this battle's turn "
    "order. Its other Stat Points are assumed: the main attacking stat maxed, then HP, then the "
    "defences.",
    "Two damage rolls per hit stand for all sixteen.",
]


def fingerprint(battle) -> str:
    return hashlib.sha256(json.dumps([battle.setup, battle.journal], sort_keys=True).encode()).hexdigest()


class Solve:
    def __init__(self, reg: Regulation, battle_id: str, battle):
        self.battle_id, self.key = battle_id, fingerprint(battle)
        self.plan = endgame.plan(reg, battle, {**solver.SEARCH, "depth": DEPTHS[0]})
        self.done: dict[int, dict[str, Any]] = {}
        self.depth: int | None = None               # being searched now
        self.error: str | None = None
        self.cancelled = False
        self.started = time.time()
        self._procs: set[subprocess.Popen] = set()
        self._lock = threading.Lock()
        if self.plan.get("eligible"):
            threading.Thread(target=self._run, daemon=True).start()

    def _at(self, depth: int) -> list[dict[str, Any]]:
        return [{**j["position"], "search": {**j["position"]["search"], "depth": depth}} for j in self.plan["jobs"]]

    def _one(self, pos: dict[str, Any]) -> dict[str, Any] | None:
        if self.cancelled:
            return None
        hit = solver._cached().get(solver.position_key(pos))
        if hit is not None:
            return hit
        p = subprocess.Popen(["node", str(solver.SOLVER), str(paths.SHOWDOWN)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        with self._lock:
            self._procs.add(p)
        try:
            out, err = p.communicate(json.dumps(pos))
        finally:
            with self._lock:
                self._procs.discard(p)
        if self.cancelled:
            return None
        if p.returncode:
            raise RuntimeError(f"endgame solver failed: {err.strip()[-300:]}")
        result = json.loads(out)
        solver.remember(pos, result)
        return result

    def _run(self) -> None:
        try:
            for depth in DEPTHS:
                self.depth = depth
                with ThreadPoolExecutor(WORKERS) as pool:
                    results = list(pool.map(self._one, self._at(depth)))
                if self.cancelled:
                    return
                combined = endgame.combine(self.plan["jobs"], results)
                self.done[depth] = combined | {
                    "elapsed": round(time.time() - self.started, 1),
                    "positions": [{"sets": j["sets"], "class": j["class"], "weight": round(j["weight"], 4),
                                   "value": r["value"], "leaf_mass": r["leaf_mass"]}
                                  for j, r in zip(self.plan["jobs"], results)]}
                if combined["leaf_mass"] == 0:
                    break                           # every line ends in a KO; deeper adds nothing
        except Exception as e:                      # shown on the page, not raised into a thread
            self.error = str(e)
        finally:
            self.depth = None

    def cancel(self) -> None:
        self.cancelled = True
        with self._lock:
            for p in self._procs:
                p.kill()

    def status(self) -> dict[str, Any]:
        if not self.plan.get("eligible"):
            return {"eligible": False, "reason": self.plan.get("reason")}
        deepest = max(self.done) if self.done else None
        answer = self.done.get(deepest) if deepest else None
        return {"eligible": True, "reason": None,
                "depth": deepest, "max_depth": DEPTHS[-1], "searching": self.depth,
                "value": round(answer["value"], 4) if answer else None,
                "leaf_mass": round(answer["leaf_mass"], 4) if answer else None,
                "positions": answer["positions"] if answer else [],
                "elapsed": round(time.time() - self.started, 1),
                "sets": self.plan["sets"], "unsolved": self.plan["unsolved"],
                "assumptions": ASSUMPTIONS + self.plan["notes"], "error": self.error}


class DoublesSolve:
    """A 2v2, 2v1 or 1v2 answered within `DEADLINE`: quick, then searched (module docstring)."""

    def __init__(self, reg: Regulation, battle_id: str, battle):
        self.battle_id, self.key = battle_id, fingerprint(battle)
        self.reg = reg
        self.started = time.time()
        self.plan = doubles.plan(reg, battle, LIVE)
        # Whose spreads are guessed: theirs, or both from the stands.
        me = battle.rp.state.perspective
        self.hidden = [sid for sid in ("p1", "p2") if sid != me]
        self.answer: dict[str, Any] | None = None
        self.guesses: dict[str, float] = {}
        self.guessing = False
        self.depth: int | None = None
        self.searching: int | None = None
        self.error: str | None = None
        self.cancelled = False
        if self.plan.get("eligible"):
            jobs = self.plan["jobs"][:TOP_ORDERS]                # 2 x TOP_ORDERS processes at once
            kept = sum(j["weight"] for j in jobs)
            self.dropped = sum(j["weight"] for j in self.plan["jobs"]) - kept
            self.jobs = [{**j, "weight": j["weight"] / kept} for j in jobs]
            self.searching = 0
            threading.Thread(target=self._run, daemon=True).start()

    def _set(self, results: list[dict[str, Any] | None], depth: int) -> None:
        combined = doubles.combine(self.jobs, results)
        if combined is None:
            return
        # Each move order's value, and the answer over them, as tempered answers (`doubles.temper`).
        self.answer = combined | {"value": doubles.temper(combined["value"]), "positions": [
            {"sets": [0, 0], "class": j["order"], "weight": round(j["weight"], 4), "value": doubles.temper(r["value"]),
             "leaf_mass": r["leaf_mass"], "forced": r.get("forced")} for j, r in zip(self.jobs, results)]}
        self.depth = depth

    def _solve(self, positions: list[dict[str, Any]], deadline: float, on_quick=None
               ) -> tuple[list[dict[str, Any] | None], list[dict[str, Any] | None]]:
        """Each position's quick answer and its searched one (None where it did not finish)."""
        at = lambda search: [{**p, "search": search} for p in positions]
        # Side by side: the quick pass would otherwise spend a second or more of the search's
        # budget. A forced win in the quick pass is found by the search's own check too.
        with ThreadPoolExecutor(2) as two:
            later = two.submit(pool().solve_all, at(LIVE), deadline)
            quick = pool().solve_all(at(QUICK), deadline)
            if on_quick is not None and not self.cancelled:
                on_quick(quick)
            return quick, later.result()

    def _run(self) -> None:
        try:
            def first(quick):
                self._set(quick, 0)
                self.searching = 1

            quick, searched = self._solve([j["position"] for j in self.jobs], self.started + DEADLINE, first)
            if self.cancelled:
                return
            best = [s if s is not None else q for s, q in zip(searched, quick)]
            if any(s is not None for s in searched) and all(b is not None for b in best):
                self._set(best, 1)
                self.answer["searched"] = sum(s is not None for s in searched)
            # Marked before `searching` clears, so a page polling in between keeps polling.
            self.guessing = self.answer is not None and bool(self.hidden)
        except Exception as e:                      # shown on the page, not raised into a thread
            self.error = str(e)
        finally:
            self.searching = None
        if self.guessing:
            self._guess()

    def _guess(self) -> None:
        """The answer again under the two other shapes of the guessed spreads, as deep as the answer
        went, on a `DEADLINE` of their own; a shape whose positions do not all finish is not shown."""
        try:
            modes = list(solver.REFILLS)
            positions = [{**j["position"], **{sid: solver.refill(self.reg, j["position"][sid], m) for sid in self.hidden}}
                         for m in modes for j in self.jobs]
            quick, searched = self._solve(positions, time.time() + DEADLINE)
            if self.cancelled:
                return
            n = len(self.jobs)
            for i, m in enumerate(modes):
                q, s = quick[i * n:(i + 1) * n], searched[i * n:(i + 1) * n]
                got = [b if self.depth == 1 and b is not None else a for a, b in zip(q, s)]
                combined = doubles.combine(self.jobs, got) if all(g is not None for g in got) else None
                if combined is not None:
                    self.guesses[m] = doubles.temper(combined["value"])
        except Exception:                           # the answer stands without them
            self.guesses = {}
        finally:
            self.guessing = False

    def cancel(self) -> None:
        self.cancelled = True
        if _pool is not None:
            _pool.cancel()

    def status(self) -> dict[str, Any]:
        if not self.plan.get("eligible"):
            return {"eligible": False, "reason": self.plan.get("reason")}
        a = self.answer
        return {"eligible": True, "reason": None, "kind": self.plan["kind"],
                "depth": self.depth, "max_depth": 1, "searching": self.searching,
                "value": round(a["value"], 4) if a else None,
                "leaf_mass": round(a["leaf_mass"], 4) if a else None,
                "positions": a["positions"] if a else [], "searched": (a or {}).get("searched"),
                "elapsed": round(time.time() - self.started, 1),
                "unsolved": round(min(1.0, self.plan["unsolved"] + self.dropped), 4),
                "guesses": {"pending": self.guessing, "values": [
                    {"key": k, "label": label, "value": round(v, 4)} for k, label in GUESSES
                    if (v := a["value"] if k == "assumed" and a else self.guesses.get(k)) is not None]},
                "assumptions": DOUBLES_ASSUMPTIONS + self.plan["notes"], "error": self.error}


def reason(reg: Regulation, battle) -> tuple[str | None, type]:
    """Why the engine is not asked about this position (None when it is), and which solve answers
    it: a 1v1's deepening one, or a doubles position's within the deadline."""
    why = endgame.reason(reg, battle.rp.state)
    if why is None:
        return None, Solve
    dwhy = doubles_reason(reg, battle)
    if dwhy is None:
        return None, DoublesSolve
    if "more than two" in dwhy or "in the back" in dwhy:
        return "the engine answers once neither side has more than two Pokémon left", Solve
    return (why if dwhy.startswith("a 1v1") else dwhy), Solve


def doubles_reason(reg: Regulation, battle) -> str | None:
    """Why a battle's position is not one the doubles answer is shown for, or None."""
    state = battle.rp.state
    why = doubles.reason(reg, state)
    if why:
        return why
    if not all(state.sides[sid].sheet or sid == state.perspective for sid in ("p1", "p2")):
        return "with a closed sheet the doubles engine has not yet beaten the model"
    return None


_current: Solve | DoublesSolve | None = None
_guard = threading.Lock()


def request(reg: Regulation, battle_id: str, battle) -> dict[str, Any]:
    """The engine's answer so far for this battle as it now stands, starting a solve if there is
    none for it. Any solve for another position is cancelled first."""
    global _current
    why, kind = reason(reg, battle)
    with _guard:
        if why:
            if _current is not None and _current.battle_id == battle_id:
                _current.cancel()
                _current = None
            return {"eligible": False, "reason": why}
        key = fingerprint(battle)
        if _current is None or (_current.battle_id, _current.key) != (battle_id, key) or not isinstance(_current, kind):
            if _current is not None:
                _current.cancel()
            _current = kind(reg, battle_id, battle)
        return _current.status()


def cancel(battle_id: str | None = None) -> None:
    """Stop the search for `battle_id` (any, with None) and kill its processes: a deleted battle's,
    or everything when the app shuts down. A node process outlives the server that started it."""
    global _current
    with _guard:
        if _current is not None and (battle_id is None or _current.battle_id == battle_id):
            _current.cancel()
            _current = None
        if battle_id is None and _pool is not None:
            _pool.close()
