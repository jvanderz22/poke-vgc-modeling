"""The live battle: a journal of taps on disk, and everything the screen needs to show a turn.

`vgc.battle.entry` is the model — a journal, and the state that replaying it produces. This is the
service around it: where a battle is kept, what one turn's screen needs, and what the numbers on
that screen are allowed to claim.

Three things are stated here because the UI has to repeat them, and a number that arrives without
its caveat gets believed:

* **The WP models are open-sheet models.** Every row they were trained on had the opponent's item,
  ability, moves and spread visible — the known-flags are 1.000 across all 433,052 of them. A
  Team Preview Only battle is not that, so `wp` is computed against the opponent's *most common
  real set per species* (`vgc.web.prior`), which keeps the input in the distribution the model
  knows. That makes it a point estimate over one guess rather than an expectation over what they
  might be holding, and `wp_open` reports the same position with their unknowns left unknown so
  the size of the guess is visible instead of hidden. Phase 8 step 2 is what turns the first into
  the second.
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
                    "theirs": [m["species"] for m in blob["setup"]["theirs"]],
                    "result": blob.get("result")})
    out.sort(key=lambda b: b["updated"], reverse=True)
    return out[:limit]


def remove(reg_id: str, battle_id: str) -> bool:
    p = _path(reg_id, battle_id)
    if not p.exists():
        return False
    p.unlink()
    return True


def create(reg: Regulation, name: str, my_team: str, theirs: list[dict[str, Any]],
           version: str | None) -> dict[str, Any]:
    now = dt.datetime.now().isoformat(timespec="seconds")
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
    """What you can tap next for one side: each active's moves, and who is left to switch to.

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
                       "category": (reg.dex.get_move(mv) or {}).get("category")}
                      for mv in moves],
            "moves_known": bool(m.moves)})
    out["bench"] = [{"species": m.species, "hp": round(m.hp, 4), "state": m.state}
                    for m in state.bench(side)]
    return out


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
    known = {(mine, m.species): m.sp["spe"] for m in state.sides[mine].mons if m.sp}
    speeds = speed_channel.infer(reg, state, known)
    them = "p2" if mine == "p1" else "p1"
    out, contradictions = {}, []
    for m in state.sides[them].mons:
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

def _filled(reg: Regulation, obs: dict[str, Any]) -> dict[str, Any]:
    """A copy of the observation with the opponent's unknowns filled from usage.

    The models were trained with the opponent fully visible, so a closed-sheet observation is not
    a position they have ever been shown. Filling it keeps the input in distribution at the price
    of stating one guess as fact — a Choice Scarf guessed as an Assault Vest is wrong rather than
    uncertain. The copy is what the model sees; nothing written here can reach the journal or any
    belief channel.
    """
    from vgc.teams.sets import calc_stats
    from vgc.web.prior import fill

    out = json.loads(json.dumps(obs))
    them = "p2" if obs["perspective"] == "p1" else "p1"
    guessed = []
    for m in out["sides"][them]["mons"]:
        try:
            guess, note = fill(m["species"], reg)
        except ValueError:
            continue
        changed = []
        if m["item"] is None:
            m["item"], m["item_source"] = to_id(guess.item or ""), "guess"
            changed.append("item")
        if m["ability"] is None:
            m["ability"], m["ability_source"] = to_id(guess.ability or ""), "guess"
            changed.append("ability")
        if not m["moves"]:
            m["moves"] = [to_id(x) for x in guess.moves][:4]
            changed.append("moves")
        if not m["nature"]:
            m["nature"] = guess.nature
            changed.append("nature")
        if not m["stats"]:
            try:
                m["stats"] = calc_stats(guess, reg.dex, reg)
                changed.append("spread")
            except Exception:
                pass
        if changed:
            guessed.append({"species": m["species"], "filled": changed,
                            "source": note["source"], "share": note["share"]})
    out["sides"][them]["sheet"] = True
    return {"obs": out, "guessed": guessed}


def wp(reg: Regulation, state, version: str) -> dict[str, Any]:
    """P(you win) from here, with what had to be assumed to say it.

    Two numbers, deliberately. `wp` fills the opponent's unknowns from usage, which is the regime
    the model was trained in and the same convention `/api/preview` already uses. `wp_open` asks
    the same question with their unknowns left unknown, which is the true position and a kind of
    row the model never saw. When the two disagree, the gap is the size of the guess — and that is
    worth showing rather than picking one and being confident.
    """
    from vgc.wp.tools import _load, _record
    from vgc.wp.features import featurize

    model, fz = _load(reg, version)
    obs = state.observation()
    kind = "preview" if not state.started else "turn"
    filled = _filled(reg, obs)
    recs = [_record(filled["obs"], kind, "human"), _record(obs, kind, "human")]
    d = featurize(recs, fz)
    p, _ = model.predict(d)
    return {"version": version, "wp": float(p[0]), "wp_open": float(p[1]),
            "kind": kind, "guessed": filled["guessed"],
            "regime": ("Their sets are guessed from usage — the models are open-sheet models and "
                       "have never been shown a position where the opponent is unknown.")}


def trajectory(reg: Regulation, battle: entry.Battle, version: str) -> list[dict[str, Any]]:
    """WP at every turn mark of the battle so far — the walk-back the UI steps through.

    One row per turn rather than per tap: within a turn the state churns through partial
    information (a move logged before its damage) and a curve drawn over that measures data entry,
    not the game.
    """
    from vgc.wp.tools import _load, _record
    from vgc.wp.features import featurize

    marks = [(i, e["n"]) for i, e in enumerate(battle.journal) if e.get("kind") == "turn"]
    marks.append((len(battle.journal), battle.rp.state.turn))
    rows, recs = [], []
    for index, turn in marks:
        rp = battle.at(index)
        obs = rp.state.observation()
        rows.append({"index": index, "turn": turn,
                     "left": {sid: sum(m.state != "fainted" for m in rp.state.sides[sid].mons[:4])
                              for sid in ("p1", "p2")},
                     "active": {sid: [m.species for m in rp.state.sides[sid].mons if m.state == "active"]
                                for sid in ("p1", "p2")}})
        recs.append(_record(_filled(reg, obs)["obs"], "preview" if not rp.state.started else "turn",
                            "human"))
    if not recs:
        return []
    model, fz = _load(reg, version)
    p, _ = model.predict(featurize(recs, fz))
    for row, q in zip(rows, p):
        row["wp"] = float(q)
    return rows


def view(reg: Regulation, blob: dict[str, Any], battle: entry.Battle, *,
         version: str | None = None, with_wp: bool = True) -> dict[str, Any]:
    """Everything one screen needs: the field, what you can tap, what is still being asked, what
    the Speed bound says, and the number — in that order of trustworthiness."""
    state = battle.rp.state
    mine, theirs = state.perspective, "p2" if state.perspective == "p1" else "p1"
    belief, contradictions = beliefs(reg, state)
    out: dict[str, Any] = {
        "id": blob["id"], "name": blob.get("name", ""), "turn": state.turn,
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
        "errors": battle.rp.errors,
        "speed": speed_read(reg, state, belief),
        "beliefs": [b.to_json() for k, b in sorted(belief.items()) if k[0] == theirs],
        "contradictions": contradictions,
    }
    if with_wp and version:
        try:
            out["wp"] = wp(reg, state, version)
        except Exception as e:                  # a missing model must not take the screen down
            out["wp"] = {"error": str(e)}
    return out
