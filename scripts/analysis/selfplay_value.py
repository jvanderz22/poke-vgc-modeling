"""Does the 60k-battle heuristic self-play corpus earn its place in the WP training set?

The same GBT recipe on hand features, trained three ways, scored on the frozen held-out human
spectator rows (the headline set).
"""
import sys
import numpy as np
sys.path.insert(0, "src")
from vgc.wp.models import GBTModel, symmetrize
from vgc.wp.evaluate import metrics

D = "data/features/reg_mc/wp-v1"
KEYS = ("cat", "num", "glob", "y", "source", "kind", "battle", "perspective", "orient", "turn", "forfeit", "bring", "point")

def load(split):
    with np.load(f"{D}/{split}.npz") as z:
        return {k: z[k] for k in KEYS}

def sub(d, m):
    return {k: v[m] for k, v in d.items()}

tr, va, ev = load("train"), load("val"), load("eval_human_ots")
print("train", len(tr["y"]), "human", int(tr["source"].sum()))

def score(name, d_tr, d_va):
    m = GBTModel.fit(d_tr, d_va)
    p, _ = m.predict(ev)
    s = symmetrize(ev, p)
    for code, label in ((0, "spectator"), (2, "player_approx")):
        k = s["perspective"] == code
        if not k.any():
            continue
        r = metrics(s["p"][k], s["y"][k])
        prev = k & (s["kind"] == 0)
        rp = metrics(s["p"][prev], s["y"][prev]) if prev.any() else {"logloss": float("nan"), "n": 0}
        print(f"  {name:22} {label:14} n={r['n']:6} logloss {r['logloss']:.4f} ece {r['ece']:.4f} "
              f"acc {r['accuracy']:.4f} | preview n={rp['n']} ll {rp['logloss']:.4f}")

score("human + selfplay", tr, va)
score("human only", sub(tr, tr["source"] == 1), sub(va, va["source"] == 1))
score("selfplay only", sub(tr, tr["source"] == 0), sub(va, va["source"] == 0))
