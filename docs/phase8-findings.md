# Phase 8, channel 1 — turn order as a bound on Speed Stat Points

_Measured 2026-09-20 on `data/selfplay/gen-heuristic-heuristic-s7-p3000x20` (25,000 battles,
62,581 opposing Pokémon) and on 2,479 cached human replays. Script:
[`scripts/analysis/speed_belief.py`](../scripts/analysis/speed_belief.py); result:
[`data/analysis/speed_belief_s7.json`](../data/analysis/speed_belief_s7.json)._

Finding 8 said the two reads every player makes constantly — *it outsped me, so it is invested*
and *that did 71%, so it is not* — are the two the stack throws away. This is the first of them,
built as arithmetic against the pinned dex rather than anything learned.

## What it does

At open sheets the opponent's nature, item and ability are on the sheet, so their Speed stat is a
known function of **one unknown integer in 0..32**. Every turn in which one of their Pokémon moved
before or after one of yours, at equal priority, is an inequality on that integer. The output is
the feasible set, not a point estimate.

A tie is never ruled out. Showdown breaks speed ties at random, so observing an order gives `>=`
and never `>`.

## Soundness, which is the whole claim

A bound that excludes the truth is worse than no bound. Measured on self-play, where the spread is
in the run's `input_log` and so is actually known:

| | count | rate |
| --- | --- | --- |
| Pokémon checked | 62,581 | |
| **silently wrong** (excluded the truth and did not know it) | 9 | **0.014%** |
| contradicted (proved itself wrong, widened back to the prior) | 22 | 0.035% |

Getting there took five fixes, and **every one of them produced a wrong inference rather than a
missing one** — the failure mode that matters, because a wrong bound can rule out the truth.

1. **Mega formes on the hidden side.** `MoveEvent.species` is the team-preview identity; the base
   stats have to come from the forme that was on the field. Mega Salamence is base 120 Speed where
   Salamence is 100. 9.7% → 3.9%.
2. **Mega formes on the known side.** The caller was passing a *stat*, which is only true of one
   forme. It now passes **Stat Points**, which is what you actually know about your own team, and
   both sides resolve the forme per event. 3.9% → 1.3%.
3. **Grassy Glide.** The dex export reports priority 0; the pinned build adds +1 under Grassy
   Terrain inside an `onModifyPriority` hook the export does not carry. Comparing it against a
   real 0 as though they raced is not a lost inference, it is a wrong one. 1.3% → 0.30%.
   `CONDITIONAL_PRIORITY` now lives in `vgc.regulation` and is shared with `vgc.meta.usage`.
4. **A Mega's ability is not the sheet's.** Mega Swampert has Swift Swim where Swampert had
   Torrent, and under rain that is a ×2. 0.30% → 0.10%.
5. **State that moved inside the turn.** Order is fixed at the turn mark, so a Weak Armor Pokémon
   at +2 by the time its own move line appears did not get there in time to reorder that turn.
   Both readings — turn-start and move-time — were tried against 3,000 battles and *each*
   contradicted cases the other did not, so neither is right alone and the pair is dropped.
   Weather counts as state: a Swift Swim Pokémon whose rain arrived mid-turn was not fast when the
   order was set. 0.10% → 0.05%, at a cost of 0.6 points of narrowing.

Plus one item class that no arithmetic can account for: **Quick Claw** moves first 20% of the time
whatever the stats say, which is how a 0-Speed Torkoal appeared to outrun a 32-Speed Mega
Salamence. It and Custap Berry are abstained on. Both are under 0.15% of sheets.

**The sample size hid the residue three times.** At 300 battles the rate read 0.00%; at 3,000 it
read 0.105%; at 25,000, 0.05% with a different composition. Gate 3 — *state the power before the
threshold* — applies to soundness gates as much as to accuracy ones, and a soundness claim from
300 battles would have been wrong twice over.

The nine that remain are concentrated in a handful of species and no single mechanic explains
them. They are recorded rather than argued away.

## The contradiction guard, and what it turned out to measure

An empty feasible set is not a discovery about the opponent — it is a proof that the model of that
battle is wrong, because the opponent did have *some* spread. The belief falls back to the prior
and sets `contradicted`. That splits one number into two, and pooling them would have hidden which
failure was shipping: 22 of the 31 failures state nothing false, and 9 do.

It then turned out to measure something else useful. On human replays the harness cannot know the
player's own spread, so it assumes one — and the contradiction rate tracks how wrong that
assumption is:

| assumed Speed SP for the known side | contradicted |
| --- | --- |
| 0 | 12.15% |
| 16 | 9.75% |
| 32 | 11.45% |
| *the true spread (self-play)* | **0.035%** |

So the flag is a detector for "the spread you told me about your own team is wrong", which is
worth surfacing in the app rather than swallowing.

## Power, which is a separate question

A bound that is always right and never narrows anything is sound and useless.

| | self-play | human replays |
| --- | --- | --- |
| racing pairs per game | 7.2 | 7.8 (median 7) |
| Pokémon narrowed at all | 31.3% | 28.0% |
| mean share of the 0..32 prior ruled out | 16.8% | 13.8% |
| pinned to ≤4 values | — | 4.5% |
| abstention rate over candidate pairs | 59.2% | — |

Most of the abstention is not a modelling gap: 39% of candidate pairs are dropped because the two
moves had different priority, which is simply not a race. 16% are an unmodelled mover and 4% are
state that moved mid-turn.

**The self-play power number is a floor, and a bad one.** The pool's spreads come from
`impute_sp`, so the truth takes two values — 32 Speed (58,788) and 0 (4,756) — and a channel that
only has to separate two well-spaced cases is being asked an easy question. A proper power
measurement needs self-play generated with randomized legal spreads, and that is the next thing to
run. The human numbers do not have this problem for *power* (the prior really is 0..32 there), but
they cannot be scored for soundness at all, which is the whole reason this phase exists.

The best single case in the human corpus: three pairs pinned a Sneasler to exactly 32 Speed SP,
ruling out 97% of the prior.

## Two things recorded rather than fixed

**`Mon.ability` still reports the sheet's ability after a Mega Evolution.** It is correct on the
`MoveEvent`, which is new, but `Mon` is serialized into `observation()`, which snapshots embed and
fingerprint at VERSION 3. Correcting it there invalidates 433,052 frozen training rows and every
manifest built on them — a regeneration that belongs with this phase's stated prerequisite, not
with this. Anything reading `ability` off a Mega-Evolved Pokémon's observation is reading the
pre-Mega ability.

**The evidence log is not in `observation()`** for the same reason. It is read from a live stream,
which is what the app and the CLI do. It has to move into the snapshot format before any of this
reaches a trained model, and that is step 3's problem.


---

# Phase 8, the spread prior — what their 66 points were doing before the battle spoke

_Measured 2026-09-20 on 2,500 cached human replays (19,255 racing pairs). Script:
[`scripts/analysis/spread_prior.py`](../scripts/analysis/spread_prior.py); result:
[`data/analysis/spread_prior.json`](../data/analysis/spread_prior.json)._

The speed channel needed a prior, and uniform-over-0..32 was the obvious placeholder. It is also
the obvious thing to be wrong about: spreads are not drawn, they are *chosen*, against goals. Two
kinds of structure follow, and both are computable from public information.

## The structure is real

**Structural zeros.** A Pokémon whose moves never scale off Attack gains nothing from Attack.
Across the 47,928 weighted Pokémon-sheets: **90.9% have exactly one offensive stat that no move of
theirs uses**, 0.1% have neither, 9.0% genuinely use both. Of the 90.9%, **95.1% have the nature
confirming it**. For nine Pokémon in ten, one of six dimensions is gone before a turn is played.
Read through `offensive_stat`, not the move's category, so Body Press counts towards Defence and
Foul Play — which scales off the *target's* Attack — does not rescue a dead Attack.

**Benchmarks.** Speed is bought to clear something. Against conventional builds — each top-30
threat at 0 and at 32 with its modal nature — the 33 investments collapse: an Adamant Rillaboom has
16 distinct outcomes, a Careful Incineroar 13, a Jolly Garchomp 13. A builder takes the cheapest
point in each class, because the rest is worth more elsewhere.

## Scoring a prior with no ground truth

A human's Stat Points are not recoverable from a replay. But a prior implies a distribution over
who moves first, and the corpus is full of observed turn orders:

    P(A moved first) = Σ_a Σ_b P(a) P(b) · [eff(a) > eff(b)] + ½·[eff(a) = eff(b)]

the half because Showdown breaks ties at random; under Trick Room the comparison inverts. Priors
are then compared by the likelihood they assign to what happened — on real games, with no truth
required. Two priors scored on the *same* pairs are a paired comparison, so the interval that has
to clear zero is the one on the **difference**; reading their separate intervals throws away the
shared between-game variance and reports overlap where there is a verdict. Clusters are replays.

| prior | all pairs | close pairs only |
| --- | --- | --- |
| `impute_sp` (a point mass) | +1.123 [1.039, 1.215] | +2.925 [2.689, 3.165] |
| structural (no free parameters) | +0.004 [0.003, 0.005] | +0.002 [−0.000, 0.003] |
| benchmark, pool tier | **−0.011** [−0.016, −0.006] | +0.012 [0.008, 0.017] |
| benchmark, usage tier | **−0.011** [−0.016, −0.007] | +0.010 [0.005, 0.015] |

_Nats per pair against a flat prior; positive means worse than assuming nothing. "Close" is the
subset where a flat prior gives the order between 5% and 95% — the pairs a prior's detail decides,
rather than the ones base stats already settled._

## Three findings

**1. `impute_sp` is catastrophic as a prior, and it generated the entire corpus.** A point mass
assigns near-zero probability to every turn order it did not predict, and it is **2.9 nats a pair
worse than assuming nothing**. This is the same degeneracy finding 8 caught in the training mix,
in a third place: every spread in the 60,000-battle self-play corpus came from this function, and
all 20,082 pool Pokémon have exactly 32 points in their offensive stat as a result. Nothing that
infers offensive investment can be gated on that corpus, because there is no variation to infer.

**2. The benchmark prior beats flat overall and loses on close pairs** — both intervals clearing
zero, in opposite directions. It is a better description of the population and a worse one of the
individual: the broad shape is right, and the specific concentration on class minima is wrong
exactly where the prior's detail decides the answer. A single pooled number would have reported the
win and hidden the loss, which is gate 1 arriving somewhere new.

So `speed_classes` is kept for what it is — a true statement about which investments are
distinguishable, and useful output for a team builder — and the **weighting** over those classes
stays opt-in and unvalidated. `flat_prior` remains what the belief layer uses, not for want of
trying to beat it.

**3. Counting allocations underrates the extremes.** A uniform distribution over legal spreads puts
0.7–1.3% on maxed Speed; real builds sit at 0 and 32 far more often than that. It is the one thing
every version of this agrees on, and it is why the benchmark tier carries an explicit `extremes`
weight at all.

## Tiers, because a regulation rotation must not take the belief offline

Benchmarks are circular — what is worth outrunning depends on what everyone runs, which depends on
what is worth outrunning. Solving it needs an independent ranking of what is good and a model of
how the meta moves toward it, which is a different and much harder project. This iterates once from
the conventional extremes and says so.

The circularity is also why the prior is tiered, and `benchmarks()` degrades on its own:

| tier | benchmarks from | needs |
| --- | --- | --- |
| `usage` | the measured meta (`vgc meta usage`) | a corpus for the regulation |
| `pool` | the legal species list at conventional spreads | the dex only |
| `structural` | none — structural zeros and allocation counting | nothing |

The `pool` tier costs almost nothing against `usage` here (−0.011 against −0.011 on all pairs,
+0.012 against +0.010 on close ones), which is the useful part: **a freshly rotated regulation with
no replays yet is not much worse off than one with 15,028 sheets.** Every result carries the tier
that produced it.
