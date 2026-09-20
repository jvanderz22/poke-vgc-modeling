"""The endgame browser: the set's selection rules, and the game view it serves.

What is worth protecting here is not the JSON shape. It is that the set cannot quietly stop being
what the page claims it is — held-out, human, open-sheet, sustained to the end — and that stepping
through a game shows the model's numbers at the real decision points rather than an interpolation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vgc.data.snapshots import human_decision_points, human_snapshots
from vgc.web import endgames

FIXTURES = Path(__file__).parent / "fixtures"
REPLAYS = sorted((FIXTURES / "replays").glob("*.json"))


def _replay(suffix: str) -> dict:
    return json.loads(next(p for p in REPLAYS if p.stem.endswith(suffix)).read_text())


# --- the shared walk ----------------------------------------------------------------------

def test_decision_points_line_up_with_the_snapshots(reg):
    """The browser reads the points and the trainer reads the snapshots. If the two walks ever
    disagree, a WP would be drawn against the wrong position — so they must stay one walk."""
    for path in REPLAYS:
        replay = json.loads(path.read_text())
        _observer, points, _forfeit = human_decision_points(replay, reg)
        records = [r for r in human_snapshots(replay, reg)
                   if r["obs"]["perspective"] == "spectator" and not r["meta"]["approx"]]
        decided = [p for p in points if p["kind"] != "end"]
        assert len(decided) == len(records), path.name
        assert [p["kind"] for p in decided] == [r["kind"] for r in records]
        assert [p["obs"]["turn"] for p in decided] == [r["obs"]["turn"] for r in records]


def test_every_log_line_lands_in_exactly_one_step(reg):
    """The steps partition the log: nothing shown twice, nothing silently dropped."""
    for path in REPLAYS:
        replay = json.loads(path.read_text())
        _observer, points, _forfeit = human_decision_points(replay, reg)
        rebuilt = [line for p in points for line in p["lines"]]
        assert rebuilt == replay["log"].split("\n"), path.name


def test_the_last_step_is_the_finish(reg):
    """The KO and the win line come after the final decision point. They belong in the game view
    — you clicked through to see how it ended — but they are not a position to put a WP on."""
    _observer, points, _forfeit = human_decision_points(_replay("2684057197"), reg)
    assert points[-1]["kind"] == "end"
    assert any(e for e in points[-1]["lines"] if e.startswith("|win|"))


# --- who counts as a person ---------------------------------------------------------------

def test_bot_accounts_are_excluded_but_joke_names_are_not(reg):
    """The set claims both teams were built by people. The shape of a bot farm's alt is what
    identifies it — the bare substring "bot" is a name a person may well have chosen."""
    assert endgames.is_bot("pcrlbot12d159c39a")
    assert endgames.is_bot("lowladderbot#5")
    for human in ("robotarmadillo", "bottomplayer", "100bo3norobot", "BATTLEBOTS_BOI", "Yinzo"):
        assert not endgames.is_bot(human), human


def test_nicknames_are_narrated_as_the_species_on_the_board(reg):
    """Human replays are full of nicknames. "Mr. VGC used Fake Out" next to a board that lists
    Incineroar is two names for one Pokémon, and the reader has to do the join."""
    from vgc.web.narrate import narrate_line, species_by_nickname

    log = ["|switch|p1a: Mr. VGC|Incineroar, L50, M|100/100",
           "|move|p1a: Mr. VGC|Fake Out|p2a: Flutter Mane"]
    names = species_by_nickname(log)
    assert names == {"Mr. VGC": "Incineroar"}
    assert narrate_line(log[1], names)["text"] == "Incineroar used Fake Out!"
    # No map, no rewriting: self-play logs carry no nicknames and must read exactly as before.
    assert narrate_line(log[1])["text"] == "Mr. VGC used Fake Out!"


# --- the set itself -----------------------------------------------------------------------

@pytest.fixture(scope="module")
def index(reg):
    built = endgames.load_index(reg)
    if built is None:
        pytest.skip("no endgame index built (run `vgc wp endgames`)")
    return built


def test_every_selected_game_meets_every_criterion(index, reg):
    from vgc.data.splits import load_rules
    from vgc.data.snapshots import replay_group

    rules, c = load_rules(reg), index["criteria"]
    assert c["min_wp"] >= 0.9 and c["held_out"] and c["ots"] and c["human_only"]
    for g in index["games"]:
        assert g["wp"] >= c["min_wp"], g["replay"]
        assert g["turns"] >= c["min_turns"], g["replay"]
        assert g["winner"] in ("p1", "p2") and g["correct"] == (g["side"] == g["winner"])
        assert not any(endgames.is_bot(p) for p in g["players"].values()), g["replay"]
        replay = endgames.find(reg, g["replay"])
        assert rules.split_of("human", replay["id"], [], replay_group(replay)) == "heldout_human"


def test_the_index_reports_what_the_set_was_drawn_from(index):
    """A hit rate without a denominator is not a claim. The counts have to survive."""
    counts = index["counts"]
    assert counts["selected"] == len(index["games"]) <= counts["eligible"] <= counts["cached"]
    assert index["correct"] <= len(index["games"])


def test_misses_are_listed_first(index):
    """The games the model got wrong are the ones worth reading, so they must not be buried on
    page three of a list sorted by confidence."""
    correctness = [g["correct"] for g in index["games"]]
    assert correctness == sorted(correctness)


# --- one game, step by step ---------------------------------------------------------------

def test_detail_scores_the_real_decision_points(index, reg):
    game = index["games"][0]
    d = endgames.detail(reg, game["replay"], index["version"])

    scored = [s for s in d["steps"] if s["kind"] != "end"]
    assert len(scored) == game["points"]
    assert all(s["wp_p1"] is not None for s in scored), d["wp_error"]
    assert d["steps"][-1]["kind"] == "end" and d["steps"][-1]["wp_p1"] is None
    assert d["winner"] == game["winner"] and d["players"] == game["players"]

    # The index's headline number is the model's confidence at the last decision point; the game
    # view has to agree with it, or the list is advertising something the game does not show.
    final = scored[-1]["wp_p1"]
    assert round(final if game["side"] == "p1" else 1 - final, 4) == pytest.approx(game["wp"], abs=5e-4)


def test_detail_shows_both_open_sheets_and_a_shrinking_board(index, reg):
    game = next(g for g in index["games"] if g["ended_by"] == "normal")
    d = endgames.detail(reg, game["replay"], index["version"])
    for sid in ("p1", "p2"):
        assert len(d["sheets"][sid]) == 6
        assert all(m["moves"] for m in d["sheets"][sid])  # open team sheets: the moves are known
    left = [s["board"][game["winner"]]["left"] for s in d["steps"]]
    assert left[0] == 4 and min(left) >= 1  # the winner never runs out
    loser = "p2" if game["winner"] == "p1" else "p1"
    assert d["steps"][-1]["board"][loser]["left"] == 0


def test_an_uncached_replay_is_a_clear_miss_not_a_crash(reg):
    with pytest.raises(FileNotFoundError, match="replay cache"):
        endgames.detail(reg, "gen9championsvgc2026regmcbo3-1", "wp-v1-gbt")
