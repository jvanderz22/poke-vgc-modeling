"""Phase 4: features, orientation/symmetry, leakage, metrics."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import numpy as np
import pytest

from vgc.data.snapshots import human_snapshots
from vgc.wp.evaluate import metrics
from vgc.wp.features import KINDS, Featurizer, featurize, train_orientations
from vgc.wp.models import ConstantModel, symmetrize

REPLAY = Path(__file__).parent / "fixtures" / "replays" / "gen9championsvgc2026regmcbo3-2684057197.json"


@pytest.fixture(scope="module")
def fz(reg):
    return Featurizer(reg)


@pytest.fixture(scope="module")
def records(reg):
    return human_snapshots(json.loads(REPLAY.read_text()), reg)


def test_orientations_mirror_each_other(fz, records):
    rec = next(r for r in records if r["kind"] == "turn" and r["obs"]["perspective"] == "spectator" and r["obs"]["turn"] == 3)
    cat1, num1, g1 = fz.row(rec, "p1")
    cat2, num2, g2 = fz.row(rec, "p2")
    assert (cat1[:6] == cat2[6:]).all() and (cat1[6:] == cat2[:6]).all()
    # Same Pokémon, same numbers, except the "is me" flag.
    assert np.allclose(num1[:6, 1:], num2[6:, 1:]) and num1[0, 0] == 1 and num2[6, 0] == 0


def test_labels_never_reach_inputs(fz, records):
    rec = next(r for r in records if r["kind"] == "turn")
    flipped = copy.deepcopy(rec)
    flipped["label"]["winner"] = "p1" if rec["label"]["winner"] == "p2" else "p2"
    flipped["label"]["brought"] = {"p1": [], "p2": []}
    a, b = featurize([rec], fz), featurize([flipped], fz)
    for k in ("cat", "num", "glob"):
        assert (a[k] == b[k]).all()
    assert (a["y"] != b["y"]).all()


def test_unknowns_are_explicit_for_spectator(fz, records):
    rec = next(r for r in records if r["kind"] == "preview" and r["obs"]["perspective"] == "spectator")
    d = featurize([rec], fz)
    num = d["num"][0].astype(np.float32)
    assert (num[:, 4] == 1).all()  # every Pokémon "unrevealed" at preview
    assert (num[:, 9] == 0).all()  # no exact HP for anyone
    assert d["kind"][0] == KINDS.index("preview") and len(d["y"]) == 2  # two orientations


def test_bring_rows_know_own_four(fz, records):
    rec = next(r for r in records if r["kind"] == "bring")
    d = featurize([rec], fz)
    own = d["num"][0, :6].astype(np.float32)
    assert own[:, 1].sum() == 2 and own[:, 2].sum() == 2 and own[:, 5].sum() == 2  # 2 lead, 2 back, 2 not brought
    assert (d["bring"][0, :6] == -1).all()  # nothing to predict about our own bring


def test_thinning_is_deterministic_and_keeps_preview(fz, reg):
    rec = {"battle": "b", "source": "selfplay", "point": 3, "kind": "turn", "obs": {"perspective": "spectator"}}
    assert train_orientations(rec, fz) == train_orientations(rec, fz)
    kept = [train_orientations(rec | {"point": i}, fz) for i in range(400)]
    assert 0.2 < np.mean([bool(k) for k in kept]) < 0.4 and all(len(k) <= 1 for k in kept)
    assert train_orientations(rec | {"kind": "preview"}, fz)


def test_spectator_symmetrization(fz, records):
    d = featurize([r for r in records if r["obs"]["perspective"] == "spectator"], fz)
    p = np.where(d["orient"] == 0, 0.7, 0.4)  # A=p1 says 70%; A=p2 says 40% for p2 → 60% for p1
    s = symmetrize(d, p)
    assert len(s["p"]) == len(d["y"]) // 2 and np.allclose(s["p"], 0.65)
    assert np.allclose(symmetrize(d, ConstantModel().predict(d)[0])["p"], 0.5)


def test_metrics():
    y = np.array([1, 0, 1, 0], float)
    m = metrics(np.full(4, 0.5), y)
    assert math.isclose(m["logloss"], math.log(2), rel_tol=1e-4) and m["brier"] == 0.25 and m["ece"] == 0
    rng = np.random.default_rng(0)
    p = rng.uniform(size=20000)
    calibrated = metrics(p, (rng.uniform(size=20000) < p).astype(float))
    assert calibrated["ece"] < 0.02
    assert metrics(np.clip(p + 0.2, 0, 1), (rng.uniform(size=20000) < p).astype(float))["ece"] > 0.1
