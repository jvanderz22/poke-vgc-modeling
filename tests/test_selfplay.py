"""Phase 2: seeded runner, policies, and reproducibility."""

from __future__ import annotations

import pytest

from vgc.engine.runner import BattleRunner, RandomPolicy, battle_seed, play_battle

pytestmark = [pytest.mark.showdown, pytest.mark.calc]


@pytest.fixture(scope="module")
def runner():
    with BattleRunner() as r:
        yield r


@pytest.fixture(scope="module")
def heuristic(reg):
    from vgc.policy.heuristic import HeuristicPolicy

    return HeuristicPolicy(reg)


def _play(runner, reg, team, policies, i, run_seed=11):
    return play_battle(runner, f"t{i}", battle_seed(run_seed, i), reg.showdown_format, (team, team), policies)


def test_same_seed_same_battle(runner, reg, team_text):
    t = team_text("valid_basic")
    a = _play(runner, reg, t, (RandomPolicy(), RandomPolicy()), 3)
    b = _play(runner, reg, t, (RandomPolicy(), RandomPolicy()), 3)
    assert a.input_log == b.input_log and a.winner == b.winner


def test_different_seeds_differ(runner, reg, team_text):
    t = team_text("valid_basic")
    logs = {tuple(_play(runner, reg, t, (RandomPolicy(), RandomPolicy()), i).input_log) for i in range(5)}
    assert len(logs) == 5


def test_input_log_replays_exactly(runner, reg, team_text, heuristic):
    rec = _play(runner, reg, team_text("valid_basic"), (heuristic, RandomPolicy()), 5)
    again = runner.request({"op": "replay", "id": "r", "inputLog": rec.input_log, "ots": rec.ots})["end"]
    untimed = lambda log: [line for line in log if not line.startswith("|t:|")]  # noqa: E731
    assert (again["winner"], again["turns"], untimed(again["log"])) == (rec.winner, rec.turns, untimed(rec.log))


def test_illegal_team_is_refused(runner, reg, team_text):
    from vgc.engine.runner import RunnerError

    with pytest.raises(RunnerError, match="invalid team"):
        _play(runner, reg, team_text("bad_67_sp"), (RandomPolicy(), RandomPolicy()), 0)


def test_levels_adjusted_to_50(runner, reg, team_text):
    t = team_text("valid_basic").replace("Level: 50\n", "")
    rec = _play(runner, reg, t, (RandomPolicy(), RandomPolicy()), 1)
    switches = [l for l in rec.log if l.startswith("|switch|")]
    assert switches and all(", L50" in l for l in switches)


def test_heuristic_beats_random(runner, reg, team_text, heuristic):
    t = team_text("valid_basic")
    recs = [_play(runner, reg, t, (heuristic, RandomPolicy()), i) for i in range(12)]
    assert sum(r.winner == "p1" for r in recs) >= 10
    assert sum(r.invalid_choices for r in recs) == 0


def test_heuristic_mega_evolves(runner, reg, team_text, heuristic):
    rec = _play(runner, reg, team_text("valid_basic"), (heuristic, RandomPolicy()), 2)
    brought = next(l for l in rec.input_log if l.startswith(">p1 team"))
    if "4" in brought.split("team ")[1]:  # Salamence (slot 4) holds the Mega Stone
        assert any("|-mega|p1" in l for l in rec.log)


def test_milk_drink_gets_a_target(runner, reg):
    """Champions makes Milk Drink `adjacentAllyOrSelf` (poke-env's Gen 9 data says `self`);
    the pool team that hit this in the Phase 2 gate must now play with no invalid choices."""
    from vgc.meta.pool import load_pool

    team = next(t.text for t in load_pool(reg) if "Milk Drink" in t.text)
    for i in range(6):
        rec = play_battle(runner, f"md{i}", battle_seed(5, i), reg.showdown_format, (team, team), (RandomPolicy(), RandomPolicy()))
        assert rec.invalid_choices == 0


def test_state_infers_own_spread(reg):
    from vgc.policy.state import infer_spread

    # Rillaboom, Adamant, 32 HP / 32 Atk / 2 Spe → 207/194/110/72/90/107
    sp = infer_spread(reg.dex.species["rillaboom"], {"hp": 207, "atk": 194, "def": 110, "spa": 72, "spd": 90, "spe": 107}, reg.dex, reg)
    assert sp is not None
    nature, points = sp
    assert points["hp"] == 32 and points["atk"] == 32 and points["spe"] == 2 and nature == "Adamant"


def test_pool_selfplay_is_clean_and_reproducible(reg, tmp_path):
    """Real pool teams exercise moves the fixture team never does (pseudo-moves, odd targets)."""
    from vgc.meta.pool import load_pool
    from vgc.sim.selfplay import gauntlet_matchups, run

    teams = [(f"pool{i}", t.text) for i, t in enumerate(load_pool(reg))]
    ms = gauntlet_matchups(teams, 24, "heuristic", "random", seed=3)
    a = run(ms, seed=3, workers=2, out_dir=tmp_path / "a", keep_logs=False)
    b = run(ms, seed=3, workers=1, out_dir=tmp_path / "b", keep_logs=False)
    assert a["errors"] == 0 and a["invalid_choices"] == 0, a["error_examples"]
    assert a["outcome_digest"] == b["outcome_digest"]
