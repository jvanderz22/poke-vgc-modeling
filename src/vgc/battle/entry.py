"""A battle entered by hand, one tap at a time — and what follows from each tap.

The cartridge has no log. What it has is a person watching, so this is the other adapter onto
`vgc.battle.state.BattleState`: where `vgc.data.observe.Observer` is driven by protocol lines,
this one is driven by somebody pressing buttons, and both end at the same object. Win probability
and all four belief channels read that object and never learn which one filled it in.

**The journal is the battle; the state is derived.** Every tap appends one small JSON entry, and
the state is what you get by replaying the journal from team preview. That is not bookkeeping
pedantry — it is the whole feature list:

  * *undo* is popping the tail,
  * *going back to turn 6* is replaying a prefix,
  * *WP at every step* is replaying every prefix,
  * and a battle survives a page reload, because the only thing worth saving is a list of taps.

Replay must therefore be a pure function of `(setup, journal)`, which is why nothing here reads a
clock, a random number or the live state of anything else.

**Derivation is what keeps the tap count down.** A cartridge names the cause and the effect in one
line, so logging *"Incineroar's Intimidate"* should be one tap and everything downstream should be
arithmetic — the Attack drop on both opposing Pokémon, the Defiant answer, the Mirror Armor
reflection, the ability each of those pins. `vgc.battle.rules` owns that arithmetic; this module
owns *when to ask*. Three rules decide it:

  1. **One possible outcome and nothing to learn — do it silently.** A known-ability Pokémon
     arriving with nothing to announce raises no question at all.
  2. **One possible outcome, but the timing is evidence — ask anyway, as a one-tap confirm.**
     Switch-in abilities announce in Speed order, so *when* Intimidate fired is a Speed
     comparison available on turn 1 (`vgc.belief.speed.ability_pairs`). Auto-applying it would
     have the app inventing an order it was never shown, so the tap that costs nothing is the tap
     that carries the information.
  3. **Otherwise ask**, with every outcome the rules allow and what each would pin.

A question can always be answered *"not sure"*, which concludes nothing. That is the honest
option and it must stay available: a belief that quietly excludes the truth because somebody
tapped the wrong thing under time pressure is worse than one that stayed wide.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from vgc.battle import rules
from vgc.battle.state import BattleState, Mon
from vgc.regulation import Regulation, to_id

# Every kind of tap. Kept as a closed set so an unknown entry is a loud error on replay rather
# than a silently skipped line that makes the state subtly wrong ten turns later.
ENTRY_KINDS = (
    "lead",        # who starts: {side, slot, species}
    "bring",       # the four a side brought: {side, species: [four]} — yours, from team preview
    "turn",        # {n}
    "move",        # {side, slot, move, target: {side, slot} | null, spread, result?, results?, chance?}
    "cant",        # {side, slot, reason} — took its turn without moving: flinch, par, slp, frz, recharge
    "switch",      # {side, slot, species}
    "swap",        # {side, slot} — Ally Switch: the Pokémon in `slot` and its partner trade places
    "damage",      # {side, slot, pct | hp, fainted, crit, eot?}
    "heal",        # {side, slot, pct | hp, eot?} — `eot`: the end-of-turn effect it confirms
    "faint",       # {side, slot}
    "status",      # {side, slot, status: "brn" | ... | null}
    "boost",       # {side, slot, stat, stages}      — anything the rules do not derive
    "field",       # {weather | terrain | pseudo, value, on}
    "side",        # {side, condition, on}
    "reveal",      # {side, species, what: "item" | "ability", value}
    "consume",     # {side, species, item}
    "tera",        # {side, species, type}
    "mega",        # {side, species, forme}
    "answer",      # {question, option: int | null}  — null means "not sure"
    "end",         # {winner: "p1" | "p2" | null, by?: "forfeit"}
    "note",        # {text} — carried for the timeline, changes nothing
)

STATUSES = ("brn", "par", "psn", "tox", "slp", "frz")
TERRAIN_NAMES = {"grassyterrain": "Grassy Terrain", "electricterrain": "Electric Terrain",
                 "psychicterrain": "Psychic Terrain", "mistyterrain": "Misty Terrain"}

# How a move went for a target it did no damage to. A move with no damage after it already reads as
# no damage, so none of these changes the state; what they add is *which* no-damage it was, so a
# miss is not later read as a resisted hit, and so a reload knows the move's results are in.
# `result` is for the move as a whole (or its one target); a spread move gives `results`, one per
# target that took no damage: [{side, slot, result}].
# `hit` with no damage after it says the move hit and its HP was not caught (Fast mode's "didn't
# catch it"), so the result is in rather than still owed.
MOVE_RESULTS = ("hit", "miss", "protected", "immune", "failed")

# Moves aimed at one Pokémon, whose results are owed for that one; spread moves owe one per target
# on the field. Anything else (self, the field, a side) owes none.
_AIMED = {"normal", "adjacentFoe", "any", "randomNormal", "adjacentAlly", "adjacentAllyOrSelf"}
_SPREAD = {"allAdjacentFoes": False, "allAdjacent": True}     # value: whether the ally is hit too


@dataclass
class Question:
    """Something the app cannot work out on its own, with every answer the rules allow.

    `forced` marks the single-outcome case that is asked anyway (rule 2 above): the UI should
    render it as one button that says what happened, not a menu of one.
    """

    id: str
    kind: str                       # switch_in | stat_drop | on_hit | terrain
    prompt: str
    side: str
    species: str
    slot: int | None
    outcomes: list[rules.Outcome]
    source: dict[str, Any] | None = None    # who caused it, for the UI to say "…from Incineroar"
    forced: bool = False

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "prompt": self.prompt, "side": self.side,
                "species": self.species, "slot": self.slot, "source": self.source,
                "forced": self.forced, "options": [o.to_json() for o in self.outcomes]}


@dataclass
class Replay:
    """What replaying a journal produced: the battle, what is still unanswered, what was derived
    without being told, and anything that did not make sense."""

    state: BattleState
    questions: list[Question] = field(default_factory=list)
    derived: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Which Pokémon each move entry was (by journal index): a tap names a slot, and what reads the
    # journal later (the Protect counter) needs the Pokémon that stood there then.
    movers: dict[int, str] = field(default_factory=dict)
    # Where the turn has got to (`progress`): who has had their turn since the last `turn` entry,
    # whether anyone has moved, and the move whose results are still owed, if any.
    acted: list[tuple[str, str]] = field(default_factory=list)
    moved: bool = False
    awaiting: dict[str, Any] | None = None
    # Who last stood in each slot, for "who came in for X?" once X has fainted.
    last_in: dict[tuple[str, int], str] = field(default_factory=dict)
    # When each badly poisoned Pokémon was poisoned, for the toxic counter. Reset on switching in.
    tox_since: dict[tuple[str, str], int] = field(default_factory=dict)
    # End-of-turn effects already confirmed this turn (`eot` on a damage or heal), so a reload does
    # not offer a Leftovers heal that has already been logged.
    eot_done: set[str] = field(default_factory=set)
    # Protect this turn, keyed (side, species): who used it and it did not fail (`guarded`), who
    # used it last turn (`guarded_before`), and whose is still up (`shielded`). Only a first
    # Protect is counted as up: one used again straight after can fail, and a move logged without
    # a result does not say whether it did. Feint and its like take it down again.
    guarded: set[tuple[str, str]] = field(default_factory=set)
    guarded_before: set[tuple[str, str]] = field(default_factory=set)
    shielded: set[tuple[str, str]] = field(default_factory=set)
    # What the replay did on its own after a move, by the move's journal index (Life Orb's recoil),
    # so the page can say so on the move's line.
    applied: dict[int, list[str]] = field(default_factory=dict)
    # The move now connecting, for what follows from it (`_on_connect`): its user, whether its own
    # stat change has landed, which targets have had its added effect, and which of its chance
    # effects you said happened (`chance`: [{side, slot}] on the move entry).
    fx: dict[str, Any] | None = None
    # Where in the journal we are, and how many questions this entry has raised so far. Together
    # they name a question (`_qid`), which is what makes an id stable under an append.
    _index: int = 0
    _n: int = 0


# --- team preview -------------------------------------------------------------------------

def setup_state(reg: Regulation, setup: dict[str, Any]) -> BattleState:
    """The battle at team preview: six species a side, and whatever is known about each.

    Your own six are fully known because you built them (`source: "own"`). Theirs are six species
    and nothing else, which is what Team Preview Only means — and under Open Team Sheets the same
    structure with item, ability, moves and nature filled in (`source: "sheet"`). The Stat Points
    stay hidden either way, which is the point of the whole belief package: an open sheet is not
    full information.
    """
    state = BattleState(setup.get("perspective", "p1"), reg.dex)
    for sid in ("p1", "p2"):
        side = state.sides[sid]
        side.name = setup.get("names", {}).get(sid)
        entries = side_entries(setup, sid)
        own = sid == state.perspective
        for spec in entries:
            m = Mon(_species_name(reg, spec["species"]))
            m.nickname = spec.get("nickname") or m.species
            source = "own" if own else "sheet"
            if spec.get("item") is not None:
                m.item, m.item_source = to_id(spec["item"]), source
            if spec.get("ability"):
                m.ability, m.ability_source = to_id(spec["ability"]), source
            if spec.get("moves"):
                m.moves = [to_id(x) for x in spec["moves"]]
            m.nature = spec.get("nature")
            if spec.get("stats"):
                m.stats = {k: int(v) for k, v in spec["stats"].items()}
                m.hp_max = m.stats.get("hp")
            if spec.get("sp"):
                m.sp = {k: int(v) for k, v in spec["sp"].items()}
            side.mons.append(m)
            side.sheet = side.sheet or (not own and spec.get("ability") is not None)
        side.team_size = len(side.mons)
    return state


def side_entries(setup: dict[str, Any], sid: str) -> list[dict[str, Any]]:
    """One side's six as the setup gives them. A player's setup has `mine` and `theirs`; a
    spectator's (someone else's game, watched with both sheets open) has `p1` and `p2`, since
    neither is yours."""
    persp = setup.get("perspective", "p1")
    if persp == "spectator":
        return setup[sid]
    return setup["mine" if sid == persp else "theirs"]


def _species_name(reg: Regulation, name: str) -> str:
    entry = reg.dex.get_species(name)
    return entry["name"] if entry else name


def from_team(reg: Regulation, text: str) -> list[dict[str, Any]]:
    """Your six, from the Showdown text the team library already stores."""
    from vgc.teams.sets import calc_stats
    from vgc.teams.showdown_text import parse_team

    out = []
    for m in parse_team(text).members:
        try:
            stats = calc_stats(m, reg.dex, reg)
        except Exception:                       # an illegal set should not stop a battle starting
            stats = {}
        out.append({"species": m.species, "nickname": m.nickname, "item": m.item or "",
                    "ability": m.ability, "moves": list(m.moves), "nature": m.nature,
                    "stats": stats, "sp": m.sp.as_dict()})
    return out


# --- replay -------------------------------------------------------------------------------

def replay(reg: Regulation, setup: dict[str, Any], journal: list[dict[str, Any]],
           upto: int | None = None) -> Replay:
    """Rebuild the battle from the taps. `upto` stops after that many entries — how the UI walks
    a finished game backwards without keeping a state per turn."""
    rp = Replay(setup_state(reg, setup))
    for index, entry in enumerate(journal if upto is None else journal[:upto]):
        try:
            _apply_entry(rp, reg, index, entry)
        except EntryError as e:
            rp.errors.append(f"entry {index} ({entry.get('kind')}): {e}")
    return rp


class EntryError(ValueError):
    """An entry that does not describe anything that could have happened."""


def _apply_entry(rp: Replay, reg: Regulation, index: int, entry: dict[str, Any]) -> None:
    kind = entry.get("kind")
    if kind not in ENTRY_KINDS:
        raise EntryError(f"unknown kind {kind!r}")
    rp._index, rp._n = index, 0        # question ids are scoped to the entry that raised them
    _HANDLERS[kind](rp, reg, entry)


def _qid(rp: Replay) -> str:
    """Stable under an append: an id names the entry that raised the question and its position in
    that entry's cascade. The journal only ever grows at the tail, so answering question 3 today
    cannot renumber question 2, and yesterday's ids still resolve.
    """
    rp._n += 1
    return f"q{rp._index}.{rp._n - 1}"


# --- addressing ---------------------------------------------------------------------------

def _active(rp: Replay, entry: dict[str, Any], key: str = "") -> Mon:
    side = entry[f"{key}side"] if key else entry["side"]
    slot = entry[f"{key}slot"] if key else entry["slot"]
    m = rp.state.at(side, int(slot))
    if m is None:
        raise EntryError(f"nothing active in {side} slot {slot}")
    return m


def _named(rp: Replay, side: str, species: str) -> Mon:
    want = rp.state._base(species)
    for m in rp.state.sides[side].mons:
        if rp.state._base(m.species) == want:
            return m
    raise EntryError(f"{species} is not on {side}")


def _ident(state: BattleState, m: Mon) -> str:
    side = state._side_of(m)
    return f"{side}{'ab'[m.position]}: {m.nickname or m.species}" if m.position is not None else ""


# --- the handlers -------------------------------------------------------------------------

def _on_bring(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    """Which four a side brought. Yours is known to you and to nothing else on the page: a Pokémon
    in your back has not been seen, and a position that counts your back needs it (the policy's
    value of a position, `vgc.policy.view.value_position`)."""
    side = rp.state.sides[e["side"]]
    chosen = [_named(rp, e["side"], x) for x in e.get("species") or []]
    if len({id(m) for m in chosen}) != reg.bring:
        raise EntryError(f"a side brings {reg.bring}, not {len({id(m) for m in chosen})}")
    # As a player's request does (`Observer.request`): the four are in the back until they come
    # out, the other two were not brought. The WP model's player rows are built the same way
    # (`snapshots.player_view`), so this is also the input it was trained on.
    side.brought = [m.species for m in chosen]
    side.brought_known = True
    for m in side.mons:
        if m not in chosen:
            m.state, m.position = "not_brought", None
        elif m.state in ("unrevealed", "not_brought"):
            m.state = "bench"


def _on_lead(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    _bring_in(rp, reg, e["side"], int(e["slot"]), _named(rp, e["side"], e["species"]))


def _on_switch(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    m = _named(rp, e["side"], e["species"])
    if m.state == "fainted":
        raise EntryError(f"{m.species} has fainted")
    _bring_in(rp, reg, e["side"], int(e["slot"]), m)


def _bring_in(rp: Replay, reg: Regulation, side: str, slot: int, m: Mon) -> None:
    rp.state.switch_in(side, slot, m, m.forme)
    rp.last_in[(side, slot)] = m.species
    rp.tox_since.pop((side, m.species), None)
    if rp.state.started:
        # A Pokémon that comes in mid-turn does not act in it, and nothing is owed any more on a
        # move once something else has happened in the turn.
        rp.acted.append((side, m.species))
        rp.awaiting = None
    # What it announces on arrival, and what a standing terrain does to whatever it is holding.
    _ask(rp, reg, m, "switch_in",
         rules.on_switch_in(reg, m.species, rules.still_possible(reg, m),
                            weather=rp.state.weather, terrain=rp.state.terrain), timed=True)
    if rp.state.terrain:
        _ask(rp, reg, m, "terrain", rules.on_terrain_set(reg, rp.state.terrain, m.item))


def _on_swap(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    """Ally Switch and friends. Nothing else restates the two positions, so a swap left out crosses
    every target and turn-order read after it (`Observer._on_swap` says the same of a log)."""
    m = _active(rp, e)
    old = m.position
    other = rp.state.at(e["side"], 1 - old)
    m.position = 1 - old
    if other is not None:
        other.position = old
    a, b = rp.last_in.get((e["side"], old)), rp.last_in.get((e["side"], 1 - old))
    for slot, who in ((1 - old, a), (old, b)):
        if who is None:
            rp.last_in.pop((e["side"], slot), None)
        else:
            rp.last_in[(e["side"], slot)] = who


def _on_turn(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    rp.state.begin_turn(int(e["n"]))
    rp.acted, rp.moved, rp.awaiting = [], False, None
    rp.eot_done = set()
    rp.fx = None
    rp.guarded_before, rp.guarded, rp.shielded = rp.guarded, set(), set()


def _on_move(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    m = _active(rp, e)
    move = to_id(e["move"])
    target = e.get("target")
    ident = None
    if target:
        t = rp.state.at(target["side"], int(target["slot"]))
        ident = _ident(rp.state, t) if t is not None else None
    rp.state.record_move(m, move, target=ident, spread=bool(e.get("spread")),
                         called_by=e.get("called_by"))
    rp.movers[rp._index] = m.species
    if e.get("called_by"):
        return
    rp.acted.append((e["side"], m.species))
    rp.moved = True
    rp.awaiting = _owed(rp, reg, e, m, move)
    _protect(rp, e, m, move)
    _move_effects(rp, reg, e, m, move)


NO_EFFECT = ("miss", "protected", "immune", "failed")

PROTECT_MOVES = {"protect", "detect", "spikyshield", "kingsshield", "banefulbunker", "silktrap",
                 "burningbulwark", "obstruct"}
# Moves that take a Protect down when they land.
LIFTS_PROTECT = {"feint", "shadowforce", "phantomforce", "hyperspacefury", "hyperspacehole"}


def _protect(rp: Replay, e: dict[str, Any], m: Mon, move: str) -> None:
    if move in PROTECT_MOVES and e.get("result") != "failed":
        key = (e["side"], m.species)
        rp.guarded.add(key)
        if key not in rp.guarded_before:
            rp.shielded.add(key)
    elif move in LIFTS_PROTECT and e.get("target") and e.get("result") not in NO_EFFECT:
        t = rp.state.at(e["target"]["side"], int(e["target"]["slot"]))
        if t is not None:
            rp.shielded.discard((e["target"]["side"], t.species))


def _move_effects(rp: Replay, reg: Regulation, e: dict[str, Any], m: Mon, move: str) -> None:
    """What a move does to stats, once it is known to have connected (`rules.MOVE_SELF` and
    friends). A status move connects as it is used unless it says otherwise; a damaging move
    connects with its first hit, so its effects wait for the damage (`_on_connect`)."""
    rp.fx = None
    result = e.get("result")
    if result in NO_EFFECT:
        return
    other = "p2" if e["side"] == "p1" else "p1"
    settled = {(r["side"], int(r["slot"])) for r in e.get("results") or [] if r.get("result") in NO_EFFECT}
    if e.get("target"):
        aimed = [(e["target"]["side"], int(e["target"]["slot"]))]
    elif e.get("spread"):
        aimed = [(other, x) for x in (0, 1)]
    else:
        aimed = []
    targets = [t for (s, x) in aimed if (s, x) not in settled and (t := rp.state.at(s, x)) is not None]
    if (reg.dex.get_move(move) or {}).get("category") == "Status":
        if move in rules.MOVE_SELF:
            _stat_change(rp, reg, m, rules.MOVE_SELF[move], source=None, move=move)
        for t in targets if move in rules.MOVE_TARGET else []:
            _stat_change(rp, reg, t, rules.MOVE_TARGET[move], source=m, move=move)
        return
    rp.fx = {"move": move, "user": m, "self_done": False, "hit": set(), "index": rp._index,
             "chance": {(c["side"], int(c["slot"])) for c in e.get("chance") or []}}
    # "Didn't catch it" on one target: it hit, with no damage entry to say so.
    if result == "hit" and len(targets) == 1:
        _on_connect(rp, reg, targets[0])


def _on_connect(rp: Replay, reg: Regulation, target: Mon | None) -> None:
    """A damaging move has hit `target` (None: it hit something that fainted). Its user's own stat
    change lands once, on the first hit; its added effect lands on each target that is still
    standing, if it is certain or you said it happened."""
    fx = rp.fx
    if fx is None:
        return
    user, move = fx["user"], fx["move"]
    side_of = rp.state._side_of
    if not fx["self_done"]:
        fx["self_done"] = True
        if user.state == "active":
            change = dict(rules.MOVE_SELF.get(move, {}))
            for sec in rules.MOVE_SECONDARY.get(move, []):
                if "self" in sec and (sec["chance"] == 100 or (side_of(user), user.position) in fx["chance"]):
                    for k, v in sec["self"].items():
                        change[k] = change.get(k, 0) + v
            if change:
                _stat_change(rp, reg, user, change, source=None, move=move)
            _life_orb(rp, reg, user, move, fx["index"])
    if target is None or target is user or target.state != "active":
        return
    key = (side_of(target), target.position)
    if key in fx["hit"]:
        return
    fx["hit"].add(key)
    for sec in rules.MOVE_SECONDARY.get(move, []):
        if "self" in sec or not (sec["chance"] == 100 or key in fx["chance"]):
            continue
        if "target" in sec:
            _stat_change(rp, reg, target, sec["target"], source=user, move=move)
        if "status" in sec and target.status is None:
            target.status = sec["status"]
            if sec["status"] == "tox":
                rp.tox_since[(side_of(target), target.species)] = rp.state.turn


def _life_orb(rp: Replay, reg: Regulation, user: Mon, move: str, index: int) -> None:
    """Life Orb's recoil once the move has hit something: a tenth of its maximum HP, when the item
    is known. Magic Guard takes none, and Sheer Force none on a move with an added effect, so while
    either could still be its ability nothing is taken and the HP is left for you to enter."""
    if to_id(user.item or "") != "lifeorb" or user.state != "active" or user.hp <= 0:
        return
    ability = rp.state._active_ability(user)
    possible = [to_id(ability)] if ability else rules.still_possible(reg, user)
    if "magicguard" in possible or ("sheerforce" in possible and move in rules.MOVE_SECONDARY):
        return
    side = rp.state._side_of(user)
    if user.hp_max and rp.state._own(side):
        hp = max(0, round(user.hp * user.hp_max) - max(1, user.hp_max // 10))
        rp.state.set_hp(user, hp, user.hp_max, user.status)
        left = f"{hp} HP"
    else:
        hp = max(0, round(user.hp * 100) - 10)
        rp.state.set_hp(user, hp, 100, user.status)
        left = f"{hp}%"
    if hp == 0:
        rp.state.faint(user)
        left = "fainted"
    rp.applied.setdefault(index, []).append(f"Life Orb: {user.species} on {left}")


def _stat_change(rp: Replay, reg: Regulation, mon: Mon, change: dict[str, int], *,
                 source: Mon | None, move: str) -> None:
    """Rises land as they are. Drops are asked about when something could have answered them,
    and settled silently when nothing could (`_ask`, rule 1)."""
    for k, v in change.items():
        if v > 0:
            rp.state.apply_boost(mon, k, v)
    drops = {k: v for k, v in change.items() if v < 0}
    if not drops:
        return
    by_other = source is not None and source is not mon
    outcomes = rules.drop_outcomes(reg, mon.species, drops, rules.still_possible(reg, mon),
                                   by_other=by_other, item=mon.item, items=_drop_items(reg, mon))
    name = (reg.dex.get_move(move) or {}).get("name", move)
    _ask(rp, reg, mon, "move_drop", outcomes, source=source if by_other else None,
         prompt=f"{name} lowers {mon.species}'s {', '.join(drops)} — what happened?")


def _drop_items(reg: Regulation, mon: Mon) -> list[str]:
    """The drop-answering items an unseen item could well be."""
    if mon.item is not None:
        return []
    from vgc.belief import sets as set_belief

    try:
        p = set_belief.given(reg, mon).item()
    except Exception:
        return []
    return [i for i in rules.DROP_ITEMS if p.get(i, 0.0) >= rules.DROP_ITEM_SHARE]


def _owed(rp: Replay, reg: Regulation, e: dict[str, Any], m: Mon, move: str) -> dict[str, Any] | None:
    """The targets a move still owes a result for: whose HP it changed, or how it did not."""
    result = e.get("result")
    if result is not None and result not in MOVE_RESULTS:
        raise EntryError(f"unknown result {result!r}; one of {', '.join(MOVE_RESULTS)}")
    settled = set()
    for r in e.get("results") or []:
        if r.get("result") not in MOVE_RESULTS:
            raise EntryError(f"unknown result {r.get('result')!r}; one of {', '.join(MOVE_RESULTS)}")
        settled.add((r["side"], int(r["slot"])))
    entry_ = reg.dex.get_move(move) or {}
    if result not in (None, "hit") or entry_.get("category") == "Status":
        return None
    kind = entry_.get("target")
    side, other = e["side"], "p2" if e["side"] == "p1" else "p1"
    if e.get("spread") or kind in _SPREAD:
        hits = [(other, s) for s in (0, 1)]
        if _SPREAD.get(kind):
            hits.append((side, 1 - m.position))
    elif e.get("target") and (kind in _AIMED or kind is None):
        if result == "hit":               # it hit, and its HP was not caught: nothing more owed
            return None
        hits = [(e["target"]["side"], int(e["target"]["slot"]))]
    else:
        return None
    targets = [{"side": s, "slot": x} for s, x in hits
               if rp.state.at(s, x) is not None and (s, x) not in settled]
    if not targets:
        return None
    return {"index": rp._index, "side": side, "slot": m.position, "species": m.species,
            "move": move, "targets": targets}


def _on_cant(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    """A Pokémon that took its turn without moving. It changes nothing on the field; what it says
    is that the Pokémon has had its turn, and where in the order it came."""
    m = _active(rp, e)
    if not to_id(str(e.get("reason") or "")):
        raise EntryError("needs a reason: flinch, par, slp, frz, recharge")
    rp.acted.append((e["side"], m.species))
    rp.moved = True
    rp.awaiting = None


def _on_damage(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    m = _active(rp, e)
    before = m.hp
    _set_hp(rp, m, e)
    if e.get("eot"):
        # An end-of-turn effect is not the last move's output; reading it as one would tell the
        # damage channel that move hits harder than it does. (The page logs these as `heal`.)
        rp.eot_done.add(e["eot"])
    else:
        rp.state.record_damage(m, before, crit=bool(e.get("crit")))
        _on_connect(rp, reg, m if m.hp > 0 and not e.get("fainted") else None)
    if rp.awaiting is not None:
        here = {"side": e["side"], "slot": int(e["slot"])}
        rp.awaiting["targets"] = [t for t in rp.awaiting["targets"] if t != here]
        if not rp.awaiting["targets"]:
            rp.awaiting = None
    if m.hp == 0.0 or e.get("fainted"):
        rp.state.faint(m)
        return
    last = rp.state._resolving
    if last is not None and not last.called_by:
        source = rp.state.at(last.side, last.slot)
        _ask(rp, reg, m, "on_hit",
             rules.on_damaging_hit(reg, m.species, last.move, rules.still_possible(reg, m)),
             source=source)


def _on_heal(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    _set_hp(rp, _active(rp, e), e)
    if e.get("eot"):
        rp.eot_done.add(e["eot"])


def _set_hp(rp: Replay, m: Mon, e: dict[str, Any]) -> None:
    """A percentage for theirs and real numbers for yours — the split the cartridge itself gives
    you, and the same one `BattleState.set_hp` already models for a spectator versus a player."""
    if e.get("fainted"):
        rp.state.set_hp(m, 0, 0, "fnt")
        return
    if e.get("hp") is not None and m.hp_max:
        rp.state.set_hp(m, int(e["hp"]), m.hp_max, m.status)
    elif e.get("pct") is not None:
        rp.state.set_hp(m, int(e["pct"]), 100, m.status)
    else:
        raise EntryError("needs pct, hp or fainted")


def _on_faint(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    rp.state.faint(_active(rp, e))


def _on_status(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    status = e.get("status")
    if status is not None and status not in STATUSES:
        raise EntryError(f"unknown status {status!r}")
    m = _active(rp, e)
    m.status = status
    if status == "tox":
        rp.tox_since[(e["side"], m.species)] = rp.state.turn


def _on_boost(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    rp.state.apply_boost(_active(rp, e), e["stat"], int(e["stages"]))


def _on_field(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    what, value, on = e.get("what"), e.get("value"), e.get("on", True)
    if what == "weather":
        rp.state.set_weather(to_id(value) if on and value else None)
    elif what == "terrain":
        rp.state.set_terrain(to_id(value) if on and value else None)
        if on and value:
            _terrain_seeds(rp, reg)
    elif what == "pseudo":
        rp.state.set_pseudo(to_id(value), bool(on))
    else:
        raise EntryError(f"unknown field effect {what!r}")


def _terrain_seeds(rp: Replay, reg: Regulation) -> None:
    """Terrain coming up is the seeds' cue, so every Pokémon on the field gets asked once."""
    for sid in ("p1", "p2"):
        for m in rp.state.sides[sid].mons:
            if m.state == "active":
                _ask(rp, reg, m, "terrain", rules.on_terrain_set(reg, rp.state.terrain, m.item))


def _on_side(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    rp.state.set_side_condition(e["side"], to_id(e["condition"]), bool(e.get("on", True)))


def _on_reveal(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    m = _named(rp, e["side"], e["species"])
    what = e["what"]
    if what not in ("item", "ability"):
        raise EntryError(f"cannot reveal {what!r}")
    value = to_id(e["value"]) if e.get("value") else ""
    if what == "ability" and value:
        m.ability, m.ability_source = value, "revealed"
    elif what == "item":
        m.item, m.item_source = value, "revealed"


def _on_consume(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    rp.state.consume_item(_named(rp, e["side"], e["species"]), to_id(e["item"]))


def _on_tera(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    _named(rp, e["side"], e["species"]).volatiles.add("terastallized")


def _on_mega(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    m = _named(rp, e["side"], e["species"])
    m.mega = True
    if e.get("forme"):
        m.forme = _species_name(reg, e["forme"])
    if e.get("item"):
        rp.state.reveal(m, "item", to_id(e["item"]))


def _on_end(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    rp.state.ended = True
    rp.state.winner = e.get("winner")


def _on_note(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    return


def _on_answer(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    """Settle an open question. `option: null` is *"not sure"* — it closes the question and
    concludes nothing, which is the only honest answer when you did not catch what happened."""
    qid = e["question"]
    q = next((x for x in rp.questions if x.id == qid), None)
    if q is None:
        raise EntryError(f"no open question {qid!r}")
    rp.questions.remove(q)
    option = e.get("option")
    if option is None:
        rp.derived.append(f"{q.species}: not sure, nothing concluded")
        return
    if not 0 <= int(option) < len(q.outcomes):
        raise EntryError(f"option {option} out of range for {qid}")
    mon = _named(rp, q.side, q.species)
    source = None
    if q.source:
        source = rp.state.at(q.source["side"], q.source["slot"]) if q.source.get("slot") is not None else None
    _settle(rp, reg, mon, q.outcomes[int(option)], source, kind=q.kind, raced=True)


_HANDLERS = {
    "lead": _on_lead, "bring": _on_bring, "turn": _on_turn, "move": _on_move, "cant": _on_cant,
    "switch": _on_switch, "swap": _on_swap,
    "damage": _on_damage, "heal": _on_heal, "faint": _on_faint, "status": _on_status,
    "boost": _on_boost, "field": _on_field, "side": _on_side, "reveal": _on_reveal,
    "consume": _on_consume, "tera": _on_tera, "mega": _on_mega, "answer": _on_answer,
    "end": _on_end, "note": _on_note,
}


# --- asking, and settling -------------------------------------------------------------------

def _empty(outcome: rules.Outcome) -> bool:
    """Nothing happened and nothing was learned — not worth a tap."""
    return not (outcome.effects or outcome.ability or outcome.excludes or outcome.item)


def _ask(rp: Replay, reg: Regulation, mon: Mon, kind: str, outcomes: list[rules.Outcome],
         source: Mon | None = None, *, timed: bool = False, prompt: str | None = None) -> None:
    """Queue a question — or settle it now, when there is one answer and no timing to record.

    `timed` is what separates a switch-in from everything else. A switch-in ability announces in
    Speed order, so *when* it fired is evidence (`vgc.belief.speed.ability_pairs`), and the app
    must be told rather than assume: a forced outcome is still asked, as a one-tap confirm.
    """
    if not outcomes or (len(outcomes) == 1 and _empty(outcomes[0])):
        return
    if len(outcomes) == 1 and not timed:
        _settle(rp, reg, mon, outcomes[0], source, kind=kind, raced=False)
        return
    rp.questions.append(Question(
        id=_qid(rp), kind=kind, prompt=prompt or _prompt(kind, mon, source, rp.state.terrain), side=rp.state._side_of(mon),
        species=mon.species, slot=mon.position, outcomes=_likeliest_first(reg, mon, outcomes),
        source=({"side": rp.state._side_of(source), "slot": source.position,
                 "species": source.species} if source is not None else None),
        forced=len(outcomes) == 1))


def _likeliest_first(reg: Regulation, mon: Mon, outcomes: list[rules.Outcome]) -> list[rules.Outcome]:
    """Order a pop-up's buttons by how often the ability behind each one is actually run.

    Alphabetical is the worst possible order for something tapped under a clock: Incineroar's
    menu led with Blaze because B sorts before I, and 99.7% of Incineroar are Intimidate. This is
    the only place the usage prior touches the entry path, and it touches *order* and nothing
    else — every option the rules produced is still on the list, and the one nobody runs is last
    rather than absent. A prior that removed options would be a prior that can be wrong about
    what is possible; one that sorts them can only ever be wrong about how many taps it took.

    The "nothing announced" option is pinned to the bottom whatever its ability's share, because
    it is the answer you reach for when none of the others happened.
    """
    from vgc.belief import sets as set_belief

    try:
        p = set_belief.given(reg, mon).ability()
    except Exception:                    # no corpus built: alphabetical is still a usable order
        return outcomes
    if not p:
        return outcomes

    def key(item: tuple[int, rules.Outcome]) -> tuple:
        i, o = item
        quiet = o.label.startswith("nothing announced")
        return (quiet, -(p.get(o.ability or "", 0.0)), i)

    return [o for _, o in sorted(enumerate(outcomes), key=key)]


def _prompt(kind: str, mon: Mon, source: Mon | None, terrain: str | None = None) -> str:
    who = mon.species
    if kind == "switch_in":
        return f"{who} arrives — what announced?"
    if kind == "stat_drop":
        return f"{source.species if source else 'Something'} aims a drop at {who} — what happened?"
    if kind == "on_hit":
        return f"{who} was hit — what announced?"
    if kind == "terrain":
        # Named, because two terrains in one turn each cue the seeds, and the questions must differ.
        name = TERRAIN_NAMES.get(terrain or "", "Terrain")
        return f"{name} is up — did {who} pop an item?"
    return f"{who} — what happened?"


def _settle(rp: Replay, reg: Regulation, mon: Mon, outcome: rules.Outcome, source: Mon | None,
            *, kind: str, raced: bool) -> None:
    """Run an outcome, record what it proved, and ask whatever it created.

    The Speed claim is made only for an arrival the app was *told* about (`raced`). An outcome the
    app applied on its own has no observed moment, so recording it as one would invent an ordering
    out of the order the code happened to run in — which is exactly the kind of thing that reads
    as a sound channel and is not one.
    """
    pendings = rules.apply(rp.state, outcome, mon, source)
    if outcome.ability:
        rp.state.record_ability(
            mon, outcome.ability,
            switch_in=raced and kind == "switch_in" and outcome.ability in rules.SWITCH_IN_ABILITIES)
    if not _empty(outcome):
        rp.derived.append(f"{mon.species}: {outcome.label}")
    # Intimidate hands back one decision per opposing Pokémon rather than applying the drop, so
    # each target's own ability gets to answer for itself and nothing lands twice.
    for p in pendings:
        _ask(rp, reg, p.mon, "stat_drop",
             rules.stat_drop_outcomes(reg, p.mon.species, p.stat, p.stages,
                                      rules.still_possible(reg, p.mon)),
             source=p.source)
    # A terrain the outcome just put up is the seeds' cue too.
    if any(x["kind"] == "terrain" for x in outcome.effects):
        _terrain_seeds(rp, reg)


# --- where the turn has got to ---------------------------------------------------------------

def sendable(reg: Regulation, state: BattleState, side: str) -> list[Mon]:
    """Who could still come in. Once a side has shown all it brought, the ones never seen sat this
    game out, so they stop being offered even though nothing has said so outright."""
    seen = sum(m.state not in ("unrevealed", "not_brought") for m in state.sides[side].mons)
    return [m for m in state.bench(side) if m.state != "unrevealed" or seen < reg.bring]


def progress(rp: Replay, reg: Regulation) -> dict[str, Any]:
    """The turn so far, for a page that asks for the next thing that happened: who has had their
    turn, whether anyone has moved yet, the move still owed its results, and the fainted slots
    waiting for a replacement. Read off the replay, so a reload resumes on the right question."""
    st = rp.state
    waiting = []
    if st.started and not st.ended:
        for sid in ("p1", "p2"):
            if not sendable(reg, st, sid):
                continue
            for slot in (0, 1):
                if st.at(sid, slot) is None:
                    waiting.append({"side": sid, "slot": slot, "was": rp.last_in.get((sid, slot))})
    shielded = [{"side": sid, "slot": slot} for sid in ("p1", "p2") for slot in (0, 1)
                if (m := st.at(sid, slot)) is not None and (sid, m.species) in rp.shielded]
    return {"turn": st.turn,
            "acted": [{"side": s, "species": sp} for s, sp in rp.acted],
            "moved": rp.moved, "awaiting": rp.awaiting, "waiting": waiting, "shielded": shielded}


def end_of_turn(rp: Replay, reg: Regulation) -> list[dict[str, Any]]:
    """What could happen at the end of this turn (`rules.end_of_turn`), with the toxic counter."""
    st = rp.state
    counters = {key: st.turn - since + 1 for key, since in rp.tox_since.items()}
    return [x for x in rules.end_of_turn(reg, st, counters) if x["key"] not in rp.eot_done]


# --- the session --------------------------------------------------------------------------

class Battle:
    """A battle in progress: setup, a journal, and the state that falls out of replaying it."""

    def __init__(self, reg: Regulation, setup: dict[str, Any],
                 journal: list[dict[str, Any]] | None = None):
        self.reg = reg
        self.setup = setup
        self.journal: list[dict[str, Any]] = list(journal or [])
        self.rp = replay(reg, setup, self.journal)

    def append(self, entry: dict[str, Any]) -> Replay:
        """Add a tap. It is kept even if it turns out not to make sense, so the error shows up in
        the timeline where it can be undone, rather than vanishing as if it had never been sent."""
        self.journal.append(entry)
        self.rp = replay(self.reg, self.setup, self.journal)
        return self.rp

    def undo(self) -> Replay:
        if self.journal:
            self.journal.pop()
        self.rp = replay(self.reg, self.setup, self.journal)
        return self.rp

    def named_journal(self) -> list[dict[str, Any]]:
        """The journal with each move entry naming the Pokémon that made it, as a log's journal
        does (`vgc.wp.doubles.from_replay`)."""
        return [e | {"species": self.rp.movers[i]} if i in self.rp.movers and not e.get("species") else e
                for i, e in enumerate(self.journal)]

    def at(self, upto: int) -> Replay:
        return replay(self.reg, self.setup, self.journal, upto=upto)

    def to_json(self) -> dict[str, Any]:
        return {"setup": self.setup, "journal": self.journal}

    @classmethod
    def from_json(cls, reg: Regulation, blob: dict[str, Any]) -> "Battle":
        return cls(reg, blob["setup"], blob.get("journal"))

    def dumps(self) -> str:
        return json.dumps(self.to_json(), separators=(",", ":"))
