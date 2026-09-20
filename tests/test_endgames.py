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

    assert len(d["steps"]) == game["points"]  # one step per decision point, and no others
    assert all(s["wp_p1"] is not None for s in d["steps"]), d["wp_error"]
    assert d["winner"] == game["winner"] and d["players"] == game["players"]

    # The index's headline number is the model's confidence at the last decision point; the game
    # view has to agree with it, or the list is advertising something the game does not show.
    final = d["steps"][-1]["wp_p1"]
    assert round(final if game["side"] == "p1" else 1 - final, 4) == pytest.approx(game["wp"], abs=5e-4)


def test_a_step_runs_from_the_position_to_what_it_produced(index, reg):
    """Each step is anchored on the decision, so `before` is what the WP describes and `after` is
    the consequence. Chaining them has to reproduce the game: one step's `after` is the next
    step's `before`."""
    d = endgames.detail(reg, index["games"][0]["replay"], index["version"])
    for a, b in zip(d["steps"], d["steps"][1:]):
        assert a["after"] == b["before"]
        assert a["wp_after"] == pytest.approx(b["wp_p1"])
    assert d["steps"][-1]["final"] is True


def test_the_last_turn_resolves_to_the_result(index, reg):
    """The model is never asked about a finished game, so the track has to end on the outcome —
    otherwise a turn that swung from 8% to a win reads as if it never resolved. It is flagged as
    an outcome so the page can say it is one rather than pass it off as a prediction."""
    game = next(g for g in index["games"] if not g["correct"])  # the miss: 92% and then lost
    d = endgames.detail(reg, game["replay"], index["version"])
    last = d["steps"][-1]
    assert last["outcome"] is True
    assert last["wp_after"] == (1.0 if d["winner"] == "p1" else 0.0)
    # The favoured side led going in and lost: that whole reversal is the point of this game.
    assert max(last["wp_p1"], 1 - last["wp_p1"]) >= index["criteria"]["min_wp"]
    assert (last["wp_p1"] > 0.5) != (last["wp_after"] > 0.5)
    assert all(s["outcome"] is False for s in d["steps"][:-1])


def test_a_switch_puts_what_left_and_what_arrived_on_one_entry(index, reg):
    """A slot whose occupant changed is one event to a reader, and `started` carries the leaver's
    state at the end of the step, so a Pokémon that was knocked out reads as knocked out."""
    d = endgames.detail(reg, index["games"][0]["replay"], index["version"])
    changes = [(s, sid, r) for s in d["steps"] for sid in ("p1", "p2")
               for r in s["slots"][sid] if r["changed"]]
    assert changes, "no switch anywhere in the game"
    for step, sid, row in changes:
        assert row["started"]["species"] != row["ended"]["species"]
        # Whoever arrived is standing there at the end of the step, by definition.
        active = {m["species"] for m in step["after"][sid]["mons"] if m["state"] == "active"}
        assert row["ended"]["species"] in active
        assert row["started"]["species"] not in active

    # A faint with no replacement yet: the slot empties, and filling it is the *next* decision.
    emptied = [r for s in d["steps"] for sid in ("p1", "p2") for r in s["slots"][sid]
               if r["ended"] is None]
    assert all(r["started"]["state"] == "fainted" for r in emptied)


def test_the_board_says_only_what_a_spectator_knows(index, reg):
    """Five states, and the one that matters is the boundary between the last two: a Pokémon
    nobody has seen is *unknown* — it may still come in — and only becomes *unselected* once the
    fourth of its side's four has appeared. Collapsing the two would either invent information
    early or throw it away late."""
    d = endgames.detail(reg, index["games"][0]["replay"], index["version"])
    seen = {"active", "bench", "fainted"}

    for sid in ("p1", "p2"):
        # Team preview: the sheets are open, but nothing has been selected in public yet.
        preview = d["steps"][0]["before"][sid]
        assert len(preview["mons"]) == 6
        assert {m["state"] for m in preview["mons"]} == {"unknown"}
        assert preview["brought_known"] is False

        for step in d["steps"]:
            board = step["before"][sid]
            revealed = [m for m in board["mons"] if m["state"] in seen]
            assert len(revealed) <= 4, "a side cannot reveal more than the four it brought"
            # `unselected` is a claim that the Pokémon is not in this game, so it may only be
            # made once the four that are have all shown themselves — never before.
            unselected = [m for m in board["mons"] if m["state"] == "unselected"]
            assert board["brought_known"] == (len(revealed) == 4)
            assert bool(unselected) == board["brought_known"]
            assert len(revealed) + len(unselected) + \
                sum(m["state"] == "unknown" for m in board["mons"]) == 6

    # By the end both sides have committed, so every sheet resolves.
    for sid in ("p1", "p2"):
        assert d["steps"][-1]["after"][sid]["brought_known"] is True


def test_detail_shows_both_open_sheets_and_a_shrinking_board(index, reg):
    game = next(g for g in index["games"] if g["ended_by"] == "normal")
    d = endgames.detail(reg, game["replay"], index["version"])
    for sid in ("p1", "p2"):
        assert len(d["sheets"][sid]) == 6
        assert all(m["moves"] for m in d["sheets"][sid])  # open team sheets: the moves are known
    left = [s["before"][game["winner"]]["left"] for s in d["steps"]]
    assert left[0] == 4 and min(left) >= 1  # the winner never runs out
    loser = "p2" if game["winner"] == "p1" else "p1"
    assert d["steps"][-1]["after"][loser]["left"] == 0


def test_an_uncached_replay_is_a_clear_miss_not_a_crash(reg):
    with pytest.raises(FileNotFoundError, match="replay cache"):
        endgames.detail(reg, "gen9championsvgc2026regmcbo3-1", "wp-v1-gbt")
