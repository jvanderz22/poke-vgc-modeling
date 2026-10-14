from __future__ import annotations

import pytest

from vgc import paths
from vgc.video import review
from vgc.video.assemble import Assembly


@pytest.fixture(autouse=True)
def log_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)


def test_a_read_logs_what_it_missed_and_what_it_read_provisionally():
    a = Assembly(missed=[{"t": 271.4, "what": "Thie opposing Naichu tt damagod", "why": "message not understood"},
                         {"t": 208.3, "what": "一", "why": "noise"}],
                 provisional=[{"t": 775.4, "pattern": "failed", "text": "But it failed!"}])
    assert review.record(a, "r6.mov") == 3
    s = review.summary(review.load())
    assert [g["pattern"] for g in s["provisional"]] == ["failed"]
    # Noise is kept in the log and left out of the summary unless asked for.
    assert [g["why"] for g in s["missed"]] == ["message not understood"]
    assert len(review.summary(review.load(), quiet=False)["missed"]) == 2


def test_the_same_miss_on_different_numbers_is_one_row():
    a = Assembly(missed=[{"t": t, "what": {"slot": "L1", "name": "dee", "hp": hp}, "why": "plate name not on the sheet"}
                         for t, hp in ((329.6, 10), (660.2, 38), (715.2, 38))])
    review.record(a, "r5.mov")
    (row,) = review.summary(review.load())["missed"]
    assert row["count"] == 3 and len(row["where"]) == 3


def test_a_flag_from_the_watch_page():
    review.flag("youtube:abc123", 754.2, "Rotom's switch was missed")
    (f,) = review.summary(review.load())["flag"]
    assert (f["source"], f["video_s"], f["note"]) == ("youtube:abc123", 754.2, "Rotom's switch was missed")
