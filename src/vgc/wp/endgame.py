"""A live 1v1 endgame, as the positions `vgc.wp.solver` searches.

When each side has one Pokémon left, the engine can answer what the model only estimates: the
value of the position under best play (`sidecar/showdown/endgame-solver.js`). This module turns a
battle into that question, through the same `solver.compose` the decided-endgame benchmark uses,
so the benchmark's solved positions are also a check on this adapter (`check`).

What goes into a position, and where each piece comes from:

  ours            exactly, from the team you built: set, Stat Points, HP to the point
  theirs          the sheet on an open sheet. On a closed one, the set belief's likeliest sets,
                  each solved and weighted, the rest reported as unsolved mass (`candidates`)
  their Speed     integrated over `belief.prior.speed_prior`, restricted to the Speed SP this
                  battle's turn order allows (`belief.speed.infer`) given the set being solved
  their other SP  `solver.spread`: the main attacking stat maxed, then HP, then the defences.
                  An assumption, and the display says so
  the state       HP, stat stages, non-volatile status, items consumed, a choice lock, weather,
                  terrain, Trick Room and screens with their turns left, the fainted counts (Last
                  Respects), and whether each Pokémon has only just come in (Fake Out)

A closed sheet's set is weighted by what the battle has shown, not only by how often it is
brought: each candidate's weight is its share of sheets times the prior probability of a Speed
investment that could have produced this battle's turn order *with that set's item and nature*. A
Basculegion that outran a 149-Speed Gholdengo cannot be the Adamant Life Orb set, which tops out
at 143, and the weight says so. That is why each candidate replays the journal with its set on the
sheet rather than reading the Speed bound off the closed-sheet state, where an unseen Scarf is
invisible to the channel.

Some positions are not built, and `plan` says why instead: a volatile the solver cannot set up
(Substitute, Encore, ...), sleep or bad poison (their counters are not shown), or a side that does
not have exactly one Pokémon left.
"""

from __future__ import annotations

import copy
from typing import Any

from vgc.regulation import STAT_IDS, Regulation, to_id
from vgc.wp import solver

# Side conditions the solver can set up, and how long each lasts from the turn it was set: the
# featurizer's durations (`features.SIDE_CONDS`). Light Clay's eight turns are not seen.
SIDE_TURNS = {"tailwind": 4, "reflect": 5, "lightscreen": 5, "auroraveil": 5}
FIELD_TURNS = 5
STATUSES = ("brn", "par", "psn")
# Items that lock their holder into its first move. Not read off the dex, which lists only what the
# regulation allows: the engine locks a Choice Band whether or not it is legal to bring one.
CHOICE_ITEMS = {"choiceband", "choicescarf", "choicespecs"}
# A closed sheet's likeliest sets solved per position. Each costs one solve per Speed class.
TOP_SETS = 3


# --- when ---------------------------------------------------------------------------------

def _left(state, sid: str) -> list[Any]:
    return [m for m in state.sides[sid].mons if m.state == "active"]


def reason(reg: Regulation, state) -> str | None:
    """Why this position is not a 1v1 the solver can be asked about, or None when it is."""
    if not state.started or state.ended:
        return "the battle is not in progress"
    for sid in ("p1", "p2"):
        fainted = sum(m.state == "fainted" for m in state.sides[sid].mons)
        if fainted != reg.bring - 1 or len(_left(state, sid)) != 1:
            return "each side needs exactly one Pokémon left"
    for sid in ("p1", "p2"):
        m = _left(state, sid)[0]
        if m.volatiles:
            return f"{m.species} has {', '.join(sorted(m.volatiles))}, which the solver cannot set up"
        if m.status and m.status not in STATUSES:
            return f"{m.species} is {m.status}, whose turn counter is not shown"
    return None


# --- the two sets ---------------------------------------------------------------------------

def _mega(reg: Regulation, state, sid: str, m: Any, item: str | None, notes: list[str]) -> str | None:
    """The forme it is in, or the one it will take: a Pokémon holding its Mega Stone on a side that
    has not Mega Evolved yet is assumed to do so on the first turn, which the solver cannot choose."""
    if m.mega:
        return m.forme
    stone = (reg.dex.get_item(item or "") or {}).get("megaStone") or {}
    forme = stone.get(m.species)
    if forme and not any(x.mega for x in state.sides[sid].mons):
        notes.append(f"{m.species} is assumed to Mega Evolve on the first turn")
        return forme
    return None


def ours(reg: Regulation, battle, notes: list[str]) -> dict[str, Any]:
    """Your Pokémon as you built it. Its Stat Points in the order they were written, zeros left
    out, which is how the benchmark writes them — the text is part of the cache key."""
    state = battle.rp.state
    me = state.perspective
    m = _left(state, me)[0]
    entry = next(e for e in battle.setup["mine"] if e["species"] == m.species)
    s = {"species": entry["species"], "ability": entry["ability"], "nature": entry.get("nature"),
         "moves": list(entry["moves"]), "sp": {k: v for k, v in (entry.get("sp") or {}).items() if v}}
    if entry.get("item"):
        s["item"] = entry["item"]
    s["mega"] = _mega(reg, state, me, m, entry.get("item"), notes)
    return s


def _named(reg: Regulation, sheet: Any, species: str) -> dict[str, Any]:
    """A corpus set (ids) as a set the solver's text can carry: items and moves by name."""
    return {"species": species,
            "item": (reg.dex.get_item(sheet.item) or {}).get("name", sheet.item) if sheet.item else None,
            "ability": sheet.ability, "nature": sheet.nature,
            "moves": [(reg.dex.get_move(x) or {}).get("name", x) for x in sheet.moves]}


def _with_sheet(battle, species: str, s: dict[str, Any]):
    """The battle replayed as if their sheet had shown `s` for `species`."""
    from vgc.battle.entry import replay

    setup = copy.deepcopy(battle.setup)
    for i, e in enumerate(setup["theirs"]):
        if e["species"] == species:
            setup["theirs"][i] = {"species": species, **{k: s.get(k) for k in ("item", "ability", "moves", "nature")}}
    return replay(battle.reg, setup, battle.journal)


def feasible(reg: Regulation, state, species: str) -> tuple[list[int], bool]:
    """Their Speed SP that this battle's turn order allows, and whether it allowed none (in which
    case every value is returned, as the channel itself does)."""
    from vgc.belief import speed

    me = state.perspective
    them = "p2" if me == "p1" else "p1"
    known = {(me, m.species): m.sp["spe"] for m in state.sides[me].mons if m.sp}
    b = speed.infer(reg, state, known).get((them, species))
    if b is None:
        return list(range(reg.sp_per_stat_cap + 1)), False
    return list(b.feasible), b.contradicted


def candidates(reg: Regulation, battle, notes: list[str], top: int = TOP_SETS
               ) -> tuple[list[dict[str, Any]], float]:
    """Their sets to solve, each `{set, weight, feasible}`, and the posterior mass left unsolved.

    An open sheet is one set. A closed sheet is the set belief (`belief.sets.given`), reweighted by
    the turn-order likelihood under each set's item, ability and nature, and cut to the `top`."""
    from vgc.belief import sets as set_belief
    from vgc.belief.prior import speed_prior

    state = battle.rp.state
    them = "p2" if state.perspective == "p1" else "p1"
    m = _left(state, them)[0]
    if state.sides[them].sheet:
        entry = next(e for e in battle.setup["theirs"] if e["species"] == m.species)
        s = {"species": entry["species"], **{k: entry.get(k) for k in ("item", "ability", "nature", "moves")}}
        ok, contradicted = feasible(reg, state, m.species)
        if contradicted:
            notes.append(f"no Speed investment fits the turn order logged for {m.species}, "
                         "so all of them are counted")
        return [{"set": s, "weight": 1.0, "feasible": ok}], 0.0

    belief = set_belief.given(reg, m)
    if not belief.sheets:
        notes.append(f"no set anybody has brought fits {m.species}")
        return [], 1.0
    groups: dict[tuple, tuple[list[int], bool]] = {}
    scored = []
    for sheet in belief.sheets:
        s = _named(reg, sheet, m.species)
        g = (sheet.item, sheet.ability, sheet.nature)
        if g not in groups:
            groups[g] = feasible(reg, _with_sheet(battle, m.species, s).state, m.species)
        ok, contradicted = groups[g]
        prior = speed_prior(reg, m.species, sheet.nature, list(sheet.moves))
        like = 0.0 if contradicted else sum(prior.mass[v] for v in ok)
        scored.append({"set": s, "weight": sheet.count * like, "feasible": ok, "count": sheet.count})
    total = sum(c["weight"] for c in scored)
    if total <= 0:
        notes.append(f"no set fits the turn order logged for {m.species}; weighted by use alone")
        for c in scored:
            c["weight"] = float(c["count"])
            c["feasible"] = list(range(reg.sp_per_stat_cap + 1))
        total = sum(c["weight"] for c in scored)
    scored.sort(key=lambda c: -c["weight"])
    for c in scored:
        c["weight"] /= total
        c.pop("count")
    kept = [c for c in scored[:top] if c["weight"] > 0]
    return kept, max(0.0, 1.0 - sum(c["weight"] for c in kept))


# --- the state --------------------------------------------------------------------------------

def _js_round(x: float) -> int:
    return int(x + 0.5)


def _our_hp(m: Any) -> float | int:
    """HP as the percentage the solver takes, written as the shortest one that gives back the exact
    HP: 30, not 29.94, when both land on 47 of 158. The benchmark writes whole percentages, and a
    battle at the same HP has to write the same key."""
    exact = round(m.hp * m.hp_max)
    guess = round(100 * m.hp)
    for pct in (guess, guess - 1, guess + 1):
        if max(1, _js_round(m.hp_max * pct / 100)) == exact:
            return pct
    return round(100 * exact / m.hp_max, 4)


def _their_hp(m: Any) -> float | int:
    pct = 100 * m.hp
    return int(round(pct)) if abs(pct - round(pct)) < 1e-6 else round(pct, 2)


def arrivals(journal: list[dict[str, Any]]) -> dict[tuple[str, str], tuple[int, bool]]:
    """For each Pokémon, the turn it last came in on, and whether a move has been logged since the
    next turn mark: the part of "has it had a turn out yet" the state does not keep."""
    turn, out = 0, {}
    for e in journal:
        kind = e.get("kind")
        if kind == "turn":
            turn = int(e["n"])
        elif kind in ("lead", "switch"):
            out[(e["side"], e["species"])] = [turn, False]
        elif kind == "move":
            for key, v in out.items():
                if key[0] == e["side"] and turn > v[0]:
                    v[1] = True
    return {k: (t, moved) for k, (t, moved) in out.items()}


def _fresh(state, sid: str, m: Any, came: dict) -> bool:
    """Its first turn out: it came in after the last turn mark, or before it with nothing logged
    for its side since. Fake Out works; a choice item has not locked it yet."""
    if m.last_move:
        return False
    turn, moved = came.get((sid, m.species), (-1, True))
    return turn == state.turn or (turn == state.turn - 1 and not moved)


def facts(reg: Regulation, battle, sets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The state of the two Pokémon in `compose`'s form, given the sets being solved (the choice
    lock depends on theirs)."""
    state = battle.rp.state
    me = state.perspective
    came = arrivals(battle.journal)
    mons = {sid: _left(state, sid)[0] for sid in ("p1", "p2")}

    def left(since: int | None, duration: int) -> int:
        return max(0, duration - (state.turn - since)) if since is not None else duration

    out: dict[str, Any] = {
        "hp": {sid: _our_hp(m) if sid == me and m.hp_max else _their_hp(m) for sid, m in mons.items()},
        "mega": {sid: bool(sets[sid].get("mega")) for sid in mons},
        "fainted": {sid: sum(x.state == "fainted" for x in state.sides[sid].mons) for sid in mons},
        "boosts": {sid: {k: v for k, v in m.boosts.items() if v} for sid, m in mons.items()},
        "consumed": {sid: bool(m.lost_item) and not m.item for sid, m in mons.items()},
        "status": {sid: m.status for sid, m in mons.items() if m.status in STATUSES},
        "sides": {sid: {c: left(since, SIDE_TURNS[c]) for c, since in state.sides[sid].conditions.items()
                        if c in SIDE_TURNS and left(since, SIDE_TURNS[c])} for sid in mons},
        "timesAttacked": {sid: n for sid, m in mons.items()
                          if (n := sum(d.target_side == sid and d.target == m.species for d in state.damage_log))},
    }
    for kind in ("weather", "terrain"):
        now = getattr(state, kind)
        turns = left(getattr(state, f"{kind}_since"), FIELD_TURNS) if now else 0
        out[kind] = [now, turns] if now and turns else None
    tr = state.pseudo.get("trickroom")
    out["trickroom"] = left(tr, FIELD_TURNS) or None if tr is not None else None
    out["choicelock"] = {}
    out["fresh"] = {}
    for sid, m in mons.items():
        if _fresh(state, sid, m, came):
            out["fresh"][sid] = True
        elif to_id(sets[sid].get("item") or "") in CHOICE_ITEMS and not out["consumed"][sid] and m.last_move:
            out["choicelock"][sid] = to_id(m.last_move)
    return out


# --- the positions ------------------------------------------------------------------------

def plan(reg: Regulation, battle, search: dict[str, Any], sets: list[dict[str, Any]] | None = None
         ) -> dict[str, Any]:
    """Every position one answer needs — per candidate set of theirs, per Speed class — with its
    weight, or why there is none. `sets` pins their candidates (`{set, weight}`), which is how the
    benchmark's closed-sheet variants are checked: the truth there is a known set."""
    from vgc.belief.prior import speed_prior

    state = battle.rp.state
    why = reason(reg, state)
    if why:
        return {"eligible": False, "reason": why}
    me = state.perspective
    them = "p2" if me == "p1" else "p1"
    notes: list[str] = []
    mine = ours(reg, battle, notes)
    their_mon = _left(state, them)[0]
    if sets is None:
        cands, unsolved = candidates(reg, battle, notes)
    else:
        cands, unsolved = [], 0.0
        for c in sets:
            ok, _ = feasible(reg, _with_sheet(battle, their_mon.species, c["set"]).state, their_mon.species)
            cands.append({"set": c["set"], "weight": c["weight"], "feasible": ok})
    jobs = []
    for i, c in enumerate(cands):
        theirs = dict(c["set"])
        theirs["mega"] = _mega(reg, state, them, their_mon, theirs.get("item"), notes)
        both = {me: mine, them: theirs}
        f = facts(reg, battle, both)
        prior = speed_prior(reg, theirs["species"], theirs.get("nature"), [to_id(x) for x in theirs["moves"]])
        allowed = set(c["feasible"])
        mass = [w if spe in allowed else 0.0 for spe, w in enumerate(prior.mass)]
        for name, k in solver.partition(reg, mine, theirs, mass, f, sides=(me, them)).items():
            their_sp = solver.spread(reg, theirs, k["spe"])
            sides = {me: mine, them: {**theirs, "sp": their_sp}}
            jobs.append({"set": i, "class": name, "weight": c["weight"] * k["weight"], "spe": k["spe"],
                         "position": solver.compose(reg, sides["p1"], sides["p2"], f, search)})
    return {"eligible": bool(jobs), "reason": None if jobs else "no set of theirs to solve",
            "sets": [{"set": c["set"], "weight": round(c["weight"], 4)} for c in cands],
            "unsolved": round(unsolved, 4), "jobs": jobs, "notes": notes}


def combine(jobs: list[dict[str, Any]], results: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    """The answer over the solved positions, or None until every one of them has one. The value is
    conditional on the solved sets; their weight outside is `plan`'s `unsolved`."""
    if not jobs or any(r is None for r in results):
        return None
    total = sum(j["weight"] for j in jobs)
    return {"value": sum(j["weight"] * r["value"] for j, r in zip(jobs, results)) / total,
            "leaf_mass": sum(j["weight"] * r["leaf_mass"] for j, r in zip(jobs, results)) / total}


# --- the check against the benchmark --------------------------------------------------------

def check(reg: Regulation, search: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every solved benchmark variant, through its hand-entry journal and this adapter: do the
    positions and their weights come out as `solver.plan` wrote them, and what value does that give
    against the stored truth? A closed-sheet variant is checked with its set pinned to the truth's,
    since what the set belief would add is not what the truth assumed."""
    from vgc.battle.entry import Battle
    from vgc.wp import benchmark

    spec = benchmark.load(reg)
    search = {**solver.SEARCH, **(search or {})}
    truth = benchmark.solved(reg)
    want_jobs, _ = solver.plan(reg, spec, search)
    by_variant: dict[str, list] = {}
    for key, name, pos in want_jobs:
        by_variant.setdefault(key, []).append((name, pos))
    cached = solver._cached()
    rows = []
    for key, want in by_variant.items():
        fid, vid = key.split("/")
        fam, var = benchmark._variant(spec, fid, vid)
        setup, journal = benchmark.build(reg, spec, fid, vid)
        battle = Battle(reg, setup, journal)
        pinned = None
        if var.get("sheets", "open") != "open":
            set_var = benchmark._variant(spec, fid, var["truth_as"])[1] if var.get("truth_as") else var
            t = {**fam["build"]["theirs"], **(set_var.get("theirs") or {})}
            pinned = [{"set": {k: t.get(k) for k in ("species", "item", "ability", "nature", "moves")},
                       "weight": 1.0}]
        got = plan(reg, battle, search, sets=pinned)
        classes = (truth.get(key) or {}).get("classes") or {}
        want_w = {n: classes[n]["weight"] for n in classes}
        got_pos = sorted((j["class"], solver.position_key(j["position"])) for j in got.get("jobs", []))
        same = got_pos == sorted((n, solver.position_key(p)) for n, p in want)
        weights = {j["class"]: round(j["weight"], 4) for j in got.get("jobs", [])}
        results = [cached.get(solver.position_key(j["position"])) for j in got.get("jobs", [])]
        value = combine(got.get("jobs", []), results)
        stored = truth.get(key) or {}
        rows.append({"variant": key, "positions_match": same,
                     "weights_match": all(abs(weights.get(n, -1) - w) <= 1e-4 for n, w in want_w.items())
                     and set(weights) == set(want_w),
                     "value": round(value["value"], 4) if value else None, "solved": stored.get("value"),
                     "leaf_mass": stored.get("leaf_mass"), "reason": got.get("reason"),
                     "weights": weights, "want_weights": {n: round(w, 4) for n, w in want_w.items()}})
    return rows
