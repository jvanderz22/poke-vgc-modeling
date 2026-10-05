"""An MCP server over the web app's API (Phase 12): every number an agent quotes comes from a call.

The app is a thin client over a documented HTTP API (docs/web-app.md, "The API"), and this is a
second thin client over the same API, so an agent sees exactly what the page sees: the same
validation, the same brings ranking, the same battle journal and the same engine answers. It
talks to whichever server `VGC_API_URL` names, the deployed one by default, so it is also how an
agent checks a deploy: start a battle, tap it to a position, and time the answer the page would
show (`solve`, which waits for the answer to settle and returns when each part landed).

    VGC_API_URL        default https://vgc-live-battle-calculator.fly.dev (or http://127.0.0.1:8001)
    VGC_WEB_USER       HTTP Basic user, default "vgc"
    VGC_WEB_PASSWORD   from the environment, else the repository's .env (as deploy_fly.sh reads it)

    .venv/bin/python -m vgc.mcp          # stdio; registered for Claude Code in .mcp.json
"""

from __future__ import annotations

import os
import time
from typing import Any

from vgc import paths

DEFAULT_URL = "https://vgc-live-battle-calculator.fly.dev"
INSTRUCTIONS = """Pokémon Champions VGC advisor (Regulation M-C), over the web app's own API.

Quote numbers only from tool results, and say which number it is. A battle's `wp` is the WP model
(how positions like this have gone in human games); the engine's answer (`solve`) is best play from
both sides, and leads only where it beat the model on held-out games (two or fewer a side, open
sheets). Each model's gate verdicts come with it: a failed gate means the number is not presented
as calibrated. Open team sheets do not show Stat Points, so an opponent's spread is always a guess.

A battle is a journal of taps (`add_entries`): lead, turn, move, switch, swap, damage, heal, faint,
status, boost, field, side, reveal, consume, mega, answer, end. The state is replayed from it, so
`undo` pops the last tap and `battle_at` shows any earlier point."""


def _password() -> str | None:
    if os.environ.get("VGC_WEB_PASSWORD"):
        return os.environ["VGC_WEB_PASSWORD"]
    env = paths.ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "VGC_WEB_PASSWORD":
                return value.strip().strip("'\"")
    return None


class Api:
    """The HTTP API, with the deployed app's password when it needs one."""

    def __init__(self, url: str | None = None, client: Any = None):
        import httpx2

        self.url = (url or os.environ.get("VGC_API_URL") or DEFAULT_URL).rstrip("/")
        pw = _password()
        auth = (os.environ.get("VGC_WEB_USER") or "vgc", pw) if pw else None
        # A stopped Fly machine starts on the first request, in about 4 s.
        self.client = client or httpx2.Client(base_url=self.url, auth=auth, timeout=120)

    def call(self, method: str, path: str, **kw: Any) -> Any:
        r = self.client.request(method, path, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:500]}")
        return r.json()


def build(api: Api | None = None):
    """The server, with its tools bound to `api` (a test passes one over the app in process)."""
    from mcp.server.mcpserver import MCPServer

    api = api or Api()
    server = MCPServer("vgc", instructions=INSTRUCTIONS)
    reg = "reg_mc"

    @server.tool()
    def health() -> dict[str, Any]:
        """The regulation the server runs (level, bring count, SP budget, mechanics) and its URL."""
        return api.call("GET", "/api/health") | {"api_url": api.url}

    @server.tool()
    def models() -> dict[str, Any]:
        """Every registered WP model with its gate verdicts, which one serves each regime, and the
        battle modes on offer."""
        return api.call("GET", "/api/models")

    @server.tool()
    def validate_team(text: str) -> dict[str, Any]:
        """Legality of a team in Showdown text, with each Pokémon's stats and any problems."""
        return api.call("POST", "/api/validate", json={"text": text, "regulation": reg})

    @server.tool()
    def pool() -> dict[str, Any]:
        """Legal species ordered by usage, with the legal items, moves and natures."""
        return api.call("GET", "/api/pool")

    @server.tool()
    def compose_team(species: list[str]) -> dict[str, Any]:
        """Six species → a legal team, each with its most common set and that set's share."""
        return api.call("POST", "/api/compose", json={"species": species, "regulation": reg})

    @server.tool()
    def list_teams() -> dict[str, Any]:
        """The saved team library."""
        return api.call("GET", "/api/teams")

    @server.tool()
    def save_team(name: str, text: str, notes: str = "", team_id: str = "") -> dict[str, Any]:
        """Save a team to the library (pass `team_id` to overwrite one)."""
        return api.call("POST", "/api/teams", json={"id": team_id, "name": name, "text": text, "notes": notes,
                                                    "regulation": reg})

    @server.tool()
    def preview(my_team: str, their_team: str = "", their_species: list[str] | None = None,
                limit: int = 15) -> dict[str, Any]:
        """Rank your bring-four choices (and lead pairs) against an opponent: their open sheet as
        text, or with closed sheets their six species. Includes their likely four and the bring
        model's gate verdicts."""
        return api.call("POST", "/api/preview", json={"my_team": my_team, "their_team": their_team,
                                                      "their_species": their_species or [], "limit": limit,
                                                      "regulation": reg})

    @server.tool()
    def simulate(team_a: str, team_b: str, seed: int = 1) -> dict[str, Any]:
        """One seeded heuristic battle between two teams, narrated turn by turn with spectator WP.
        The heuristic's self-play does not predict human results (Phase 6): use it to see a battle,
        not to judge a matchup."""
        return api.call("POST", "/api/simulate", json={"team_a": team_a, "team_b": team_b, "seed": seed,
                                                       "regulation": reg})

    @server.tool()
    def endgames(only: str = "all") -> dict[str, Any]:
        """The decided-endgame set (human games), with criteria and gate verdicts. `only`: all,
        played_out or misses."""
        return api.call("GET", "/api/endgames", params={"only": only})

    @server.tool()
    def endgame(replay_id: str) -> dict[str, Any]:
        """One decided endgame position by position: board, events, WP and both open sheets."""
        return api.call("GET", f"/api/endgames/{replay_id}")

    @server.tool()
    def list_battles() -> dict[str, Any]:
        """Battles saved on the server, newest first, with each one's regime."""
        return api.call("GET", "/api/battles")

    @server.tool()
    def create_battle(my_team: str = "", team_id: str = "", their_team: str = "",
                      their_species: list[str] | None = None, p1_team: str = "", name: str = "") -> dict[str, Any]:
        """Start a battle. Yours against an open sheet: `my_team` (or `team_id`) and `their_team`.
        Against a closed sheet: `their_species` instead. Watching someone else's game: `p1_team` and
        `their_team` (player 2), with no team of yours. Returns the battle's view, with its id."""
        return api.call("POST", "/api/battles", json={
            "name": name, "my_team": my_team, "team_id": team_id, "their_team": their_team,
            "their_species": their_species or [], "p1_team": p1_team, "regulation": reg})

    @server.tool()
    def get_battle(battle_id: str) -> dict[str, Any]:
        """A battle as it stands: the board, the menu the next tap is made from, open questions,
        the WP with its gate verdicts, and what was derived."""
        return api.call("GET", f"/api/battles/{battle_id}")

    @server.tool()
    def add_entries(battle_id: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
        """Append taps, e.g. {"kind": "lead", "side": "p2", "slot": 0, "species": "Incineroar"},
        {"kind": "move", "side": "p1", "slot": 0, "move": "Fake Out", "target": {"side": "p2",
        "slot": 0}}, {"kind": "damage", "side": "p2", "slot": 0, "pct": 88}, {"kind": "turn", "n": 2}.
        Your own HP may be given as {"hp": exact}. Taps that belong together go in one call.
        Returns the new view; a tap that makes no sense is kept and reported in its errors."""
        return api.call("POST", f"/api/battles/{battle_id}/entries", json={"entries": entries, "regulation": reg})

    @server.tool()
    def undo(battle_id: str) -> dict[str, Any]:
        """Remove the last tap."""
        return api.call("POST", f"/api/battles/{battle_id}/undo")

    @server.tool()
    def battle_at(battle_id: str, index: int) -> dict[str, Any]:
        """The battle as it stood after its first `index` taps."""
        return api.call("GET", f"/api/battles/{battle_id}/at/{index}")

    @server.tool()
    def trajectory(battle_id: str) -> dict[str, Any]:
        """The WP at every turn so far, with the band over drawn sets."""
        return api.call("GET", f"/api/battles/{battle_id}/trajectory")

    @server.tool()
    def solve(battle_id: str, wait: bool = True, timeout: float = 60.0) -> dict[str, Any]:
        """The engine's answer for the position as it stands (eligible with two or fewer a side,
        open sheets for doubles). With `wait`, polls until the answer settles (the search is done
        and, in doubles, the guesses at their bulk have landed) or `timeout` seconds pass, and
        returns the last answer with `timeline`: when each depth and value first appeared, the
        server's own `elapsed` beside the wall clock here. The page's budget is about 5 s."""
        start = time.time()
        timeline: list[dict[str, Any]] = []
        last = None
        while True:
            got = api.call("GET", f"/api/battles/{battle_id}/solve")
            mark = (got.get("depth"), got.get("value"), len((got.get("guesses") or {}).get("values") or []))
            if mark != last:
                timeline.append({"wall": round(time.time() - start, 2), "elapsed": got.get("elapsed"),
                                 "depth": got.get("depth"), "value": got.get("value"),
                                 "guesses": len((got.get("guesses") or {}).get("values") or [])})
                last = mark
            settled = not got.get("eligible") or (got.get("searching") is None and
                                                  not (got.get("guesses") or {}).get("pending"))
            if not wait or settled or time.time() - start > timeout:
                return got | {"timeline": timeline, "settled": settled}
            time.sleep(0.25)

    @server.tool()
    def delete_battle(battle_id: str) -> dict[str, Any]:
        """Delete a battle and stop its search."""
        return api.call("DELETE", f"/api/battles/{battle_id}")

    return server


def main() -> None:
    build().run("stdio")
