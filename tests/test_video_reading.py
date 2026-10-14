"""The battle reader run live, frame by frame (PLAN-video step 4).

The screen layer is stubbed: each frame is the Observation it would have read, taken from
Louisville round 5, so these check what a live stream adds (video time, seeking, hand edits,
the review log) without OCR.
"""

from __future__ import annotations

import pytest

from vgc import paths
from vgc.regulation import load_regulation
from vgc.video import reading as live_reading
from vgc.video import review
from vgc.video.screen import Observation, Plate
from tests.test_video_assemble import SETUP

LEADS_SEEN = {"L0": Plate("Sneasler", 100), "L1": Plate("Gardevoir", 100),
              "R0": Plate("Grimmsnarl", 100), "R1": Plate("Raichu", 100)}


def _script() -> list[tuple[float, Observation]]:
    """Frames at 4 a second: the leads, the first decision point, Close Combat into Raichu."""
    shown = [(174.0, 176.0, Observation(message="The Trainer sent out Raichu and Grimmsnarl the Treasure Hunter!")),
             (179.0, 181.0, Observation(message="Go! Sneasler and Gardevoir!")),
             (182.5, 184.0, Observation(choosing=True, plates=LEADS_SEEN)),
             (250.0, 252.0, Observation(message="Sneasler used Close Combat!")),
             (253.5, 255.0, Observation(plates={"R1": Plate("Raichu", 20)})),
             (256.0, 258.0, Observation(message="Sneasler's Defense and Sp. Def fell!")),
             (260.0, 262.0, Observation(choosing=True,
                                        plates={**LEADS_SEEN, "R1": Plate("Raichu", 20)}))]
    out = []
    t = 173.0
    while t <= 264.0:
        out.append((t, next((obs for start, end, obs in shown if start <= t <= end), Observation())))
        t += 0.25
    return out


@pytest.fixture(scope="module")
def reg():
    return load_regulation("reg_mc")


@pytest.fixture
def logged(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    return tmp_path


def _reading(reg, script):
    r = live_reading.Reading(reg, SETUP, "youtube:test")
    frames = iter(script)
    current = {"looks": 0}

    def observe(_img):
        current["looks"] += 1
        return current["obs"]

    r.screen.observe = observe
    return r, frames, current


def _feed(r, frames, current, until=None):
    journal = []
    for t, obs in frames:
        current["obs"] = obs
        journal += r.frame(t, None).entries
        if until is not None and t >= until:
            break
    return journal


@pytest.fixture(autouse=True)
def _no_scaling(monkeypatch):
    monkeypatch.setattr(live_reading, "scale", lambda img: img)


def test_entries_carry_the_video_time_they_were_read_at(reg, logged):
    r, frames, current = _reading(reg, _script())
    journal = _feed(r, frames, current)
    assert [e["kind"] for e in journal] == ["lead"] * 4 + ["turn", "move", "damage", "turn"]
    move = next(e for e in journal if e["kind"] == "move")
    assert move["video_s"] == 250.0 and "t" not in move
    assert r.read_to is not None and not r.gaps


def test_a_rewind_is_skipped_until_the_video_is_past_what_was_read(reg, logged):
    r, frames, current = _reading(reg, _script())
    journal = _feed(r, frames, current, until=255.0)
    # Back to the Close Combat: nothing is read again.
    current["obs"] = Observation(message="Sneasler used Close Combat!")
    for t in (250.0, 250.25, 250.5):
        s = r.frame(t, None)
        assert s.behind and s.entries == []
    journal += _feed(r, frames, current)
    assert sum(e["kind"] == "move" for e in journal) == 1


def test_a_paused_player_reads_nothing_new(reg, logged):
    r, frames, current = _reading(reg, _script())
    _feed(r, frames, current, until=179.5)
    looks, before = current["looks"], r.read_to
    for _ in range(5):
        r.frame(before, None)
    assert r.read_to == before and current["looks"] == looks


def test_a_jump_forward_is_a_gap_in_the_review_log(reg, logged):
    r, frames, current = _reading(reg, _script())
    _feed(r, frames, current, until=184.0)
    current["obs"] = Observation()
    s = r.frame(300.0, None)
    assert s.gaps == [(184.0, 300.0)]
    assert [m["why"] for m in review.load("missed")] == ["not read: the video skipped ahead"]


def test_a_hand_edit_is_taken_as_the_journal_and_reading_carries_on(reg, logged):
    r, frames, current = _reading(reg, _script())
    journal = _feed(r, frames, current, until=185.0)
    assert journal[-1]["kind"] == "turn" and r.follows(journal)
    # A hand correction: the turn marker taken back and put in again by hand.
    edited = journal[:-1] + [{"kind": "turn", "n": 1}]
    assert r.follows(edited)  # the same entry, without a video time, is the same journal
    edited = journal + [{"kind": "status", "side": "p2", "slot": 0, "status": "par"}]
    assert not r.follows(edited)
    r.resync(edited)
    rest = _feed(r, frames, current)
    assert [e["kind"] for e in rest] == ["move", "damage", "turn"]
    assert r.asm.rp.state.sides["p2"].mons[1].status == "par"  # Raichu, as edited


def test_misses_reach_the_review_log_as_they_happen(reg, logged):
    r, frames, current = _reading(reg, _script())
    _feed(r, frames, current, until=184.0)
    current["obs"] = Observation(message="Thie opposing Naichu tt damagod by the recoil")
    for t in (184.25, 184.5, 184.75):
        r.frame(t, None)
    current["obs"] = Observation()
    r.frame(185.0, None)
    lines = review.load("missed")
    assert [(m["source"], m["why"]) for m in lines] == [("youtube:test", "message not understood")]


def test_the_end_of_the_game_reaches_the_journal_and_reading_stops(reg, logged):
    r, frames, current = _reading(reg, _script())
    journal = _feed(r, frames, current)
    current["obs"] = Observation(message="The battle has ended due to a forfeit.")
    for t in (264.25, 264.5, 264.75):
        journal += r.frame(t, None).entries
    current["obs"] = Observation(message="The Trainer lost!")
    statuses = [r.frame(t, None) for t in (265.0, 265.25, 265.5)]
    journal += [e for s in statuses for e in s.entries]
    assert journal[-1] == {"kind": "end", "winner": "p1", "by": "forfeit", "video_s": 265.0}
    assert statuses[-1].ended and r.frame(266.0, None).entries == []
