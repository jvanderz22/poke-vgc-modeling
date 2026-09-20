"""Showdown protocol lines as readable English.

Shared by the simulator page and the endgame browser: both are showing a battle log, and a
Pokémon should not be described one way when the simulator played it and another way when a
human did.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

# Protocol lines worth showing. Everything else (|upkeep|, |t:|, request payloads) is noise here.
_IGNORE = {"", "t:", "upkeep", "request", "uhtml", "html", "player", "teamsize", "gametype", "gen",
           "tier", "rule", "start", "teampreview", "seed", "choice", "j", "l", "c", "inactive",
           "split", "sentchoices", "clearpoke", "poke", "showteam", "done", "-hint", "raw"}


def _side_of(ident: str) -> str | None:
    m = re.match(r"(p[12])[ab]?:", ident)
    return m.group(1) if m else None


def species_by_nickname(log: Iterable[str]) -> dict[str, str]:
    """`|switch|p1a: Mr. VGC|Incineroar, L50, M|100/100` → `{"Mr. VGC": "Incineroar"}`.

    Human replays are full of nicknames, and a story about Mr. VGC is unreadable next to a board
    that lists Incineroar. Self-play has none, so this is empty there and costs nothing.
    """
    out: dict[str, str] = {}
    for line in log:
        parts = line[1:].split("|") if line.startswith("|") else []
        # `detailschange` is a Mega Evolution or other permanent forme change, and the board
        # shows the new forme — so the story follows it rather than keeping the old name.
        if len(parts) >= 3 and parts[0] in ("switch", "drag", "replace", "detailschange") and ": " in parts[1]:
            nickname = parts[1].split(": ", 1)[1]
            species = parts[2].split(",")[0].strip()
            if nickname != species:
                out[nickname] = species
    return out


def narrate_line(line: str, names: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """One protocol line as something readable, or None to drop it.

    `names` maps a nickname to the species to show instead — see `species_by_nickname`.
    """
    if not line.startswith("|"):
        return None
    parts = line[1:].split("|")
    kind, args = parts[0], parts[1:]
    if kind in _IGNORE:
        return None

    def _name(ident: str) -> str:
        """`p1a: Rillaboom` → `Rillaboom`; the nickname is what the log carries."""
        shown = ident.split(": ", 1)[1] if ": " in ident else ident
        return (names or {}).get(shown, shown)

    def ev(text: str, side: str | None = None, key: str = kind) -> dict[str, Any]:
        return {"kind": key, "text": text, "side": side}

    match kind:
        case "move":
            return ev(f"{_name(args[0])} used {args[1]}!", _side_of(args[0]))
        case "switch" | "drag":
            hp = args[2].split(" ")[0] if len(args) > 2 else ""
            return ev(f"{_name(args[0])} came in ({hp})", _side_of(args[0]))
        case "faint":
            return ev(f"{_name(args[0])} fainted!", _side_of(args[0]))
        case "-damage" | "-heal":
            who, hp = _name(args[0]), args[1].split(" ")[0]
            verb = "took damage" if kind == "-damage" else "healed"
            return ev(f"{who} {verb} → {hp}", _side_of(args[0]))
        case "-status":
            return ev(f"{_name(args[0])} was {args[1].upper()}'d", _side_of(args[0]))
        case "-curestatus":
            return ev(f"{_name(args[0])} shook off {args[1].upper()}", _side_of(args[0]))
        case "-boost" | "-unboost":
            arrow = "rose" if kind == "-boost" else "fell"
            return ev(f"{_name(args[0])}'s {args[1]} {arrow} ({args[2]})", _side_of(args[0]))
        case "-crit":
            return ev("A critical hit!", _side_of(args[0]))
        case "-supereffective":
            return ev("It's super effective!", _side_of(args[0]))
        case "-resisted":
            return ev("It's not very effective…", _side_of(args[0]))
        case "-immune":
            return ev(f"{_name(args[0])} is immune.", _side_of(args[0]))
        case "-miss":
            return ev(f"{_name(args[0])}'s attack missed.", _side_of(args[0]))
        case "-fail":
            return ev(f"{_name(args[0])}: it failed.", _side_of(args[0]))
        case "-item":
            return ev(f"{_name(args[0])}'s {args[1]}", _side_of(args[0]))
        case "-enditem":
            return ev(f"{_name(args[0])} used up its {args[1]}", _side_of(args[0]))
        case "-ability":
            return ev(f"{_name(args[0])}: {args[1]}", _side_of(args[0]))
        case "-mega":
            return ev(f"{_name(args[0])} Mega Evolved into {args[1]}!", _side_of(args[0]))
        case "-weather":
            return None if len(args) > 1 and args[1] == "[upkeep]" else ev(f"Weather: {args[0]}")
        case "-fieldstart" | "-fieldend":
            return ev(("" if kind == "-fieldstart" else "End: ") + args[0].replace("move: ", ""))
        case "-sidestart":
            return ev(f"{args[1].replace('move: ', '')} on {_side_of(args[0]) or args[0]}", _side_of(args[0]))
        case "-sideend":
            return ev(f"{args[1].replace('move: ', '')} faded", _side_of(args[0]))
        case "-start" | "-end":
            return ev(f"{_name(args[0])}: {'' if kind == '-start' else 'no longer '}{args[1].replace('move: ', '')}",
                      _side_of(args[0]))
        case "-activate":
            return ev(f"{_name(args[0])}: {args[1].replace('move: ', '')}", _side_of(args[0]))
        case "cant":
            return ev(f"{_name(args[0])} couldn't move ({args[1]})", _side_of(args[0]))
        case "win":
            return ev(f"{args[0]} wins.", None, "win")
        case "tie":
            return ev("Tie.", None, "tie")
        case _:
            return None


def narrate_turns(log: list[str], names: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """The log split at `|turn|N`. Everything before turn 1 is the opening (leads, abilities)."""
    turns: list[dict[str, Any]] = [{"turn": 0, "events": []}]
    for line in log:
        if line.startswith("|turn|"):
            turns.append({"turn": int(line.split("|")[2]), "events": []})
            continue
        e = narrate_line(line, names)
        if e:
            turns[-1]["events"].append(e)
    return [t for t in turns if t["events"]]
