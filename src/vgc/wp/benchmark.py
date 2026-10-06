"""The decided-endgame benchmark: does WP follow a revealed fact that settles the game?

`benchmarks/wp/<regulation>/decided_endgames.yaml` holds families of positions that differ in one
fact. Each variant is built here as a hand-entry journal — the taps a person would make at the
table — and scored through the app's own WP (`web.live.wp`: the position as shown, with what
has not been revealed left unknown). So a fact counts only if the app can be told it, and the number
scored is the number a person would be shown.

Four scores, as the benchmark file defines them:
  direction   between two variants whose expected answers differ by at least half, does WP move
              the right way, and how far
  distance    |WP − expected| where `expected` is a number
  mixing      with the fact hidden, WP against the belief-weighted mix of the model's own WP on
              the known variants (the model is compared with itself, not with `expected`)
  invariance  |WP(variant) − WP(base)| for facts that cannot matter (F9)

`expected` is hand-reasoned until a solver over the pinned engine replaces it; direction and
invariance do not depend on it, and mixing does not use it at all.

How a journal is laid out, so every variant of a family is the same battle up to its one fact:
  turn 1        leads chosen so any Speed evidence pair is on the field; the evidence moves, in
                order; moves used with nothing to order them against
  turns 2..     one faint a side a turn until only the two Pokémon of the position are left. A
                choice lock is the locked Pokémon's move KOing our last other Pokémon
  5 idle turns  weather, terrain and Trick Room set on the turn that leaves the stated turns
  last turn     HP, stat stages, items consumed or revealed
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vgc import paths
from vgc.regulation import Regulation

STAT_LABEL = {"hp": "HP", "atk": "Atk", "def": "Def", "spa": "SpA", "spd": "SpD", "spe": "Spe"}
SIDE = {"ours": "p1", "theirs": "p2"}
# Two variants are a direction pair when their expected answers are at least this far apart.
DIRECTION_GAP = 0.5
# Weather, terrain and Trick Room last this many turns, as the featurizer counts them.
FIELD_TURNS = 5
FIELD_KEYS = (("weather", "weather_turns_left"), ("terrain", "terrain_turns_left"),
              ("pseudo", "trick_room_turns_left"))


def path(reg: Regulation) -> Path:
    return paths.ROOT / "benchmarks" / "wp" / reg.id / "decided_endgames.yaml"


def solved_path(reg: Regulation) -> Path:
    return path(reg).with_name("solved.json")


def solved(reg: Regulation) -> dict[str, dict[str, Any]]:
    """The engine's answer per variant (`vgc wp solve`), where it has been computed."""
    p = solved_path(reg)
    return json.loads(p.read_text())["truth"] if p.exists() else {}


def load(reg: Regulation) -> dict[str, Any]:
    import yaml

    return yaml.safe_load(path(reg).read_text())


# --- building a position ----------------------------------------------------------------------

def _set_text(s: dict[str, Any]) -> str:
    lines = [f"{s['species']} @ {s['item']}" if s.get("item") else s["species"], f"Ability: {s['ability']}"]
    if s.get("sp"):
        lines.append("EVs: " + " / ".join(f"{v} {STAT_LABEL[k]}" for k, v in s["sp"].items()))
    if s.get("nature"):
        lines.append(f"{s['nature']} Nature")
    return "\n".join(lines + [f"- {m}" for m in s.get("moves", [])])


def _sheet(m: dict[str, Any]) -> dict[str, Any]:
    """What an open team sheet shows: no Stat Points."""
    return {k: m.get(k) for k in ("species", "item", "ability", "moves", "nature")}


def _variant(spec: dict[str, Any], fid: str, vid: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(family, variant) with F9's `base` resolved: the base variant with this one's change on top."""
    fam = next(f for f in spec["families"] if f["id"] == fid)
    var = dict(fam["variants"][vid])
    if "build" not in fam and fam.get("base"):
        base_fid, _, base_vid = fam["base"].partition(" variant ")
        base_fam, base_var = _variant(spec, base_fid, base_vid)
        return base_fam, base_var | var | {"base": f"{base_fid}/{base_vid}"}
    return fam, var


def build(reg: Regulation, spec: dict[str, Any], fid: str, vid: str) -> tuple[dict[str, Any], list[dict]]:
    """The (setup, journal) a person would have entered to reach this variant's position."""
    from vgc.battle.entry import from_team

    fam, var = _variant(spec, fid, vid)
    b = fam["build"]
    ours = {**b["ours"], **(var.get("ours") or {})}
    theirs = {**b["theirs"], **(var.get("theirs") or {})}
    pool = from_team(reg, spec["fillers"] + "\n\n" + (fam.get("extra_ours") or ""))
    by_species = {m["species"]: m for m in pool}

    def team(side: str, endgame: dict[str, Any]) -> tuple[list[dict], list[str]]:
        named = (b.get("fainted") or {}).get(side)
        free = [m for m in pool if m["species"] != endgame["species"] and m["item"] != endgame.get("item")]
        fainted = [by_species[s] for s in named] if named else free[:3]
        rest = [m for m in free if m not in fainted][: reg.team_size - 1 - len(fainted)]
        return fainted + rest, [m["species"] for m in fainted]

    our_end = from_team(reg, _set_text(ours))[0]
    our_others, our_fainted = team("ours", ours)
    their_others, their_fainted = team("theirs", theirs)
    open_sheets = var.get("sheets", "open") == "open"
    their_six = [_sheet(theirs)] + [_sheet(m) for m in their_others]
    setup = {"perspective": "p1", "mine": [our_end] + our_others,
             "theirs": their_six if open_sheets else [{"species": m["species"]} for m in their_six]}

    lock = "locked_into" in var
    evidence = {s: [e[1] for e in (var.get("order") or []) + (var.get("used") or []) if e[0] == s]
                for s in ("p1", "p2")}

    def brought(sid: str, end: str, fainted: list[str], last: bool) -> list[str]:
        if var.get("faint_order") == "reversed" and sid == "p2":
            fainted = fainted[::-1]
        first = [s for s in dict.fromkeys(evidence[sid]) if s != end]
        fillers = first + [s for s in fainted if s not in first]
        return fillers + [end] if last else first + [end] + [s for s in fillers if s not in first]

    order = {"p1": brought("p1", ours["species"], our_fainted, last=lock and var["locked_into"] is not None),
             "p2": brought("p2", theirs["species"], their_fainted, last=lock and var["locked_into"] is None)}
    end = {"p1": ours["species"], "p2": theirs["species"]}
    mega = {SIDE[k]: s["mega"] for k, s in (("ours", ours), ("theirs", theirs)) if s.get("mega")}
    journal: list[dict[str, Any]] = []
    slots: dict[str, list[str | None]] = {"p1": [None, None], "p2": [None, None]}
    queue = {sid: list(o) for sid, o in order.items()}

    def arrive(sid: str, slot: int, kind: str) -> None:
        species = queue[sid].pop(0)
        slots[sid][slot] = species
        journal.append({"kind": kind, "side": sid, "slot": slot, "species": species})
        if species == end[sid] and sid in mega:
            journal.append({"kind": "mega", "side": sid, "species": species, "forme": mega[sid]})

    def slot_of(sid: str, species: str) -> int:
        if species not in slots[sid]:
            raise ValueError(f"{fid}/{vid}: {species} is not on the field on turn 1 for its evidence")
        return slots[sid].index(species)

    for sid in ("p1", "p2"):
        for slot in (0, 1):
            arrive(sid, slot, "lead")
    journal.append({"kind": "turn", "n": 1})
    for sid, species, move in (var.get("order") or []) + (var.get("used") or []):
        journal.append({"kind": "move", "side": sid, "slot": slot_of(sid, species), "move": move})

    turn = 1
    alive = {sid: [s for s in order[sid] if s != end[sid]] for sid in order}
    # Not locked because it has only just come in: their Pokémon arrives on the last turn, after
    # the idle ones, so the journal says what the variant says. Arriving before them would have
    # it out for five turns without moving, which is no position a battle reaches.
    held: int | None = None
    while any(alive.values()):
        turn += 1
        journal.append({"kind": "turn", "n": turn})
        for sid in ("p1", "p2"):
            victim = next((s for s in alive[sid] if s in slots[sid]), None)
            if victim is None:
                continue
            slot = slots[sid].index(victim)
            if (sid == "p2" and lock and var["locked_into"] is None and held is None
                    and queue["p2"] == [end["p2"]]):
                held = slot
                alive[sid].remove(victim)
                continue
            if sid == "p1" and lock and var["locked_into"] and len(alive["p1"]) == 1:
                journal.append({"kind": "move", "side": "p2", "slot": slot_of("p2", end["p2"]),
                                "move": var["locked_into"], "target": {"side": "p1", "slot": slot}})
                journal.append({"kind": "damage", "side": "p1", "slot": slot, "fainted": True})
            else:
                journal.append({"kind": "faint", "side": sid, "slot": slot})
            alive[sid].remove(victim)
            slots[sid][slot] = None
            if queue[sid] and not (sid == "p2" and held is not None):
                arrive(sid, slot, "switch")

    field = dict(b.get("field") or {})
    last = turn + FIELD_TURNS
    setters = {}
    for what, key in FIELD_KEYS:
        value = "Trick Room" if what == "pseudo" else field.get(what)
        left = var.get(key, field.get(key, FIELD_TURNS if value and what != "pseudo" else 0))
        if value and left:
            setters.setdefault(last - (FIELD_TURNS - left), []).append({"kind": "field", "what": what,
                                                                        "value": value, "on": True})
    for t in range(turn + 1, last + 1):
        journal.append({"kind": "turn", "n": t})
        journal.extend(setters.get(t, []))
    if held is not None:
        journal.append({"kind": "faint", "side": "p2", "slot": held})
        arrive("p2", held, "switch")

    for sid, s in (("p1", ours), ("p2", theirs)):
        if s.get("hp", 100) < 100:
            hp = ({"hp": round(s["hp"] / 100 * our_end["stats"]["hp"])} if sid == "p1" else {"pct": s["hp"]})
            journal.append({"kind": "damage", "side": sid, "slot": slots[sid].index(end[sid])} | hp)
    # The stages are the position's. A move in the journal may already have set some (Close Combat
    # locked in, F2-B), so only what the moves leave unexplained is entered by hand.
    want = var.get("boosts") or b.get("boosts") or {}
    if want:
        from vgc.battle.entry import Battle

        slot = slots["p2"].index(end["p2"])
        have = Battle(reg, setup, journal).rp.state.at("p2", slot).boosts
        for stat, stages in want.items():
            if stages - have.get(stat, 0):
                journal.append({"kind": "boost", "side": "p2", "slot": slot, "stat": stat,
                                "stages": stages - have.get(stat, 0)})
    consumed = var.get("consumed") or b.get("consumed")
    if consumed:
        journal.append({"kind": "consume", "side": "p2", "species": end["p2"], "item": consumed})
    for sid, species, what, value in var.get("reveal") or []:
        journal.append({"kind": "reveal", "side": sid, "species": species, "what": what, "value": value})
    return setup, journal


# --- scoring ----------------------------------------------------------------------------------

def _expected(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def score(reg: Regulation, version: str | None = None, *, k: int = 24) -> dict[str, Any]:
    """Every variant's WP, and the four scores. `version` scores one model on every variant; with
    none, each variant is scored by the model pinned for its regime, which is what the app shows."""
    from vgc.battle.entry import replay
    from vgc.web.live import wp
    from vgc.wp.models import CLOSED, OPEN, in_battle_version

    spec = load(reg)
    truth = solved(reg)
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for fam in spec["families"]:
        for vid in fam.get("variants") or {}:
            fid = fam["id"]
            _, var = _variant(spec, fid, vid)
            setup, journal = build(reg, spec, fid, vid)
            rp = replay(reg, setup, journal)
            if rp.errors:
                raise ValueError(f"{fid}/{vid} does not replay: {rp.errors}")
            sheets = OPEN if var.get("sheets", "open") == "open" else CLOSED
            v = version or in_battle_version(reg.id, sheets)
            r = wp(reg, rp.state, v, k=k)
            from vgc.data.snapshots import evidence

            ev = evidence(reg, rp.state)
            rows[(fid, vid)] = {"family": fid, "variant": vid, "sheets": sheets, "version": v,
                                "wp": round(r["wp"], 4), "lo": round(r["lo"], 4), "hi": round(r["hi"], 4),
                                "drawn": round(r["drawn"], 4),
                                # The engine's answer where it has been solved, else the hand one.
                                "hand": _expected(fam["variants"][vid].get("expected")),
                                "solved": (truth.get(f"{fid}/{vid}") or {}).get("value"),
                                "expected": (truth.get(f"{fid}/{vid}") or {}).get(
                                    "value", _expected(fam["variants"][vid].get("expected"))),
                                "ahead": ev["ahead"], "last_move": ev["last_move"],
                                "blind_spot": fam.get("blind_spot"), "base": var.get("base"),
                                "mix": fam["variants"][vid].get("mix")}
    return summarize(rows)


def summarize(rows: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    direction, distance, mixing, invariance = [], [], [], []
    by_family: dict[str, list[dict]] = {}
    for r in rows.values():
        by_family.setdefault(r["family"], []).append(r)
        if r["expected"] is not None:
            distance.append({"variant": f"{r['family']}/{r['variant']}", "error": round(abs(r["wp"] - r["expected"]), 4)})
        if r["mix"]:
            target = sum(w * rows[(r["family"], v)]["wp"] for v, w in r["mix"].items())
            mixing.append({"variant": f"{r['family']}/{r['variant']}", "wp": r["wp"], "mix": round(target, 4),
                           "error": round(r["wp"] - target, 4)})
        if r["base"]:
            bf, bv = r["base"].split("/")
            invariance.append({"variant": f"{r['family']}/{r['variant']}", "base": r["base"],
                               "moved": round(abs(r["wp"] - rows[(bf, bv)]["wp"]), 4)})
    for fid, fam in by_family.items():
        known = [r for r in fam if r["expected"] is not None]
        for i, a in enumerate(known):
            for b in known[i + 1:]:
                gap = b["expected"] - a["expected"]
                if abs(gap) < DIRECTION_GAP:
                    continue
                moved = b["wp"] - a["wp"]
                direction.append({"family": fid, "pair": f"{a['variant']}→{b['variant']}",
                                  "expected": round(gap, 3), "moved": round(moved, 4),
                                  "right_way": bool(moved * gap > 0), "share": round(moved / gap, 3),
                                  "same_model": a["version"] == b["version"]})
    right = [d["right_way"] for d in direction]
    # Decided positions, scored on their own: what the model says where the truth is lost (≤ 5%)
    # and where it is won (≥ 95%). Direction pairs miss a model that moves correctly around the
    # wrong centre; this does not. A model that follows the game separates them by about 0.9.
    lost = [r["wp"] for r in rows.values() if r["expected"] is not None and r["expected"] <= 0.05]
    won = [r["wp"] for r in rows.values() if r["expected"] is not None and r["expected"] >= 0.95]
    mean = lambda xs: round(sum(xs) / len(xs), 4) if xs else None  # noqa: E731
    return {
        "variants": list(rows.values()),
        "direction": direction, "distance": distance, "mixing": mixing, "invariance": invariance,
        "summary": {
            "direction_pairs": len(direction),
            "right_way": round(sum(right) / len(right), 3) if right else None,
            "mean_share_of_gap": round(sum(d["share"] for d in direction) / len(direction), 3) if direction else None,
            "mean_distance": round(sum(d["error"] for d in distance) / len(distance), 4) if distance else None,
            "mean_abs_mix_error": round(sum(abs(m["error"]) for m in mixing) / len(mixing), 4) if mixing else None,
            "max_invariance": max((i["moved"] for i in invariance), default=None),
            "decided": {"lost_n": len(lost), "wp_when_lost": mean(lost), "won_n": len(won),
                        "wp_when_won": mean(won),
                        "separation": round(mean(won) - mean(lost), 4) if lost and won else None},
        },
    }


def format_report(res: dict[str, Any]) -> str:
    lines = [f"{'variant':9} {'sheets':6} {'model':24} {'WP':>6} {'band':>13} {'drawn':>6} {'truth':>5} {'hand':>5}  evidence"]
    for r in res["variants"]:
        exp = f"{r['expected']:.2f}" if r["expected"] is not None else "  —"
        hand = f"{r['hand']:.2f}" if r.get("hand") is not None else "  —"
        ev = "; ".join(f"{a[0]} {a[2]} ≥ {b[0]} {b[2]}" for a, b in r["ahead"])
        lock = {s: m for s, mv in r["last_move"].items() for m in mv.values()}
        lines.append(f"{r['family'] + '/' + r['variant']:9} {r['sheets']:6} {r['version']:24} {r['wp']:6.3f} "
                     f"[{r['lo']:.2f}, {r['hi']:.2f}] {r['drawn']:6.3f} {exp:>5} {hand:>5}  "
                     f"{ev}{'  last ' + json.dumps(lock) if lock else ''}")
    lines.append("\ndirection (expected gap → WP moved):")
    for d in res["direction"]:
        lines.append(f"  {d['family']:4} {d['pair']:6} {d['expected']:+.2f} → {d['moved']:+.3f}  "
                     f"{'ok ' if d['right_way'] else 'WRONG'} share {d['share']:+.2f}"
                     f"{'' if d['same_model'] else '  (two models)'}")
    if res["mixing"]:
        lines.append("mixing (hidden fact vs the model's own mix of the known cases):")
        for m in res["mixing"]:
            lines.append(f"  {m['variant']:6} WP {m['wp']:.3f} vs mix {m['mix']:.3f}  error {m['error']:+.3f}")
    if res["invariance"]:
        lines.append("invariance (must not move):")
        for i in res["invariance"]:
            lines.append(f"  {i['variant']:6} vs {i['base']}: moved {i['moved']:.4f}")
    lines.append("\n" + json.dumps(res["summary"]))
    return "\n".join(lines)
