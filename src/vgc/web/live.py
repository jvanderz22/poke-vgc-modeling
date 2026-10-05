"""The live battle: a journal of taps on disk, and everything the screen needs to show a turn.

`vgc.battle.entry` is the model — a journal, and the state that replaying it produces. This is the
service around it: where a battle is kept, what one turn's screen needs, and what the numbers on
that screen are allowed to claim.

Three things are stated here because the UI has to repeat them, and a number that arrives without
its caveat gets believed:

* **The WP number is an average, and its model follows the regime.** The opponent's unknowns are
  filled by `k` complete sets drawn from `vgc.belief.sets` and the model is asked about each, so
  the number is an expectation over what they might be holding and the band is what that is
  worth. Which model answers is pinned per regime in `models/served.json`, because open- and
  closed-sheet play are gated separately and the best model for one is not the best for the other.
* **The Speed read is a bound, not a probability.** `vgc.belief.speed` narrows what an opponent's
  Speed investment could be; what the screen wants is whether you move first, so the bound is
  turned into one of *faster / slower / not yet decided*, and the third is reported as often as it
  is true. Where their nature is unknown the range spans it, because a Timid version of the same
  investment is a different Pokémon to be outrun.
* **Nothing here writes to the battle state.** The guessed sets fill a copy of the observation on
  its way to the model and never reach the journal, so no belief channel can ever read a guess as
  though it were something you saw.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

from vgc import paths
from vgc.battle import entry
from vgc.regulation import Regulation, to_id

BATTLES = paths.DATA / "battles"

# What a Pokémon's Speed can be multiplied by without anything having been announced. A nature is
# the one the app cannot see on an opposing sheet in any regime, so an unknown one widens the
# range rather than being assumed neutral.
NATURE_SPAN = (0.9, 1.1)


# --- storage ------------------------------------------------------------------------------

def _dir(reg_id: str):
    return BATTLES / reg_id


def _path(reg_id: str, battle_id: str):
    return _dir(reg_id) / f"{battle_id}.json"


def save(reg_id: str, blob: dict[str, Any]) -> dict[str, Any]:
    """One file per battle. A journal is a few kilobytes and stays readable by hand, which is what
    you want the one time something goes wrong in the middle of a game."""
    blob["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    d = _dir(reg_id)
    d.mkdir(parents=True, exist_ok=True)
    _path(reg_id, blob["id"]).write_text(json.dumps(blob, indent=1) + "\n")
    return blob


def load(reg_id: str, battle_id: str) -> dict[str, Any] | None:
    p = _path(reg_id, battle_id)
    return json.loads(p.read_text()) if p.exists() else None


def listing(reg_id: str, limit: int = 50) -> list[dict[str, Any]]:
    out = []
    for p in sorted(_dir(reg_id).glob("*.json"), reverse=True):
        try:
            blob = json.loads(p.read_text())
        except Exception:
            continue
        out.append({"id": blob["id"], "name": blob.get("name", ""),
                    "created": blob.get("created", ""), "updated": blob.get("updated", ""),
                    "turn": blob.get("turn", 0), "entries": len(blob.get("journal", [])),
                    "theirs": [m["species"] for m in entry.side_entries(blob["setup"], _other(blob["setup"]))],
                    "sheets": sheets(blob), "perspective": perspective(blob), "result": blob.get("result")})
    out.sort(key=lambda b: b["updated"], reverse=True)
    return out[:limit]


def remove(reg_id: str, battle_id: str) -> bool:
    p = _path(reg_id, battle_id)
    if not p.exists():
        return False
    p.unlink()
    return True


def sheets(blob: dict[str, Any]) -> str:
    """Which information regime this battle is played in: one of `vgc.wp.models.SHEETS`.

    Read off the setup rather than stored beside it, so a battle saved before the regime was
    recorded answers the same way as one saved after: an open sheet is the one that came with
    their abilities filled in, which is also what `entry` reads it as.
    """
    from vgc.wp.models import CLOSED, OPEN

    theirs = entry.side_entries(blob["setup"], _other(blob["setup"]))
    return OPEN if any(m.get("ability") is not None for m in theirs) else CLOSED


def perspective(blob: dict[str, Any]) -> str:
    """Whose seat the battle is seen from: `p1` for your own game, `spectator` for someone else's
    watched with both sheets open (observer mode, PLAN-v3 step 4)."""
    return blob["setup"].get("perspective", "p1")


def _other(setup: dict[str, Any]) -> str:
    """The side a battle's regime is read off: the opponent, or player 2 when watching."""
    return "p1" if setup.get("perspective", "p1") == "p2" else "p2"


def create(reg: Regulation, name: str, my_team: str, theirs: list[dict[str, Any]],
           version: str | None, p1_sheet: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A battle of yours, or with `p1_sheet`, someone else's: two open sheets watched from outside,
    neither side's Stat Points known."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    if p1_sheet is not None:
        setup = {"perspective": "spectator", "p1": p1_sheet, "p2": theirs}
    else:
        setup = {"perspective": "p1", "mine": entry.from_team(reg, my_team), "theirs": theirs}
    return save(reg.id, {"id": uuid.uuid4().hex[:12], "name": name or "Battle",
                         "regulation": reg.id, "version": version, "created": now,
                         "my_team": my_team, "setup": setup, "journal": [], "turn": 0,
                         "result": None})


# --- the screen ----------------------------------------------------------------------------

def mon_view(state, m, own: bool) -> dict[str, Any]:
    """One Pokémon as the screen shows it. `hp_exact` only for your own side, because that is all
    the cartridge gives you: real numbers for yours and a percentage for theirs."""
    return {
        "species": m.species, "forme": m.forme, "state": m.state, "slot": m.position,
        "hp": round(m.hp, 4), "hp_max": m.hp_max if own else None,
        "hp_exact": round(m.hp * m.hp_max) if own and m.hp_max else None,
        "status": m.status, "boosts": {k: v for k, v in sorted(m.boosts.items()) if v},
        "volatiles": sorted(m.volatiles), "mega": m.mega,
        "item": m.item, "item_source": m.item_source, "lost_item": m.lost_item,
        "ability": m.ability, "ability_source": m.ability_source,
        "moves": list(m.moves), "moves_used": list(m.moves_used),
        "nature": m.nature, "stats": m.stats,
        "ability_unknown": sorted(m.ability_ruled_out) if m.ability is None else [],
    }


def menu(reg: Regulation, state, side: str) -> dict[str, Any]:
    """What you can tap next for one side: each active's moves, and who is left to switch to
    (`entry.sendable`: once all four a side brought have been seen, the other two are not offered).

    An opponent's moves are the ones you have *seen*, which on turn 1 is nothing — so the move
    picker has to accept a name that is not on any list, and says so by returning `moves_known`.
    """
    out: dict[str, Any] = {"actives": [], "bench": []}
    for slot in (0, 1):
        m = state.at(side, slot)
        if m is None:
            out["actives"].append(None)
            continue
        moves = m.moves or m.moves_used
        out["actives"].append({
            "slot": slot, "species": m.species,
            "moves": [{"id": mv, "name": (reg.dex.get_move(mv) or {}).get("name", mv),
                       "target": (reg.dex.get_move(mv) or {}).get("target"),
                       "category": (reg.dex.get_move(mv) or {}).get("category"), **move_effects(mv)}
                      for mv in moves],
            "moves_known": bool(m.moves),
            "megas": megas(reg, state, side, m),
            **({} if m.moves else unseen_moves(reg, m))})
    out["bench"] = [{"species": m.species, "hp": round(m.hp, 4), "state": m.state}
                    for m in entry.sendable(reg, state, side)]
    return out


STATUS_WORDS = {"brn": "burned", "par": "paralysed", "psn": "poisoned", "tox": "badly poisoned",
                "slp": "asleep", "frz": "frozen"}


def move_effects(move: str) -> dict[str, Any]:
    """What follows from a move on its own (`follows`), and what might (`chance`): the page says
    the first so it is not entered twice, and offers the second as something to tick."""
    from vgc.battle import rules

    def stats(b: dict[str, int]) -> str:
        return ", ".join(f"{k} {v:+d}" for k, v in b.items())

    follows, chance = [], []
    if move in rules.MOVE_SELF:
        follows.append(f"user {stats(rules.MOVE_SELF[move])}")
    if move in rules.MOVE_TARGET:
        follows.append(f"target {stats(rules.MOVE_TARGET[move])}")
    for sec in rules.MOVE_SECONDARY.get(move, []):
        on = "self" if "self" in sec else "target"
        what = stats(sec[on]) if on in sec else STATUS_WORDS.get(sec.get("status", ""), sec.get("status", ""))
        if sec["chance"] == 100:
            follows.append(f"{'user' if on == 'self' else 'target'} {what}")
        elif on == "target":
            chance.append({"label": what, "chance": sec["chance"]})
    return {"follows": follows, "chance": chance}


def unseen_moves(reg: Regulation, m) -> dict[str, Any]:
    """For a Pokémon whose moves the sheet does not show: until all four have been used, the six it
    most likely has and has not used yet (`likely`, with the share of the sets still possible that
    run each); and every move it can legally have (`legal`), so a typed move is picked from a list
    rather than guessed at."""
    from vgc.belief import sets as set_belief

    def option(mv: str, share: float | None = None) -> dict[str, Any]:
        d = reg.dex.get_move(mv) or {}
        out = {"id": mv, "name": d.get("name", mv), "target": d.get("target"), "category": d.get("category"),
               **move_effects(mv)}
        return out | ({"share": round(share, 3)} if share is not None else {})

    used = set(m.moves_used)
    try:
        belief = set_belief.given(reg, m)
        # Only while the four are not all known: the six most run among the sets still possible.
        likely = ([option(mv, p) for mv, p in belief.moves(top=12) if mv not in used][:6]
                  if len(used) < 4 else [])
        legal = belief.legal_moves
    except Exception:                    # no corpus built: the dex still knows what is legal
        likely, legal = [], set_belief.legal_moves(reg, m.forme)
    return {"likely": likely, "legal": [option(mv) for mv in legal]}


def megas(reg: Regulation, state, side: str, m) -> list[dict[str, str]]:
    """The Mega formes this Pokémon could become this turn, with the stone each needs.

    One Mega Evolution a side, so once any of the side has evolved the list is empty. An item
    nobody has seen could be any stone for this species, so each one is offered: Charizard is
    two answers until its item shows, and picking one is also the reveal.
    """
    if not reg.mega or m.mega or any(x.mega for x in state.sides[side].mons):
        return []
    base = (reg.dex.get_species(m.species) or {}).get("name", m.species)
    stones = ([reg.dex.get_item(m.item)] if m.item else
              [] if m.item == "" else list(reg.dex.items.values()))
    return [{"forme": stone["megaStone"][base], "item": stone["name"]}
            for stone in stones if stone and base in (stone.get("megaStone") or {})]


def speed_read(reg: Regulation, state, beliefs: dict) -> list[dict[str, Any]]:
    """Who moves first, for every pair of Pokémon currently on the field.

    The answer is a trichotomy and the third branch is the honest one: *faster*, *slower*, or
    *not yet decided*. A bound that has not separated the two is reported as undecided rather
    than resolved to whichever end happens to be likelier, because the cost of being told
    "you outspeed" and being wrong is a lost game and the cost of "not yet" is nothing.
    """
    from vgc.belief import speed as speed_channel

    def band(m) -> tuple[float, float] | None:
        """The Speed this Pokémon could have, at its widest given what is known."""
        stat = (m.stats or {}).get("spe")
        if stat:
            return float(stat), float(stat)
        belief = beliefs.get((state._side_of(m), m.species))
        base = speed_channel.base_speed(reg, m.forme)
        if base is None:
            return None
        lo_sp, hi_sp = 0, reg.sp_per_stat_cap
        if belief is not None and belief.bounds().get("spe"):
            lo_sp, hi_sp = belief.bounds()["spe"]
        raw_lo, raw_hi = base + lo_sp + 20, base + hi_sp + 20
        if m.nature:
            nat = reg.dex.get_nature(m.nature) or {}
            mult = 1.1 if nat.get("plus") == "spe" else 0.9 if nat.get("minus") == "spe" else 1.0
            return raw_lo * mult, raw_hi * mult
        # Nature is hidden on an open sheet as surely as on a closed one, so it widens the band.
        return raw_lo * NATURE_SPAN[0], raw_hi * NATURE_SPAN[1]

    trick_room = "trickroom" in state.pseudo
    out = []
    for slot in (0, 1):
        mine = state.at("p1", slot)
        if mine is None:
            continue
        for other in (0, 1):
            theirs = state.at("p2", other)
            if theirs is None:
                continue
            a, b = band(mine), band(theirs)
            if a is None or b is None:
                verdict = "unknown"
            elif a[0] > b[1]:
                verdict = "slower" if trick_room else "faster"
            elif a[1] < b[0]:
                verdict = "faster" if trick_room else "slower"
            else:
                verdict = "undecided"
            out.append({"mine": mine.species, "my_slot": slot, "theirs": theirs.species,
                        "their_slot": other, "verdict": verdict, "trick_room": trick_room,
                        "my_speed": [round(x) for x in a] if a else None,
                        "their_speed": [round(x) for x in b] if b else None})
    return out


def beliefs(reg: Regulation, state) -> tuple[dict, list[dict[str, Any]]]:
    """Each opposing Pokémon's 66-point allocation, from the channels the app can afford per turn.

    Only the turn-order channel runs live. The damage and bulk channels each need a sweep through
    the `@smogon/calc` sidecar per observation, which is right for an offline gate over thousands
    of battles and wrong between two taps in a game on a clock.

    Your own side is passed in as **Stat Points, not stats** — the distinction the channel is
    built on. A stat is only true of one forme, so a Pokémon that Mega Evolves mid-battle changes
    base Speed without changing its investment, and the points are what you actually know anyway
    because you chose them.
    """
    from vgc.belief import sp as sp_belief
    from vgc.belief import speed as speed_channel

    mine = state.perspective
    known = ({(mine, m.species): m.sp["spe"] for m in state.sides[mine].mons if m.sp}
             if mine in state.sides else {})
    speeds = speed_channel.infer(reg, state, known)
    out, contradictions = {}, []
    # Everyone whose spread is hidden: the opponent's six, or all twelve when watching.
    for them, m in [(sid, m) for sid in ("p1", "p2") if sid != mine for m in state.sides[sid].mons]:
        key = (them, m.species)
        out[key] = sp_belief.combine(reg, key, m.nature, m.moves or m.moves_used, speeds.get(key))
        # A contradiction is not a discovery about the opponent — they did have *some* spread — so
        # it is a proof that something logged here is wrong. The channel's own answer is to widen
        # back to the prior, which is right and silent; saying so is the other half, because the
        # fix is a tap the person has to make.
        belief = speeds.get(key)
        if belief is not None and belief.contradicted:
            contradictions.append({
                "species": m.species, "channel": "turn order",
                "note": f"no Speed investment fits the order logged for {m.species}. "
                        "Check the order you logged the arrivals or the moves in — the bound has "
                        "been widened back to nothing in the meantime."})
        if out[key].contradicted:
            contradictions.append({
                "species": m.species, "channel": out[key].contradicted,
                "note": f"two channels disagree about {m.species} under the 66-point budget."})
    return out, contradictions


# --- win probability -------------------------------------------------------------------------

def _particle(reg: Regulation, obs: dict[str, Any], state, rng: Any,
              note: dict[str, Any]) -> dict[str, Any]:
    """One complete opponent, drawn from what is still possible.

    Their item, ability, nature and moves are drawn from `vgc.belief.sets` — 15,000 other people's
    sheets, in proportion to how often each set was actually brought.

    **The spread is not filled, and that is a measurement rather than an oversight.** It would be
    the elegant thing to do: `vgc.belief.sp` bounds the 66 points from this battle's own turn
    orders, so a particle could carry a spread the opponent could really have. But an opponent's
    `stats` is `None` in *every* row any WP model was trained on — a player row carries your own
    and nobody else's, a spectator row carries none at all — so setting it flips a feature the
    model has never seen set, and hands it a number it has no weights for. Scored on held-out
    games, filling it made the answer worse. The SP belief earns its keep on screen, in the Speed
    read and the belief panel, and is kept out of the model that cannot use it.
    """
    from vgc.belief import sets as set_belief

    out = json.loads(json.dumps(obs))
    hidden = [sid for sid in ("p1", "p2") if sid != obs["perspective"]]
    for them, m in [(sid, m) for sid in hidden for m in out["sides"][sid]["mons"]]:
        mon = next((x for x in state.sides[them].mons if x.species == m["species"]), None)
        if mon is None:
            continue
        sb = set_belief.given(reg, mon)
        drawn = sb.particles(rng, 1)
        if not drawn:
            continue
        pick = drawn[0]
        if m["item"] is None:
            m["item"], m["item_source"] = pick.item, "belief"
        if m["ability"] is None:
            m["ability"], m["ability_source"] = pick.ability, "belief"
        if not m["moves"]:
            m["moves"] = list(pick.moves)
        if not m["nature"]:
            m["nature"] = pick.nature or None
        note.setdefault(m["species"], {"sets": len(sb.sheets), "off_meta": sb.off_meta,
                                       "concentration": round(sb.concentration, 3),
                                       "evidence": sb.evidence})
    for sid in hidden:
        out["sides"][sid]["sheet"] = True
    return out


def _per_record(recs: list[dict[str, Any]], fz, p) -> list[float]:
    from vgc.wp.tools import per_record

    return per_record(recs, fz, p)


def wp(reg: Regulation, state, version: str, *, k: int = 24, seed: int = 0) -> dict[str, Any]:
    """P(you win) from here, from the position as it has been shown: what their sheet or this
    battle has not revealed is left unknown.

    That is a measured choice (docs/phase8-findings.md, "which number leads on a closed sheet").
    Until 2026-09-29 the number was the average over `k` opponents drawn from the set belief, each
    a fully-known row. On 1,025 held-out closed-sheet games the position as shown beat that average
    by 0.015 nats, at every turn bucket to t5-6, at the same confidence. Since `wp-v1c` the models
    have been trained on closed-sheet rows, so a position with unknowns in it is one they know. On
    an open sheet nothing is unknown but the spread, which no model reads, so the two are the same
    number there.

    The draws are still made. Their mean is `drawn`, and their 10th-to-90th spread (`lo`, `hi`) is
    what their hidden sets are worth in this position: a wide band means what they hold decides it.
    """
    import random

    from vgc.wp.features import featurize
    from vgc.wp.tools import _load, _record

    model, fz = _load(reg, version)
    obs = state.observation()
    kind = "preview" if not state.started else "turn"
    # Seeded on the journal length, so the same position gives the same number twice running and
    # the WP does not jitter when nothing happened.
    rng = random.Random(seed)
    note: dict[str, Any] = {}
    particles = [_particle(reg, obs, state, rng, note) for _ in range(k)]
    # The orderings and the last move are the same for every draw: they are what this battle
    # showed, and a particle only fills in what it did not.
    from vgc.data.snapshots import evidence

    shown = evidence(reg, state)
    recs = [_record(p, kind, "human", evidence=shown) for p in particles] + [_record(obs, kind, "human", evidence=shown)]
    d = featurize(recs, fz)
    p, _ = model.predict(d)
    per = _per_record(recs, fz, p)
    draws = sorted(per[:k])
    mean = sum(draws) / max(len(draws), 1)
    lo = draws[max(0, int(0.1 * len(draws)) - 1)] if draws else 0.0
    hi = draws[min(len(draws) - 1, int(0.9 * len(draws)))] if draws else 0.0
    return {"version": version, "wp": per[-1], "drawn": mean, "lo": lo, "hi": hi, "k": k, "kind": kind,
            "belief": [{"species": sp} | v for sp, v in sorted(note.items())],
            "regime": ("The position as shown, with what has not been revealed left unknown. The "
                       "band is " + str(k) + " complete opponents drawn from the belief (items, "
                       "abilities, natures and moves from other players' sheets, narrowed by this "
                       "battle): what their hidden sets are worth here. Their spreads are not "
                       "filled in, because no model has been trained on an opponent's.")}


def trajectory(reg: Regulation, battle: entry.Battle, version: str, *,
               k: int = 8) -> list[dict[str, Any]]:
    """WP at every turn mark of the battle so far — the walk-back the UI steps through.

    One row per turn rather than per tap: within a turn the state churns through partial
    information (a move logged before its damage), and a curve drawn over that measures data
    entry, not the game.

    Each turn is the position as shown, as the live number is, with a band from fewer draws than
    the live one, because this is a whole game at once and a curve does not need the precision a
    decision does.
    """
    import random

    from vgc.data.snapshots import evidence
    from vgc.wp.features import featurize
    from vgc.wp.tools import _load, _record

    marks = [(i, e["n"]) for i, e in enumerate(battle.journal) if e.get("kind") == "turn"]
    marks.append((len(battle.journal), battle.rp.state.turn))
    rows, recs = [], []
    for index, turn in marks:
        rp = battle.at(index)
        obs = rp.state.observation()
        rng = random.Random(index)
        kind = "preview" if not rp.state.started else "turn"
        rows.append({"index": index, "turn": turn, "draws": k,
                     "left": {sid: sum(m.state != "fainted" for m in rp.state.sides[sid].mons[:4])
                              for sid in ("p1", "p2")},
                     "active": {sid: [m.species for m in rp.state.sides[sid].mons if m.state == "active"]
                                for sid in ("p1", "p2")}})
        shown = evidence(reg, rp.state)
        recs.append([_record(_particle(reg, obs, rp.state, rng, {}), kind, "human", evidence=shown)
                     for _ in range(k)] + [_record(obs, kind, "human", evidence=shown)])
    if not recs:
        return []
    model, fz = _load(reg, version)
    flat = [r for group in recs for r in group]
    p, _ = model.predict(featurize(flat, fz))
    per = _per_record(flat, fz, p)
    at = 0
    for row, group in zip(rows, recs):
        draws, shown_wp = per[at:at + len(group) - 1], per[at + len(group) - 1]
        at += len(group)
        row["wp"] = shown_wp
        row["lo"], row["hi"] = min(draws), max(draws)
    return rows


def _engine_reason(reg: Regulation, battle) -> str | None:
    from vgc.web import solving

    return solving.reason(reg, battle)[0]


def view(reg: Regulation, blob: dict[str, Any], battle: entry.Battle, *,
         version: str | None = None, with_wp: bool = True) -> dict[str, Any]:
    """Everything one screen needs: the field, what you can tap, what is still being asked, what
    the Speed bound says, and the number — in that order of trustworthiness."""
    from vgc.wp import endgame

    state = battle.rp.state
    mine, theirs = state.perspective, "p2" if state.perspective == "p1" else "p1"
    belief, contradictions = beliefs(reg, state)
    out: dict[str, Any] = {
        "id": blob["id"], "name": blob.get("name", ""), "sheets": sheets(blob),
        "perspective": perspective(blob), "turn": state.turn,
        "started": state.started, "ended": state.ended, "winner": state.winner,
        "entries": len(battle.journal), "journal": battle.journal,
        "sides": {sid: {"mons": [mon_view(state, m, sid == mine) for m in state.sides[sid].mons],
                        "conditions": dict(sorted(state.sides[sid].conditions.items())),
                        "sheet": state.sides[sid].sheet}
                  for sid in ("p1", "p2")},
        "field": {"weather": state.weather, "terrain": state.terrain,
                  "pseudo": dict(sorted(state.pseudo.items()))},
        "menu": {sid: menu(reg, state, sid) for sid in ("p1", "p2")},
        "questions": [q.to_json() for q in battle.rp.questions],
        "derived": battle.rp.derived,
        "turn_progress": entry.progress(battle.rp, reg),
        "end_of_turn": entry.end_of_turn(battle.rp, reg),
        "errors": battle.rp.errors,
        "speed": speed_read(reg, state, belief),
        "beliefs": [b.to_json() for k, b in sorted(belief.items()) if k[0] != mine],
        "contradictions": contradictions,
        # Whether the engine can be asked about this position (`/solve`), and if not, why.
        # A 1v1, or two or fewer a side on open sheets (`solving.reason`).
        "endgame": {"eligible": (why := _engine_reason(reg, battle)) is None, "reason": why},
    }
    if with_wp and version:
        try:
            # Seeded on how much has been logged, so the same position gives the same number
            # twice running and the bar does not jitter when nothing has happened.
            out["wp"] = wp(reg, state, version, seed=len(battle.journal))
        except Exception as e:                  # a missing model must not take the screen down
            out["wp"] = {"error": str(e)}
    return out
