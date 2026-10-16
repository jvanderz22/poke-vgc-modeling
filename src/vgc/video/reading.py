"""A battle read as it plays: the battle reader run frame by frame on what the watch page captures
(PLAN-video step 4).

The three layers are the offline reader's, unchanged: `screen.ScreenReader` and `screen.Settler`
turn frames into settled events, and `assemble.Assembler` turns those into journal entries. What
this adds is what a live stream needs and a recorded clip doesn't:

  * **Time is the video's.** Each frame comes stamped with the player's time, and that is the
    time its events and entries carry (`video_s` on each entry, and in the review log).
  * **Seeking.** A frame behind what has been read (a rewind, or the player paused) is skipped:
    the reader keeps its journal and doesn't read it twice. A rewind to past the last thing it
    wrote is read again, from the journal: that is how a second pass picks up what the first
    missed. A jump forward leaves a gap, which is reported and logged; what happened in it is not
    read.
  * **Hand edits.** The battle's journal is the record, and it can be corrected by hand while the
    reader runs. When it no longer ends where the reader left it, the reader takes the journal as
    it now stands and carries on from there.
  * **Review.** What the reader missed or read provisionally goes to the review log as it happens,
    so a session that is never closed still leaves it there.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from vgc.battle import entry as journal
from vgc.regulation import Regulation
from vgc.video import review
from vgc.video.assemble import Assembler
from vgc.video.screen import ScreenReader, Settler, scale

# Frames come about every quarter second; a jump longer than this is a seek forward or a stall.
GAP_S = 2.0
# A frame this close to the last one read is the same moment: the player paused, or the page
# sent it twice.
SAME_S = 0.05
# A rewind to this long after the last thing written is read again. A message is up for 2 s at
# most, so one that was written is off the screen by then and can't be read twice.
REREAD_AFTER_S = 2.5


@dataclass
class Status:
    """What one frame did, and where the reading stands."""

    entries: list[dict[str, Any]] = field(default_factory=list)  # new, for the battle's journal
    read_to: float | None = None      # video time of the last frame read
    behind: bool = False              # the frame was before `read_to` (a rewind): skipped
    message: str | None = None        # the last message read
    missed: int = 0                   # misses so far, OCR fragments not counted
    recent_missed: list[dict[str, Any]] = field(default_factory=list)
    provisional: int = 0
    gaps: list[tuple[float, float]] = field(default_factory=list)
    ended: bool = False
    ms: float = 0.0                   # how long this frame took to read
    resynced: bool = False            # the journal had been edited by hand, and was taken as it stands
    reread: bool = False              # a rewind past everything written: read again from this frame
    left: str | None = None           # the player on the left of the screen, once the reader can tell
    # Regions the page reads itself but has never sent lines for (the server restarted, say): the
    # page reads every region on its next frame.
    unread: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


class Reading:
    """One battle being read. Not thread-safe by itself: `lock` is for the caller."""

    def __init__(self, reg: Regulation, setup: dict[str, Any], source: str):
        self.reg, self.setup, self.source = reg, setup, source
        self.screen, self.settler = ScreenReader(), Settler()
        self.asm = Assembler(reg, setup)
        self.lock = threading.Lock()
        self.read_to: float | None = None
        self.message: str | None = None
        self.gaps: list[tuple[float, float]] = []
        self.ended = False
        # How much of the assembler's output has been handed out and logged.
        self._sent = 0
        self._logged_missed = 0
        self._logged_provisional = 0

    def follows(self, entries: list[dict[str, Any]]) -> bool:
        """Whether the battle's journal is what the reader has written, and nothing else."""
        mine = self.asm.out.entries[:self._sent]
        return len(entries) == len(mine) and all(_bare(a) == _bare(b) for a, b in zip(entries, mine))

    def resync(self, entries: list[dict[str, Any]]) -> None:
        """Take the battle's journal as it now stands (it was edited by hand), and read on from it."""
        a = self.asm
        a.use(journal.replay(self.reg, self.setup, entries))
        a.out.entries = [dict(e) for e in entries]
        a.turn = a.rp.state.turn
        a.led = {e["side"] for e in entries if e.get("kind") == "lead"}
        a.started = a.led == {"p1", "p2"}
        a.pending = None
        # An empty slot is found from the state; what was held for one is the old journal's.
        a.vacated = {"p1": [], "p2": []}
        a.held = {"p1": [], "p2": []}
        a.asked = {q.id: self.read_to or 0.0 for q in a.rp.questions}
        self._sent = len(entries)

    def frame(self, video_s: float, image: Any, read: dict[str, list] | None = None) -> Status:
        """Read one frame (a PIL image or an RGB array) taken at `video_s` into the video. `read`
        is the lines the page OCR'd itself, by region (ScreenReader.observe)."""
        if self.ended:
            return self._status()
        reread = False
        if self.read_to is not None:
            same = abs(video_s - self.read_to) < SAME_S
            if same or (video_s < self.read_to and video_s - self._written_to() < REREAD_AFTER_S):
                if read:
                    self.screen.take(read)
                return self._status(behind=not same and video_s < self.read_to)
            if video_s < self.read_to:
                self._reread(video_s)
                reread = True
            elif video_s - self.read_to > GAP_S:
                self.gaps.append((self.read_to, video_s))
                self.asm._miss(self.read_to, f"{self.read_to:.1f}s to {video_s:.1f}s",
                               "not read: the video skipped ahead")
        start = time.perf_counter()
        for e in self.settler.feed(video_s, self.screen.observe(scale(image), read)):
            self.asm.feed(e)
            if e.kind == "message":
                self.message = e.data["text"]
        self.asm.tick(video_s)
        self.read_to = video_s
        if any(e["kind"] == "end" for e in self.asm.out.entries[self._sent:]):
            self._finish()
        return self._status(ms=(time.perf_counter() - start) * 1000, unread=sorted(self.screen.unread),
                            reread=reread)

    def _written_to(self) -> float:
        """The video time of the last thing read into the journal, or waiting to be: a later
        rewind can be read again without reading anything twice."""
        times = [e.get("t", e.get("video_s")) for e in self.asm.out.entries]
        if self.asm.pending is not None:
            times.append(self.asm.pending.t)
        return max((t for t in times if t is not None), default=float("-inf"))

    def _reread(self, video_s: float) -> None:
        """Read again from `video_s`, a rewind past everything written. The move waiting on its
        results is written with what it has; a decision point not yet closed is dropped, to be read
        again; the rest is the journal's, as after a hand edit."""
        a = self.asm
        a._flush(video_s)
        a.decision, a.snapshot = None, {}
        a.lead_check, a.lead_plates = None, {}
        a.replaying = False
        a.banners = [(t, b) for t, b in a.banners if t < video_s]
        a.hints = [h for h in a.hints if h[0] < video_s]
        sent = self._sent  # what the flush wrote still goes out with this frame
        self.read_to = video_s
        self.resync(a.out.entries)
        self._sent = sent
        self.settler = Settler()
        self.gaps = [(s, min(e, video_s)) for s, e in self.gaps if s < video_s]

    def finish(self) -> Status:
        """Write out what is still held (a move waiting on its results, a turn's last plates)."""
        self._finish()
        return self._status()

    def _finish(self) -> None:
        if not self.ended:
            self.asm.finish(self.read_to or 0.0)
            self.ended = True

    def _status(self, **kw: Any) -> Status:
        out = self.asm.out
        new = [_stamped(e) for e in out.entries[self._sent:]]
        self._sent = len(out.entries)
        self._log()
        missed = [m for m in out.missed if m["why"] not in review.QUIET]
        return Status(entries=new, read_to=self.read_to, message=self.message, missed=len(missed),
                      recent_missed=[{"t": m["t"], "why": m["why"], "what": str(m["what"])[:120]}
                                     for m in missed[-3:]],
                      provisional=len(out.provisional), gaps=list(self.gaps), ended=self.ended,
                      left=self.asm.side["L"] if self.asm.oriented else None, **kw)

    def _log(self) -> None:
        out = self.asm.out
        fresh = _Fresh(out.missed[self._logged_missed:], out.provisional[self._logged_provisional:])
        if fresh.missed or fresh.provisional:
            review.record(fresh, self.source)
        self._logged_missed, self._logged_provisional = len(out.missed), len(out.provisional)


@dataclass
class _Fresh:
    missed: list[dict[str, Any]]
    provisional: list[dict[str, Any]]


def _bare(e: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in e.items() if k not in ("t", "video_s")}


def _stamped(e: dict[str, Any]) -> dict[str, Any]:
    """A journal entry, with the assembler's time as the video time it was read at."""
    out = {k: v for k, v in e.items() if k != "t"}
    if "t" in e:
        out["video_s"] = e["t"]
    return out
