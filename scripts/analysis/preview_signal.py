"""Is there any team-composition signal at team preview, and where does it come from?

Bradley-Terry over species presence: x = (my 6 one-hot) - (their 6 one-hot), y = I won.
Antisymmetric by construction, no intercept. Trained on human preview rows and, separately,
on self-play preview rows; both scored on the frozen held-out human preview rows.
"""
import json
import numpy as np
from scipy.sparse import csr_matrix
from sklearn.linear_model import LogisticRegression

D = "data/features/reg_mc/wp-v1"
vocab = json.load(open(f"{D}/vocab.json"))
NSP = len(vocab["species"]) + 3

def load(split, keys=("cat", "y", "source", "kind", "battle", "perspective", "orient")):
    with np.load(f"{D}/{split}.npz") as z:
        return {k: z[k] for k in keys}

def design(d, rows):
    cat = d["cat"][rows]                    # [n, 12, 8]
    n = len(rows)
    sp = cat[:, :, 0]                       # species ids
    data, ind, ptr = [], [], [0]
    for i in range(n):
        acc = {}
        for j in range(12):
            s = int(sp[i, j])
            if s <= 2:                      # PAD/UNK/NONE
                continue
            acc[s] = acc.get(s, 0) + (1 if j < 6 else -1)
        for k, v in acc.items():
            if v:
                ind.append(k); data.append(float(v))
        ptr.append(len(ind))
    return csr_matrix((data, ind, ptr), shape=(n, NSP)), d["y"][rows]

def ll(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))

tr = load("train")
ev = load("eval_human_ots")

prev_tr_h = np.nonzero((tr["kind"] == 0) & (tr["source"] == 1))[0]
prev_tr_s = np.nonzero((tr["kind"] == 0) & (tr["source"] == 0))[0]
prev_ev = np.nonzero((ev["kind"] == 0) & (ev["perspective"] == 0))[0]
print(f"preview rows: human train {len(prev_tr_h)} ({len(np.unique(tr['battle'][prev_tr_h]))} battles), "
      f"selfplay train {len(prev_tr_s)}, heldout human {len(prev_ev)}")

Xe, ye = design(ev, prev_ev)
print(f"constant 0.5 on heldout preview: {ll(np.full(len(ye), .5), ye):.4f}")

for name, rows, src in (("human", prev_tr_h, tr), ("selfplay", prev_tr_s, tr)):
    X, y = design(src, rows)
    for C in (0.003, 0.01, 0.03, 0.1):
        m = LogisticRegression(C=C, fit_intercept=False, max_iter=2000).fit(X, y)
        p = m.predict_proba(Xe)[:, 1]
        # symmetrize over the two orientations of each spectator preview row
        key = {}
        for i, r in enumerate(prev_ev):
            key.setdefault((int(ev["battle"][r]), int(ev["orient"][r])), i)
        print(f"  train={name:9} C={C:<6} heldout-human preview logloss {ll(p, ye):.4f} "
              f"acc {np.mean((p > .5) == (ye > .5)):.4f}  spread sd {p.std():.3f} "
              f"nonzero coefs {int((np.abs(m.coef_) > 1e-6).sum())}")
