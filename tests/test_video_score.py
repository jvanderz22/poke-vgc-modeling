"""Scoring a read journal against a hand transcription (PLAN-video step 3)."""

from __future__ import annotations

import pytest

from vgc.video import score

LEADS = [{"kind": "lead", "side": s, "slot": i, "species": sp}
         for s, pair in (("p1", ("Sneasler", "Gardevoir")), ("p2", ("Raichu", "Grimmsnarl"))) for i, sp in enumerate(pair)]


def turn(n, *entries):
    return [{"kind": "turn", "n": n}, *entries]


def move(side, slot, name, target=None):
    return {"kind": "move", "side": side, "slot": slot, "move": name, "target": target, "spread": False}


def dmg(side, slot, pct):
    return {"kind": "damage", "side": side, "slot": slot, "pct": pct, "fainted": pct == 0}


HAND = LEADS + turn(1, move("p1", 0, "Close Combat", {"side": "p2", "slot": 0}), dmg("p2", 0, 20),
                    move("p2", 1, "Snarl"), dmg("p1", 0, 86)) \
             + turn(2, move("p2", 0, "Volt Tackle", {"side": "p1", "slot": 0}), dmg("p1", 0, 0),
                    {"kind": "faint", "side": "p1", "slot": 0}) \
             + turn(3, {"kind": "switch", "side": "p1", "slot": 0, "species": "Indeedee-F"},
                    move("p1", 1, "Hyper Voice"), dmg("p2", 0, 0))


def test_a_read_that_matches_is_all_matched():
    s = score.compare(HAND, HAND)
    assert sum(c["wrong"] + c["missed"] for c in s.kinds.values()) == 0
    assert s.turns_paired == 3 and s.hp_within() == (4, 4)


def test_a_missed_decision_point_does_not_shift_the_turns_after_it():
    # The reader never saw turn 2's marker: its entries land in turn 1.
    read = [e for e in HAND if not (e.get("kind") == "turn" and e.get("n") == 2)]
    s = score.compare(read, HAND)
    assert s.turns_read == 2 and s.turns_hand == 3
    # Turn 3 still pairs; only turns 1 and 2 disagree, and nothing is double-counted.
    assert s.kinds["switch"]["matched"] == 1
    assert s.kinds["move"]["read"] == s.kinds["move"]["hand"] == 4


def test_a_wrong_switch_fails_g_read():
    read = [dict(e, species="Gengar") if e.get("kind") == "switch" else e for e in HAND]
    s = score.compare(read, HAND)
    assert s.kinds["switch"]["wrong"] == 1 and s.kinds["switch"]["missed"] == 1
    assert score.gates([s])["G-read"]["verdict"] == "fail"


def test_hp_two_points_off_counts_against_g_hp_and_a_wrong_target_against_moves():
    read = [dict(e, pct=84) if e.get("kind") == "damage" and e["pct"] == 86 else e for e in HAND]
    read = [dict(e, target={"side": "p2", "slot": 1}) if e.get("move") == "Close Combat" else e for e in read]
    s = score.compare(read, HAND)
    assert s.hp_within() == (3, 4)
    assert s.move_wrong() == (1, 4)


def test_answers_and_causes_are_not_claims():
    read = HAND + [{"kind": "answer", "question": "q9.0", "option": 0}]
    hand = [dict(e, eot="leftovers") if e.get("kind") == "damage" else e for e in HAND]
    s = score.compare(read, hand)
    assert sum(c["wrong"] + c["missed"] for c in s.kinds.values()) == 0


@pytest.mark.parametrize("n", [30, 300, 1000])
def test_none_seen_bounds_the_rate_at_about_three_over_n(n):
    lo, hi = score.interval(0, n)
    assert lo == 0 and 2.8 / n < hi < 3.0 / n


def test_a_gate_passes_only_when_its_whole_interval_does():
    assert score._verdict(0, 15, 0.01) == "undecided"   # 15 clean reads can't show 99%
    assert score._verdict(0, 400, 0.01) == "pass"
    assert score._verdict(10, 50, 0.01) == "fail"
    assert score._verdict(0, 0, 0.01) == "undecided"
