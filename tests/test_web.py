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
