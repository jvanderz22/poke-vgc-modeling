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
from vgc.wp.features import KINDS, Featurizer, Vocab, featurize, hand_features, logistic_columns

MODELS = paths.ROOT / "models"
REGISTRY = MODELS / "registry.json"

# `Featurizer._glob` lays out: turn, one-hot over KINDS, perspective, ctx_human, approx,
# both-sheets-open. Derived rather than written as 8, because the two callers that need it are
# calibration and inference and a silent off-by-one there would mis-temperature every row.
TURN_COL = 0  # turn / 10
HUMAN_CTX_COL = len(KINDS) + 2
SHEETS_COL = len(KINDS) + 4
# The temperature varies with the turn, log-linearly, up to this turn and flat after it. Measured
# on validation rows, open sheets: preview wants 1.90, t1-2 1.52, t3-4 1.26, t5-6 1.10, t7+ 1.05 —
# one temperature (1.18) is over-confident early and fails t3 on under the calibration test.
TEMPERATURE_TURN_CAP = 7


def _stage(glob: np.ndarray) -> np.ndarray:
    """The turn a temperature is read at: 0 at preview and bring, capped at TEMPERATURE_TURN_CAP."""
    return np.minimum(np.rint(glob[:, TURN_COL] * 10), TEMPERATURE_TURN_CAP)


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

    def __init__(self, path: Path, calibration: dict | None = None, human_ctx_col: int = 6,
                 sheets_col: int = SHEETS_COL):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 4
        self.session = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        self.calibration = calibration or {}
        self.human_ctx_col = human_ctx_col
        self.sheets_col = sheets_col

    def _temperatures(self, glob: np.ndarray) -> np.ndarray:
        """Per-row temperature: play context *and* information regime are calibrated separately.

        The regime split is a gate-rule-7 fix rather than a tuning idea. A temperature fitted on
        open-sheet rows and applied to a closed-sheet row is a number travelling out of the regime
        it was measured in, which is the mistake the rule exists to stop — and it was measurable:
        every model fails `closed_in_battle_ece` on calibration while its log loss holds up.

        It only became possible on 2026-09-21. Fitting a closed-sheet temperature needs
        closed-sheet *training* rows, and until the ladder shard entered the manifest there were
        none; a model with no such rows falls back to the open-sheet temperature, which is what
        every model shipped before that date does.
        """
        if not self.calibration:
            return np.ones(len(glob))
        c = self.calibration
        human = glob[:, self.human_ctx_col] == 1
        closed = glob[:, self.sheets_col] == 0
        # Each context has a temperature at turn 0 and a slope in log temperature per turn. A
        # calibration without slopes (anything before 2026-09-28) has one temperature per context.
        closed_key = "human_closed" if "human_closed" in c else "human"
        t0 = np.where(human, np.where(closed, c.get(closed_key, 1.0), c.get("human", 1.0)), c.get("selfplay", 1.0))
        slope = np.where(human, np.where(closed, c.get(f"{closed_key}_slope", 0.0), c.get("human_slope", 0.0)),
                         c.get("selfplay_slope", 0.0))
        return t0 * np.exp(slope * _stage(glob))

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


class EnsembleSetModel(SetModel):
    """Several set models trained alike but for their seed, read as one: the mean of their raw
    logits, then the ensemble's own temperatures (it is calibrated and gated like any model). Every
    run stops at epoch 3–4 and where it stops sets the confidence (PLAN-v3); a mean over seeds is
    less hostage to where any one of them stopped."""

    def __init__(self, paths_: list[Path], calibration: dict | None = None, human_ctx_col: int = 6,
                 sheets_col: int = SHEETS_COL):
        import onnxruntime as ort

        super().__init__(paths_[0], calibration, human_ctx_col, sheets_col)
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 4
        self.sessions = [self.session] + [ort.InferenceSession(str(p), opts, providers=["CPUExecutionProvider"])
                                          for p in paths_[1:]]

    def predict(self, d, bs: int = 4096):
        wp, br = [], []
        for i in range(0, len(d["glob"]), bs):
            feed = {"cat": d["cat"][i : i + bs].astype(np.int64), "num": d["num"][i : i + bs].astype(np.float32),
                    "glob": d["glob"][i : i + bs]}
            outs = [s.run(None, feed) for s in self.sessions]
            wp.append(np.mean([o[0] for o in outs], axis=0))
            br.append(np.mean([o[1] for o in outs], axis=0))
        if not wp:
            return np.zeros(0), np.zeros((0, 12))
        sig = lambda z: 1 / (1 + np.exp(-z))  # noqa: E731
        return sig(np.concatenate(wp) / self._temperatures(d["glob"])), sig(np.concatenate(br))


def registered(reg_id: str) -> list[dict[str, Any]]:
    return [e for e in (json.loads(REGISTRY.read_text())["wp"] if REGISTRY.exists() else [])
            if e["regulation"] == reg_id]


SERVED = MODELS / "served.json"

# The information regimes a battle can be played in, and the one place they are named. Everything
# that offers, pins, gates or labels a regime reads this: `served.json`'s roles, the evaluator's
# roll-ups, `/api/models`' list of modes, and the new-battle form built from that list.
OPEN, CLOSED = "open", "closed"
SHEETS = (CLOSED, OPEN)          # the order the app offers them in; a cartridge game comes first
# Each regime's in-battle verdict, and the gates it rolls up. `in_battle_pass` is scored on Open
# Team Sheets games and `closed_sheet_pass` on the held-out Team Preview Only shard.
REGIME_GATES: dict[str, tuple[str, tuple[str, ...]]] = {
    OPEN: ("in_battle_pass", ("in_battle_beats_constant", "in_battle_ece")),
    CLOSED: ("closed_sheet_pass", ("closed_in_battle_beats_constant", "closed_in_battle_ece")),
}
ROLES = ("bring",) + tuple(f"in_battle_{s}" for s in SHEETS)


def check_sheets(sheets: str) -> str:
    if sheets not in SHEETS:
        raise ValueError(f"sheets must be one of {SHEETS}, not {sheets!r}")
    return sheets


def regime_verdict(gates: dict[str, Any], sheets: str) -> dict[str, Any]:
    """One regime's in-battle verdict for a model, and which of its gates failed."""
    name, parts = REGIME_GATES[check_sheets(sheets)]
    got = {g: gates[g] for g in parts if isinstance(gates.get(g), dict)}
    failed = [g for g, v in got.items() if v.get("pass") is False]
    # A gate that ran and could not decide says why — "too little data to tell" is a different
    # thing to show from "never scored", and the page needs to know which it is.
    undecided = {g: v["reason"] for g, v in got.items() if v.get("pass") is None and v.get("reason")}
    return {"sheets": sheets, "gate": name, "pass": gates.get(name), "failed": failed,
            "undecided": undecided}


def served(reg_id: str, role: str) -> str | None:
    """The model a person chose for this role, from `models/served.json`, or None if nobody has.

    Which model the app serves is a decision, and it used to be a side effect: "newest registered
    wins" meant registering a retrained model swapped what every page showed, with nothing in the
    diff saying so. A pin to a model the registry does not have is an error rather than a fallback,
    because falling back is how a swap goes unnoticed.
    """
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {ROLES}")
    pins = json.loads(SERVED.read_text()).get(reg_id, {}) if SERVED.exists() else {}
    version = pins.get(role)
    if version is None:
        return None
    entry = next((e for e in registered(reg_id) if e["version"] == version), None)
    if entry is None:
        raise ValueError(f"{SERVED.name} pins {role} to {version!r}, which is not registered for {reg_id}")
    if role == "bring" and entry["kind"] != "set":
        raise ValueError(f"{SERVED.name} pins bring to {version!r}, a {entry['kind']} model with no bring head")
    return version


def default_version(reg_id: str) -> str | None:
    """The model that ranks brings: the pinned one, or with no pin the newest set model — the only
    kind with a bring head."""
    pinned = served(reg_id, "bring")
    if pinned:
        return pinned
    sets = [e for e in registered(reg_id) if e["kind"] == "set"]
    return sets[-1]["version"] if sets else None


def in_battle_version(reg_id: str, sheets: str = OPEN) -> str | None:
    """The model to draw a WP with *during* a battle, in the regime the battle is played in.

    `sheets` is one of `SHEETS`, because the regimes are different claims (`REGIME_GATES`) and the
    model that does best in one is not the model that does best in the other (gate rule 7).

    With no pin: the newest model whose in-battle gates pass, then the bring model. That fallback
    is for a regulation nobody has made the decision for yet.
    """
    pinned = served(reg_id, f"in_battle_{check_sheets(sheets)}")
    if pinned:
        return pinned
    passing = [e for e in registered(reg_id) if (e.get("gates") or {}).get(REGIME_GATES[OPEN][0])]
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
        cal = json.loads(cal.read_text()) if cal.exists() else None
        if (o / "members.json").exists():
            members = json.loads((o / "members.json").read_text())["members"]
            return EnsembleSetModel([model_dir(reg_id, m) / "model.onnx" for m in members], cal)
        return SetModel(o / "model.onnx", cal)

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
        from vgc.wp.features import featurizer_version

        fv = featurizer_version(json.loads((FEATURES / reg.id / dataset / "info.json").read_text()))
        model: WPModel = LogisticModel.fit(train, logistic_columns(
            Featurizer(reg, Vocab.load(FEATURES / reg.id / dataset / "vocab.json"), version=fv)))
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


def fit_temperature_by_turn(logit: np.ndarray, y: np.ndarray, stage: np.ndarray) -> tuple[float, float]:
    """(temperature at turn 0, slope of log temperature per turn) that minimise log loss."""
    from scipy.optimize import minimize

    def loss(x):
        z = logit / np.exp(x[0] + x[1] * stage)
        return float(np.mean(np.logaddexp(0, z) - y * z))

    start = [np.log(fit_temperature(logit, y)), 0.0]
    x = minimize(loss, start, method="Nelder-Mead", options={"xatol": 1e-5, "fatol": 1e-8}).x
    return float(np.exp(x[0])), float(x[1])


def calibrate(reg_id: str, version: str, val: dict[str, np.ndarray],
              human_ctx_col: int = 6, sheets_col: int = SHEETS_COL) -> dict[str, Any]:
    """Fit a temperature per play context *and* information regime, varying with the turn.

    Bot self-play and human play differ in how decisive positions are, and validation is mostly
    self-play, so a single temperature leaves human predictions over-confident. Open and closed
    sheets differ again: `partial_information.py` shows a set model's ECE rising from 0.032 to
    0.053 when a quarter of the opponent is hidden, which is a confidence fault and not a
    discrimination one, so a temperature is the right shape of fix for it.

    Every temperature is fitted on the validation split — battles from the training manifest the
    model never took a gradient step on, and never an eval set. Human temperatures used to be
    fitted on training rows, and a model scores its own training rows more confidently than new
    ones: wp-v1e's training rows asked for 0.82 (sharpen) where its validation rows asked for
    1.18 (soften), and its ECE nearly doubled. The closed-sheet bucket is empty for any model
    trained before the ladder shard was manifested, and an empty bucket is written as absent so
    `_temperatures` falls back to the open-sheet value rather than to 1.0.

    One temperature per regime was not enough once the open-sheet ECE gates became a test with
    power: the model is over-confident early and about right late, so a single temperature
    over-softens late turns to fix early ones and fails both. Each regime gets a slope in log
    temperature per turn — two numbers, not one per bucket, because the closed-sheet buckets are
    a few hundred battles each and a per-bucket fit would chase their noise.
    """
    out = model_dir(reg_id, version)
    model = load_model(reg_id, version)
    model.calibration = {}  # fit on raw logits, not on top of an earlier calibration.json
    temps = {}
    human = val["glob"][:, human_ctx_col] == 1
    for name, rows in (("selfplay", ~human),
                       ("human", human & (val["glob"][:, sheets_col] == 1)),
                       ("human_closed", human & (val["glob"][:, sheets_col] == 0))):
        sub = {k: v[rows] for k, v in val.items() if k != "battle_names"}
        if not len(sub["y"]):
            continue
        p = np.clip(model.predict(sub)[0], 1e-6, 1 - 1e-6)
        t0, slope = fit_temperature_by_turn(np.log(p / (1 - p)), sub["y"], _stage(sub["glob"]))
        temps[name] = round(t0, 4)
        temps[f"{name}_slope"] = round(slope, 5)
        temps[f"{name}_rows"] = int(len(sub["y"]))
    (out / "calibration.json").write_text(json.dumps(temps, indent=1) + "\n")
    return temps
