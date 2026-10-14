"""What a battle broadcast shows, frame by frame, and when each part of it settles.

The first layer of the battle reader (PLAN-video step 2). A frame is cut into the regions a layout
names (docs/video-findings.md): the message line, the four nameplates with their HP, the field,
each side's screens and the ability and item banners. Each region is OCR'd only when its white text
changed. A value counts only once it has *settled*, the same over consecutive frames, so an HP
number counting down or a message read garbled on one frame never becomes an event.

What comes out is a timeline of raw events in the screen's own terms: left and right, names as
printed. Turning those into journal entries, through the two sheets, is the grammar's job.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any

import numpy as np

# Frames are scaled to this width before OCR: the text stays 20–30 px tall, and the detector has
# a tenth of the pixels to look at of a 2342-wide recording.
WORK_WIDTH = 1200
# A region counts as changed when this share of its pixels moved into or out of "white text".
CHANGED = 0.004
# A value settles when it is the same on this many frames in a row (4 fps: half a second).
SETTLE = 2

MARKER = "Trainers are choosing actions"
# A message read again within this many seconds, cut short, is the first one fading out.
ECHO_S = 3.0

# Positions on the screen. The game is shown from the left player's side, so L is that player.
SLOTS = ("L0", "L1", "R0", "R1")


@dataclass(frozen=True)
class Layout:
    """Where a broadcast puts each part of the battle screen, as fractions of the frame."""

    name: str
    regions: dict[str, tuple[float, float, float, float]]
    # Message lines start left of this; a line starting right of `side_x` is the right side's
    # screens ("Light Screen 1/5"), which share the message band.
    message_x: float
    side_x: float


# Play! Pokémon's 2026 regional broadcast (Louisville): docs/video-findings.md.
BROADCAST_2026 = Layout(
    name="broadcast-2026",
    regions={
        "message": (0.13, 0.70, 0.95, 0.86),
        "L0": (0.0, 0.865, 0.16, 0.985),
        "L1": (0.16, 0.865, 0.33, 0.985),
        "R0": (0.655, 0.865, 0.82, 0.985),
        "R1": (0.82, 0.865, 1.0, 0.985),
        "field": (0.30, 0.03, 0.45, 0.09),
        "field_turns": (0.55, 0.03, 0.66, 0.09),
        "banner_L": (0.0, 0.25, 0.30, 0.55),
        "banner_R": (0.70, 0.25, 1.0, 0.55),
    },
    message_x=0.25,
    side_x=0.60,
)


@dataclass(frozen=True)
class Plate:
    name: str | None
    hp: int | None  # percent, as printed


@dataclass
class Observation:
    """One frame, as the layout reads it. None where nothing was read."""

    message: str | None = None
    choosing: bool = False
    plates: dict[str, Plate] = field(default_factory=dict)
    effect: str | None = None  # the field's weather or terrain, "Rain 3/5"
    sides: tuple[str, ...] = ()  # the right side's screens: ("Light Screen 1/5",)
    banners: dict[str, str | None] = field(default_factory=dict)  # "L"/"R" -> "Indeedee's Rocky Helmet"


@dataclass(frozen=True)
class Event:
    t: float  # seconds into the clip the value first showed
    kind: str  # message | choosing | plate | field | sides | banner
    data: dict[str, Any]


# --- OCR ------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _engine():
    try:
        from rapidocr import RapidOCR
    except ImportError as e:
        raise RuntimeError("reading a video needs RapidOCR: pip install -e '.[video]'") from e
    # The detector's default scales the *short* side up to 736 px, which turns a thin strip of the
    # frame into an enormous image. Capping the long side keeps a region's OCR under 0.1 s.
    return RapidOCR(params={"Det.limit_side_len": 960, "Det.limit_type": "max", "Global.log_level": "error"})


def _lines(img: np.ndarray) -> list[tuple[str, float, float, float, float]]:
    """(text, x0, y0, x1, y1) for each line, in the region's own pixels."""
    out = _engine()(img)
    if out.boxes is None:
        return []
    return [(str(t).strip(), float(min(p[0] for p in b)), float(min(p[1] for p in b)),
             float(max(p[0] for p in b)), float(max(p[1] for p in b)))
            for b, t in zip(out.boxes, out.txts) if str(t).strip()]


def _white(img: np.ndarray) -> np.ndarray:
    """Where the broadcast's text is: near-white in every channel."""
    return img.min(axis=2) > 200


# --- reading a region -----------------------------------------------------------------------

_HP = re.compile(r"(\d{1,3})\s*%")
_TURNS = re.compile(r"(\d)\s*/\s*(\d)")


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _letters(text: str) -> int:
    return sum(c.isascii() and c.isalpha() for c in text)


def _echo(text: str, before: str) -> bool:
    """Whether `text` is `before` read again as it fades: the same line, cut short or a letter off."""
    head = before[:len(text)]
    return len(text) <= len(before) + 1 and _similar(text, head) >= 0.9


def _read_message(lines, width: float, layout: Layout, x_off: float) -> tuple[str | None, bool, tuple[str, ...]]:
    msg, choosing, sides = [], False, []
    for text, x0, y0, _, _ in sorted(lines, key=lambda ln: (ln[2], ln[1])):
        fx = x_off + x0 / width  # where the line starts, as a fraction of the frame
        if _similar(text, MARKER) > 0.8:
            choosing = True
        elif fx >= layout.side_x:
            sides.append(text)
        elif fx < layout.message_x:
            msg.append(text)
    # The right side's screens arrive as a name and its turns, on one line or two.
    joined = " ".join(sides)
    screens = tuple(m.group(0).strip() for m in re.finditer(r"[A-Za-z][A-Za-z ]+?\s*\d\s*/\s*\d", joined))
    return (" ".join(msg) or None), choosing, screens


def _read_plate(lines) -> Plate | None:
    if not lines:
        return None
    name, hp = None, None
    for text, *_ in lines:
        m = _HP.search(text)
        if m and int(m.group(1)) <= 100:
            hp = int(m.group(1))
        elif sum(c.isalpha() for c in text) >= 3:
            name = re.sub(r"[^A-Za-z .'-]", "", text).strip() or name
    return Plate(name, hp) if name or hp is not None else None


class ScreenReader:
    """Reads frames of one broadcast layout, OCR-ing a region only when its text changed."""

    def __init__(self, layout: Layout = BROADCAST_2026):
        self.layout = layout
        self._masks: dict[str, np.ndarray] = {}
        self._lines: dict[str, list] = {}
        self.ocr_calls = 0

    def _region(self, frame: np.ndarray, key: str) -> list:
        h, w = frame.shape[:2]
        x0, y0, x1, y1 = self.layout.regions[key]
        crop = frame[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
        mask = _white(crop)
        prev = self._masks.get(key)
        if prev is not None and prev.shape == mask.shape and np.count_nonzero(prev ^ mask) < CHANGED * mask.size:
            return self._lines[key]
        self._masks[key] = mask
        self.ocr_calls += 1
        self._lines[key] = _lines(crop)
        return self._lines[key]

    def observe(self, frame: np.ndarray) -> Observation:
        """One RGB frame (H×W×3, uint8), already scaled to about WORK_WIDTH."""
        h, w = frame.shape[:2]
        r = self.layout.regions
        mx0, _, mx1, _ = r["message"]
        message, choosing, sides = _read_message(self._region(frame, "message"), (mx1 - mx0) * w, self.layout, mx0)
        plates = {s: p for s in SLOTS if (p := _read_plate(self._region(frame, s)))}
        field_text = " ".join(t for t, *_ in self._region(frame, "field"))
        turns = " ".join(t for t, *_ in self._region(frame, "field_turns"))
        m = _TURNS.search(turns)
        fld = f"{field_text} {m.group(1)}/{m.group(2)}" if field_text and m else None
        # The team icons at the edges read as stray characters; a banner is words.
        banners = {side: " ".join(t for t, *_ in sorted(self._region(frame, f"banner_{side}"), key=lambda ln: ln[2])
                                  if _letters(t) >= 3) or None
                   for side in ("L", "R")}
        return Observation(message, choosing, plates, fld, sides, banners)


def scale(img: Any) -> np.ndarray:
    """A PIL image or array, as RGB uint8 at about WORK_WIDTH wide."""
    from PIL import Image

    im = img if isinstance(img, Image.Image) else Image.fromarray(img)
    if im.width > WORK_WIDTH * 1.1:
        im = im.resize((WORK_WIDTH, round(im.height * WORK_WIDTH / im.width)), Image.BILINEAR)
    return np.asarray(im.convert("RGB"))


# --- settling -------------------------------------------------------------------------------

class Settler:
    """Turns per-frame observations into events: a value is reported once, when it has held for
    SETTLE frames, stamped with the time it first showed."""

    def __init__(self, settle: int = SETTLE):
        self.settle = settle
        self._pending: dict[str, tuple[Any, int, float]] = {}  # channel -> (value, frames, since)
        self._emitted: dict[str, Any] = {}
        self._last_message: tuple[str, float] | None = None

    def _feed(self, channel: str, value: Any, t: float) -> Any:
        v, n, since = self._pending.get(channel, (object(), 0, t))
        v, n, since = (v, n + 1, since) if v == value else (value, 1, t)
        self._pending[channel] = (v, n, since)
        if n >= self.settle and self._emitted.get(channel, object()) != v:
            self._emitted[channel] = v
            return since
        return None

    def feed(self, t: float, obs: Observation) -> list[Event]:
        out = []
        if (at := self._feed("message", obs.message, t)) is not None and obs.message:
            last = self._last_message
            if not (last and at - last[1] < ECHO_S and _echo(obs.message, last[0])):
                out.append(Event(at, "message", {"text": obs.message}))
                self._last_message = (obs.message, at)
        if (at := self._feed("choosing", obs.choosing, t)) is not None and obs.choosing:
            out.append(Event(at, "choosing", {}))
        for s in SLOTS:
            p = obs.plates.get(s)
            if (at := self._feed(f"plate:{s}", p, t)) is not None and p is not None:
                out.append(Event(at, "plate", {"slot": s, "name": p.name, "hp": p.hp}))
        if (at := self._feed("field", obs.effect, t)) is not None and obs.effect:
            out.append(Event(at, "field", {"text": obs.effect}))
        if (at := self._feed("sides", obs.sides, t)) is not None and obs.sides:
            out.append(Event(at, "sides", {"side": "R", "screens": list(obs.sides)}))
        for side in ("L", "R"):
            b = obs.banners.get(side)
            if (at := self._feed(f"banner:{side}", b, t)) is not None and b:
                out.append(Event(at, "banner", {"side": side, "text": b}))
        return sorted(out, key=lambda e: e.t)


def timeline(frames: Iterable[tuple[float, Any]], layout: Layout = BROADCAST_2026) -> Iterator[Event]:
    """Events from (seconds, frame) pairs taken at a steady rate (4 a second suits messages)."""
    reader, settler = ScreenReader(layout), Settler()
    for t, img in frames:
        yield from settler.feed(t, reader.observe(scale(img)))
