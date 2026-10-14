from __future__ import annotations

import json
from pathlib import Path

import pytest

from vgc.video.clip import ClipTimes, frames


def _times(samples: list[tuple[int, float | None, bool]]) -> ClipTimes:
    return ClipTimes("vid", [{"ms": ms, "video": v, "playing": p} for ms, v, p in samples])


def test_a_playing_video_advances_from_the_sample_before():
    t = _times([(0, 100.0, True), (200, 100.2, True), (400, 100.4, True)])
    assert t.at(0.25) == (100.25, True)


def test_a_paused_video_stays_where_it_was():
    t = _times([(0, 100.0, True), (200, 100.2, False), (400, 100.2, False)])
    assert t.at(0.35) == (100.2, False)


def test_no_sample_near_enough_says_nothing():
    t = _times([(0, 100.0, True), (5000, 105.0, True)])
    assert t.at(2.5) == (None, False)
    assert _times([]).at(1.0) == (None, False)
    assert _times([(0, None, False)]).at(0.0) == (None, False)


def _write_clip(path: Path, seconds: float, fps: int = 10) -> None:
    """A clip like the watch page's: VP8 in WebM, each frame a flat grey set by its index."""
    av = pytest.importorskip("av")
    import numpy as np

    with av.open(str(path), "w", format="webm") as out:
        s = out.add_stream("libvpx", rate=fps)
        s.width, s.height, s.pix_fmt = 64, 36, "yuv420p"
        for i in range(int(seconds * fps)):
            img = np.full((36, 64, 3), (i * 7) % 256, dtype=np.uint8)
            for packet in s.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                out.mux(packet)
        for packet in s.encode():
            out.mux(packet)


def test_frames_are_taken_every_second_and_placed_in_the_video(tmp_path):
    clip = tmp_path / "vid-1m40s.webm"
    _write_clip(clip, seconds=3.0)
    samples = [{"ms": ms, "video": 100 + ms / 1000, "playing": True} for ms in range(0, 3200, 200)]
    clip.with_suffix(".json").write_text(json.dumps({"video": "vid", "times": samples}))

    got = list(frames(clip, every=1.0))
    assert [f.clip_s for f in got] == [0.0, 1.0, 2.0]
    assert [f.video_s for f in got] == [100.0, 101.0, 102.0]
    assert got[0].image.size == (64, 36)


def test_without_its_times_a_clip_still_gives_frames(tmp_path):
    clip = tmp_path / "loose.webm"
    _write_clip(clip, seconds=1.0)
    got = list(frames(clip, every=0))
    assert len(got) == 10 and all(f.video_s is None for f in got)
