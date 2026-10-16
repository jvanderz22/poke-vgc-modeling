"""A battle broadcast's events as journal entries: the third layer of the battle reader.

The screen layer (`vgc.video.screen`) gives settled events; `vgc.video.said` reads each message
through the two sheets. This turns them into the entries a person tapping along would make
(`vgc.battle.entry`): leads, turns, moves with their targets and results, damage and healing from
the printed HP, faints, switches, Mega Evolution, field effects and screens, and answers to the
journal's questions from the ability and item banners.

A replay is kept alongside, entry by entry, so every entry is checked against the battle as it
stands: a move by a Pokémon that isn't on the field, or an HP the replay already has, is never
written. What can't be placed is recorded in `missed`, never guessed (PLAN-video decision 3), and an
open question with nothing on screen to settle it is answered "not sure", which concludes nothing.

Every entry carries `t`, the clip time of the event behind it, so a read can be lined up with the
video and with a hand transcription of the same battle (PLAN-video step 3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vgc.battle import entry as journal
from vgc.battle.state import Mon
from vgc.regulation import Regulation, to_id
from vgc.video.said import Said, Vocabulary, Who, banner, best, parse, trainer_id
from vgc.video.screen import Event

# Which player is on which side of the screen, until the leads say otherwise: the watch page gives
# the first sheet it reads to player 1, and the broadcast doesn't have to show the left one first.
SIDE = {"L": "p1", "R": "p2"}
FLIPPED = {"L": "p2", "R": "p1"}

SPREAD = {"allAdjacentFoes", "allAdjacent"}
AIMED = {"normal", "adjacentFoe", "any", "randomNormal", "adjacentAlly", "adjacentAllyOrSelf"}
RESULT_OF = {"protected": "protected", "immune": "immune", "miss": "miss"}

# A banner settles a question raised this long before it, at most.
BANNER_WINDOW_S = 6.0
# The four plates shown at a decision point arrive within this long of its marker.
SNAPSHOT_S = 1.0
# How far behind the frames a settled event can be: it is stamped with when it first showed, and
# reported a couple of frames later.
SETTLE_LAG_S = 0.75
# The plates in the order each side sent its leads out: the right side's run the other way (R1 was
# sent out first in all three games read so far, Louisville r5 and r6 and JET7oW_RiLI game 2).
LEAD_PLATES = {"L": ("L0", "L1"), "R": ("R1", "R0")}


@dataclass
class Assembly:
    entries: list[dict[str, Any]] = field(default_factory=list)
    # What was on screen and couldn't be placed: {t, what, why}.
    missed: list[dict[str, Any]] = field(default_factory=list)
    # The replay's own complaints, if an entry it was given didn't fit after all.
    errors: list[str] = field(default_factory=list)
    # Messages read by a phrasing no footage has confirmed yet: {t, pattern, text}. They were used;
    # they are listed so the first one of each can be checked against the video.
    provisional: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Move:
    t: float
    side: str
    species: str
    move: str
    named: list[tuple[Who, str | None]] = field(default_factory=list)  # (target, result or None)
    damage: list[tuple[float, Who, int]] = field(default_factory=list)
    crit: bool = False
    result: str | None = None  # for the move as a whole: "miss" (its attack missed), "failed"
    self_hp: tuple[float, int] | None = None  # the user's own HP during its move: drain, recoil
    redirected: Who | None = None  # Lightning Rod, Storm Drain: "… took the attack"


class Assembler:
    def __init__(self, reg: Regulation, setup: dict[str, Any]):
        self.reg = reg
        names = setup.get("names") or {}
        # One vocabulary per way round: the screen's left side is player 1's sheet, or player 2's.
        self.vocabs = {flip: Vocabulary.from_sheets(reg.dex, setup[m["L"]], setup[m["R"]],
                                                    {"L": names.get(m["L"]), "R": names.get(m["R"])})
                       for flip, m in ((False, SIDE), (True, FLIPPED))}
        self.side, self.vocab = SIDE, self.vocabs[False]
        # Settled by the first send-out only one way round explains; until then, either could be.
        self.oriented = False
        self.use(journal.Replay(journal.setup_state(reg, setup)))
        self.out = Assembly()
        self.started = False
        self.led: set[str] = set()
        self.turn = 0
        self.pending: _Move | None = None
        self.stone: dict[tuple[str, str], str] = {}
        self.vacated: dict[str, list[int]] = {"p1": [], "p2": []}
        self.banners: list[tuple[float, Said]] = []
        self.asked: dict[str, float] = {}  # question id -> when it was raised
        # A decision point seen and its turn not yet written: the plates that follow it are still
        # coming in, and they are the check on every HP the turn left unexplained.
        self.decision: float | None = None
        self.snapshot: dict[Who, tuple[float, int]] = {}
        # What a message said caused an HP change about to show: (t, who, cause).
        self.hints: list[tuple[float, Who, str]] = []
        # Moves written this turn, to catch the broadcast showing a turn again (an instant replay).
        self.used: set[tuple[str, str, str]] = set()
        self.replaying = False
        # Where the last aimed move landed, for a Pokémon dragged out by Roar or Dragon Tail.
        self.last_target: dict[str, int] | None = None
        # Send-outs with no free slot: a faint the broadcast didn't show. Held for the decision
        # point's plates to say who left.
        self.held: dict[str, list[tuple[float, Who]]] = {"p1": [], "p2": []}
        self.forfeit = False
        self.won: tuple[float, str] | None = None  # (when, winner) once a side has nothing left
        # A decision point seen before both sides' leads were read, and the names on its plates:
        # the leads a missed send-out didn't give (`_lead_from_plates`).
        self.lead_check: float | None = None
        self.lead_plates: dict[str, str | None] = {}

    def use(self, rp: journal.Replay) -> None:
        """Read on from this replay, keeping each stat step it applies (`_boost`)."""
        self.rp = rp
        # Each stat step the replay has applied since the last move that no message has announced
        # yet, by Pokémon: id(mon) -> [(stat, stages)]. Steps, not a net change: Intimidate's -1
        # and Defiant's +2 are two messages, and their net +1 matches neither.
        self.unannounced: dict[int, list[tuple[str, int]]] = {}
        self.announcing = False
        apply = rp.state.apply_boost

        def apply_and_keep(m: Mon, stat: str, stages: int) -> None:
            apply(m, stat, stages)
            if not self.announcing:
                self.unannounced.setdefault(id(m), []).append((stat, stages))

        rp.state.apply_boost = apply_and_keep

    # --- writing ------------------------------------------------------------------------------

    def _write(self, t: float, entry: dict[str, Any]) -> bool:
        """Apply one entry to the replay; keep it only if the replay accepts it."""
        index = len(self.out.entries)
        before = {q.id for q in self.rp.questions}
        try:
            journal._apply_entry(self.rp, self.reg, index, entry)
        except journal.EntryError as e:
            self.out.missed.append({"t": t, "what": entry, "why": str(e)})
            return False
        self.out.entries.append({**entry, "t": round(t, 2)})
        for q in self.rp.questions:
            if q.id not in before:
                self.asked[q.id] = t
        if entry["kind"] != "end" and self.won is None and (out := self._out()) is not None:
            # A side with nothing left has lost, whatever the broadcast shows next (or doesn't:
            # round 5's recording stops before its winner banner). Written once the rest of what
            # is being written (the other targets of a spread move) is in.
            self.won = (t, {"p1": "p2", "p2": "p1"}[out])
        return True

    def _write_won(self) -> None:
        if self.won is not None and not self.rp.state.ended:
            t, winner = self.won
            self._settle_questions(t, all_=True)
            self._write(t, {"kind": "end", "winner": winner})

    def _out(self) -> str | None:
        """The one side that has lost all it brought, if the game is not already over."""
        st = self.rp.state
        if st.ended:
            return None
        out = [sid for sid in ("p1", "p2") if sum(m.state == "fainted" for m in st.sides[sid].mons) >= self.reg.bring]
        return out[0] if len(out) == 1 else None

    def _miss(self, t: float, what: Any, why: str) -> None:
        self.out.missed.append({"t": round(t, 2), "what": what, "why": why})

    def _mon(self, who: Who) -> Mon | None:
        side = self.side[who.side]
        want = self.rp.state._base(who.species)
        return next((m for m in self.rp.state.sides[side].mons if self.rp.state._base(m.species) == want), None)

    def _active(self, who: Who) -> Mon | None:
        m = self._mon(who)
        return m if m is not None and m.state == "active" and m.position is not None else None

    # --- questions ----------------------------------------------------------------------------

    def _settle_questions(self, t: float, *, all_: bool = False) -> None:
        """Answer each open question from a banner that names its answer; answer "not sure" once
        its window has closed with none (or at a turn boundary, `all_`)."""
        for q in list(self.rp.questions):
            raised = self.asked.get(q.id, t)
            pick = None
            for bt, b in self.banners:
                if bt + 0.5 < raised or bt - raised > BANNER_WINDOW_S or not b.who:
                    continue
                if self.side[b.who[0].side] != q.side or to_id(b.who[0].species) != to_id(q.species):
                    continue
                for i, o in enumerate(q.outcomes):
                    if (b.kind == "ability" and o.ability == to_id(b.data["ability"])) or \
                       (b.kind == "item" and o.item == to_id(b.data["item"])):
                        pick = i
                        break
                if pick is not None:
                    break
            if pick is not None or all_ or t - raised > BANNER_WINDOW_S:
                # Species and label ride along, as the Turn Stepper's do, so the log can say what
                # was answered; replay reads only `option`.
                self._write(t, {"kind": "answer", "question": q.id, "option": pick, "species": q.species,
                                "label": None if pick is None else q.outcomes[pick].label})

    # --- moves --------------------------------------------------------------------------------

    def _flush(self, t: float) -> None:
        """Write the move waiting for its results, then what it did."""
        mv, self.pending = self.pending, None
        if mv is None:
            return
        user = self._active(Who(mv.side, mv.species))
        if user is None:
            self._miss(mv.t, f"{mv.species} used {mv.move}", "not on the field")
            return
        side = self.side[mv.side]
        dexmove = self.reg.dex.get_move(mv.move) or {}
        kind = dexmove.get("target")
        entry: dict[str, Any] = {"kind": "move", "side": side, "slot": user.position, "move": mv.move,
                                 "target": None, "spread": kind in SPREAD}
        results = []
        if kind in SPREAD:
            for who, result in mv.named:
                if result and (m := self._active(who)) is not None:
                    results.append({"side": self.side[who.side], "slot": m.position, "result": result})
            if results:
                entry["results"] = results
        elif kind in AIMED:
            # The target is whoever the game named after the move, or failing that the one Pokémon
            # whose HP the move changed. Neither: the target is left unsaid.
            named = [(w, r) for w, r in mv.named if self._active(w) is not None]
            if mv.redirected is not None and self._active(mv.redirected) is not None:
                named.insert(0, (mv.redirected, None))
            hit = [w for _, w, _ in mv.damage if w != Who(mv.side, mv.species) and self._active(w) is not None]
            target = named[0][0] if named else (hit[0] if len(set(hit)) == 1 else None)
            if target is not None:
                m = self._active(target)
                entry["target"] = {"side": self.side[target.side], "slot": m.position}
                result = next((r for w, r in named if w == target and r), None)
                if result:
                    entry["result"] = result
                self.last_target = {"side": self.side[target.side], "slot": m.position}
        if mv.result and "result" not in entry and not results:
            entry["result"] = mv.result
        unannounced, self.unannounced = self.unannounced, {}
        if not self._write(mv.t, entry):
            self.unannounced = unannounced
            return
        self.used.add((mv.side, mv.species, mv.move))
        for dt, who, hp in mv.damage:
            self._hp(dt, who, hp, crit=mv.crit, by_move=True)
        if mv.self_hp is not None:
            # The user's own HP moving during its move: drained HP back, recoil, Life Orb. The
            # rules work most of it out already; `_hp` writes only what they didn't.
            dt, hp = mv.self_hp
            me = Who(mv.side, mv.species)
            self._hp(dt, me, hp, cause=self._cause(dt, me) or "own move")

    def _hp(self, t: float, who: Who, hp: int, *, crit: bool = False, by_move: bool = False,
            cause: str | None = None) -> None:
        m = self._active(who)
        if m is None:
            return
        now = round(m.hp * 100)
        if abs(now - hp) <= 1 and not (hp == 0 and m.hp > 0):
            return  # the replay has it already: a derived recoil, a plate shown again
        side, slot = self.side[who.side], m.position
        if hp < now:
            e = {"kind": "damage", "side": side, "slot": slot, "pct": hp, "fainted": hp == 0}
            if crit:
                e["crit"] = True
            if not by_move:
                # Not the last move's doing: Rocky Helmet, recoil, weather. Marking it so keeps it
                # out of the damage channel's reading of that move.
                e["eot"] = cause or "other"
            self._write(t, e)
        else:
            self._write(t, {"kind": "heal", "side": side, "slot": slot, "pct": hp,
                            **({"eot": cause} if cause else {})})

    # --- events -------------------------------------------------------------------------------

    def _close_decision(self) -> None:
        """Write the turn the last decision point started, after reconciling its four plates: an
        HP that changed during the turn with nothing on screen to explain it is written here, once
        a still frame has shown it, and not before."""
        if self.decision is None:
            return
        t, self.decision = self.decision, None
        self._release_held()
        for who, (pt, hp) in self.snapshot.items():
            self._hp(pt, who, hp, cause=self._cause(pt, who) or "unexplained")
        self.snapshot = {}
        self._settle_questions(t, all_=True)
        self.used, self.replaying = set(), False
        self.turn += 1
        self._write(t, {"kind": "turn", "n": self.turn})

    def _release_held(self) -> None:
        """Place the send-outs that had no slot. The still plates name the two on each side; one the
        replay has active that isn't among them left unseen, and with no withdrawal read it fainted."""
        for side, held in self.held.items():
            if not held:
                continue
            shown = {self.rp.state._base(w.species) for w in self.snapshot if self.side[w.side] == side}
            gone = [m for m in self.rp.state.sides[side].mons
                    if m.state == "active" and m.position is not None and self.rp.state._base(m.species) not in shown]
            for t, who in held:
                if gone and self.rp.state._base(who.species) in shown:
                    m = gone.pop(0)
                    slot = m.position
                    self._write(t, {"kind": "faint", "side": side, "slot": slot})
                    self._miss(t, f"{m.species} fainted", "not shown: worked out from who was sent out")
                    self._write(t, {"kind": "switch", "side": side, "slot": slot, "species": who.species})
                else:
                    self._miss(t, f"{who.species} sent out", "no free slot, and the plates don't say whose")
            self.held[side] = []

    def feed(self, e: Event) -> None:
        self._write_won()
        if self.lead_check is not None and not (e.kind == "plate" and e.t - self.lead_check <= SNAPSHOT_S):
            self._lead_from_plates()
        if self.decision is not None and not (e.kind == "plate" and e.t - self.decision <= SNAPSHOT_S):
            self._close_decision()
        if e.kind == "message":
            self._message(e.t, e.data["text"])
        elif e.kind == "banner":
            b = banner(e.data["text"], e.data["side"], self.vocab, self.reg.dex)
            if b.kind == "unknown":
                if self.started:
                    self._miss(e.t, e.data["text"], "banner not understood")
                return
            self.banners.append((e.t, b))
            self._settle_questions(e.t)
        elif e.kind == "plate" and self.started and e.data.get("hp") is not None:
            side = e.data["slot"][0]
            name = e.data.get("name")
            sp = None
            if name:
                sp = best(name, self.vocab.names[side])
            if sp is None:
                self._miss(e.t, e.data, "plate name not on the sheet")
                return
            who = Who(side, sp)
            if self.decision is not None:
                self.snapshot[who] = (e.t, e.data["hp"])
            elif self.pending is not None and who != Who(self.pending.side, self.pending.species):
                self.pending.damage.append((e.t, who, e.data["hp"]))
            elif self.pending is not None:
                self.pending.self_hp = (e.t, e.data["hp"])
            elif (cause := self._cause(e.t, who)) is not None:
                self._hp(e.t, who, e.data["hp"], cause=cause)
            # Otherwise nothing on screen says why it changed (a plate sliding past during a
            # switch reads "dee 10%"): the next decision point's still plates settle it.
        elif e.kind == "plate" and self.lead_check is not None:
            self.lead_plates[e.data["slot"]] = e.data.get("name")
        elif e.kind == "choosing" and self.started:
            self._flush(e.t)
            self.decision = e.t
        elif e.kind == "choosing":
            self.lead_check, self.lead_plates = e.t, {}

    def _lead_from_plates(self) -> None:
        """Lead from a decision point's plates for a side whose send-out wasn't read. A message is
        up for a couple of seconds, and a slow enough frame rate reads it once or not at all; until
        both sides have led, nothing after is read, so one missed send-out lost a whole game. The
        plates show every lead at the first decision point and stay up while the players choose."""
        t, plates = self.lead_check, self.lead_plates
        self.lead_check, self.lead_plates = None, {}
        assert t is not None
        if not self.oriented:
            # Which sheet is on the left, from the names: settled only if just one way round reads
            # every plate (a species on both sheets reads either way).
            fits = [flip for flip in (False, True)
                    if plates and all(n and best(n, self.vocabs[flip].names[s[0]]) for s, n in plates.items())]
            if len(fits) != 1:
                return
            self.oriented = True
            self.side, self.vocab = (FLIPPED, self.vocabs[True]) if fits[0] else (SIDE, self.vocabs[False])
        before = {q.id for q in self.rp.questions}
        for screen, slots in LEAD_PLATES.items():
            side = self.side[screen]
            names = [plates.get(s) for s in slots]
            species = [best(n, self.vocab.names[screen]) if n else None for n in names]
            if side in self.led or None in species or species[0] == species[1]:
                continue
            for slot, sp in enumerate(species):
                self._write(t, {"kind": "lead", "side": side, "slot": slot, "species": sp})
            self.led.add(side)
            self._miss(t, f"{species[0]} and {species[1]} sent out", "not read: the leads were taken from the plates")
        # Their switch-in abilities announced before this decision point: a banner since the last
        # message read can still answer them.
        for q in self.rp.questions:
            if q.id not in before:
                self.asked[q.id] = t - BANNER_WINDOW_S
        self.started = self.led == {"p1", "p2"}
        if self.started:
            self.decision = t
            self._close_decision()

    def tick(self, t: float) -> None:
        """The frames have reached `t`. Read live, a decision point is closed (its turn written)
        once its plates have had time to come in, not when the next message comes, which is after
        the players have chosen."""
        if self.lead_check is not None and t - self.lead_check > SNAPSHOT_S + SETTLE_LAG_S:
            self._lead_from_plates()
        if self.decision is not None and t - self.decision > SNAPSHOT_S + SETTLE_LAG_S:
            self._close_decision()
        if self.won is not None and t - self.won[0] > SETTLE_LAG_S:
            self._write_won()

    def _cause(self, t: float, who: Who) -> str | None:
        """What a message or banner near it says changed this HP: "sandstorm", "rockyhelmet"."""
        for ht, hw, cause in reversed(self.hints):
            if hw == who and abs(t - ht) <= 4:
                return cause
        for bt, b in reversed(self.banners):
            if t - bt > 4:
                break
            if b.kind == "item":
                return to_id(b.data["item"])
            if b.kind == "ability":
                return to_id(b.data["ability"])
        return None

    def _message(self, t: float, text: str) -> None:
        if text.upper().startswith("GAME WINNER"):
            return self._end(t, text)
        s = parse(text, self.vocab)
        if not self.oriented:
            s = self._orient(text, s)
        if not self.started and s.kind != "sendout":
            return  # the sheets and the desk before the battle
        if self.replaying:
            return
        if s.kind == "unknown" and sum(c.isascii() and c.isalpha() for c in text) < 8:
            # A line of a few letters is OCR catching an effect or a fragment, not a message: it
            # doesn't end the move before it, whose results may still be coming in.
            return self._miss(t, text, "noise")
        if s.provisional:
            self.out.provisional.append({"t": round(t, 2), "pattern": s.pattern, "text": text})
        key = (s.who[0].side, s.who[0].species, s.data["move"]) if s.kind == "move" else None
        waiting = (self.pending.side, self.pending.species, self.pending.move) if self.pending else None
        if key is not None and (key in self.used or key == waiting):
            # The same Pokémon using the same move twice in a turn is a turn shown again. Nothing
            # more is read until the next decision point, whose plates put the HP right.
            self._flush(t)
            self.replaying = True
            return self._miss(t, text, "the same move twice in a turn: an instant replay?")
        pending = self.pending
        if pending is not None:
            if s.kind in ("effective", "protected", "guarded", "immune", "miss"):
                pending.named.append((s.who[0], RESULT_OF.get(s.kind, "protected" if s.kind == "guarded" else None)))
                return
            if s.kind == "crit":
                pending.crit = True
                return
            if s.kind == "missed":
                pending.result = "miss"
                return
            if s.kind == "failed":
                pending.result = "failed"
                return
            if s.kind == "redirect":
                pending.redirected = s.who[0]
                return
            if s.kind in ("drain", "multihit", "endured", "feint"):
                return  # what follows on the plates says it; nothing to log
        self._flush(t)
        if s.kind == "sendout":
            self._sendout(t, s)
        elif s.kind == "withdraw":
            m = self._active(s.who[0])
            if m is not None:
                self.vacated[self.side[s.who[0].side]].append(m.position)
        elif s.kind == "move":
            self.pending = _Move(t, s.who[0].side, s.who[0].species, s.data["move"])
        elif s.kind == "faint":
            m = self._active(s.who[0])
            if m is not None:
                pos = m.position
                if m.hp > 0:
                    self._write(t, {"kind": "faint", "side": self.side[s.who[0].side], "slot": pos})
                self.vacated[self.side[s.who[0].side]].append(pos)
        elif s.kind == "boost":
            for who in s.who:
                self._boost(t, who, s.data["stats"], s.data["stages"])
        elif s.kind == "mega_stone":
            self.stone[(s.who[0].side, s.who[0].species)] = s.data.get("item") or ""
        elif s.kind == "mega":
            self._mega(t, s.who[0])
        elif s.kind == "field":
            what = next(k for k in ("weather", "terrain", "pseudo") if k in s.data)
            value = to_id(s.data[what])
            now = (value in self.rp.state.pseudo) if what == "pseudo" else getattr(self.rp.state, what) == value
            # An ability or a move may already have set it (Psychic Surge answered, Trick Room
            # logged); saying it again would ask the terrain seeds a second time.
            if now != s.data["on"]:
                self._write(t, {"kind": "field", "what": what, "value": s.data[what], "on": s.data["on"]})
        elif s.kind in ("status", "status_end", "cant"):
            m = self._active(s.who[0])
            if m is None:
                return self._miss(t, text, "not on the field")
            side = self.side[s.who[0].side]
            if s.kind == "cant":
                self._write(t, {"kind": "cant", "side": side, "slot": m.position, "reason": s.data["reason"]})
            elif (m.status or None) != s.data.get("status"):
                self._write(t, {"kind": "status", "side": side, "slot": m.position, "status": s.data.get("status")})
        elif s.kind in ("chip", "heal"):
            # What moves the HP is the plate that follows; the message says why.
            self.hints.append((t, s.who[0], s.data["cause"]))
            if s.kind == "heal" and s.data["cause"] == "hospitality":
                # The host's ability announcing on arrival, said as a message: it answers the
                # switch-in question as a banner would, and when is Speed evidence.
                self.banners.append((t, Said("ability", s.who[1:], {"ability": "Hospitality"}, text, s.pattern)))
                self._settle_questions(t)
        elif s.kind == "consume":
            self.hints.append((t, s.who[0], to_id(s.data["item"])))
            self._write(t, {"kind": "consume", "side": self.side[s.who[0].side], "species": s.who[0].species,
                            "item": s.data["item"]})
        elif s.kind == "knocked_off":
            # The target's item is gone. The journal's word for an item that is gone is "consumed".
            target = s.who[1]
            self._write(t, {"kind": "consume", "side": self.side[target.side], "species": target.species,
                            "item": s.data["item"]})
        elif s.kind == "trick":
            self._miss(t, text, "Trick: which two swapped items isn't read yet")
        elif s.kind == "swap":
            m = self._active(s.who[0])
            if m is None:
                return self._miss(t, text, "not on the field")
            self._write(t, {"kind": "swap", "side": self.side[s.who[0].side], "slot": m.position})
        elif s.kind == "dragged":
            side = self.side[s.who[0].side]
            if self.last_target is None or self.last_target["side"] != side:
                return self._miss(t, text, "dragged out, but in place of whom wasn't read")
            self._write(t, {"kind": "switch", "side": side, "slot": self.last_target["slot"],
                            "species": s.who[0].species})
        elif s.kind == "forfeit_notice":
            self.forfeit = True
        elif s.kind == "lost":
            self._close_decision()
            self._settle_questions(t, all_=True)
            self._write(t, {"kind": "end", "winner": self.side[s.data["winner"]],
                            **({"by": "forfeit"} if self.forfeit else {})})
        elif s.kind == "forfeit":
            self._close_decision()
            self._settle_questions(t, all_=True)
            self._write(t, {"kind": "end", "winner": self.side.get(s.data.get("winner")), "by": "forfeit"})
        elif s.kind == "side":
            side, cond = self.side[s.data["side"]], s.data["condition"]
            if (to_id(cond) in self.rp.state.sides[side].conditions) != s.data["on"]:
                self._write(t, {"kind": "side", "side": side, "condition": cond, "on": s.data["on"]})
        elif s.kind == "unknown":
            self._miss(t, text, "message not understood")
        # recoil, center, helping: the rules derive what they do, or there is nothing to log. An
        # aura announces a Mega's ability, which the Mega forme already gives.

    def _orient(self, text: str, s: Said) -> Said:
        """Which sheet is on the left, from the Pokémon on screen: the names on the sheets and the
        names on screen needn't agree, and the order the sheets were read in says nothing. The
        first message only one way round can read settles it: the leads, usually, or the first
        move named after a reading starts mid-game."""
        flipped = parse(text, self.vocabs[True])
        here, there = s.kind != "unknown", flipped.kind != "unknown"
        if here == there:
            return s  # neither, or both (no names, or a species on both sheets): wait for the next
        self.oriented = True
        if there:
            self.side, self.vocab = FLIPPED, self.vocabs[True]
            return flipped
        return s

    def _sendout(self, t: float, s: Said) -> None:
        if s.data.get("trainer"):
            # What the game calls the right player ("The Trainer", "Alex™"), which the setup's
            # names needn't match: a forfeit's "… lost!" will use it.
            for v in self.vocabs.values():
                v.trainers[trainer_id(s.data["trainer"])] = "R"
        side = self.side[s.who[0].side]
        if not self.started:
            if side in self.led:
                return
            for slot, who in enumerate(s.who):
                self._write(t, {"kind": "lead", "side": side, "slot": slot, "species": who.species})
            self.led.add(side)
            self.started = self.led == {"p1", "p2"}
            return
        for who in s.who:
            free = self.vacated[side]
            slot = free.pop(0) if free else next((x for x in (0, 1) if self.rp.state.at(side, x) is None), None)
            if slot is None:
                self.held[side].append((t, who))
                continue
            self._write(t, {"kind": "switch", "side": side, "slot": slot, "species": who.species})

    def _boost(self, t: float, who: Who, stats: list[str], stages: int) -> None:
        """A stat change the rules didn't already make. Each message is matched to the first step
        the replay applied that no message has announced, of the same stat and direction, so Close
        Combat's own drops aren't counted twice, nor Defiant's rise after Intimidate."""
        m = self._active(who)
        if m is None:
            return
        steps = self.unannounced.get(id(m), [])
        for stat in stats:
            rest = stages
            i = next((i for i, (s, n) in enumerate(steps) if s == stat and (n > 0) == (stages > 0)), None)
            if i is not None:
                rest -= steps.pop(i)[1]
            if rest:
                self.announcing = True
                try:
                    self._write(t, {"kind": "boost", "side": self.side[who.side], "slot": m.position,
                                    "stat": stat, "stages": rest})
                finally:
                    self.announcing = False

    def _mega(self, t: float, who: Who) -> None:
        m = self._mon(who)
        item = self.stone.get((who.side, who.species)) or (m.item if m else "")
        forme = self.reg.dex.mega_forme(to_id(who.species), to_id(item or ""))
        if forme is None:
            self._miss(t, f"{who.species} Mega Evolved", f"no Mega forme for {item or 'no stone'}")
            return
        self._write(t, {"kind": "mega", "side": self.side[who.side], "species": who.species,
                        "forme": forme["name"], "item": item})

    def _end(self, t: float, text: str) -> None:
        if self.rp.state.ended or self.won is not None:
            return  # the board said so first
        self._close_decision()
        self._flush(t)
        self._settle_questions(t, all_=True)
        # A side wiped out has already ended the game (`_write`), so this is a game the board left
        # open. The broadcast's name for the winner needn't be the name on the sheet: a name that
        # matches neither, or both, leaves the winner unsaid.
        name = text.upper().replace("GAME WINNER", "").strip()
        names = {sid: (self.rp.state.sides[sid].name or "").upper() for sid in ("p1", "p2")}
        by_name = [sid for sid, n in names.items() if n and name and (n in name or name in n)]
        winner = by_name[0] if len(by_name) == 1 else None
        self._write(t, {"kind": "end", "winner": winner})

    def finish(self, t: float) -> Assembly:
        if self.lead_check is not None:
            self._lead_from_plates()
        self._close_decision()
        self._flush(t)
        self._write_won()
        self._settle_questions(t, all_=True)
        self.out.errors = list(self.rp.errors)
        return self.out


def assemble(reg: Regulation, setup: dict[str, Any], events: list[Event]) -> Assembly:
    """Journal entries for a Watching battle from a broadcast's events. `setup` is the battle's
    (perspective "spectator", `p1` the left player's six, `p2` the right's, `names`)."""
    a = Assembler(reg, setup)
    for e in sorted(events, key=lambda e: e.t):
        a.feed(e)
    return a.finish(events[-1].t if events else 0.0)
