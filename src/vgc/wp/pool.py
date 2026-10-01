"""Endgame-solver processes kept running, for answers that have seconds rather than minutes.

Starting `endgame-solver.js` loads Showdown, about 0.4 s a process, which a live answer with a 5 s
budget cannot spend once per position. So a few run `--serve` and take positions one at a time
over their pipes (`{id, position}` in, `{id, result}` or `{id, error}` out). A position past its
deadline is not waited for: its process is killed and a fresh one started in the background, and
the caller is told there is no answer. Every answer lands in the solver's cache, as a one-off
solve's does, so the same position later is answered at once.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from vgc import paths
from vgc.wp import solver


class Worker:
    def __init__(self) -> None:
        self.proc = subprocess.Popen(["node", str(solver.SOLVER), str(paths.SHOWDOWN), "--serve"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, bufsize=1)
        self.lines: queue.Queue = queue.Queue()
        self.n = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)                       # the process ended

    def alive(self) -> bool:
        return self.proc.poll() is None

    def solve(self, pos: dict[str, Any], timeout: float) -> dict[str, Any] | None:
        """The answer, or None past `timeout` seconds (the caller then kills this worker)."""
        self.n += 1
        self.proc.stdin.write(json.dumps({"id": self.n, "position": pos}) + "\n")
        self.proc.stdin.flush()
        try:
            line = self.lines.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None
        if line is None:
            raise RuntimeError("the endgame solver stopped")
        out = json.loads(line)
        if "error" in out:
            raise RuntimeError(f"endgame solver failed: {out['error'][-300:]}")
        return out["result"]

    def kill(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass


class Pool:
    """`size` warm workers. `solve_all` answers positions in parallel, each within the deadline."""

    def __init__(self, size: int):
        self.size = size
        self.idle: queue.Queue = queue.Queue()
        self.busy: set[Worker] = set()
        self._lock = threading.Lock()
        self._exec = ThreadPoolExecutor(size)

    def warm(self) -> None:
        """Start the workers now, so the first answer does not pay for loading Showdown."""
        with self._lock:
            have = self.idle.qsize() + len(self.busy)
        for _ in range(self.size - have):
            self.idle.put(Worker())

    def _take(self) -> Worker:
        try:
            w = self.idle.get_nowait()
        except queue.Empty:
            w = Worker()
        if not w.alive():
            w = Worker()
        with self._lock:
            self.busy.add(w)
        return w

    def _give(self, w: Worker, ok: bool) -> None:
        with self._lock:
            self.busy.discard(w)
        if ok and w.alive():
            self.idle.put(w)
        else:
            w.kill()
            threading.Thread(target=lambda: self.idle.put(Worker()), daemon=True).start()

    def _one(self, pos: dict[str, Any], deadline: float) -> dict[str, Any] | None:
        hit = solver._cached().get(solver.position_key(pos))
        if hit is not None:
            return hit
        left = deadline - time.time()
        if left <= 0:
            return None
        w = self._take()
        ok = False
        try:
            result = w.solve(pos, left)
            ok = result is not None
        finally:
            self._give(w, ok)
        if result is not None:
            solver.remember(pos, result)
        return result

    def solve_all(self, positions: list[dict[str, Any]], deadline: float) -> list[dict[str, Any] | None]:
        """Each position's answer, or None for those that did not finish by `deadline` (a
        `time.time()`), solved `size` at a time."""
        return list(self._exec.map(lambda p: self._one(p, deadline), positions))

    def cancel(self) -> None:
        """Kill whatever is running: the position it was for is no longer the one on screen."""
        with self._lock:
            busy = list(self.busy)
        for w in busy:
            w.kill()

    def close(self) -> None:
        self.cancel()
        while True:
            try:
                self.idle.get_nowait().kill()
            except queue.Empty:
                break
