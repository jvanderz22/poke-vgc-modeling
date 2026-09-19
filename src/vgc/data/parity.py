"""Snapshot ↔ live-play parity.

A player snapshot re-derived from the `inputLog` must describe what that player's poke-env
`DoubleBattle` showed when it chose. Both sides are reduced to the same comparable view:
active Pokémon (species, HP, status, boosts), whether each side has Mega Evolved, fainted
counts, weather, terrain and room effects, side conditions, turn. Differences are returned as
readable strings. Species are compared by base species (regulation dex): poke-env names a Mega
by its base species right after it evolves but by its Mega forme after it switches back in.

Known divergence: poke-env tracks Pokémon by name, so when an opponent's Illusion user and the
Pokémon it imitates are both on the field it misplaces one of them. The snapshot resolves this
(see `Observer._switch_in`). Mismatches in battles where Illusion was broken are reported
separately and don't count as failures.
"""

from __future__ import annotations

from typing import Any

from poke_env.battle.double_battle import DoubleBattle

from vgc.regulation import Dex, to_id

View = dict[str, Any]


def _base(dex: Dex, species: str) -> str:
    s = dex.get_species(species)
    return to_id(s["baseSpecies"]) if s else to_id(species)


def _mon_view(species: str, hp: float, status: str | None, boosts: dict[str, int]) -> dict:
    return {"species": species, "hp": round(hp, 3), "status": status, "boosts": {k: v for k, v in sorted(boosts.items()) if v}}


def live_view(battle: DoubleBattle, dex: Dex) -> View:
    """What poke-env shows the player now."""
    def side(actives, team, conditions, mega) -> dict:
        return {
            "mega_used": bool(mega),
            "active": [None if m is None or m.fainted else _mon_view(
                _base(dex, m.species), m.current_hp_fraction, m.status.name.lower() if m.status else None, m.boosts) for m in actives],
            "fainted": sum(m.fainted for m in team.values()),
            "conditions": sorted(to_id(c.name) for c in conditions),
        }

    me, opp = battle.player_role, "p2" if battle.player_role == "p1" else "p1"
    fields = {to_id(f.name) for f in battle.fields}
    return {
        "turn": battle.turn,
        "weather": sorted(to_id(w.name) for w in battle.weather),
        "fields": sorted(fields),
        me: side(battle.active_pokemon, battle.team, battle.side_conditions, battle.used_mega_evolve),
        opp: side(battle.opponent_active_pokemon, battle.opponent_team, battle.opponent_side_conditions,
                  battle.opponent_used_mega_evolve),
    }


def snapshot_view(obs: dict[str, Any], dex: Dex) -> View:
    """The same view, from a snapshot observation."""
    f = obs["field"]
    out: View = {
        "turn": obs["turn"],
        "weather": [f["weather"]] if f["weather"] else [],
        "fields": sorted(([f["terrain"]] if f["terrain"] else []) + list(f["pseudo"])),
    }
    for sid, side in obs["sides"].items():
        active: list = [None, None]
        for m in side["mons"]:
            if m["state"] == "active":
                active[m["position"]] = _mon_view(_base(dex, m["species"]), m["hp"], m["status"], m["boosts"])
        out[sid] = {
            "mega_used": any(m["mega"] for m in side["mons"]),
            "active": active,
            "fainted": sum(m["state"] == "fainted" for m in side["mons"]),
            "conditions": sorted(side["conditions"]),
        }
    return out


def diff(live: View, snap: View, hp_tol: float = 0.011) -> list[str]:
    """Differences between two views; HP within `hp_tol` counts as equal (the public HP line
    is a floored percentage, poke-env's fraction is of the same number)."""
    out = []
    for key in ("turn", "weather", "fields"):
        if live[key] != snap[key]:
            out.append(f"{key}: live {live[key]} vs snapshot {snap[key]}")
    for sid in ("p1", "p2"):
        a, b = live[sid], snap[sid]
        for key in ("mega_used", "fainted", "conditions"):
            if a[key] != b[key]:
                out.append(f"{sid} {key}: live {a[key]} vs snapshot {b[key]}")
        for slot, (x, y) in enumerate(zip(a["active"], b["active"])):
            if (x is None) != (y is None):
                out.append(f"{sid} slot {slot}: live {x} vs snapshot {y}")
            elif x is not None:
                for k in ("species", "status", "boosts"):
                    if x[k] != y[k]:
                        out.append(f"{sid} slot {slot} {k}: live {x[k]} vs snapshot {y[k]}")
                if abs(x["hp"] - y["hp"]) > hp_tol:
                    out.append(f"{sid} slot {slot} hp: live {x['hp']} vs snapshot {y['hp']}")
    return out


def run_parity(reg, n: int, seed: int = 0) -> dict[str, Any]:
    """Play `n` live battles between random pool teams (alternately heuristic vs heuristic and
    heuristic vs random), capture each player's poke-env view at every accepted choice, then
    re-derive the snapshots from the inputLog and diff them."""
    import random
    from collections import Counter

    from vgc.data.snapshots import trace_snapshots
    from vgc.engine.runner import BattleRunner, RandomPolicy, battle_seed, play_battle
    from vgc.meta.pool import load_pool
    from vgc.policy.heuristic import HeuristicPolicy

    pool = load_pool(reg)
    rng = random.Random(seed)
    heuristic = HeuristicPolicy(reg)
    kinds: Counter = Counter()
    illusion: Counter = Counter()
    examples: dict[str, str] = {}
    checked = missing = 0
    with BattleRunner() as runner:
        for i in range(n):
            a, b = rng.sample(pool, 2)
            live: dict[tuple[str, int], View] = {}
            rec = play_battle(
                runner, f"parity{i}", battle_seed(f"parity{seed}", i), reg.showdown_format, (a.text, b.text),
                (heuristic, heuristic if i % 2 else RandomPolicy()), team_ids=(a.id, b.id),
                on_decision=lambda side, battle, k: live.__setitem__((side, k), live_view(battle, reg.dex)),
            )
            trace = runner.request({"op": "trace", "id": rec.battle_id, "inputLog": rec.input_log, "ots": rec.ots})
            broken_illusion = any(line.startswith("|replace|") for line in rec.log)
            for s in trace_snapshots(trace, rec.summary(), reg):
                p = s["obs"]["perspective"]
                if p not in s["deciding"]:
                    continue
                key = (p, s["choice_index"][p])
                if key not in live:
                    missing += 1
                    continue
                checked += 1
                for d in diff(live[key], snapshot_view(s["obs"], reg.dex)):
                    k = d.split(":")[0]
                    (illusion if broken_illusion else kinds)[k] += 1
                    examples.setdefault(k, f"{rec.battle_id} point {s['point']}: {d}")
    return {"battles": n, "decisions_checked": checked, "missing": missing,
            "mismatches": dict(kinds.most_common()), "illusion_divergences": dict(illusion.most_common()),
            "examples": examples}
