"""Phase 1 verification: `vgc calc` reproduces the simulator's damage rolls exactly.

Ground truth is the pinned Showdown itself: each scenario plays one scripted turn across
many seeds and every observed (non-crit) hit must be one of the calc's 16 rolls, with
the observed spread covering the calc's range end to end.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from vgc import paths
from vgc.teams.showdown_text import parse_set

pytestmark = [pytest.mark.showdown, pytest.mark.calc]

FILLER = {
    # Inert partners: Splash only, no entry abilities.
    "garchomp": "Garchomp\nAbility: Rough Skin\nEVs: 1 HP\n- Splash",
    "rillaboom": "Rillaboom\nAbility: Overgrow\nEVs: 1 HP\n- Splash",
    "incineroar": "Incineroar\nAbility: Blaze\nEVs: 1 HP\n- Splash",
    "kingambit": "Kingambit\nAbility: Defiant\nEVs: 1 HP\n- Splash",
}

SCENARIOS = [
    pytest.param(
        dict(
            attacker="Kingambit @ Black Glasses\nAbility: Defiant\nEVs: 32 HP / 32 Atk / 2 Spe\nAdamant Nature\n- Kowtow Cleave",
            move="Kowtow Cleave",
            p1choice="move 1 1, move 1",
            defenders={"p2a": "Sinistcha\nAbility: Hospitality\nEVs: 32 HP / 16 Def / 18 SpD\nBold Nature\n- Splash"},
            field={},
        ),
        id="single-target-item-boost",
    ),
    pytest.param(
        dict(
            attacker="Rillaboom @ Miracle Seed\nAbility: Grassy Surge\nEVs: 32 Atk / 32 Spe / 2 HP\nAdamant Nature\n- Grassy Glide",
            move="Grassy Glide",
            p1choice="move 1 1, move 1",
            defenders={"p2a": "Garchomp\nAbility: Rough Skin\nEVs: 32 HP / 32 Def / 2 SpD\nImpish Nature\n- Splash"},
            field={"terrain": "Grassy"},
        ),
        id="terrain-boost-priority",
    ),
    pytest.param(
        dict(
            attacker="Salamence @ Salamencite\nAbility: Intimidate\nEVs: 2 HP / 32 SpA / 32 Spe\nModest Nature\n- Hyper Voice",
            attacker_calc_species="Salamence-Mega",
            attacker_calc_ability="Aerilate",
            move="Hyper Voice",
            p1choice="move 1 mega, move 1",
            defenders={
                "p2a": "Kingambit\nAbility: Defiant\nEVs: 32 HP / 32 SpD / 2 Def\nCareful Nature\n- Splash",
                "p2b": "Incineroar\nAbility: Blaze\nEVs: 32 HP / 32 SpD / 2 Def\nCareful Nature\n- Splash",
            },
            field={},
        ),
        id="mega-aerilate-spread",
    ),
    pytest.param(
        dict(
            attacker="Garchomp\nAbility: Rough Skin\nEVs: 2 HP / 32 Atk / 32 Spe\nJolly Nature\n- Earthquake",
            move="Earthquake",
            p1choice="move 1, move 1",
            defenders={
                "p2a": "Kingambit\nAbility: Defiant\nEVs: 32 HP / 32 Def / 2 SpD\nImpish Nature\n- Splash",
                "p2b": "Incineroar\nAbility: Blaze\nEVs: 32 HP / 32 Def / 2 SpD\nImpish Nature\n- Splash",
            },
            field={},
        ),
        id="spread-earthquake-super-effective",
    ),
]


def _side(first: str, second: str | None, fill: list[str]) -> str:
    mons = [first] + ([second] if second else []) + fill
    return "\n\n".join(mons[:4])


def _sample(sc: dict, seeds: int = 300) -> dict:
    defenders = list(sc["defenders"].items())
    p2_first = defenders[0][1]
    p2_second = defenders[1][1] if len(defenders) > 1 else None
    p1 = _side(sc["attacker"], FILLER["incineroar"], [FILLER["garchomp"], FILLER["kingambit"]])
    p2 = _side(p2_first, p2_second, [FILLER["rillaboom"], FILLER["garchomp"], FILLER["kingambit"]])
    req = dict(
        format="gen9championsvgc2026regmc", p1=p1, p2=p2, p1choice=sc["p1choice"],
        p2choice="move 1, move 1", seeds=seeds, targets=[t for t, _ in defenders],
    )
    res = subprocess.run(
        ["node", str(paths.SIDECAR / "showdown" / "sim-damage.js"), str(paths.SHOWDOWN)],
        input=json.dumps(req), capture_output=True, text=True, check=True,
    )
    return json.loads(res.stdout)


@pytest.mark.parametrize("sc", SCENARIOS)
def test_calc_matches_simulator(sc, calc):
    observed = _sample(sc)
    attacker = parse_set(sc["attacker"])
    if "attacker_calc_species" in sc:
        attacker.species = sc["attacker_calc_species"]
        attacker.ability = sc["attacker_calc_ability"]
    for slot, text in sc["defenders"].items():
        result = calc.calc(attacker, parse_set(text), sc["move"], field=sc["field"])
        rolls, seen = result.damage, observed[slot]
        # The sampler drops KOing hits (damage clamped to remaining HP), so only non-KO
        # rolls can be observed; any KO roll must show up as KOs instead.
        survivable = [r for r in rolls if r < result.defender_hp]
        assert len(seen) > 100, f"too few clean hits on {slot}: {observed}"
        assert set(seen) <= set(rolls), f"{slot}: sim damage {sorted(set(seen) - set(rolls))} not in calc rolls {rolls}"
        assert (min(seen), max(seen)) == (min(survivable), max(survivable)), f"{slot}: sim {min(seen)}-{max(seen)} vs calc {rolls}"
        if len(survivable) < len(rolls):
            assert observed.get("kos", 0) > 0
