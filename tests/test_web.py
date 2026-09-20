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
