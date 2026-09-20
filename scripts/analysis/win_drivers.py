"""What actually predicts the winner of a human Reg M-C replay: the teams, or the players?

Bradley-Terry over player identity vs over team composition, both 5-fold grouped by battle,
on the same games.
"""
import glob, gzip, json, collections
import numpy as np
from scipy.sparse import csr_matrix, hstack
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import KFold

# --- battle -> (players, winner, teams) from snapshots + replay files ------------------
games = {}
for f in glob.glob("data/snapshots/reg_mc/human/*/*.jsonl.gz"):
    for line in gzip.open(f, "rt"):
        r = json.loads(line)
        if r["kind"] != "preview":
            continue
        games.setdefault(r["battle"], {"winner": r["label"]["winner"], "teams": r["meta"]["teams"],
                                       "rating": r["meta"].get("rating"), "ended": r["label"]["ended_by"],
                                       "species": {s: [m["species"] for m in r["obs"]["sides"][s]["mons"]]
                                                   for s in ("p1", "p2")}})
print("human battles with a preview snapshot:", len(games))

names = {}
for f in glob.glob("data/replays/*/*.json.gz"):
    j = json.load(gzip.open(f, "rt"))
    names[j["id"]] = j["players"]

rows = [(b, g) for b, g in games.items() if g["winner"] in ("p1", "p2") and b in names and len(names[b]) == 2]
print("joined to a replay:", len(rows))

pl_idx, sp_idx = {}, {}
def idx(d, k): return d.setdefault(k, len(d))
for b, g in rows:
    for p in names[b]: idx(pl_idx, p)
    for s in ("p1", "p2"):
        for sp in g["species"][s]: idx(sp_idx, sp)
print(f"distinct players {len(pl_idx)}, distinct species {len(sp_idx)}")

def build(cols, fn):
    data, ind, ptr = [], [], [0]
    for b, g in rows:
        acc = collections.Counter(); fn(b, g, acc)
        for k, v in acc.items():
            if v: ind.append(k); data.append(float(v))
        ptr.append(len(ind))
    return csr_matrix((data, ind, ptr), shape=(len(rows), cols))

def players(b, g, acc):
    a, c = names[b]
    acc[pl_idx[a]] += 1; acc[pl_idx[c]] -= 1

def teams(b, g, acc):
    for sp in g["species"]["p1"]: acc[sp_idx[sp]] += 1
    for sp in g["species"]["p2"]: acc[sp_idx[sp]] -= 1

Xp, Xt = build(len(pl_idx), players), build(len(sp_idx), teams)
y = np.array([float(g["winner"] == "p1") for _, g in rows])
print("p1 win rate:", round(y.mean(), 4), " forfeits:", round(np.mean([g["ended"] == "forfeit" for _, g in rows]), 3))

def cv(X, label, C):
    kf, out = KFold(5, shuffle=True, random_state=0), []
    for tr, te in kf.split(X):
        m = LogisticRegression(C=C, fit_intercept=False, max_iter=3000).fit(X[tr], y[tr])
        p = np.clip(m.predict_proba(X[te])[:, 1], 1e-6, 1 - 1e-6)
        out.append(-np.mean(y[te] * np.log(p) + (1 - y[te]) * np.log(1 - p)))
    print(f"  {label:26} C={C:<6} cv logloss {np.mean(out):.4f} (+-{np.std(out):.4f})")

print("constant 0.5:", round(float(np.log(2)), 4))
for C in (0.01, 0.1, 1.0):
    cv(Xp, "player identity", C)
for C in (0.003, 0.01, 0.1):
    cv(Xt, "team composition", C)
for C in (0.03,):
    cv(hstack([Xp, Xt]).tocsr(), "players + teams", C)

# how often does a player recur at all?
cnt = collections.Counter()
for b, _ in rows:
    for p in names[b]: cnt[p] += 1
g = np.array(sorted(cnt.values()))
print(f"games per player: median {np.median(g):.0f}, p90 {np.percentile(g,90):.0f}, "
      f"share of players with 1 game {np.mean(g==1):.1%}")
