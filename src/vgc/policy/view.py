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
# The policy's search at the design point (PLAN-policy, stages 1 and 3): one turn; each Pokémon's
# choices as `prune` 2 keeps them and the three a model of people ranks highest, Mega both ways; the
# side's top K pairs by that model; four draws; replacements searched; at the horizon the race with
# the back, blended as fitted on human games ('policy').
SEARCH = {"depth": 1, "prune": 2, "prune_by": "people", "people_top": 3, "side_k": 6, "sample": 4,
          "sample_only": True, "ko_extend": False, "race": True, "race_doubles": "policy", "race_bench": True,
          "fast_race": True, "fast_dice": True, "switches": True, "mega": "both"}


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

    def __init__(self, reg: Regulation, perspective: str, team_text: str | None):
        from vgc.battle.entry import from_team
        from vgc.data.observe import Observer

        self.reg, self.perspective = reg, perspective
        self.o = Observer(perspective, reg.dex)
        self.rp = type("Viewed", (), {"state": self.o})()
        self.journal: list[dict[str, Any]] = []
        # From the stands ("spectator", the human checks) there is no team of one's own.
        self.mine = from_team(reg, team_text) if team_text else []
        self.setup = {"perspective": perspective, "mine": self.mine, "theirs": []}
        self.req: dict[str, Any] | None = None

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
        self.req = req


class EntryView:
    """The page's battle (`vgc.battle.entry.Battle`, built from taps) as the policy reads one: the
    state, journal, setup and own six that `PlayerView` builds from a player's channel. The page has
    no requests, so PP and trapping are not known (`req` is None, as a log without requests gives).
    `tests/test_policy_entry.py` holds the two to the same solver positions."""

    def __init__(self, reg: Regulation, battle: Any):
        from vgc.battle.entry import side_entries

        self.reg = reg
        self.rp = battle.rp
        self.o = battle.rp.state
        self.perspective = self.o.perspective
        self.setup = battle.setup
        # A tap names a slot; the Protect counter and the rest read the Pokémon (`named_journal`).
        self.journal = battle.named_journal()
        self.mine = side_entries(battle.setup, self.perspective) if self.perspective != "spectator" else []
        self.req: dict[str, Any] | None = None

    def named_journal(self) -> list[dict[str, Any]]:
        return self.journal


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


def empty_slots(reg: Regulation, view: PlayerView) -> dict[str, list[int]]:
    """The slots waiting for a replacement at this decision (PLAN-policy stage 4): the player's as
    its request asks, and the opponent's where it has fewer than two on the field and some in the
    back, which at the player's own replacement means both lost a Pokémon in the same turn."""
    state, me = view.o, view.perspective
    them = "p2" if me == "p1" else "p1"
    out: dict[str, list[int]] = {}
    force = (view.req or {}).get("forceSwitch")
    if force:
        out[me] = [i for i, x in enumerate(force) if x]
        on = doubles.actives(state, them)
        back = reg.bring - sum(m.state == "fainted" for m in state.sides[them].mons) - len(on)
        if len(on) < 2 and back > 0:
            taken = {m.position for m in on}
            out[them] = [i for i in (0, 1) if i not in taken][:back]
    return out


def reason(reg: Regulation, view: PlayerView) -> str | None:
    """Why no position is built for this decision, or None."""
    state = view.o
    me = view.perspective
    them = "p2" if me == "p1" else "p1"
    if not state.started or state.ended:
        return "the battle is not in progress"
    if not state.sides[them].sheet:
        return "their sheet is closed"
    empty = empty_slots(reg, view)
    if me in empty:
        mons = (view.req or {}).get("side", {}).get("pokemon", [])
        for i in empty[me]:
            if i < len(mons) and not mons[i]["condition"].endswith("fnt"):
                return "a switch in the middle of a turn (U-turn, an Eject Button), which is not searched"
    for sid in ("p1", "p2"):
        on = doubles.actives(state, sid)
        left = reg.bring - sum(m.state == "fainted" for m in state.sides[sid].mons)
        if left > len(on) and len(on) < 2 and sid not in empty:
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

    empty = empty_slots(reg, view)
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
        nopp = _out_of_pp(view, mons[me])
        if nopp:
            f["nopp"] = {me: nopp}
        trapped = _trapped(view, mons[me])
        if trapped:
            f["trapped"] = {me: trapped}
        if empty:
            f = _emptied(reg, f, sets_, empty)
        for o in orders:
            sides = {}
            for sid in ("p1", "p2"):
                sides[sid] = [{**s, "sp": sp} for s, sp in zip(sets_[sid], o["sp"][sid])] + \
                    [s for s in sets_[sid][len(o["sp"][sid]):]]
                for i in empty.get(sid, []):
                    sides[sid].insert(i, _placeholder(reg, sets_))
            jobs.append({"weight": w_back * o["weight"], "back": [m.species for m in back], "order": o["order"],
                         "position": compose(reg, sides, f, search)})
    jobs.sort(key=lambda j: -j["weight"])
    total = sum(j["weight"] for j in jobs)
    kept = jobs[:positions]
    covered = sum(j["weight"] for j in kept)
    # How the solver's sides map onto the battle's: its slot i is the battle's `slots[sid][i]` (a
    # side with one Pokémon left has it in the solver's first slot, wherever it stands), and the
    # player's Pokémon k (field, then back) is `team[k]` by nickname, None for an empty slot's filler.
    slots = {sid: [0, 1] if sid in empty or len(on[sid]) != 1 else [on[sid][0].position, 1 - on[sid][0].position]
             for sid in ("p1", "p2")}
    team = [m.nickname for m in on[me] + seen_back[me]]
    for i in empty.get(me, []):
        team.insert(i, None)
    return {"eligible": True, "reason": None,
            "kind": f"{len(mine)}v{len(theirs_seen) + _unseen(reg, state, them)[1]}",
            "replacing": sorted(empty), "slots": slots, "team": team,
            "jobs": [{**j, "weight": j["weight"] / covered} for j in kept],
            "unsolved": round(1 - covered / total * (1 - unsolved_speed), 4) if total else 1.0,
            "considered": len(jobs), "notes": notes}


def _out_of_pp(view: PlayerView, mons: list[Any]) -> list[list[str]] | None:
    """The moves each of the player's Pokémon on the field has no PP left for, as its request shows
    them (a long battle runs out), in the solver's order; None when there are none."""
    active = (view.req or {}).get("active") or []
    out = []
    for m in mons:
        slot = active[m.position] if m.state == "active" and m.position is not None and m.position < len(active) else {}
        out.append([mv["id"] for mv in slot.get("moves", []) if mv.get("pp") == 0])
    return out if any(out) else None


def _trapped(view: PlayerView, mons: list[Any]) -> list[bool] | None:
    """Which of the player's Pokémon on the field its request says are trapped (a hidden trap is
    shown there once a switch was refused), in the solver's order; None when none are."""
    active = (view.req or {}).get("active") or []
    out = [bool(m.state == "active" and m.position is not None and m.position < len(active)
                and active[m.position].get("trapped")) for m in mons]
    return out if any(out) else None


# A Pokémon's entries in the solver's per-Pokémon lists, for an empty slot's filler: it is fainted
# before anything reads them.
_NEUTRAL = {"hp": 100, "mega": False, "boosts": {}, "consumed": False, "status": None, "timesAttacked": 0,
            "stall": 0, "fresh": False, "choicelock": None, "nopp": [], "trapped": False}


def _placeholder(reg: Regulation, side_sets: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    used = {s["species"] for ss in side_sets.values() for s in ss}
    x = next(x for x in reversed(solver.FILLERS) if x not in used)
    return {"species": x, "ability": solver._ability(reg, x), "moves": ["Protect"]}


def _emptied(reg: Regulation, f: dict[str, Any], side_sets: dict[str, list[dict[str, Any]]],
             empty: dict[str, list[int]]) -> dict[str, Any]:
    """`facts` with a filler standing in each empty slot: two on the field, the solver told which
    are empty, and a neutral entry in each per-Pokémon list at the slot."""
    f = {**f, "active": dict(f["active"]), "empty": {sid: list(v) for sid, v in empty.items()}}
    for sid, slots in empty.items():
        f["active"][sid] = 2
        n = len(side_sets[sid])
        for k, neutral in _NEUTRAL.items():
            v = (f.get(k) or {}).get(sid)
            if v is None:
                continue
            v = list(v) + [neutral] * (n - len(v))
            for i in slots:
                v.insert(i, neutral)
            f[k] = {**f[k], sid: v}
    return f


def combine(jobs: list[dict[str, Any]], results: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    return endgame.combine(jobs, results)


# --- the policy's value of a position, as the page shows it above two a side -------------------

def value_reason(reg: Regulation, view: Any) -> str | None:
    """Why the policy's value is not given for this position, or None. It is given where it was
    gated: from a player's seat, open sheets, more than two a side and after the first faint, with
    the player's back known (phase9-findings, "the policy's value of a position")."""
    state, me = view.o, view.perspective
    if me not in ("p1", "p2"):
        return "the policy's value is given from a player's seat"
    them = "p2" if me == "p1" else "p1"
    if not state.started or state.ended:
        return "the battle is not in progress"
    if not state.sides[them].sheet:
        return "with a closed sheet the policy's value has not been checked"
    left = {sid: reg.bring - sum(m.state == "fainted" for m in state.sides[sid].mons) for sid in ("p1", "p2")}
    if max(left.values()) <= 2 or min(left.values()) == 0:
        return "the engine answers with two or fewer a side"
    if all(v == reg.bring for v in left.values()):
        return "the policy's value adds to the model once a Pokémon has fainted, not before"
    for sid in ("p1", "p2"):
        on = doubles.actives(state, sid)
        if len(on) < min(2, left[sid]):
            return "a slot is waiting for a replacement"
        for m in on:
            if m.volatiles:
                return f"{m.species} has {', '.join(sorted(m.volatiles))}, which the solver cannot set up"
        for m in _left(state, sid):
            if m.status and m.status not in endgame.STATUSES:
                return f"{m.species} is {m.status}, whose turn counter is not shown"
    if _unseen(reg, state, me)[1]:
        return "mark the four you brought to get the policy's value"
    return None


def value_position(reg: Regulation, view: Any, search: dict[str, Any]) -> dict[str, Any]:
    """The one position the policy's value is read from, built as `policy_value.py` built the
    positions it was gated on (`policy_vs_people.positions`): both sides on the field and in the
    back, theirs filled with the heaviest guess at what is unseen, their spreads at the commonest
    Speed. Your own sets and spreads are the ones you built."""
    why = value_reason(reg, view)
    if why:
        return {"eligible": False, "reason": why}
    state, me = view.o, view.perspective
    them = "p2" if me == "p1" else "p1"
    backs_ = backs(reg, state, them)
    their_back = list(backs_[0][0]) if backs_ else []
    mons, sets_ = {}, {}
    for sid, extra in ((me, []), (them, their_back)):
        on = doubles.actives(state, sid)
        seen_back = [m for m in state.sides[sid].mons if m.state == "bench"]
        mons[sid] = on + seen_back + list(extra)
        ss = []
        for m in mons[sid]:
            if sid == me:
                x = _own_set(reg, view, m) | {"mega": m.forme if m.mega else None}
            else:
                x = _sheet_set(reg, m) | {"mega": m.forme if m.mega else None}
                x["sp"] = solver.spread(reg, x, _commonest_speed(reg, x))
            ss.append(x)
        sets_[sid] = ss
    if any(endgame.UNSOLVABLE_MOVES & {to_id(x) for x in s.get("moves") or []} for ss in sets_.values() for s in ss):
        return {"eligible": False, "reason": "Revival Blessing, which would bring back a Pokémon the solver does not have"}
    f = doubles.facts(reg, view, sets_, mons=mons)
    on_n = {sid: len(doubles.actives(state, sid)) for sid in ("p1", "p2")}
    f["bench"] = {sid: len(mons[sid]) - on_n[sid] for sid in ("p1", "p2")}
    f["megaUsed"] = {sid: True for sid in ("p1", "p2") if any(m.mega for m in state.sides[sid].mons)}
    left = {sid: reg.bring - sum(m.state == "fainted" for m in state.sides[sid].mons) for sid in ("p1", "p2")}
    return {"eligible": True, "reason": None, "kind": f"{left[me]}v{left[them]}", "back": [m.species for m in their_back],
            "position": compose(reg, sets_, f, search)}
