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

Two closed sheets, as a closed-sheet replay is seen from the stands, are answered over pairs of
likely sets (`pair_candidates`): the replay is read again with each pair shown on the sheets, so
the turn order weighs the pair jointly. A hand-entered battle cannot be read again that way, and
with both sheets closed it is not built.

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
# Moves the solver cannot play: Revival Blessing brings back a fainted teammate, and the solver's
# fainted teammates are stand-ins. A set with one is not solved; its weight is left unsolved.
UNSOLVABLE_MOVES = {"revivalblessing"}
# Items that lock their holder into its first move. Not read off the dex, which lists only what the
# regulation allows: the engine locks a Choice Band whether or not it is legal to bring one.
CHOICE_ITEMS = {"choiceband", "choicescarf", "choicespecs"}
# A closed sheet's likeliest sets solved per position. Each costs one solve per Speed class.
TOP_SETS = 3


# --- when ---------------------------------------------------------------------------------

def _left(state, sid: str) -> list[Any]:
    return [m for m in state.sides[sid].mons if m.state == "active"]


def reason(reg: Regulation, state, both_closed: bool = False) -> str | None:
    """Why this position is not a 1v1 the solver can be asked about, or None when it is.
    `both_closed` says two closed sheets can be answered: a replay can be read again with each
    candidate pair of sets shown (`LogBattle.with_sets`), a hand-entered battle cannot."""
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
    if not both_closed and not any(state.sides[sid].sheet or sid == state.perspective for sid in ("p1", "p2")):
        return "neither side's set is known"
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


def own_set(reg: Regulation, battle, sid: str, notes: list[str]) -> dict[str, Any]:
    """Your Pokémon as you built it. Its Stat Points in the order they were written, zeros left
    out, which is how the benchmark writes them — the text is part of the cache key."""
    from vgc.battle.entry import side_entries

    state = battle.rp.state
    m = _left(state, sid)[0]
    entry = next(e for e in side_entries(battle.setup, sid) if e["species"] == m.species)
    s = {"species": entry["species"], "ability": entry["ability"], "nature": entry.get("nature"),
         "moves": list(entry["moves"]), "sp": {k: v for k, v in (entry.get("sp") or {}).items() if v}}
    if entry.get("item"):
        s["item"] = entry["item"]
    s["mega"] = _mega(reg, state, sid, m, entry.get("item"), notes)
    return s


def _named(reg: Regulation, sheet: Any, species: str) -> dict[str, Any]:
    """A corpus set (ids) as a set the solver's text can carry: items and moves by name."""
    return {"species": species,
            "item": (reg.dex.get_item(sheet.item) or {}).get("name", sheet.item) if sheet.item else None,
            "ability": sheet.ability, "nature": sheet.nature,
            "moves": [(reg.dex.get_move(x) or {}).get("name", x) for x in sheet.moves]}


def _with_sheet(battle, sid: str, species: str, s: dict[str, Any]):
    """The battle replayed as if `sid`'s sheet had shown `s` for `species`."""
    from vgc.battle.entry import replay, side_entries

    setup = copy.deepcopy(battle.setup)
    entries = side_entries(setup, sid)
    for i, e in enumerate(entries):
        if e["species"] == species:
            entries[i] = {"species": species, **{k: s.get(k) for k in ("item", "ability", "moves", "nature")}}
    return replay(battle.reg, setup, battle.journal)


_PRIORS: dict[tuple, list[float]] = {}


def _prior_mass(reg: Regulation, species: str, nature: str | None, moves: Any,
                item: str | None = None) -> list[float]:
    """`speed_prior`'s mass, remembered: a closed sheet asks it for every Pokémon under every
    candidate set, and it depends on only these."""
    from vgc.belief.prior import speed_prior

    key = (reg.id, species, nature, tuple(sorted(to_id(x) for x in moves or [])), to_id(item or ""))
    if key not in _PRIORS:
        _PRIORS[key] = speed_prior(reg, species, nature, list(key[3]), item=item).mass
    return _PRIORS[key]


def _prior(reg: Regulation, s: dict[str, Any]) -> list[float]:
    return _prior_mass(reg, s["species"], s.get("nature"), s.get("moves"), s.get("item"))


def speed_joint(reg: Regulation, state, sets: dict[str, dict[str, Any]]):
    """The joint weight on the two Pokémon's Speed investments (`belief.speed.joint`), with the sets
    being solved. Everyone else's prior is read off the state: its nature, and its moves as far as
    they are known. Your own side is known exactly."""
    from vgc.belief import speed

    me = state.perspective
    keys = tuple((sid, _left(state, sid)[0].species) for sid in ("p1", "p2"))
    known = ({(me, m.species): m.sp["spe"] for m in state.sides[me].mons if m.sp}
             if me in state.sides else {})
    priors = {(sid, m.species): _prior_mass(reg, m.species, m.nature, m.moves or m.moves_used,
                                            m.item or m.lost_item)
              for sid, side in state.sides.items() for m in side.mons if speed.base_speed(reg, m.species)}
    priors.update({k: _prior(reg, sets[k[0]]) for k in keys})
    return speed.joint(reg, state, keys, priors, known)


def candidates(reg: Regulation, battle, sid: str, notes: list[str], top: int = TOP_SETS
               ) -> tuple[list[dict[str, Any]], float]:
    """`sid`'s sets to solve, each `{set, weight}`, and the weight left unsolved.

    Yours is the set you built; an open sheet is the sheet. A closed sheet is the set belief
    (`belief.sets.given`), reweighted by how likely this battle's turn order is under each set's
    item, ability and nature — the joint Speed weight's total — and cut to the `top`."""
    from vgc.battle.entry import side_entries
    from vgc.belief import sets as set_belief

    state = battle.rp.state
    m = _left(state, sid)[0]
    if sid == state.perspective:
        return [{"set": own_set(reg, battle, sid, notes), "weight": 1.0, "known": True}], 0.0
    if state.sides[sid].sheet:
        entry = next(e for e in side_entries(battle.setup, sid) if e["species"] == m.species)
        s = {"species": entry["species"], **{k: entry.get(k) for k in ("item", "ability", "nature", "moves")}}
        return [{"set": s, "weight": 1.0}], 0.0

    other = "p2" if sid == "p1" else "p1"
    if other != state.perspective:
        return [], 1.0                     # two closed sheets: not built (`reason` says so)
    mine = own_set(reg, battle, other, [])
    belief = set_belief.given(reg, m)
    if not belief.sheets:
        notes.append(f"no set anybody has brought fits {m.species}")
        return [], 1.0
    replays: dict[tuple, Any] = {}
    likes: dict[tuple, float] = {}
    scored = []
    for sheet in belief.sheets:
        s = _named(reg, sheet, m.species)
        g = (sheet.item, sheet.ability, sheet.nature)
        if g not in replays:
            replays[g] = _with_sheet(battle, sid, m.species, s).state
        # The likelihood depends on the set through its item, ability and nature (the replay) and
        # its Speed prior, which its moves change only by which stats are dead.
        lk = g + (tuple(_prior(reg, s)),)
        if lk not in likes:
            js = speed_joint(reg, replays[g], {sid: s, other: mine})
            likes[lk] = 0.0 if js.contradicted else js.total
        like = likes[lk]
        scored.append({"set": s, "weight": sheet.count * like, "count": sheet.count})
    total = sum(c["weight"] for c in scored)
    if total <= 0:
        notes.append(f"no set fits the turn order logged for {m.species}; weighted by use alone")
        for c in scored:
            c["weight"] = float(c["count"])
        total = sum(c["weight"] for c in scored)
    scored.sort(key=lambda c: -c["weight"])
    for c in scored:
        c["weight"] /= total
        c.pop("count")
    kept = [c for c in scored[:top] if c["weight"] > 0]
    return kept, max(0.0, 1.0 - sum(c["weight"] for c in kept))


def pair_candidates(reg: Regulation, battle, notes: list[str], top: int = 16, cover: float = 0.9
                    ) -> tuple[list[dict[str, Any]], float]:
    """Both sides' sets, for a closed-sheet game seen from the stands, where neither is known.

    `candidates` weighs one side's sets against the other's known one. Here neither is known, so
    the weight belongs to the pair: how often each set is brought, times how likely this battle's
    turn order is with both of them shown (the joint Speed weight's total). The replay is read
    again with each pair on the sheets (`LogBattle.with_sets`), because an item acts on the turn
    order as the log is read. The heaviest `top` pairs are kept, each with the battle read with
    exactly its two sets shown, heaviest first until `cover` of the weight is in or `top` pairs are,
    and the weight left over is unsolved."""
    from vgc.belief import sets as set_belief

    state = battle.rp.state
    beliefs = {}
    for sid in ("p1", "p2"):
        m = _left(state, sid)[0]
        b = set_belief.given(reg, m)
        if not b.sheets:
            notes.append(f"no set anybody has brought fits {m.species}")
            return [], 1.0
        beliefs[sid] = [(_named(reg, sh, m.species), sh.count, (sh.item, sh.ability, sh.nature)) for sh in b.sheets]
    views: dict[tuple, Any] = {}
    likes: dict[tuple, float] = {}
    scored = []
    for s1, n1, g1 in beliefs["p1"]:
        for s2, n2, g2 in beliefs["p2"]:
            # As in `candidates`: the likelihood depends on a set through its item, ability and
            # nature (the reading of the log) and its Speed prior.
            g = (g1, g2)
            if g not in views:
                views[g] = battle.with_sets({"p1": s1, "p2": s2})
            lk = g + (tuple(_prior(reg, s1)), tuple(_prior(reg, s2)))
            if lk not in likes:
                js = speed_joint(reg, views[g].rp.state, {"p1": s1, "p2": s2})
                likes[lk] = 0.0 if js.contradicted else js.total
            scored.append({"sets": (s1, s2), "weight": n1 * n2 * likes[lk], "count": n1 * n2})
    total = sum(c["weight"] for c in scored)
    if total <= 0:
        notes.append("no pair of sets fits the turn order logged; weighted by use alone")
        for c in scored:
            c["weight"] = float(c["count"])
        total = sum(c["weight"] for c in scored)
    scored.sort(key=lambda c: -c["weight"])
    kept: list[dict[str, Any]] = []
    for c in scored[:top]:
        if c["weight"] <= 0 or sum(k["weight"] for k in kept) >= cover:
            break
        kept.append({"sets": c["sets"], "weight": c["weight"] / total,
                     "battle": battle.with_sets({"p1": c["sets"][0], "p2": c["sets"][1]})})
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
    """Every position one answer needs — per pair of sets being solved, per Speed class — with its
    weight, or why there is none. `sets` pins the opposing side's candidates (`{set, weight}`),
    which is how the benchmark's closed-sheet variants are checked: the truth there is a known set.

    From a player's seat one side is known and the other is a sheet or a belief. From a
    spectator's, both are sheets and both Speed investments are integrated (`speed_joint`)."""
    from vgc.wp.solver import compose, partition_joint

    state = battle.rp.state
    closed_both = sets is None and not any(state.sides[sid].sheet or sid == state.perspective
                                           for sid in ("p1", "p2"))
    why = reason(reg, state, both_closed=hasattr(battle, "with_sets"))
    if why:
        return {"eligible": False, "reason": why}
    notes: list[str] = []
    unsolved = 0.0

    def unplayable(s: dict[str, Any]) -> bool:
        return bool(UNSOLVABLE_MOVES & {to_id(x) for x in s.get("moves") or []})

    # What to solve: pairs of sets, each with its weight, and the battle as it reads with those
    # sets shown when that differs from `battle` (two closed sheets from the stands).
    combos: list[dict[str, Any]] = []
    if closed_both:
        pairs, unsolved = pair_candidates(reg, battle, notes)
        playable = [c for c in pairs if not any(unplayable(x) for x in c["sets"])]
        if pairs and not playable:
            return {"eligible": False, "reason": "every likely pair of sets has Revival Blessing, which would "
                                                 "bring back a Pokémon the solver does not have"}
        if len(playable) < len(pairs):
            unsolved += sum(c["weight"] for c in pairs) - sum(c["weight"] for c in playable)
            notes.append("pairs of sets with Revival Blessing are not solved")
        combos = [{"sets": ({"set": c["sets"][0]}, {"set": c["sets"][1]}), "weight": c["weight"],
                   "battle": c["battle"]} for c in playable]
    else:
        cands: dict[str, list[dict[str, Any]]] = {}
        for sid in ("p1", "p2"):
            if sets is not None and sid != state.perspective:
                cands[sid] = [dict(c) for c in sets]
            else:
                cands[sid], u = candidates(reg, battle, sid, notes)
                unsolved = max(unsolved, u)
            playable = [c for c in cands[sid] if not unplayable(c["set"])]
            if len(playable) < len(cands[sid]):
                if not playable:
                    return {"eligible": False, "reason": f"{_left(state, sid)[0].species} has Revival Blessing, "
                                                         "which would bring back a Pokémon the solver does not have"}
                unsolved = max(unsolved, 1 - sum(c["weight"] for c in playable))
                notes.append(f"{_left(state, sid)[0].species}'s sets with Revival Blessing are not solved")
                cands[sid] = playable
        combos = [{"sets": (c1, c2), "weight": c1["weight"] * c2["weight"], "battle": None}
                  for c1 in cands["p1"] for c2 in cands["p2"]]

    # Each side's distinct sets, for the summary and for the jobs' indices into it. A side's own
    # candidates keep their weights; from pairs, a set's weight is its share of the pairs kept.
    listed: dict[str, list[dict[str, Any]]] = {"p1": [], "p2": []}
    shares: dict[str, list[float]] = {"p1": [], "p2": []}
    for combo in combos:
        for sid, c in zip(("p1", "p2"), combo["sets"]):
            if c["set"] not in listed[sid]:
                listed[sid].append(c["set"])
                shares[sid].append(0.0 if closed_both else c["weight"])
            if closed_both:
                shares[sid][listed[sid].index(c["set"])] += combo["weight"]

    jobs = []
    for combo in combos:
        c1, c2 = combo["sets"]
        pair = {"p1": dict(c1["set"]), "p2": dict(c2["set"])}
        seen = combo["battle"] or battle
        view = seen.rp.state
        for sid, c in (("p1", c1), ("p2", c2)):
            m = _left(state, sid)[0]
            pair[sid]["mega"] = pair[sid].get("mega") or _mega(reg, state, sid, m, pair[sid].get("item"), notes)
            if combo["battle"] is None and not c.get("known") and not state.sides[sid].sheet:
                # A set from the belief, or pinned: the turn order is read with it on the sheet,
                # where an unseen Scarf is invisible to the Speed channel.
                view = _with_sheet(battle, sid, m.species, pair[sid]).state
        js = speed_joint(reg, view, pair)
        if js.contradicted:
            notes.append("no pair of Speed investments fits the turn order logged, so all of them "
                         "are counted")
        f = facts(reg, seen, pair)
        fixed = tuple(pair[sid]["sp"] if c.get("known") else None for sid, c in (("p1", c1), ("p2", c2)))
        for name, k in partition_joint(reg, (pair["p1"], pair["p2"]), js, f, fixed).items():
            sides = {sid: {**pair[sid], "sp": sp} for sid, sp in zip(("p1", "p2"), k["sp"])}
            jobs.append({"sets": (listed["p1"].index(c1["set"]), listed["p2"].index(c2["set"])), "class": name,
                         "weight": combo["weight"] * k["weight"], "spe": k["spe"],
                         "position": compose(reg, sides["p1"], sides["p2"], f, search)})
    return {"eligible": bool(jobs), "reason": None if jobs else "no set to solve",
            "sets": {sid: [{"set": {k: v for k, v in x.items() if k not in ("sp", "mega")},
                            "weight": round(w, 4)} for x, w in zip(listed[sid], shares[sid])]
                     for sid in ("p1", "p2")},
            "unsolved": round(min(unsolved, 1.0), 4), "jobs": jobs, "notes": list(dict.fromkeys(notes))}


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


# --- a replay, from the stands (PLAN-v3 step 3.6) -------------------------------------------------

class LogBattle:
    """A public replay stopped at its first 1v1, in the shape `plan` reads: the state (the log's own
    `Observer`), the two open sheets as a spectator's setup, and a journal of just what `arrivals`
    needs — turn marks, arrivals and moves — written as the log is fed."""

    def __init__(self, reg: Regulation, state, setup: dict[str, Any], journal: list[dict[str, Any]],
                 replay: dict[str, Any] | None = None):
        self.reg, self.setup, self.journal, self.replay = reg, setup, journal, replay
        self.rp = type("Replayed", (), {"state": state})()

    def with_sets(self, sets: dict[str, dict[str, Any]]) -> LogBattle:
        """The same replay read again as if each side's sheet had shown `sets[sid]` for the
        Pokémon it has left: what a closed sheet's candidate sets are weighed and solved with."""
        out = from_replay(self.reg, self.replay, reveal=sets)
        assert out is not None, "a replay that reached a 1v1 reaches it again"
        return out


def _one_left(state) -> bool:
    return all(sum(m.state == "fainted" for m in state.sides[sid].mons) == 3 and len(_left(state, sid)) == 1
               for sid in ("p1", "p2"))


def _showteam(reg: Regulation, sid: str, s: dict[str, Any]) -> str:
    """A `|showteam|` line naming one set, in Showdown's packed form, as an open sheet logs it."""
    item = to_id(s.get("item") or "")
    moves = ",".join(to_id(x) for x in s.get("moves") or [])
    return f"|showteam|{sid}|{s['species']}||{item}|{to_id(s.get('ability') or '')}|{moves}|{s.get('nature') or ''}||||||50|"


def from_replay(reg: Regulation, replay: dict[str, Any],
                reveal: dict[str, dict[str, Any]] | None = None) -> LogBattle | None:
    """The replay at the turn mark where each side first has one Pokémon left, or None if it never
    gets there. The position is the one about to be played, as the app's is.

    `reveal` reads it as if each side's sheet had shown that set for that species, from the
    first line: a `|showteam|` line is put in after the team is announced, the way an open-sheet
    log carries one. A closed sheet's candidate sets are weighed this way (`pair_candidates`)."""
    from vgc.data.observe import Observer

    o = Observer("spectator", reg.dex)
    journal: list[dict[str, Any]] = []
    lines = replay["log"].split("\n")
    if reveal:
        at = next((i for i, x in enumerate(lines) if x.startswith(("|teampreview", "|start"))), None)
        assert at is not None, "a log announces its teams before it starts"
        lines = lines[:at] + [_showteam(reg, sid, s) for sid, s in reveal.items()] + lines[at:]
    for line in lines:
        o.feed(line)
        parts = line.split("|")
        kind = parts[1] if len(parts) > 1 else ""
        if kind in ("switch", "drag") and len(parts) > 2:
            m = o._mon(parts[2])
            if m is not None:
                journal.append({"kind": "switch", "side": parts[2][:2], "species": m.species})
        elif kind == "move" and len(parts) > 2:
            journal.append({"kind": "move", "side": parts[2][:2]})
        elif kind == "turn":
            journal.append({"kind": "turn", "n": int(parts[2])})
            if _one_left(o):
                break
    else:
        return None
    if not _one_left(o):
        return None

    def sheet(m: Any) -> dict[str, Any]:
        return {"species": m.species,
                "item": (reg.dex.get_item(m.item) or {}).get("name", m.item) if m.item else (m.item if m.item == "" else None),
                "ability": m.ability, "nature": m.nature,
                "moves": [(reg.dex.get_move(x) or {}).get("name", x) for x in m.moves]}

    # The sheet as shown: an item consumed since is still the one it brought.
    setup = {"perspective": "spectator",
             **{sid: [sheet(m) | ({"item": (reg.dex.get_item(m.lost_item) or {}).get("name", m.lost_item)}
                                  if m.lost_item and not m.item else {})
                      for m in o.sides[sid].mons] for sid in ("p1", "p2")}}
    return LogBattle(reg, o, setup, journal, replay)
