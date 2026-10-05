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

**The spreads are imputed, and cannot not be.** An open team sheet carries species, item, ability,
moves and nature; it does not carry the 66 Stat Points (`unpack_sheet`: "no stats: sheets don't carry
them"). The pool therefore stores a spread inferred from nature and moves by `impute_sp`, and these
50,580 battles were played with those guesses rather than with what the two players actually ran.
Two real teams with identical sheets and different speed investments are one pairing here.

This is a limit on the experiment that no version of the experiment can remove — the truth is not in
the replay. It is a reason to treat the measured signal as a floor rather than a point estimate. It
is not a reason to read the verdict differently: §3's split-half reliability of 0.96 says the
imputed-spread matchup is being measured very precisely, and §1 says that precisely-measured quantity
is uncorrelated with who won. An attenuation argument would have to close a gap between AUC 0.512 and
something worth building on, starting from a predictor whose own noise is already accounted for.

It does, separately, sharpen what Phase 8 is for. The hidden Stat Points mean speed order and exact
damage are unknown to a player at *open* sheets, not only at closed ones, and nothing in the pipeline
infers either — see PLAN-v2 finding 8.

## 8. Re-run against the Phase 9 policy (PLAN-v4 step 3, 2026-10-04)

The standing check, against the policy that passed PLAN-policy's gates (the people reading, K 4,
heuristic team preview): `scripts/analysis/step3_validity.py`, result in
`data/analysis/reg_mc/sim_validity_ewp.json`. 1,500 human-corpus pairings × 8 battles, the policy on
both sides, 12,000 battles on four Kaggle sessions (about 9 hours each), no errors, merged after 12
battles replayed on the laptop to the same inputs. Scored by the same `validity.score` and `verdict`
as §1, on games that ended normally, cluster bootstrap by group.

| | games (groups) | AUC | log loss − constant | recalibrated − constant |
| --- | --- | --- | --- | --- |
| **Phase 9 policy** | 2,064 (1,207) | **0.514** [0.488, 0.541] | +0.232 [+0.195, +0.269] | −0.0009 [−0.0027, +0.0010] |
| heuristic, the same pairings | 2,019 (1,173) | 0.504 [0.479, 0.530] | +0.428 [+0.378, +0.476] | −0.0007 [−0.0025, +0.0010] |

**It fails, as the heuristic did.** Neither scale-free test clears its interval. The policy's win
rates are less lopsided (38% of pairings beyond 85/15 against the heuristic's 57% on these pairings,
sd 0.29 against 0.35), so the raw log loss is half as bad. But they order the human results no better
than the heuristic's, and no better than chance. The interval's top, 0.541, sits below the 0.55 this
run was sized to detect (PLAN-policy: 92% power at 1,500 series, though ended-normal games cover
1,207 groups). So this is a bounded null, not an underpowered one: any team-strength signal in this
policy's self-play is small.

The other subsets agree: all games 0.519 [0.498, 0.540], forfeits 0.529 [0.496, 0.563], held-out
0.462 [0.405, 0.521]. Rated games, 0.536 [0.502, 0.568], is the one interval just above 0.5. It is
one of five subsets, not the gated one, and is not read as a pass.

**What it means** (§6's fork, now taken twice): a policy that plays as long as people do (7.44 turns
a battle against 7.55), beats the heuristic 0.68, and picks a human's exact choice 11.5% of the time
does not make self-play predict who wins between two human teams. The deterministic team tools stay
the floor. Phase 10's matrix form and Phase 11's search over self-play stay blocked. §7's limits
still apply (imputed spreads, a ~1100 population, the heuristic's team preview), and the preview is
the one untested lever this run could change: the humans' own brings, for the same pairings.

## 9. The ceiling: a team-only predictor on the same test (PLAN-v5 step 1, 2026-10-04)

Both self-play runs were bounded nulls against the AUC of 0.55 they were sized to detect. That
counts against the policy only if something that sees only the two teams can reach it on these
games. The best such predictor available is the WP model's preview head, trained on this corpus
with both teams in view. `scripts/analysis/phase6_ceiling.py` scores it by this phase's own code,
on held-out games only (the `heldout_human` and `heldout_team` shards, ended normally), beside the
two self-play runs on the same games. Result: `data/analysis/reg_mc/phase6_ceiling.json`.

| predictor | games (series) | AUC | recalibrated − constant | passes |
| --- | --- | --- | --- | --- |
| **preview head**, `wp-v1f-idp5` | 2,518 (1,509) | **0.562** [0.537, 0.584] | **−0.0066** [−0.0115, −0.0013] | **yes** |
| preview head, `wp-v1f-ens5` | 2,518 (1,509) | 0.560 [0.537, 0.583] | −0.0067 [−0.0118, −0.0013] | yes |
| heuristic self-play | 1,794 (1,052) | 0.531 [0.503, 0.560] | −0.0014 [−0.0044, +0.0015] | no |
| Phase 9 policy self-play | 767 (445) | 0.531 [0.491, 0.571] | −0.0012 [−0.0063, +0.0039] | no |

Paired, on the games both cover (cluster bootstrap by series):

| | games (series) | AUC difference |
| --- | --- | --- |
| preview head − heuristic | 1,794 (1,052) | **+0.036** [+0.001, +0.071] |
| preview head − policy | 767 (445) | +0.020 [−0.028, +0.070] |
| policy − heuristic | 750 (432) | +0.009 [−0.036, +0.053] |

**The check can be passed on this corpus.** A team-only predictor clears both scale-free tests on
held-out games, at about the AUC the runs were sized for. So the self-play fails are fails, not
"this corpus cannot judge".

**The preview head beats heuristic self-play on the same games, just.** Against the policy, the
held-out overlap is 767 games, too few to separate them. On the whole run the policy's interval
topped out at 0.541 (§8), below the preview head's 0.562 here. Those are different games, so that
comparison is suggestive, not a result.

**The ceiling is low.** The best team-only predictor orders these games at 0.56. That is enough to
pass, but not enough to tell someone they are favoured. It agrees with the preview number being
worth 0.007 nats (PLAN-v5, "where things stand").

**Most of what is predictable about a series is not in its two sheets.** Game 2 of a Bo3 went to
game 1's winner in 57.7% [55.3, 60.1] of 1,628 series where both ended normally. If the two games
were independent draws at a fixed p for the series, that p would spread with an sd of about 0.20
around 0.5, and an oracle that knew it would score an AUC of about 0.72. Players adapt between
games, which pulls agreement down, so 0.72 is a rough floor on that oracle, not a measurement. It
covers the teams, the players and anything else fixed within a series. The teams' sheets alone reach
0.56.

**Self-play adds nothing to the preview head** (PLAN-v5 step 3b, on the battles already played).
The test is a logistic over the preview logit alone against one over both logits, fitted out of fold
by series on the held-out games, since the preview head trained on the rest. The per-game log loss
is compared by a cluster bootstrap.

| | games (series) | stacked − preview alone, log loss | self-play's weight (by fold) |
| --- | --- | --- | --- |
| heuristic | 1,794 (1,052) | +0.0004 [−0.0008, +0.0015] | −0.001 to 0.029 |
| Phase 9 policy | 767 (445) | −0.0000 [−0.0028, +0.0027] | 0.024 to 0.071 |

The heuristic's interval rules out a gain larger than 0.0008 nats. The policy's is three times
wider, on a third of the games, and centred on nothing.

**What this changes.** The check can be passed here, and the preview head passes it, so self-play
is not failing an impossible test. Neither policy's self-play carries team-strength information that
the preview head lacks. Step 3b stays the test for any later policy (3a), because it can pass on a
signal too weak to stand alone. And the team-level diagnostic (step 2a) has
to account for players. 1,471 teams play in two or more series, and 42% of games have both teams in
two or more, but a team is mostly one player's.

## 10. Why self-play misses: the diagnostics (PLAN-v5 step 2, 2026-10-04)

`scripts/analysis/phase6_diagnostics.py`, on the battles already played (the heuristic's 3,372
pairings × 15, the policy's 1,500 × 8), games that ended normally. Result:
`data/analysis/reg_mc/phase6_diagnostics.json`. Rollouts from human positions, the fourth check,
need building and are not here.

**(a) Pooling a team's battles does not help.** A Bradley–Terry strength per team, fitted on every
battle a team played in a run, predicts human games no better than the per-pairing win rate. That
holds on pairings the run never played, too:

| | games (series) | per pairing | per team (pooled) |
| --- | --- | --- | --- |
| heuristic | 4,591 (2,690) | 0.512 [0.496, 0.528] | 0.507 [0.490, 0.524] |
| policy | 2,064 (1,207) | 0.514 [0.488, 0.542] | 0.511 [0.486, 0.539] |
| policy, pairings it never played | 721 (430) | | 0.491 [0.446, 0.534] |

So the null is not per-pairing noise. As references, strengths fitted from human training series
and scored on held-out series give 0.549 [0.477, 0.621] per team (252 games) and 0.567
[0.510, 0.617] per player (417 games). A team's human record is mostly its player's: of the 1,471
teams in two or more series, 83% were played by one player only.

**(b) Self-play misjudges two archetypes.** These are logistic coefficients (logit units) on which
team has the archetype (A has it, minus B has it): for human results, by game with a cluster
bootstrap by series; for self-play, by pairing over its battles. The last column is humans minus
self-play, as z, with Bonferroni over the eight archetypes (|z| > 2.73). Mega is left out of the
table: 99.6% of teams carry one.

| archetype (share of teams) | humans | heuristic | policy | humans − heuristic, policy (z) |
| --- | --- | --- | --- | --- |
| **Trick Room** (37%) | **+0.17** [+0.06, +0.27] | −0.06 [−0.17, +0.04] | +0.00 [−0.13, +0.13] | **3.1**, 2.0 |
| **Fake Out** (71%) | **+0.15** [+0.03, +0.26] | −0.16 [−0.26, −0.06] | −0.09 [−0.25, +0.06] | **4.0**, 2.4 |
| Tailwind (57%) | −0.03 [−0.11, +0.06] | +0.10 [+0.02, +0.18] | +0.13 [+0.03, +0.23] | −2.1, −2.3 |
| weather (35%) | −0.00 [−0.10, +0.09] | −0.14 [−0.23, −0.05] | −0.19 [−0.33, −0.07] | 2.1, 2.4 |
| setup (60%) | −0.10 [−0.20, −0.01] | −0.02 [−0.11, +0.06] | −0.09 [−0.20, +0.01] | −1.2, −0.1 |
| redirection (30%) | −0.06 [−0.16, +0.06] | −0.07 [−0.18, +0.03] | −0.23 [−0.38, −0.08] | 0.2, 1.8 |
| Intimidate (62%) | +0.08 [−0.01, +0.19] | +0.10 [−0.00, +0.20] | +0.19 [+0.06, +0.32] | −0.2, −1.3 |

Humans win more with Trick Room and with Fake Out. Self-play rates both lower, and for the
heuristic the gap clears the correction. The policy leans the same way on fewer pairings. Both pay
off through turn order and over more than one turn. Trick Room reverses the order for the four turns
after the one it is set. Fake Out buys a partner a free turn. A one-turn search valued by a damage
race sees neither past the turn it plays in. Self-play also likes Tailwind and dislikes weather
more than humans do, short of the correction. One caveat: the human coefficients include who
pilots these teams, so part of Trick Room's +0.17 could be its players.

**(c) The players matter more than the teams, and self-play adds nothing beside them.** On rated
games (mostly Bo1 ladder, so each game is its own series), each player's pre-game rating is in the
log. The test is the rating difference alone against the rating difference plus the self-play
logit, cross-fitted by series:

| | games | rating alone, AUC | self-play adds (log loss) |
| --- | --- | --- | --- |
| heuristic | 1,513 | 0.598 [0.570, 0.625] | +0.0002 [−0.0016, +0.0021] |
| policy | 687 | 0.555 [0.511, 0.599] | −0.0015 [−0.0057, +0.0028] |

The ratings alone order these games better than anything team-only does (0.60, against the preview
head's 0.56 in §9).

**What it points to.** Pooling is closed: the signal is not hiding under per-pairing noise.
Stacking (§9) is closed for both existing policies. What is left is a bias with a mechanism. Self-play
undervalues the two archetypes whose payoff comes after the turn they are played. That is a target
for PLAN-v5 step 3a: look at how the policy plays Trick Room and Fake Out, and search two turns while
either is in play. Before a full Phase 6 run, a cheap check is enough: does the Trick Room and Fake
Out gap close in self-play on a sample of pairings?
