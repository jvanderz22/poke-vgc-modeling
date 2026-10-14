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
from vgc.video.said import Said, Vocabulary, Who, banner, best, parse
from vgc.video.screen import Event

# The left player is P1: the watch page makes the left player's sheet player 1's.
SIDE = {"L": "p1", "R": "p2"}

SPREAD = {"allAdjacentFoes", "allAdjacent"}
AIMED = {"normal", "adjacentFoe", "any", "randomNormal", "adjacentAlly", "adjacentAllyOrSelf"}
RESULT_OF = {"protected": "protected", "immune": "immune", "miss": "miss"}

# A banner settles a question raised this long before it, at most.
BANNER_WINDOW_S = 6.0
# The four plates shown at a decision point arrive within this long of its marker.
SNAPSHOT_S = 1.0


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
    def __init__(self, reg: Regulation, setup: dict[str, Any], vocab: Vocabulary):
        self.reg, self.vocab = reg, vocab
        self.rp = journal.Replay(journal.setup_state(reg, setup))
        self.out = Assembly()
        self.started = False
        self.led: set[str] = set()
        self.turn = 0
        self.pending: _Move | None = None
        self.stone: dict[tuple[str, str], str] = {}
        self.vacated: dict[str, list[int]] = {"p1": [], "p2": []}
        self.banners: list[tuple[float, Said]] = []
        self.asked: dict[str, float] = {}  # question id -> when it was raised
        self.boosts_before: dict[int, dict[str, int]] = {}
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
        return True

    def _miss(self, t: float, what: Any, why: str) -> None:
        self.out.missed.append({"t": round(t, 2), "what": what, "why": why})

    def _mon(self, who: Who) -> Mon | None:
        side = SIDE[who.side]
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
                if SIDE[b.who[0].side] != q.side or to_id(b.who[0].species) != to_id(q.species):
                    continue
                for i, o in enumerate(q.outcomes):
                    if (b.kind == "ability" and o.ability == to_id(b.data["ability"])) or \
                       (b.kind == "item" and o.item == to_id(b.data["item"])):
                        pick = i
                        break
                if pick is not None:
                    break
            if pick is not None or all_ or t - raised > BANNER_WINDOW_S:
                self._write(t, {"kind": "answer", "question": q.id, "option": pick})

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
        side = SIDE[mv.side]
        dexmove = self.reg.dex.get_move(mv.move) or {}
        kind = dexmove.get("target")
        entry: dict[str, Any] = {"kind": "move", "side": side, "slot": user.position, "move": mv.move,
                                 "target": None, "spread": kind in SPREAD}
        results = []
        if kind in SPREAD:
            for who, result in mv.named:
                if result and (m := self._active(who)) is not None:
                    results.append({"side": SIDE[who.side], "slot": m.position, "result": result})
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
                entry["target"] = {"side": SIDE[target.side], "slot": m.position}
                result = next((r for w, r in named if w == target and r), None)
                if result:
                    entry["result"] = result
                self.last_target = {"side": SIDE[target.side], "slot": m.position}
        if mv.result and "result" not in entry and not results:
            entry["result"] = mv.result
        mons = {id(m): dict(m.boosts) for s in ("p1", "p2") for m in self.rp.state.sides[s].mons}
        if not self._write(mv.t, entry):
            return
        self.boosts_before = mons
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
        side, slot = SIDE[who.side], m.position
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
            shown = {self.rp.state._base(w.species) for w in self.snapshot if SIDE[w.side] == side}
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
        elif e.kind == "choosing" and self.started:
            self._flush(e.t)
            self.decision = e.t

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
        if not self.started and s.kind != "sendout":
            return  # the sheets and the desk before the battle
        if self.replaying:
            return
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
                self.vacated[SIDE[s.who[0].side]].append(m.position)
        elif s.kind == "move":
            self.pending = _Move(t, s.who[0].side, s.who[0].species, s.data["move"])
        elif s.kind == "faint":
            m = self._active(s.who[0])
            if m is not None:
                pos = m.position
                if m.hp > 0:
                    self._write(t, {"kind": "faint", "side": SIDE[s.who[0].side], "slot": pos})
                self.vacated[SIDE[s.who[0].side]].append(pos)
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
            side = SIDE[s.who[0].side]
            if s.kind == "cant":
                self._write(t, {"kind": "cant", "side": side, "slot": m.position, "reason": s.data["reason"]})
            elif (m.status or None) != s.data.get("status"):
                self._write(t, {"kind": "status", "side": side, "slot": m.position, "status": s.data.get("status")})
        elif s.kind == "chip":
            self.hints.append((t, s.who[0], s.data["cause"]))
        elif s.kind == "consume":
            self.hints.append((t, s.who[0], to_id(s.data["item"])))
            self._write(t, {"kind": "consume", "side": SIDE[s.who[0].side], "species": s.who[0].species,
                            "item": s.data["item"]})
        elif s.kind == "knocked_off":
            # The target's item is gone. The journal's word for an item that is gone is "consumed".
            target = s.who[1]
            self._write(t, {"kind": "consume", "side": SIDE[target.side], "species": target.species,
                            "item": s.data["item"]})
        elif s.kind == "trick":
            self._miss(t, text, "Trick: which two swapped items isn't read yet")
        elif s.kind == "swap":
            m = self._active(s.who[0])
            if m is None:
                return self._miss(t, text, "not on the field")
            self._write(t, {"kind": "swap", "side": SIDE[s.who[0].side], "slot": m.position})
        elif s.kind == "dragged":
            side = SIDE[s.who[0].side]
            if self.last_target is None or self.last_target["side"] != side:
                return self._miss(t, text, "dragged out, but in place of whom wasn't read")
            self._write(t, {"kind": "switch", "side": side, "slot": self.last_target["slot"],
                            "species": s.who[0].species})
        elif s.kind == "forfeit_notice":
            self.forfeit = True
        elif s.kind == "lost":
            self._close_decision()
            self._settle_questions(t, all_=True)
            self._write(t, {"kind": "end", "winner": SIDE[s.data["winner"]],
                            **({"by": "forfeit"} if self.forfeit else {})})
        elif s.kind == "forfeit":
            self._close_decision()
            self._settle_questions(t, all_=True)
            self._write(t, {"kind": "end", "winner": SIDE.get(s.data.get("winner")), "by": "forfeit"})
        elif s.kind == "side":
            side, cond = SIDE[s.data["side"]], s.data["condition"]
            if (to_id(cond) in self.rp.state.sides[side].conditions) != s.data["on"]:
                self._write(t, {"kind": "side", "side": side, "condition": cond, "on": s.data["on"]})
        elif s.kind == "unknown":
            # A line of a few letters is OCR catching an effect or a fragment, not a message.
            letters = sum(c.isascii() and c.isalpha() for c in text)
            self._miss(t, text, "message not understood" if letters >= 8 else "noise")
        # recoil, center, helping: the rules derive what they do, or there is nothing to log.

    def _sendout(self, t: float, s: Said) -> None:
        side = SIDE[s.who[0].side]
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
        """A stat change the rules didn't already make: what the replay applied since the last
        move is subtracted, so Close Combat's own drops aren't counted twice."""
        m = self._active(who)
        if m is None:
            return
        before = self.boosts_before.get(id(m), {})
        for stat in stats:
            done = m.boosts.get(stat, 0) - before.get(stat, 0)
            rest = stages - done if (stages > 0) == (done > 0) or done == 0 else stages
            if rest:
                self._write(t, {"kind": "boost", "side": SIDE[who.side], "slot": m.position,
                                "stat": stat, "stages": rest})
            before[stat] = m.boosts.get(stat, 0)
        self.boosts_before[id(m)] = before

    def _mega(self, t: float, who: Who) -> None:
        m = self._mon(who)
        item = self.stone.get((who.side, who.species)) or (m.item if m else "")
        forme = self.reg.dex.mega_forme(to_id(who.species), to_id(item or ""))
        if forme is None:
            self._miss(t, f"{who.species} Mega Evolved", f"no Mega forme for {item or 'no stone'}")
            return
        self._write(t, {"kind": "mega", "side": SIDE[who.side], "species": who.species,
                        "forme": forme["name"], "item": item})

    def _end(self, t: float, text: str) -> None:
        self._close_decision()
        self._flush(t)
        self._settle_questions(t, all_=True)
        name = text.upper().replace("GAME WINNER", "").strip()
        names = {sid: (self.rp.state.sides[sid].name or "").upper() for sid in ("p1", "p2")}
        winner = next((sid for sid, n in names.items() if n and (n in name or name in n)), None)
        self._write(t, {"kind": "end", "winner": winner})

    def finish(self, t: float) -> Assembly:
        self._close_decision()
        self._flush(t)
        self._settle_questions(t, all_=True)
        self.out.errors = list(self.rp.errors)
        return self.out


def assemble(reg: Regulation, setup: dict[str, Any], events: list[Event]) -> Assembly:
    """Journal entries for a Watching battle from a broadcast's events. `setup` is the battle's
    (perspective "spectator", `p1` the left player's six, `p2` the right's, `names`)."""
    names = setup.get("names") or {}
    vocab = Vocabulary.from_sheets(reg.dex, setup["p1"], setup["p2"], {"L": names.get("p1"), "R": names.get("p2")})
    a = Assembler(reg, setup, vocab)
    for e in sorted(events, key=lambda e: e.t):
        a.feed(e)
    return a.finish(events[-1].t if events else 0.0)
