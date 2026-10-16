"""The battle-companion API.

Scope, honestly stated, because the UI has to say the same thing:

* **The models are open-sheet models.** Every WP model is trained with the opponent's items,
  abilities, moves and spreads visible (`info_regime: "ots"`), which is a Bo3 game.
  `/api/preview` accepts a closed-sheet opponent as six species. The simulator needs *a* team, so
  each is built with its most common real set (`prior.py`), and then that set is hidden again: the
  model is shown the position as a closed sheet shows it. Closed-sheet answers are still weaker
  than open ones.
* **Bring recommendations carry their model's gate verdicts.** `vgc wp eval` records which of
  Phase 4's gates a model passed; the preview gate is the one that says whether ranked bring
  options mean anything. Responses include it so the UI can show a guess as a guess.
* **No turn-by-turn advice yet.** That is Phase 6 (expected WP and search) plus the state
  reconstruction work in PLAN.md L5b, which needs its own parity tests first.
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from vgc.battle import rules
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


def _validate(text: str, reg, sheet: bool = False) -> dict[str, Any]:
    from vgc.teams.showdown_text import TeamParseError, parse_team
    from vgc.teams.validate import is_legal, validate_sheet, validate_team

    try:
        team = parse_team(text)
    except TeamParseError as e:
        return {"ok": False, "legal": False, "problems": [{"severity": "error", "code": "parse",
                                                           "message": str(e), "pokemon": None}], "mons": []}
    problems = (validate_sheet if sheet else validate_team)(team, reg)
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
            # And a third, for the regime. `in_battle_pass` is scored on Open Team Sheets games,
            # which the Bo3 ladder plays and a cartridge does not; this one is scored on the
            # held-out Team Preview Only shard, which is the regime this app is used in.
            "closed_sheet_pass": gates.get("closed_sheet_pass"),
            "closed_headline": entry.get("headline", {}).get("closed_spectator", {}),
            "headline": entry.get("headline", {}).get("human_spectator", {})}


@asynccontextmanager
async def _lifespan(_: FastAPI):
    # The solver processes a doubles endgame is answered with: warm before the first one is asked.
    from vgc.web import solving

    solving.warm()
    yield
    # A 1v1 search runs in node processes the server started; they do not end with it.
    from vgc.web import solving

    solving.cancel()


app = FastAPI(title="VGC battle companion", version="0.1", lifespan=_lifespan)

# A password, for a copy served beyond this machine (`deploy/`): `VGC_WEB_PASSWORD`. Unset, the
# app is open, as it is on localhost. `/api/health` stays open for the platform's health check.
# Without it a public copy would run anybody's solves and simulations on its owner's bill.
#
# Both ways in take user `VGC_WEB_USER` ("vgc" by default) and that password. A browser signs in
# at `/login`, which sets a signed cookie; a script (the MCP server, `fly_timing.py`) sends HTTP
# Basic. A 401 never carries `WWW-Authenticate`, so the browser's own password dialog never opens.
_PASSWORD = os.environ.get("VGC_WEB_PASSWORD")
if _PASSWORD:
    import base64
    import hashlib
    import hmac
    import html
    import secrets
    import time
    from string import Template
    from urllib.parse import parse_qs, quote

    from fastapi import Request
    from fastapi.responses import RedirectResponse, Response

    _COOKIE = "vgc_session"
    _SESSION_DAYS = 30

    def _sign(expires: int) -> str:
        # Keyed by the password, so changing the secret signs everybody out.
        return hmac.new(_PASSWORD.encode(), f"vgc-session:{expires}".encode(), hashlib.sha256).hexdigest()

    def _session_ok(token: str) -> bool:
        expires, _, mac = token.partition(".")
        return expires.isdigit() and int(expires) > time.time() \
            and secrets.compare_digest(mac, _sign(int(expires)))

    def _credentials_ok(user: str, password: str) -> bool:
        return (secrets.compare_digest(user.encode(), os.environ.get("VGC_WEB_USER", "vgc").encode())
                and secrets.compare_digest(password.encode(), _PASSWORD.encode()))

    def _basic_ok(header: str) -> bool:
        try:
            user, _, password = base64.b64decode(header[6:]).decode().partition(":") \
                if header.startswith("Basic ") else ("", "", "")
        except ValueError:
            return False
        return _credentials_ok(user, password)

    def _next(path: str | None) -> str:
        # Only a path on this site: `//elsewhere.com` would make the form an open redirect.
        return path if path and path.startswith("/") and not path.startswith("//") else "/"

    @app.middleware("http")
    async def _password(request: Request, call_next):
        if request.url.path in ("/api/health", "/login", "/logout") \
                or _session_ok(request.cookies.get(_COOKIE, "")) \
                or _basic_ok(request.headers.get("authorization", "")):
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            return Response('{"detail": "password required"}', 401, media_type="application/json")
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(target)}", 303)

    def _login_page(next_path: str, user: str = "", wrong: bool = False) -> HTMLResponse:
        error = '<p class="err">That username or password is not right.</p>' if wrong else ""
        page = Template(LOGIN_PAGE).substitute(next=html.escape(next_path), user=html.escape(user),
                                               error=error)
        return HTMLResponse(page, 401 if wrong else 200)

    @app.get("/login", include_in_schema=False)
    def login_form(next: str = "/"):
        return _login_page(_next(next))

    @app.post("/login", include_in_schema=False)
    async def login(request: Request):
        # Parsed by hand: FastAPI's `Form` needs python-multipart for one field.
        form = parse_qs((await request.body()).decode())
        user, password = form.get("user", [""])[0], form.get("password", [""])[0]
        next_path = _next(form.get("next", ["/"])[0])
        if not _credentials_ok(user, password):
            return _login_page(next_path, user, wrong=True)
        expires = int(time.time()) + _SESSION_DAYS * 86400
        page = RedirectResponse(next_path, 303)
        # Fly ends TLS at its proxy, so the scheme the browser used is in the forwarded header.
        https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        page.set_cookie(_COOKIE, f"{expires}.{_sign(expires)}", max_age=_SESSION_DAYS * 86400,
                        httponly=True, secure=https, samesite="lax")
        return page

    @app.get("/logout", include_in_schema=False)
    def logout():
        page = RedirectResponse("/login", 303)
        page.delete_cookie(_COOKIE)
        return page


class TeamText(BaseModel):
    text: str
    regulation: str = "reg_mc"
    sheet: bool = Field(default=False, description="an open team sheet: no Stat Points to check")


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
    p1_team: str = Field(default="", description="Watching someone else's game: player 1's open "
                         "sheet as text, with `their_team` as player 2's. No team of yours.")
    p1_name: str = Field(default="", description="Watching: player 1's name, as the broadcast shows it")
    p2_name: str = Field(default="", description="Watching: player 2's name")
    best_of: int = Field(default=1, description="1, or 3 for game 1 of a series: ending a game sets "
                         "up the next, which starts out knowing what the earlier games showed")
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
    from vgc.wp.models import OPEN, REGIME_GATES, SHEETS, default_version, in_battle_version, registered

    out = [gate_summary(e["version"]) | {"kind": e["kind"], "created": e["created"]}
           for e in registered(regulation)]
    # The battle modes the app can offer, in order, each with the model it would run. This list is
    # what the new-battle form is built from, so a mode exists in the UI exactly when it exists here.
    modes = [{"id": s, "sheets": s, "perspective": "p1", "version": in_battle_version(regulation, s),
              "gate": REGIME_GATES[s][0]} for s in SHEETS]
    # Watching someone else's open-sheet game: the open-sheet model and gate, from the seat that
    # gate is scored from (`in_battle_ece` is measured on spectator rows). PLAN-v3 step 4.
    modes.append({"id": "watching", "sheets": OPEN, "perspective": "spectator",
                  "version": in_battle_version(regulation, OPEN), "gate": REGIME_GATES[OPEN][0]})
    return {"models": out, "default": default_version(regulation), "modes": modes}


@app.post("/api/validate")
def validate(body: TeamText) -> dict[str, Any]:
    return _validate(body.text, _reg(body.regulation), sheet=body.sheet)


SHEET_IMAGE_LIMIT = 15 * 1024 * 1024
# Sheet reads take turns on one OCR engine; one running and one waiting is all that is worth
# holding, and anything more is turned away rather than queued (each holds its image meanwhile).
_SHEET_READS = threading.BoundedSemaphore(2)


@app.post("/api/sheet/read")
async def sheet_read(request: Request, regulation: str = "reg_mc") -> dict[str, Any]:
    """An open team sheet read off a screenshot, sent as the request body (any image type).

    What is read only fills the form: a name that didn't read clearly is left blank and listed in
    `unread`, never guessed, and `problems` is the validator's verdict without the Stat Point
    checks a sheet can't meet. PLAN-video decision 3.
    """
    reg = _reg(regulation)
    data = await request.body()
    if not data:
        raise HTTPException(422, "no image in the request")
    if len(data) > SHEET_IMAGE_LIMIT:
        raise HTTPException(413, f"image over {SHEET_IMAGE_LIMIT // (1024 * 1024)} MB")
    try:
        from vgc.video.sheet import problems, read_sheet
    except ImportError as e:
        raise HTTPException(501, f"this server can't read images: {e}") from e

    def run() -> dict[str, Any]:
        import io

        from PIL import Image, UnidentifiedImageError

        try:
            Image.open(io.BytesIO(data)).verify()
        except (UnidentifiedImageError, OSError) as e:
            raise HTTPException(422, "that isn't an image this server can read") from e
        read = read_sheet(data, reg)
        return {**read.as_dict(), "problems": [asdict(p) for p in problems(read, reg)]}

    if not _SHEET_READS.acquire(blocking=False):
        raise HTTPException(429, "still reading the frames before this one")
    try:
        return await run_in_threadpool(run)  # about three seconds of OCR: off the event loop
    except RuntimeError as e:  # RapidOCR missing: the server was built without '.[video]'
        raise HTTPException(501, str(e)) from e
    finally:
        _SHEET_READS.release()


class VideoFlag(BaseModel):
    video: str = Field(description="the YouTube id being watched")
    video_s: float | None = Field(default=None, description="seconds into the video")
    note: str = Field(default="", max_length=500)


@app.post("/api/video/flag")
def video_flag(body: VideoFlag) -> dict[str, Any]:
    """A moment marked from the watch page: something the reader got wrong or didn't catch, kept
    in the review log (`vgc.video.review`) beside what the reader itself missed."""
    from vgc.video import review

    return review.flag(f"youtube:{body.video}", body.video_s, body.note)


@app.get("/api/video/review")
def video_review(all: bool = False) -> dict[str, Any]:
    """The review log, grouped: moments flagged, phrasings read provisionally, and what was missed."""
    from vgc.video import review

    return review.summary(review.load(), quiet=not all)


_ocr_lock = threading.Lock()
_ocr: dict[str, Any] = {}


def _ocr_models() -> dict[str, Any]:
    """The battle reader's models as RapidOCR resolved them, with a version from their bytes so the
    page can cache them for good. Loads them on first use (the reader would anyway)."""
    with _ocr_lock:
        if not _ocr:
            import hashlib

            try:
                from vgc.video import ocr, screen

                models = ocr.browser_models(screen._engine())
            except (ImportError, RuntimeError) as e:
                raise HTTPException(501, f"this server can't read video: {e}") from e
            digest = hashlib.sha256()
            for part in ("det", "rec"):
                digest.update(models[part].read_bytes())
            _ocr.update(models, version=digest.hexdigest()[:12])
        return _ocr


@app.get("/api/video/ocr")
def video_ocr() -> dict[str, Any]:
    """What the watch page needs to read a battle's regions itself (frontend/src/ocr), so the server
    doesn't: the models' URLs, the recognizer's characters, the detector's cap, and the layout and
    change threshold the server's own reader uses. Its lines go with each frame to `read_frame`."""
    from vgc.video import screen

    m = _ocr_models()
    return {"version": m["version"], "det": f"/api/video/ocr/det.onnx?v={m['version']}",
            "rec": f"/api/video/ocr/rec.onnx?v={m['version']}", "keys": m["keys"],
            "limit": screen.DET_LIMIT, "work_width": screen.WORK_WIDTH, "changed": screen.CHANGED,
            "regions": screen.BROADCAST_2026.regions}


@app.get("/api/video/ocr/{part}.onnx")
def video_ocr_model(part: str) -> FileResponse:
    """One of the reader's models. Its URL carries the version, so it is cached for good."""
    if part not in ("det", "rec"):
        raise HTTPException(404, "no such model")
    return FileResponse(_ocr_models()[part], media_type="application/octet-stream",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


READ_LINES_LIMIT = 64 * 1024


def _read_lines(data: bytes, header: str | None, regions: dict[str, Any]) -> tuple[dict[str, list] | None, bytes]:
    """Split a frame's body into the lines the page read itself and the JPEG. With `x-ocr-lines: n`,
    the body starts with n bytes of UTF-8 JSON `{region: [[text, x0, y0, x1, y1], ...]}`; without
    it, the body is the JPEG alone and the server reads it."""
    if header is None:
        return None, data
    try:
        n = int(header)
    except ValueError as e:
        raise HTTPException(422, "x-ocr-lines is the length of the lines") from e
    if not 0 < n <= min(READ_LINES_LIMIT, len(data)):
        raise HTTPException(413 if n > READ_LINES_LIMIT else 422, "x-ocr-lines is out of range")
    try:
        raw = json.loads(data[:n].decode("utf-8"))
        lines = {key: [(str(t), float(x0), float(y0), float(x1), float(y1)) for t, x0, y0, x1, y1 in got]
                 for key, got in raw.items() if key in regions}
    except (ValueError, TypeError, AttributeError) as e:
        raise HTTPException(422, f"the lines aren't lines by region: {e}") from e
    return lines, data[n:]


# Battles being read off a video, by battle id: in memory, so a restart ends them. The journal is
# the record; a reading restarted on the same battle carries on from it (`Reading.resync`).
_readings: dict[str, Any] = {}
FRAME_LIMIT = 4 * 1024 * 1024


@app.post("/api/battles/{battle_id}/read")
async def read_frame(battle_id: str, request: Request, video_s: float, video: str = "",
                     regulation: str = "reg_mc") -> dict[str, Any]:
    """One frame of the video beside a watched battle, sent as the request body (JPEG), taken
    `video_s` into the video. What it settles is appended to the battle's journal, each entry
    stamped with its `video_s`; the reply says what was added and where the reading stands.

    One frame at a time per battle: the page sends the next when this one has been answered, so a
    slow host reads fewer frames rather than falling behind. PLAN-video step 4.

    A page that reads the regions itself (GET /api/video/ocr) puts its lines before the JPEG and
    says how many bytes they take in `x-ocr-lines`; the server then runs no OCR on the frame."""
    reg = _reg(regulation)
    data = await request.body()
    if not data:
        raise HTTPException(422, "no frame in the request")
    if len(data) > FRAME_LIMIT:
        raise HTTPException(413, f"frame over {FRAME_LIMIT // (1024 * 1024)} MB")
    try:
        from vgc.video.reading import Reading
        from vgc.video.screen import BROADCAST_2026
    except ImportError as e:
        raise HTTPException(501, f"this server can't read video: {e}") from e
    lines, data = _read_lines(data, request.headers.get("x-ocr-lines"), BROADCAST_2026.regions)

    def run() -> dict[str, Any]:
        import io

        from PIL import Image, UnidentifiedImageError

        blob, b = _battle(reg, battle_id)
        if blob["setup"].get("perspective") != "spectator":
            raise HTTPException(409, "only a watched game (both sheets open) can be read off a video")
        if b.rp.state.ended and battle_id not in _readings:
            # Over: what the broadcast shows next (the desk, the next game's sheets) isn't this game.
            from vgc.video.reading import Status

            return {**Status(read_to=video_s, ended=True).as_dict(), "errors": list(b.rp.errors)}
        reading = _readings.get(battle_id)
        if reading is None:
            reading = _readings[battle_id] = Reading(reg, blob["setup"], f"youtube:{video}" if video else battle_id)
            reading.resync(b.journal)
        if not reading.lock.acquire(blocking=False):
            raise HTTPException(409, "a frame of this battle is already being read")
        try:
            try:
                image = Image.open(io.BytesIO(data)).convert("RGB")
            except (UnidentifiedImageError, OSError) as e:
                raise HTTPException(422, "that isn't an image this server can read") from e
            resynced = not reading.follows(b.journal)
            if resynced:
                reading.resync(b.journal)
            status = reading.frame(video_s, image, lines)
            status.resynced = resynced
            if status.entries:
                for e in status.entries:
                    b.append(e)
                blob["journal"] = b.journal
                blob["turn"] = b.rp.state.turn
                blob["result"] = b.rp.state.winner if b.rp.state.ended else None
                live.save(reg.id, blob)
                live.after_edit(reg, blob, b.rp.state.ended)
            if status.ended:
                _readings.pop(battle_id, None)
            return {**status.as_dict(), "errors": list(b.rp.errors)}
        finally:
            reading.lock.release()

    try:
        return await run_in_threadpool(run)
    except RuntimeError as e:  # RapidOCR missing: the server was built without '.[video]'
        raise HTTPException(501, str(e)) from e


@app.delete("/api/battles/{battle_id}/read")
def stop_reading(battle_id: str, regulation: str = "reg_mc") -> dict[str, Any]:
    """Stop reading: what the reader still held (a move waiting on its results) is written, and the
    reading is dropped. Frames sent later start a new one from the journal as it stands."""
    reg = _reg(regulation)
    reading = _readings.pop(battle_id, None)
    if reading is None:
        return {"stopped": False}
    with reading.lock:
        blob, b = _battle(reg, battle_id)
        if not reading.follows(b.journal):
            return {"stopped": True, "entries": []}
        status = reading.finish()
        for e in status.entries:
            b.append(e)
        if status.entries:
            blob["journal"] = b.journal
            blob["turn"] = b.rp.state.turn
            blob["result"] = b.rp.state.winner if b.rp.state.ended else None
            live.save(reg.id, blob)
            live.after_edit(reg, blob, b.rp.state.ended)
        return {"stopped": True, "entries": status.entries}


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
            "used_up": sorted({i["name"] for k, i in reg.dex.items.items() if rules.used_up(reg, k)}),
            "moves": sorted({m["name"] for m in reg.dex.moves.values()}),
            "natures": NATURES}


@app.get("/api/species/{name}")
def species_options(name: str, regulation: str = "reg_mc") -> dict[str, Any]:
    """What a sheet can say about one species, for entering a sheet by hand: its types, its legal
    abilities and moves, and the items and natures people run on it. Each list is ordered by how
    often it appears on that species' sheets (`share`, 0 for a legal option nobody has run), so the
    one you are looking for is usually near the top."""
    reg = _reg(regulation)
    entry = reg.dex.get_species(name)
    if entry is None or entry.get("battleOnly"):
        raise HTTPException(404, f"{name!r} is not a species in {reg.name}")
    from vgc.belief import sets as set_belief

    try:
        belief = set_belief.for_species(reg, entry["name"])
    except Exception:                   # no corpus built: the dex still knows what is legal
        belief = None
    total = sum(s.count for s in belief.sheets) if belief else 0

    def share(key, value: str) -> float:
        if not total:
            return 0.0
        return round(sum(s.count for s in belief.sheets if key(s) == value) / total, 3)

    def ranked(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(rows, key=lambda r: (-r["share"], r["name"]))

    moves = belief.moves(top=999) if belief else []
    move_share = dict(moves)
    move_rows = [{"name": (reg.dex.get_move(mv) or {}).get("name", mv),
                  "share": round(move_share.get(mv, 0.0), 3)}
                 for mv in set_belief.legal_moves(reg, entry["name"])]
    abilities = [{"name": a, "share": share(lambda s: s.ability, to_id(a))}
                 for a in dict.fromkeys((entry.get("abilities") or {}).values())]
    items = {}
    for s_ in (belief.sheets if belief else []):
        if s_.item and s_.item != set_belief.NONE:
            items[s_.item] = items.get(s_.item, 0) + s_.count
    item_rows = [{"name": (reg.dex.get_item(i) or {}).get("name", i), "share": round(n / total, 3)}
                 for i, n in items.items() if reg.dex.get_item(i)]
    natures = {}
    for s_ in (belief.sheets if belief else []):
        if s_.nature:
            natures[s_.nature.capitalize()] = natures.get(s_.nature.capitalize(), 0) + s_.count
    return {"name": entry["name"], "types": entry.get("types", []), "seen": total,
            "abilities": ranked(abilities), "moves": ranked(move_rows), "items": ranked(item_rows),
            "natures": ranked([{"name": n, "share": round(c / total, 3)} for n, c in natures.items()
                               if n in NATURES])}


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
        # Closed sheets: we know six species. The simulator needs a legal team, so each is built
        # with its most common set, and then `hidden` hides those sets again: the model is shown
        # what a closed sheet shows (docs/phase8-findings.md, "which number leads on a closed sheet").
        if len(body.their_species) != reg.team_size:
            raise HTTPException(422, f"give the opponent's full sheet, or exactly {reg.team_size} species")
        from vgc.web.prior import compose

        try:
            built = compose(body.their_species, reg)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        their_team, inferred = built["text"], built["sets"]

    try:
        result = run_preview(reg, body.my_team, their_team, version, context=body.context,
                             hidden=inferred is not None)
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
            # Present only for closed sheets: each Pokémon's most common set and its share, for
            # reference. The numbers above leave their sets unknown and do not use these.
            "inferred_sets": inferred, "hidden": result.get("hidden", False)}


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
    from vgc.wp.models import OPEN, in_battle_version

    reg = _reg(regulation)
    chosen = version or (in_battle_version(regulation, OPEN) or "")  # the endgames are OTS games
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

# The deployed copy's sign-in (see `_PASSWORD`). Standalone, because the app's own bundle is
# behind the password too. `$next`, `$user` and `$error` are filled in by `_login_page`.
LOGIN_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>VGC Companion</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 16px;
         background: #12141a; color: #e6e8ee; font: 15px/1.5 -apple-system, BlinkMacSystemFont, system-ui, sans-serif; }
  form { width: 100%; max-width: 340px; background: #1b1e26; border: 1px solid #2c3040;
         border-radius: 12px; padding: 24px; display: grid; gap: 14px; }
  h1 { font-size: 16px; margin: 0; letter-spacing: .2px; }
  label { display: grid; gap: 6px; color: #9aa1b4; font-size: 13px; }
  input { font: inherit; color: inherit; background: #161922; border: 1px solid #2c3040;
          border-radius: 8px; padding: 9px 11px; }
  input:focus { outline: 2px solid #6aa9ff; outline-offset: -1px; border-color: transparent; }
  button { font: inherit; font-weight: 600; color: #12141a; background: #6aa9ff; border: 0;
           border-radius: 8px; padding: 9px 11px; cursor: pointer; }
  .err { margin: 0; color: #e06666; font-size: 13px; }
</style>
<form method="post" action="/login">
  <h1>VGC Companion</h1>
  $error
  <label>Username<input name="user" value="$user" autocomplete="username" autocapitalize="none" autofocus required></label>
  <label>Password<input type="password" name="password" autocomplete="current-password" required></label>
  <input type="hidden" name="next" value="$next">
  <button type="submit">Sign in</button>
</form>
</html>"""


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
    from vgc.wp.models import OPEN, in_battle_version

    # The WP track here is entirely in-battle, and a simulation shows both sheets, so it uses the
    # model pinned for open-sheet battles rather than the one that ranks brings.
    version = body.version or (in_battle_version(body.regulation, OPEN) or "")
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


def _version(regulation: str, blob: dict[str, Any]) -> str:
    """The model a battle's WP comes from: the one it was started with, else the one pinned for
    its regime. A battle keeps its model when the pin moves later, so its numbers do not change
    under it between one tap and the next."""
    from vgc.wp.models import in_battle_version

    return blob.get("version") or in_battle_version(regulation, live.sheets(blob)) or ""


def _view(reg, blob: dict[str, Any], b, **kw: Any) -> dict[str, Any]:
    """A battle's screen, with the gate verdicts for the model behind its number — the page has to
    be able to say when that model failed a gate, and it can only say what it is told. `verdict`
    is the one for this battle's regime, so the page does not have to know which gate that is."""
    from vgc.wp.models import registered, regime_verdict

    version = _version(reg.id, blob)
    entry = next((e for e in registered(reg.id) if e["version"] == version), None)
    verdict = regime_verdict((entry or {}).get("gates") or {}, live.sheets(blob))
    return live.view(reg, blob, b, version=version, **kw) | {"gates": gate_summary(version),
                                                              "verdict": verdict}


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
    if body.p1_team:
        return _watch(reg, body)
    text = body.my_team
    if not text and body.team_id:
        saved = library.get(reg.id, body.team_id)
        if saved is None:
            raise HTTPException(404, f"no team {body.team_id!r}")
        text = saved.text
    if not text:
        raise HTTPException(422, "give a team, or the id of one in the library")
    _require_legal(reg, text, "your team")

    if body.their_team:
        theirs = _sheet(reg, body.their_team, "their")
    else:
        if len(body.their_species) != reg.team_size:
            raise HTTPException(422, f"give their sheet, or exactly {reg.team_size} species")
        theirs = [{"species": s} for s in body.their_species]
        unknown = [s for s in body.their_species if reg.dex.get_species(s) is None]
        if unknown:
            raise HTTPException(422, f"not legal in {reg.name}: {', '.join(unknown)}")
        nums = [reg.dex.get_species(s)["num"] for s in body.their_species]
        if reg.species_clause and len(set(nums)) < len(nums):
            raise HTTPException(422, f"Species Clause: their species repeat ({', '.join(body.their_species)})")

    from vgc.wp.models import CLOSED, OPEN, in_battle_version

    # The model is chosen by the regime the battle is played in, and recorded on the battle.
    version = body.version or in_battle_version(reg.id, OPEN if body.their_team else CLOSED)
    blob = live.create(reg, body.name, text, theirs, version, best_of=_best_of(body.best_of))
    return battle_view(blob["id"], reg.id)


def _best_of(n: int) -> int:
    if n not in (1, 3):
        raise HTTPException(422, "best_of is 1 or 3")
    return n


def _require_legal(reg, text: str, whose: str, sheet: bool = False):
    """A battle starts only from a legal team: each error the validator finds is named in a 422.
    A sheet is held to everything but the Stat Points, which it never shows."""
    from vgc.teams.showdown_text import TeamParseError, parse_team
    from vgc.teams.validate import validate_sheet, validate_team

    try:
        team = parse_team(text)
    except TeamParseError as e:
        raise HTTPException(422, f"{whose}: {e}") from e
    errors = [p for p in (validate_sheet if sheet else validate_team)(team, reg) if p.severity == "error"]
    if errors:
        raise HTTPException(422, f"{whose} isn't legal in {reg.name}: "
                                 + "; ".join(f"{p.pokemon}: {p.message}" if p.pokemon else p.message for p in errors))
    return team


def _sheet(reg, text: str, whose: str) -> list[dict[str, Any]]:
    team = _require_legal(reg, text, f"{whose} sheet", sheet=True)
    return [{"species": m.species, "item": m.item or "", "ability": m.ability,
             "moves": list(m.moves), "nature": m.nature} for m in team.members]


def _watch(reg, body: NewBattle) -> dict[str, Any]:
    """Someone else's game, both sheets open: a spectator's battle, whose WP is player 1's."""
    from vgc.wp.models import OPEN, in_battle_version

    if not body.their_team:
        raise HTTPException(422, "watching needs both players' sheets")
    p1, p2 = _sheet(reg, body.p1_team, "player 1's"), _sheet(reg, body.their_team, "player 2's")
    version = body.version or in_battle_version(reg.id, OPEN)
    names = {k: v.strip() for k, v in (("p1", body.p1_name), ("p2", body.p2_name)) if v.strip()}
    blob = live.create(reg, body.name, "", p2, version, p1_sheet=p1, best_of=_best_of(body.best_of), names=names)
    return battle_view(blob["id"], reg.id)


@app.get("/api/battles/{battle_id}")
def battle_view(battle_id: str, regulation: str = "reg_mc", wp: bool = True) -> dict[str, Any]:
    reg = _reg(regulation)
    blob, b = _battle(reg, battle_id)
    return _view(reg, blob, b, with_wp=wp)


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
    live.after_edit(reg, blob, b.rp.state.ended)
    return _view(reg, blob, b)


@app.post("/api/battles/{battle_id}/preview")
def preview_entries(battle_id: str, body: Entries) -> dict[str, Any]:
    """What these entries would lead to, without logging them: the questions they would leave open
    and any that make no sense. The page uses it to show the consequences of an answer (the seeds a
    terrain cues) before you confirm the lot, so nothing is written until you do."""
    from vgc.battle import entry

    reg = _reg(body.regulation)
    blob, b = _battle(reg, battle_id)
    rp = entry.replay(reg, blob["setup"], b.journal + list(body.entries))
    first = len(b.journal)
    errors = []
    for err in rp.errors:
        at = int(err.split()[1]) if err.startswith("entry ") else -1
        if at >= first:
            errors.append({"index": at - first, "message": err})
    return {"questions": [q.to_json() for q in rp.questions], "errors": errors}


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
    live.after_edit(reg, blob, b.rp.state.ended)
    return _view(reg, blob, b)


@app.post("/api/battles/{battle_id}/next")
def next_game(battle_id: str, regulation: str = "reg_mc") -> dict[str, Any]:
    """The next game of this battle's series, made if it is not there yet: the same two teams,
    starting out knowing what the games so far showed. Ending a game of a Bo3 already does this;
    this is for a battle started as a single game that turned out to be game 1."""
    reg = _reg(regulation)
    blob, _ = _battle(reg, battle_id)
    try:
        made = live.next_game(reg, blob)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return battle_view(made["id"], reg.id)


@app.get("/api/battles/{battle_id}/at/{index}")
def battle_at(battle_id: str, index: int, regulation: str = "reg_mc") -> dict[str, Any]:
    """The battle as it stood after `index` taps — replaying a prefix, which is the same
    operation as undo without throwing anything away."""
    from vgc.battle import entry

    reg = _reg(regulation)
    blob, b = _battle(reg, battle_id)
    index = max(0, min(index, len(b.journal)))
    prefix = entry.Battle(reg, blob["setup"], b.journal[:index])
    # A prefix that stops at the next turn's tap is a turn that is over, so its own end is where
    # the number is taken, as the trajectory takes it there.
    closed = index < len(b.journal) and b.journal[index].get("kind") == "turn"
    view = _view(reg, blob, prefix, closed=closed)
    return view | {"at": index, "entries_total": len(b.journal)}


@app.get("/api/battles/{battle_id}/trajectory")
def battle_trajectory(battle_id: str, regulation: str = "reg_mc") -> dict[str, Any]:
    """WP at every turn mark — the walk-back through a finished game.

    One row per turn, not per tap: inside a turn the state passes through partial information (a
    move logged before its damage), and a curve drawn over that measures data entry, not the game.
    """
    reg = _reg(regulation)
    blob, b = _battle(reg, battle_id)
    version = _version(reg.id, blob)
    if not version:
        raise HTTPException(400, "no WP model is registered for this regulation")
    return {"version": version, "gates": gate_summary(version),
            "turns": live.trajectory(reg, b, version)}


@app.get("/api/battles/{battle_id}/solve")
def battle_solve(battle_id: str, regulation: str = "reg_mc") -> dict[str, Any]:
    """The engine's answer to the 1v1 this battle is in, as deep as it has been searched so far.

    The first call for a position starts the search in the background and returns at once; the
    page asks again to see it deepen. Beside the model's number, never instead of it: the engine
    assumes best play on both sides. On 192 held-out human 1v1s it predicted who won better than the
    model did (PLAN-v3, steps 3.6 and 8).
    """
    from vgc.web import solving

    reg = _reg(regulation)
    _, b = _battle(reg, battle_id)
    # Asked where the model's number is taken: a search from inside a turn would have every
    # Pokémon choose again (`live.decision_point`).
    point, kind, as_of = live.at_decision_point(b)
    return solving.request(reg, battle_id, point, kind) | {"as_of": as_of}


@app.delete("/api/battles/{battle_id}")
def delete_battle(battle_id: str, regulation: str = "reg_mc") -> dict[str, Any]:
    from vgc.web import solving

    reg = _reg(regulation)
    solving.cancel(battle_id)
    blob = live.load(reg.id, battle_id)
    if blob is None or not live.remove(reg.id, battle_id):
        raise HTTPException(404, f"no battle {battle_id!r}")
    # The games after it in its series no longer carry what it showed.
    live.refresh_series(reg, blob)
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
