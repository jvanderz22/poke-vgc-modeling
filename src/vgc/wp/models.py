"""WP model versions: training the baselines, loading any version, and the registry.

  models/wp/<regulation>/<version>/card.json   model card (always)
  models/wp/<regulation>/<version>/...         model.onnx | model.pkl | coef.json, vocab.json
  models/registry.json                         every version, for comparison

Kinds:
  logistic  remaining Pokémon + HP (the plan's simple baseline)
  gbt       gradient-boosted trees on team-level hand features (v0)
  set       permutation-invariant set encoder with a bring head (v1), trained by `set_torch`

Every model maps feature rows → P(side A wins). `predict_records` handles orientation and
symmetrizes spectator snapshots.
"""

from __future__ import annotations

import datetime as dt
import json
import pickle
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from vgc import paths
from vgc.wp.features import Featurizer, Vocab, featurize, hand_features, logistic_columns

MODELS = paths.ROOT / "models"
REGISTRY = MODELS / "registry.json"


def model_dir(reg_id: str, version: str) -> Path:
    return MODELS / "wp" / reg_id / version


def _hand(d: dict[str, np.ndarray]) -> np.ndarray:
    return np.stack([hand_features(g, n) for g, n in zip(d["glob"], d["num"])]) if len(d["y"]) else np.zeros((0, 50))


class WPModel:
    kind = "base"
    has_bring = False

    def predict(self, d: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray | None]:
        """(P(A wins) [N], P(brought) [N, 12] or None) for feature rows."""
        raise NotImplementedError


class ConstantModel(WPModel):
    kind = "constant"

    def predict(self, d):
        return np.full(len(d["y"]), 0.5), None


class LogisticModel(WPModel):
    kind = "logistic"

    def __init__(self, coef: np.ndarray, intercept: float, cols: list[int]):
        self.coef, self.intercept, self.cols = coef, intercept, cols

    @classmethod
    def fit(cls, d: dict[str, np.ndarray], cols: list[int]) -> "LogisticModel":
        from sklearn.linear_model import LogisticRegression

        m = LogisticRegression(C=1.0, max_iter=1000).fit(d["glob"][:, cols], d["y"])
        return cls(m.coef_[0], float(m.intercept_[0]), cols)

    def predict(self, d):
        z = d["glob"][:, self.cols] @ self.coef + self.intercept
        return 1 / (1 + np.exp(-z)), None

    def save(self, out: Path) -> None:
        (out / "coef.json").write_text(json.dumps({"coef": self.coef.tolist(), "intercept": self.intercept, "cols": self.cols}))

    @classmethod
    def load(cls, out: Path) -> "LogisticModel":
        c = json.loads((out / "coef.json").read_text())
        return cls(np.array(c["coef"]), c["intercept"], c["cols"])


class GBTModel(WPModel):
    kind = "gbt"

    def __init__(self, model: Any):
        self.model = model

    @classmethod
    def fit(cls, d: dict[str, np.ndarray], val: dict[str, np.ndarray]) -> "GBTModel":
        from sklearn.ensemble import HistGradientBoostingClassifier

        X, Xv = _hand(d), _hand(val)
        best = None
        for lr, leaves in ((0.05, 31), (0.05, 15), (0.1, 31)):
            m = HistGradientBoostingClassifier(learning_rate=lr, max_leaf_nodes=leaves, max_iter=600, l2_regularization=1.0,
                                               early_stopping=True, validation_fraction=0.1, random_state=0).fit(X, d["y"])
            p = np.clip(m.predict_proba(Xv)[:, 1], 1e-6, 1 - 1e-6)
            loss = -np.mean(val["y"] * np.log(p) + (1 - val["y"]) * np.log(1 - p))
            if best is None or loss < best[0]:
                best = (loss, m)
        return cls(best[1])

    def predict(self, d):
        return self.model.predict_proba(_hand(d))[:, 1], None

    def save(self, out: Path) -> None:
        (out / "model.pkl").write_bytes(pickle.dumps(self.model))

    @classmethod
    def load(cls, out: Path) -> "GBTModel":
        return cls(pickle.loads((out / "model.pkl").read_bytes()))


class SetModel(WPModel):
    kind = "set"
    has_bring = True

    def __init__(self, path: Path, calibration: dict | None = None, human_ctx_col: int = 6):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 4
        self.session = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        self.calibration = calibration or {}
        self.human_ctx_col = human_ctx_col

    def _temperatures(self, glob: np.ndarray) -> np.ndarray:
        """Per-row temperature: human play and bot play are calibrated separately."""
        if not self.calibration:
            return np.ones(len(glob))
        human = glob[:, self.human_ctx_col] == 1
        return np.where(human, self.calibration.get("human", 1.0), self.calibration.get("selfplay", 1.0))

    def predict(self, d, bs: int = 4096):
        wp, br = [], []
        for i in range(0, len(d["glob"]), bs):
            w, b = self.session.run(None, {"cat": d["cat"][i : i + bs].astype(np.int64),
                                           "num": d["num"][i : i + bs].astype(np.float32), "glob": d["glob"][i : i + bs]})
            wp.append(w)
            br.append(b)
        if not wp:
            return np.zeros(0), np.zeros((0, 12))
        sig = lambda z: 1 / (1 + np.exp(-z))  # noqa: E731
        return sig(np.concatenate(wp) / self._temperatures(d["glob"])), sig(np.concatenate(br))


def registered(reg_id: str) -> list[dict[str, Any]]:
    return [e for e in (json.loads(REGISTRY.read_text())["wp"] if REGISTRY.exists() else [])
            if e["regulation"] == reg_id]


def default_version(reg_id: str) -> str | None:
    """The newest set model: the only kind with a bring head, so the only one that can rank brings."""
    sets = [e for e in registered(reg_id) if e["kind"] == "set"]
    return sets[-1]["version"] if sets else None


def in_battle_version(reg_id: str) -> str | None:
    """The model to draw a WP with *during* a battle.

    Prefers one whose in-battle gates pass over the newest set encoder, because that is the claim
    being made when a percentage is put on screen mid-battle. Today that is the GBT baseline: it
    beats the constant in every turn bucket and holds ECE < 0.03 throughout, while the set encoder
    misses on the last bucket and on games played to the end.
    """
    passing = [e for e in registered(reg_id) if (e.get("gates") or {}).get("in_battle_pass")]
    if passing:
        return sorted(passing, key=lambda e: e.get("created") or "")[-1]["version"]
    return default_version(reg_id)


def load_model(reg_id: str, version: str) -> WPModel:
    if version == "constant":
        return ConstantModel()
    out = model_dir(reg_id, version)
    kind = json.loads((out / "card.json").read_text())["kind"]
    def _set(o: Path) -> SetModel:
        cal = o / "calibration.json"
        return SetModel(o / "model.onnx", json.loads(cal.read_text()) if cal.exists() else None)

    return {"logistic": LogisticModel.load, "gbt": GBTModel.load, "set": _set}[kind](out)


# --- predictions for records (orientation + spectator symmetry) -----------------------------------

def symmetrize(d: dict[str, np.ndarray], p: np.ndarray) -> dict[str, np.ndarray]:
    """Collapse rows to one prediction per snapshot, as P(p1 wins) for spectator rows and P(A wins)
    for player rows. Spectator: average of the A=p1 row and 1 − the A=p2 row of the same snapshot."""
    key = np.stack([d["battle"], d["point"], d["kind"], d["perspective"]], 1)
    spec = d["perspective"] == 0
    rows = np.nonzero(~spec | (d["orient"] == 0))[0]
    out_p = p[rows].astype(np.float64).copy()
    s_rows = np.nonzero(spec & (d["orient"] == 1))[0]
    if len(s_rows):
        lookup = {tuple(k): i for i, k in zip(s_rows, key[s_rows])}
        for j, r in enumerate(rows):
            if spec[r]:
                mate = lookup.get(tuple(key[r]))
                if mate is not None:
                    out_p[j] = (p[r] + 1 - p[mate]) / 2
    res = {k: v[rows] for k, v in d.items() if k not in ("battle_names", "cat", "num", "glob", "bring")}
    res["p"] = out_p
    return res


def predict_records(model: WPModel, records: list[dict], fz: Featurizer) -> list[float]:
    """P(p1 wins) for spectator records, P(perspective wins) for player records."""
    d = featurize(records, fz)
    p, _ = model.predict(d)
    return list(symmetrize(d, p)["p"])


# --- cards and registry ---------------------------------------------------------------------------

def git_state() -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=paths.ROOT, capture_output=True, text=True).stdout.strip()

    return {"sha": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain", "--untracked-files=no"))}


def write_card(reg_id: str, version: str, kind: str, dataset_info: dict, extra: dict | None = None) -> Path:
    out = model_dir(reg_id, version)
    manifest = paths.ROOT / dataset_info["manifest"]
    card = {
        "version": version, "kind": kind, "regulation": reg_id,
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "perspectives": ["spectator", "player"], "info_regime": "ots",
        "dataset": dataset_info,
        "training_manifest": {"path": dataset_info["manifest"],
                              "sha256": __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()},
        "training_data": _data_summary(manifest),
        "git": git_state(),
    } | (extra or {})
    (out / "card.json").write_text(json.dumps(card, indent=1) + "\n")
    _register(card, out)
    return out


def _data_summary(manifest: Path) -> dict:
    m = json.loads(manifest.read_text())
    return {"files": [f["path"] for f in m["files"]],
            "battles_by_source": {s: sum(b["source"] == s for b in m["battles"]) for s in ("selfplay", "human")}}


def eval_fingerprint(files: list[Path]) -> dict:
    """Identity of the rows a model was *scored* on.

    The training manifest cannot answer this. It hashes the file a model was trained from, which
    changes when the manifest is merely rebuilt (it carries a `created` timestamp) and does not
    change when the held-out features are re-featurized underneath an old model. Comparing two
    headline numbers is a question about the evaluation rows and nothing else, so hash those.
    """
    import hashlib

    each, combined = {}, hashlib.sha256()
    for p in sorted(files):
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        each[p.name] = h.hexdigest()[:16]
        combined.update(p.name.encode()); combined.update(h.digest())
    return {"sha256": combined.hexdigest(), "files": each}


def _register(card: dict, out: Path) -> None:
    reg = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {"wp": []}
    reg["wp"] = [e for e in reg["wp"] if not (e["version"] == card["version"] and e["regulation"] == card["regulation"])]
    reg["wp"].append({"version": card["version"], "regulation": card["regulation"], "kind": card["kind"],
                      "created": card["created"], "path": str(out.relative_to(paths.ROOT)),
                      "headline": card.get("headline", {}), "gates": card.get("gates", {}),
                      # A dataset name is reused when its contents are rebuilt, so the name alone
                      # doesn't say two models were scored on the same rows. The manifest hash does.
                      "manifest_sha256": card.get("training_manifest", {}).get("sha256"),
                      # What the headline was actually measured on — the only sound basis for
                      # saying two rows of the registry may or may not be compared. `eval_at` is
                      # when it was scored, which `created` (when the model was built) is not.
                      "eval_sha256": card.get("eval_dataset", {}).get("sha256"),
                      "eval_at": card.get("eval_dataset", {}).get("at")})
    reg["wp"].sort(key=lambda e: (e["regulation"], e["created"]))
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, indent=1) + "\n")


def update_card(reg_id: str, version: str, **fields: Any) -> None:
    out = model_dir(reg_id, version)
    card = json.loads((out / "card.json").read_text()) | fields
    (out / "card.json").write_text(json.dumps(card, indent=1) + "\n")
    _register(card, out)


def train_baseline(reg, kind: str, dataset: str, version: str) -> Path:
    from vgc.wp.dataset import FEATURES, load

    info = json.loads((FEATURES / reg.id / dataset / "info.json").read_text())
    train, val = load(reg, dataset, "train"), load(reg, dataset, "val")
    out = model_dir(reg.id, version)
    out.mkdir(parents=True, exist_ok=True)
    if kind == "logistic":
        model: WPModel = LogisticModel.fit(train, logistic_columns(Featurizer(reg, Vocab.load(FEATURES / reg.id / dataset / "vocab.json"))))
    elif kind == "gbt":
        model = GBTModel.fit(train, val)
    else:
        raise ValueError(kind)
    model.save(out)  # type: ignore[attr-defined]
    p, _ = model.predict(val)
    p = np.clip(p, 1e-6, 1 - 1e-6)
    val_loss = float(-np.mean(val["y"] * np.log(p) + (1 - val["y"]) * np.log(1 - p)))
    (out / "vocab.json").write_text((FEATURES / reg.id / dataset / "vocab.json").read_text())
    return write_card(reg.id, version, kind, info, {"val_wp_logloss": round(val_loss, 5)})


# --- calibration ------------------------------------------------------------------------------

def fit_temperature(logit: np.ndarray, y: np.ndarray) -> float:
    """Temperature that minimises log loss: >1 makes the model less confident."""
    best = (1e9, 1.0)
    for t in np.exp(np.linspace(np.log(0.5), np.log(4.0), 141)):
        p = 1 / (1 + np.exp(-logit / t))
        loss = -np.mean(y * np.log(p + 1e-9) + (1 - y) * np.log(1 - p + 1e-9))
        best = min(best, (float(loss), float(t)))
    return best[1]


def calibrate(reg_id: str, version: str, train: dict[str, np.ndarray], val: dict[str, np.ndarray],
              human_ctx_col: int = 6) -> dict[str, Any]:
    """Fit one temperature per play context. Bot self-play and human play differ in how decisive
    positions are, and validation is mostly self-play, so a single temperature leaves human
    predictions over-confident. Human rows come from the *training* manifest — never a held-out set."""
    out = model_dir(reg_id, version)
    model = load_model(reg_id, version)
    temps = {}
    for name, d, rows in (("selfplay", val, val["glob"][:, human_ctx_col] == 0),
                          ("human", train, train["glob"][:, human_ctx_col] == 1)):
        sub = {k: v[rows] for k, v in d.items() if k != "battle_names"}
        if not len(sub["y"]):
            continue
        p = np.clip(model.predict(sub)[0], 1e-6, 1 - 1e-6)
        temps[name] = round(fit_temperature(np.log(p / (1 - p)), sub["y"]), 4)
        temps[f"{name}_rows"] = int(len(sub["y"]))
    (out / "calibration.json").write_text(json.dumps(temps, indent=1) + "\n")
    return temps
