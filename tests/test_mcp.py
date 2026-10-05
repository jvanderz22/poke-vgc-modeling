"""The MCP server is the web API again, for an agent (Phase 12): same calls, same numbers."""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("mcp")


@pytest.fixture
def server(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from vgc.mcp.server import Api, build
    from vgc.web import live
    from vgc.web.app import app

    monkeypatch.setattr(live, "BATTLES", tmp_path / "battles")
    return build(Api("http://test", client=TestClient(app)))


def call(server, name, **args):
    r = asyncio.run(server.call_tool(name, args))
    assert not r.is_error, r.content
    return r.structured_content


def test_the_tools_are_the_api(server):
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {"health", "preview", "create_battle", "add_entries", "solve", "trajectory"} <= names
    h = call(server, "health")
    assert h["regulation"] == "reg_mc" and h["bring"] == 4 and h["api_url"] == "http://test"


def test_a_battle_through_the_tools(server):
    """Start one, read it back, and ask the engine: before two or fewer a side it says why it does
    not answer, at once, rather than waiting out the timeout."""
    from vgc import paths

    pool = sorted((paths.DATA / "teams" / "reg_mc").glob("ots_pool_*.json"))
    if not pool:
        pytest.skip("no team pool built")
    teams = json.loads(pool[-1].read_text())["teams"]
    b = call(server, "create_battle", my_team=teams[0]["text"], their_team=teams[5]["text"])
    v = call(server, "get_battle", battle_id=b["id"])
    assert v["id"] == b["id"] and v["sheets"] == "open"
    got = call(server, "solve", battle_id=b["id"], timeout=5)
    assert got["settled"] and got["eligible"] is False and got["timeline"]
    assert call(server, "delete_battle", battle_id=b["id"]) == {"deleted": b["id"]}
