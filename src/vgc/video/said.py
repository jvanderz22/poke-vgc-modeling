"""What a battle message says, in the closed vocabulary of the two sheets.

The second layer of the battle reader (PLAN-video step 2). A message from the screen layer is
matched against the game's own phrasing and its names against the twelve Pokémon on the two
sheets, their moves and their items. Anything that doesn't match is `unknown` and is kept as such,
never forced onto the nearest phrasing or name (PLAN-video decision 3).

**Every phrasing is either confirmed or provisional.** A confirmed one has been seen in broadcast
footage (docs/video-findings.md, "Messages"). A provisional one is the main series' wording, which
Champions has changed in places ("It's extremely effective", "The battlefield got weird!"), so it
reads but is reported for review (`Said.provisional`, `vgc.video.review`) until footage confirms it.

Sides are the screen's: the game is shown from the left player's side, so a bare name is the left
side's ("L") and "the opposing …" is the right side's ("R").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from vgc.regulation import Dex, to_id

# A name read with an OCR slip still commits at this ratio, if no other name is as close.
MIN_RATIO = 0.84
MARGIN = 0.08

STATS = {"attack": "atk", "defense": "def", "sp. atk": "spa", "sp. def": "spd", "speed": "spe",
         "accuracy": "accuracy", "evasiveness": "evasion"}
STAGES = {"rose": 1, "rosesharply": 2, "rosedrastically": 3, "fell": -1, "harshlyfell": -2, "severelyfell": -3}

# The phrasings seen in the Louisville Regionals footage (docs/video-findings.md). Everything else
# in this module is provisional until a game shows it.
CONFIRMED = {
    "move", "sendout_L", "sendout_R", "withdraw_L", "went_back", "faint", "effective", "protected",
    "miss", "boost", "mega", "mega_stone", "center", "helping", "crit", "charge", "drain", "herb",
    "knocked_off", "chip:recoil", "chip:sandstorm", "status:par", "aura",
    "field:Psychic Terrain:on", "field:Psychic Terrain:off", "field:Electric Terrain:on",
    "field:RainDance:on", "field:RainDance:off", "field:Sandstorm:on", "side:Light Screen:on",
    "banner:item", "banner:ability", "forfeit_notice", "lost", "hospitality",
}

# What starts and ends each field effect, as the game says it.
FIELD_STARTS = {
    "It started to rain!": ("weather", "RainDance"),
    "The sunlight turned harsh!": ("weather", "SunnyDay"),
    "The sunlight turned extremely harsh!": ("weather", "DesolateLand"),
    "A sandstorm kicked up!": ("weather", "Sandstorm"),
    "It started to snow!": ("weather", "Snow"),
    "The battlefield got weird!": ("terrain", "Psychic Terrain"),
    "An electric current ran across the battlefield!": ("terrain", "Electric Terrain"),
    "Grass grew to cover the battlefield!": ("terrain", "Grassy Terrain"),
    "Mist swirled around the battlefield!": ("terrain", "Misty Terrain"),
    "twisted the dimensions!": ("pseudo", "Trick Room"),
}
FIELD_ENDS = {
    "The rain stopped.": ("weather", "RainDance"),
    "The harsh sunlight faded.": ("weather", "SunnyDay"),
    "The sandstorm subsided.": ("weather", "Sandstorm"),
    "The snow stopped.": ("weather", "Snow"),
    "The weirdness disappeared from the battlefield!": ("terrain", "Psychic Terrain"),
    "The electricity disappeared from the battlefield.": ("terrain", "Electric Terrain"),
    "The grass disappeared from the battlefield.": ("terrain", "Grassy Terrain"),
    "The mist disappeared from the battlefield.": ("terrain", "Misty Terrain"),
    "The twisted dimensions returned to normal!": ("pseudo", "Trick Room"),
}

# Champions words paralysis its own way: "is paralyzed, so it may be unable to move!"
STATUS_GAINED = {"was burned": "brn", "is paralyzed, so it may be unable to move": "par",
                 "was poisoned": "psn", "was badly poisoned": "tox", "fell asleep": "slp",
                 "was frozen solid": "frz"}
CANT = {"flinched and couldn't move": "flinch", "is paralyzed! It can't move": "par",
        "is fast asleep": "slp", "is frozen solid": "frz", "must recharge": "recharge"}
CHIP = {"is hurt by its burn": "brn", "was hurt by poison": "psn", "is buffeted by the sandstorm": "sandstorm",
        "lost some of its HP": "lifeorb", "was damaged by the recoil": "recoil",
        "hurt itself in its confusion": "confusion"}


@dataclass(frozen=True)
class Who:
    side: str  # "L" | "R"
    species: str  # the sheet's species, e.g. "Indeedee-F"


@dataclass(frozen=True)
class Said:
    kind: str
    who: tuple[Who, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    pattern: str | None = None  # which phrasing matched

    @property
    def provisional(self) -> bool:
        """Matched a phrasing no footage has shown yet: worth a look when it first turns up."""
        return self.pattern is not None and self.pattern not in CONFIRMED


@dataclass
class Vocabulary:
    """The names a game's messages can use: each side's six, as the screen prints them."""

    # side -> {printed name id -> sheet species}; "Indeedee" prints for Indeedee-F
    names: dict[str, dict[str, str]]
    moves: dict[str, str]  # id -> move name, for every move on either sheet
    items: dict[str, str]
    trainers: dict[str, str] = field(default_factory=dict)  # name id -> side, for a forfeit
    # A Pokémon whose sheet was read with fewer than four moves: every move it can learn.
    open_moves: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)

    @classmethod
    def from_sheets(cls, dex: Dex, left: list[dict[str, Any]], right: list[dict[str, Any]],
                    names: dict[str, str] | None = None) -> "Vocabulary":
        """`left` and `right` are sheet entries (species, ability, nature, item, moves)."""
        printed: dict[str, dict[str, str]] = {"L": {}, "R": {}}
        moves, items, open_moves = {}, {}, {}
        for side, sheet in (("L", left), ("R", right)):
            for e in sheet:
                if not e.get("species"):
                    continue
                sp = dex.get_species(e["species"]) or {"name": e["species"]}
                base = sp.get("baseSpecies") or sp["name"]
                printed[side][to_id(base)] = sp["name"]
                printed[side][to_id(sp["name"])] = sp["name"]
                known = [m for m in e.get("moves") or [] if m]
                for m in known:
                    moves[to_id(m)] = m
                if len(known) < 4:
                    open_moves[(side, sp["name"])] = {
                        k: dex.moves[k]["name"] for k in sp.get("moves", []) if k in dex.moves}
                if e.get("item"):
                    items[to_id(e["item"])] = e["item"]
        trainers = {trainer_id(n): side for side, n in (names or {}).items() if n}
        return cls(printed, moves, items, trainers, open_moves)


def trainer_id(name: str) -> str:
    """A trainer's name as an id, without the ™ the game prints after it (which OCR may read as
    "TM")."""
    return to_id(re.sub(r"\s*(?:™|TM)$", "", name.strip()))


def trainer_side(name: str, vocab: Vocabulary) -> str | None:
    """The screen side a printed trainer name is: the name the right player's send-outs used, or
    a player's name from the setup. "The Trainer" is always the right player."""
    key = trainer_id(name)
    if key == "thetrainer":
        return "R"
    return best(key, vocab.trainers) if vocab.trainers else None


def best(text: str, table: dict[str, str]) -> str | None:
    """The one entry `text` reads as: exact, or close and clear of the runner-up."""
    key = to_id(text)
    if key in table:
        return table[key]
    scored = sorted(((SequenceMatcher(None, key, k).ratio(), k) for k in table), reverse=True)
    if not scored or scored[0][0] < MIN_RATIO:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < MARGIN and table[scored[0][1]] != table[scored[1][1]]:
        return None
    return table[scored[0][1]]


_best = best  # the name the assembler first imported it under


def _who(text: str, vocab: Vocabulary) -> Who | None:
    """'the opposing Raichu', 'Archaludon the Great Friend', 'Gardevoir' as a side and a species."""
    t = text.strip()
    side = "L"
    m = re.match(r"(?i)^the opposing\s+", t)
    if m:
        side, t = "R", t[m.end():]
    table = vocab.names[side]
    # A title follows the name ("Archaludon the Great Friend"), so try the whole, then the part
    # before " the ".
    for candidate in (t, re.split(r"\s+the\s+", t, maxsplit=1)[0]):
        if (sp := best(candidate, table)) is not None:
            return Who(side, sp)
    return None


def _pair(text: str, vocab: Vocabulary, side: str | None = None) -> tuple[Who, ...] | None:
    """'A and B' or 'A', each resolved; None if any part doesn't resolve."""
    out = []
    for p in re.split(r"\s+and\s+", text.strip()):
        w = _who(("the opposing " if side == "R" else "") + p, vocab)
        if w is None:
            return None
        out.append(w)
    return tuple(out)


def _bare(text: str) -> str:
    """A message without its closing punctuation, which OCR keeps or drops."""
    return re.sub(r"[!.…\s]+$", "", text)


_STARTS = {_bare(k): v for k, v in FIELD_STARTS.items()}
_ENDS = {_bare(k): v for k, v in FIELD_ENDS.items()}
_STAT = r"(?:Attack|Defense|Sp\. Atk|Sp\. Def|Speed|accuracy|evasiveness)"
_SCREEN = r"(?P<cond>Light Screen|Reflect|Aurora Veil)"
# The game is shown from the left player's side: "your" team is the left one.
_WHOSE = {"your side": "L", "your team": "L", "the opposing side": "R", "the opposing team": "R",
          "your team's": "L", "the opposing team's": "R"}


def _alt(table: dict[str, str]) -> str:
    return "|".join(re.escape(k) for k in sorted(table, key=len, reverse=True))


# (pattern id, regex on the message without its closing punctuation). Order matters: the first
# that matches wins, so the specific comes before the general ("…used…" last but one).
PATTERNS: list[tuple[str, str]] = [
    ("sendout_L", r"^Go! (?P<who>.+)$"),
    ("sendout_R", r"^(?P<trainer>.+?) sent out (?P<who>.+)$"),
    ("withdraw_L", r"^(?P<who>.+?), come back$"),
    ("withdraw_R", r"^(?P<trainer>.+?) withdrew (?P<who>.+)$"),
    ("went_back", r"^(?P<who>.+?) went back to .+$"),
    ("eject", r"^(?P<who>.+?) is switched out with the Eject (?:Button|Pack)$"),
    ("dragged", r"^(?P<who>.+?) was dragged out$"),
    ("swap", r"^(?P<a>.+?) and (?P<b>.+?) switched places$"),
    ("faint", r"^(?P<who>.+?) fainted$"),
    ("effective", r"^It's (?P<how>super|extremely|not very) effective on (?P<who>.+)$"),
    ("immune", r"^It doesn't affect (?P<who>.+)$"),
    ("protected", r"^(?P<who>.+?) protected itself$"),
    ("guarded", r"^(?:Wide|Quick) Guard protected (?P<who>.+)$"),
    ("miss", r"^(?P<who>.+?) avoided the attack$"),
    ("missed", r"^(?P<who>.+?)'s attack missed$"),
    ("failed", r"^But it failed$"),
    ("redirect", r"^(?P<who>.+?) took the attack$"),
    ("boost", rf"^(?P<who>.+?)'s (?P<stats>{_STAT}(?: and {_STAT})*) "
              r"(?P<how>rose ?drastically|rose ?sharply|rose|harshly ?fell|severely ?fell|fell)$"),
    ("mega_stone", r"^(?P<who>.+?)'s (?P<item>.+?) is reacting to .+$"),
    ("mega", r"^(?P<who>.+?) has Mega Evolved into Mega .+$"),
    ("aura", r"^(?P<who>.+?) is radiating an? (?P<aura>fairy|dark) aura$"),
    ("status", rf"^(?P<who>.+?) (?P<what>{_alt(STATUS_GAINED)})$"),
    ("cant", rf"^(?P<who>.+?) (?P<what>{_alt(CANT)})$"),
    ("chip", rf"^(?P<who>.+?) (?P<what>{_alt(CHIP)})$"),
    ("status_end", r"^(?P<who>.+?) (?:woke up|thawed out|was cured of its .+)$"),
    ("confused", r"^(?P<who>.+?) became confused$"),
    ("drain", r"^(?P<who>.+?) had its energy drained$"),
    # Sinistcha's Hospitality, on arrival: its ally gets back a quarter of its HP.
    ("hospitality", r"^(?P<who>.+?) drank down all the matcha that (?P<host>.+?) made$"),
    ("berry", r"^(?P<who>.+?) restored its HP using its (?P<item>.+)$"),
    ("sash", r"^(?P<who>.+?) hung on using its (?P<item>.+)$"),
    ("herb", r"^(?P<who>.+?) returned its stats to normal using its (?P<item>.+)$"),
    ("endured", r"^(?P<who>.+?) endured the hit$"),
    ("knocked_off", r"^(?P<who>.+?) knocked off (?P<target>.+?)'s (?P<item>.+)$"),
    ("trick", r"^(?P<who>.+?) switched items with its target$"),
    ("multihit", r"^The Pokémon was hit (?P<n>\d+) times?$"),
    ("charge", r"^(?P<who>.+?) absorbed electricity$"),
    ("center", r"^(?P<who>.+?) became the center of attention$"),
    ("helping", r"^(?P<who>.+?) is ready to help (?P<ally>.+)$"),
    ("feint", r"^(?P<who>.+?) fell for the feint$"),
    ("forfeit", r"^(?P<trainer>.+?) forfeited(?: the match)?$"),
    ("forfeit_notice", r"^The battle has ended due to a forfeit$"),
    # From the left player's view the right one is "the Trainer", or their in-game name ("Alex™"),
    # whichever their send-outs used; "You lost" is provisional.
    ("lost", r"^(?P<loser>.+?) lost$"),
    ("move", r"^(?P<who>.+?) used (?P<move>.+)$"),
]
_COMPILED = [(pid, re.compile(rx)) for pid, rx in PATTERNS]

# Patterns whose one name is the subject, with no further reading.
_SIMPLE = {"faint", "immune", "protected", "guarded", "miss", "missed", "redirect", "status_end",
           "confused", "drain", "endured", "center", "feint", "went_back", "eject", "dragged"}


def parse(text: str, vocab: Vocabulary) -> Said:
    full = re.sub(r"\s+", " ", text.strip())
    t = _bare(full)
    unknown = Said("unknown", text=full)

    for table, on in ((_STARTS, True), (_ENDS, False)):
        for phrase, (kind, value) in table.items():
            if t == phrase or (kind == "pseudo" and t.endswith(phrase)):
                return Said("field", data={kind: value, "on": on}, text=full,
                            pattern=f"field:{value}:{'on' if on else 'off'}")
    if t.startswith("A critical hit"):
        return Said("crit", text=full, pattern="crit")
    for rx, cond_group, on in (
        (rf"^{_SCREEN} made (?P<whose>your side|your team|the opposing side|the opposing team) stronger against .+ moves$", True, True),
        (rf"^(?i:(?P<whose>your team's|the opposing team's)) {_SCREEN} wore off$", True, False),
        (r"^The tailwind blew from behind (?P<whose>your team|the opposing team)$", False, True),
        (r"^(?i:(?P<whose>your team's|the opposing team's)) tailwind petered out$", False, False),
    ):
        m = re.match(rx, t)
        if m:
            cond = m["cond"] if cond_group else "Tailwind"
            return Said("side", data={"side": _WHOSE[m["whose"].lower()], "condition": cond, "on": on},
                        text=full, pattern=f"side:{cond}:{'on' if on else 'off'}")

    for pid, rx in _COMPILED:
        m = rx.match(t)
        if not m:
            continue
        g = m.groupdict()
        said = _read(pid, g, vocab, full)
        return said if said is not None else unknown
    return unknown


def _read(pid: str, g: dict[str, str], vocab: Vocabulary, full: str) -> Said | None:
    """The Said for a matched phrasing, or None when a name in it doesn't resolve."""
    def said(kind: str, who: tuple[Who, ...] = (), **data: Any) -> Said:
        return Said(kind, who, data, full, pid)

    if pid in ("sendout_L", "sendout_R", "withdraw_L", "withdraw_R"):
        side = pid[-1]
        who = _pair(g["who"], vocab, side)
        trainer = {"trainer": g["trainer"]} if g.get("trainer") else {}
        return said(pid.rsplit("_", 1)[0], who, side=side, **trainer) if who else None
    if pid == "swap":
        a, b = _who(g["a"], vocab), _who(g["b"], vocab)
        return said("swap", (a, b)) if a and b else None
    if pid == "boost":
        who = _pair(g["who"], vocab)
        if not who:
            return None
        return said("boost", who, stats=[STATS[x.lower()] for x in g["stats"].split(" and ")],
                    stages=STAGES[g["how"].replace(" ", "")])
    if pid == "multihit":
        return said("multihit", n=int(g["n"]))
    if pid == "failed":
        return said("failed")
    if pid == "forfeit_notice":
        return said("forfeit_notice")
    if pid == "lost":
        if g["loser"] == "You":
            return Said("lost", (), {"winner": "R"}, full, "lost:you")
        side = trainer_side(g["loser"], vocab)
        return said("lost", winner={"L": "R", "R": "L"}[side]) if side else None
    if pid == "forfeit":
        side = trainer_side(g["trainer"], vocab)
        return said("forfeit", winner={"L": "R", "R": "L"}.get(side)) if side else None
    w = _who(g["who"], vocab)
    if w is None:
        return None
    if pid == "move":
        move = best(g["move"], vocab.moves)
        if move:
            return said("move", (w,), move=move)
        # A sheet read with a move left blank: the move may be any the species can learn. That is
        # a wider vocabulary than the sheet's, so it reads as provisional.
        if (w.side, w.species) in vocab.open_moves:
            move = best(g["move"], vocab.open_moves[(w.side, w.species)])
            if move:
                return Said("move", (w,), {"move": move}, full, "move:off-sheet")
        return None
    if pid == "effective":
        return said("effective", (w,), how=g["how"])
    if pid == "mega":
        # "Mega Raichu" doesn't say X or Y; the stone on the sheet does, so the forme is left to
        # whoever holds the sheet.
        return said("mega", (w,))
    if pid == "mega_stone":
        return said("mega_stone", (w,), item=best(g["item"], vocab.items))
    if pid == "aura":
        return said("ability", (w,), ability=f"{g['aura'].title()} Aura")
    if pid == "helping":
        ally = _who(g["ally"], vocab)
        return said("helping", (w,) + ((ally,) if ally else ()))
    if pid in ("status", "cant", "chip"):
        value = {"status": STATUS_GAINED, "cant": CANT, "chip": CHIP}[pid][g["what"]]
        key = {"status": "status", "cant": "reason", "chip": "cause"}[pid]
        # Confirmed per cause: recoil has been seen on screen, a burn's chip has not.
        return Said(pid, (w,), {key: value}, full, f"{pid}:{value}")
    if pid in ("berry", "sash", "herb"):
        item = best(g["item"], vocab.items)
        return said("consume", (w,), item=item) if item else None
    if pid == "knocked_off":
        target = _who(g["target"], vocab)
        item = best(g["item"], vocab.items)
        return said("knocked_off", (w,) + ((target,) if target else ()), item=item) if target and item else None
    if pid == "trick":
        return said("trick", (w,))
    if pid == "hospitality":
        # The host is the drinker's partner, so on the drinker's side however its name is written.
        name = re.sub(r"(?i)^the opposing\s+", "", g["host"])
        host = _who(("the opposing " if w.side == "R" else "") + name, vocab)
        return said("heal", (w, host), cause="hospitality") if host else None
    if pid in ("went_back", "eject"):
        return said("withdraw", (w,), side=w.side)
    if pid in _SIMPLE:
        return said(pid, (w,))
    return said(pid, (w,))


def banner(text: str, side: str, vocab: Vocabulary, dex: Dex) -> Said:
    """An ability or item banner: "Archaludon's Stamina", "Indeedee's Rocky Helmet"."""
    m = re.match(r"^(?P<who>.+?)'s? (?P<what>.+)$", text.strip())
    if not m:
        return Said("unknown", text=text)
    w = _who(("the opposing " if side == "R" else "") + m.group("who"), vocab)
    if w is None:
        return Said("unknown", text=text)
    what = m.group("what")
    if (item := best(what, vocab.items)) is not None:
        return Said("item", (w,), {"item": item}, text, "banner:item")
    ability = best(what, {k: v["name"] for k, v in dex.abilities.items()})
    return Said("ability", (w,), {"ability": ability}, text, "banner:ability") if ability else Said("unknown", text=text)
