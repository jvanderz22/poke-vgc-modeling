"""Learning curve for pre-battle team signal: is 8k games too few, or is the signal not there?

Same Bradley-Terry over species presence, 5-fold CV, trained on a growing share of the games.
If log loss is still falling at 100%, more replays help. If it has flattened, it hasn't.
"""
import glob, gzip, json, collections
import numpy as np
from scipy.sparse import csr_matrix
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import KFold

games = {}
for f in glob.glob("data/snapshots/reg_mc/human/*/*.jsonl.gz"):
    for line in gzip.open(f, "rt"):
        r = json.loads(line)
        if r["kind"] != "preview" or r["label"]["winner"] not in ("p1", "p2"):
            continue
        games[r["battle"]] = (r["label"]["winner"], r["label"]["ended_by"], r["label"]["turns"],
                              {s: [m["species"] for m in r["obs"]["sides"][s]["mons"]] for s in ("p1", "p2")})

rows = list(games.values())
sp_idx = {}
for _, _, _, sp in rows:
    for s in ("p1", "p2"):
        for x in sp[s]:
            sp_idx.setdefault(x, len(sp_idx))

def build(rs):
    data, ind, ptr = [], [], [0]
    for _, _, _, sp in rs:
        acc = collections.Counter()
        for x in sp["p1"]: acc[sp_idx[x]] += 1
        for x in sp["p2"]: acc[sp_idx[x]] -= 1
        for k, v in acc.items():
            if v: ind.append(k); data.append(float(v))
        ptr.append(len(ind))
    return csr_matrix((data, ind, ptr), shape=(len(rs), len(sp_idx)))

def cv(rs, C=0.01):
    X = build(rs); y = np.array([float(w == "p1") for w, _, _, _ in rs])
    out = []
    for tr, te in KFold(5, shuffle=True, random_state=0).split(X):
        m = LogisticRegression(C=C, fit_intercept=False, max_iter=3000).fit(X[tr], y[tr])
        p = np.clip(m.predict_proba(X[te])[:, 1], 1e-6, 1 - 1e-6)
        out.append(-np.mean(y[te] * np.log(p) + (1 - y[te]) * np.log(1 - p)))
    return np.mean(out), np.std(out)

rng = np.random.default_rng(0)
order = rng.permutation(len(rows))
print(f"{len(rows)} human games, {len(sp_idx)} species")
for frac in (0.25, 0.5, 0.75, 1.0):
    rs = [rows[i] for i in order[: int(frac * len(rows))]]
    m, s = cv(rs)
    print(f"  {frac:>5.0%}  n={len(rs):5}  cv logloss {m:.4f} (+-{s:.4f})   lift vs 0.6931: {0.6931-m:+.4f}")

# same, on the games most likely to carry team signal: played out, not a quick forfeit
long_ = [r for r in rows if r[1] == "normal" and r[2] >= 6]
m, s = cv(long_)
print(f"  normal endings, >=6 turns: n={len(long_)}  cv logloss {m:.4f}  lift {0.6931-m:+.4f}")
