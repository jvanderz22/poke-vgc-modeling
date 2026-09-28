"""Feature datasets for WP models: `data/features/<regulation>/<name>/`.

  train.npz, val.npz     from a checked training manifest, thinned (`features.train_orientations`);
                         val is 5% of its battles by hash (the frozen held-out sets are never
                         used for model selection)
  eval_<set>.npz         the frozen held-out shards, one file per evaluation set
  vocab.json, info.json  the vocabulary and feature layout the arrays were built with
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import time
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from vgc import paths
from vgc.data.splits import check_manifest, read_shard
from vgc.regulation import Regulation

FEATURES = paths.ROOT / "data" / "features"
VAL_RATE = 0.05

# Evaluation sets, named by the information regime they were played in, because gate rule 7 says
# a number does not travel out of one. The Bo3 shard is Open Team Sheets; the non-Bo3 ladder shard
# is Team Preview Only, which is the regime a cartridge actually runs in, and it is the only place
# a model can be asked whether it knows what an unknown opponent is.
EVAL_SETS = {
    "human_ots": ["human/{bo3}/heldout_human.jsonl.gz"],
    "human_ots_team": ["human/{bo3}/heldout_team.jsonl.gz"],
    "human_closed": ["human/{closed}/heldout_human.jsonl.gz"],
    "human_closed_team": ["human/{closed}/heldout_team.jsonl.gz"],
    # `{runs}` is the self-play runs the training manifest names — see `_eval_files`.
    "selfplay_battle": ["selfplay/{runs}/heldout_battle.jsonl.gz"],
    "selfplay_team": ["selfplay/{runs}/heldout_team.jsonl.gz"],
}

# Self-play runs that are not the meta under open sheets, marked by the tag `vgc data generate`
# already puts in the run id. The training manifest never takes them — `--spreads sampled` prints
# "must not be manifested" and a manifest names its files one by one — but the eval sets were a
# bare glob with no such guard, so the two belief-gating runs generated on 2026-09-20 walked into
# `eval_selfplay_*` and changed it under a name that had not changed. Two different reasons to
# exclude them, and the second is the one that matters:
#
#   -spreads  every spread redrawn from `vgc.belief.prior`, deliberately *not* the meta. Scoring
#             win probability on it measures a distribution built to stress the belief layer.
#   -closed   Team Preview Only. Gate rule 7: pooling it with open-sheet self-play under one
#             name is the exact mistake this phase exists to undo.
#
# A tag is a weak key, so the rule is stated where the tag is written (`cmd_data_generate`) and
# enforced here; the durable fix is an eval manifest that names its shards the way the training
# manifest does.
SELFPLAY_EVAL_EXCLUDE = ("-spreads", "-closed")


def _eval_files(snap: Path, patterns: list[str], bo3: str, closed: str, runs: list[str]) -> list[Path]:
    """The held-out shards for one eval set.

    Self-play held-out shards come from the runs the training manifest names, not from whatever
    `selfplay/*` holds. The glob pooled every run on disk — three of them at snapshot VERSION 2,
    older than any model's training rows — and it is the durable half of PLAN-v2 Phase 8 step 5:
    an eval set that names its shards the way the training manifest does. The tag exclusion stays
    as a second guard.
    """
    files = sorted({f for pat in patterns
                    for run in (runs if "{runs}" in pat else [""])
                    for f in snap.glob(pat.format(bo3=bo3, closed=closed, runs=run))})
    return [f for f in files
            if f.parent.parent.name != "selfplay"
            or not any(tag in f.parent.name for tag in SELFPLAY_EVAL_EXCLUDE)]

_W: dict[str, Any] = {}


def _init(reg_id: str) -> None:
    from vgc.regulation import load_regulation
    from vgc.wp.features import Featurizer

    _W["fz"] = Featurizer(load_regulation(reg_id))


def _featurize_chunk(job: tuple[list[dict], bool]) -> dict[str, np.ndarray]:
    from vgc.wp.features import featurize

    recs, thin = job
    return featurize(recs, _W["fz"], thin=thin)


def _chunks(files: list[Path], size: int = 2000) -> Iterator[list[dict]]:
    buf: list[dict] = []
    for f in files:
        for rec in read_shard(f):
            buf.append(rec)
            if len(buf) >= size:
                yield buf
                buf = []
    if buf:
        yield buf


def _concat(parts: list[dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
    """Join chunk outputs, re-indexing battles globally (by name)."""
    names: dict[str, int] = {}
    out: dict[str, list] = {k: [] for k in parts[0] if k != "battle_names"} if parts else {}
    for p in parts:
        remap = np.array([names.setdefault(n, len(names)) for n in p["battle_names"]], np.int32)
        for k in out:
            out[k].append(remap[p[k]] if k == "battle" else p[k])
    arrays = {k: np.concatenate(v) for k, v in out.items()}
    arrays["battle_names"] = np.array(list(names), dtype=str)
    return arrays


def featurize_files(files: list[Path], reg: Regulation, workers: int = 6, thin: bool = False) -> dict[str, np.ndarray]:
    jobs = ((chunk, thin) for chunk in _chunks(files))
    with mp.get_context("spawn").Pool(workers, initializer=_init, initargs=(reg.id,)) as pool:
        parts = [p for p in pool.imap(_featurize_chunk, jobs) if len(p["y"])]
    return _concat(parts)


def _is_val(name: str) -> bool:
    return int(hashlib.sha256(f"val:{name}".encode()).hexdigest()[:8], 16) / 16**8 < VAL_RATE


def _subset(d: dict[str, np.ndarray], rows: np.ndarray) -> dict[str, np.ndarray]:
    out = {k: v[rows] for k, v in d.items() if k != "battle_names"}
    out["battle_names"] = d["battle_names"]
    return out


def build(reg: Regulation, manifest_path: Path, name: str, workers: int = 6) -> dict[str, Any]:
    from vgc.wp.features import Featurizer

    manifest = json.loads(manifest_path.read_text())
    problems = check_manifest(manifest, reg)
    if problems:
        raise ValueError(f"manifest {manifest_path} fails its check: {problems[:3]}")
    out = FEATURES / reg.id / name
    out.mkdir(parents=True, exist_ok=True)
    fz = Featurizer(reg)
    (out / "vocab.json").write_text(json.dumps(fz.vocab.to_json()))
    t0 = time.perf_counter()
    train = featurize_files([paths.ROOT / f["path"] for f in manifest["files"]], reg, workers, thin=True)
    val_battle = np.array([_is_val(n) for n in train["battle_names"]])
    is_val = val_battle[train["battle"]]
    counts = {}
    for split, rows in (("train", ~is_val), ("val", is_val)):
        np.savez_compressed(out / f"{split}.npz", **_subset(train, np.nonzero(rows)[0]))
        counts[split] = int(rows.sum())
    snap = paths.ROOT / "data" / "snapshots" / reg.id
    bo3 = reg.showdown_format + "bo3"
    runs = sorted({Path(f["path"]).parent.name for f in manifest["files"]
                   if Path(f["path"]).parent.parent.name == "selfplay"})
    for set_name, patterns in EVAL_SETS.items():
        files = _eval_files(snap, patterns, bo3, reg.showdown_format, runs)
        if files:
            d = featurize_files(files, reg, workers)
            np.savez_compressed(out / f"eval_{set_name}.npz", **d)
            counts[f"eval_{set_name}"] = int(len(d["y"]))
    info = {
        "name": name, "regulation": reg.id, "manifest": str(manifest_path.resolve().relative_to(paths.ROOT)),
        "manifest_battles": len(manifest["battles"]), "featurizer_version": Featurizer.VERSION,
        "n_num": fz.n_num, "n_glob": fz.n_glob,
        "rows": counts, "val_rate": VAL_RATE, "seconds": round(time.perf_counter() - t0, 1),
    }
    (out / "info.json").write_text(json.dumps(info, indent=1) + "\n")
    return info | {"out": str(out)}


def load(reg: Regulation, name: str, split: str) -> dict[str, np.ndarray]:
    with np.load(FEATURES / reg.id / name / f"{split}.npz") as z:
        return {k: z[k] for k in z.files}


def merge(parts: list[dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
    """Concatenate feature sets, keeping battle indices distinct."""
    out: dict[str, list] = {k: [] for k in parts[0] if k != "battle_names"}
    names: list = []
    for p in parts:
        for k in out:
            out[k].append(p[k] + len(names) if k == "battle" else p[k])
        names.extend(p["battle_names"])
    merged = {k: np.concatenate(v) for k, v in out.items()}
    merged["battle_names"] = np.array(names, dtype=str)
    return merged
