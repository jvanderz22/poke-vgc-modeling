"""The battle-companion API (PLAN.md L5b).

The behaviour worth protecting is not the JSON shape — it is that the app cannot quietly present
a model that failed its gates as if it were calibrated.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from vgc import paths  # noqa: E402
from vgc.web.app import app  # noqa: E402

POOL = sorted((paths.DATA / "teams" / "reg_mc").glob("ots_pool_*.json"))


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def teams():
    if not POOL:
        pytest.skip("no team pool built")
    t = json.loads(POOL[-1].read_text())["teams"]
    return t[0]["text"], t[5]["text"]


def test_health_reports_the_regulation(client):
    h = client.get("/api/health").json()
    assert h["regulation"] == "reg_mc" and h["bring"] == 4 and h["level"] == 50
    assert h["tera"] is False  # Champions M-C: the app must not offer a Tera control
    assert h["open_sheets_only"] is True


def test_validate_accepts_a_pool_team_and_rejects_junk(client, teams):
    ok = client.post("/api/validate", json={"text": teams[0]}).json()
    assert ok["legal"] and len(ok["mons"]) == 6
    assert all(m["stats"] for m in ok["mons"])
    bad = client.post("/api/validate", json={"text": "Notamon @ Nothing\nAbility: Nope\n- Nothing"}).json()
    assert not bad["legal"] and bad["problems"]


def test_models_expose_gate_verdicts(client):
    m = client.get("/api/models").json()
    evaluated = [x for x in m["models"] if x.get("evaluated")]
    if not evaluated:
        pytest.skip("no evaluated model")
    for x in evaluated:
        # Either it passes everything, or it names what it failed. Never silence.
        assert x["all_pass"] is True or x["failed"]


@pytest.mark.showdown
def test_preview_ranks_every_option_and_carries_its_gates(client, teams):
    r = client.post("/api/preview", json={"my_team": teams[0], "their_team": teams[1], "limit": 5})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["n_options"] == 90  # 15 ways to bring 4 of 6 x 6 lead pairs
    assert len(d["best_by_bring"]) == 5
    wps = [o["wp"] for o in d["best_by_bring"]]
    assert wps == sorted(wps, reverse=True) and all(0 <= w <= 1 for w in wps)
    assert set(d["gates"]) >= {"preview_gate", "bring_gate", "failed"}
    assert len(d["their_likely_bring"]) == 6


@pytest.mark.showdown
def test_illegal_team_is_a_client_error_not_a_crash(client, teams):
    r = client.post("/api/preview", json={"my_team": "Pikachu @ Light Ball\nAbility: Static\n- Thunderbolt",
                                          "their_team": teams[1]})
    assert r.status_code == 422


def test_pool_is_ordered_by_usage(client):
    p = client.get("/api/pool").json()
    assert len(p["species"]) > 250 and p["items"] and p["moves"] and len(p["natures"]) == 25
    seen = [s["seen"] for s in p["species"]]
    assert seen == sorted(seen, reverse=True)  # an empty search box shows what people play
    assert all(not s.get("battleOnly") for s in p["species"])


def test_compose_builds_a_legal_team_from_species_alone(client, teams):
    del teams  # only needed for the pool fixture's skip
    names = ["Incineroar", "Rillaboom", "Gholdengo", "Sylveon", "Staraptor", "Raichu"]
    r = client.post("/api/compose", json={"species": names}).json()
    assert [s["species"] for s in r["sets"]] == names
    # The share is the honest part: a guessed set is one of many, and the UI must be able to say so.
    assert all(s["share"] is None or 0 < s["share"] <= 1 for s in r["sets"])
    ok = client.post("/api/validate", json={"text": r["text"]}).json()
    assert ok["legal"], [p["message"] for p in ok["problems"] if p["severity"] == "error"]


def test_compose_rejects_an_illegal_species(client):
    assert client.post("/api/compose", json={"species": ["Flutter Mane"]}).status_code == 422


@pytest.mark.showdown
def test_closed_sheet_preview_needs_a_full_team(client, teams):
    body = {"my_team": teams[0], "their_species": ["Incineroar"]}
    assert client.post("/api/preview", json=body).status_code == 422


@pytest.mark.showdown
def test_closed_sheet_preview_reports_what_it_guessed(client, teams):
    names = ["Incineroar", "Rillaboom", "Gholdengo", "Sylveon", "Staraptor", "Raichu"]
    r = client.post("/api/preview", json={"my_team": teams[0], "their_species": names, "limit": 3})
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["best_by_bring"]) == 3
    assert d["inferred_sets"] and len(d["inferred_sets"]) == 6
    # An open sheet is not a guess, so it must not claim to be one.
    open_sheet = client.post("/api/preview", json={"my_team": teams[0], "their_team": teams[1], "limit": 1}).json()
    assert open_sheet["inferred_sets"] is None


@pytest.mark.showdown
def test_simulate_plays_a_battle_and_narrates_it(client, teams):
    r = client.post("/api/simulate", json={"team_a": teams[0], "team_b": teams[1], "seed": 3})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["winner"] in ("p1", "p2", None) and d["turns"] > 0
    assert d["invalid_choices"] == 0  # an invalid choice means the policy/driver is broken
    assert d["timeline"] and all(t["events"] for t in d["timeline"])
    assert any(e["kind"] == "move" for t in d["timeline"] for e in t["events"])
    # WP is a spectator read on the same battle; it must not silently vanish.
    assert d["wp_error"] is None
    assert any(t["wp_p1"] is not None for t in d["timeline"])


@pytest.mark.showdown
def test_simulate_is_seeded(client, teams):
    body = {"team_a": teams[0], "team_b": teams[1], "seed": 11}
    a = client.post("/api/simulate", json=body).json()
    b = client.post("/api/simulate", json=body).json()
    # A battle is a pure function of (seed, teams, policies) — that is what makes a seed worth citing.
    assert a["winner"] == b["winner"] and a["turns"] == b["turns"]
    assert [t["events"] for t in a["timeline"]] == [t["events"] for t in b["timeline"]]
    other = client.post("/api/simulate", json={**body, "seed": 12}).json()
    assert other["battle_id"] != a["battle_id"]


@pytest.mark.showdown
def test_simulate_rejects_an_illegal_team(client, teams):
    r = client.post("/api/simulate", json={"team_a": "Pikachu @ Light Ball\nAbility: Static\n- Thunderbolt",
                                           "team_b": teams[1]})
    assert r.status_code == 422


def test_endgames_index_is_served_with_what_it_was_drawn_from(client):
    """The page leads with a hit rate, so the response has to carry the denominator and the
    criteria alongside it — otherwise the UI is free to show "239/240" with nothing behind it."""
    r = client.get("/api/endgames").json()
    if not r["built"]:
        assert "vgc wp endgames" in r["hint"]  # never a bare 404: the app is fine, the index is absent
        pytest.skip("no endgame index built")
    assert r["criteria"]["min_wp"] >= 0.9 and r["criteria"]["human_only"]
    assert len(r["games"]) <= r["matched"] <= r["total"] <= r["counts"]["eligible"]
    assert 0 <= r["correct"] <= r["total"]
    assert r["gates"]["version"] == r["version"]  # the model's gate verdicts travel with its claims


def test_endgame_filters_narrow_the_same_set(client):
    full = client.get("/api/endgames").json()
    if not full["built"]:
        pytest.skip("no endgame index built")
    misses = client.get("/api/endgames?only=misses").json()
    assert all(g["correct"] is False for g in misses["games"])
    assert misses["matched"] <= full["matched"]
    assert misses["total"] == full["total"]  # the denominator does not move when the view does
    played = client.get("/api/endgames?only=played_out").json()
    assert all(g["ended_by"] == "normal" for g in played["games"])
    assert client.get("/api/endgames?only=nonsense").status_code == 422


def test_endgame_detail_walks_one_game(client):
    index = client.get("/api/endgames").json()
    if not index["built"]:
        pytest.skip("no endgame index built")
    game = index["games"][0]
    d = client.get(f"/api/endgames/{game['replay']}").json()
    assert [s["kind"] for s in d["steps"]][0] == "preview"
    assert d["steps"][-1]["final"] is True  # the finish is the last turn, not a step of its own
    assert d["players"] == game["players"] and d["winner"] == game["winner"]
    assert d["gates"]["version"] == d["version"]


def test_a_replay_that_is_not_cached_is_a_404(client):
    assert client.get("/api/endgames/gen9championsvgc2026regmcbo3-1").status_code == 404


def test_client_routes_are_served_the_app_so_a_deep_link_survives_a_reload(client):
    """The frontend routes on real paths, which exist only in the browser. Reloading one, or
    pasting it to someone else, has to reach the app rather than a 404 — otherwise every URL in
    the address bar is decorative."""
    for path in ("/endgames", "/endgames/gen9championsvgc2026regmcbo3-2682643305/9",
                 "/teams/abc123", "/simulate", "/preview"):
        r = client.get(path)
        # 503 is the honest answer when the frontend has not been built; either way it is the
        # page's response and not a missing route.
        assert r.status_code in (200, 503), path
        assert "text/html" in r.headers["content-type"], path


def test_an_unknown_api_path_is_still_a_404(client):
    """The fallback must not swallow API typos: handing a caller 200 and a page of HTML for a
    misspelled endpoint is far worse to debug than a plain 404."""
    r = client.get("/api/endgame")  # singular: not an endpoint
    assert r.status_code == 404
    assert "text/html" not in r.headers["content-type"]


def test_in_battle_model_is_chosen_by_its_in_battle_gate(monkeypatch):
    """Drawing a WP number on a battle in progress is a claim about in-battle calibration, so the
    choice follows that gate — not "newest set encoder". Today the GBT baseline is the model that
    passes it and the set encoder is not, and the app has to be able to say so."""
    from vgc.wp import models as wp_models

    rows = [
        {"version": "old-gbt", "kind": "gbt", "created": "2026-01-01", "gates": {"in_battle_pass": True}},
        {"version": "new-gbt", "kind": "gbt", "created": "2026-02-01", "gates": {"in_battle_pass": True}},
        {"version": "shiny-set", "kind": "set", "created": "2026-03-01", "gates": {"in_battle_pass": False}},
    ]
    monkeypatch.setattr(wp_models, "registered", lambda reg: rows)
    assert wp_models.in_battle_version("reg_mc") == "new-gbt"  # newest that passes, not newest overall
    assert wp_models.default_version("reg_mc") == "shiny-set"  # bring ranking still wants a set model

    # Nothing passes: fall back to the default rather than refusing to show a battle at all.
    monkeypatch.setattr(wp_models, "registered",
                        lambda reg: [dict(r, gates={"in_battle_pass": False}) for r in rows])
    assert wp_models.in_battle_version("reg_mc") == "shiny-set"


# --- a battle in progress ---------------------------------------------------------------------
#
# The journal is the battle and the state is replayed from it, so the API is thin by construction:
# every write appends to a list and every read replays it. What is worth protecting is that the
# thinness holds — that a reload, an undo and a walk back to turn 3 all agree — and that the WP
# number never arrives without the caveat that makes it honest.

THEIR_SIX = ["Kingambit", "Milotic", "Archaludon", "Torkoal", "Gholdengo", "Pelipper"]


@pytest.fixture
def battles_dir(tmp_path, monkeypatch):
    from vgc.web import live

    monkeypatch.setattr(live, "BATTLES", tmp_path / "battles")
    return tmp_path


@pytest.fixture
def started(client, teams, battles_dir):
    r = client.post("/api/battles", json={"name": "test", "my_team": teams[0],
                                          "their_species": THEIR_SIX})
    assert r.status_code == 200, r.text
    return r.json()


def entries(client, battle_id, *items):
    r = client.post(f"/api/battles/{battle_id}/entries", json={"entries": list(items)})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_battle_starts_at_preview_with_their_six_and_nothing_else(started):
    assert started["turn"] == 0 and not started["started"]
    theirs = started["sides"]["p2"]["mons"]
    assert [m["species"] for m in theirs] == THEIR_SIX
    assert all(m["ability"] is None and m["item"] is None for m in theirs)
    # Yours is fully known, because you built it.
    assert all(m["ability"] and m["stats"] for m in started["sides"]["p1"]["mons"])


def test_a_species_that_is_not_legal_is_refused_rather_than_tracked(client, teams, battles_dir):
    r = client.post("/api/battles", json={"my_team": teams[0],
                                          "their_species": ["Mewtwo"] + THEIR_SIX[1:]})
    assert r.status_code == 422 and "Mewtwo" in r.json()["detail"]


def test_the_wrong_number_of_species_is_refused(client, teams, battles_dir):
    r = client.post("/api/battles", json={"my_team": teams[0], "their_species": THEIR_SIX[:4]})
    assert r.status_code == 422


def test_wp_is_an_average_over_drawn_opponents_and_says_how_wide(started):
    """The models are open-sheet models and a Team Preview Only position is not a row any of them
    has seen. So the number is an expectation over `k` complete opponents drawn from the belief —
    each of *those* is a row they were trained on — and the 10th-to-90th spread is what their
    hidden sets are worth here. `wp_open` is the true position, kept as a diagnostic."""
    wp = started["wp"]
    assert 0 <= wp["lo"] <= wp["wp"] <= wp["hi"] <= 1
    assert 0 <= wp["wp_open"] <= 1
    assert wp["k"] >= 8
    assert {b["species"] for b in wp["belief"]} <= set(THEIR_SIX)
    assert all(0 <= b["concentration"] <= 1 for b in wp["belief"])
    assert "open-sheet" in wp["regime"]


def test_the_same_position_gives_the_same_number_twice(client, started):
    """A Monte-Carlo estimate that jitters when nothing happened is a number nobody can read, so
    the draw is seeded on how much has been logged."""
    bid = started["id"]
    a = client.get(f"/api/battles/{bid}").json()["wp"]["wp"]
    b = client.get(f"/api/battles/{bid}").json()["wp"]["wp"]
    assert a == b


def test_logging_a_lead_raises_the_question_the_rules_cannot_answer(client, started):
    v = entries(client, started["id"],
                {"kind": "lead", "side": "p2", "slot": 0, "species": "Torkoal"})
    q = next(x for x in v["questions"] if x["species"] == "Torkoal")
    assert q["kind"] == "switch_in"
    assert any(o["ability"] == "drought" for o in q["options"])
    assert any(o["label"].startswith("nothing announced") for o in q["options"])


def test_answering_applies_the_effect_and_pins_the_ability(client, started):
    bid = started["id"]
    v = entries(client, bid, {"kind": "lead", "side": "p2", "slot": 0, "species": "Torkoal"})
    q = next(x for x in v["questions"] if x["species"] == "Torkoal")
    pick = next(i for i, o in enumerate(q["options"]) if o["ability"] == "drought")
    v = entries(client, bid, {"kind": "answer", "question": q["id"], "option": pick})
    assert v["field"]["weather"] == "sunnyday"
    torkoal = next(m for m in v["sides"]["p2"]["mons"] if m["species"] == "Torkoal")
    assert torkoal["ability"] == "drought" and torkoal["ability_source"] == "revealed"


def test_undo_walks_the_journal_back(client, started):
    bid = started["id"]
    v = entries(client, bid, {"kind": "lead", "side": "p2", "slot": 0, "species": "Torkoal"})
    before = v["entries"]
    v = client.post(f"/api/battles/{bid}/undo").json()
    assert v["entries"] == before - 1
    assert not any(m["state"] == "active" for m in v["sides"]["p2"]["mons"])


def test_a_battle_survives_a_reload(client, started):
    bid = started["id"]
    entries(client, bid, {"kind": "lead", "side": "p2", "slot": 0, "species": "Torkoal"},
            {"kind": "turn", "n": 1})
    again = client.get(f"/api/battles/{bid}").json()
    assert again["turn"] == 1 and again["entries"] == 2
    assert bid in [b["id"] for b in client.get("/api/battles").json()["battles"]]


def test_walking_back_to_a_turn_shows_that_turn(client, started):
    bid = started["id"]
    entries(client, bid,
            {"kind": "lead", "side": "p1", "slot": 0, "species": "Incineroar"},
            {"kind": "lead", "side": "p2", "slot": 0, "species": "Gholdengo"},
            {"kind": "turn", "n": 1}, {"kind": "turn", "n": 2}, {"kind": "turn", "n": 3})
    v = client.get(f"/api/battles/{bid}/at/4").json()
    assert v["turn"] == 2 and v["at"] == 4 and v["entries_total"] == 5
    # ...and asking past the end is the end, not an error.
    assert client.get(f"/api/battles/{bid}/at/99").json()["turn"] == 3


def test_the_trajectory_is_one_row_a_turn(client, started):
    bid = started["id"]
    entries(client, bid,
            {"kind": "lead", "side": "p1", "slot": 0, "species": "Incineroar"},
            {"kind": "lead", "side": "p2", "slot": 0, "species": "Gholdengo"},
            {"kind": "turn", "n": 1},
            {"kind": "move", "side": "p1", "slot": 0, "move": "Flare Blitz",
             "target": {"side": "p2", "slot": 0}},
            {"kind": "damage", "side": "p2", "slot": 0, "pct": 40},
            {"kind": "turn", "n": 2})
    t = client.get(f"/api/battles/{bid}/trajectory").json()
    assert [row["turn"] for row in t["turns"]] == [1, 2, 2]
    assert all(0 <= row["lo"] <= row["wp"] <= row["hi"] <= 1 for row in t["turns"])
    assert t["gates"]["version"] == t["version"]


def test_the_speed_read_is_a_bound_and_says_undecided_rather_than_guessing(
        client, team_text, battles_dir):
    """A local team, so the two Pokémon in the claim are certainly on it.

    Their Speed comes back as a *range* because neither their investment nor their nature is
    known — an open sheet hides the spread as surely as a closed one does. The verdict is only
    `faster` or `slower` when the ranges do not overlap; anything else is `undecided`, which is
    reported as often as it is true rather than resolved to whichever end looks likelier.
    """
    bid = client.post("/api/battles", json={"my_team": team_text("valid_basic"),
                                            "their_species": THEIR_SIX}).json()["id"]
    v = entries(client, bid,
                {"kind": "lead", "side": "p1", "slot": 0, "species": "Garchomp"},
                {"kind": "lead", "side": "p2", "slot": 0, "species": "Torkoal"})
    assert not v["errors"]
    read = next(r for r in v["speed"] if r["theirs"] == "Torkoal")
    # Garchomp is base 102 and Torkoal base 20: nothing Torkoal could invest closes that.
    assert read["verdict"] == "faster"
    assert read["my_speed"][0] == read["my_speed"][1]        # yours is known exactly
    assert read["their_speed"][0] < read["their_speed"][1]   # theirs is not

    # Your Incineroar sits at 80 and Pelipper's range is 76–128: the bound has not separated
    # them, and the honest answer is that it has not.
    v = entries(client, bid,
                {"kind": "lead", "side": "p1", "slot": 1, "species": "Incineroar"},
                {"kind": "lead", "side": "p2", "slot": 1, "species": "Pelipper"})
    pair = next(r for r in v["speed"] if r["mine"] == "Incineroar" and r["theirs"] == "Pelipper")
    assert pair["verdict"] == "undecided"
    assert pair["their_speed"][0] < pair["my_speed"][0] < pair["their_speed"][1]
    assert all(r["their_speed"][0] <= r["their_speed"][1] for r in v["speed"])


def test_an_entry_that_could_not_have_happened_comes_back_as_an_error(client, started):
    v = entries(client, started["id"],
                {"kind": "move", "side": "p1", "slot": 0, "move": "Flare Blitz"})
    assert v["errors"] and "nothing active" in v["errors"][0]
    assert not v["started"]


def test_deleting_a_battle_removes_it(client, started):
    bid = started["id"]
    assert client.delete(f"/api/battles/{bid}").status_code == 200
    assert client.get(f"/api/battles/{bid}").status_code == 404
