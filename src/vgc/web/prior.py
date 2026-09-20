"""Most common set per species, counted from the open-team-sheet corpus.

**This is not Phase 5's set prior.** Phase 5 keeps a distribution over an opponent's sets and
updates it as the battle reveals things, so WP becomes an expectation over that belief. This is
the crude ancestor of it: the single most common set for each species, weighted by how often the
sheet appeared. It exists so a closed-sheet team preview can produce *a* legal team to evaluate.

What that costs you, stated plainly because the UI has to repeat it: the model then evaluates one
guessed team with full confidence, instead of averaging over what the opponent might actually be
holding. A Choice Scarf you guessed as Assault Vest is simply wrong, not uncertain.
"""

from __future__ import annotations

import functools
from collections import Counter
from typing import Any

from vgc.regulation import Regulation, to_id
from vgc.teams.sets import PokemonSet
from vgc.teams.showdown_text import export_set, parse_team


def _set_key(mon: PokemonSet) -> tuple:
    return (to_id(mon.item or ""), to_id(mon.ability or ""), (mon.nature or "").lower(),
            tuple(sorted(to_id(m) for m in mon.moves)),
            tuple(sorted((k, v) for k, v in vars(mon.sp).items() if v)))


@functools.lru_cache(maxsize=4)
def usage(reg_id: str) -> dict[str, dict[str, Any]]:
    """`{species_id: {"set": PokemonSet, "count": n, "share": f, "seen": total}}`.

    Counted per *sheet*, so a team that appeared in 40 games counts 40 times — that is what makes
    it usage rather than a list of what exists.
    """
    from vgc.meta.pool import load_pool
    from vgc.regulation import load_regulation

    reg = load_regulation(reg_id)
    per_species: dict[str, Counter] = {}
    examples: dict[str, dict[tuple, PokemonSet]] = {}
    for entry in load_pool(reg):
        try:
            team = parse_team(entry.text)
        except Exception:
            continue
        for mon in team.members:
            sid = mon.species_id
            key = _set_key(mon)
            per_species.setdefault(sid, Counter())[key] += entry.count
            examples.setdefault(sid, {}).setdefault(key, mon)
    out = {}
    for sid, counter in per_species.items():
        key, count = counter.most_common(1)[0]
        total = sum(counter.values())
        out[sid] = {"set": examples[sid][key], "count": count, "share": count / total, "seen": total}
    return out


def fill(species: str, reg: Regulation) -> tuple[PokemonSet, dict[str, Any]]:
    """The most common real set for `species`, or a plain legal one when nobody has used it.

    The fallback matters: 293 species are legal and far fewer appear in any corpus, so a species
    with no usage still has to produce something the simulator will accept.
    """
    sid = to_id(species)
    known = usage(reg.id).get(sid)
    if known:
        mon = PokemonSet(**{**vars(known["set"])})
        return mon, {"source": "usage", "share": round(known["share"], 3), "seen": known["seen"]}

    entry = reg.dex.get_species(species)
    if entry is None:
        raise ValueError(f"{species!r} is not legal in {reg.name}")
    # A neutral, legal default: the first ability, no item, the four moves the dex lists first,
    # and the SP budget spread evenly. It will not be their real spread — the UI says so.
    moves = [m for m in (entry.get("moves") or [])][:4] or ["tackle"]
    mon = PokemonSet(species=entry["name"], ability=(entry.get("abilities") or {}).get("0"),
                     nature="Serious", moves=[m for m in moves], level=reg.level)
    per = reg.sp_budget // 6
    for stat in ("hp", "atk", "def", "spa", "spd", "spe"):
        setattr(mon.sp, stat, min(per, reg.sp_per_stat_cap))
    return mon, {"source": "default", "share": None, "seen": 0}


def compose(species: list[str], reg: Regulation) -> dict[str, Any]:
    """Six species -> a legal Showdown team, with a note per Pokémon saying where its set came from."""
    blocks, notes = [], []
    for name in species:
        mon, note = fill(name, reg)
        blocks.append(export_set(mon))
        notes.append({"species": mon.species, "item": mon.item, "ability": mon.ability,
                      "moves": list(mon.moves), **note})
    return {"text": "\n\n".join(blocks) + "\n", "sets": notes,
            "inferred": sum(1 for n in notes if n["source"] != "given")}
