"""The battle-companion API.

Scope, honestly stated, because the UI has to say the same thing:

* **The models are open-sheet models.** Every WP model is trained with the opponent's items,
  abilities, moves and spreads visible (`info_regime: "ots"`), which is a Bo3 game.
  `/api/preview` accepts a closed-sheet opponent as six species and fills each with its most
  common real set (`prior.py`), because the simulator needs *a* team. That is a point estimate
  over one guess, not an expectation over what they might be holding — Phase 5's belief tracker
  is what makes it the latter, and until then closed-sheet answers are weaker than open ones.
* **Bring recommendations carry their model's gate verdicts.** `vgc wp eval` records which of
  Phase 4's gates a model passed; the preview gate is the one that says whether ranked bring
  options mean anything. Responses include it so the UI can show a guess as a guess.
* **No turn-by-turn advice yet.** That is Phase 6 (expected WP and search) plus the state
  reconstruction work in PLAN.md L5b, which needs its own parity tests first.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from vgc.regulation import load_regulation, to_id
from vgc.web import library, live

STATIC = Path(__file__).parent / "static"

NATURES = ["Adamant", "Bashful", "Bold", "Brave", "Calm", "Careful", "Docile", "Gentle", "Hardy",
           "Hasty", "Impish", "Jolly", "Lax", "Lonely", "Mild", "Modest", "Naive", "Naughty",
           "Quiet", "Quirky", "Rash", "Relaxed", "Sassy", "Serious", "Timid"]


def _reg(reg_id: str):
    try:
        return load_regulation(reg_id)
    except Exception as e:  # a bad regulation id is a client error, not a crash
        raise HTTPException(400, f"unknown regulation {reg_id!r}: {e}") from e


def _validate(text: str, reg) -> dict[str, Any]:
    from vgc.teams.showdown_text import TeamParseError, parse_team
    from vgc.teams.validate import is_legal, validate_team

    try:
        team = parse_team(text)
    except TeamParseError as e:
        return {"ok": False, "legal": False, "problems": [{"severity": "error", "code": "parse",
                                                           "message": str(e), "pokemon": None}], "mons": []}
    problems = validate_team(team, reg)
    from vgc.teams.sets import calc_stats

    mons = []
    for m in team.members:
        try:
            stats = calc_stats(m, reg.dex, reg)
        except Exception:
            stats = {}
        mons.append({"species": m.species, "nickname": m.nickname, "item": m.item, "ability": m.ability,
                     "moves": list(m.moves), "level": m.level, "nature": m.nature, "stats": stats,
                     "sp": asdict(m.sp)})
    return {"ok": True, "legal": is_legal(problems), "mons": mons,
            "problems": [asdict(p) for p in problems]}


def gate_summary(version: str) -> dict[str, Any]:
    """What `vgc wp eval` concluded about this model. A model that misses a gate must not be
    presented as calibrated (PLAN.md), and the UI can only honour that if it is told."""
    from vgc.wp.models import REGISTRY

    if not REGISTRY.exists():
        return {"version": version, "known": False}
    reg = json.loads(REGISTRY.read_text())
    entry = next((e for e in reg["wp"] if e["version"] == version), None)
    if entry is None:
        return {"version": version, "known": False}
    gates = entry.get("gates") or {}
    failed = [k for k, v in gates.items() if isinstance(v, dict) and v.get("pass") is False]
    return {"version": version, "known": True, "evaluated": bool(gates),
            "all_pass": gates.get("all_pass"), "failed": failed,
            # The gate that decides whether ranked bring options are trustworthy. Scored on the
            # 2,899 held-out preview rows against a 0.5 constant, which replaced a correlation over
            # 30 simulated pairings that could not tell signal from noise.
            "preview_gate": gates.get("preview_beats_constant", {}).get("pass"),
            "bring_gate": gates.get("bring_beats_usage", {}).get("pass"),
            # Separate verdict for a battle in progress: a model can be trustworthy turn by turn
            # and useless at preview, which is exactly where Reg M-C stands today.
            "in_battle_pass": gates.get("in_battle_pass"),
            "headline": entry.get("headline", {}).get("human_spectator", {})}


app = FastAPI(title="VGC battle companion", version="0.1")


class TeamText(BaseModel):
    text: str
    regulation: str = "reg_mc"


class SaveTeam(BaseModel):
    id: str = ""
    name: str
    text: str
    notes: str = ""
    archived: bool = False
    regulation: str = "reg_mc"


class PreviewRequest(BaseModel):
    my_team: str = Field(description="your team as Showdown text")
    their_team: str = Field(default="", description="their open team sheet as Showdown text")
    their_species: list[str] = Field(default_factory=list,
                                     description="closed sheets: their six species, sets inferred from usage")
    version: str = ""
    regulation: str = "reg_mc"
    context: str = "human"
    limit: int = 15


class ComposeRequest(BaseModel):
    species: list[str]
    regulation: str = "reg_mc"


class NewBattle(BaseModel):
    name: str = ""
    my_team: str = Field(default="", description="your team as Showdown text")
    team_id: str = Field(default="", description="...or the id of a team in the library")
    their_species: list[str] = Field(default_factory=list,
                                     description="Team Preview Only: their six species")
    their_team: str = Field(default="", description="Open Team Sheets: their six sets as text")
    version: str = ""
    regulation: str = "reg_mc"


class Entries(BaseModel):
    """One tap, or several that belong together — a spread move and both its damage numbers go in
    one call so the screen never renders the half-applied state in between."""

    entries: list[dict[str, Any]]
    regulation: str = "reg_mc"


class SimulateRequest(BaseModel):
    team_a: str
    team_b: str
    seed: int = 1
    policy_a: str = "heuristic"
    policy_b: str = "heuristic"
    version: str = ""
    regulation: str = "reg_mc"


@app.get("/api/health")
def health(regulation: str = "reg_mc") -> dict[str, Any]:
    reg = _reg(regulation)
    return {"regulation": reg.id, "name": reg.name, "format": reg.showdown_format,
            "level": reg.level, "bring": reg.bring, "team_size": reg.team_size,
            "sp_budget": reg.sp_budget, "sp_per_stat_cap": reg.sp_per_stat_cap,
            "tera": reg.tera, "mega": reg.mega,
            "open_sheets_only": True}


@app.get("/api/models")
def models(regulation: str = "reg_mc") -> dict[str, Any]:
    from vgc.wp.models import default_version, registered

    out = [gate_summary(e["version"]) | {"kind": e["kind"], "created": e["created"]}
           for e in registered(regulation)]
    return {"models": out, "default": default_version(regulation)}


@app.post("/api/validate")
def validate(body: TeamText) -> dict[str, Any]:
    return _validate(body.text, _reg(body.regulation))


@app.get("/api/pool")
def pool(regulation: str = "reg_mc") -> dict[str, Any]:
    """Everything the hand-entry pickers need: what is legal, and what people actually use.

    `species` is ordered by usage so the names you will actually type are at the top of an empty
    search box, and each carries the number of sheets it appeared in.
    """
    reg = _reg(regulation)
    from vgc.web.prior import usage

    seen = {sid: v["seen"] for sid, v in usage(reg.id).items()}
    species = [{"name": s["name"], "id": to_id(s["name"]), "types": s["types"],
                "abilities": list((s.get("abilities") or {}).values()),
                "is_mega": bool(s.get("isMega")), "seen": seen.get(to_id(s["name"]), 0)}
               for s in reg.dex.species.values() if not s.get("battleOnly")]
    species.sort(key=lambda s: (-s["seen"], s["name"]))
    return {"species": species,
            "items": sorted({i["name"] for i in reg.dex.items.values()}),
            "moves": sorted({m["name"] for m in reg.dex.moves.values()}),
            "natures": NATURES}


@app.post("/api/compose")
def compose_team(body: ComposeRequest) -> dict[str, Any]:
    """Six species → a legal team, each Pokémon given its most common real set.

    `share` per Pokémon is how often that set was the one used: 0.15 means the guess is one of
    many, and the caller should say so rather than present the result as the opponent's team.
    """
    from vgc.web.prior import compose

    try:
        return compose(body.species, _reg(body.regulation))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.get("/api/teams")
def list_teams(regulation: str = "reg_mc") -> dict[str, Any]:
    return {"teams": [asdict(t) for t in library.load(regulation)]}


@app.post("/api/teams")
def save_team(body: SaveTeam) -> dict[str, Any]:
    reg = _reg(body.regulation)
    checked = _validate(body.text, reg)
    t = library.SavedTeam(id=body.id, name=body.name, text=body.text, notes=body.notes,
                          archived=body.archived, legal=checked["legal"], problems=checked["problems"])
    return {"team": asdict(library.upsert(body.regulation, t)), "validation": checked}


@app.delete("/api/teams/{team_id}")
def remove_team(team_id: str, regulation: str = "reg_mc") -> dict[str, Any]:
    if not library.delete(regulation, team_id):
        raise HTTPException(404, "no such team")
    return {"deleted": team_id}


@app.post("/api/preview")
def preview(body: PreviewRequest) -> dict[str, Any]:
    """Rank your 15 bring choices (and the 6 lead pairs within each) against a known opponent.

    Both sheets are required and both are validated by the real simulator, exactly as a Bo3 game
    would present them. The response carries the model's gate verdicts: when `preview_gate` is
    false the model does not beat answering 50% before turn 1, and the caller is expected to say so
    rather than render a confident list.
    """
    from vgc.engine.runner import RunnerError
    from vgc.wp.tools import preview as run_preview

    reg = _reg(body.regulation)
    version = body.version or (models(body.regulation)["default"] or "")
    if not version:
        raise HTTPException(400, "no WP model is registered for this regulation")

    their_team, inferred = body.their_team, None
    if not their_team:
        # Closed sheets: we know six species. Fill each with its most common real set so there is
        # a team to simulate. The result is a point estimate over one guess, not an expectation
        # over what they might have — Phase 5 is what turns this into the latter.
        if len(body.their_species) != reg.team_size:
            raise HTTPException(422, f"give the opponent's full sheet, or exactly {reg.team_size} species")
        from vgc.web.prior import compose

        try:
            built = compose(body.their_species, reg)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        their_team, inferred = built["text"], built["sets"]

    try:
        result = run_preview(reg, body.my_team, their_team, version, context=body.context)
    except (ValueError, RunnerError) as e:
        # Showdown's own validator rejected a sheet. That is the user's input being wrong, not a
        # server fault, and its message names the offending Pokémon — so pass it straight through.
        raise HTTPException(422, str(e)) from e
    options = result.get("options", [])
    return {"version": version, "gates": gate_summary(version),
            # `best_by_bring` collapses the 6 lead pairs to the best one per set of 4: that is the
            # "which four do I bring" answer, where `options` is the full 90-way ranking.
            "best_by_bring": result.get("best_by_bring", [])[: body.limit],
            "options": options[: body.limit], "n_options": len(options),
            "preview_wp_player": result.get("preview_wp_player"),
            "preview_wp_spectator": result.get("preview_wp_spectator"),
            "their_likely_bring": result.get("their_bring"),
            "mine": result.get("mine"), "theirs": result.get("theirs"),
            # Present only for closed sheets: which sets were guessed, and how common each was.
            "inferred_sets": inferred}


@app.get("/api/endgames")
def endgame_index(regulation: str = "reg_mc", only: str = "all", limit: int = 200) -> dict[str, Any]:
    """Held-out human games the in-battle model called at 90%+ before they finished.

    The index is built offline (`vgc wp endgames`) because it reads every cached replay. The
    response carries the selection criteria and the drop counts as well as the games, because
    "the model was right 239 times" is only a claim if you can see what the 239 were drawn from.

    `only`: `all`, `played_out` (no forfeits — someone had to actually finish the job), or
    `misses` (the games the confident side went on to lose, which are the ones worth reading).
    """
    from vgc.web import endgames

    reg = _reg(regulation)
    index = endgames.load_index(reg)
    if index is None:
        # Not an error: the app is fine, the index has simply never been built. Say what to run.
        return {"built": None, "games": [], "matched": 0, "total": 0,
                "hint": "no endgame index yet — build it with `vgc wp endgames`"}
    games = index["games"]
    if only == "played_out":
        games = [g for g in games if g["ended_by"] == "normal"]
    elif only == "misses":
        games = [g for g in games if not g["correct"]]
    elif only != "all":
        raise HTTPException(422, "only must be one of: all, played_out, misses")
    # `matched` is the filter's answer and `games` is what fits in one response: the UI has to be
    # able to say "200 of 240", not quietly imply the set is smaller than it is.
    return {"built": index["built"], "version": index["version"], "criteria": index["criteria"],
            "counts": index["counts"], "gates": gate_summary(index["version"]),
            "correct": index["correct"], "total": len(index["games"]),
            "matched": len(games), "games": games[:limit]}


@app.get("/api/endgames/{replay_id}")
def endgame_detail(replay_id: str, regulation: str = "reg_mc", version: str = "") -> dict[str, Any]:
    """One of those games, position by position: the board, what happened next, and the WP.

    Scored on demand from the cached replay rather than read from the index, so stepping through
    a game always shows what the named model says *now*, not what it said when the index was cut.
    """
    from vgc.web import endgames
    from vgc.wp.models import in_battle_version

    reg = _reg(regulation)
    chosen = version or (in_battle_version(regulation) or "")
    if not chosen:
        raise HTTPException(400, "no WP model is registered for this regulation")
    try:
        result = endgames.detail(reg, replay_id, chosen)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return result | {"gates": gate_summary(chosen)}


BUILD_HINT = """<!doctype html><meta charset="utf-8"><title>VGC Companion</title>
<body style="font:15px/1.6 system-ui;background:#12141a;color:#e6e8ee;padding:40px;max-width:44em;margin:auto">
<h1 style="font-size:18px">The frontend has not been built</h1>
<p>The React app lives in <code>frontend/</code> and is compiled into this package. It is generated,
so it is not in git.</p>
<pre style="background:#1b1e26;border:1px solid #2c3040;border-radius:8px;padding:12px">npm --prefix frontend install
npm --prefix frontend run build</pre>
<p>Then reload. <code>make web</code> does both. The API is already running —
see <a style="color:#6aa9ff" href="/docs">/docs</a>.</p>"""


@app.post("/api/simulate")
def simulate_battle(body: SimulateRequest) -> dict[str, Any]:
    """Play one battle between two teams and return it turn by turn, with spectator WP.

    Seeded, so the same seed replays exactly. Both sides are driven by Phase 2's heuristic, which
    beats random 98.4% of the time and is not strong play — a line it chooses is not evidence that
    the line is good. Phase 6's search is what would make that claim.
    """
    from vgc.engine.runner import RunnerError
    from vgc.web.simulate import simulate

    reg = _reg(body.regulation)
    from vgc.wp.models import in_battle_version

    # The WP track here is entirely in-battle, so it uses the model that passes the in-battle
    # gates rather than the newest set encoder.
    version = body.version or (in_battle_version(body.regulation) or "")
    try:
        return simulate(reg, body.team_a, body.team_b, seed=body.seed,
                        policy_a=body.policy_a, policy_b=body.policy_b, version=version or None)
    except (ValueError, RunnerError) as e:
        raise HTTPException(422, str(e)) from e


# --- a battle in progress -------------------------------------------------------------------
#
# The journal is the battle and the state is derived from it (`vgc.battle.entry`), so these are
# thinner than they look: every write appends to a list, and every read replays it. That is also
# why there is no "edit turn 6" endpoint — you undo back to it, which cannot leave the state
# describing a game that never happened.

def _battle(reg, battle_id: str):
    from vgc.battle import entry

    blob = live.load(reg.id, battle_id)
    if blob is None:
        raise HTTPException(404, f"no battle {battle_id!r}")
    return blob, entry.Battle(reg, blob["setup"], blob.get("journal"))


def _version(regulation: str, asked: str) -> str:
    return asked or (models(regulation)["default"] or "")


@app.get("/api/battles")
def battles(regulation: str = "reg_mc", limit: int = 50) -> dict[str, Any]:
    return {"battles": live.listing(_reg(regulation).id, limit)}


@app.post("/api/battles")
def new_battle(body: NewBattle) -> dict[str, Any]:
    """Start a battle from your team and what you can see of theirs at preview.

    Team Preview Only is the default and the case built for: six species, nothing else. Open Team
    Sheets is the same battle with item, ability, moves and nature filled in — and the Stat Points
    still hidden, which is why it is one mode of one thing rather than two code paths.
    """
    reg = _reg(body.regulation)
    text = body.my_team
    if not text and body.team_id:
        saved = library.get(reg.id, body.team_id)
        if saved is None:
            raise HTTPException(404, f"no team {body.team_id!r}")
        text = saved.text
    if not text:
        raise HTTPException(422, "give a team, or the id of one in the library")

    if body.their_team:
        from vgc.teams.showdown_text import TeamParseError, parse_team

        try:
            theirs = [{"species": m.species, "item": m.item or "", "ability": m.ability,
                       "moves": list(m.moves), "nature": m.nature}
                      for m in parse_team(body.their_team).members]
        except TeamParseError as e:
            raise HTTPException(422, f"their sheet: {e}") from e
    else:
        if len(body.their_species) != reg.team_size:
            raise HTTPException(422, f"give their sheet, or exactly {reg.team_size} species")
        theirs = [{"species": s} for s in body.their_species]

    unknown = [m["species"] for m in theirs if reg.dex.get_species(m["species"]) is None]
    if unknown:
        raise HTTPException(422, f"not legal in {reg.name}: {', '.join(unknown)}")

    blob = live.create(reg, body.name, text, theirs, _version(reg.id, body.version))
    return battle_view(blob["id"], reg.id)


@app.get("/api/battles/{battle_id}")
def battle_view(battle_id: str, regulation: str = "reg_mc", wp: bool = True) -> dict[str, Any]:
    reg = _reg(regulation)
    blob, b = _battle(reg, battle_id)
    return live.view(reg, blob, b, version=_version(reg.id, blob.get("version") or ""), with_wp=wp)


@app.post("/api/battles/{battle_id}/entries")
def add_entries(battle_id: str, body: Entries) -> dict[str, Any]:
    """Log what you saw. The entries are kept even when one of them turns out not to describe
    anything that could have happened — the error comes back on the view, where it can be undone,
    rather than vanishing as though it had never been sent."""
    reg = _reg(body.regulation)
    blob, b = _battle(reg, battle_id)
    for e in body.entries:
        b.append(e)
    blob["journal"] = b.journal
    blob["turn"] = b.rp.state.turn
    blob["result"] = b.rp.state.winner if b.rp.state.ended else None
    live.save(reg.id, blob)
    return live.view(reg, blob, b, version=_version(reg.id, blob.get("version") or ""))


@app.post("/api/battles/{battle_id}/undo")
def undo(battle_id: str, regulation: str = "reg_mc", count: int = 1) -> dict[str, Any]:
    reg = _reg(regulation)
    blob, b = _battle(reg, battle_id)
    for _ in range(max(1, count)):
        b.undo()
    blob["journal"] = b.journal
    blob["turn"] = b.rp.state.turn
    blob["result"] = b.rp.state.winner if b.rp.state.ended else None
    live.save(reg.id, blob)
    return live.view(reg, blob, b, version=_version(reg.id, blob.get("version") or ""))


@app.get("/api/battles/{battle_id}/at/{index}")
def battle_at(battle_id: str, index: int, regulation: str = "reg_mc") -> dict[str, Any]:
    """The battle as it stood after `index` taps — replaying a prefix, which is the same
    operation as undo without throwing anything away."""
    from vgc.battle import entry

    reg = _reg(regulation)
    blob, b = _battle(reg, battle_id)
    index = max(0, min(index, len(b.journal)))
    prefix = entry.Battle(reg, blob["setup"], b.journal[:index])
    view = live.view(reg, blob, prefix, version=_version(reg.id, blob.get("version") or ""))
    return view | {"at": index, "entries_total": len(b.journal)}


@app.get("/api/battles/{battle_id}/trajectory")
def battle_trajectory(battle_id: str, regulation: str = "reg_mc") -> dict[str, Any]:
    """WP at every turn mark — the walk-back through a finished game.

    One row per turn, not per tap: inside a turn the state passes through partial information (a
    move logged before its damage), and a curve drawn over that measures data entry, not the game.
    """
    reg = _reg(regulation)
    blob, b = _battle(reg, battle_id)
    version = _version(reg.id, blob.get("version") or "")
    if not version:
        raise HTTPException(400, "no WP model is registered for this regulation")
    return {"version": version, "gates": gate_summary(version),
            "turns": live.trajectory(reg, b, version)}


@app.delete("/api/battles/{battle_id}")
def delete_battle(battle_id: str, regulation: str = "reg_mc") -> dict[str, Any]:
    if not live.remove(_reg(regulation).id, battle_id):
        raise HTTPException(404, f"no battle {battle_id!r}")
    return {"deleted": battle_id}


@app.get("/")
def index():
    """The built app, or instructions for building it — never a bare 404, because the build step
    is easy to miss and the API being up makes it look like the app is broken instead."""
    page = STATIC / "index.html"
    return FileResponse(page) if page.exists() else HTMLResponse(BUILD_HINT, status_code=503)


STATIC.mkdir(parents=True, exist_ok=True)  # so the mount survives a fresh checkout
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# Declared last, so it only sees what nothing above claimed. The frontend routes on real paths
# (`/endgames/<replay>/9`), which exist only in the browser — without this, reloading one or
# pasting it to someone else would 404 and the URLs would be decorative.
@app.get("/{client_route:path}", include_in_schema=False)
def spa(client_route: str):
    # An unmatched API path is a missing endpoint and must say so. Returning the page instead
    # would hand a caller 200 and a lump of HTML for a typo'd route, which is far worse to debug.
    if client_route.startswith(("api/", "static/")):
        raise HTTPException(404, f"no such endpoint: /{client_route}")
    return index()


def serve(host: str = "127.0.0.1", port: int = 8001, reload: bool = False) -> None:
    import uvicorn

    uvicorn.run("vgc.web.app:app" if reload else app, host=host, port=port, reload=reload)
