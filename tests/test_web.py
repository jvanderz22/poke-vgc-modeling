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


def test_the_items_that_can_be_used_up_are_berries_gems_and_the_one_shot_items(client):
    used_up = client.get("/api/pool").json()["used_up"]
    assert {"Colbur Berry", "Sitrus Berry", "Focus Sash", "White Herb", "Normal Gem", "Grassy Seed"} <= set(used_up)
    assert not {"Leftovers", "Life Orb", "Choice Scarf", "Rocky Helmet"} & set(used_up)


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
    # The numbers leave their sets unknown; the most common sets are reference, not input.
    assert d["hidden"] is True
    # An open sheet is not a guess, so it must not claim to be one.
    open_sheet = client.post("/api/preview", json={"my_team": teams[0], "their_team": teams[1], "limit": 1}).json()
    assert open_sheet["inferred_sets"] is None and open_sheet["hidden"] is False


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


def test_with_no_pin_the_in_battle_model_is_chosen_by_its_in_battle_gate(monkeypatch, tmp_path):
    """The fallback for a regulation nobody has pinned models for yet. Drawing a WP number on a
    battle in progress is a claim about in-battle calibration, so the choice follows that gate —
    not "newest set encoder"."""
    from vgc.wp import models as wp_models

    rows = [
        {"version": "old-gbt", "kind": "gbt", "created": "2026-01-01", "gates": {"in_battle_pass": True}},
        {"version": "new-gbt", "kind": "gbt", "created": "2026-02-01", "gates": {"in_battle_pass": True}},
        {"version": "shiny-set", "kind": "set", "created": "2026-03-01", "gates": {"in_battle_pass": False}},
    ]
    monkeypatch.setattr(wp_models, "SERVED", tmp_path / "served.json")
    monkeypatch.setattr(wp_models, "registered", lambda reg: rows)
    assert wp_models.in_battle_version("reg_mc") == "new-gbt"  # newest that passes, not newest overall
    assert wp_models.default_version("reg_mc") == "shiny-set"  # bring ranking still wants a set model

    # Nothing passes: fall back to the default rather than refusing to show a battle at all.
    monkeypatch.setattr(wp_models, "registered",
                        lambda reg: [dict(r, gates={"in_battle_pass": False}) for r in rows])
    assert wp_models.in_battle_version("reg_mc") == "shiny-set"


def test_a_pin_beats_newest_and_the_regime_picks_the_pin(monkeypatch, tmp_path):
    """Registering a newer model must not change what the app serves — that used to happen, and
    nothing in the diff said so."""
    from vgc.wp import models as wp_models

    rows = [
        {"version": "old-set", "kind": "set", "created": "2026-01-01", "gates": {}},
        {"version": "old-gbt", "kind": "gbt", "created": "2026-01-02", "gates": {"in_battle_pass": True}},
        {"version": "new-set", "kind": "set", "created": "2026-03-01", "gates": {}},
        {"version": "new-gbt", "kind": "gbt", "created": "2026-03-02", "gates": {"in_battle_pass": True}},
    ]
    served = tmp_path / "served.json"
    served.write_text(json.dumps({"reg_mc": {"bring": "old-set", "in_battle_open": "old-gbt",
                                             "in_battle_closed": "old-set"}}))
    monkeypatch.setattr(wp_models, "SERVED", served)
    monkeypatch.setattr(wp_models, "registered", lambda reg: rows)
    assert wp_models.default_version("reg_mc") == "old-set"
    assert wp_models.in_battle_version("reg_mc", "open") == "old-gbt"
    assert wp_models.in_battle_version("reg_mc", "closed") == "old-set"
    with pytest.raises(ValueError):
        wp_models.in_battle_version("reg_mc", "ots")


@pytest.mark.parametrize("pins, wrong", [
    ({"in_battle_open": "retired-gbt"}, "not registered"),
    ({"bring": "old-gbt"}, "no bring head"),
])
def test_a_pin_that_cannot_be_served_is_an_error_not_a_fallback(monkeypatch, tmp_path, pins, wrong):
    """Falling back is how a swap goes unnoticed, so a bad pin says so instead."""
    from vgc.wp import models as wp_models

    served = tmp_path / "served.json"
    served.write_text(json.dumps({"reg_mc": pins}))
    monkeypatch.setattr(wp_models, "SERVED", served)
    monkeypatch.setattr(wp_models, "registered", lambda reg: [
        {"version": "old-gbt", "kind": "gbt", "created": "2026-01-01", "gates": {}}])
    with pytest.raises(ValueError, match=wrong):
        wp_models.served("reg_mc", next(iter(pins)))


def test_the_committed_pins_resolve_and_the_open_one_passes_its_gate():
    """`models/served.json` against the real registry. The open-sheet pin is held to the gate it
    is scored on; the closed-sheet one is not, because no model passes there yet and the Battle
    page says so rather than hiding the number."""
    from vgc.wp import models as wp_models

    if not wp_models.SERVED.exists():
        pytest.skip("no models/served.json")
    for reg_id in json.loads(wp_models.SERVED.read_text()):
        for role in wp_models.ROLES:
            wp_models.served(reg_id, role)          # raises if it cannot be served
        pinned = wp_models.served(reg_id, f"in_battle_{wp_models.OPEN}")
        if pinned:
            entry = next(e for e in wp_models.registered(reg_id) if e["version"] == pinned)
            assert wp_models.regime_verdict(entry["gates"], wp_models.OPEN)["pass"] is True, pinned


def test_the_modes_on_offer_are_the_regimes_the_backend_defines(client):
    """The new-battle form is built from this list, so a mode is offered exactly when the backend
    has it — and each one says which model it would run and which gate that model answers to.
    Watching someone else's game is a seat on the open-sheet regime, not a regime of its own."""
    from vgc.wp import models as wp_models

    modes = client.get("/api/models").json()["modes"]
    assert [m["sheets"] for m in modes if m["perspective"] == "p1"] == list(wp_models.SHEETS)
    assert [m["sheets"] for m in modes if m["perspective"] == "spectator"] == [wp_models.OPEN]
    for m in modes:
        assert m["version"] == wp_models.in_battle_version("reg_mc", m["sheets"])
        assert m["gate"] == wp_models.REGIME_GATES[m["sheets"]][0]


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


def test_the_same_species_twice_is_refused(client, teams, battles_dir):
    r = client.post("/api/battles", json={"my_team": teams[0], "their_species": THEIR_SIX[:5] + THEIR_SIX[:1]})
    assert r.status_code == 422 and "Species Clause" in r.json()["detail"]


def test_a_battle_is_not_started_from_an_illegal_team(client, teams, battles_dir):
    """Yours is held to everything, Stat Points included; their sheet to all a sheet shows."""
    first, rest = teams[0].split("\n", 1)
    mine = f"{first}\n- Splash\n{rest}"
    r = client.post("/api/battles", json={"my_team": mine, "their_species": THEIR_SIX})
    assert r.status_code == 422 and "your team" in r.json()["detail"]
    r = client.post("/api/battles", json={"my_team": teams[0], "their_team": teams[1].replace("- ", "- Splash\n- ", 1)})
    assert r.status_code == 422 and "their sheet" in r.json()["detail"]


def test_wp_is_the_position_as_shown_and_says_how_wide(started):
    """The number is the position with their unrevealed sets left unknown, which beat averaging
    over drawn sets on held-out closed-sheet games. The draws are still made: the 10th-to-90th
    spread is what their hidden sets are worth here, and `drawn` is their mean."""
    wp = started["wp"]
    assert 0 <= wp["lo"] <= wp["drawn"] <= wp["hi"] <= 1
    assert 0 <= wp["wp"] <= 1
    assert wp["k"] >= 8
    assert {b["species"] for b in wp["belief"]} <= set(THEIR_SIX)
    assert all(0 <= b["concentration"] <= 1 for b in wp["belief"])
    assert "spreads are not filled" in wp["regime"]


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
    # The number is the position as shown, which need not sit inside the band of drawn sets.
    assert all(0 <= row["lo"] <= row["hi"] <= 1 and 0 <= row["wp"] <= 1 for row in t["turns"])
    assert t["gates"]["version"] == t["version"]


def test_a_battle_takes_the_model_pinned_for_its_regime(client, teams, started):
    """Hidden sheets and open sheets are gated separately, so each gets its own model, recorded on
    the battle — and the screen carries that model's verdicts so it can say when one failed."""
    from vgc.wp.models import in_battle_version

    assert started["sheets"] == "closed"
    assert started["wp"]["version"] == in_battle_version("reg_mc", "closed")
    assert started["gates"]["version"] == started["wp"]["version"]
    assert started["verdict"]["sheets"] == "closed"
    assert started["verdict"]["gate"] == "closed_sheet_pass"
    assert started["verdict"]["pass"] == started["gates"]["closed_sheet_pass"]

    r = client.post("/api/battles", json={"my_team": teams[0], "their_team": teams[1]})
    assert r.status_code == 200, r.text
    opened = r.json()
    assert opened["sheets"] == "open"
    assert opened["wp"]["version"] == in_battle_version("reg_mc", "open")
    assert opened["verdict"]["gate"] == "in_battle_pass"
    rows = {b["id"]: b["sheets"] for b in client.get("/api/battles").json()["battles"]}
    assert rows == {started["id"]: "closed", opened["id"]: "open"}


def test_a_battle_saved_without_a_model_gets_its_regimes_pin(client, started):
    """Battles saved before the model was recorded, or with it cleared, answer by regime."""
    from vgc.web import live
    from vgc.wp.models import in_battle_version

    blob = live.load("reg_mc", started["id"])
    blob["version"] = None
    live.save("reg_mc", blob)
    v = client.get(f"/api/battles/{started['id']}").json()
    assert v["wp"]["version"] == in_battle_version("reg_mc", "closed")


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


# --- watching someone else's game (PLAN-v3 step 4) ------------------------------------------------

@pytest.fixture(scope="module")
def two_sheets():
    """Two legal open sheets: the benchmark's fillers, six each."""
    from vgc.regulation import load_regulation
    from vgc.wp import benchmark

    sets = benchmark.load(load_regulation("reg_mc"))["fillers"].strip().split("\n\n")
    return "\n\n".join(sets[:6]), "\n\n".join(sets[1:7])


@pytest.fixture
def watched(client, two_sheets, battles_dir):
    r = client.post("/api/battles", json={"name": "stream", "p1_team": two_sheets[0], "their_team": two_sheets[1]})
    assert r.status_code == 200, r.text
    return r.json()


def test_watching_is_offered_with_the_open_sheet_model(client):
    modes = {m["id"]: m for m in client.get("/api/models").json()["modes"]}
    assert modes["watching"]["perspective"] == "spectator" and modes["watching"]["sheets"] == "open"
    assert modes["watching"]["version"] == modes["open"]["version"]
    assert modes["watching"]["gate"] == modes["open"]["gate"]


def test_a_watched_battle_knows_both_sheets_and_neither_spread(watched):
    assert watched["perspective"] == "spectator" and watched["sheets"] == "open"
    for sid in ("p1", "p2"):
        mons = watched["sides"][sid]["mons"]
        assert all(m["ability"] and m["moves"] for m in mons)
        assert all(m["stats"] is None and m["hp_max"] is None for m in mons)


def test_watching_needs_both_sheets(client, two_sheets, battles_dir):
    r = client.post("/api/battles", json={"p1_team": two_sheets[0]})
    assert r.status_code == 422


def test_watching_needs_two_legal_sheets(client, two_sheets, battles_dir):
    """A sheet read off a video with a field misread is refused, naming what is wrong. A sheet's
    Stat Points aren't checked: it never shows them."""
    p1 = two_sheets[0].replace("Ability: Grassy Surge", "Ability: Drizzle")
    r = client.post("/api/battles", json={"p1_team": p1, "their_team": two_sheets[1]})
    assert r.status_code == 422
    assert "player 1's sheet" in r.json()["detail"] and "Rillaboom" in r.json()["detail"]
    blank = "\n".join(ln for ln in two_sheets[1].splitlines() if not ln.startswith("EVs:"))
    r = client.post("/api/battles", json={"p1_team": two_sheets[0], "their_team": blank})
    assert r.status_code == 200, r.text


def test_a_watched_turn_narrows_both_sides(client, watched):
    """Both sides' spreads are hidden, so both get a belief panel, and the number is still there:
    player 1's chance, from the open-sheet model."""
    bid = watched["id"]
    leads = [{"kind": "lead", "side": sid, "slot": slot, "species": sp}
             for sid, pair in (("p1", ("Rillaboom", "Garchomp")), ("p2", ("Incineroar", "Pelipper")))
             for slot, sp in enumerate(pair)]
    view = entries(client, bid, *leads, {"kind": "turn", "n": 1})
    assert view["started"] and view["wp"].get("error") is None and 0 <= view["wp"]["wp"] <= 1
    assert {b["side"] for b in view["beliefs"]} == {"p1", "p2"}
    assert view["endgame"] == {"eligible": False, "reason": "the engine answers once neither side has more than two Pokémon left"}
    listed = client.get("/api/battles").json()["battles"]
    assert listed[0]["perspective"] == "spectator"


def test_a_password_guards_everything_but_health(monkeypatch):
    """Served beyond localhost, the app asks for a password; Fly's health check still gets in."""
    import base64
    import importlib

    import vgc.web.app as webapp

    monkeypatch.setenv("VGC_WEB_PASSWORD", "hunter2")
    guarded = importlib.reload(webapp)
    try:
        from fastapi.testclient import TestClient

        c = TestClient(guarded.app)
        assert c.get("/api/health").status_code == 200
        refused = c.get("/api/teams")
        # No `WWW-Authenticate`: that is what opens the browser's own password dialog.
        assert refused.status_code == 401 and "www-authenticate" not in refused.headers
        bad = base64.b64encode(b"vgc:wrong").decode()
        assert c.get("/api/teams", headers={"Authorization": f"Basic {bad}"}).status_code == 401
        good = base64.b64encode(b"vgc:hunter2").decode()
        assert c.get("/api/teams", headers={"Authorization": f"Basic {good}"}).status_code == 200

        # A page sends the browser to the sign-in page, which sends it back after.
        page = c.get("/battles/abc?x=1", follow_redirects=False)
        assert page.status_code == 303 and page.headers["location"] == "/login?next=/battles/abc%3Fx%3D1"
        assert 'value="/battles/abc?x=1"' in c.get(page.headers["location"]).text
        assert c.post("/login", data={"user": "vgc", "password": "wrong"}).status_code == 401
        assert c.post("/login", data={"user": "someone", "password": "hunter2"}).status_code == 401
        assert c.post("/login", data={"user": "vgc", "password": "hunter2", "next": "//evil.example"},
                      follow_redirects=False).headers["location"] == "/"
        signed_in = c.post("/login", data={"user": "vgc", "password": "hunter2", "next": "/battles/abc"},
                           follow_redirects=False)
        assert signed_in.status_code == 303 and signed_in.headers["location"] == "/battles/abc"
        assert c.get("/api/teams").status_code == 200
        c.cookies.set("vgc_session", "9999999999.forged")
        assert c.get("/api/teams").status_code == 401
    finally:
        monkeypatch.delenv("VGC_WEB_PASSWORD")
        importlib.reload(webapp)


def test_ally_switch_is_a_tap(client, started):
    """The two on a side trade places, and the menu the next tap is made from says so."""
    entries(client, started["id"],
            {"kind": "lead", "side": "p2", "slot": 0, "species": "Kingambit"},
            {"kind": "lead", "side": "p2", "slot": 1, "species": "Milotic"})
    v = entries(client, started["id"], {"kind": "swap", "side": "p2", "slot": 0})
    assert [a["species"] for a in v["menu"]["p2"]["actives"]] == ["Milotic", "Kingambit"]


def test_the_menu_offers_each_mega_a_hidden_item_allows(client, teams, battles_dir):
    """An unseen item could be either stone, so a closed-sheet Charizard offers both Megas; once
    one side has evolved, nobody on that side is offered another."""
    six = ["Charizard"] + THEIR_SIX[1:]
    bid = client.post("/api/battles", json={"my_team": teams[0], "their_species": six}).json()["id"]
    v = entries(client, bid, {"kind": "lead", "side": "p2", "slot": 0, "species": "Charizard"},
                {"kind": "lead", "side": "p2", "slot": 1, "species": "Milotic"})
    zard, milotic = v["menu"]["p2"]["actives"]
    assert {x["forme"] for x in zard["megas"]} == {"Charizard-Mega-X", "Charizard-Mega-Y"}
    assert milotic["megas"] == []
    v = entries(client, bid, {"kind": "mega", "side": "p2", "species": "Charizard",
                              "forme": "Charizard-Mega-Y", "item": "Charizardite Y"})
    assert all(a["megas"] == [] for a in v["menu"]["p2"]["actives"])


def test_the_view_says_where_the_turn_has_got_to(client, started):
    """The page reads its next question off the view, so a reload resumes mid-turn."""
    v = entries(client, started["id"],
                {"kind": "lead", "side": "p2", "slot": 0, "species": "Kingambit"},
                {"kind": "lead", "side": "p2", "slot": 1, "species": "Milotic"},
                {"kind": "turn", "n": 1},
                {"kind": "cant", "side": "p2", "slot": 0, "reason": "flinch"})
    assert v["turn_progress"]["acted"] == [{"side": "p2", "species": "Kingambit"}]
    assert v["turn_progress"]["moved"] and v["turn_progress"]["awaiting"] is None
    assert isinstance(v["end_of_turn"], list)


def test_a_preview_shows_what_answers_would_lead_to_without_logging_them(client, started):
    """The page shows an answer's consequences before you confirm: the dry run raises the same
    questions logging would, and the journal is untouched."""
    bid = started["id"]
    v = entries(client, bid, {"kind": "lead", "side": "p2", "slot": 0, "species": "Kingambit"})
    q = v["questions"][0]
    r = client.post(f"/api/battles/{bid}/preview", json={"entries": [{"kind": "answer", "question": q["id"], "option": None}]})
    assert r.status_code == 200 and all(x["id"] != q["id"] for x in r.json()["questions"])
    bad = client.post(f"/api/battles/{bid}/preview", json={"entries": [{"kind": "answer", "question": "q99.0", "option": 0}]})
    assert bad.json()["errors"][0]["index"] == 0
    assert client.get(f"/api/battles/{bid}").json()["entries"] == v["entries"]


def test_an_unseen_moveset_offers_its_likeliest_moves_and_every_legal_one(client, started):
    v = entries(client, started["id"], {"kind": "lead", "side": "p2", "slot": 0, "species": "Kingambit"})
    gambit = v["menu"]["p2"]["actives"][0]
    assert gambit["moves"] == [] and len(gambit["likely"]) > 0
    assert {"kowtowcleave", "suckerpunch"} <= {m["id"] for m in gambit["legal"]}
    assert all(m["name"] and "share" in m for m in gambit["likely"])


# --- a Bo3 ----------------------------------------------------------------------------------------

def _game_one(client, team_text):
    """Game 1 of a Bo3 in which Incineroar (80 Speed, known) moves before Pelipper, and Pelipper
    shows its Damp Rock and Hurricane."""
    v = client.post("/api/battles", json={"name": "vs X", "my_team": team_text("valid_basic"),
                                          "their_species": THEIR_SIX, "best_of": 3}).json()
    return entries(client, v["id"],
                   {"kind": "lead", "side": "p1", "slot": 0, "species": "Garchomp"},
                   {"kind": "lead", "side": "p1", "slot": 1, "species": "Incineroar"},
                   {"kind": "lead", "side": "p2", "slot": 0, "species": "Torkoal"},
                   {"kind": "lead", "side": "p2", "slot": 1, "species": "Pelipper"},
                   {"kind": "turn", "n": 1},
                   {"kind": "move", "side": "p1", "slot": 1, "move": "Flare Blitz",
                    "target": {"side": "p2", "slot": 1}, "result": "miss"},
                   {"kind": "move", "side": "p2", "slot": 1, "move": "Hurricane",
                    "target": {"side": "p1", "slot": 0}, "result": "miss"},
                   {"kind": "reveal", "side": "p2", "species": "Pelipper", "what": "item", "value": "Damp Rock"})


def _spe(view, species):
    return next(b for b in view["beliefs"] if b["species"] == species)["bounds"]["spe"]


def test_a_single_game_is_the_default_and_is_not_a_series(started):
    assert started["series"] is None


def test_ending_a_bo3_game_sets_up_the_next_one(client, team_text, battles_dir):
    g1 = _game_one(client, team_text)
    assert g1["series"]["game"] == 1 and g1["series"]["next"] is None
    v = entries(client, g1["id"], {"kind": "end", "winner": "p1"})
    assert v["series"]["score"] == {"p1": 1, "p2": 0}
    g2 = client.get(f"/api/battles/{v['series']['next']}").json()
    assert g2["series"]["game"] == 2 and g2["name"] == "vs X · game 2"
    assert [m["species"] for m in g2["sides"]["p2"]["mons"]] == THEIR_SIX and not g2["started"]


def test_the_next_game_knows_what_the_earlier_one_showed(client, team_text, battles_dir):
    """The race bounds the same Stat Points in game 2, since a team cannot change in a series, and on
    a hidden sheet what Pelipper revealed is known from the start."""
    g1 = _game_one(client, team_text)
    bound = _spe(g1, "Pelipper")
    assert bound[1] < 32
    g2 = client.get(f"/api/battles/{entries(client, g1['id'], {'kind': 'end', 'winner': 'p1'})['series']['next']}").json()
    pel = next(m for m in g2["sides"]["p2"]["mons"] if m["species"] == "Pelipper")
    assert pel["item"] == "damprock" and pel["item_source"] == "earlier game"
    assert pel["moves_used"] == ["hurricane"]
    assert g2["series"]["carried"]["races"] == 1
    g2 = entries(client, g2["id"], {"kind": "lead", "side": "p1", "slot": 1, "species": "Incineroar"},
                 {"kind": "lead", "side": "p2", "slot": 1, "species": "Pelipper"})
    assert _spe(g2, "Pelipper") == bound


def _state(reg, battle_id):
    from vgc.battle import entry
    from vgc.web import live

    blob = live.load("reg_mc", battle_id)
    return entry.Battle(reg, blob["setup"], blob["journal"]).rp.state


def test_the_model_is_given_the_earlier_games_orderings(client, team_text, battles_dir, reg):
    """Game 1 showed Incineroar at least as fast as Pelipper, and game 2's WP evidence says so
    before game 2 has shown anything."""
    from vgc.belief.speed import orderings, series_pairs

    g1 = _game_one(client, team_text)
    seen = orderings(reg, _state(reg, g1["id"]))
    g2 = entries(client, g1["id"], {"kind": "end", "winner": "p1"})["series"]["next"]
    state = _state(reg, g2)
    assert len(series_pairs(reg, state)) == 1
    assert orderings(reg, state) == seen and [x[1] for x in seen[0]] == ["Incineroar", "Pelipper"]


def test_an_earlier_order_under_another_item_is_not_the_models_evidence(client, team_text, battles_dir, reg):
    """A game starts with every item held again, so an order game 1 showed after Pelipper lost a
    Choice Scarf says nothing about the Pelipper of game 2, which is holding it."""
    from vgc.belief.speed import orderings

    v = client.post("/api/battles", json={"my_team": team_text("valid_basic"),
                                          "their_species": THEIR_SIX, "best_of": 3}).json()
    entries(client, v["id"],
            {"kind": "lead", "side": "p1", "slot": 1, "species": "Incineroar"},
            {"kind": "lead", "side": "p2", "slot": 1, "species": "Pelipper"},
            {"kind": "consume", "side": "p2", "species": "Pelipper", "item": "Choice Scarf"},
            {"kind": "turn", "n": 1},
            {"kind": "move", "side": "p1", "slot": 1, "move": "Flare Blitz",
             "target": {"side": "p2", "slot": 1}, "result": "miss"},
            {"kind": "move", "side": "p2", "slot": 1, "move": "Hurricane",
             "target": {"side": "p1", "slot": 1}, "result": "miss"})
    assert orderings(reg, _state(reg, v["id"]))
    g2 = entries(client, v["id"], {"kind": "end", "winner": "p1"})["series"]["next"]
    assert orderings(reg, _state(reg, g2)) == []


def test_undoing_the_end_takes_back_a_next_game_nobody_has_played(client, team_text, battles_dir):
    g1 = _game_one(client, team_text)
    g2 = entries(client, g1["id"], {"kind": "end", "winner": "p1"})["series"]["next"]
    v = client.post(f"/api/battles/{g1['id']}/undo").json()
    assert v["series"]["next"] is None
    assert client.get(f"/api/battles/{g2}").status_code == 404


def test_fixing_an_earlier_game_reaches_the_later_one(client, team_text, battles_dir):
    g1 = _game_one(client, team_text)
    g2 = entries(client, g1["id"], {"kind": "end", "winner": "p1"})["series"]["next"]
    entries(client, g2, {"kind": "lead", "side": "p1", "slot": 0, "species": "Garchomp"})
    # The reveal was a mis-tap: undo back past it in game 1 (the end, then the reveal).
    client.post(f"/api/battles/{g1['id']}/undo?count=2")
    pel = next(m for m in client.get(f"/api/battles/{g2}").json()["sides"]["p2"]["mons"]
               if m["species"] == "Pelipper")
    assert pel["item"] is None and pel["moves_used"] == ["hurricane"]


def test_a_decided_series_has_no_next_game(client, team_text, battles_dir):
    g1 = _game_one(client, team_text)
    g2 = entries(client, g1["id"], {"kind": "end", "winner": "p2"})["series"]["next"]
    v = entries(client, g2, {"kind": "end", "winner": "p2"})
    assert v["series"]["decided"] and v["series"]["next"] is None
    assert [g["result"] for g in v["series"]["games"]] == ["p2", "p2"]
    assert client.post(f"/api/battles/{g2}/next").status_code == 409


def test_best_of_is_one_or_three(client, team_text, battles_dir):
    r = client.post("/api/battles", json={"my_team": team_text("valid_basic"),
                                          "their_species": THEIR_SIX, "best_of": 5})
    assert r.status_code == 422


def test_species_options_rank_what_is_run_and_keep_what_is_legal(client):
    r = client.get("/api/species/Golisopod").json()
    assert r["types"] == ["Bug", "Water"] and r["abilities"][0]["name"] == "Emergency Exit"
    shares = [m["share"] for m in r["moves"]]
    assert shares == sorted(shares, reverse=True)
    assert {"Leech Life", "Sucker Punch"} <= {m["name"] for m in r["moves"]}
    assert client.get("/api/species/Missingno").status_code == 404


# --- a team sheet read off a screenshot -----------------------------------------------------

def test_sheet_read_fills_the_form_from_a_screenshot(client):
    pytest.importorskip("rapidocr")
    image = (paths.ROOT / "tests" / "fixtures" / "sheets" / "broadcast-confer.jpg").read_bytes()
    r = client.post("/api/sheet/read", content=image, headers={"content-type": "image/jpeg"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "CARSON CONFER"
    assert [e["species"] for e in body["entries"]] == [
        "Golisopod", "Raichu", "Archaludon", "Basculegion-F", "Pelipper", "Sneasler"]
    assert body["entries"][1] == {"species": "Raichu", "ability": "Lightning Rod", "nature": "Timid",
                                  "item": "Raichunite Y",
                                  "moves": ["Zap Cannon", "Focus Blast", "Fake Out", "Protect"]}
    assert body["unread"] == [] and body["notes"] == [] and body["problems"] == []


def test_sheet_read_refuses_what_is_not_an_image(client):
    pytest.importorskip("rapidocr")
    assert client.post("/api/sheet/read", content=b"").status_code == 422
    r = client.post("/api/sheet/read", content=b"Golisopod @ Golisopite", headers={"content-type": "image/png"})
    assert r.status_code == 422


def test_a_moment_flagged_while_watching_lands_in_the_review_log(client, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    r = client.post("/api/video/flag", json={"video": "abc123", "video_s": 61.5, "note": "missed a switch"})
    assert r.status_code == 200, r.text
    flags = client.get("/api/video/review").json()["flag"]
    assert [(f["source"], f["video_s"], f["note"]) for f in flags] == [("youtube:abc123", 61.5, "missed a switch")]


def test_a_watched_battle_is_read_frame_by_frame(client, watched, tmp_path, monkeypatch):
    """A frame of the video goes to the battle reader; its reply says where the reading stands.
    Before the leads there is nothing to write, and a still player is not read twice."""
    from vgc.web import app as webapp

    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(webapp, "_readings", {})
    frame = (paths.ROOT / "tests" / "fixtures" / "video" / "r5-choosing.jpg").read_bytes()
    url = f"/api/battles/{watched['id']}/read?video_s=182.5&video=abc123"
    r = client.post(url, content=frame, headers={"content-type": "image/jpeg"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["read_to"] == 182.5 and s["entries"] == [] and not s["ended"] and s["errors"] == []
    assert client.post(url, content=frame, headers={"content-type": "image/jpeg"}).json()["read_to"] == 182.5
    behind = client.post(url.replace("182.5", "100.0"), content=frame, headers={"content-type": "image/jpeg"})
    assert behind.json()["behind"]
    assert client.delete(f"/api/battles/{watched['id']}/read").json() == {"stopped": True, "entries": []}
    assert client.get(f"/api/battles/{watched['id']}").json()["journal"] == []


def test_the_page_gets_the_readers_own_models_and_layout(client):
    """The watch page reads frames on its GPU with what the server reads with: the same model
    files, the same characters, the same regions and threshold."""
    pytest.importorskip("rapidocr")
    from vgc.video import screen

    cfg = client.get("/api/video/ocr").json()
    assert cfg["limit"] == screen.DET_LIMIT and cfg["changed"] == screen.CHANGED
    assert cfg["regions"] == {k: list(v) for k, v in screen.BROADCAST_2026.regions.items()}
    assert len(cfg["keys"]) > 1000 and cfg["det"].endswith(f"v={cfg['version']}")
    r = client.get(cfg["det"])
    assert r.status_code == 200 and len(r.content) > 1_000_000 and "immutable" in r.headers["cache-control"]
    assert client.get("/api/video/ocr/cls.onnx").status_code == 404


def test_a_frame_the_page_read_itself_runs_no_ocr_on_the_server(client, watched, tmp_path, monkeypatch):
    from vgc.video import screen
    from vgc.web import app as webapp

    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(webapp, "_readings", {})
    monkeypatch.setattr(screen, "_lines", lambda crop: pytest.fail("the server ran OCR"))
    frame = (paths.ROOT / "tests" / "fixtures" / "video" / "r5-choosing.jpg").read_bytes()
    read = {region: [] for region in screen.BROADCAST_2026.regions}
    read["message"] = [["Trainers are choosing actions", 300.0, 20.0, 700.0, 50.0]]
    lines = json.dumps(read).encode()
    url = f"/api/battles/{watched['id']}/read?video_s=182.5&video=abc123"
    r = client.post(url, content=lines + frame, headers={"content-type": "image/jpeg", "x-ocr-lines": str(len(lines))})
    assert r.status_code == 200, r.text
    assert r.json()["read_to"] == 182.5 and r.json()["unread"] == []
    bad = client.post(url.replace("182.5", "183"), content=frame, headers={"content-type": "image/jpeg", "x-ocr-lines": "12"})
    assert bad.status_code == 422


def test_only_a_watched_battle_is_read_off_a_video(client, started):
    r = client.post(f"/api/battles/{started['id']}/read?video_s=1", content=b"x", headers={"content-type": "image/jpeg"})
    assert r.status_code == 409


def test_a_finished_game_is_not_read_on(client, watched, monkeypatch):
    from vgc.web import app as webapp

    monkeypatch.setattr(webapp, "_readings", {})
    bid = watched["id"]
    leads = [{"kind": "lead", "side": sid, "slot": slot, "species": sp}
             for sid, pair in (("p1", ("Rillaboom", "Garchomp")), ("p2", ("Incineroar", "Pelipper")))
             for slot, sp in enumerate(pair)]
    entries(client, bid, *leads, {"kind": "turn", "n": 1}, {"kind": "end", "winner": "p1"})
    r = client.post(f"/api/battles/{bid}/read?video_s=900", content=b"not even an image",
                    headers={"content-type": "image/jpeg"})
    assert r.status_code == 200 and r.json()["ended"] and r.json()["entries"] == []


def test_sheet_reads_beyond_one_waiting_are_turned_away(client, monkeypatch):
    """Each queued read holds its image, and a pile of them took the deployed server past 2 GB."""
    import threading

    from vgc.web import app as webapp

    monkeypatch.setattr(webapp, "_SHEET_READS", threading.BoundedSemaphore(1))
    webapp._SHEET_READS.acquire()  # a read already under way
    r = client.post("/api/sheet/read", content=b"\x89PNG", headers={"content-type": "image/png"})
    assert r.status_code == 429
    webapp._SHEET_READS.release()
