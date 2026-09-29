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
from vgc.wp import endgame, solver

DEPTHS = (1, 2, 3, 4)
WORKERS = max(1, min(6, (os.cpu_count() or 2) // 2))

# What every engine answer assumes, beside what `endgame.plan` notes for this position.
ASSUMPTIONS = [
    "Both sides play their best, as the engine sees it: each turn is solved as a game over the "
    "four moves a side has.",
    "Their Speed investment is averaged over how people build it, narrowed by this battle's turn "
    "order. Their other Stat Points are assumed: the main attacking stat maxed, then HP, then the "
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
                    "positions": [{"set": j["set"], "class": j["class"], "weight": round(j["weight"], 4),
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


_current: Solve | None = None
_guard = threading.Lock()


def request(reg: Regulation, battle_id: str, battle) -> dict[str, Any]:
    """The engine's answer so far for this battle as it now stands, starting a solve if there is
    none for it. Any solve for another position is cancelled first."""
    global _current
    why = endgame.reason(reg, battle.rp.state)
    with _guard:
        if why:
            if _current is not None and _current.battle_id == battle_id:
                _current.cancel()
                _current = None
            return {"eligible": False, "reason": why}
        key = fingerprint(battle)
        if _current is None or (_current.battle_id, _current.key) != (battle_id, key):
            if _current is not None:
                _current.cancel()
            _current = Solve(reg, battle_id, battle)
        return _current.status()
