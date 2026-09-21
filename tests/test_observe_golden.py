"""A byte-for-byte lock on what the Observer produces, so its internals can be rebuilt safely.

`observation()` is embedded in snapshots and fingerprinted at VERSION 3, so **433,052 frozen
training rows depend on its exact bytes**. The evidence logs are younger but four belief channels
read them. Together that makes the Observer's output a published interface and its internals an
implementation detail — and the split between protocol parsing and battle state cannot be made
without something that proves the output did not move.

This is that proof. It walks each replay line by line, hashes `dumps(observation())` at every step
rather than only at the end — so a divergence that later heals is still caught — and hashes both
evidence logs alongside. Three replays are committed and always run. The local replay cache, when
present, runs hundreds more; those hashes are committed too, so a machine without the cache loses
coverage but never silently passes a comparison it did not make.

Regenerate deliberately, never to make a red test green:

    VGC_WRITE_GOLDEN=1 .venv/bin/python -m pytest tests/test_observe_golden.py -q
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

import pytest

from vgc import paths
from vgc.data.observe import Observer, dumps

GOLDEN = Path(__file__).parent / "fixtures" / "observe_golden.json"
FIXTURES = Path(__file__).parent / "fixtures" / "replays"
CACHE = paths.ROOT / "data" / "replays"
SAMPLE = 400          # cached replays to lock, taken in sorted order so the set is reproducible
PERSPECTIVES = ("spectator", "p1")


def _log_of(replay: dict) -> list[str]:
    log = replay["log"]
    return log if isinstance(log, list) else log.split("\n")


def fingerprint(replay: dict, dex, perspective: str) -> str:
    """One hash covering every intermediate observation and both evidence logs."""
    obs = Observer(perspective, dex)
    h = hashlib.sha256()
    for line in _log_of(replay):
        obs.feed(line)
        h.update(dumps(obs.observation()).encode())
        h.update(b"\x00")
    h.update(dumps([e.to_json() for e in obs.moves_log]).encode())
    h.update(dumps([e.to_json() for e in obs.damage_log]).encode())
    return h.hexdigest()


def committed_replays() -> list[tuple[str, dict]]:
    return [(p.stem, json.loads(p.read_text())) for p in sorted(FIXTURES.glob("*.json"))]


def cached_replays() -> list[tuple[str, dict]]:
    if not CACHE.exists():
        return []
    files = sorted(p for fmt in sorted(CACHE.iterdir()) if fmt.is_dir()
                   for p in sorted(fmt.glob("*.json.gz")))
    out = []
    for p in files[:SAMPLE]:
        try:
            out.append((p.name.removesuffix(".json.gz"), json.loads(gzip.decompress(p.read_bytes()))))
        except Exception:
            continue
    return out


def _all_replays() -> list[tuple[str, dict]]:
    return committed_replays() + cached_replays()


def _write(reg) -> None:
    entries = {}
    for name, rep in _all_replays():
        entries[name] = {p: fingerprint(rep, reg.dex, p) for p in PERSPECTIVES}
    GOLDEN.write_text(json.dumps(
        {"note": "sha256 over every intermediate observation plus both evidence logs; see "
                 "tests/test_observe_golden.py",
         "showdown_sha": reg.showdown_sha, "sample": SAMPLE,
         "replays": dict(sorted(entries.items()))}, indent=1) + "\n")


def test_the_observer_still_produces_exactly_what_it_did(reg):
    if os.environ.get("VGC_WRITE_GOLDEN"):
        _write(reg)
        pytest.skip(f"rewrote {GOLDEN.name}")
    if not GOLDEN.exists():
        pytest.skip(f"no {GOLDEN.name}; run with VGC_WRITE_GOLDEN=1")

    golden = json.loads(GOLDEN.read_text())["replays"]
    checked = missing = 0
    for name, rep in _all_replays():
        want = golden.get(name)
        if want is None:
            missing += 1
            continue
        for perspective, digest in want.items():
            assert fingerprint(rep, reg.dex, perspective) == digest, (
                f"{name} ({perspective}) no longer produces the same observations. This is a "
                f"published interface: snapshots are fingerprinted at VERSION 3 on it.")
            checked += 1
    assert checked, "the golden file matched no replay on disk"
    # Said out loud rather than passed quietly: a cache-less machine checks 6 comparisons, not 800.
    print(f"\n{checked} fingerprints checked, {missing} replays on disk not in the golden file")


def test_the_locked_replays_exercise_most_of_the_protocol(reg):
    """A lock is only worth what it covers, so the coverage is a number and not an assumption."""
    seen: Counter = Counter()
    for _, rep in _all_replays():
        for line in _log_of(rep):
            if line.startswith("|") and len(line) > 1:
                seen[line[1:].split("|")[0]] += 1
    handled = {n.removeprefix("_on_") for n in dir(Observer) if n.startswith("_on_")}
    covered = {k for k in seen if k.replace("-", "") in handled or k in handled}
    print(f"\n{len(covered)}/{len(handled)} handlers exercised; "
          f"uncovered: {sorted(handled - {k.replace('-', '') for k in seen} - {k for k in seen})}")
    assert len(covered) >= 20, "too little of the protocol is locked for a refactor to lean on this"


# --- the path the frozen training rows actually came through -----------------------------

TRACE_GOLDEN = Path(__file__).parent / "fixtures" / "observe_trace_golden.json"
TRACE_RUN = paths.ROOT / "data" / "selfplay" / "gen-heuristic-heuristic-spreads-s21-p4000x5"
TRACE_BATTLES = 12


def _traced_battles():
    path = TRACE_RUN / "battles.jsonl.gz"
    if not path.exists():
        return []
    out = []
    with gzip.open(path, "rt") as f:
        for i, line in enumerate(f):
            if i >= TRACE_BATTLES:
                break
            out.append(json.loads(line))
    return out


def trace_fingerprint(trace: dict, dex) -> dict[str, str]:
    """Per perspective, a hash over every observation *including the request payloads*.

    Replays carry no `|request|`, so the first golden test never touches `Observer.request` — and
    that is precisely the path `vgc.data.snapshots` uses to build the rows that are frozen at
    VERSION 3. Exact HP, exact stats, the own-side item and ability and which four were brought all
    arrive through it, so a lock that skips it leaves the fingerprinted interface half open. Found
    by perturbing `round(hp, 4)` to `round(hp, 3)` and watching the first test pass: spectator HP is
    always out of 100, so the two agree, and only a request makes HP exact enough to disagree.
    """
    from vgc.data.observe import PERSPECTIVES as ALL

    obs = {p: Observer(p, dex) for p in ALL}
    digests = {p: hashlib.sha256() for p in ALL}
    for step in trace["steps"]:
        for p in ALL:
            obs[p].feed_many(step[p])
        for sid, r in step["requests"].items():
            obs[sid].request(json.loads(r))
        for p in ALL:
            digests[p].update(dumps(obs[p].observation()).encode())
            digests[p].update(b"\x00")
    for p in ALL:
        digests[p].update(dumps([e.to_json() for e in obs[p].moves_log]).encode())
        digests[p].update(dumps([e.to_json() for e in obs[p].damage_log]).encode())
    return {p: d.hexdigest() for p, d in digests.items()}


@pytest.mark.showdown
def test_the_request_fed_observer_still_produces_exactly_what_it_did(reg):
    from vgc.engine.runner import BattleRunner

    battles = _traced_battles()
    if not battles:
        pytest.skip("no self-play run to trace")

    with BattleRunner() as runner:
        traces = {b["battle_id"]: runner.request(
            {"op": "trace", "id": b["battle_id"], "inputLog": b["input_log"],
             "ots": bool(b.get("ots", True))}) for b in battles}

    if os.environ.get("VGC_WRITE_GOLDEN"):
        TRACE_GOLDEN.write_text(json.dumps(
            {"note": "sha256 per perspective over every observation, with request payloads fed; "
                     "see tests/test_observe_golden.py",
             "showdown_sha": reg.showdown_sha, "run": TRACE_RUN.name,
             "battles": {bid: trace_fingerprint(t, reg.dex) for bid, t in sorted(traces.items())}},
            indent=1) + "\n")
        pytest.skip(f"rewrote {TRACE_GOLDEN.name}")
    if not TRACE_GOLDEN.exists():
        pytest.skip(f"no {TRACE_GOLDEN.name}; run with VGC_WRITE_GOLDEN=1")

    golden = json.loads(TRACE_GOLDEN.read_text())["battles"]
    checked = 0
    for bid, trace in traces.items():
        want = golden.get(bid)
        if want is None:
            continue
        got = trace_fingerprint(trace, reg.dex)
        for perspective, digest in want.items():
            assert got[perspective] == digest, (
                f"{bid} ({perspective}) no longer produces the same observations through the "
                f"request path — this is what snapshots are fingerprinted on at VERSION 3.")
            checked += 1
    assert checked, "the trace golden file matched no battle"
    print(f"\n{checked} request-fed fingerprints checked over {len(traces)} battles")
