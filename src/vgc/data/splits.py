"""Frozen held-out sets and training manifests.

Every battle is assigned to exactly one split by a salted hash, so assignment never depends on
what data exists yet or on processing order:

  heldout_team    any battle involving a held-out team (team-generalization test)
  heldout_human   a human replay whose group (Bo3 series, or player pair) is held out
  heldout_battle  a self-play battle held out by id
  train           everything else

The salt and rates are frozen in `data/splits/<regulation>.json` (tracked in git) together
with the held-out teams and replay groups that existed at freeze time, so a changed salt or
rate is caught by `verify_frozen`. Training code must only read files listed in a manifest
that `check_manifest` accepts: the check recomputes every battle's split from its ids and
the frozen rules, and it does not trust any split name written in the data.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from vgc import paths
from vgc.regulation import Regulation

SPLITS = paths.ROOT / "data" / "splits"
SPLIT_NAMES = ("train", "heldout_battle", "heldout_team", "heldout_human")
DEFAULT_RATES = {"team": 0.15, "battle": 0.10, "human": 0.15}


def _unit(salt: str, kind: str, key: str) -> float:
    h = hashlib.sha256(f"{salt}:{kind}:{key}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


@dataclass(frozen=True)
class SplitRules:
    regulation: str
    salt: str
    rates: dict[str, float]

    def team_heldout(self, team_id: str | None) -> bool:
        return bool(team_id) and _unit(self.salt, "team", team_id) < self.rates["team"]

    def split_of(self, source: str, battle: str, teams: Iterable[str | None], group: str | None = None) -> str:
        if any(self.team_heldout(t) for t in teams):
            return "heldout_team"
        if source == "human":
            return "heldout_human" if _unit(self.salt, "human", group or battle) < self.rates["human"] else "train"
        return "heldout_battle" if _unit(self.salt, "battle", battle) < self.rates["battle"] else "train"

    def split_of_record(self, rec: dict[str, Any]) -> str:
        return self.split_of(rec["source"], rec["battle"], rec["meta"]["teams"].values(), rec["meta"].get("group"))


def split_path(reg: Regulation) -> Path:
    return SPLITS / f"{reg.id}.json"


def freeze(reg: Regulation, team_ids: list[str], human_groups: list[str], pool_file: str,
           rates: dict[str, float] | None = None) -> Path:
    """Write the frozen split file. Refuses to overwrite: a frozen split is never re-rolled."""
    path = split_path(reg)
    if path.exists():
        raise FileExistsError(f"{path} is frozen; delete it by hand only if you mean to invalidate every evaluation")
    today = dt.date.today().isoformat()
    rules = SplitRules(reg.id, f"{reg.id}:{today}", dict(rates or DEFAULT_RATES))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "regulation": reg.id,
        "frozen": today,
        "salt": rules.salt,
        "rates": rules.rates,
        "rules": __doc__.split("\n\n")[1].strip(),
        "pool": pool_file,
        "heldout_teams": sorted(t for t in set(team_ids) if rules.team_heldout(t)),
        "teams_at_freeze": len(set(team_ids)),
        "heldout_human_groups": sorted(g for g in set(human_groups) if _unit(rules.salt, "human", g) < rules.rates["human"]),
        "human_groups_at_freeze": len(set(human_groups)),
    }, indent=1) + "\n")
    return path


def load_rules(reg: Regulation) -> SplitRules:
    path = split_path(reg)
    if not path.exists():
        raise FileNotFoundError(f"no frozen split for {reg.id}; run `vgc data freeze`")
    d = json.loads(path.read_text())
    return SplitRules(d["regulation"], d["salt"], d["rates"])


def verify_frozen(reg: Regulation) -> list[str]:
    """The rules must still reproduce the lists recorded at freeze time."""
    d = json.loads(split_path(reg).read_text())
    rules = SplitRules(d["regulation"], d["salt"], d["rates"])
    problems = [f"team {t} recorded as held out but the rules disagree" for t in d["heldout_teams"] if not rules.team_heldout(t)]
    problems += [f"group {g} recorded as held out but the rules disagree" for g in d["heldout_human_groups"]
                 if _unit(rules.salt, "human", g) >= rules.rates["human"]]
    return problems


# --- shards ---------------------------------------------------------------------------

def write_shard(path: Path, lines: Iterable[str]) -> int:
    """Gzipped JSONL with a zeroed header timestamp, so equal content ⇒ equal bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as f:
        for line in lines:
            f.write(line.encode() + b"\n")
            n += 1
    return n


def read_shard(path: Path) -> Iterable[dict[str, Any]]:
    with gzip.open(path, "rt") as f:
        for line in f:
            yield json.loads(line)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- manifests ------------------------------------------------------------------------

def build_manifest(files: list[Path], reg: Regulation, purpose: str = "train") -> dict[str, Any]:
    """Everything a training run will read: files (with hashes) and every battle in them."""
    battles: dict[str, dict[str, Any]] = {}
    entries = []
    for path in sorted(files):
        n = 0
        for rec in read_shard(path):
            n += 1
            battles.setdefault(rec["battle"], {
                "battle": rec["battle"], "source": rec["source"],
                "teams": sorted(t for t in rec["meta"]["teams"].values() if t), "group": rec["meta"].get("group"),
            })
        entries.append({"path": str(path.resolve().relative_to(paths.ROOT)), "sha256": _sha256(path), "records": n})
    return {
        "regulation": reg.id, "purpose": purpose, "created": dt.datetime.now().isoformat(timespec="seconds"),
        "split_file_sha256": _sha256(split_path(reg)), "files": entries,
        "battles": sorted(battles.values(), key=lambda b: b["battle"]),
    }


def check_manifest(manifest: dict[str, Any], reg: Regulation) -> list[str]:
    """Problems that make a manifest unusable for training; empty means clean. The files'
    records are re-read and each battle's split is recomputed from its ids. The manifest's own
    battle list is not trusted."""
    rules = load_rules(reg)
    problems = verify_frozen(reg)
    if manifest["split_file_sha256"] != _sha256(split_path(reg)):
        problems.append("the frozen split file changed since this manifest was built")
    seen: set[str] = set()
    for f in manifest["files"]:
        p = paths.ROOT / f["path"]
        if not p.exists():
            problems.append(f"missing file {f['path']}")
            continue
        if _sha256(p) != f["sha256"]:
            problems.append(f"file changed since the manifest was built: {f['path']}")
        if manifest["purpose"] != "train":
            continue
        for rec in read_shard(p):
            if rec["battle"] in seen:
                continue
            seen.add(rec["battle"])
            split = rules.split_of_record(rec)
            if split != "train":
                problems.append(f"{rec['battle']} ({rec['source']}, {f['path']}) is {split}")
    return problems
