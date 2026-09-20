"""The battle-companion API.

Scope, honestly stated, because the UI has to say the same thing:

* **Open team sheets only.** Every WP model is trained with the opponent's items, abilities,
  moves and spreads visible (`info_regime: "ots"`), which is a Bo3 game. Closed sheets need the
  set prior from Phase 5 — you know six species and nothing else, and there is no team to hand
  the simulator. `/api/preview` therefore requires the opponent's full sheet.
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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from vgc.regulation import load_regulation
from vgc.web import library

STATIC = Path(__file__).parent / "static"


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
            # The gate that decides whether ranked bring options are trustworthy.
            "preview_gate": gates.get("preview_tracks_sim", {}).get("pass"),
            "bring_gate": gates.get("bring_beats_usage", {}).get("pass"),
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
    their_team: str = Field(description="the opponent's open team sheet as Showdown text")
    version: str = ""
    regulation: str = "reg_mc"
    context: str = "human"
    limit: int = 15


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
    from vgc.wp.models import REGISTRY

    entries = json.loads(REGISTRY.read_text())["wp"] if REGISTRY.exists() else []
    out = [gate_summary(e["version"]) | {"kind": e["kind"], "created": e["created"]}
           for e in entries if e["regulation"] == regulation]
    # Default to the newest set model: it is the only kind with a bring head.
    sets = [e for e in entries if e["regulation"] == regulation and e["kind"] == "set"]
    return {"models": out, "default": sets[-1]["version"] if sets else None}


@app.post("/api/validate")
def validate(body: TeamText) -> dict[str, Any]:
    return _validate(body.text, _reg(body.regulation))


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
    false these numbers order your options no better than chance on unseen teams, and the caller
    is expected to say so rather than render a confident list.
    """
    from vgc.engine.runner import RunnerError
    from vgc.wp.tools import preview as run_preview

    reg = _reg(body.regulation)
    version = body.version or (models(body.regulation)["default"] or "")
    if not version:
        raise HTTPException(400, "no WP model is registered for this regulation")
    try:
        result = run_preview(reg, body.my_team, body.their_team, version, context=body.context)
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
            "mine": result.get("mine"), "theirs": result.get("theirs")}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    import uvicorn

    uvicorn.run("vgc.web.app:app" if reload else app, host=host, port=port, reload=reload)
