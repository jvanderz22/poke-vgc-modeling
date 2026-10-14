"""What a battle message says, in the closed vocabulary of the two sheets.

The second layer of the battle reader (PLAN-video step 2). A message from the screen layer is
matched against the game's own phrasing (docs/video-findings.md, "Messages") and its names against
the twelve Pokémon on the two sheets, their moves and their items. Anything that doesn't match is
`unknown` and is kept as such, never forced onto the nearest phrasing or name (PLAN-video decision 3).

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
STAGES = {"rose": 1, "sharply rose": 2, "rose drastically": 3, "fell": -1, "harshly fell": -2, "severely fell": -3}

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
}


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


@dataclass
class Vocabulary:
    """The names a game's messages can use: each side's six, as the screen prints them."""

    # side -> {printed name id -> sheet species}; "Indeedee" prints for Indeedee-F
    names: dict[str, dict[str, str]]
    moves: dict[str, str]  # id -> move name, for every move on either sheet
    items: dict[str, str]

    @classmethod
    def from_sheets(cls, dex: Dex, left: list[dict[str, Any]], right: list[dict[str, Any]]) -> "Vocabulary":
        """`left` and `right` are sheet entries (species, ability, nature, item, moves)."""
        names: dict[str, dict[str, str]] = {"L": {}, "R": {}}
        moves, items = {}, {}
        for side, sheet in (("L", left), ("R", right)):
            for e in sheet:
                if not e.get("species"):
                    continue
                sp = dex.get_species(e["species"]) or {"name": e["species"]}
                printed = sp.get("baseSpecies") or sp["name"]
                names[side][to_id(printed)] = sp["name"]
                names[side][to_id(sp["name"])] = sp["name"]
                for m in e.get("moves") or []:
                    if m:
                        moves[to_id(m)] = m
                if e.get("item"):
                    items[to_id(e["item"])] = e["item"]
        return cls(names, moves, items)


def _best(text: str, table: dict[str, str]) -> str | None:
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
        if (sp := _best(candidate, table)) is not None:
            return Who(side, sp)
    return None


def _pair(text: str, vocab: Vocabulary, side: str | None = None) -> tuple[Who, ...] | None:
    """'A and B' or 'A', each resolved; None if any part doesn't resolve."""
    parts = re.split(r"\s+and\s+", text.strip())
    out = []
    for p in parts:
        w = _who(("the opposing " if side == "R" else "") + p, vocab)
        if w is None:
            return None
        out.append(w)
    return tuple(out)


def parse(text: str, vocab: Vocabulary) -> Said:
    t = re.sub(r"\s+", " ", text.strip())
    unknown = Said("unknown", text=t)

    if t in FIELD_STARTS:
        kind, value = FIELD_STARTS[t]
        return Said("field", data={kind: value, "on": True}, text=t)
    if t in FIELD_ENDS:
        kind, value = FIELD_ENDS[t]
        return Said("field", data={kind: value, "on": False}, text=t)
    if t.startswith("A critical hit"):
        return Said("crit", text=t)

    rules: list[tuple[str, Any]] = [
        (r"^(?P<who>.+?) used (?P<move>.+?)!$", "move"),
        (r"^Go! (?P<who>.+?)!$", "sendout_L"),
        (r"^.+? sent out (?P<who>.+?)!$", "sendout_R"),
        (r"^(?P<who>.+?), come back!$", "withdraw_L"),
        (r"^.+? withdrew (?P<who>.+?)!$", "withdraw_R"),
        (r"^(?P<who>.+?) fainted!$", "faint"),
        (r"^It's (?P<how>super|extremely|not very) effective on (?P<who>.+?)!$", "effective"),
        (r"^It doesn't affect (?P<who>.+?)[.!]*$", "immune"),
        (r"^(?P<who>.+?) protected itself!$", "protected"),
        (r"^(?P<who>.+?) avoided the attack!$", "miss"),
        (r"^(?P<who>.+?)'s (?P<stat>Attack|Defense|Sp\. Atk|Sp\. Def|Speed|accuracy|evasiveness) "
         r"(?P<how>rose drastically|sharply rose|rose|harshly fell|severely fell|fell)!$", "boost"),
        (r"^(?P<who>.+?) has Mega Evolved into Mega (?P<forme>.+?)!$", "mega"),
        (r"^(?P<who>.+?) was damaged by the recoil!$", "recoil"),
        (r"^(?P<who>.+?) became the center of attention!$", "center"),
        (r"^(?P<who>.+?) is ready to help (?P<ally>.+?)!$", "helping"),
    ]
    for pattern, kind in rules:
        m = re.match(pattern, t)
        if not m:
            continue
        g = m.groupdict()
        if kind in ("sendout_L", "sendout_R", "withdraw_L", "withdraw_R"):
            side = kind[-1]
            who = _pair(g["who"], vocab, side)
            return Said(kind.rsplit("_", 1)[0], who, {"side": side}, t) if who else unknown
        if kind == "boost":
            who = _pair(g["who"], vocab)
            if not who:
                return unknown
            return Said("boost", who, {"stat": STATS[g["stat"].lower()], "stages": STAGES[g["how"]]}, t)
        w = _who(g["who"], vocab)
        if w is None:
            return unknown
        if kind == "move":
            move = _best(g["move"], vocab.moves)
            return Said("move", (w,), {"move": move}, t) if move else unknown
        if kind == "effective":
            return Said("effective", (w,), {"how": g["how"]}, t)
        if kind == "mega":
            return Said("mega", (w,), {"forme": f"{w.species}-Mega" + ("" if " " not in g["forme"] else "-" + g["forme"].split()[-1])}, t)
        if kind == "helping":
            ally = _who(g["ally"], vocab)
            return Said("helping", (w,) + ((ally,) if ally else ()), {}, t)
        return Said(kind, (w,), {}, t)
    # "The opposing Raichu's Raichunite X is reacting to Trainer's Omni Ring!": the Mega's announcement.
    m = re.match(r"^(?P<who>.+?)'s (?P<item>.+?) is reacting to .+$", t)
    if m and (w := _who(m.group("who"), vocab)):
        return Said("mega_stone", (w,), {"item": _best(m.group("item"), vocab.items)}, t)
    return unknown


def banner(text: str, side: str, vocab: Vocabulary, dex: Dex) -> Said:
    """An ability or item banner: "Archaludon's Stamina", "Indeedee's Rocky Helmet"."""
    m = re.match(r"^(?P<who>.+?)'s? (?P<what>.+)$", text.strip())
    if not m:
        return Said("unknown", text=text)
    w = _who(("the opposing " if side == "R" else "") + m.group("who"), vocab)
    if w is None:
        return Said("unknown", text=text)
    what = m.group("what")
    if (item := _best(what, vocab.items)) is not None:
        return Said("item", (w,), {"item": item}, text)
    ability = _best(what, {k: v["name"] for k, v in dex.abilities.items()})
    return Said("ability", (w,), {"ability": ability}, text) if ability else Said("unknown", text=text)
