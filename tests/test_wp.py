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


def test_identity_dropout_can_spare_preview_rows():
    """Identity dropout stops the encoder memorising repeated self-play pairings, but before the
    battle starts the teams are the whole input, so masking there only destroys the preview signal.
    Omitting the flag must leave the old uniform behaviour untouched."""
    torch = pytest.importorskip("torch")
    from vgc.wp.set_torch import UNK, _batch, _tensors

    n, rng = 4000, np.random.default_rng(0)
    kind = np.repeat([0, 1, 2, 3], n // 4)  # preview, bring, turn, switch
    data = _tensors({"cat": rng.integers(2, 50, size=(n, 12, 8)), "num": rng.random((n, 12, 150)).astype(np.float16),
                     "glob": rng.random((n, 42)).astype(np.float32), "y": rng.random(n).astype(np.float32),
                     "bring": np.full((n, 12), -1, np.float32), "source": rng.integers(0, 2, n), "kind": kind})
    idx = np.arange(n)
    spared = _batch(data, idx, id_dropout=0.5, id_dropout_preview=0.0)[0].numpy()[..., 0]
    assert (spared[kind <= 1] == UNK).mean() == 0
    assert 0.45 < (spared[kind >= 2] == UNK).mean() < 0.55
    uniform = _batch(data, idx, id_dropout=0.5)[0].numpy()[..., 0]
    assert 0.45 < (uniform[kind <= 1] == UNK).mean() < 0.55


def _vs_const(delta, half=0.004):
    """A `vs_constant` block: `beats` is true only when the whole interval is below zero."""
    return {"n": 2899, "battles": 1450, "delta": delta, "se": round(half / 1.96, 5),
            "delta_95ci": [delta - half, delta + half], "beats": bool(delta + half < 0)}


def _spectator(by_turn, all_logloss=0.55, all_ece=0.01, normal_ece=0.02, preview_logloss=0.69315,
               preview_half=0.004):
    """`by_turn` covers the in-battle buckets; the preview bucket is added here because every real
    evaluation has one and the preview gate is scored on it."""
    by = {b: dict(v, vs_constant=_vs_const(round(v["logloss"] - 0.69315, 5), 0.01))
          for b, v in by_turn.items()}
    return {"human_ots_all": {"perspectives": {"spectator": {
        "all": {"logloss": all_logloss, "ece": all_ece},
        "ended_normal": {"logloss": all_logloss + 0.01, "ece": normal_ece, "n": 100},
        "by_turn": dict(by, preview={"n": 2899, "logloss": preview_logloss, "ece": 0.008,
                                     "vs_constant": _vs_const(round(preview_logloss - 0.69315, 5),
                                                              preview_half)})}}}}


def test_gates_separate_in_battle_from_preview():
    """A model can be trustworthy turn by turn and useless at team preview — `wp-v1-gbt` is
    exactly that, scoring the constant to five decimals at preview and beating it in every turn
    bucket. One pooled verdict would report the working half as failing."""
    from vgc.wp.evaluate import IN_BATTLE_BUCKETS, gates

    good = {b: {"logloss": 0.60, "ece": 0.02} for b in IN_BATTLE_BUCKETS}
    const = _spectator({b: {"logloss": 0.69315, "ece": 0.01} for b in IN_BATTLE_BUCKETS},
                       all_logloss=0.69315)

    g = gates(_spectator(good), {"constant": const})
    assert g["in_battle_pass"] is True
    assert g["all_pass"] is False  # the preview bucket ties the constant, so it does not beat it
    assert g["preview_beats_constant"]["pass"] is False
    assert g["preview_beats_constant"]["n"] == 2899

    # One miscalibrated bucket sinks it, even though the pooled ECE is fine.
    bad = dict(good, **{"t7+": {"logloss": 0.60, "ece": 0.05}})
    assert gates(_spectator(bad), {"constant": const})["in_battle_pass"] is False

    # Losing to the constant in any single bucket sinks it, and the gate names the bucket.
    worse = dict(good, **{"t1-2": {"logloss": 0.70, "ece": 0.02}})
    g3 = gates(_spectator(worse), {"constant": const})
    assert g3["in_battle_beats_constant"]["pass"] is False
    assert g3["in_battle_beats_constant"]["losing_buckets"] == ["t1-2"]

    # A third of held-out rows are forfeits and they are easier, so a model that is calibrated
    # only once they are mixed in must not pass.
    assert gates(_spectator(good, normal_ece=0.04), {"constant": const})["in_battle_pass"] is False

    # The preview gate is scored on the preview bucket against the constant, with its n recorded —
    # not on a correlation over 30 simulated pairings, which is what `preview_tracks_sim` did and
    # is why it was retired. A model that did beat the constant before turn 1 would pass.
    beats = gates(_spectator(good, preview_logloss=0.68), {"constant": const})
    assert beats["preview_beats_constant"]["pass"] is True and beats["all_pass"] is True
    assert "preview_tracks_sim" not in beats

    # And the margin has to clear its own interval. `wp-v1-set-full` really does score 0.68928
    # against 0.69315 at preview; on ~1,450 battles that 0.004 is inside the noise, and finding 1
    # says every model family lands within 0.004 of the constant there. A gate reading the point
    # estimate would flip on which side of zero the noise fell.
    noisy = gates(_spectator(good, preview_logloss=0.68928, preview_half=0.006), {"constant": const})
    assert noisy["preview_beats_constant"]["pass"] is False
    assert noisy["preview_beats_constant"]["delta_95ci"][1] > 0


def test_vs_constant_clusters_by_battle():
    """A battle contributes several decision points and the label is the same winner at all of
    them, so the rows are not independent. On the real held-out set this widens the interval by
    1.3× at t1–2 and 2.1× at t7+; the synthetic below exaggerates it to make the mechanism visible."""
    from vgc.wp.evaluate import vs_constant

    rng = np.random.default_rng(0)
    nb, per = 200, 12
    battle = np.repeat(np.arange(nb), per)
    # The label is the battle's winner, so it is *constant* across a battle's rows — that is what
    # makes the correlation strong rather than incidental.
    y = np.repeat((rng.random(nb) < 0.5).astype(float), per)
    # Predictions drift as a real WP track does — toward the eventual winner in most battles, away
    # from it in some. The confidence is drawn per battle, so whole battles are right or wrong
    # together and the row-level independence assumption is the thing being violated.
    conf = rng.normal(0.25, 0.18, nb).clip(-0.45, 0.45)
    sharp = (np.repeat(conf, per) * np.tile(np.linspace(0.2, 1.0, per), nb))
    p = np.clip(np.where(y > 0.5, 0.5 + sharp, 0.5 - sharp), 0.02, 0.98)

    clustered = vs_constant(p, y, battle)
    independent = vs_constant(p, y, np.arange(nb * per))
    assert clustered["battles"] == nb and independent["battles"] == nb * per
    assert clustered["delta"] == independent["delta"]  # same point estimate
    assert clustered["se"] > 2 * independent["se"], "clustering must widen the interval"

    # A model that is exactly the constant has delta 0 and cannot claim to beat it.
    flat = vs_constant(np.full(nb * per, 0.5), y, battle)
    assert flat["delta"] == 0 and flat["beats"] is False
    assert vs_constant(np.array([]), np.array([]), np.array([]))["n"] == 0


def test_metrics():
    y = np.array([1, 0, 1, 0], float)
    m = metrics(np.full(4, 0.5), y)
    assert math.isclose(m["logloss"], math.log(2), rel_tol=1e-4) and m["brier"] == 0.25 and m["ece"] == 0
    rng = np.random.default_rng(0)
    p = rng.uniform(size=20000)
    calibrated = metrics(p, (rng.uniform(size=20000) < p).astype(float))
    assert calibrated["ece"] < 0.02
    assert metrics(np.clip(p + 0.2, 0, 1), (rng.uniform(size=20000) < p).astype(float))["ece"] > 0.1


def test_onnx_export_works_on_this_torch(tmp_path):
    """The export must not depend on which torch is installed.

    torch >= 2.6 defaults to the dynamo exporter, which needs `onnxscript`; a Kaggle GPU image has
    neither it nor the internet to fetch it. A cloud sweep discovered this after 25 minutes of
    training, so `train()` now probes the export before the first epoch — and this test covers the
    probe itself on whatever torch is present.
    """
    torch = pytest.importorskip("torch")
    from vgc.wp.set_torch import SetWP, _export

    model = SetWP({"species": 50, "items": 20, "abilities": 20, "moves": 60}, 150, 42, d=32, layers=1).eval()
    sample = (torch.randint(2, 20, (2, 12, 8)), torch.rand(2, 12, 150), torch.rand(2, 42))
    out = tmp_path / "m.onnx"
    _export(model, sample, out)
    assert out.stat().st_size > 1000


def test_eval_fingerprint_identifies_the_scored_rows(tmp_path):
    """Comparability between two registry rows is a question about the rows they were scored on.

    The training manifest cannot answer it: it carries a `created` timestamp, so rebuilding it
    from identical data changes the hash and every model looks incomparable — which is how the
    warning came to fire on every row at once and stop meaning anything.
    """
    from vgc.wp.models import eval_fingerprint

    a, b = tmp_path / "eval_x.npz", tmp_path / "eval_y.npz"
    a.write_bytes(b"rows-a"), b.write_bytes(b"rows-b")
    first = eval_fingerprint([a, b])
    assert first["sha256"] == eval_fingerprint([b, a])["sha256"]  # order must not matter

    a.write_bytes(b"rows-a")  # rewriting identical bytes is not a change
    assert eval_fingerprint([a, b])["sha256"] == first["sha256"]

    a.write_bytes(b"rows-a!")  # different rows are
    assert eval_fingerprint([a, b])["sha256"] != first["sha256"]

    # A file appearing or vanishing changes the set that was scored.
    assert eval_fingerprint([b])["sha256"] != first["sha256"]
