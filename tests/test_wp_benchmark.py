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


def test_the_solver_finds_the_priority_move_and_the_lock(reg, spec):
    """Four positions whose answer does not depend on luck or on their spread: Extreme Speed on the
    sheet loses it and its absence wins it (F5), and the choice lock decides F2 the same way. The
    Fake Out a fresh Sneasler would have is the reason F5 needs the Pokémon to have been out.

    Three turns deep, so a Protect stall is still undecided a ninth of the time (1/3 per extra
    Protect) and the answer is near 0 or 1 rather than on it; `leaf_mass` says by how much."""
    from vgc.wp.solver import solve

    keys = ["F5/A", "F5/B", "F2/A", "F2/B"]
    got = solve(reg, workers=4, only=keys, search={"depth": 3})
    want = {"F5/A": 0.0, "F5/B": 1.0, "F2/A": 0.0, "F2/B": 1.0}
    for k in keys:
        assert abs(got[k]["value"] - want[k]) <= got[k]["leaf_mass"] + 1e-6, (k, got[k])
    assert abs(got["F5/C"]["value"] - (0.96 * got["F5/A"]["value"] + 0.04 * got["F5/B"]["value"])) < 1e-3


def test_a_speed_ordering_removes_the_speed_it_rules_out(reg, spec):
    """F1-D: their Scarf Basculegion moved before our 149-Speed Gholdengo, so every Speed class it
    could be in is at least that; unconditioned (B), the prior still has Scarf sets slower than the
    Mega Charizard."""
    from vgc.wp.solver import speed_classes

    b, d = speed_classes(reg, spec, "F1", "B"), speed_classes(reg, spec, "F1", "D")
    assert set(b) >= {"faster", "slower"}
    assert all(c["speed"] >= 149 for c in d.values())
    assert d.get("faster", {}).get("weight", 0) > b["faster"]["weight"]


def test_invariance_variants_are_their_base_plus_one_change(reg, spec):
    base = _state(reg, spec, "F4", "A")
    changed = _state(reg, spec, "F9", "A")
    assert changed.turn == base.turn and changed.pseudo == base.pseudo
    inc = next(m for m in changed.sides["p2"].mons if m.species == "Incineroar")
    assert inc.state == "fainted" and inc.item == "sitrusberry"
