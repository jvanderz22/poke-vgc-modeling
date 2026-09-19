"""Phase 3: decision-point snapshots, perspective limits, live parity, frozen splits."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vgc.data import splits
from vgc.data.observe import Observer, _hp, dumps
from vgc.data.snapshots import human_snapshots, trace_snapshots
from vgc.engine.runner import BattleRunner, RandomPolicy, battle_seed, play_battle

FIXTURES = Path(__file__).parent / "fixtures"
REPLAYS = sorted((FIXTURES / "replays").glob("*.json"))


def _replay(suffix: str) -> dict:
    return json.loads(next(p for p in REPLAYS if p.stem.endswith(suffix)).read_text())


# --- observer -----------------------------------------------------------------------------

def test_hp_text_with_champions_bar_colour():
    assert _hp("50/100y") == (50, 100, None)
    assert _hp("20/100r par") == (20, 100, "par")
    assert _hp("0 fnt") == (0, 0, "fnt")


def test_human_replay_snapshots(reg):
    snaps = human_snapshots(_replay("2684057197"), reg)
    spectator = [s for s in snaps if s["obs"]["perspective"] == "spectator"]
    kinds = [s["kind"] for s in spectator]
    assert kinds[0] == "preview" and kinds.count("turn") == 8
    label = snaps[0]["label"]
    assert label["winner"] == "p2" and label["ended_by"] == "normal"
    assert all(len(v) == 4 for v in label["brought"].values())
    preview = spectator[0]["obs"]
    for side in preview["sides"].values():
        assert len(side["mons"]) == 6 and side["sheet"]
        assert all(m["state"] == "unrevealed" and m["item_source"] == "sheet" and m["nature"] for m in side["mons"])
    # Farigiraf ate its Sitrus Berry on turn 1; Trick Room went up.
    t2 = spectator[2]["obs"]
    fari = next(m for m in t2["sides"]["p2"]["mons"] if m["species"] == "Farigiraf")
    assert fari["item"] == "" and fari["lost_item"] == "sitrusberry"
    assert "trickroom" in t2["field"]["pseudo"]


def test_forfeit_is_labelled(reg):
    snaps = human_snapshots(_replay("2684058730"), reg)
    assert snaps[0]["label"]["ended_by"] == "forfeit" and snaps[0]["label"]["winner"] in ("p1", "p2")


def test_every_fixture_replay_extracts(reg):
    for path in REPLAYS:
        snaps = human_snapshots(json.loads(path.read_text()), reg)
        assert snaps and snaps[0]["label"]["winner"] in ("p1", "p2"), path.name
        assert [dumps(s) for s in snaps] == [dumps(s) for s in human_snapshots(json.loads(path.read_text()), reg)]


def test_reconstructed_player_view_marks_brought(reg):
    snaps = human_snapshots(_replay("2684057197"), reg)
    p1_preview = next(s for s in snaps if s["obs"]["perspective"] == "p1" and s["kind"] == "preview")
    assert p1_preview["meta"]["approx"]
    states = [m["state"] for m in p1_preview["obs"]["sides"]["p1"]["mons"]]
    assert states.count("not_brought") == 2 and states.count("bench") == 4
    assert all(m["state"] == "unrevealed" for m in p1_preview["obs"]["sides"]["p2"]["mons"])


# --- self-play snapshots (need the simulator) ------------------------------------------------

@pytest.fixture(scope="module")
def runner():
    with BattleRunner() as r:
        yield r


@pytest.fixture(scope="module")
def played(runner, reg):
    t = (FIXTURES / "teams" / "valid_basic.txt").read_text()
    rec = play_battle(runner, "snap", battle_seed(21, 0), reg.showdown_format, (t, t), (RandomPolicy(), RandomPolicy()))
    return rec


def _trace(runner, rec):
    return runner.request({"op": "trace", "id": rec.battle_id, "inputLog": rec.input_log, "ots": rec.ots})


@pytest.mark.showdown
def test_snapshots_are_byte_identical_across_rederivations(runner, reg, played, tmp_path):
    a = [dumps(s) for s in trace_snapshots(_trace(runner, played), played.summary(), reg)]
    b = [dumps(s) for s in trace_snapshots(_trace(runner, played), played.summary(), reg)]
    assert a == b and len(a) >= 9
    splits.write_shard(tmp_path / "a.jsonl.gz", a)
    splits.write_shard(tmp_path / "b.jsonl.gz", b)
    assert (tmp_path / "a.jsonl.gz").read_bytes() == (tmp_path / "b.jsonl.gz").read_bytes()


@pytest.mark.showdown
def test_perspectives_only_see_what_they_may(runner, reg, played):
    snaps = trace_snapshots(_trace(runner, played), played.summary(), reg)
    turn1 = {s["obs"]["perspective"]: s["obs"] for s in snaps if s["kind"] == "turn" and s["obs"]["turn"] == 1}
    spec, p1 = turn1["spectator"], turn1["p1"]
    # Spectator: never exact HP or stats, never "not_brought" while the game runs.
    for side in spec["sides"].values():
        assert all(m["hp_exact"] is None and m["stats"] is None for m in side["mons"])
        assert not any(m["state"] == "not_brought" for m in side["mons"])
    # Player: own side exact and fully known, 2 not brought; opponent like the spectator.
    own = p1["sides"]["p1"]["mons"]
    assert sum(m["state"] == "not_brought" for m in own) == 2 and p1["sides"]["p1"]["brought_known"]
    assert all(m["hp_exact"] and m["stats"] for m in own)
    assert p1["sides"]["p2"] == spec["sides"]["p2"]
    # Labels carry the truth; observations don't.
    assert snaps[0]["label"]["brought_complete"] == {"p1": True, "p2": True}
    assert snaps[0]["kind"] == "preview" and snaps[0]["label"]["choice"]["p1"].startswith("team ")


@pytest.mark.showdown
@pytest.mark.calc
def test_player_snapshots_match_live_poke_env_view(reg):
    from vgc.data.parity import run_parity

    r = run_parity(reg, 8, seed=1)
    assert r["decisions_checked"] > 50
    assert r["missing"] == 0 and r["mismatches"] == {}, r["examples"]


# --- splits and manifests --------------------------------------------------------------------

def _rec(battle: str, source: str, teams: tuple, group: str | None = None) -> dict:
    return {"battle": battle, "source": source, "meta": {"teams": {"p1": teams[0], "p2": teams[1]}, "group": group}}


@pytest.fixture
def frozen(reg, tmp_path, monkeypatch):
    monkeypatch.setattr(splits, "SPLITS", tmp_path / "splits")
    monkeypatch.setattr(splits.paths, "ROOT", tmp_path)
    teams = [f"t{i:011d}" for i in range(200)]
    splits.freeze(reg, teams, [f"g{i}" for i in range(100)], "pool.json")
    return splits.load_rules(reg), teams


def test_split_assignment_is_deterministic_and_team_first(reg, frozen):
    rules, teams = frozen
    held = [t for t in teams if rules.team_heldout(t)]
    kept = [t for t in teams if not rules.team_heldout(t)]
    assert 10 < len(held) < 50
    assert rules.split_of("selfplay", "run-1", (held[0], kept[0])) == "heldout_team"
    assert rules.split_of("human", "r1", (kept[0], None), "g1") in ("train", "heldout_human")
    battles = [rules.split_of("selfplay", f"run-{i}", (kept[0], kept[1])) for i in range(2000)]
    assert 100 < battles.count("heldout_battle") < 300 and set(battles) == {"train", "heldout_battle"}
    assert splits.verify_frozen(reg) == []
    with pytest.raises(FileExistsError):
        splits.freeze(reg, teams, [], "pool.json")


def test_manifest_check_catches_held_out_data(reg, frozen, tmp_path):
    rules, teams = frozen
    kept = [t for t in teams if not rules.team_heldout(t)]
    held = next(t for t in teams if rules.team_heldout(t))
    train = [_rec(f"run-{i}", "selfplay", (kept[0], kept[1])) for i in range(50)]
    train = [r for r in train if rules.split_of_record(r) == "train"]
    clean = tmp_path / "clean.jsonl.gz"
    splits.write_shard(clean, [dumps(r) for r in train])
    m = splits.build_manifest([clean], reg)
    assert splits.check_manifest(m, reg) == []

    leaky = tmp_path / "leaky.jsonl.gz"
    splits.write_shard(leaky, [dumps(r) for r in train + [_rec("run-x", "selfplay", (held, kept[0]))]])
    problems = splits.check_manifest(splits.build_manifest([leaky], reg), reg)
    assert len(problems) == 1 and "heldout_team" in problems[0]

    # Editing a file after the manifest was built is caught too.
    splits.write_shard(clean, [dumps(r) for r in train[:-1]])
    assert any("changed" in p for p in splits.check_manifest(m, reg))


def test_observer_rejects_unknown_perspective(reg):
    with pytest.raises(ValueError):
        Observer("p3", reg.dex)
