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


def _ct(ece_by_bucket, floor=0.03):
    """A `calibration_test` block that rejects any bucket whose ECE is past `floor`."""
    rejected = [b for b, e in ece_by_bucket.items() if e >= floor]
    return {"pass": not rejected, "alpha": 0.05, "power": {"logits_1.5x_too_sharp": 0.99},
            "rejected_buckets": rejected,
            "by_bucket": {b: {"ece": e, "floor_median": floor / 2, "threshold": floor, "battles": 500,
                              "p_calibrated_worse": 0.001 if b in rejected else 0.5}
                          for b, e in ece_by_bucket.items()}}


def _spectator(by_turn, all_logloss=0.55, all_ece=0.01, normal_ece=0.02, preview_logloss=0.69315,
               preview_half=0.004):
    """`by_turn` covers the in-battle buckets; the preview bucket is added here because every real
    evaluation has one and the preview gate is scored on it. The calibration tests are stubs that
    reject a bucket past 0.03, standing in for the simulated floor."""
    by = {b: dict(v, vs_constant=_vs_const(round(v["logloss"] - 0.69315, 5), 0.01))
          for b, v in by_turn.items()}
    return {"human_ots_all": {"perspectives": {"spectator": {
        "all": {"logloss": all_logloss, "ece": all_ece},
        "ended_normal": {"logloss": all_logloss + 0.01, "ece": normal_ece, "n": 100},
        "calibration_test": _ct({b: v["ece"] for b, v in by_turn.items()}),
        "calibration_test_played_out": _ct({b: normal_ece for b in by_turn}),
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

    # Played-out games are reported beside the verdict and do not decide it: whether a game will be
    # forfeited is not known when the prediction is made, so no model can be calibrated on that slice.
    g4 = gates(_spectator(good, normal_ece=0.04), {"constant": const})
    assert g4["in_battle_pass"] is True
    assert g4["played_out_calibration"]["ece"] == 0.04 and "pass" not in g4["played_out_calibration"]

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


def _battles(rng, nb: int, per: int, sharpness: float = 1.0):
    """Synthetic held-out rows: `nb` battles of `per` rows, one winner each, split over the four
    in-battle buckets. The model's logit is `sharpness` times the true one, so 1.0 is calibrated
    and anything above it is overconfident."""
    from vgc.wp.evaluate import IN_BATTLE_BUCKETS

    battle = np.repeat(np.arange(nb), per)
    bucket = np.tile(np.array(IN_BATTLE_BUCKETS)[np.arange(per) * 4 // per], nb)
    truth = 1 / (1 + np.exp(-rng.normal(0, 1.2, nb * per)))
    y = (rng.random(nb)[battle] < truth).astype(float)          # one draw per battle
    p = 1 / (1 + np.exp(-np.log(truth / (1 - truth)) * sharpness))
    return p, y, battle, bucket


def test_the_calibration_test_asks_about_the_model_not_the_sample_size():
    """ECE is biased upward on small n, so `ECE < 0.03` on 131 battles could not be passed by a
    model calibrated by construction. The test compares against that model instead: a calibrated
    model passes on few battles, and an overconfident one is caught once there are enough."""
    from vgc.wp.evaluate import ECE_GATE, IN_BATTLE_BUCKETS, _ece, calibration_test

    rng = np.random.default_rng(0)
    p, y, battle, bucket = _battles(rng, 60, 8)
    small = calibration_test(p, y, battle, bucket, IN_BATTLE_BUCKETS, sims=400)
    # Not rejected — where the old threshold would have failed it, because the floor is above it.
    assert not small["rejected_buckets"]
    assert all(r["floor_median"] > ECE_GATE for r in small["by_bucket"].values())
    # But on 60 battles the test could not have caught much either, so that is not a pass.
    assert small["power"]["logits_1.5x_too_sharp"] < 0.8
    assert small["pass"] is None and "too little data" in small["reason"]

    # With enough battles, not rejecting *is* a pass.
    p, y, battle, bucket = _battles(rng, 3000, 8)
    calibrated = calibration_test(p, y, battle, bucket, IN_BATTLE_BUCKETS, sims=400)
    assert calibrated["pass"] is True

    p, y, battle, bucket = _battles(rng, 3000, 8, sharpness=1.6)
    big = calibration_test(p, y, battle, bucket, IN_BATTLE_BUCKETS, sims=400)
    assert big["pass"] is False
    assert big["power"]["logits_1.5x_too_sharp"] > 0.9

    # The closed-sheet gate is this test's verdict, with its power carried beside it.
    from vgc.wp.evaluate import gates

    closed = lambda ct: {"human_closed": {"perspectives": {"spectator": {  # noqa: E731
        "by_turn": {b: {"logloss": 0.6, "ece": 0.09, "n": 100} for b in IN_BATTLE_BUCKETS},
        "calibration_test": ct}}}}
    assert gates(closed(calibrated))["closed_in_battle_ece"]["pass"] is True
    assert gates(closed(big))["closed_in_battle_ece"]["pass"] is False
    undecided = gates(closed(small))["closed_in_battle_ece"]
    assert undecided["pass"] is None and undecided["reason"] == small["reason"]
    assert undecided["power"] == small["power"]
    assert gates(closed(None))["closed_in_battle_ece"]["pass"] is None

    # And so are both open-sheet ECE gates — the in-battle one and the played-out one — whatever
    # the raw ECE says: 0.09 in every bucket here, which the old 0.03 threshold would have failed.
    opened = lambda ct: {"human_ots_all": {"perspectives": {"spectator": {  # noqa: E731
        "by_turn": {b: {"logloss": 0.6, "ece": 0.09, "n": 100} for b in IN_BATTLE_BUCKETS},
        "ended_normal": {"logloss": 0.6, "ece": 0.09, "n": 400},
        "calibration_test": ct, "calibration_test_played_out": ct}}}}
    for gate in ("in_battle_ece",):
        assert gates(opened(calibrated))[gate]["pass"] is True
        assert gates(opened(big))[gate]["pass"] is False
        assert gates(opened(small))[gate]["pass"] is None
        assert gates(opened(None))[gate]["pass"] is None

    # Seeded: the same rows give the same verdict twice.
    again = calibration_test(p, y, battle, bucket, IN_BATTLE_BUCKETS, sims=400)
    assert again == big
    # The fast ECE is `metrics`' ECE.
    assert math.isclose(_ece(p, y), metrics(p, y)["ece"], abs_tol=1e-5)


def test_a_model_is_never_fed_columns_from_another_featurizer_version():
    """`n_num`/`n_glob` catch a column added or removed; they cannot catch one whose meaning moved.
    The version is what does, and a card from before it existed reads as version 1."""
    from vgc.wp.features import Featurizer, check_featurizer, featurizer_version

    assert featurizer_version({}) == 1
    assert check_featurizer({"featurizer_version": Featurizer.VERSION}, "current") == Featurizer.VERSION
    with pytest.raises(ValueError, match="no longer builds"):
        check_featurizer({"featurizer_version": 99}, "future")
    with pytest.raises(ValueError, match="do not mean the same thing"):
        check_featurizer({"featurizer_version": 1}, "old model", against={"featurizer_version": 2})


def test_weather_and_terrain_are_timed_like_trick_room(reg, records):
    """Sun with one turn left and sun with four were the same row until version 2. Version 1 must
    still build exactly its old layout, because every model before it was trained on that."""
    import copy

    from vgc.wp.features import Featurizer

    v1, v2 = Featurizer(reg, version=1), Featurizer(reg, version=2)
    assert v2.version == 2 and v2.n_glob == v1.n_glob + 2
    assert v1.n_glob == 42  # the layout wp-v1 … wp-v1d were trained on

    rec = copy.deepcopy(records[0])
    rec["obs"]["turn"] = 6
    rec["obs"]["field"].update(weather="sunnyday", terrain="grassyterrain")
    rows = {}
    for age in (1, 4):
        r = copy.deepcopy(rec)
        r["obs"]["field"].update(weather_since=6 - age, terrain_since=6 - age)
        rows[age] = (v1.row(r, "p1")[2], v2.row(r, "p1")[2])
    assert np.array_equal(rows[1][0], rows[4][0]), "version 1 cannot tell them apart, by design"
    assert not np.array_equal(rows[1][1], rows[4][1])
    # The logistic baseline reads the last eight columns; the new ones must not move them.
    assert np.array_equal(rows[1][0][-8:], rows[1][1][-8:])


def test_snapshots_carry_what_the_battle_showed_beyond_the_observation(records):
    """Snapshots v4: every record has `evidence`, the orderings only ever pair opposing Pokémon,
    and a last move belongs to a Pokémon on the field. The observation itself is untouched."""
    from vgc.data.snapshots import VERSION

    assert VERSION == 4 and all(r["v"] == 4 and "evidence" in r for r in records)
    pairs = [p for r in records for p in r["evidence"]["ahead"]]
    assert pairs, "the fixture replay has turns that raced"
    assert all(a[0] != b[0] for a, b in pairs)
    for r in records:
        for sid, moves in r["evidence"]["last_move"].items():
            active = {m["species"] for m in r["obs"]["sides"][sid]["mons"] if m["state"] == "active"}
            assert set(moves) <= active
    assert all("evidence" not in r["obs"] and "last_move" not in str(r["obs"]) for r in records)


def test_version_3_puts_the_evidence_on_each_token(reg, records):
    import copy

    from vgc.wp.features import Featurizer

    fz = Featurizer(reg, version=3)
    assert fz.n_num == fz.n_mon + fz.EVIDENCE_WIDTH
    rec = copy.deepcopy(next(r for r in records if r["kind"] == "turn"))
    p1 = next(m for m in rec["obs"]["sides"]["p1"]["mons"] if m["state"] == "active")
    p2 = next(m for m in rec["obs"]["sides"]["p2"]["mons"] if m["state"] == "active")
    key = lambda sid, m: [sid, m["species"], m["forme"] or m["species"]]  # noqa: E731
    known = fz._known_moves(p1)
    rec["evidence"] = {"ahead": [[key("p1", p1), key("p2", p2)]],
                       "last_move": {"p1": {p1["species"]: known[-1]}, "p2": {}}}
    _, num, _ = fz.row(rec, "p1")
    i = rec["obs"]["sides"]["p1"]["mons"].index(p1)
    j = rec["obs"]["sides"]["p2"]["mons"].index(p2)
    base = fz.n_mon
    assert num[i, base + j] == 1 and num[i, base:base + 12].sum() == 1   # p1's mon ahead of p2's
    assert num[6 + j, base + 6 + i] == 1                                  # ...seen from p2's token
    assert num[i, base + 12 + len(known) - 1] == 1                        # its last move
    # From the other orientation the same fact lands on the other tokens.
    _, flipped, _ = fz.row(rec, "p2")
    assert flipped[j, base + 6 + i] == 1 and flipped[6 + i, base + j] == 1

    rec.pop("evidence")
    with pytest.raises(ValueError, match="evidence"):
        fz.row(rec, "p1")


def test_an_ability_nobody_has_seen_can_block_an_ordering(reg):
    """Unburden on 94% of Sneasler, found only once revealed: with sheets hidden, an unseen one
    that has eaten its seed moves at double Speed with nothing in the log to say so."""
    from types import SimpleNamespace

    from vgc.belief.speed import _could_hide, _temporary

    ev = lambda **kw: SimpleNamespace(**({"species": "Sneasler", "forme": "Sneasler", "ability": None,  # noqa: E731
                                          "item": None, "order_boosts": {}, "order_status": None,
                                          "order_side_conditions": [], "order_weather": None,
                                          "order_terrain": None} | kw))
    assert _could_hide(reg, ev()) is True
    assert _could_hide(reg, ev(ability="poisontouch")) is False
    swim = dict(species="Basculegion", forme="Basculegion")
    assert _could_hide(reg, ev(**swim, order_weather="raindance")) is True    # Swift Swim, unseen
    assert _could_hide(reg, ev(**swim)) is False
    # A held Choice Scarf is persistent speed, so it is not divided out; a boost is.
    assert _temporary(ev(ability="poisontouch", item="choicescarf")) == 1.0
    assert _temporary(ev(ability="poisontouch", order_boosts={"spe": 1})) == 1.5


def test_an_old_model_is_served_with_the_columns_it_was_trained_on():
    from vgc.regulation import load_regulation
    from vgc.wp.models import registered
    from vgc.wp.tools import _load

    old = next((e["version"] for e in registered("reg_mc") if e["kind"] == "gbt"), None)
    if old is None:
        pytest.skip("no registered GBT")
    _, fz = _load(load_regulation("reg_mc"), old)
    assert fz.version == 1 and fz.n_glob == 42


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


def test_temperature_varies_with_the_turn():
    """Over-confident early and about right late is what the models are, so the temperature is
    fitted as a slope in log temperature per turn, and a calibration without one still reads."""
    from vgc.wp.models import SHEETS_COL, TEMPERATURE_TURN_CAP, TURN_COL, SetModel, fit_temperature_by_turn

    rng = np.random.default_rng(0)
    n = 60000
    stage = rng.integers(0, TEMPERATURE_TURN_CAP + 1, n).astype(float)
    true_z = rng.normal(0, 1.5, n)
    y = (rng.random(n) < 1 / (1 + np.exp(-true_z))).astype(float)
    logit = true_z * 1.9 * np.exp(-0.09 * stage)  # sharper than the truth, most at turn 0
    t0, slope = fit_temperature_by_turn(logit, y, stage)
    assert abs(t0 - 1.9) < 0.1 and abs(slope + 0.09) < 0.02

    glob = np.zeros((3, SHEETS_COL + 1), np.float32)
    glob[:, TURN_COL] = [0.0, 0.3, 1.2]  # turns 0, 3, 12 (capped)
    glob[:, 6] = 1
    glob[:, SHEETS_COL] = 1
    model = SetModel.__new__(SetModel)
    model.human_ctx_col, model.sheets_col = 6, SHEETS_COL
    model.calibration = {"human": 2.0, "human_slope": -0.1}
    assert np.allclose(model._temperatures(glob), 2.0 * np.exp(-0.1 * np.array([0, 3, TEMPERATURE_TURN_CAP])))
    model.calibration = {"human": 1.2}
    assert np.allclose(model._temperatures(glob), 1.2)


def test_validation_keeps_a_bo3_series_together():
    """A validation game whose sibling games are in training scores a matchup the model has half
    seen, so validation is drawn per group — the way `heldout_human` is — and per battle only
    where there is no group."""
    from vgc.wp.dataset import val_battles

    manifest = {"battles": [{"battle": f"s{s}-g{g}", "source": "human", "group": f"series-{s}"}
                            for s in range(400) for g in range(3)]
                + [{"battle": f"sp-{i}", "source": "selfplay", "group": None} for i in range(2000)]}
    names = np.array([b["battle"] for b in manifest["battles"]])
    val = val_battles(names, manifest)
    series = val[:1200].reshape(400, 3)
    assert (series.all(axis=1) | ~series.any(axis=1)).all()  # all three games, or none
    assert series[:, 0].any() and val[1200:].any() and not val[1200:].all()


def test_validation_rate_is_per_source():
    """Human validation is what calibrators and early stopping are fitted on, so it is drawn at a
    higher rate than self-play, and each source gets its own rate."""
    from vgc.wp.dataset import VAL_RATE, val_battles

    manifest = {"battles": [{"battle": f"h-{i}", "source": "human", "group": f"g-{i}"} for i in range(4000)]
                + [{"battle": f"sp-{i}", "source": "selfplay", "group": None} for i in range(4000)]}
    val = val_battles(np.array([b["battle"] for b in manifest["battles"]]), manifest)
    assert abs(val[:4000].mean() - VAL_RATE["human"]) < 0.02
    assert abs(val[4000:].mean() - VAL_RATE["selfplay"]) < 0.01
