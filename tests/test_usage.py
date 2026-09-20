"""Phase 7's usage report. Counting is the whole product, so the tests are reconciliation.

Every number has to resolve to a count of sheets, and the two denominators — per game and per
player — have to stay distinct, because the report claims they differ and says so on its own face.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vgc.meta import usage
from vgc.regulation import to_id

FIXTURES = Path(__file__).parent / "fixtures"
REPLAYS = sorted((FIXTURES / "replays").glob("*.json"))


@pytest.fixture(scope="module")
def corpus():
    return [json.loads(p.read_text()) for p in REPLAYS]


@pytest.fixture(scope="module")
def report(reg, corpus):
    return usage.build(reg, corpus)


def test_sheets_are_attributed_per_side_and_per_player(reg, corpus):
    sheets = list(usage.sheets(reg, corpus))
    # Three replays, two sheets each; two of them are the same Bo3 pairing, so 6 sheets, 4 players.
    assert len(sheets) == 6
    assert len({s.player for s in sheets}) == 4
    assert all(s.named for s in sheets)


def test_a_replay_is_counted_once_even_if_the_source_repeats_it(reg, corpus):
    assert len(list(usage.sheets(reg, corpus + corpus))) == len(list(usage.sheets(reg, corpus)))


def test_species_counts_reconcile_to_sheets(report):
    # Six Pokémon per team, every one of them counted exactly once.
    assert sum(s["sheets"] for s in report["species"].values()) == 6 * report["sheets"]
    assert report["players"] == 4 and report["replays"] == len(REPLAYS)


def test_each_distribution_reconciles_to_its_species(report):
    for s in report["species"].values():
        for key in ("items", "abilities", "natures"):
            assert sum(r["sheets"] for r in s[key]) == s["sheets"], (s["species"], key)
            assert all(0 < r["share"] <= 1 for r in s[key])
        # A move is counted once per sheet, and a sheet carries at most four.
        assert sum(r["sheets"] for r in s["moves"]) <= 4 * s["sheets"]
        assert all(r["sheets"] <= s["sheets"] for r in s["moves"])


def test_partners_are_symmetric(reg, corpus):
    # Untruncated: `top` cuts each list by count, so the tail is asymmetric by construction.
    full = usage.build(reg, corpus, top=100)
    for sid, s in full["species"].items():
        for row in s["partners"]:
            back = full["species"][to_id(row["species"])]["partners"]
            mine = next(r for r in back if to_id(r["species"]) == sid)
            assert mine["sheets"] == row["sheets"]


def test_lift_is_relative_to_the_partners_own_rate(report):
    total = report["sheets"]
    for s in report["species"].values():
        for row in s["partners"]:
            base = report["species"][to_id(row["species"])]["sheets"] / total
            assert row["lift"] == pytest.approx((row["sheets"] / s["sheets"]) / base)


def test_the_two_denominators_stay_distinct(reg, corpus):
    """The per-player share must not be a restatement of the per-game share.

    One of the fixture pairings played two games with the same teams. Those Pokémon are 2 sheets
    but 1 player, and if the report ever counted players by summing sheets the two columns would
    collapse — which is the thing the report exists to show does not happen in the real corpus.
    """
    report = usage.build(reg, corpus)
    repeated = [s for s in report["species"].values() if s["sheets"] > s["players"]]
    assert repeated, "expected at least one species whose owner played it twice"


def test_nature_and_ability_variation_within_one_team_key_is_kept(reg, corpus):
    """The reason this counts replays and not `vgc.meta.pool`.

    `pool.team_key` is species + item + moves: it ignores ability and nature, so two sheets that
    differ only in nature collapse to one entry there. Here they must both be counted.
    """
    from vgc.meta import replays

    base = corpus[0]
    twin = json.loads(json.dumps(base))
    twin["id"] = base["id"] + "-twin"
    lines = twin["log"].split("\n")
    for i, line in enumerate(lines):
        if line.startswith("|showteam|"):
            head, side, packed = line.split("|", 3)[1:]
            mons = packed.split("]")
            f = mons[0].split("|")
            f[5] = "Bashful" if f[5] != "Bashful" else "Docile"
            mons[0] = "|".join(f)
            lines[i] = f"|{head}|{side}|" + "]".join(mons)
            changed = f[1] or f[0]
            break
    else:
        pytest.skip("fixture carries no team sheet")
    twin["log"] = "\n".join(lines)

    assert replays.team_key(list(usage.sheets(reg, [base]))[0].team) == \
        replays.team_key(list(usage.sheets(reg, [twin]))[0].team), "the twin must share a team key"

    both = usage.build(reg, [base, twin])["species"][to_id(changed)]
    assert len(both["natures"]) >= 2
    assert sum(r["sheets"] for r in both["natures"]) == both["sheets"]


def test_min_rating_selects_a_different_corpus(reg, corpus):
    rated = json.loads(json.dumps(corpus[0]))
    rated["rating"] = 1500
    report = usage.build(reg, [rated] + corpus[1:], min_rating=1400)
    assert report["sheets"] == 2 and report["rated_sheets"] == 2
    assert report["min_rating"] == 1400


def test_no_spread_is_reported_anywhere(report):
    """Sheets do not carry Stat Points; the pool's are `impute_sp`'s guess. Publishing them as
    usage would present our own function's output as a measurement of what people play."""
    assert "not reported" in report["spreads"]
    blob = json.dumps(report).lower()
    for token in ('"sp"', '"spreads":', "stat points"):
        assert blob.count(token) <= 1, token
    for s in report["species"].values():
        assert not {"sp", "spread", "spreads", "stats"} & set(s)


def test_top_species_orders_by_the_denominator_it_is_asked_for(report):
    by_game = usage.top_species(report, 5)
    assert [s["share"] for s in by_game] == sorted((s["share"] for s in by_game), reverse=True)
    by_player = usage.top_species(report, 5, by="player_share")
    assert [s["player_share"] for s in by_player] == sorted((s["player_share"] for s in by_player), reverse=True)


def test_round_trips_through_disk(reg, report, tmp_path):
    path = usage.save(reg, report, tmp_path / "usage_2026-01-01.json")
    assert usage.load(reg, path) == report
