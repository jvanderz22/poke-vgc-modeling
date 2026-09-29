"""Feature datasets for WP models: `data/features/<regulation>/<name>/`.

  train.npz, val.npz     from a checked training manifest, thinned (`features.train_orientations`);
                         val is drawn by hash per group, at `VAL_RATE` for each source (the
                         frozen held-out sets are never used for model selection)
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
# Validation is drawn per source. Human validation is what early stopping and every calibrator
# are fitted on, and at 5% of groups it was 287 open-sheet battles in 137 series: a temperature
# fitted there swung 0.87–1.55 between draws and could not pass a test judged on 4,127 held-out
# battles (docs/phase8-findings.md, "wp-v1e"). 20% is ~1,150 open-sheet and ~1,300 closed-sheet
# battles. Self-play is 75% of the battles and a model is never selected on it, so it stays at 5%.
# The draw is a threshold on one hash, so the 5% groups are inside the 20%.
VAL_RATE = {"human": 0.20, "selfplay": 0.05}

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


def _is_val(name: str, rate: float) -> bool:
    return int(hashlib.sha256(f"val:{name}".encode()).hexdigest()[:8], 16) / 16**8 < rate


def val_battles(battle_names: np.ndarray, manifest: dict[str, Any]) -> np.ndarray:
    """Which battles are validation, decided per *group* — a Bo3 series or a player pair — the
    way `heldout_human` is. Drawn per battle, a validation game had its sibling games, with the
    same teams and players, in training: the model half-remembers who won the matchup and is
    confidently wrong when the series split, so validation asked `wp-v1e` for a temperature of
    1.18 where held-out groups asked for 1.04, and early stopping read the same leak. Self-play
    has no groups, and a battle is its own group, as in `heldout_battle`."""
    group = {b["battle"]: b.get("group") or b["battle"] for b in manifest["battles"]}
    rate = {b["battle"]: VAL_RATE[b["source"]] for b in manifest["battles"]}
    return np.array([_is_val(group.get(n, n), rate.get(n, VAL_RATE["selfplay"])) for n in battle_names])


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
    val_battle = val_battles(train["battle_names"], manifest)
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


def resplit(reg: Regulation, source: str, name: str) -> dict[str, Any]:
    """A new dataset with the same rows as `source` and validation redrawn at today's `VAL_RATE`.

    Featurizing takes a quarter of an hour and the split does not depend on it: train and val are
    one featurized pool, indexed against the same battle names, so the pool is rejoined and cut
    again. The held-out files and the vocabulary are hard-linked, since they are byte-identical
    and a dataset directory must be complete on its own.
    """
    import os

    src, out = FEATURES / reg.id / source, FEATURES / reg.id / name
    info = json.loads((src / "info.json").read_text())
    manifest = json.loads((paths.ROOT / info["manifest"]).read_text())
    tr, va = load(reg, source, "train"), load(reg, source, "val")
    if not np.array_equal(tr["battle_names"], va["battle_names"]):
        raise ValueError(f"{source}: train and val are not indexed against the same battles")
    pool = {k: np.concatenate([tr[k], va[k]]) for k in tr if k != "battle_names"}
    pool["battle_names"] = tr["battle_names"]
    is_val = val_battles(pool["battle_names"], manifest)[pool["battle"]]
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for split, rows in (("train", ~is_val), ("val", is_val)):
        np.savez_compressed(out / f"{split}.npz", **_subset(pool, np.nonzero(rows)[0]))
        counts[split] = int(rows.sum())
    for f in [*src.glob("eval_*.npz"), src / "vocab.json"]:
        (out / f.name).unlink(missing_ok=True)
        os.link(f, out / f.name)
    val_src = pool["source"][is_val]
    info = info | {"name": name, "rows": info["rows"] | counts, "val_rate": VAL_RATE, "val_split": "group",
                   "resplit_from": source,
                   "val_battles": {s: int(len(np.unique(pool["battle"][is_val][val_src == c])))
                                   for s, c in (("selfplay", 0), ("human", 1))}}
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
