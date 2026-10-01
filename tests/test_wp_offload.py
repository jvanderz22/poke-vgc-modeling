"""Solver positions solved elsewhere (`vgc.wp.offload`, `scripts/cloud/solve_batch.py`): an export skips
what is cached, the runner answers each job once and resumes, and a merge caches only answers from
this exact solver that re-solve here the same."""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

from vgc import paths
from vgc.wp import benchmark, offload, solver


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(solver, "CACHE", tmp_path / "cache.jsonl")
    monkeypatch.setattr(solver, "TIMEOUTS", tmp_path / "timeouts.jsonl")
    return tmp_path


@pytest.fixture(scope="module")
def positions(reg):
    """Three of the benchmark's locked positions, each solved in about a second."""
    jobs, _ = solver.plan(reg, benchmark.load(reg), solver.SEARCH, ["F1/D", "F2/A"])
    return [j[2] for j in jobs]


def _run(tmp_path, jobs, cap=120):
    subprocess.run([sys.executable, str(paths.ROOT / "scripts" / "cloud" / "solve_batch.py"),
                    "--jobs", str(jobs), "--out", str(tmp_path / "results.jsonl"),
                    "--solver", str(solver.SOLVER), "--showdown", str(paths.SHOWDOWN),
                    "--workers", "3", "--cap", str(cap), "--max-minutes", "10"], check=True,
                   capture_output=True)
    return [json.loads(x) for x in (tmp_path / "results.jsonl").read_text().splitlines()]


def test_export_run_merge_and_the_command_finds_them_cached(cache, positions):
    jobs = cache / "jobs.jsonl"
    counts = offload.export(positions + positions[:1], jobs)
    assert counts["written"] == len(positions) and counts["duplicate"] == 1
    rows = _run(cache, jobs)
    assert sorted(r["key"] for r in rows) == sorted(solver.position_key(p) for p in positions)
    assert all("result" in r for r in rows)
    # Nothing new to do: a rerun resumes and answers nothing twice.
    assert len(_run(cache, jobs)) == len(rows)

    out = offload.merge(cache / "results.jsonl", jobs, verify=1)
    assert out["merged"] == len(positions) and out["verified"][0]["same"]
    assert all(solver.run(p).get("cached") for p in positions)
    assert offload.export(positions, cache / "again.jsonl")["written"] == 0


def test_a_merge_refuses_another_solvers_answers(cache, positions):
    jobs = cache / "jobs.jsonl"
    offload.export(positions[:1], jobs)
    _run(cache, jobs)
    s = json.loads((cache / "summary.json").read_text())
    (cache / "summary.json").write_text(json.dumps(s | {"solver_sha256": "0" * 64, "solver_version": -1}))
    with pytest.raises(ValueError, match="solver"):
        offload.merge(cache / "results.jsonl", jobs)
    assert not solver.CACHE.exists()


def test_a_merge_refuses_an_answer_that_does_not_re_solve_the_same(cache, positions):
    jobs = cache / "jobs.jsonl"
    offload.export(positions[:1], jobs)
    rows = _run(cache, jobs)
    rows[0]["result"]["value"] = 1 - rows[0]["result"]["value"]
    (cache / "results.jsonl").write_text(json.dumps(rows[0]) + "\n")
    with pytest.raises(ValueError, match="differs"):
        offload.merge(cache / "results.jsonl", jobs)
    assert not solver.CACHE.exists()


def test_a_position_past_its_cap_is_a_timeout_and_is_exported_again(cache, reg):
    jobs, _ = solver.plan(reg, benchmark.load(reg), solver.SEARCH, ["F6"])
    path = cache / "jobs.jsonl"
    offload.export([jobs[0][2]], path)
    rows = _run(cache, path, cap=1)
    assert rows == [{"key": solver.position_key(jobs[0][2]), "timeout": 1.0}]
    out = offload.merge(cache / "results.jsonl", path)
    assert out["timeouts"] == 1 and out["merged"] == 0
    assert offload.export([jobs[0][2]], cache / "again.jsonl")["timed_out_before"] == 1


ORIGINAL = solver.SOLVER          # read before any test puts a copy in its place


def _versioned(tmp_path, name, version, extra=""):
    """A copy of the solver declaring `version`, with `extra` appended (a change of source only)."""
    src = re.sub(r"^const VERSION = \d+;\n", "", ORIGINAL.read_text(), flags=re.M)
    if version is not None:
        src = src.replace("'use strict';\n", f"'use strict';\nconst VERSION = {version};\n", 1)
    p = tmp_path / name
    p.write_text(src + extra)
    return p


def test_a_declared_version_keys_the_cache_so_an_edit_that_changes_nothing_keeps_it(tmp_path, monkeypatch, positions):
    pos = positions[0]
    keys = {}
    for name, version, extra in (("a.js", 7, ""), ("b.js", 7, "// a comment\n"), ("c.js", 8, ""),
                                 ("d.js", None, ""), ("e.js", None, "// a comment\n")):
        monkeypatch.setattr(solver, "SOLVER", _versioned(tmp_path, name, version, extra))
        keys[name] = solver.position_key(pos)
    assert keys["a.js"] == keys["b.js"]            # same version, different source: the same answers
    assert keys["a.js"] != keys["c.js"]            # a bump: new answers
    assert keys["d.js"] != keys["e.js"]            # no version declared: keyed on the source, as before
    assert solver.solver_version(tmp_path / "a.js") == 7 and solver.solver_version(tmp_path / "d.js") is None


def test_the_cache_check_catches_an_answer_the_solver_would_not_give(cache, monkeypatch, positions):
    monkeypatch.setattr(solver, "SOLVER", _versioned(cache, "v.js", 7))
    solver.run(positions[0])
    assert [r["same"] for r in solver.check_cache(5, cheapest=True)] == [True]
    rows = solver.cache_rows()
    rows[0]["result"]["value"] = 1 - rows[0]["result"]["value"]
    solver.CACHE.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert [r["same"] for r in solver.check_cache(5, cheapest=True)] == [False]


def test_a_merge_takes_the_same_version_from_another_source_and_refuses_another_version(cache, monkeypatch, positions):
    monkeypatch.setattr(solver, "SOLVER", _versioned(cache, "ran.js", 7))
    jobs = cache / "jobs.jsonl"
    offload.export(positions[:1], jobs)
    _run(cache, jobs)
    monkeypatch.setattr(solver, "SOLVER", _versioned(cache, "bumped.js", 8))
    with pytest.raises(ValueError, match="version"):
        offload.merge(cache / "results.jsonl", jobs, verify=0)
    monkeypatch.setattr(solver, "SOLVER", _versioned(cache, "here.js", 7, "// edited, same behaviour\n"))
    out = offload.merge(cache / "results.jsonl", jobs, verify=1)
    assert out["merged"] == 1 and out["verified"][0]["same"]
    assert solver.run(positions[0]).get("cached")


def test_the_real_cache_still_gives_its_cheapest_answers():
    """The guard against a forgotten VERSION bump, on this machine's cache: its three cheapest
    answers under the current key, solved again without it."""
    out = solver.check_cache(3, cheapest=True)
    if not out:
        pytest.skip("no cached answer under the current key keeps its position yet")
    assert all(r["same"] for r in out), out
