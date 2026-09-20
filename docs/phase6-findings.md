# Phase 6 — does the simulator measure the game, or the bot?

Date: 2026-09-20 · Run `phase6-heuristic`, outcome digest `5190c588937c884a`. Re-runs with
`make sim-validity` (or `vgc sim validate`); the full result, including every pairing, is
[`data/analysis/reg_mc/sim_validity.json`](../data/analysis/reg_mc/sim_validity.json).

**Verdict: negative, and not marginally.** Heuristic-vs-heuristic win rate is a highly reliable
measurement of *something* — split-half reliability 0.96 — and that something has no relationship to
who wins the real game. On 4,579 played-out human battles spanning 2,684 independent series, the
simulated win rate orders the human outcome at **AUC 0.5119, 95% CI [0.4948, 0.5296]**. Granted the
best single-parameter rescaling that can be fitted without seeing the rows it is scored on, it buys
**0.0003 nats**, 95% CI [−0.0010, +0.0004].

PLAN-v2's fork therefore takes the second branch. Phase 10's precomputed matchup matrix is dead, and
so is any team-strength model distilled from it. Phase 9 — a stronger policy — becomes the
prerequisite, and this check re-runs against that policy before anything is built on it.

---

## 1. The experiment

Every human battle whose two open team sheets resolve to the pool gives a real-meta pairing. Each
pairing was played 15 times, heuristic vs heuristic, sides alternating, from the same team text the
replay showed. The simulated win rate for that pairing is then a prediction of who won the human
game, scored against a 0.5 constant.

```
7,455 human battles · 3,372 distinct pairings · 3,431 series
50,580 battles simulated · 0 errors · 27.8 battles/s · 30.3 min on 8 workers
```

| subset | games | series | log loss | constant | delta | 95% CI | AUC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| all | 7,455 | 3,431 | 1.09494 | 0.69315 | +0.40179 | [+0.377, +0.428] | 0.5149 |
| **ended_normal** | **4,579** | **2,684** | **1.10146** | 0.69315 | **+0.40831** | [+0.374, +0.441] | **0.5119** |
| forfeit | 2,876 | 2,032 | 1.08455 | 0.69315 | +0.39141 | [+0.349, +0.435] | 0.5195 |
| rated | 2,704 | 2,703 | 1.08010 | 0.69315 | +0.38696 | [+0.349, +0.425] | 0.5237 |
| held-out replay groups | 865 | 400 | 1.13794 | 0.69315 | +0.44479 | [+0.364, +0.520] | 0.4974 |
| **all, recalibrated out-of-fold** | 7,455 | 3,431 | 0.69287 | 0.69315 | **−0.00027** | **[−0.0010, +0.0004]** | 0.5124 |

Pearson correlation between simulated WP and the human result: **0.0245**.

## 2. Why the two log-loss rows differ by 0.41 nats, and which one matters

The raw row says the simulator is *confidently* wrong. Its win rates are bimodal — median 0.529, but
quartiles at 0.118 and 0.882, and **55.8% of pairings land beyond 85/15** — while the human games
those pairings produced are coin flips (base rate 0.5038). Answering 0.94 to a question whose answer
is 0.50 costs far more than answering 0.50 would, which is the whole of the +0.41.

That number is real but it is the *second* question. A matchup matrix is consumed for ranking and
selection, and a pure scale error in it can be fitted out downstream. So the gate turns on the
scale-free evidence: AUC, and a log loss after one parameter has been fitted on logit(sim WP) with
the folds split by series so the scale never sees the rows it is scored on. Both come back at chance.
The distinction is worth keeping because it names the failure mode that *would* have been
recoverable, and this is not it.

## 3. The control that makes this a validity result, not a budget result

Fifteen battles per pairing is a small number, and a noisy predictor cannot predict. So: split each
pairing's 15 battles into alternating halves and correlate the two win rates. **r = 0.926, which
Spearman-Brown extrapolates to 0.961 at full length.**

The simulated win rate is one of the most reliably measured quantities in this repo. Spending 50
battles per pairing instead of 15 would have moved that from 0.96 to 0.99 and changed nothing:
the ceiling is not measurement error, it is that the quantity being measured so precisely is not the
one that decides human games.

This is the same shape as finding 2 in the [Phase 4 review](phase4-findings.md) — a team-strength
model fit on 108,918 self-play preview rows scored 0.7327 on held-out human preview, worse than
answering 0.5 — reached by a completely different route, and now with the power to be conclusive.

A supporting tell, weaker but consistent: the heuristic ends a game in 5.81 turns on average against
7.55 for a played-out human game. It is resolving matchups faster and harder than real play does.

## 4. What this retires

`vgc wp check-preview` and the `preview_tracks_sim` gate are removed. The gate scored a model's
preview WP against a simulated win rate over 30 pairings, 15 of them held out, and compared the
resulting Pearson correlation to a threshold of 0.5 — a statistic whose 95% interval at n=15 is
roughly ±0.5, so it could not distinguish "no signal" from "passing" either way. Gate rule 3 exists
because of it. Its last readings, kept here because the cards no longer carry them:

| model | pairings | corr (all) | corr (held-out team) | MAE | MAE vs constant |
| --- | --- | --- | --- | --- | --- |
| `wp-v1-set-full` | 30 | 0.138 | −0.021 | 0.3849 | 0.3930 |
| `wp-v1-sw-split-small` | 30 | 0.111 | −0.042 | 0.3927 | 0.3930 |
| `wp-v1-set-small` | 20 | 0.326 | 0.226 | 0.3402 | 0.3683 |

The substantive question it was asking — is anything known before turn 1 worth showing? — is now
`preview_beats_constant`, scored on the 2,899 held-out preview rows against the constant. The
separate question of whether the *simulator* tracks human results is what this document answers, and
`vgc sim validate` owns it from here.

**The replacement gate needed the same fix applied to it.** Its first version compared two log-loss
point estimates, and on that basis `wp-v1-set-full` passed: 0.68928 against 0.69315, a margin of
0.00387 nats on 2,899 preview battles. But finding 1 records that *every* model family lands within
0.004 of the constant at preview, so the entire field fits inside one model's margin — and the
interval for that margin is [−0.00951, +0.00177], which straddles zero. The verdict would have
turned on which side the noise fell. Replacing an underpowered gate with a differently underpowered
one is not a fix.

So `vs_constant()` now attaches an interval to every bucket, and both `preview_beats_constant` and
`in_battle_beats_constant` require the whole interval to clear zero. No bootstrap is involved: the
constant's loss is exactly log 2 on every row, so the comparison is a one-sample test on the per-row
differences and the CR0 sandwich is closed-form. It is clustered by battle, which matters for the
in-battle buckets — a battle contributes 2.5 to 3.6 decision points and the label is the same
winner at all of them, so treating the rows as independent narrows the interval by 1.3× at t1–2
rising to 2.1× at t7+. It does *not* matter at preview, where `symmetrize` has already collapsed the
two orientations and each row is its own battle; there the margin simply is not resolvable at
n=2,899.

Under the tightened gate all three carded models fail at preview (`wp-v1-gbt` by exactly 0.0,
`wp-v1-set-full` by −0.00387 ± 0.0056, `wp-v1-sw-split-small` by −0.00236 ± 0.0062) and all three
still beat the constant in every in-battle bucket, by 0.036 to 0.287 nats with intervals well clear
of zero. The shipped model's `in_battle_pass` is unchanged.

## 5. Two things the design got right, worth keeping

**The unit of independence is the series, not the game.** PLAN-v2 quoted "2,796 pairings seen ≥2
times" as evidence of replication. Almost all of that is Bo3 repetition: only **44 pairings recur
across two distinct series**, and a series shares both teams *and both players*. Every interval above
is a cluster bootstrap over the 3,431 series. Bootstrapping games instead would have narrowed them by
roughly the square root of the average series length and claimed precision the data does not have.

**Breadth beat depth.** The plan sketched 1,000 pairings × 50 battles. Outcome-side precision is
linear in series covered; extra battles per pairing only shrink predictor noise, and §3 shows how
little of that there was to shrink. All 3,372 pairings × 15 battles covers every game and every
series in the corpus for the same wall clock, and is worth about 1.7× the power.

## 6. What happens now

- **Phase 10's matrix form is dead** in its precomputed shape, and Phase 11 cannot be built on it.
  On-demand evaluation of the single matchup in front of the user was already Phase 10's default;
  it remains available, but it inherits this result — under *this* policy its numbers describe the
  bot, so it waits on Phase 9 too.
- **Phase 9 (policy strength) is promoted to the prerequisite**, which is the branch PLAN-v2 named.
- **Self-play generation stays paused.** Finding 3 said the rows were not paying for themselves in
  training; its one remaining justification was the simulator role this phase tests, and the test
  failed.
- **Phase 7 is unaffected and is the next thing to build.** The deterministic team tools never
  depended on this answer, which is why they were ordered independently of it.
- **This check is standing.** Re-run it against every new policy, before anything is built on that
  policy's numbers.

## 7. Scope of the claim

Team ids come from `|showteam|`, so every game scored here is **open-sheet Bo3 ladder play**, median
rating near 1100, 58% of the corpus unrated (Phase 4 finding 5). The result says the current
heuristic does not predict *that* population. It does not establish what a stronger policy would do —
that is exactly what re-running this against Phase 9's policy is for.
