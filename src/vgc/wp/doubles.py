"""An endgame with at most two Pokémon a side — 2v1, 1v2, 2v2 — as positions the solver searches
(PLAN-endgame-doubles, stage 2). The 1v1 stays with `vgc.wp.endgame`, which this builds on.

What differs from the 1v1:

  the Pokémon      every one left, in field-slot order, each with its own HP, stages, status,
                   item consumed, choice lock, Fake Out freshness and Protect counter
  the search       `SEARCH`: one turn, realistic-play pruning, chance sampled where it is wide,
                   and every 1v1 the search reaches valued by its damage race. A KO does not use
                   up the turn, so a trade is followed into the smaller position
  Speed            three or four investments at once (`speed_classes`). A draw from each
                   Pokémon's prior, weighted by every turn order this battle showed, grouped by the
                   move order it gives on this turn, the heaviest groups solved until `COVER` of the
                   weight is in. The 1v1's joint over two (`belief.speed.joint`) is the same weighing
                   with two keys, done exactly

Open sheets only so far, from a player's seat or the stands. A closed sheet multiplies the
positions by its set candidates, per Pokémon, and comes next.
"""

from __future__ import annotations

import random
from typing import Any

from vgc.regulation import Regulation, to_id
from vgc.wp import endgame, solver

# One turn, then the KO extension: about 6 s for a 2v1 and 1–3 minutes for a 2v2 (stage 1).
SEARCH = {"depth": 1, "prune": 3, "sample": 16, "race": True, "race_1v1": True}
# Speed: draws, the share of their weight solved, and at most this many move orders.
DRAWS = 4000
COVER = 0.9
MAX_ORDERS = 6


# --- when ---------------------------------------------------------------------------------

def actives(state, sid: str) -> list[Any]:
    """The Pokémon `sid` has on the field, in slot order."""
    return sorted(endgame._left(state, sid), key=lambda m: m.position if m.position is not None else 9)


def reason(reg: Regulation, state) -> str | None:
    """Why this is not a position with at most two Pokémon a side that this module builds, or
    None when it is."""
    if not state.started or state.ended:
        return "the battle is not in progress"
    left = {}
    for sid in ("p1", "p2"):
        fainted = sum(m.state == "fainted" for m in state.sides[sid].mons)
        left[sid] = reg.bring - fainted
        if left[sid] > 2:
            return "a side has more than two Pokémon left"
        if len(actives(state, sid)) != left[sid]:
            return "a Pokémon is still in the back"
    if left["p1"] == 1 and left["p2"] == 1:
        return "a 1v1, which `endgame` answers"
    for sid in ("p1", "p2"):
        for m in actives(state, sid):
            if m.volatiles:
                return f"{m.species} has {', '.join(sorted(m.volatiles))}, which the solver cannot set up"
            if m.status and m.status not in endgame.STATUSES:
                return f"{m.species} is {m.status}, whose turn counter is not shown"
    for sid in ("p1", "p2"):
        if not (state.sides[sid].sheet or sid == state.perspective):
            return "a closed sheet, which is not built yet"
    return None


# --- the sets ---------------------------------------------------------------------------------

def sets(reg: Regulation, battle, notes: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Each side's Pokémon on the field as sets, in slot order: yours as you built them (Stat
    Points included), an open sheet's as shown. A side that has not Mega Evolved is assumed to do
    so with its first stone holder only: one Mega a side."""
    from vgc.battle.entry import side_entries

    state = battle.rp.state
    out: dict[str, list[dict[str, Any]]] = {}
    for sid in ("p1", "p2"):
        entries = side_entries(battle.setup, sid)
        side_sets, mega_taken = [], any(x.mega for x in state.sides[sid].mons)
        for m in actives(state, sid):
            e = next(x for x in entries if x["species"] == m.species)
            s = {"species": e["species"], **{k: e.get(k) for k in ("item", "ability", "nature")},
                 "moves": list(e.get("moves") or [])}
            if sid == state.perspective:
                s["sp"] = {k: v for k, v in (e.get("sp") or {}).items() if v}
            if m.mega:
                s["mega"] = m.forme
            elif not mega_taken:
                s["mega"] = endgame._mega(reg, state, sid, m, s.get("item"), notes)
                mega_taken = bool(s["mega"])
            else:
                s["mega"] = None
            side_sets.append(s)
        out[sid] = side_sets
    return out


# --- the state --------------------------------------------------------------------------------

def stall_counts(journal: list[dict[str, Any]], turn: int, stalling: set[str]) -> dict[tuple[str, str], int]:
    """Protect and its kin used on consecutive turns, ending with the last one played: the counter
    that makes the next one work one time in three, nine, ... Moves are matched by id against
    `stalling` (the dex's stalling moves). A journal without species or move ids says nothing."""
    used: dict[tuple[str, str], set[int]] = {}
    now = 0
    for e in journal:
        if e.get("kind") == "turn":
            now = int(e["n"])
        elif e.get("kind") == "move" and e.get("species") and to_id(e.get("move") or "") in stalling:
            used.setdefault((e["side"], e["species"]), set()).add(now)
    out = {}
    for key, turns in used.items():
        n, t = 0, turn - 1
        while t in turns:
            n, t = n + 1, t - 1
        if n:
            out[key] = n
    return out


# Moves that share the Protect counter (Showdown's `stallingMove`, which the exported dex leaves out).
STALLING = {"protect", "detect", "spikyshield", "kingsshield", "banefulbunker", "silktrap",
            "burningbulwark", "obstruct", "endure", "wideguard", "quickguard", "maxguard"}


def facts(reg: Regulation, battle, side_sets: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """The state in the solver's form, one entry a Pokémon in slot order wherever it is per
    Pokémon (the solver's `per`)."""
    state = battle.rp.state
    me = state.perspective
    came = endgame.arrivals(battle.journal)
    stall = stall_counts(battle.journal, state.turn, STALLING)

    def left(since: int | None, duration: int) -> int:
        return max(0, duration - (state.turn - since)) if since is not None else duration

    mons = {sid: actives(state, sid) for sid in ("p1", "p2")}
    out: dict[str, Any] = {
        "active": {sid: len(ms) for sid, ms in mons.items()},
        "hp": {sid: [endgame._our_hp(m) if sid == me and m.hp_max else endgame._their_hp(m) for m in ms]
               for sid, ms in mons.items()},
        "mega": {sid: [bool(s.get("mega")) for s in side_sets[sid]] for sid in mons},
        "fainted": {sid: sum(x.state == "fainted" for x in state.sides[sid].mons) for sid in mons},
        "boosts": {sid: [{k: v for k, v in m.boosts.items() if v} for m in ms] for sid, ms in mons.items()},
        "consumed": {sid: [bool(m.lost_item) and not m.item for m in ms] for sid, ms in mons.items()},
        "status": {sid: [m.status if m.status in endgame.STATUSES else None for m in ms] for sid, ms in mons.items()},
        "sides": {sid: {c: left(since, endgame.SIDE_TURNS[c]) for c, since in state.sides[sid].conditions.items()
                        if c in endgame.SIDE_TURNS and left(since, endgame.SIDE_TURNS[c])} for sid in mons},
        "timesAttacked": {sid: [sum(d.target_side == sid and d.target == m.species for d in state.damage_log)
                                for m in ms] for sid, ms in mons.items()},
        "stall": {sid: [stall.get((sid, m.species), 0) for m in ms] for sid, ms in mons.items()},
        "fresh": {sid: [endgame._fresh(state, sid, m, came) for m in ms] for sid, ms in mons.items()},
        "choicelock": {sid: [to_id(m.last_move) if (to_id(s.get("item") or "") in endgame.CHOICE_ITEMS
                                                    and m.last_move and not (m.lost_item and not m.item)
                                                    and not endgame._fresh(state, sid, m, came)) else None
                             for m, s in zip(ms, side_sets[sid])] for sid, ms in mons.items()},
    }
    for kind in ("weather", "terrain"):
        now = getattr(state, kind)
        turns = left(getattr(state, f"{kind}_since"), endgame.FIELD_TURNS) if now else 0
        out[kind] = [now, turns] if now and turns else None
    tr = state.pseudo.get("trickroom")
    out["trickroom"] = left(tr, endgame.FIELD_TURNS) or None if tr is not None else None
    # A list that says nothing for any Pokémon is left out, as `compose` prunes the 1v1's.
    for k in ("boosts", "consumed", "status", "timesAttacked", "stall", "fresh", "choicelock", "mega"):
        out[k] = {sid: v for sid, v in out[k].items() if any(v)}
    return out


def _one(f: dict[str, Any], sid: str, i: int) -> dict[str, Any]:
    """One Pokémon's slice of `facts`, in the 1v1's form, for `solver.position_speed`."""
    def at(k: str) -> Any:
        v = (f.get(k) or {}).get(sid)
        return v[i] if isinstance(v, list) else None
    return {"boosts": {sid: at("boosts") or {}}, "consumed": {sid: at("consumed")},
            "status": {sid: at("status")}, "sides": f.get("sides") or {}}


# --- Speed ------------------------------------------------------------------------------------

def speed_classes(reg: Regulation, battle, side_sets: dict[str, list[dict[str, Any]]], f: dict[str, Any],
                  draws: int = DRAWS, cover: float = COVER, top: int = MAX_ORDERS, seed: int = 0
                  ) -> tuple[list[dict[str, Any]], float, bool]:
    """Move orders on this turn, each `{weight, sp: {sid: [sp, ...]}, order}`, the weight left
    unsolved, and whether the log contradicted every draw (then the priors alone are used).

    Each Pokémon's Speed investment is drawn from its prior (`endgame._prior`), times what every
    ordering with a Pokémon outside these showed (as `belief.speed.joint` weighs one); an ordering
    between two of these is an indicator on the draw. Your own are known. The draws are grouped by
    the order of the Speeds they give in this position (ties kept), and each group stands for itself
    by its heaviest draw. Seeded, so the same battle writes the same positions."""
    from vgc.belief import speed as sp_belief

    state = battle.rp.state
    me = state.perspective
    keys = [(sid, i) for sid in ("p1", "p2") for i in range(len(side_sets[sid]))]
    species = {k: side_sets[k[0]][k[1]]["species"] for k in keys}
    known = {k: side_sets[k[0]][k[1]]["sp"].get("spe", 0) for k in keys if k[0] == me and "sp" in side_sets[k[0]][k[1]]}
    cap = reg.sp_per_stat_cap
    natures = {(sid, m.species): m.nature for sid, side in state.sides.items() for m in side.mons}
    priors = {(sid, m.species): endgame._prior_mass(reg, m.species, m.nature, m.moves or m.moves_used, m.item or m.lost_item)
              for sid, side in state.sides.items() for m in side.mons if sp_belief.base_speed(reg, m.species)}
    for k in keys:
        priors[(k[0], species[k])] = endgame._prior(reg, side_sets[k[0]][k[1]])

    def support(sk: tuple[str, str]) -> tuple[list[int], list[float]]:
        k = next((x for x in keys if (x[0], species[x]) == sk), None)
        if k is not None and k in known:
            return [known[k]], [1.0]
        p = priors.get(sk) or [1.0] * (cap + 1)
        return list(range(len(p))), list(p)

    def band(ev: Any, sk: tuple[str, str], sp: int) -> tuple[float, float] | None:
        b = sp_belief.speed_band(reg, ev.forme or ev.species, natures.get(sk), sp)
        return None if b is None else (sp_belief.effective_speed(b[0], ev), sp_belief.effective_speed(b[1], ev))

    mine = {(k[0], species[k]): k for k in keys}
    vals = {k: support((k[0], species[k])) for k in keys}
    factor = {k: [1.0] * len(vals[k][0]) for k in keys}
    within = []
    for first, second, sign in sp_belief.pairs(reg, state.moves_log) + sp_belief.ability_pairs(reg, getattr(state, "ability_log", [])):
        k1, k2 = (first.side, first.species), (second.side, second.species)
        if sp_belief.base_speed(reg, first.forme or first.species) is None or \
                sp_belief.base_speed(reg, second.forme or second.species) is None:
            continue
        if k1 in mine and k2 in mine:
            within.append((first, second, sign, mine[k1], mine[k2]))
            continue
        for sk, ev_mine in ((k1, True), (k2, False)):
            if sk not in mine:
                continue
            other = k2 if ev_mine else k1
            ov, op = support(other)
            total = sum(op) or 1.0
            k = mine[sk]
            for i, s in enumerate(vals[k][0]):
                ok = sum(w for o, w in zip(ov, op)
                         if sp_belief._ordered(band(first, k1, s if ev_mine else o),
                                               band(second, k2, o if ev_mine else s), sign))
                factor[k][i] *= ok / total

    rng = random.Random(seed)
    weights = {k: [p * fa for p, fa in zip(vals[k][1], factor[k])] for k in keys}
    contradicted = any(not sum(w) for w in weights.values())
    if contradicted:
        weights = {k: list(vals[k][1]) for k in keys}

    def ok(draw: dict) -> bool:
        return all(sp_belief._ordered(band(a, (ka[0], species[ka]), vals[ka][0][draw[ka]]),
                                      band(b, (kb[0], species[kb]), vals[kb][0][draw[kb]]), sign)
                   for a, b, sign, ka, kb in within)

    speeds: dict[tuple, int] = {}

    def speed_of(k: tuple[str, int], idx: int) -> int:
        if (k, idx) not in speeds:
            s = side_sets[k[0]][k[1]]
            spe = vals[k][0][idx]
            sp = s["sp"] if k in known else solver.spread(reg, s, spe)
            speeds[(k, idx)] = solver.position_speed(reg, s, sp, _one(f, k[0], k[1]), k[0])
        return speeds[(k, idx)]

    def sample(check: bool) -> tuple[dict[tuple, dict[str, Any]], int]:
        groups: dict[tuple, dict[str, Any]] = {}
        kept = 0
        for _ in range(draws):
            draw = {k: rng.choices(range(len(weights[k])), weights=weights[k])[0] for k in keys}
            if check and not ok(draw):
                continue
            kept += 1
            sp = {k: speed_of(k, draw[k]) for k in keys}
            order = tuple(sorted(keys, key=lambda k: -sp[k]))
            ties = tuple(sp[a] == sp[b] for a, b in zip(order, order[1:]))
            g = groups.setdefault((order, ties), {"n": 0, "draws": {}})
            g["n"] += 1
            sig = tuple(draw[k] for k in keys)
            g["draws"][sig] = g["draws"].get(sig, 0) + 1
        return groups, kept

    groups, kept = sample(bool(within) and not contradicted)
    if not kept:
        # The orderings between these Pokémon rule out every draw: the priors alone, said so.
        contradicted = True
        groups, kept = sample(False)
    out, covered = [], 0.0
    for (order, ties), g in sorted(groups.items(), key=lambda kv: -kv[1]["n"]):
        if covered >= cover or len(out) >= top:
            break
        w = g["n"] / kept
        # Each Pokémon's commonest investment in the group, if together they give the group's
        # order; with four Pokémon the commonest whole draw is too rare to be a good stand-in.
        rep = tuple(max(range(len(vals[k][0])), key=lambda i: sum(n for sig, n in g["draws"].items() if sig[x] == i))
                    for x, k in enumerate(keys))
        sp_rep = {k: speed_of(k, i) for k, i in zip(keys, rep)}
        if tuple(sorted(keys, key=lambda k: -sp_rep[k])) != order or \
                tuple(sp_rep[a] == sp_rep[b] for a, b in zip(order, order[1:])) != ties:
            rep = max(g["draws"].items(), key=lambda kv: kv[1])[0]
        sp = {sid: [] for sid in ("p1", "p2")}
        for k, idx in zip(keys, rep):
            s = side_sets[k[0]][k[1]]
            sp[k[0]].append(s["sp"] if k in known else solver.spread(reg, s, vals[k][0][idx]))
        name = "".join(f"{k[0]}{'ab'[k[1]]}" + ("=" if t else ">") for k, t in zip(order, ties + (False,)))[:-1]
        out.append({"weight": w, "sp": sp, "order": name})
        covered += w
    return out, max(0.0, 1.0 - covered), contradicted


# --- the positions ------------------------------------------------------------------------

def compose(reg: Regulation, side_sets: dict[str, list[dict[str, Any]]], f: dict[str, Any],
            search: dict[str, Any]) -> dict[str, Any]:
    """One solver input: each side's Pokémon in slot order, then fainted fillers to four."""
    used = {s["species"] for ss in side_sets.values() for s in ss}
    texts = {}
    for sid, ss in side_sets.items():
        fill = [x for x in solver.FILLERS if x not in used][:reg.bring - len(ss)]
        filler_text = [f"{x}\nAbility: {solver._ability(reg, x)}\n- Protect" for x in fill]
        texts[sid] = "\n\n".join([solver._set_text(s) for s in ss] + filler_text)
    return {"format": reg.showdown_format, "active": f["active"], "p1": texts["p1"], "p2": texts["p2"],
            "setup": solver._pruned({k: v for k, v in f.items() if k != "active"}), "search": search}


def plan(reg: Regulation, battle, search: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every position one answer needs — one a move order — with its weight, or why there is none."""
    search = {**SEARCH, **(search or {})}
    state = battle.rp.state
    why = reason(reg, state)
    if why:
        return {"eligible": False, "reason": why}
    notes: list[str] = []
    side_sets = sets(reg, battle, notes)
    for sid, ss in side_sets.items():
        for s in ss:
            if endgame.UNSOLVABLE_MOVES & {to_id(x) for x in s.get("moves") or []}:
                return {"eligible": False, "reason": f"{s['species']} has Revival Blessing, which would bring "
                                                     "back a Pokémon the solver does not have"}
    f = facts(reg, battle, side_sets)
    orders, unsolved, contradicted = speed_classes(reg, battle, side_sets, f)
    if contradicted:
        notes.append("no set of Speed investments fits the turn order logged, so all of them are counted")
    jobs = []
    for o in orders:
        sides = {sid: [{**s, "sp": sp} for s, sp in zip(side_sets[sid], o["sp"][sid])] for sid in ("p1", "p2")}
        jobs.append({"order": o["order"], "weight": o["weight"], "position": compose(reg, sides, f, search)})
    return {"eligible": bool(jobs), "reason": None if jobs else "no Speed order to solve",
            "kind": f"{f['active']['p1']}v{f['active']['p2']}", "unsolved": round(unsolved, 4),
            "jobs": jobs, "notes": list(dict.fromkeys(notes))}


def combine(jobs: list[dict[str, Any]], results: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    return endgame.combine(jobs, results)


# --- a replay, from the stands ----------------------------------------------------------------

def _two_or_fewer(reg: Regulation, state) -> bool:
    left = [reg.bring - sum(m.state == "fainted" for m in state.sides[sid].mons) for sid in ("p1", "p2")]
    return max(left) <= 2 and left != [1, 1]


def from_replay(reg: Regulation, replay: dict[str, Any]):
    """The replay at the first turn mark where neither side has more than two Pokémon left (and it
    is not a 1v1), or None if it never gets there. The journal records each move's Pokémon and
    move, which the Protect counter needs."""
    from vgc.data.observe import Observer

    o = Observer("spectator", reg.dex)
    journal: list[dict[str, Any]] = []
    for line in replay["log"].split("\n"):
        o.feed(line)
        parts = line.split("|")
        kind = parts[1] if len(parts) > 1 else ""
        if kind in ("switch", "drag") and len(parts) > 2:
            m = o._mon(parts[2])
            if m is not None:
                journal.append({"kind": "switch", "side": parts[2][:2], "species": m.species})
        elif kind == "move" and len(parts) > 3:
            m = o._mon(parts[2])
            journal.append({"kind": "move", "side": parts[2][:2], "species": m.species if m else None,
                            "move": parts[3]})
        elif kind == "turn":
            journal.append({"kind": "turn", "n": int(parts[2])})
            if _two_or_fewer(reg, o):
                break
    else:
        return None
    if not _two_or_fewer(reg, o):
        return None
    setup = {"perspective": "spectator",
             **{sid: [{"species": m.species,
                       "item": (reg.dex.get_item(m.item or m.lost_item) or {}).get("name", m.item or m.lost_item)
                       if (m.item or m.lost_item) else (m.item if m.item == "" else None),
                       "ability": m.ability, "nature": m.nature,
                       "moves": [(reg.dex.get_move(x) or {}).get("name", x) for x in m.moves]}
                      for m in o.sides[sid].mons] for sid in ("p1", "p2")}}
    return endgame.LogBattle(reg, o, setup, journal, replay)
