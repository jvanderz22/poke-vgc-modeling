"""What the video reader couldn't read, or read on a guess the footage hasn't confirmed, and the
moments you flagged while watching: one log to come back to.

Three kinds of line, appended to `data/review/video.jsonl`:

  * `missed`: on screen and not placed (`Assembly.missed`): a message no phrasing matched, a
    plate whose name wasn't on the sheet, a faint worked out rather than shown. `why` says which.
  * `provisional`: a message read by a phrasing no footage had confirmed yet (`said.CONFIRMED`).
    It was used; the line is there so the first one of each can be checked against the video, and
    the phrasing confirmed or fixed.
  * `flag`: a moment marked from the watch page, with your note: something the page got wrong or
    didn't catch.

Each line carries where it came from (`source`: a clip, a YouTube id) and when (`t`, seconds into
the clip; `video_s`, seconds into the video, where known), so `vgc video review` can say where to
look. The log is personal state, like the library and the battles: gitignored, and on the volume
when deployed.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vgc import paths

KINDS = ("missed", "provisional", "flag")
# Misses that are OCR catching a fragment, not something the reader should have understood.
QUIET = {"noise"}


def log_path() -> Path:
    return paths.DATA / "review" / "video.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append(lines: list[dict[str, Any]]) -> int:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for line in lines:
            f.write(json.dumps(line, default=str) + "\n")
    return len(lines)


def record(assembly: Any, source: str) -> int:
    """Log a read's misses and provisional matches. Returns how many lines were written."""
    at = _now()
    lines = [{"at": at, "kind": "missed", "source": source, "t": m["t"], "why": m["why"],
              "text": m["what"] if isinstance(m["what"], str) else json.dumps(m["what"], default=str)}
             for m in assembly.missed]
    lines += [{"at": at, "kind": "provisional", "source": source, "t": p["t"], "pattern": p["pattern"],
               "text": p["text"]} for p in assembly.provisional]
    return _append(lines)


def flag(source: str, video_s: float | None, note: str = "", t: float | None = None) -> dict[str, Any]:
    """A moment you marked while watching."""
    line = {"at": _now(), "kind": "flag", "source": source, "t": t, "video_s": video_s, "text": note.strip()}
    _append([line])
    return line


def load(kind: str | None = None) -> list[dict[str, Any]]:
    path = log_path()
    if not path.exists():
        return []
    out = []
    for raw in path.read_text().splitlines():
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if kind is None or line.get("kind") == kind:
            out.append(line)
    return out


def _shape(text: str) -> str:
    """A message with its names and numbers blurred, so the same miss on two Pokémon is one row."""
    return re.sub(r"\d+", "#", text.strip())[:120]


def summary(lines: list[dict[str, Any]], *, quiet: bool = True) -> dict[str, list[dict[str, Any]]]:
    """Lines grouped for reading: misses by reason and text, provisional matches by phrasing,
    flags newest first. Each group has a count and up to three places to look."""
    groups: dict[str, dict[tuple, dict[str, Any]]] = {"missed": {}, "provisional": {}}
    flags = []
    for line in lines:
        kind = line.get("kind")
        where = {"source": line.get("source"), "t": line.get("t"), "video_s": line.get("video_s")}
        if kind == "flag":
            flags.append({**where, "note": line.get("text", ""), "at": line.get("at")})
            continue
        if kind == "missed" and quiet and line.get("why") in QUIET:
            continue
        key = (line.get("why"), _shape(line.get("text", ""))) if kind == "missed" else (line.get("pattern"),)
        g = groups.setdefault(kind, {}).setdefault(key, {
            **({"why": line.get("why"), "text": line.get("text")} if kind == "missed"
               else {"pattern": line.get("pattern"), "text": line.get("text")}),
            "count": 0, "where": []})
        g["count"] += 1
        if len(g["where"]) < 3:
            g["where"].append(where)
    out = {k: sorted(v.values(), key=lambda g: -g["count"]) for k, v in groups.items()}
    out["flag"] = sorted(flags, key=lambda f: f.get("at") or "", reverse=True)
    return out
