"""Positions from a player's view, with a bench (PLAN-policy, stage 2).

What the policy searches is built from what the player can see, never from the battle being played:
the player's own channel and requests (an `Observer` for its side), its own six as built, and the
opponent's open sheet. In self-play the runner's battle is never read, so nothing hidden leaks in.

A position has every Pokémon left on a side: the two on the field, then the back. What is hidden is
guessed, and each guess is one position:

  which are in the back   preview shows six. Until a Pokémon comes out, which of the unseen were
                          brought is unknown; each way to fill the back is weighed by how often each
                          species is brought when on a sheet (`bring_rates.json`, training games)
  the opponent's spreads  an open sheet hides the Stat Points (`vgc.wp.doubles.speed_classes`): the
                          move order of the four on the field, drawn from the Speed prior and
                          weighed by every turn order seen, each Pokémon in the back at its
                          commonest investment. One you have not seen takes its prior's commonest

The heaviest `positions` of (back, move order) are solved and the rest is reported as unsolved, as
`vgc.wp.doubles.plan` does for two or fewer a side. Open sheets only: the policy's gates and Phase 6
are played with them.
"""

from __future__ import annotations

import itertools
import json
from functools import lru_cache
from typing import Any

from vgc import paths
from vgc.regulation import Regulation, to_id
from vgc.wp import doubles, endgame, solver

BRING_RATES = paths.DATA / "analysis" / "reg_mc" / "bring_rates.json"
# Sides behind a rate before it moves off the base rate: a species seen on a dozen sheets says little.
BRING_PRIOR_SIDES = 20
# Positions a decision solves (the plan's D).
POSITIONS = 2
# The policy's search at the design point (PLAN-policy, stage 1): one turn, two choices a Pokémon,
# K a side, four draws, replacements searched, the blended race at the horizon.
SEARCH = {"depth": 1, "prune": 2, "side_k": 6, "sample": 4, "sample_only": True, "ko_extend": False,
          "race": True, "race_doubles": "blend_boosts", "fast_race": True, "fast_dice": True,
          "switches": True, "mega": True}


@lru_cache(maxsize=1)
def _rates() -> dict[str, Any]:
    return json.loads(BRING_RATES.read_text())


def bring_rate(species: str) -> float:
    """P(brought | on the sheet), shrunk towards the base rate (two in three) by a prior of
    `BRING_PRIOR_SIDES` sides."""
    r = _rates()
    s = r["species"].get(species) or {"shown": 0, "brought": 0}
    return (s["brought"] + BRING_PRIOR_SIDES * r["base"]) / (s["shown"] + BRING_PRIOR_SIDES)


# --- the view ----------------------------------------------------------------------------------

class PlayerView:
    """One player's battle as it is played: its channel's lines (`feed`) and its requests
    (`request`) into an `Observer` for its side, with the journal `vgc.wp.doubles` reads (arrivals,
    moves, turn marks), and its own six as built (`from_team`)."""

    def __init__(self, reg: Regulation, perspective: str, team_text: str):
        from vgc.battle.entry import from_team
        from vgc.data.observe import Observer

        self.reg, self.perspective = reg, perspective
        self.o = Observer(perspective, reg.dex)
        self.rp = type("Viewed", (), {"state": self.o})()
        self.journal: list[dict[str, Any]] = []
        self.mine = from_team(reg, team_text)
        self.setup = {"perspective": perspective, "mine": self.mine, "theirs": []}

    def feed(self, lines: list[str]) -> None:
        for line in lines:
            self.o.feed(line)
            parts = line.split("|")
            kind = parts[1] if len(parts) > 1 else ""
            if kind in ("switch", "drag") and len(parts) > 2:
                m = self.o._mon(parts[2])
                if m is not None:
                    self.journal.append({"kind": "switch", "side": parts[2][:2], "species": m.species})
            elif kind == "move" and len(parts) > 3:
                m = self.o._mon(parts[2])
                self.journal.append({"kind": "move", "side": parts[2][:2], "species": m.species if m else None,
                                     "move": parts[3]})
            elif kind == "turn":
                self.journal.append({"kind": "turn", "n": int(parts[2])})

    def request(self, req: dict[str, Any]) -> None:
        self.o.request(req)


# --- the positions -------------------------------------------------------------------------------

def _left(state, sid: str) -> list[Any]:
    return [m for m in state.sides[sid].mons if m.state in ("active", "bench")]


def _own_set(reg: Regulation, view: PlayerView, m: Any) -> dict[str, Any]:
    e = next((x for x in view.mine if to_id(x["species"]) == to_id(m.species)), None)
    if e is None:
        e = next(x for x in view.mine if to_id(m.species).startswith(to_id(x["species"]))
                 or to_id(x["species"]).startswith(to_id(m.species)))
    return {"species": e["species"], **{k: e.get(k) for k in ("item", "ability", "nature")},
            "moves": list(e.get("moves") or []), "sp": {k: v for k, v in (e.get("sp") or {}).items() if v}}


def _sheet_set(reg: Regulation, m: Any) -> dict[str, Any]:
    """An open sheet's set as shown: an item consumed or knocked off since is still the one brought."""
    item = m.item or m.lost_item
    return {"species": m.species, "item": (reg.dex.get_item(item) or {}).get("name", item) if item else None,
            "ability": m.ability, "nature": m.nature,
            "moves": [(reg.dex.get_move(x) or {}).get("name", x) for x in m.moves]}


def reason(reg: Regulation, view: PlayerView) -> str | None:
    """Why no position is built for this decision, or None."""
    state = view.o
    me = view.perspective
    them = "p2" if me == "p1" else "p1"
    if not state.started or state.ended:
        return "the battle is not in progress"
    if not state.sides[them].sheet:
        return "their sheet is closed"
    for sid in ("p1", "p2"):
        on = doubles.actives(state, sid)
        left = reg.bring - sum(m.state == "fainted" for m in state.sides[sid].mons)
        if left > len(on) and len(on) < 2:
            return "a slot is waiting for a replacement"
        for m in on:
            if m.volatiles:
                return f"{m.species} has {', '.join(sorted(m.volatiles))}, which the solver cannot set up"
        for m in _left(state, sid):
            if m.status and m.status not in endgame.STATUSES:
                return f"{m.species} is {m.status}, whose turn counter is not shown"
    return None


def _unseen(reg: Regulation, state, sid: str) -> tuple[list[Any], int]:
    """A side's sheets not yet seen, and how many of them are in its back."""
    seen = sum(m.state in ("active", "bench", "fainted") for m in state.sides[sid].mons)
    unseen = [m for m in state.sides[sid].mons if m.state == "unrevealed"]
    return unseen, max(0, min(len(unseen), reg.bring - seen))


def backs(reg: Regulation, state, sid: str) -> list[tuple[tuple[Any, ...], float]]:
    """Every way to fill a side's back from its unseen sheets, with its weight: each species in it
    brought at its rate, each left out not, normalized."""
    unseen, n = _unseen(reg, state, sid)
    out = []
    for combo in itertools.combinations(unseen, n):
        w = 1.0
        for m in unseen:
            r = bring_rate(m.species)
            w *= r if m in combo else 1 - r
        out.append((combo, w))
    total = sum(w for _, w in out) or 1.0
    return sorted(((c, w / total) for c, w in out), key=lambda cw: -cw[1])


def _commonest_speed(reg: Regulation, s: dict[str, Any]) -> int:
    p = endgame._prior(reg, s)
    return max(range(len(p)), key=lambda i: p[i])


def compose(reg: Regulation, side_sets: dict[str, list[dict[str, Any]]], f: dict[str, Any],
            search: dict[str, Any]) -> dict[str, Any]:
    """One solver input: each side's Pokémon on the field in slot order, then its back, then fainted
    fillers to four."""
    used = {s["species"] for ss in side_sets.values() for s in ss}
    texts = {}
    for sid, ss in side_sets.items():
        fill = [x for x in solver.FILLERS if x not in used][:reg.bring - len(ss)]
        filler_text = [f"{x}\nAbility: {solver._ability(reg, x)}\n- Protect" for x in fill]
        texts[sid] = "\n\n".join([solver._set_text(s) for s in ss] + filler_text)
    setup = {k: v for k, v in f.items() if k not in ("active", "bench")}
    return {"format": reg.showdown_format, "active": f["active"], "bench": f["bench"],
            "p1": texts["p1"], "p2": texts["p2"], "setup": solver._pruned(setup), "search": search}


def plan(reg: Regulation, view: PlayerView, search: dict[str, Any] | None = None,
         positions: int = POSITIONS, seed: int = 0) -> dict[str, Any]:
    """The positions one decision solves, each `{weight, back, order, position}`, heaviest first,
    with the weight left unsolved; or why there are none."""
    search = {**SEARCH, **(search or {})}
    why = reason(reg, view)
    if why:
        return {"eligible": False, "reason": why}
    state = view.o
    me = view.perspective
    them = "p2" if me == "p1" else "p1"
    notes: list[str] = []

    on = {sid: doubles.actives(state, sid) for sid in ("p1", "p2")}
    seen_back = {sid: [m for m in state.sides[sid].mons if m.state == "bench"] for sid in ("p1", "p2")}
    mine = [_own_set(reg, view, m) for m in on[me] + seen_back[me]]
    theirs_seen = [_sheet_set(reg, m) for m in on[them] + seen_back[them]]
    for sets_, mons in ((mine, on[me] + seen_back[me]), (theirs_seen, on[them] + seen_back[them])):
        for s, m in zip(sets_, mons):
            s["mega"] = m.forme if m.mega else None
    if any(endgame.UNSOLVABLE_MOVES & {to_id(x) for x in s.get("moves") or []} for s in mine + theirs_seen):
        return {"eligible": False, "reason": "Revival Blessing, which would bring back a Pokémon the solver does not have"}

    # The move orders on the field, read once: what is in the back moves nothing this turn, and one
    # not seen yet has no turn order to read.
    seen_sets = {me: mine, them: theirs_seen}
    seen_mons = {me: on[me] + seen_back[me], them: on[them] + seen_back[them]}
    f0 = doubles.facts(reg, view, seen_sets, mons=seen_mons)
    orders, unsolved_speed, contradicted, _ = doubles.speed_classes(
        reg, view, seen_sets, f0, seed=seed, ordered={sid: len(on[sid]) for sid in ("p1", "p2")})
    if contradicted:
        notes.append("no set of Speed investments fits the turn order logged, so all of them are counted")
    if not orders:
        return {"eligible": False, "reason": "no Speed order to solve"}

    jobs = []
    for back, w_back in backs(reg, state, them):
        extra = [_sheet_set(reg, m) for m in back]
        for s in extra:
            s["mega"] = None
            s["sp"] = solver.spread(reg, s, _commonest_speed(reg, s))
        sets_ = {me: mine, them: theirs_seen + extra}
        mons = {me: seen_mons[me], them: seen_mons[them] + list(back)}
        f = doubles.facts(reg, view, sets_, mons=mons)
        f["bench"] = {sid: len(mons[sid]) - len(on[sid]) for sid in ("p1", "p2")}
        used = {sid: any(m.mega for m in state.sides[sid].mons) for sid in ("p1", "p2")}
        f["megaUsed"] = {sid: True for sid, u in used.items() if u}
        for o in orders:
            sides = {}
            for sid in ("p1", "p2"):
                sides[sid] = [{**s, "sp": sp} for s, sp in zip(sets_[sid], o["sp"][sid])] + \
                    [s for s in sets_[sid][len(o["sp"][sid]):]]
            jobs.append({"weight": w_back * o["weight"], "back": [m.species for m in back], "order": o["order"],
                         "position": compose(reg, sides, f, search)})
    jobs.sort(key=lambda j: -j["weight"])
    total = sum(j["weight"] for j in jobs)
    kept = jobs[:positions]
    covered = sum(j["weight"] for j in kept)
    return {"eligible": True, "reason": None,
            "kind": f"{len(mine)}v{len(theirs_seen) + _unseen(reg, state, them)[1]}",
            "jobs": [{**j, "weight": j["weight"] / covered} for j in kept],
            "unsolved": round(1 - covered / total * (1 - unsolved_speed), 4) if total else 1.0,
            "considered": len(jobs), "notes": notes}


def combine(jobs: list[dict[str, Any]], results: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    return endgame.combine(jobs, results)
