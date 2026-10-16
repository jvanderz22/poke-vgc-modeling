"""An open team sheet read off a screenshot: a broadcast's team-list graphic, for now.

OCR (RapidOCR, on onnxruntime) gives each line of text and its box. Every line is matched against
the regulation's own names (species, abilities, natures, items, moves) and commits only if it wins
by a margin. A line that doesn't is reported as unread and never filled with its nearest name
(PLAN-video decision 3). Lines are grouped into Pokémon by position, with each species name as its
card's anchor, so nothing here is tied to one broadcast's pixel layout.

What the reader does not check, `problems` does: the sheet goes through the same validator as a
pasted one, so a move the species can't learn is flagged rather than trusted.
"""

from __future__ import annotations

import statistics
import threading
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

from vgc.regulation import Dex, Regulation, to_id

# A fuzzy match commits at this ratio or above, and only when the runner-up (a different name)
# trails it by MARGIN. Exact matches, the usual case on clean broadcast text, skip both.
MIN_RATIO = 0.85
MARGIN = 0.08

# How a sheet may spell a forme, against the dex's name for it: "BASCULEGION (FEMALE)" for
# Basculegion-F, "ALOLAN NINETALES" for Ninetales-Alola.
_GENDER_FORMES = {"F": "female", "M": "male"}
_REGIONAL_FORMES = {"Alola": "alolan", "Galar": "galarian", "Hisui": "hisuian", "Paldea": "paldean"}

# The validator's Stat Point checks don't apply: a sheet never shows Stat Points.
_SP_CODES = {"sp_unspent", "sp_zero", "sp_budget", "sp_stat_cap", "sp_negative"}

MOVES_PER_SET = 4


@dataclass(frozen=True)
class Line:
    """One line of OCR text and its box, in image pixels."""

    text: str
    score: float  # the OCR's own confidence, kept for the record; matching doesn't use it
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True)
class Match:
    kind: str  # species | ability | nature | item | move
    name: str  # the dex's spelling
    ratio: float


@dataclass
class SheetEntry:
    """One Pokémon as the sheet shows it: the fields of `frontend/src/sheet.ts`'s SheetEntry."""

    species: str | None = None
    ability: str | None = None
    nature: str | None = None
    item: str | None = None
    moves: list[str] = field(default_factory=list)

    def missing(self) -> list[str]:
        out = [k for k in ("ability", "nature", "item") if getattr(self, k) is None]
        if len(self.moves) < MOVES_PER_SET:
            out.append(f"{MOVES_PER_SET - len(self.moves)} move(s)")
        return out


@dataclass
class SheetRead:
    title: str | None
    entries: list[SheetEntry]
    unread: list[Line] = field(default_factory=list)  # inside the sheet, matched nothing
    notes: list[str] = field(default_factory=list)

    def showdown(self) -> str:
        blocks = []
        for e in self.entries:
            if not e.species:
                continue
            lines = [f"{e.species}{f' @ {e.item}' if e.item else ''}"]
            lines += [f"Ability: {e.ability}"] if e.ability else []
            lines += [f"{e.nature} Nature"] if e.nature else []
            lines += [f"- {m}" for m in e.moves]
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks) + "\n"

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "entries": [asdict(e) for e in self.entries],
                "unread": [asdict(ln) for ln in self.unread], "notes": list(self.notes),
                "showdown": self.showdown()}


# --- vocabulary -----------------------------------------------------------------------------

def vocabulary(dex: Dex) -> dict[str, list[tuple[str, str]]]:
    """Every name a sheet can show, keyed by `to_id`: id -> [(kind, dex name)]."""
    vocab: dict[str, list[tuple[str, str]]] = {}

    def add(key: str, kind: str, name: str) -> None:
        if (kind, name) not in vocab.setdefault(key, []):
            vocab[key].append((kind, name))

    for s in dex.species.values():
        if s.get("battleOnly"):  # a Mega forme is never what a sheet lists
            continue
        add(to_id(s["name"]), "species", s["name"])
        base, forme = s.get("baseSpecies") or s["name"], s.get("forme") or ""
        if forme in _GENDER_FORMES:
            add(to_id(base + _GENDER_FORMES[forme]), "species", s["name"])
        if forme in _REGIONAL_FORMES:
            add(to_id(_REGIONAL_FORMES[forme] + base), "species", s["name"])
    # A sheet prints the base name for a forme whose base isn't legal itself: "FLOETTE" for
    # Floette-Eternal. Without this the nearest name was the item Floettite, the card had no
    # species line, and nothing on it was read (flagged in prod, 2026-10-15). Only where the base
    # has one legal forme: a base with several says nothing about which.
    formes: dict[str, set[str]] = {}
    for s in dex.species.values():
        if not s.get("battleOnly"):
            formes.setdefault(to_id(s.get("baseSpecies") or s["name"]), set()).add(s["name"])
    for base, names in formes.items():
        if len(names) == 1 and not any(kind == "species" for kind, _ in vocab.get(base, [])):
            add(base, "species", next(iter(names)))
    for kind, table in (("ability", dex.abilities), ("nature", dex.natures),
                        ("item", dex.items), ("move", dex.moves)):
        for entry in table.values():
            add(to_id(entry["name"]), kind, entry["name"])
    return vocab


def match(text: str, vocab: dict[str, list[tuple[str, str]]]) -> Match | None:
    """The one name `text` reads as, or None when no name wins clearly."""
    key = to_id(text)
    if not key:
        return None
    if key in vocab:
        hits = vocab[key]
        return Match(*hits[0], 1.0) if len(hits) == 1 else None
    scored = []
    for cand in vocab:
        if abs(len(cand) - len(key)) > max(3, len(key) // 3):
            continue
        sm = SequenceMatcher(None, key, cand)
        if sm.real_quick_ratio() < MIN_RATIO - MARGIN or sm.quick_ratio() < MIN_RATIO - MARGIN:
            continue
        scored.append((sm.ratio(), cand))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < MIN_RATIO:
        return None
    best, cand = scored[0]
    if len(scored) > 1 and best - scored[1][0] < MARGIN:
        return None
    hits = vocab[cand]
    return Match(*hits[0], best) if len(hits) == 1 else None


# --- layout ---------------------------------------------------------------------------------

def _clusters(values: list[float], gap: float) -> list[list[float]]:
    out: list[list[float]] = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= gap:
            out[-1].append(v)
        else:
            out.append([v])
    return out


def assemble(lines: list[Line], dex: Dex) -> SheetRead:
    """Group OCR lines into a sheet. Species names anchor the cards; the rest is placed by position."""
    vocab = _vocab(dex)
    matched = [(ln, match(ln.text, vocab)) for ln in lines]
    anchors = [ln for ln, m in matched if m and m.kind == "species"]
    if not anchors:
        return SheetRead(None, [], notes=["no species name found: is this a team sheet?"])

    h = statistics.median(ln.h for ln in lines)
    # Columns of cards, from where their species names start; rows, from where they sit.
    col_x = [min(c) for c in _clusters([a.x0 for a in anchors], 3 * h)]
    col_w = min((b - a for a, b in zip(col_x, col_x[1:])), default=float("inf"))
    row_y = [min(r) for r in _clusters([a.y0 for a in anchors], h)]
    row_h = min((b - a for a, b in zip(row_y, row_y[1:])), default=float("inf"))

    def column(ln: Line) -> int | None:
        for i, x in enumerate(col_x):
            if x - h <= ln.cx < x - h + col_w:
                return i
        return None

    def row(y: float) -> int | None:
        for i in reversed(range(len(row_y))):
            if row_y[i] <= y < row_y[i] + row_h:
                return i
        return None

    cards: dict[tuple[int, int], dict[str, Any]] = {}
    for a in anchors:
        key = (row(a.y0), column(a))
        if key in cards:
            return SheetRead(None, [], notes=[f"two species names in one card: {cards[key]['anchor'].text!r}, {a.text!r}"])
        cards[key] = {"anchor": a, "species": match(a.text, vocab).name, "lines": []}

    title_lines, unread, notes = [], [], []
    for ln, m in matched:
        if ln in anchors:
            continue
        col = column(ln)
        if col is None:
            continue  # off the sheet: a facecam, a logo, the rest of the frame
        if ln.cy < row_y[0]:
            title_lines.append(ln)
            continue
        r = row(ln.cy)
        if (r, col) not in cards:
            continue
        cards[(r, col)]["lines"].append((ln, m))

    entries = []
    for (r, col) in sorted(cards):
        card = cards[(r, col)]
        entry = SheetEntry(species=card["species"])
        found: dict[str, list[tuple[Line, str]]] = {"ability": [], "nature": [], "item": [], "move": []}
        leftover = []
        for ln, m in card["lines"]:
            if m and m.kind in found:
                found[m.kind].append((ln, m.name))
            else:
                leftover.append(ln)
        # A nature's stat labels follow it on its row ("▲Attack ▼Sp. Atk"). The nature already
        # says what they say, so they're neither read nor reported.
        if len(found["nature"]) == 1:
            nat = found["nature"][0][0]
            leftover = [ln for ln in leftover if not (abs(ln.cy - nat.cy) < h / 2 and ln.x0 > nat.x1)]
        for kind in ("ability", "nature", "item"):
            names = {n for _, n in found[kind]}
            if len(names) == 1:
                setattr(entry, kind, names.pop())
            elif names:
                notes.append(f"{entry.species}: more than one {kind} read ({', '.join(sorted(names))}); left blank")
        moves = [n for _, n in sorted(found["move"], key=lambda t: (round(t[0].y0 / h), t[0].x0))]
        if len(moves) > MOVES_PER_SET:
            notes.append(f"{entry.species}: {len(moves)} moves read; left blank")
        else:
            entry.moves = moves
        if entry.missing():
            notes.append(f"{entry.species}: missing {', '.join(entry.missing())}")
        unread += leftover
        entries.append(entry)

    title = max(title_lines, key=lambda ln: ln.h).text if title_lines else None
    return SheetRead(title, entries, unread, notes)


_vocab = lru_cache(maxsize=4)(vocabulary)  # a Dex hashes by identity, and a regulation keeps one


# --- OCR ------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _engine():
    try:
        from rapidocr import RapidOCR
    except ImportError as e:  # an optional dependency: pip install -e '.[video]'
        raise RuntimeError("reading a sheet needs RapidOCR: pip install -e '.[video]'") from e
    # The detector's default only ever scales a frame up, so a full-size capture (2342×1324) was
    # read whole: 670 MB at its peak, which a few queued reads took past a 2 GB server. Capping the
    # long side at 1280 halved that, ran faster, and read two moves the full size left blank.
    return RapidOCR(params={"Det.limit_side_len": 1280, "Det.limit_type": "max", "Global.log_level": "error"})


_ENGINE_LOCK = threading.Lock()  # one engine, shared by the web app's worker threads


def ocr(image: str | Path | bytes) -> list[Line]:
    """Every line of text in the image, with its box."""
    engine = _engine()
    with _ENGINE_LOCK:
        out = engine(str(image) if isinstance(image, Path) else image)
    if out.boxes is None:
        return []
    return [Line(str(text), float(score), float(min(p[0] for p in box)), float(min(p[1] for p in box)),
                 float(max(p[0] for p in box)), float(max(p[1] for p in box)))
            for box, text, score in zip(out.boxes, out.txts, out.scores)]


def read_sheet(image: str | Path | bytes, reg: Regulation) -> SheetRead:
    return assemble(ocr(image), reg.dex)


def problems(read: SheetRead, reg: Regulation) -> list[Any]:
    """The validator's verdict on what was read, without the Stat Point checks a sheet can't meet."""
    from vgc.teams.showdown_text import parse_team
    from vgc.teams.validate import validate_team

    if not any(e.species for e in read.entries):
        return []
    return [p for p in validate_team(parse_team(read.showdown()), reg) if p.code not in _SP_CODES]
