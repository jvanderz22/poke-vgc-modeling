"""P(item, ability, nature, moves | species) — the ranking half of the belief, and not a bound.

Everything else in this package produces a **bound**: a feasible set the truth is never allowed to
leave, bought by abstaining whenever the evidence does not settle something. This module is the
other kind of object, and keeping the two apart is the whole design:

| | `speed` / `damage` / `bulk` / `sp` | `sets` |
| --- | --- | --- |
| what it says | what is *possible* | what is *likely* |
| where it comes from | this battle | 15,000 other people's sheets |
| when it is wrong | a bug, measured as `silently_wrong` | a tap |

So **this module may never make anything impossible.** Only a sound observation can do that —
seeing the item, watching the ability fire, an ability that would have announced and did not
(`Mon.ability_ruled_out`). Usage only *orders* what is left, and every legal option keeps a
non-zero share however rare it is, because somebody is always running the thing nobody runs.

What it buys, concretely: at team preview the pop-up for an arriving Incineroar can lead with
Intimidate at 99.7% instead of listing Blaze first because B sorts before I, and the win
probability can be an average over what they might be holding rather than one guess. Incineroar's
single most common set is **17.7% of its sheets** — so a point estimate over the mode is the wrong
set five times in six, and that is the number this exists to fix.

**No spreads.** Sheets do not carry Stat Points, and the pool's are imputed by a function that
scored 2.9 nats a pair *worse than assuming nothing* (docs/phase8-findings.md). The spread is what
`vgc.belief.sp` is for, from this battle's own evidence. This module stops at what a sheet shows.
"""

from __future__ import annotations

import functools
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from vgc import paths
from vgc.regulation import Regulation, to_id

TEAMS = paths.DATA / "teams"
NONE = "(none)"

# How much probability an option that nobody in the corpus has used still gets, in units of
# sheets. Half a sighting: enough that no legal option is ever reported as impossible, small
# enough that it never outranks something real. It matters most where it should — a species with
# four sheets in the corpus has barely been measured, and the floor says so.
FLOOR = 0.5


@dataclass(frozen=True)
class Sheet:
    """One distinct set somebody actually brought, and how many sheets brought it."""

    item: str
    ability: str
    nature: str
    moves: tuple[str, ...]
    count: int

    def to_json(self) -> dict[str, Any]:
        return {"item": self.item, "ability": self.ability, "nature": self.nature,
                "moves": list(self.moves), "count": self.count}


# --- the corpus ----------------------------------------------------------------------------

def prior_path(reg: Regulation) -> Path:
    return TEAMS / reg.id / "setprior.json"


def build(reg: Regulation, **kw: Any) -> dict[str, Any]:
    """Count every distinct set per species, untruncated.

    `vgc.meta.usage` already counts exactly this joint and says so; the report truncates it to the
    top three for reading, which is right for a report and useless for a belief — the tail is
    where an opponent who is not running the obvious thing lives.
    """
    from vgc.meta import usage

    report = usage.build(reg, top=10 ** 6, sets=10 ** 6, **kw)
    return {
        "regulation": reg.id,
        "showdown_sha": reg.showdown_sha,
        "built": report["built"],
        "source": report["source"],
        "sheets": report["sheets"],
        "note": "P(item, ability, nature, moves | species). No spreads: sheets do not carry "
                "Stat Points, and imputing them scores worse than assuming nothing.",
        "species": {sid: {"species": s["species"], "sheets": s["sheets"],
                          "share": s["share"],
                          "sets": [{"item": x["item"], "ability": x["ability"],
                                    "nature": x["nature"], "moves": x["moves"],
                                    "sheets": x["sheets"]} for x in s["sets"]]}
                    for sid, s in report["species"].items()},
    }


def save(reg: Regulation, report: dict[str, Any], path: Path | None = None) -> Path:
    path = path or prior_path(reg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, separators=(",", ":")) + "\n")
    return path


@functools.lru_cache(maxsize=4)
def corpus(reg_id: str) -> dict[str, list[Sheet]]:
    """`{species_id: [Sheet, ...]}`, most common first. Empty when nothing has been built."""
    from vgc.regulation import load_regulation

    path = prior_path(load_regulation(reg_id))
    if not path.exists():
        return {}
    blob = json.loads(path.read_text())
    out = {}
    for sid, s in blob["species"].items():
        out[sid] = sorted(
            (Sheet(to_id(x["item"]) if x["item"] != NONE else "",
                   to_id(x["ability"]) if x["ability"] != NONE else "",
                   x["nature"] if x["nature"] != NONE else "",
                   tuple(sorted(to_id(m) for m in x["moves"])), x["sheets"])
             for x in s["sets"]),
            key=lambda x: -x.count)
    return out


# --- one Pokémon's set -----------------------------------------------------------------------

@dataclass
class SetBelief:
    """What one opposing Pokémon is probably holding, given everything seen so far.

    `sheets` is what the corpus still allows; `possible` is what the *rules* still allow, which is
    always a superset — every legal ability, item and move stays in the support at `FLOOR` weight
    even when nobody has run it. `off_meta` is set when the evidence has eliminated every set
    anybody brought, which is not a contradiction but a fact about the opponent worth saying.
    """

    species: str
    sheets: list[Sheet]
    possible_abilities: list[str]
    legal_items: list[str]
    legal_moves: list[str]
    seen: int                          # sheets in the corpus for this species, before conditioning
    evidence: list[str] = field(default_factory=list)
    off_meta: bool = False

    # --- marginals ---------------------------------------------------------------------------

    def ability(self) -> dict[str, float]:
        """P(ability), over everything the rules still allow. Never zero for a live candidate."""
        return self._marginal(lambda s: s.ability, self.possible_abilities)

    def item(self) -> dict[str, float]:
        return self._marginal(lambda s: s.item, self.legal_items)

    def nature(self) -> dict[str, float]:
        natures = sorted({s.nature for s in self.sheets if s.nature})
        return self._marginal(lambda s: s.nature, natures)

    def moves(self, top: int = 8) -> list[tuple[str, float]]:
        """P(it has this move), which is a marginal over sets and so does not sum to 1."""
        total = sum(s.count for s in self.sheets) or 1
        per: dict[str, float] = {}
        for s in self.sheets:
            for m in s.moves:
                per[m] = per.get(m, 0.0) + s.count
        ranked = sorted(((m, n / total) for m, n in per.items()), key=lambda kv: (-kv[1], kv[0]))
        return ranked[:top]

    def _marginal(self, key, support: Sequence[str]) -> dict[str, float]:
        counts = {k: FLOOR for k in support}
        for s in self.sheets:
            k = key(s)
            if k in counts:
                counts[k] += s.count
            elif not support:
                counts[k] = counts.get(k, FLOOR) + s.count
        total = sum(counts.values()) or 1.0
        return {k: v / total for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))}

    # --- what the screen asks for --------------------------------------------------------------

    def rank(self, abilities: Iterable[str]) -> list[tuple[str, float]]:
        """Those abilities, likeliest first — what turns a pop-up's alphabetical list into one
        whose first button is usually the right one."""
        p = self.ability()
        return sorted(((a, p.get(a, 0.0)) for a in abilities), key=lambda kv: (-kv[1], kv[0]))

    def particles(self, rng: Any, k: int = 16) -> list[Sheet]:
        """`k` full sets drawn in proportion to how often they were actually brought.

        Drawn *with* replacement and without deduplicating, so the list is a sample from the
        belief and its mean is an estimate of an expectation over it. That is the whole point:
        averaging a win probability over these is an answer about what the opponent might be
        holding, where evaluating the mode is an answer about one guess that is wrong five times
        in six.
        """
        if not self.sheets:
            return []
        weights = [s.count for s in self.sheets]
        total = sum(weights)
        out = []
        for _ in range(k):
            r = rng.random() * total
            acc = 0.0
            for s, w in zip(self.sheets, weights):
                acc += w
                if r <= acc:
                    out.append(s)
                    break
            else:
                out.append(self.sheets[-1])
        return out

    def top(self) -> Sheet | None:
        return self.sheets[0] if self.sheets else None

    @property
    def concentration(self) -> float:
        """How much of the belief the single likeliest set carries. Low means a point estimate
        over the mode is answering a question about a set nobody is holding."""
        total = sum(s.count for s in self.sheets)
        return self.sheets[0].count / total if total else 0.0

    @property
    def entropy(self) -> float:
        """Bits of uncertainty left about the whole set — the number a reveal is supposed to move."""
        total = sum(s.count for s in self.sheets)
        if not total:
            return 0.0
        return -sum((s.count / total) * math.log2(s.count / total) for s in self.sheets if s.count)

    def to_json(self) -> dict[str, Any]:
        return {"species": self.species, "sets": len(self.sheets), "seen": self.seen,
                "off_meta": self.off_meta, "evidence": list(self.evidence),
                "concentration": round(self.concentration, 4), "entropy": round(self.entropy, 3),
                "ability": {k: round(v, 4) for k, v in list(self.ability().items())[:4]},
                "item": {k: round(v, 4) for k, v in list(self.item().items())[:4]},
                "moves": [[m, round(p, 4)] for m, p in self.moves(6)],
                "top": self.top().to_json() if self.top() else None}


# --- building one, and conditioning it ---------------------------------------------------------

def legal_abilities(reg: Regulation, species: str) -> list[str]:
    entry = reg.dex.get_species(species) or {}
    return sorted({to_id(a) for a in (entry.get("abilities") or {}).values()})


def legal_moves(reg: Regulation, species: str) -> list[str]:
    entry = reg.dex.get_species(species) or {}
    return sorted({to_id(m) for m in (entry.get("moves") or [])})


def for_species(reg: Regulation, species: str) -> SetBelief:
    """The prior, before this battle has said anything."""
    sheets = corpus(reg.id).get(to_id(species), [])
    return SetBelief(species=species, sheets=list(sheets),
                     possible_abilities=legal_abilities(reg, species),
                     legal_items=sorted({s.item for s in sheets}),
                     legal_moves=legal_moves(reg, species),
                     seen=sum(s.count for s in sheets))


# The order constraints are given up when nothing in the corpus satisfies all of them. Not an
# order of confidence — every one of these is a fact, and none of them is ever *un*-applied to the
# support. It is an order of how surprising a violation is: plenty of real Pokémon run a move
# nobody has been recorded running, a fair number pick an unusual nature, fewer carry an item off
# the list entirely, and an ability outside the two its species has is impossible.
BACKOFF = ("moves", "nature", "item")


def given(reg: Regulation, mon: Any) -> SetBelief:
    """The prior, conditioned on everything sound that this battle has established.

    Only sound facts condition it, and each one is recorded in `evidence` so a screen can say why
    the answer changed.

    **It never empties.** When no set anybody brought satisfies everything seen, the constraints
    are given up one at a time, weakest-surprise first, until something matches — and the screen
    is told which. An opponent running a move nobody has run is a real opponent, and a belief that
    reported them impossible would be the failure mode this whole package exists to avoid. Note
    what backing off does *not* do: the support never grows back. A ruled-out ability stays ruled
    out, because that came from the rules and not from a popularity contest.
    """
    belief = for_species(reg, mon.species)
    all_sets = list(belief.sheets)
    evidence: list[str] = []
    tests: dict[str, Any] = {}

    ruled_out = set(getattr(mon, "ability_ruled_out", ()) or ())
    if mon.ability:
        want = to_id(mon.ability)
        tests["ability"] = lambda s, want=want: s.ability == want
        belief.possible_abilities = [want]
        evidence.append(f"ability is {want}")
    elif ruled_out:
        tests["ability"] = lambda s, out=ruled_out: s.ability not in out
        belief.possible_abilities = [a for a in belief.possible_abilities if a not in ruled_out]
        evidence.append("not " + ", ".join(sorted(ruled_out)))

    held = mon.item if mon.item else (mon.lost_item or None)
    if held:
        want = to_id(held)
        tests["item"] = lambda s, want=want: s.item == want
        belief.legal_items = [want]
        evidence.append(f"item is {want}")
    elif mon.item == "" and not mon.lost_item:
        tests["item"] = lambda s: not s.item
        belief.legal_items = [""]
        evidence.append("holds nothing")

    if mon.moves:                       # an open sheet gives the whole four
        want = tuple(sorted(to_id(m) for m in mon.moves))
        tests["moves"] = lambda s, want=want: s.moves == want
        evidence.append("moves from the sheet")
    elif mon.moves_used:
        used = {to_id(m) for m in mon.moves_used}
        tests["moves"] = lambda s, used=used: used <= set(s.moves)
        evidence.append("uses " + ", ".join(sorted(used)))

    if mon.nature:
        tests["nature"] = lambda s, want=mon.nature.lower(): s.nature.lower() == want
        evidence.append(f"nature is {mon.nature}")

    dropped: list[str] = []
    while True:
        keep = [s for s in all_sets if all(t(s) for k, t in tests.items() if k not in dropped)]
        if keep or not all_sets:
            break
        nxt = next((k for k in BACKOFF if k in tests and k not in dropped), None)
        if nxt is None:
            break                       # only the ability constraint is left and nothing matches
        dropped.append(nxt)

    if dropped:
        belief.off_meta = True
        evidence.append("no corpus set matches — ignoring " + ", ".join(dropped) + " to rank")
    belief.sheets = keep
    belief.evidence = evidence
    return belief


def summary(beliefs: dict[Any, SetBelief]) -> dict[str, Any]:
    n = len(beliefs) or 1
    return {
        "pokemon": len(beliefs),
        "with_corpus": sum(1 for b in beliefs.values() if b.seen),
        "off_meta": sum(1 for b in beliefs.values() if b.off_meta),
        "concentration_mean": round(sum(b.concentration for b in beliefs.values()) / n, 4),
        "entropy_mean": round(sum(b.entropy for b in beliefs.values()) / n, 3),
    }
