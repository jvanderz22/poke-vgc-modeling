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
    "turn",        # {n}
    "move",        # {side, slot, move, target: {side, slot} | null, spread}
    "switch",      # {side, slot, species}
    "damage",      # {side, slot, pct | hp, fainted, crit}
    "heal",        # {side, slot, pct | hp}
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
    "end",         # {winner: "p1" | "p2" | null}
    "note",        # {text} — carried for the timeline, changes nothing
)

STATUSES = ("brn", "par", "psn", "tox", "slp", "frz")


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
        entries = setup["mine" if sid == setup.get("perspective", "p1") else "theirs"]
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
            side.mons.append(m)
            side.sheet = side.sheet or (not own and spec.get("ability") is not None)
        side.team_size = len(side.mons)
    return state


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

def _on_lead(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    _bring_in(rp, reg, e["side"], int(e["slot"]), _named(rp, e["side"], e["species"]))


def _on_switch(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    m = _named(rp, e["side"], e["species"])
    if m.state == "fainted":
        raise EntryError(f"{m.species} has fainted")
    _bring_in(rp, reg, e["side"], int(e["slot"]), m)


def _bring_in(rp: Replay, reg: Regulation, side: str, slot: int, m: Mon) -> None:
    rp.state.switch_in(side, slot, m, m.forme)
    # What it announces on arrival, and what a standing terrain does to whatever it is holding.
    _ask(rp, reg, m, "switch_in",
         rules.on_switch_in(reg, m.species, rules.still_possible(reg, m)), timed=True)
    if rp.state.terrain:
        _ask(rp, reg, m, "terrain", rules.on_terrain_set(reg, rp.state.terrain, m.item))


def _on_turn(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    rp.state.begin_turn(int(e["n"]))


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


def _on_damage(rp: Replay, reg: Regulation, e: dict[str, Any]) -> None:
    m = _active(rp, e)
    before = m.hp
    _set_hp(rp, m, e)
    rp.state.record_damage(m, before, crit=bool(e.get("crit")))
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
    _active(rp, e).status = status


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
    "lead": _on_lead, "turn": _on_turn, "move": _on_move, "switch": _on_switch,
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
         source: Mon | None = None, *, timed: bool = False) -> None:
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
        id=_qid(rp), kind=kind, prompt=_prompt(kind, mon, source), side=rp.state._side_of(mon),
        species=mon.species, slot=mon.position, outcomes=outcomes,
        source=({"side": rp.state._side_of(source), "slot": source.position,
                 "species": source.species} if source is not None else None),
        forced=len(outcomes) == 1))


def _prompt(kind: str, mon: Mon, source: Mon | None) -> str:
    who = mon.species
    if kind == "switch_in":
        return f"{who} arrives — what announced?"
    if kind == "stat_drop":
        return f"{source.species if source else 'Something'} aims a drop at {who} — what happened?"
    if kind == "on_hit":
        return f"{who} was hit — what announced?"
    if kind == "terrain":
        return f"Terrain is up — did {who} pop an item?"
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

    def at(self, upto: int) -> Replay:
        return replay(self.reg, self.setup, self.journal, upto=upto)

    def to_json(self) -> dict[str, Any]:
        return {"setup": self.setup, "journal": self.journal}

    @classmethod
    def from_json(cls, reg: Regulation, blob: dict[str, Any]) -> "Battle":
        return cls(reg, blob["setup"], blob.get("journal"))

    def dumps(self) -> str:
        return json.dumps(self.to_json(), separators=(",", ":"))
