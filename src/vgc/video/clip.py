"""Frames out of a clip recorded on the watch page, each placed in the video it came from.

The page saves a clip as `<video>-<time>.webm` and, beside it, `<video>-<time>.json`: the video's
id, the capture size, and the player's own time sampled every 200 ms (`ms` from the recording's
start, `video` in seconds, `playing`). A frame's video time is read off the nearest sample, so
anything read from it can be lined up with the video, and with a hand transcription of the same
battle (PLAN-video step 3).
"""

from __future__ import annotations

import bisect
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# A sample further than this from a frame says nothing about it: the page was busy, or the
# recording outran its samples.
MAX_GAP_S = 0.5


@dataclass(frozen=True)
class Frame:
    clip_s: float  # seconds from the start of the clip
    video_s: float | None  # the video's own time, None when no sample is near enough
    playing: bool
    image: Any  # PIL.Image, RGB


@dataclass
class ClipTimes:
    video: str | None
    samples: list[dict[str, Any]]

    @classmethod
    def load(cls, path: Path) -> "ClipTimes":
        meta = json.loads(path.read_text())
        return cls(meta.get("video"), sorted(meta.get("times", []), key=lambda t: t["ms"]))

    def at(self, clip_s: float) -> tuple[float | None, bool]:
        """The video time and play state at `clip_s` seconds into the clip.

        While the video plays, its time advances with the clip's from the sample before; while
        it's paused, it stays where the sample says."""
        if not self.samples:
            return None, False
        ms = clip_s * 1000
        keys = [t["ms"] for t in self.samples]
        i = bisect.bisect_right(keys, ms) - 1
        near = [j for j in (i, i + 1) if 0 <= j < len(self.samples)]
        j = min(near, key=lambda k: abs(keys[k] - ms))
        s = self.samples[j]
        if s["video"] is None or abs(keys[j] - ms) > MAX_GAP_S * 1000:
            return None, False
        if s["playing"]:
            return round(s["video"] + (ms - keys[j]) / 1000, 3), True
        return s["video"], False


def times_for(clip: Path) -> Path:
    return clip.with_suffix(".json")


def frames(clip: Path, every: float = 1.0, times: ClipTimes | None = None,
           start: float = 0.0, end: float | None = None) -> Iterator[Frame]:
    """One frame per `every` seconds of the clip (every frame when `every` is 0), from `start` to
    `end` seconds."""
    try:
        import av
    except ImportError as e:  # an optional dependency: pip install -e '.[video]'
        raise RuntimeError("reading a clip needs PyAV: pip install -e '.[video]'") from e
    if times is None and times_for(clip).exists():
        times = ClipTimes.load(times_for(clip))
    next_at = start
    with av.open(str(clip)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        # MediaRecorder's timestamps needn't start at 0: the first frame's is the clip's zero.
        first = next(container.decode(stream)).time or 0.0
        container.seek(int((first + start) / stream.time_base), stream=stream, backward=True) if start else \
            container.seek(0, stream=stream, backward=True)
        for f in container.decode(stream):
            if f.time is None:
                continue
            t = f.time - first
            if end is not None and t > end:
                break
            if t + 1e-6 < next_at:
                continue
            next_at = t + every if every > 0 else 0.0
            video_s, playing = times.at(t) if times else (None, False)
            yield Frame(round(t, 3), video_s, playing, f.to_image())
