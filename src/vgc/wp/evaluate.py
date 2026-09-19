"""WP evaluation on the frozen held-out sets: calibration first, then discrimination.

For every evaluation set and perspective: log loss, Brier and ECE (10 equal-width bins), with
reliability bins, overall and per turn bucket (preview, bring, turns 1–2, 3–4, 5–6, 7+).
Human sets are also split by how the game ended.

Also:
  player_vs_spectator: on the same decision points, the player's view (more information)
    must score a lower log loss than the spectator's.
  bring: the bring head's top-4 at team preview, against a species usage-rate baseline.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from vgc.wp.features import KINDS
from vgc.wp.models import WPModel, symmetrize

PERSPECTIVES = {0: "spectator", 1: "player", 2: "player_approx"}
PREVIEW, BRING = KINDS.index("preview"), KINDS.index("bring")


def metrics(p: np.ndarray, y: np.ndarray, bins: int = 10) -> dict[str, Any]:
    if len(p) == 0:
        return {"n": 0}
    p = np.clip(p.astype(np.float64), 1e-6, 1 - 1e-6)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    rel, ece = [], 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            rel.append({"bin": [round(edges[b], 1), round(edges[b + 1], 1)], "mean_p": round(float(p[m].mean()), 4),
                        "win_rate": round(float(y[m].mean()), 4), "n": int(m.sum())})
            ece += m.mean() * abs(p[m].mean() - y[m].mean())
    return {
        "n": int(len(p)),
        "logloss": round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 5),
        "brier": round(float(np.mean((p - y) ** 2)), 5),
        "ece": round(float(ece), 5),
        "accuracy": round(float(np.mean((p > 0.5) == (y > 0.5))), 4),
        "reliability": rel,
    }


def _bucket(kind: np.ndarray, turn: np.ndarray) -> np.ndarray:
    out = np.empty(len(kind), dtype=object)
    out[:] = "t7+"
    out[turn <= 6] = "t5-6"
    out[turn <= 4] = "t3-4"
    out[turn <= 2] = "t1-2"
    out[kind == PREVIEW] = "preview"
    out[kind == BRING] = "bring"
    return out


BUCKETS = ("preview", "bring", "t1-2", "t3-4", "t5-6", "t7+")


def evaluate_set(model: WPModel, d: dict[str, np.ndarray], usage: np.ndarray | None = None) -> dict[str, Any]:
    p, bring = model.predict(d)
    s = symmetrize(d, p)
    y = s["y"]
    buckets = _bucket(s["kind"], s["turn"])
    out: dict[str, Any] = {"perspectives": {}}
    for code, name in PERSPECTIVES.items():
        m = s["perspective"] == code
        if not m.any():
            continue
        entry = {"all": metrics(s["p"][m], y[m]), "by_turn": {}}
        for b in BUCKETS:
            mb = m & (buckets == b)
            if mb.any():
                entry["by_turn"][b] = {k: v for k, v in metrics(s["p"][mb], y[mb]).items() if k != "reliability"}
        if s["source"][m].any():
            for ended, flag in (("normal", 0), ("forfeit", 1)):
                me = m & (s["forfeit"] == flag)
                if me.any():
                    entry[f"ended_{ended}"] = {k: v for k, v in metrics(s["p"][me], y[me]).items() if k != "reliability"}
        out["perspectives"][name] = entry
    out["player_vs_spectator"] = player_vs_spectator(d, p, s)
    if bring is not None:
        out["bring"] = bring_eval(d, bring, usage)
    return out


def player_vs_spectator(d: dict[str, np.ndarray], p: np.ndarray, s: dict[str, np.ndarray]) -> dict[str, Any]:
    """Log loss of player vs spectator predictions on exactly the same decision points.

    `matched`: the spectator row with the same orientation (A = the player's side), so model and
    orientation are identical and only the information differs. That comparison is the test.
    `symmetrized`: the spectator's two-orientation average, which is also a small ensemble."""
    raw = {(b, pt, k, o): pr for b, pt, k, o, pr, persp in
           zip(d["battle"], d["point"], d["kind"], d["orient"], p, d["perspective"]) if persp == 0}
    sym = {(b, pt, k): pr for b, pt, k, pr, persp in zip(s["battle"], s["point"], s["kind"], s["p"], s["perspective"]) if persp == 0}
    res = {}
    for code in (1, 2):
        pl, sp_raw, sp_sym, ys = [], [], [], []
        for b, pt, k, o, pr, persp, y in zip(d["battle"], d["point"], d["kind"], d["orient"], p, d["perspective"], d["y"]):
            if persp != code or (b, pt, k, o) not in raw:
                continue
            pl.append(pr)
            sp_raw.append(raw[(b, pt, k, o)])
            q = sym[(b, pt, k)]
            sp_sym.append(q if o == 0 else 1 - q)
            ys.append(y)
        if ys:
            y_arr = np.array(ys)
            ll = lambda x: metrics(np.array(x), y_arr)["logloss"]  # noqa: E731
            res[PERSPECTIVES[code]] = {"n": len(ys), "player_logloss": ll(pl), "spectator_logloss": ll(sp_raw),
                                       "spectator_symmetrized_logloss": ll(sp_sym)}
    return res


def usage_rates(train: dict[str, np.ndarray]) -> np.ndarray:
    """P(brought | species) from the training bring targets, indexed by species vocabulary id."""
    ids = train["cat"][:, :, 0].ravel()
    t = train["bring"].ravel()
    m = t >= 0
    n = np.bincount(ids[m], minlength=ids.max() + 1).astype(np.float64)
    k = np.bincount(ids[m], weights=t[m], minlength=ids.max() + 1)
    return (k + 2 * (4 / 6)) / (n + 2)  # shrink toward the base rate 4/6


def bring_eval(d: dict[str, np.ndarray], bring: np.ndarray, usage: np.ndarray | None) -> dict[str, Any]:
    """At team preview: how many of each side's true 4 are in the predicted top 4."""
    rows = np.nonzero(d["kind"] == PREVIEW)[0]
    res: dict[str, list] = {"model": [], "usage": [], "model_exact": [], "usage_exact": []}
    for r in rows:
        for k in (0, 1):
            t = d["bring"][r, 6 * k : 6 * k + 6]
            if (t < 0).any():
                continue
            truth = set(np.nonzero(t > 0.5)[0])
            top = set(np.argsort(-bring[r, 6 * k : 6 * k + 6])[:4])
            res["model"].append(len(truth & top) / 4)
            res["model_exact"].append(float(truth == top))
            if usage is not None:
                ids = d["cat"][r, 6 * k : 6 * k + 6, 0]
                u = usage[np.minimum(ids, len(usage) - 1)]
                utop = set(np.argsort(-u, kind="stable")[:4])
                res["usage"].append(len(truth & utop) / 4)
                res["usage_exact"].append(float(truth == utop))
    return {"n": len(res["model"]), "model_top4_overlap": _mean(res["model"]), "usage_top4_overlap": _mean(res["usage"]),
            "model_exact4": _mean(res["model_exact"]), "usage_exact4": _mean(res["usage_exact"]),
            "chance_top4_overlap": round(4 / 6, 4)}


def _mean(xs: list[float]) -> float | None:
    return round(float(np.mean(xs)), 4) if xs else None


def headline(results: dict[str, Any]) -> dict[str, Any]:
    """The numbers that matter: held-out human OTS games."""
    h = results.get("human_ots_all", {}).get("perspectives", {})
    out = {}
    for name in ("spectator", "player_approx"):
        if name in h:
            out[f"human_{name}"] = {k: h[name]["all"][k] for k in ("n", "logloss", "brier", "ece")}
    return out


def format_report(results: dict[str, Any], baselines: dict[str, dict] | None = None) -> str:
    lines = []
    for set_name, r in results.items():
        lines.append(f"== {set_name}")
        for persp, e in r["perspectives"].items():
            a = e["all"]
            base = ""
            for bname, b in (baselines or {}).items():
                bp = b.get(set_name, {}).get("perspectives", {}).get(persp, {}).get("all")
                if bp:
                    base += f" | {bname} {bp['logloss']:.4f}"
            lines.append(f"  {persp:14} n={a['n']:6} logloss {a['logloss']:.4f} brier {a['brier']:.4f} "
                         f"ece {a['ece']:.4f} acc {a['accuracy']:.3f}{base}")
            for b, m in e["by_turn"].items():
                lines.append(f"      {b:8} n={m['n']:6} logloss {m['logloss']:.4f} ece {m['ece']:.4f}")
            for ended in ("normal", "forfeit"):
                if f"ended_{ended}" in e:
                    m = e[f"ended_{ended}"]
                    lines.append(f"      ended {ended:8} n={m['n']:6} logloss {m['logloss']:.4f} ece {m['ece']:.4f}")
        for persp, c in r.get("player_vs_spectator", {}).items():
            lines.append(f"  {persp} vs spectator on {c['n']} shared points: {c['player_logloss']:.4f} vs "
                         f"{c['spectator_logloss']:.4f} (same orientation), {c['spectator_symmetrized_logloss']:.4f} (symmetrized)")
        if "bring" in r:
            b = r["bring"]
            lines.append(f"  bring head (n={b['n']}): top-4 overlap {b['model_top4_overlap']} vs usage {b['usage_top4_overlap']} "
                         f"(chance {b['chance_top4_overlap']}); exact 4 {b['model_exact4']} vs {b['usage_exact4']}")
    return "\n".join(lines)
