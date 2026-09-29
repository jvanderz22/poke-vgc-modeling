"""The decided-endgame benchmark builds each variant as a journal, and the journal has to say what
the variant says: a Speed ordering the variant names reaches `evidence`, a choice lock is the
locked Pokémon's last move, timers read the stated turns, and every variant of a family ends in
the same two Pokémon. None of this needs a model, which is the point: a benchmark that silently
builds the wrong position scores the wrong question."""

from __future__ import annotations

import pytest

from vgc.battle.entry import replay
from vgc.data.snapshots import evidence
from vgc.wp import benchmark


@pytest.fixture(scope="module")
def spec(reg):
    return benchmark.load(reg)


def _variants(spec):
    return [(f["id"], v) for f in spec["families"] for v in f.get("variants") or {}]


def _state(reg, spec, fid, vid):
    setup, journal = benchmark.build(reg, spec, fid, vid)
    rp = replay(reg, setup, journal)
    assert not rp.errors, rp.errors
    return rp.state


def test_every_variant_replays_to_a_1v1(reg, spec):
    for fid, vid in _variants(spec):
        state = _state(reg, spec, fid, vid)
        for sid in ("p1", "p2"):
            active = [m for m in state.sides[sid].mons if m.state == "active"]
            fainted = [m for m in state.sides[sid].mons if m.state == "fainted"]
            assert len(active) == 1 and len(fainted) == 3, (fid, vid, sid)


def test_a_named_ordering_reaches_the_model_input(reg, spec):
    ahead = evidence(reg, _state(reg, spec, "F8b", "B"))["ahead"]
    assert [[a[:2], b[:2]] for a, b in ahead] == [[["p2", "Basculegion"], ["p1", "Gholdengo"]]]
    assert evidence(reg, _state(reg, spec, "F8b", "A"))["ahead"] == []


def test_a_choice_lock_is_the_last_move_and_a_fresh_arrival_has_none(reg, spec):
    assert evidence(reg, _state(reg, spec, "F2", "A"))["last_move"]["p2"] == {"Annihilape": "ragefist"}
    assert evidence(reg, _state(reg, spec, "F2", "C"))["last_move"]["p2"] == {}


def test_timers_read_the_stated_turns(reg, spec):
    for vid, left in (("A", 3), ("B", 1)):
        state = _state(reg, spec, "F7", vid)
        assert state.terrain == "grassyterrain"
        assert benchmark.FIELD_TURNS - (state.turn - state.terrain_since) == left
    assert _state(reg, spec, "F7", "C").terrain is None


def test_a_closed_sheet_shows_only_what_was_revealed(reg, spec):
    theirs = {m.species: m for m in _state(reg, spec, "F1", "C").sides["p2"].mons}
    assert theirs["Basculegion"].item is None
    assert _state(reg, spec, "F1", "F").sides["p2"].mons[0].item == "lifeorb"


def test_invariance_variants_are_their_base_plus_one_change(reg, spec):
    base = _state(reg, spec, "F4", "A")
    changed = _state(reg, spec, "F9", "A")
    assert changed.turn == base.turn and changed.pseudo == base.pseudo
    inc = next(m for m in changed.sides["p2"].mons if m.species == "Incineroar")
    assert inc.state == "fainted" and inc.item == "sitrusberry"
