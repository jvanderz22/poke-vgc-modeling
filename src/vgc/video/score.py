"""A battle read off a video, scored against a hand transcription of the same game: PLAN-video
step 3's gates.

The two journals are lined up turn by turn, not by time: a read of a screen recording carries clip
time and a transcription made on the watch page carries the video's, and a turn is what both agree
on. Turns are paired by what was used in them, so a decision point the reader missed (or invented)
shifts nothing after it: the turns either side still pair, and the odd one counts as missed or
wrong whole.

Within a pair of turns, entries match on what they claim (kind, who, which move or stat), in
order. Then:

  * **G-read.** A read entry with no match is *wrong*; a hand entry with no match is *missed*.
    Kinds that change who is on the field (`lead`, `switch`, `faint`, `mega`) may have none wrong.
    Moves may have at most 1% wrong, counting a matched move whose target differs.
    Missed entries are reported, not gated.
  * **G-hp.** Each matched `damage` or `heal`: the read HP within 1 point of the hand's.
  * **G-wp.** The WP model's number after each paired turn mark, read journal against hand
    journal, within 0.03 on at least 95% of them.

The unit is the battle (principle 5): every count is reported per battle, and the gates are judged
on the pooled counts with the worst battle beside them. Each count carries its exact interval, and a
gate passes only when all of it is on the right side of the threshold: a count too small to show
that is undecided, not a pass (principle 6).
"""

from __future__ import annotations

import difflib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

CRITICAL = ("lead", "switch", "faint", "mega")
# Not claims about the game: the journal's own bookkeeping, or a reply to its questions (whose ids
# depend on everything before them).
SKIP = {"turn", "answer", "note"}
# What an entry claims, by kind: the fields that must agree for two entries to be the same event.
# Anything else (an HP, a target, a cause) is a detail, compared once they are matched.
CLAIM = {
    "lead": ("side", "slot", "species"),
    "switch": ("side", "slot", "species"),
    "faint": ("side", "slot"),
    "mega": ("side", "species"),
    "move": ("side", "slot", "move"),
    "damage": ("side", "slot"),
    "heal": ("side", "slot"),
    "status": ("side", "slot", "status"),
    "cant": ("side", "slot", "reason"),
    "boost": ("side", "slot", "stat", "stages"),
    "field": ("what", "value", "on"),
    "side": ("side", "condition", "on"),
    "swap": ("side",),
    "reveal": ("side", "species", "what", "value"),
    "consume": ("side", "slot", "item"),
    "end": ("winner",),
}
HP_SLACK = 1
WP_SLACK = 0.03
MOVE_WRONG = 0.01
WP_SHARE = 0.95


def _claim(e: dict[str, Any]) -> tuple:
    kind = e.get("kind")
    fields = CLAIM.get(kind)
    if fields is None:  # a kind with no rule here: everything but times and causes
        return (kind, *sorted((k, str(v)) for k, v in e.items() if k not in ("t", "video_s", "eot")))
    return (kind, *(str(e.get(f)) for f in fields))


def turns(journal: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """The journal cut at its turn marks: [before turn 1 (the leads), turn 1, turn 2, ...]."""
    out: list[list[dict[str, Any]]] = [[]]
    for e in journal:
        if e.get("kind") == "turn":
            out.append([])
        elif e.get("kind") not in SKIP:
            out[-1].append(e)
    return out


def _signature(turn: list[dict[str, Any]]) -> tuple:
    """What pairs two turns: the moves used and who came in."""
    return tuple(sorted(_claim(e) for e in turn if e["kind"] in ("move", "switch", "lead", "faint")))


def pair_turns(read: list[list[dict]], hand: list[list[dict]]) -> list[tuple[int | None, int | None]]:
    """(read turn, hand turn) index pairs; None where one side has a turn the other doesn't."""
    sm = difflib.SequenceMatcher(a=[_signature(t) for t in read], b=[_signature(t) for t in hand],
                                 autojunk=False)
    out: list[tuple[int | None, int | None]] = []
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == "equal" or (op == "replace" and a1 - a0 == b1 - b0):
            # A replaced run of the same length is the same turns read differently: still paired.
            out += list(zip(range(a0, a1), range(b0, b1)))
        else:
            out += [(i, None) for i in range(a0, a1)] + [(None, j) for j in range(b0, b1)]
    return out


@dataclass
class Score:
    """One battle's counts. `kinds` is per entry kind: {"hand", "read", "matched", "wrong", "missed"}."""

    name: str
    kinds: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    target_differs: int = 0
    hp: list[tuple[int, int]] = field(default_factory=list)  # (read, hand) for each matched HP
    wp: list[tuple[int, float, float]] = field(default_factory=list)  # (turn, read, hand)
    turns_read: int = 0
    turns_hand: int = 0
    turns_paired: int = 0
    examples: list[dict[str, Any]] = field(default_factory=list)  # wrong and missed, with turn

    def critical_wrong(self) -> int:
        return sum(self.kinds[k]["wrong"] for k in CRITICAL)

    def move_wrong(self) -> tuple[int, int]:
        return self.kinds["move"]["wrong"] + self.target_differs, self.kinds["move"]["read"]

    def hp_within(self) -> tuple[int, int]:
        return sum(abs(r - h) <= HP_SLACK for r, h in self.hp), len(self.hp)

    def wp_within(self) -> tuple[int, int]:
        return sum(abs(r - h) <= WP_SLACK for _, r, h in self.wp), len(self.wp)


def compare(read: list[dict[str, Any]], hand: list[dict[str, Any]], name: str = "") -> Score:
    """Entry-level counts for one battle (G-read and G-hp); `wp` is left for `with_wp`."""
    s = Score(name)
    rt, ht = turns(read), turns(hand)
    s.turns_read, s.turns_hand = len(rt) - 1, len(ht) - 1
    for e in (e for t in rt for e in t):
        s.kinds[e["kind"]]["read"] += 1
    for e in (e for t in ht for e in t):
        s.kinds[e["kind"]]["hand"] += 1
    for i, j in pair_turns(rt, ht):
        r = rt[i] if i is not None else []
        h = ht[j] if j is not None else []
        if i and j:  # the leads are not a turn
            s.turns_paired += 1
        turn = j if j is not None else i
        unused = list(h)
        for e in r:
            k = _claim(e)
            m = next((x for x in unused if _claim(x) == k), None)
            if m is None:
                s.kinds[e["kind"]]["wrong"] += 1
                s.examples.append({"turn": turn, "wrong": e})
                continue
            unused.remove(m)
            s.kinds[e["kind"]]["matched"] += 1
            if e["kind"] == "move" and e.get("target") != m.get("target"):
                s.target_differs += 1
                s.examples.append({"turn": turn, "target differs": e, "hand": m})
            if e["kind"] in ("damage", "heal") and e.get("pct") is not None and m.get("pct") is not None:
                s.hp.append((int(e["pct"]), int(m["pct"])))
        for m in unused:
            s.kinds[m["kind"]]["missed"] += 1
            s.examples.append({"turn": turn, "missed": m})
    return s


def with_wp(s: Score, reg, setup: dict[str, Any], read: list[dict], hand: list[dict], version: str) -> Score:
    """G-wp: the model's number at each turn mark, read against hand, for the turns `compare`
    paired. The mark after turn list index i is the position that turn left."""
    from vgc.battle import entry
    from vgc.web import live

    def marks(journal):
        b = entry.Battle(reg, setup, journal)
        # The last row is the journal's end, not a turn mark.
        return [row["wp"] for row in live.trajectory(reg, b, version, k=1)[:-1]]

    r, h = marks(read), marks(hand)
    s.wp = [(j + 1, r[i], h[j]) for i, j in pair_turns(turns(read), turns(hand))
            if i is not None and j is not None and i < len(r) and j < len(h)
            and r[i] is not None and h[j] is not None]
    return s


def _cdf(x: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(x + 1))


def interval(x: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact (Clopper-Pearson) one-sided bounds on a rate seen x times in n, each at 1 - alpha.
    With none seen the upper bound is about 3/n, the rule of three."""
    def solve(f) -> float:
        lo, hi = 0.0, 1.0
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) else (lo, mid)
        return (lo + hi) / 2

    upper = 1.0 if x >= n else solve(lambda p: _cdf(x, n, p) > alpha)
    lower = 0.0 if x == 0 else solve(lambda p: 1 - _cdf(x - 1, n, p) < alpha)
    return lower, upper


def _rate(failures: int, n: int) -> str:
    if n == 0:
        return "no data"
    lo, hi = interval(failures, n)
    return f"{failures}/{n}, rate {lo:.1%}–{hi:.1%} (95%)"


def _verdict(failures: int, n: int, threshold: float) -> str:
    """Whether a failure rate is under `threshold`, judged on its interval: pass only if all of it
    is under, fail only if all of it is over, otherwise undecided (principle 6)."""
    if n == 0:
        return "undecided"
    lo, hi = interval(failures, n)
    return "pass" if hi < threshold else "fail" if lo > threshold else "undecided"


def gates(scores: list[Score]) -> dict[str, Any]:
    """The gates on the pooled counts, the worst battle for each, and each count's interval."""
    def pooled(f):
        a = [f(s) for s in scores]
        return sum(x for x, _ in a), sum(n for _, n in a)

    crit_wrong = sum(s.critical_wrong() for s in scores)
    crit_n = sum(s.kinds[k]["read"] for s in scores for k in CRITICAL)
    mw, mn = pooled(Score.move_wrong)
    hw, hn = pooled(Score.hp_within)
    ww, wn = pooled(Score.wp_within)

    def worst(f, *, low=True):
        rated = [(s.name, *f(s)) for s in scores if f(s)[1]]
        if not rated:
            return None
        name, x, n = (min if low else max)(rated, key=lambda r: r[1] / r[2])
        return {"battle": name, "count": f"{x}/{n}"}

    # Who is on the field may have none wrong: one is a fail, and none decides nothing by itself.
    moves = _verdict(mw, mn, MOVE_WRONG)
    read = "fail" if crit_wrong else moves
    return {
        "battles": len(scores),
        "G-read": {"verdict": read,
                   "who's on the field, wrong": _rate(crit_wrong, crit_n),
                   "moves, wrong": _rate(mw, mn),
                   "worst battle (moves wrong)": worst(Score.move_wrong, low=False)},
        "G-hp": {"verdict": _verdict(hn - hw, hn, 0.01),
                 "outside 1 point": _rate(hn - hw, hn), "worst battle (within)": worst(Score.hp_within)},
        "G-wp": {"verdict": _verdict(wn - ww, wn, 1 - WP_SHARE),
                 "outside 0.03": _rate(wn - ww, wn), "worst battle (within)": worst(Score.wp_within)},
        "note": "The unit is the battle, and the pooled intervals treat entries as independent, "
                "which they are not within a battle: read them as optimistic (principle 5).",
    }
