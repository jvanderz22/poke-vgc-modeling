from __future__ import annotations

import copy
import json

import pytest

from vgc import paths
from vgc.regulation import available_regulations, load_regulation


def test_reg_mc_config(reg):
    assert reg.showdown_format == "gen9championsvgc2026regmc"
    assert (reg.team_size, reg.bring, reg.level) == (6, 4, 50)
    assert (reg.sp_budget, reg.sp_per_stat_cap, reg.fixed_iv) == (66, 32, 31)
    assert reg.mega and not reg.tera


def test_every_regulation_loads_with_snapshot():
    """The L0 spine: a second regulation is config + snapshot only."""
    assert {"reg_mc", "reg_mb"} <= set(available_regulations())
    for reg_id in available_regulations():
        reg = load_regulation(reg_id)
        assert reg.dex.meta["showdown_format"] == reg.showdown_format
        assert reg.dex.meta["showdown_sha"] == reg.showdown_sha


def test_mc_adds_to_mb():
    mc, mb = load_regulation("reg_mc").dex, load_regulation("reg_mb").dex
    assert "rillaboom" in mc.species and "rillaboom" not in mb.species
    assert "salamencemega" in mc.species


def test_snapshot_sha_mismatch_is_refused(reg, tmp_path, monkeypatch):
    data = json.loads(reg.dex_path.read_text())
    data["meta"]["showdown_sha"] = "0" * 40
    fake = tmp_path / reg.id / "dex.json"
    fake.parent.mkdir()
    fake.write_text(json.dumps(data))
    monkeypatch.setattr(paths, "REGULATION_DATA", tmp_path)
    stale = load_regulation(reg.id)
    with pytest.raises(ValueError, match="Re-run"):
        stale.dex


def test_unknown_regulation():
    with pytest.raises(FileNotFoundError):
        load_regulation("reg_zz")


def test_mega_forme_lookup(reg):
    assert reg.dex.mega_forme("salamence", "salamencite")["name"] == "Salamence-Mega"
    assert reg.dex.mega_forme("garchomp", "garchompitez")["name"] == "Garchomp-Mega-Z"
    assert reg.dex.mega_forme("incineroar", "salamencite") is None


# --- Cross-check against vbbjandrade/pokemon-champions-data ---------------------------

def _compile_dataset(reg_dir: str) -> dict:
    """Apply the dataset's chained deltas (README semantics) up to `reg_dir`."""
    root = paths.CHAMPIONS_DATA / "data"
    master = {k: json.loads((root / "master" / f"{k}.json").read_text()) for k in ("roster", "items")}
    master["learnsets"] = {}
    deltas = {}
    for d in root.glob("regm-*/delta.json"):
        delta = json.loads(d.read_text())
        deltas[delta["regulationId"]] = (d.parent.name, delta)
    by_dir = {name: rid for rid, (name, _) in deltas.items()}
    chain, rid = [], by_dir[reg_dir]
    while rid:
        chain.append(deltas[rid][1])
        rid = deltas[rid][1].get("baseRegulationId")
    cur = copy.deepcopy(master)
    for delta in reversed(chain):
        for coll, overrides in delta["overrides"].items():
            if coll not in cur:
                continue
            for key, val in overrides.items():
                if val is None:
                    cur[coll].pop(key, None)
                else:
                    cur[coll][key] = {**cur[coll].get(key, master[coll].get(key, {})), **val}
    return cur


@pytest.mark.skipif(not (paths.CHAMPIONS_DATA / "data").exists(), reason="champions-data submodule not checked out")
def test_dataset_agrees_on_learnsets_and_stats(reg):
    ds = _compile_dataset(reg.dataset_dir)
    sd = reg.dex.species
    shared = [s for s in ds["learnsets"] if s in sd and sd[s]["moves"]]
    assert len(shared) > 250
    for s in shared:
        assert set(ds["learnsets"][s]["moves"]) == set(sd[s]["moves"]), s
    for s, entry in ds["roster"].items():
        if s in sd:
            assert entry["baseStats"] == sd[s]["baseStats"], s
    # Every dataset species is legal in Showdown (Showdown also lists cosmetic formes).
    assert set(ds["roster"]) <= set(sd)


@pytest.mark.skipif(not (paths.CHAMPIONS_DATA / "data").exists(), reason="champions-data submodule not checked out")
def test_dataset_item_drift_is_known(reg):
    """The dataset under-reports legal items at this pin (Phase 0/1 finding). If this
    starts failing, upstream fixed it — re-check and consider trusting it again."""
    ds_items = set(_compile_dataset(reg.dataset_dir)["items"])
    assert ds_items <= set(reg.dex.items)
    missing = set(reg.dex.items) - ds_items
    assert {"lifeorb", "rockyhelmet", "expertbelt"} <= missing


@pytest.mark.skipif(not (paths.CHAMPIONS_DATA / "data").exists(), reason="champions-data submodule not checked out")
def test_type_chart_matches_dataset(reg):
    eff = json.loads((paths.CHAMPIONS_DATA / "data" / "mechanics" / "effectiveness.json").read_text())["chart"]
    for atk, row in reg.dex.type_chart.items():
        for dfn, mult in row.items():
            assert eff[atk][dfn] == mult, (atk, dfn)


def test_the_stat_override_tables_still_match_the_pinned_build():
    """`overrideOffensiveStat` and `overrideDefensiveStat` are not in the dex export — the same
    omission that hid `multihit` from the damage channel — so both tables are hand-carried and
    re-derived here. A Showdown bump that adds a move to either fails this instead of quietly
    measuring the wrong stat."""
    import re

    from vgc import paths
    from vgc.regulation import DEFENSIVE_STAT, OFFENSIVE_STAT, load_regulation

    src = (paths.SHOWDOWN / "data" / "moves.ts").read_text()
    reg = load_regulation("reg_mc")
    for field, table in (("overrideOffensiveStat", OFFENSIVE_STAT),
                         ("overrideDefensiveStat", DEFENSIVE_STAT)):
        found = {}
        for m in re.finditer(r"^\t(\w+): \{\n(.*?)^\t\},", src, re.S | re.M):
            hit = re.search(rf"{field}: '(\w+)'", m.group(2))
            if hit and m.group(1) in reg.dex.moves:
                found[m.group(1)] = hit.group(1)
        assert found == table, f"{field} drifted: pinned build says {found}"


def test_psyshock_is_a_special_move_that_checks_defence(reg):
    from vgc.regulation import defensive_stat, offensive_stat

    assert reg.dex.get_move("Psyshock")["category"] == "Special"
    assert offensive_stat(reg.dex, "Psyshock") == "spa"
    assert defensive_stat(reg.dex, "Psyshock") == "def"
    assert defensive_stat(reg.dex, "Body Press") == "def"     # physical, ordinary defensively
    assert defensive_stat(reg.dex, "Hyper Voice") == "spd"
    assert defensive_stat(reg.dex, "Protect") is None
