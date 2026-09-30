"""Solver positions solved elsewhere (`vgc.wp.offload`, `scripts/cloud/solve_batch.py`): an export skips
what is cached, the runner answers each job once and resumes, and a merge caches only answers from
this exact solver that re-solve here the same."""

from __future__ import annotations

import json
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
    (cache / "summary.json").write_text(json.dumps(s | {"solver_sha256": "0" * 64}))
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
