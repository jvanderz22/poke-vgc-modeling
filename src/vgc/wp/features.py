"""Snapshot → model inputs, shared by every perspective and every WP model.

A row is one snapshot seen from one **side A** ("me") against the other side ("them"), and its
target is whether A won. Player snapshots are oriented to the player. Spectator snapshots
become two rows, one for each orientation; at inference the two predictions are averaged
as `(f(A=p1) + 1 − f(A=p2)) / 2`, so spectator WP is symmetric by construction.

Set-encoder inputs:
  cat  int32  [12, 8]   species, current forme, item, ability, 4 moves (vocabulary ids)
  num  float32[12, F]   per-Pokémon numbers: state, HP, status, boosts, what is known, forme
                        stats and types, nature, exact stats (own side), volatiles, move summary
  glob float32[G]       turn, kind, perspective, context, field, side conditions, totals
Tokens 0–5 are A's team, 6–11 the opponent's, both in team-preview order. The model has no
positional encoding, so it is permutation-invariant within a side.

Unknowns are explicit: a vocabulary id of UNK and a "known" flag of 0 mean "not known to this
perspective". NONE means "known to hold nothing". Nothing about the future (the label, the
unrevealed Pokémon's identity as brought) enters an input.

`hand` features are team-level aggregates for the baselines (logistic, GBT).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from vgc.regulation import Regulation, to_id

PAD, UNK, NONE = 0, 1, 2
STATES = ("active", "bench", "fainted", "unrevealed", "not_brought")
STATUSES = ("brn", "par", "slp", "frz", "psn", "tox")
BOOSTS = ("atk", "def", "spa", "spd", "spe", "accuracy", "evasion")
STATS = ("hp", "atk", "def", "spa", "spd", "spe")
VOLATILES = ("substitute", "confusion", "taunt", "encore", "yawn", "leechseed", "disable", "perish")
KINDS = ("preview", "bring", "turn", "switch")
WEATHERS = ("sunnyday", "raindance", "sandstorm", "snowscape")
TERRAINS = ("electricterrain", "grassyterrain", "psychicterrain", "mistyterrain")
SIDE_CONDS = (("tailwind", 4), ("reflect", 5), ("lightscreen", 5), ("auroraveil", 5), ("safeguard", 5))
HAZARDS = ("stealthrock", "spikes", "toxicspikes", "stickyweb")
SPREAD = {"allAdjacent", "allAdjacentFoes"}
SPEED_CONTROL = {"tailwind", "trickroom", "icywind", "electroweb", "thunderwave", "scaryface"}

CAT_FIELDS = ("species", "forme", "item", "ability", "move1", "move2", "move3", "move4")


@dataclass
class Vocab:
    species: dict[str, int]
    items: dict[str, int]
    abilities: dict[str, int]
    moves: dict[str, int]
    types: list[str]
    regulation: str = ""

    @classmethod
    def from_regulation(cls, reg: Regulation) -> "Vocab":
        def ids(keys: Iterable[str]) -> dict[str, int]:
            return {k: i + 3 for i, k in enumerate(sorted(keys))}

        d = reg.dex
        types = sorted(t for t in d.type_chart if t != "Stellar")
        return cls(ids(d.species), ids(d.items), ids(d.abilities), ids(d.moves), types, reg.id)

    def to_json(self) -> dict:
        return self.__dict__

    @classmethod
    def load(cls, path: Path) -> "Vocab":
        return cls(**json.loads(path.read_text()))

    def sizes(self) -> dict[str, int]:
        return {"species": len(self.species) + 3, "items": len(self.items) + 3,
                "abilities": len(self.abilities) + 3, "moves": len(self.moves) + 3}


def _lookup(table: dict[str, int], value: str | None) -> int:
    if value is None:
        return UNK
    if value == "":
        return NONE
    return table.get(to_id(value), UNK)


class Featurizer:
    def __init__(self, reg: Regulation, vocab: Vocab | None = None):
        self.reg = reg
        self.dex = reg.dex
        self.vocab = vocab or Vocab.from_regulation(reg)
        self.type_idx = {t: i for i, t in enumerate(self.vocab.types)}
        self._forme_cache: dict[str, np.ndarray] = {}
        self._move_cache: dict[str, np.ndarray] = {}
        self.n_num = len(self._mon_num(_blank_mon(), True, False))
        self.n_glob = len(self._glob(_blank_obs(), "p1", "turn", False, False))

    # --- pieces -------------------------------------------------------------------------

    def _forme(self, name: str) -> np.ndarray:
        """Base stats /200 and type multi-hot of a species or forme."""
        if name not in self._forme_cache:
            v = np.zeros(6 + len(self.vocab.types), np.float32)
            s = self.dex.get_species(name)
            if s:
                v[:6] = [s["baseStats"][k] / 200 for k in STATS]
                for t in s["types"]:
                    if t in self.type_idx:
                        v[6 + self.type_idx[t]] = 1
            self._forme_cache[name] = v
        return self._forme_cache[name]

    def _move_summary(self, moves: list[str]) -> np.ndarray:
        """Damaging-move types, priority, Protect, spread, status count, max power, Fake Out,
        speed control. The result is independent of move order."""
        key = ",".join(sorted(moves))
        if key not in self._move_cache:
            nt = len(self.vocab.types)
            v = np.zeros(nt + 7, np.float32)
            for mid in moves:
                m = self.dex.moves.get(mid)
                if not m:
                    continue
                damaging = m["category"] != "Status"
                if damaging and m["type"] in self.type_idx:
                    v[self.type_idx[m["type"]]] = 1
                v[nt + 0] = max(v[nt + 0], float(damaging and m["priority"] > 0))
                v[nt + 1] = max(v[nt + 1], float(mid in ("protect", "detect", "spikyshield", "kingsshield", "banefulbunker", "silktrap", "burningbulwark")))
                v[nt + 2] = max(v[nt + 2], float(damaging and m["target"] in SPREAD))
                v[nt + 3] += (not damaging) / 4
                v[nt + 4] = max(v[nt + 4], (m["basePower"] or 0) / 150)
                v[nt + 5] = max(v[nt + 5], float(mid == "fakeout"))
                v[nt + 6] = max(v[nt + 6], float(mid in SPEED_CONTROL))
            self._move_cache[key] = v
        return self._move_cache[key]

    def _known_moves(self, m: dict) -> list[str]:
        if m["moves"]:
            return m["moves"][:4]
        return m["moves_used"][:4]

    def _can_mega(self, m: dict, side_mega_used: bool) -> bool:
        if side_mega_used or m["mega"] or not m["item"]:
            return False
        return self.dex.mega_forme(to_id(m["species"]), m["item"]) is not None

    def _mon_num(self, m: dict, is_me: bool, side_mega_used: bool) -> np.ndarray:
        parts: list[Any] = [float(is_me)]
        parts += [float(m["state"] == s) for s in STATES]
        parts += [float(m["position"] == 0), float(m["position"] == 1)]
        parts += [m["hp"], float(m["hp_exact"] is not None)]
        parts += [float(m["status"] == s) for s in STATUSES]
        parts += [m["boosts"].get(b, 0) / 6 for b in BOOSTS]
        parts += [float(m["mega"]), float(self._can_mega(m, side_mega_used))]
        parts += [float(m["item"] is not None), float(m["item"] == ""), float(m["lost_item"] is not None)]
        parts += [float(m["ability"] is not None), len(self._known_moves(m)) / 4]
        nature = self.dex.get_nature(m["nature"] or "") if m["nature"] else None
        head = np.array(parts, np.float32)
        nat = np.zeros(11, np.float32)
        if nature:
            for i, s in enumerate(STATS[1:]):
                nat[i] = float(nature.get("plus") == s)
                nat[5 + i] = float(nature.get("minus") == s)
            nat[10] = 1
        exact = np.zeros(7, np.float32)
        if m["stats"]:
            hp_max = m["hp_exact"][1] if m["hp_exact"] else 0
            exact[0] = hp_max / 250
            for i, s in enumerate(STATS[1:], start=1):
                exact[i] = m["stats"].get(s, 0) / 250
            exact[6] = 1
        vol = np.array([float(any(v.startswith(x) for v in m["volatiles"])) for x in VOLATILES], np.float32)
        return np.concatenate([head, self._forme(m["forme"]), nat, exact, vol, self._move_summary(self._known_moves(m))])

    def _mon_cat(self, m: dict) -> list[int]:
        v = self.vocab
        moves = self._known_moves(m)
        return [
            _lookup(v.species, m["species"]), _lookup(v.species, m["forme"]),
            _lookup(v.items, m["item"] if m["item"] is not None else m["lost_item"]),
            _lookup(v.abilities, m["ability"]),
        ] + [_lookup(v.moves, moves[i]) if i < len(moves) else UNK for i in range(4)]

    @staticmethod
    def _side_totals(side: dict) -> tuple[float, float, float, float]:
        mons = side["mons"]
        size = side["team_size"] or 4
        fainted = sum(m["state"] == "fainted" for m in mons)
        seen_alive = [m for m in mons if m["state"] in ("active", "bench")]
        remaining = max(size - fainted, 0)
        unseen = max(remaining - len(seen_alive), 0)
        hp = sum(m["hp"] for m in seen_alive) + unseen
        active = sum(m["state"] == "active" for m in mons)
        return remaining / 4, hp / 4, active / 2, unseen / 4

    def _glob(self, obs: dict, a: str, kind: str, ctx_human: bool, approx: bool) -> np.ndarray:
        b = "p2" if a == "p1" else "p1"
        f, turn = obs["field"], obs["turn"]
        g: list[float] = [turn / 10]
        g += [float(kind == k) for k in KINDS]
        g += [float(obs["perspective"] == a), float(ctx_human), float(approx),
              float(obs["sides"]["p1"]["sheet"] and obs["sides"]["p2"]["sheet"])]
        w = f["weather"] or ""
        g += [float(w == x) for x in WEATHERS] + [float(bool(w) and w not in WEATHERS)]
        g += [float(f["terrain"] == x) for x in TERRAINS]
        tr = f["pseudo"].get("trickroom")
        g += [max(0.0, (5 - (turn - tr)) / 5) if tr is not None else 0.0, float("gravity" in f["pseudo"])]
        for sid in (a, b):
            conds = obs["sides"][sid]["conditions"]
            for name, dur in SIDE_CONDS:
                g.append(max(0.0, (dur - (turn - conds[name])) / dur) if name in conds else 0.0)
            g.append(float(any(h in conds for h in HAZARDS)))
        for sid in (a, b):
            g.append(float(any(m["mega"] for m in obs["sides"][sid]["mons"])))
        for sid in (a, b):
            g += list(self._side_totals(obs["sides"][sid]))
        return np.array(g, np.float32)

    # --- rows ---------------------------------------------------------------------------

    def row(self, rec: dict, a: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        obs = rec["obs"]
        b = "p2" if a == "p1" else "p1"
        cat = np.zeros((12, len(CAT_FIELDS)), np.int32)
        num = np.zeros((12, self.n_num), np.float32)
        for k, sid in enumerate((a, b)):
            side = obs["sides"][sid]
            mega_used = any(m["mega"] for m in side["mons"])
            for i, m in enumerate(side["mons"][:6]):
                cat[6 * k + i] = self._mon_cat(m)
                num[6 * k + i] = self._mon_num(m, k == 0, mega_used)
        glob = self._glob(obs, a, rec["kind"], rec["meta"]["policies"]["p1"] == "human", rec["meta"].get("approx", False))
        return cat, num, glob

    def orientations(self, rec: dict) -> list[str]:
        p = rec["obs"]["perspective"]
        return ["p1", "p2"] if p == "spectator" else [p]


def hand_features(glob: np.ndarray, num: np.ndarray) -> np.ndarray:
    """Team-level aggregates for the baselines: the global vector plus, per side, the sum of
    alive base stats, alive base Speed, statused count and net boosts."""
    out = [glob]
    for k in (0, 1):
        side = num[6 * k : 6 * k + 6]
        alive = side[:, 1] + side[:, 2]  # active or bench
        base = side[:, 30:36]  # forme base stats (see _mon_num layout)
        out.append(np.array([
            (alive[:, None] * base).sum() / 4,
            (side[:, 1] * base[:, 5]).sum() / 2,
            (alive * side[:, 10:16].sum(1)).sum() / 4,
            (alive[:, None] * side[:, 16:23]).sum() / 4,
        ], np.float32))
    return np.concatenate(out)


# Indices into `glob` used by the "remaining Pokémon / HP" logistic baseline.
def logistic_columns(featurizer: Featurizer) -> list[int]:
    g = featurizer.n_glob
    # the last 8 entries are (remaining, hp, active, unseen) for A then B
    return [g - 8, g - 7, g - 4, g - 3]


def _blank_mon() -> dict:
    return {"species": "", "forme": "", "state": "unrevealed", "position": None, "hp": 1.0, "hp_exact": None,
            "status": None, "boosts": {}, "volatiles": [], "item": None, "item_source": None, "lost_item": None,
            "ability": None, "ability_source": None, "moves": [], "moves_used": [], "nature": None, "stats": None,
            "mega": False}


def _blank_obs() -> dict:
    side = {"name_known": True, "team_size": 4, "conditions": {}, "sheet": False, "brought_known": False, "mons": []}
    return {"perspective": "spectator", "turn": 0,
            "field": {"weather": None, "weather_since": None, "terrain": None, "terrain_since": None, "pseudo": {}},
            "sides": {"p1": dict(side), "p2": dict(side)}}


# --- datasets -------------------------------------------------------------------------------

@dataclass
class Batch:
    cat: list = field(default_factory=list)
    num: list = field(default_factory=list)
    glob: list = field(default_factory=list)
    y: list = field(default_factory=list)
    bring: list = field(default_factory=list)  # per token: 1 brought, 0 not, -1 unknown/masked
    battle: list = field(default_factory=list)
    point: list = field(default_factory=list)
    kind: list = field(default_factory=list)
    perspective: list = field(default_factory=list)  # 0 spectator, 1 player (exact), 2 player (approx)
    orient: list = field(default_factory=list)  # 0: A=p1, 1: A=p2
    source: list = field(default_factory=list)  # 0 selfplay, 1 human
    forfeit: list = field(default_factory=list)
    turn: list = field(default_factory=list)


def _bring_targets(rec: dict, a: str) -> np.ndarray:
    """Which of each side's 6 were brought — a target only where the perspective doesn't
    already know it and the truth is known."""
    t = np.full(12, -1.0, np.float32)
    b = "p2" if a == "p1" else "p1"
    for k, sid in enumerate((a, b)):
        side = rec["obs"]["sides"][sid]
        if side["brought_known"] or not rec["label"]["brought_complete"][sid]:
            continue
        brought = set(rec["label"]["brought"][sid])
        for i, m in enumerate(side["mons"][:6]):
            t[6 * k + i] = float(m["species"] in brought)
    return t


def _unit(*parts: Any) -> float:
    import hashlib

    return int(hashlib.sha1(":".join(map(str, parts)).encode()).hexdigest()[:8], 16) / 16**8


def train_orientations(rec: dict, fz: Featurizer, turn_rate: float = 0.3) -> list[str]:
    """Training-set thinning for self-play: snapshots within a battle share one outcome and are
    highly correlated, so keep every preview/bring snapshot, a deterministic `turn_rate` share
    of the rest, and one orientation per spectator snapshot. Human games are kept whole."""
    if rec["source"] == "human":
        return fz.orientations(rec)
    key = (rec["battle"], rec["point"], rec["kind"], rec["obs"]["perspective"])
    if rec["kind"] not in ("preview", "bring") and _unit("keep", *key) >= turn_rate:
        return []
    o = fz.orientations(rec)
    return [o[int(_unit("orient", *key) * len(o))]]


def featurize(records: Iterable[dict], fz: Featurizer, thin: bool = False) -> dict[str, np.ndarray]:
    b = Batch()
    battle_ids: dict[str, int] = {}
    for rec in records:
        winner = rec["label"]["winner"]
        if winner not in ("p1", "p2"):
            continue
        persp = 0 if rec["obs"]["perspective"] == "spectator" else (2 if rec["meta"].get("approx") else 1)
        sides = train_orientations(rec, fz) if thin else fz.orientations(rec)
        if not sides:
            continue
        bid = battle_ids.setdefault(rec["battle"], len(battle_ids))
        for a in sides:
            cat, num, glob = fz.row(rec, a)
            b.cat.append(cat); b.num.append(num); b.glob.append(glob)  # noqa: E702
            b.y.append(float(winner == a))
            b.bring.append(_bring_targets(rec, a))
            b.battle.append(bid); b.point.append(rec["point"]); b.kind.append(KINDS.index(rec["kind"]))  # noqa: E702
            b.perspective.append(persp); b.orient.append(int(a == "p2"))  # noqa: E702
            b.source.append(int(rec["source"] == "human"))
            b.forfeit.append(int(rec["label"]["ended_by"] == "forfeit"))
            b.turn.append(rec["obs"]["turn"])
    out = {
        "cat": np.array(b.cat, np.int32).reshape(-1, 12, len(CAT_FIELDS)),
        "num": np.array(b.num, np.float16).reshape(-1, 12, fz.n_num),  # float16 on disk; values are in [-1, 3]
        "glob": np.array(b.glob, np.float32).reshape(-1, fz.n_glob),
        "y": np.array(b.y, np.float32), "bring": np.array(b.bring, np.float32).reshape(-1, 12),
    }
    for k in ("battle", "point", "kind", "perspective", "orient", "source", "forfeit", "turn"):
        out[k] = np.array(getattr(b, k), np.int32)
    out["battle_names"] = np.array(list(battle_ids), dtype=object)
    return out
