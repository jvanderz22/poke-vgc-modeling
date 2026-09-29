"""The decided-endgame benchmark's truth, from search over the pinned engine.

Each variant's `expected` was reasoned by hand, and the first solve showed how that goes wrong: F5-A
was written as 0.0 and solved to 0.12, because a Sneasler that has just arrived can Fake Out the
Extreme Speed user. So the truth is computed: `sidecar/showdown/endgame-solver.js` searches the 1v1
as a simultaneous-move game on the real simulator, with chance enumerated rather than sampled
(its `ScriptedPRNG` says why) to `depth` turns. What the search leaves undecided at that depth is
reported as `leaf_mass`; a Protect stall leaves a third of itself per turn.

What the solver cannot be told is their spread, which is hidden in both regimes:

  Speed          integrated over `belief.prior.speed_prior`, the realistic prior over Speed
                 investment, by the move order it produces (`speed_classes`)
  the rest       the main offensive stat maxed, then HP, then the defences. An assumption, and a
                 weak one to rest on: the positions were built so the deciding KO lands for any
                 spread. What does move with the spread is Speed, and that is integrated.

A variant's truth is the expectation given what the variant knows, which is what `expected` has
always meant:
  open sheet     the set on the sheet, over its hidden Speed
  truth_as: X    a closed-sheet variant whose evidence pins it to case X: X's set
  mix            the weighted mix of the known cases' truths
and a Speed ordering the variant names conditions the prior, so "they were faster" means only the
Speed investments that could have been.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np

from vgc import paths
from vgc.regulation import Regulation, to_id
from vgc.wp.benchmark import SIDE, _set_text, _variant, load

SOLVER = paths.ROOT / "sidecar" / "showdown" / "endgame-solver.js"
SEARCH = {"depth": 4}
SPEED_ITEMS = {"choicescarf": 1.5, "ironball": 0.5}
FILLERS = ("Sinistcha", "Dragonite", "Whimsicott", "Pelipper", "Garchomp")


def spread(reg: Regulation, s: dict[str, Any], spe: int) -> dict[str, int]:
    """Their spread with `spe` Speed SP: the main offensive stat maxed from what is left, then HP,
    then the defences.

    One offensive stat, the one most of their damaging moves use (Attack on a tie). Maxing every
    live one gave an Incineroar with Snarl 32 Special Attack and 2 HP, which is nobody's
    Incineroar, and turned F11's crit-or-lose position into a plain KO."""
    moves = [to_id(m) for m in s.get("moves", [])]
    left, cap = reg.sp_budget - spe, reg.sp_per_stat_cap
    out = {k: 0 for k in ("hp", "atk", "def", "spa", "spd")} | {"spe": spe}
    kinds = [(reg.dex.get_move(m) or {}).get("category") for m in moves]
    main = "spa" if kinds.count("Special") > kinds.count("Physical") else "atk"
    for stat in (main, "hp", "def", "spd"):
        out[stat] = min(cap, left)
        left -= out[stat]
    return out


def speed(reg: Regulation, s: dict[str, Any], sp: dict[str, int]) -> float:
    from vgc.teams.sets import PokemonSet, StatPoints, calc_stats

    mon = PokemonSet(species=s["species"], nature=s.get("nature"), sp=StatPoints.from_dict(sp))
    forme = to_id(s["mega"]) if s.get("mega") else None
    return calc_stats(mon, reg.dex, reg, species_id=forme)["spe"] * SPEED_ITEMS.get(to_id(s.get("item") or ""), 1.0)


def _modify(value: int, factors: list[float]) -> int:
    """`value` after a chain of `chainModify` factors, as Showdown does it: the factors multiplied
    in 4096ths with rounding, and the result applied rounding half down. A Scarf on 101 is 151,
    not 151.5, which is a Speed tie with a 151, not a win."""
    mod = 4096
    for f in factors:
        mod = (mod * int(f * 4096) + 2048) >> 12
    return (value * mod + 2047) // 4096


def position_speed(reg: Regulation, s: dict[str, Any], sp: dict[str, int], facts: dict[str, Any],
                   sid: str) -> int:
    """The Speed that orders the turn in this position: the stat after its stage, then the item,
    Tailwind and paralysis, each as the engine applies it. What decides which Speed class a spread
    falls in, so it has to tie where the engine ties."""
    from vgc.teams.sets import PokemonSet, StatPoints, calc_stats

    mon = PokemonSet(species=s["species"], nature=s.get("nature"), sp=StatPoints.from_dict(sp))
    forme = to_id(s["mega"]) if s.get("mega") else None
    stat = calc_stats(mon, reg.dex, reg, species_id=forme)["spe"]
    stage = ((facts.get("boosts") or {}).get(sid) or {}).get("spe", 0)
    stat = stat * (2 + stage) // 2 if stage >= 0 else stat * 2 // (2 - stage)
    factors = []
    if not ((facts.get("consumed") or {}).get(sid)):
        factors.append(SPEED_ITEMS.get(to_id(s.get("item") or ""), 1.0))
    if "tailwind" in ((facts.get("sides") or {}).get(sid) or {}):
        factors.append(2.0)
    out = _modify(stat, [f for f in factors if f != 1.0])
    # Paralysis runs last and is not one more factor: it applies the others (`finalModify`), then
    # halves and floors. A paralysed Scarf holder on 101 is 75, where chaining would say 76.
    if (facts.get("status") or {}).get(sid) == "par":
        out = out * 50 // 100
    return out


def _fits(reg: Regulation, order: list, ours: dict, theirs: dict, their_sp: dict[str, int]) -> bool:
    """Whether their drawn spread could have produced every ordering the variant names. Ours is
    known; theirs is the Pokémon of the position. An ordering between two other Pokémon, or one
    involving a Pokémon of ours that is not in the position, is checked against that Pokémon's set
    from the fillers."""
    if not order:
        return True
    (s1, sp1, _), (s2, sp2, _) = order
    their_speed = speed(reg, theirs, their_sp)
    def of(sid: str, species: str) -> float | None:
        if sid == "p2":
            return their_speed if species == theirs["species"] else None
        return _our_speed(reg, ours, species)
    a, b = of(s1, sp1), of(s2, sp2)
    return a is None or b is None or a >= b


_OURS_EXTRA: dict[str, dict] = {}


def _our_speed(reg: Regulation, ours: dict, species: str) -> float:
    s = ours if species == ours["species"] else _OURS_EXTRA[species]
    return speed(reg, s, s["sp"])


def facts_of(spec: dict, fid: str, vid: str, theirs: dict[str, Any]) -> dict[str, Any]:
    """The state a benchmark variant puts the two Pokémon in, in the form `compose` takes: each
    entry by side, as the live adapter (`vgc.wp.endgame`) produces it from a battle. `theirs` is
    the set being solved, whose item decides whether an evidence move locked it."""
    from vgc.wp.endgame import CHOICE_ITEMS

    fam, var = _variant(spec, fid, vid)
    b = fam["build"]
    ours = {**b["ours"], **(var.get("ours") or {})}
    field = dict(b.get("field") or {})
    weather = var.get("weather_turns_left", field.get("weather_turns_left", 5 if field.get("weather") else 0))
    terrain = var.get("terrain_turns_left", field.get("terrain_turns_left", 5 if field.get("terrain") else 0))
    tr = var.get("trick_room_turns_left", field.get("trick_room_turns_left", 0))
    out: dict[str, Any] = {
        "hp": {"p1": ours.get("hp", 100), "p2": theirs.get("hp", 100)},
        "mega": {SIDE[k]: bool(x.get("mega")) for k, x in (("ours", ours), ("theirs", theirs))},
        "fainted": {"p1": 3, "p2": 3},
        "boosts": {"p2": var.get("boosts") or b.get("boosts") or {}},
        "consumed": {"p2": bool(var.get("consumed") or b.get("consumed"))},
        "weather": [to_id(field["weather"]), weather] if field.get("weather") and weather else None,
        "terrain": [to_id(field["terrain"]), terrain] if field.get("terrain") and terrain else None,
        "trickroom": tr or None,
    }
    if "locked_into" in var:
        if var["locked_into"]:
            out["choicelock"] = {"p2": to_id(var["locked_into"])}
        else:
            out["fresh"] = {"p2": True}
    elif to_id(theirs.get("item") or "") in CHOICE_ITEMS:
        # Their Pokémon makes its evidence moves on turn 1 and stays in (`benchmark.build`), so a
        # choice item has locked it into the last of them. Solving it unlocked answered a position
        # the journal does not describe; for F1-D and F5-D the value came out the same.
        moved = [e[2] for e in (var.get("order") or []) + (var.get("used") or [])
                 if e[0] == "p2" and e[1] == theirs["species"]]
        if moved:
            out["choicelock"] = {"p2": to_id(moved[-1])}
    return out


def _pruned(x: Any) -> Any:
    """Without the entries that say nothing: no stages, no lock, not consumed. The solver reads a
    missing entry as the default, so this changes no position, only how it is written — and the
    written form is the cache key, so a battle and a benchmark that describe one position have to
    write it the same way."""
    if isinstance(x, dict):
        out = {k: _pruned(v) for k, v in x.items()}
        return {k: v for k, v in out.items() if v is not None and v is not False and v != {} and v != []}
    return x


def compose(reg: Regulation, ours: dict[str, Any], theirs: dict[str, Any], facts: dict[str, Any],
            search: dict[str, Any]) -> dict[str, Any]:
    """One solver input: the two sets (theirs with a drawn spread in `sp`), the three fainted
    fillers a side, and the state. Both the benchmark and a live battle are written through this,
    which is what lets the one be checked against the other."""
    fill = [f for f in FILLERS if f not in (ours["species"], theirs["species"])][:3]
    filler_text = "\n\n".join(f"{f}\nAbility: {a}\n- Protect" for f, a in zip(
        fill, [_ability(reg, f) for f in fill]))
    return {"format": reg.showdown_format, "p1": _set_text(ours) + "\n\n" + filler_text,
            "p2": _set_text(theirs) + "\n\n" + filler_text, "setup": _pruned(facts), "search": search}


def position(reg: Regulation, spec: dict, fid: str, vid: str, their_sp: dict[str, int],
             search: dict[str, Any]) -> dict[str, Any]:
    """One solver input: the variant's set (or `truth_as`'s), their drawn spread, the state."""
    fam, var = _variant(spec, fid, vid)
    as_vid = var.get("truth_as")
    set_var = _variant(spec, fid, as_vid)[1] if as_vid else var
    b = fam["build"]
    ours = {**b["ours"], **(var.get("ours") or {})}
    theirs = {**b["theirs"], **(set_var.get("theirs") or {}), "sp": their_sp}
    return compose(reg, ours, theirs, facts_of(spec, fid, vid, theirs), search)


def _ability(reg: Regulation, species: str) -> str:
    return next(iter((reg.dex.get_species(species) or {}).get("abilities", {}).values()), "")


# One line per solved position, keyed on the position and the solver's own source, so a long
# solve that is stopped keeps what it finished — F6 alone ran for hours, and the whole run used to
# be written only at the end — and a change to the solver invalidates everything it produced.
CACHE = paths.ROOT / ".vgc" / "endgame-solver.jsonl"
_cache_lock = threading.Lock()


def position_key(pos: dict[str, Any]) -> str:
    h = hashlib.sha256(SOLVER.read_bytes())
    h.update(json.dumps(pos, sort_keys=True).encode())
    return h.hexdigest()


def _cached() -> dict[str, dict[str, Any]]:
    if not CACHE.exists():
        return {}
    out = {}
    for line in CACHE.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["key"]] = row["result"]
    return out


def remember(pos: dict[str, Any], result: dict[str, Any]) -> None:
    with _cache_lock:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        with CACHE.open("a") as fh:
            fh.write(json.dumps({"key": position_key(pos), "result": result}) + "\n")


def run(pos: dict[str, Any]) -> dict[str, Any]:
    hit = _cached().get(position_key(pos))
    if hit is not None:
        return hit | {"cached": True}
    r = subprocess.run(["node", str(SOLVER), str(paths.SHOWDOWN)], input=json.dumps(pos),
                       capture_output=True, text=True, check=False)
    if r.returncode:
        raise RuntimeError(f"endgame solver failed: {r.stderr.strip()[-500:]}")
    result = json.loads(r.stdout)
    remember(pos, result)
    return result


def speed_classes(reg: Regulation, spec: dict, fid: str, vid: str) -> dict[str, dict[str, Any]]:
    """Their Speed SP, as the prior weights it, grouped by what it does to the move order.

    The spread matters to these positions through the order the two Pokémon move in, so the
    expectation over the prior is exact as a weighted sum over three classes — faster than ours,
    tied, slower — with one solve each. Sampling spreads instead was noisy where it mattered (a
    Scarf Basculegion is slower than the Mega Charizard only below 4 Speed SP, and eight draws put
    that at 0.375 ± 0.17) and eight times the work where it did not. A Speed ordering the variant
    names removes the Speed SP it rules out before the classes are weighed.
    """
    from vgc.belief.prior import speed_prior
    from vgc.teams.showdown_text import parse_team

    fam, var = _variant(spec, fid, vid)
    set_var = _variant(spec, fid, var["truth_as"])[1] if var.get("truth_as") else var
    theirs = {**fam["build"]["theirs"], **(set_var.get("theirs") or {})}
    ours = {**fam["build"]["ours"], **(var.get("ours") or {})}
    _OURS_EXTRA.clear()
    for m in parse_team(spec["fillers"] + "\n\n" + (fam.get("extra_ours") or "")).members:
        _OURS_EXTRA[m.species] = {"species": m.species, "nature": m.nature, "item": m.item, "sp": m.sp.as_dict()}
    prior = speed_prior(reg, theirs["species"], theirs.get("nature"), [to_id(m) for m in theirs.get("moves", [])])
    order = var.get("order") or []
    classes = partition(reg, ours, theirs, prior.mass, facts_of(spec, fid, vid, theirs),
                        lambda sp: _fits(reg, order, ours, theirs, sp))
    if not classes:
        raise ValueError(f"{fid}/{vid}: no Speed investment fits the named ordering")
    return classes


def partition(reg: Regulation, ours: dict[str, Any], theirs: dict[str, Any], mass: list[float],
              facts: dict[str, Any], allowed: Any = None, sides: tuple[str, str] = ("p1", "p2")
              ) -> dict[str, dict[str, Any]]:
    """Their Speed SP, weighted by `mass` over those `allowed` leaves, grouped by the move order it
    gives in this position — faster than ours, tied, slower — each with the SP that carries the
    most weight to stand for it. Empty when nothing is allowed. `sides` are ours and theirs."""
    mine = position_speed(reg, ours, ours["sp"], facts, sides[0])
    classes: dict[str, dict[str, Any]] = {}
    for spe, m in enumerate(mass):
        sp = spread(reg, theirs, spe)
        if m <= 0 or (allowed is not None and not allowed(sp)):
            continue
        theirs_speed = position_speed(reg, theirs, sp, facts, sides[1])
        name = "faster" if theirs_speed > mine else "tied" if theirs_speed == mine else "slower"
        c = classes.setdefault(name, {"mass": 0.0, "spe": spe, "top": 0.0})
        c["mass"] += m
        if m > c["top"]:
            c["spe"], c["top"] = spe, m
    total = sum(c["mass"] for c in classes.values())
    return {k: {"weight": c["mass"] / total, "spe": c["spe"],
                "speed": position_speed(reg, theirs, spread(reg, theirs, c["spe"]), facts, sides[1]),
                "ours": mine} for k, c in classes.items()}


def partition_joint(reg: Regulation, sets: tuple[dict[str, Any], dict[str, Any]], js: Any,
                    facts: dict[str, Any], fixed: tuple[dict | None, dict | None] = (None, None),
                    sides: tuple[str, str] = ("p1", "p2")) -> dict[str, dict[str, Any]]:
    """`partition` with both spreads unknown: every pair of Speed investments in `js`
    (`belief.speed.JointSpeed`), grouped by the move order it gives in this position — the second
    side faster than the first, tied, slower — each represented by its heaviest pair. A side
    whose spread is known passes it in `fixed`, and `js` then has one value for it.

    Still at most three solves a position: what the spreads do to a 1v1 is decide who moves first,
    and the other stats they imply are `spread`'s assumption either way."""
    def speeds(x: int) -> list[tuple[dict[str, int], int]]:
        out = []
        for spe in js.values[x]:
            sp = fixed[x] if fixed[x] is not None else spread(reg, sets[x], spe)
            out.append((sp, position_speed(reg, sets[x], sp, facts, sides[x])))
        return out

    a, b = speeds(0), speeds(1)
    classes: dict[str, dict[str, Any]] = {}
    for i, (_, sa) in enumerate(a):
        for j, (_, sb) in enumerate(b):
            m = js.mass[i][j]
            if m <= 0:
                continue
            name = "faster" if sb > sa else "tied" if sb == sa else "slower"
            c = classes.setdefault(name, {"mass": 0.0, "cell": (i, j), "top": 0.0})
            c["mass"] += m
            if m > c["top"]:
                c["cell"], c["top"] = (i, j), m
    total = sum(c["mass"] for c in classes.values())
    return {k: {"weight": c["mass"] / total,
                "spe": (js.values[0][c["cell"][0]], js.values[1][c["cell"][1]]),
                "sp": (a[c["cell"][0]][0], b[c["cell"][1]][0]),
                "speed": (a[c["cell"][0]][1], b[c["cell"][1]][1])} for k, c in classes.items()}

def plan(reg: Regulation, spec: dict, search: dict[str, Any], only: list[str] | None = None
         ) -> tuple[list[tuple[str, str, dict[str, Any]]], dict[str, dict[str, dict[str, Any]]]]:
    """The solver inputs a solve needs — one per variant and Speed class — without running any."""
    jobs: list[tuple[str, str, dict[str, Any]]] = []
    classes: dict[str, dict[str, dict[str, Any]]] = {}
    for fam in spec["families"]:
        for vid, v in (fam.get("variants") or {}).items():
            key = f"{fam['id']}/{vid}"
            if only and key not in only and fam["id"] not in only:
                continue
            if v.get("mix") or ("build" not in fam and fam.get("base")):
                continue
            classes[key] = speed_classes(reg, spec, fam["id"], vid)
            for name, c in classes[key].items():
                their_sp = spread(reg, {**fam["build"]["theirs"], **((_variant(spec, fam["id"], v["truth_as"])[1]
                                  if v.get("truth_as") else v).get("theirs") or {})}, c["spe"])
                jobs.append((key, name, position(reg, spec, fam["id"], vid, their_sp, search)))
    return jobs, classes


def solve(reg: Regulation, *, workers: int = 6, search: dict[str, Any] | None = None,
          only: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Every variant's truth: solved directly (one solve per Speed class, all in one pool), as a mix
    of solved variants, or as its base (F9)."""
    spec = load(reg)
    search = {**SEARCH, **(search or {})}
    jobs, classes = plan(reg, spec, search, only)
    out: dict[str, dict[str, Any]] = {}
    import time
    from concurrent.futures import as_completed

    results: list[dict[str, Any]] = [{}] * len(jobs)
    t0 = time.perf_counter()
    with ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(run, j[2]): i for i, j in enumerate(jobs)}
        for n, f in enumerate(as_completed(futures), 1):
            i = futures[f]
            results[i] = f.result()
            print(f"  [{n}/{len(jobs)} {time.perf_counter() - t0:6.0f}s] {jobs[i][0]} {jobs[i][1]}: "
                  f"{results[i]['value']:.3f} (leaf {results[i]['leaf_mass']:.3f}, "
                  f"{results[i]['ms'] / 1000:.0f}s)", flush=True)
    for (key, name, _), r in zip(jobs, results):
        classes[key][name] |= {"value": r["value"], "leaf_mass": r["leaf_mass"], "nodes": r["nodes"]}
    for key, cs in classes.items():
        out[key] = {"value": round(sum(c["weight"] * c["value"] for c in cs.values()), 4),
                    "leaf_mass": round(sum(c["weight"] * c["leaf_mass"] for c in cs.values()), 4),
                    "classes": {k: {kk: (round(vv, 4) if isinstance(vv, float) else vv) for kk, vv in c.items()}
                                for k, c in cs.items()},
                    "search": search}
        print(f"{key:7} {out[key]['value']:.3f}  leaf {out[key]['leaf_mass']:.3f}  "
              + "  ".join(f"{k} {c['weight']:.2f}→{c['value']:.2f}" for k, c in cs.items()), flush=True)
    for fam in spec["families"]:
        for vid, v in (fam.get("variants") or {}).items():
            key = f"{fam['id']}/{vid}"
            if v.get("mix") and all(f"{fam['id']}/{k}" in out for k in v["mix"]):
                parts = {k: out[f"{fam['id']}/{k}"]["value"] for k in v["mix"]}
                out[key] = {"value": round(sum(w * parts[k] for k, w in v["mix"].items()), 4), "mix": v["mix"]}
            elif "build" not in fam and fam.get("base"):
                base_fid, _, base_vid = fam["base"].partition(" variant ")
                if f"{base_fid}/{base_vid}" in out:
                    out[key] = {"value": out[f"{base_fid}/{base_vid}"]["value"], "as": f"{base_fid}/{base_vid}"}
    return out
