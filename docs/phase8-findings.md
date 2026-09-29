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

**The self-play power number was a floor, and a bad one** — the pool's spreads come from
`impute_sp`, so the truth took two values, 32 Speed (58,788) and 0 (4,756), and a channel that
only has to separate two well-spaced cases is being asked an easy question. That has since been
replaced; see *Re-gating on a corpus that can stress it* below. The human numbers never had this
problem for *power* (the prior really is 0..32 there), but they cannot be scored for soundness at
all, which is the whole reason this phase exists.

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


---

# Re-gating on a corpus that can stress it

_Measured 2026-09-20 on `data/selfplay/gen-heuristic-heuristic-spreads-s21-p4000x5` — 20,000
battles whose spreads were drawn from `vgc.belief.prior.sample_spread` rather than `impute_sp`.
Result: [`data/analysis/speed_belief_spreads.json`](../data/analysis/speed_belief_spreads.json)._

Every gate above was scored against a corpus where Speed took two values and **offensive
investment took one**: all 20,082 pool Pokémon have exactly 32 points in their attacking stat,
because that is what `impute_sp` does. `vgc data generate --spreads sampled` replaces the spreads
and leaves everything else on the sheet alone.

The generator does not try to imitate the meta, and should not. A corpus exists here to measure
whether a channel can infer a hidden quantity, and the hardest honest test of that is a truth as
close to uniform as the rules allow — if the spreads matched the human prior, a channel could
score well by echoing the prior back rather than by reading the battle. So Speed and the live
offensive stat get flat marginals. What it keeps from reality is the part that is not a guess: a
stat no move of theirs uses gets nothing, which is how 90.9% of real sheets are built. The
consequence decides what these runs may be used for — **the teams are not realistic teams**, win
rates over them mean nothing, and they must not be manifested into WP training.

| | `impute_sp` corpus | sampled corpus |
| --- | --- | --- |
| distinct Speed SP in the truth | 2 | **33** |
| distinct offensive SP in the truth | **1** | **33** |
| Pokémon checked | 62,581 | 54,904 |
| **silently wrong** | 9 (0.014%) | **16 (0.029%)** |
| contradicted (detected, widened back) | 22 (0.035%) | 13 (0.024%) |
| narrowed at all | 31.3% | 35.3% |
| mean share of the prior ruled out | 16.8% | **12.9%** |
| racing pairs per battle | 7.2 | 9.2 |

**The degenerate corpus was flattering the gate in both directions, and the soundness number is
the one that matters: it doubles, to 0.029%.** The channel is still sound — 16 in 54,904 — but the
old figure was measured where it could not be stressed. Power moves the other way and for the same
reason: mean narrowing falls from 16.8% to 12.9%, because ruling out a broad middle looks
impressive when the truth only ever sits at 0 or 32. What rises is coverage — 35.3% of their
Pokémon get narrowed at all, against 31.3% — so the channel bites more often and less deeply than
the first measurement claimed.

## What was baked in, and where it actually was

The constant is a property of `impute_sp`, not of how people build. At higher level a spread is
chosen to hit a number — enough Speed to outrun a specific threat, enough Attack for a specific
KO, the rest into bulk — so maxing the attacking stat is one option among many rather than the
rule, and a corpus that assumes otherwise will keep flattering anything measured on it. Three
places assumed it; only one was where it would have been guessed.

- **The generator** — fixed, and the regression is guarded by a test that asserts a sampled corpus
  spans the full range in both offence and Speed.
- **The web app**, which nobody had looked at. `vgc.web.prior.compose` handed the WP model a
  Kingambit at 32 Attack / 32 Speed and labelled the whole set `share: 0.211`, presenting an
  imputed spread with a measured set's confidence. The 21% describes the item, ability, nature and
  moves — what the sheet actually shows. `share` is now scoped to those and the spread ships as its
  own `spread` field, so the UI cannot conflate them.
- **`prior.benchmarks()`**, which measured "what is worth outrunning" against opponents at 0 or 32
  Speed — the same maxed-or-nothing assumption in the stat this phase happened to be working on.
  Now a parameter, with the caveat recorded: it is roughly true of a corpus that is 58% unrated at
  a median rating of 1101, and it is exactly what should stop holding as the level rises.


---

# Filtering the corpus by who played, not by what the battle was rated

_Measured 2026-09-20 on 10,194 cached replays, 4,261 players._

The goal is modest and worth stating plainly: not a corpus of top players, which this ladder may
not contain, but one that is **not the bottom half of whoever is here**.

## Two things had to be fixed before that was possible

**The battle's rating is the wrong field.** 58% of cached battles carry no rating at all, so a
per-battle filter discards most of what any given player did. Skill belongs to the player: at a
1300 floor the cache held 211 battles *rated* 1300 and 2,039 battles *played by* someone who had
been there — an order of magnitude, for the same skill floor. One player's own replays included a
1412 and an unrated game on the same day.

**The maximum is a biased statistic, and it was the first one tried here.** A player's best
observed rating rises with how many of their games we happen to hold, because a maximum over more
draws is larger:

| cached rated games | players | mean *best* | mean *median* |
| --- | --- | --- | --- |
| 1 | 2,267 | 1125 | 1125 |
| 2–3 | 815 | 1162 | 1130 |
| 4–9 | 403 | 1205 | 1135 |
| 10+ | 134 | **1282** | 1166 |

Almost all of that first column is sample size. A filter built on it selects heavy uploaders and
calls them strong — so `player_skill` reports the **median** of the ratings a player's battles
carried, and a player with no rated game is `None` rather than a number, because unrated is not the
same as bad.

## The filter is a percentile, and it is applied per sheet

A percentile of the observed player population, not a rating: 1100 means one thing on this ladder
and something else on the next one, and "not the bottom half" is a statement about the population.
It also survives a regulation rotation, which a hardcoded number does not.

Applied **per sheet, by whoever brought it**. A sheet belongs to one player, so a strong player's
team still counts when their opponent is weak. A *battle* corpus is the opposite case and needs
both sides to qualify, because a trajectory is the product of both — that distinction is in the
docstrings so the next use does not quietly pick the wrong one.

    vgc meta players                          who is here, and the yield at each percentile
    vgc meta usage --skill-percentile 50      usage over the top half
    vgc meta pool  --skill-percentile 50      a team pool from the top half
    vgc meta scrape --players 50              fetch every replay of the top half, then snowball

| percentile | rating floor | players | their sheets |
| --- | --- | --- | --- |
| 0 | 1000 | 3,701 | 18,977 |
| 25 | 1050 | 2,801 | 16,068 |
| **50** | **1104** | **1,857** | **10,892** |
| 75 | 1187 | 927 | 5,044 |
| 90 | 1291 | 377 | 1,252 |

## What the top half actually plays

Cluster bootstrap over players, since one player's sheets are not independent:

| species | all | top half | delta | 95% CI |
| --- | --- | --- | --- | --- |
| Salamence | 32.7% | 29.4% | −3.3% | [−5.8%, −0.9%] |
| Golisopod | 11.2% | 9.1% | −2.1% | [−3.6%, −0.7%] |
| Charizard | 12.6% | 14.4% | +1.8% | [+0.2%, +3.8%] |
| Kingambit | 25.5% | 27.9% | +2.4% | [−0.2%, +4.8%] |
| Garchomp | 15.4% | 17.1% | +1.7% | [−0.0%, +3.8%] |

Three of five clear zero. **The shift is real and small** — a few points on a handful of species,
and the structural traits barely move at all (Trick Room 34.2% → 33.5%, Tailwind 58.6% → 58.0%,
redirection 43.1% → 45.0%). The top half is playing a recognizably similar game, which is what a
compressed ladder should look like.

## The ceiling, which bears on a deferral

Across 4,261 players the 90th percentile sits at 1291 and the highest single battle rating ever
observed is 1578. There is no 1800 tail here to filter down to. VGC-Bench's behaviour cloning
worked on 700,000 logs from genuinely high-rated players, and PLAN-v2 defers BC until "a
higher-rated corpus" exists; on this evidence that corpus may not be buildable for Reg M-C before
the 2026-12-02 rotation, and the deferral is better read as *waiting on a ladder* than as waiting
on scraping effort. What **is** available — and is now built — is the top half.


---

# Phase 8, channel 2 — damage magnitude as a bound on their offensive Stat Points

_Measured 2026-09-20 on 1,000 battles of the sampled-spread corpus (2,897 attackers, 11,658 usable
damage events). Script: [`scripts/analysis/damage_belief.py`](../scripts/analysis/damage_belief.py);
result: [`data/analysis/damage_belief_spreads.json`](../data/analysis/damage_belief_spreads.json)._

The other read: *that did 71% to my Incineroar, so it is not fully invested.* Same arithmetic as
`vgc team weakness`, run backwards — there "how many points would they need for the KO", here "how
many did they have, given what landed" — against the same pinned calc.

| | at first gating | after the bulk channel |
| --- | --- | --- |
| attackers checked | 2,897 | 2,866 |
| **silently wrong** | **19 (0.66%)** | **0** |
| contradicted (detected, widened back to the prior) | 272 (9.39%) | 56 (1.95%) |
| narrowed at all | 34.5% | 34.7% |
| mean share of the prior ruled out | 11.0% | 10.8% |
| — on hits the target survived | 20.8% | **22.1%** |
| — on hits that KO'd | 6.0% | 5.8% |
| observations per attacker | 2.0 | 2.0 |

**The second column was not produced by working on this channel.** Building `vgc.belief.bulk` — the
same arithmetic pointed the other way — turned up six faults this channel shared and that its own
gate had not isolated. It ends at zero silently wrong and a fifth of the contradictions, having
lost no power. The faults are listed under that channel; the lesson is about gating rather than
about damage. A 0.66% error rate looked like an acceptable diffuse residue and was six specific
bugs, and what found them was asking the same question from the other side.

**A KO is worth a third of a survived hit**, and that is the censoring working as intended: a kill
says the move did *at least* the remaining HP and nothing about how much more, so it cannot rule
out the top of the range. Reading a KO as an equality would have been the easiest way to look
powerful and be wrong.

It now reads zero silently wrong where it began at twenty times the speed channel's 0.029%. The
structural point survives the improvement and is the reason to expect more: damage needs the entire
calc reproduced — field, screens, boosts, items, abilities, formes, spread targets, HP precision —
and every one of those is a way to be quietly wrong. Eleven faults have now been found in it and
every one was in that list. Zero over 2,866 attackers is an upper bound of roughly 0.1% at this
sample size, not a proof of correctness.

## Five bugs, four of them the same shape

Something was handed to the calc, or read from the dex export, that was silently not what it looked
like. None of them raised an error; each just returned a plausible wrong number.

1. **Items and abilities as ids.** The Observer stores `blackglasses`; `@smogon/calc` wants
   `Black Glasses` and **ignores what it does not recognise** rather than failing — 90-106 against
   108-127 on the same calc. Every item and ability was quietly vanishing. 38% → 31% contradicted.
2. **Formes read from the wrong moment.** Both sides' forme was being looked up on the Observer's
   *final* state. Gengar is base 60 Defence and Mega Gengar is 80, so a Pokémon that Mega Evolved
   later in the battle made earlier observations look impossible. The event now carries both
   formes, and the attacker's item and ability, as of the moment. 31% → 14%.
3. **Weather and terrain as ids.** The same silent drop a third time: `terrain="grassyterrain"` is
   a neutral field, `"Grassy"` is the 1.3×. Now translated through an explicit table, and anything
   absent from it makes the event unusable instead of quietly becoming neutral. 14% → 11%.
4. **`multihit` is not in the dex export at all**, so `entry.get("multihit")` never fired and
   Population Bomb was being treated as a single hit. The lists now come from the pinned
   `data/moves.ts`, and `tests/test_belief_damage.py` re-derives them from `vendor/` so a Showdown
   bump fails a test instead of widening the error. The same pass also picked up the 53 moves with
   a `basePowerCallback` — power computed at run time from the attacker's remaining HP (Eruption),
   fainted allies (Last Respects), whether the target has moved (Avalanche) — which are abstained
   on wholesale.

## And one that was nearly mis-diagnosed as a mechanic

Electro Shot dominated what was left. The evidence said its damage behaved as though *unboosted*:
over 52 uncensored hits with the true spread known, the implied multiplier against an unboosted
calc was a median of 0.975. That contradicts the pinned `onTryMove`, which boosts Special Attack
and then attacks — in rain immediately, otherwise charging and firing next turn with the +1 still
up — and it contradicts the log, which plainly shows the boost line landing before the damage.

Both were right. **The calc applies that move's boost itself**: its Champions mechanics do
`if (move.named('Meteor Beam', 'Electro Shot'))`, so handing it the boost from the log counts it
twice. It is visible in one line — a +1 passed to Electro Shot moves the damage by 1.32×, where a
+1 passed to Flash Cannon moves it by 1.50×, because the calc is really going from +1 to +2. On an
ordinary move the calc matches Showdown's stat table exactly at every stage, which is now pinned by
a test.

The correction is to subtract what the calc adds, not to ignore the log, because the boost persists
into later turns and a second Electro Shot at a logged +2 has to arrive as +1. With that,
**50 of 52 uncensored Electro Shot hits contain the truth, against 1 of 52 before.**

It barely moves the aggregate — those are 52 events out of 11,658 — and it is the most useful thing
in this section anyway. The first instinct was to abstain on charge moves as a class, which "fixed"
the contradictions by discarding the evidence. The measurement that looked like a mechanical
discovery was a bug in the bridge, and the way to tell the difference was to read the pinned source
rather than trust the aggregate.

## What is still unexplained

1.95% of attackers still contradict themselves, and the guard widens those back to the prior, so
they state nothing false — they cost power. The residue is diffuse: no single species, move,
ability or item accounts for more than a few percent of it. It is recorded rather than argued away,
and the last six fixes came from building a different channel rather than from staring at this one,
which is the reason to expect the remainder to go the same way.

---

# Phase 8, channel 3 — your damage, read backwards to their bulk

_Measured 2026-09-20 on 1,000 battles of the sampled-spread corpus (3,373 Pokémon, 5,819 usable
events). Script: [`scripts/analysis/bulk_belief.py`](../scripts/analysis/bulk_belief.py); result:
[`data/analysis/bulk_belief_spreads.json`](../data/analysis/bulk_belief_spreads.json)._

The first channel that reads your own moves. A move whose every term you know landed for a measured
fraction of their health, and the only unknowns left are their HP investment and their investment
in the stat that resisted it.

| | value |
| --- | --- |
| Pokémon checked | 3,373 |
| **silently wrong** | **6 (0.178%)** |
| contradicted (detected, widened back) | 34 (1.01%) |
| **share of the 33×33 grid ruled out** | **27.8%** |
| — share of the HP axis alone | 3.1% |
| — share of the Defence axis alone | 7.1% |
| — share of the Special Defence axis alone | 9.7% |
| observations per Pokémon | 1.7 |
| of those, censored (a KO or a sliver survivor) | 49.5% |
| hit both physically and specially | 21.6% |

## One equation, two unknowns — and the interesting part is which one it answers

The fraction of health a hit takes is `damage(defence) / hp`, and damage falls roughly as
1/defence, so the fraction goes roughly as `1 / (hp × defence)`. The indistinguishable direction is
therefore a hyperbola: more HP with less Defence looks exactly like less HP with more Defence.
Moving *both* up or both down does change what you see.

So a single observation does not locate the split and does locate the product — which is the
quantity a player means by "physically bulky" in the first place. **The three axis rows above are
the whole argument for keeping the region.** A report of per-stat ranges would have shown 3.1% of
the HP axis gone and called that the result; the region itself rules out 27.8% of the grid, nearly
nine times as much, and it is the corners — very frail and very bulky — that it removes.

`vgc.belief.sp.Block` carries several stats per block for exactly this reason, and the honest way
to separate HP from a defensive stat is not a convention but a second observation of a different
kind: a physical hit and a special hit share their HP term. That happened for 21.6% of Pokémon here.

**It does not assume HP is bought first.** That convention is real — and it is a tendency, not a
rule, since spreads are built defence-first or to an exact HP/Defence pair chosen to survive one
named move. So it may weight a prior and must not prune the region.

## Half of what it sees is a lower bound

49.5% of usable observations are censored: the target either fainted or was left on a sliver of HP.
That is the single largest limit on the channel's power and it is intrinsic — you are reading your
own attacks, and attacks that work are the ones that end the exchange.

The sliver case was a real error before it was handled. Focus Sash, Sturdy and Endure all floor a
lethal hit at 1 HP and the `|-damage|` line looks identical either way, so a move that would have
done far more reads as having done exactly the health that was left. **16 of the channel's first 19
misses were survivors at ≤2% HP.** Censoring them is sound whether or not a sash was the reason,
and it cost almost nothing, because a hit that nearly kills is near the top of the range anyway.

## Six faults, and all of them were shared

Every one was a case of the calc being told something the battle never said, and every one also
affected `vgc.belief.damage`, which is what took that channel from 19 silently wrong to 0.

1. **A spread move that hit one target.** Showdown applies the 0.75 only when more than one
   Pokémon was actually hit; `@smogon/calc` derives it from `field.gameType` and the move's target
   with no per-move override. A Heat Wave that caught one Pokémon read 0.75× in the calc and 1.0×
   in the battle. `gameType` is overloaded — it also sets screen strength, 1/3 in doubles against
   1/2 in singles — so where the two disagree the event is dropped rather than made wrong in one
   term or the other.
2. **A burned attacker.** Both sweeps hardcoded `"status": ""`, so every attacker was described to
   the calc as healthy and every burned physical move read at twice its real output.
3. **Sliver survivors**, above.
4. **A Mega's ability, on your own side.** Mega Golisopod has Tough Claws where Golisopod has
   Emergency Exit: 1.3× on every contact move, straight out of the region. Your team file cannot
   know this and the Observer already did the work, so forme, item and ability come from the event.
   This is the fourth distinct appearance of the Mega-ability fault in this phase.
5. **Weather Ball.** Its *type and base power* are decided at run time — Water at 100 in rain,
   Normal at 50 outside — and the export carries only the unconditional pair. Under Drizzle that
   understates by 2× before type effectiveness applies: 11 of the first 39 misses. `RUNTIME_TYPE`
   is derived from `onModifyType` in the pinned build and re-derived in a test.
6. **The target's item, read from the end of the battle.** `obs.sides` holds the final state, so a
   Pokémon whose item was knocked off reads as never having held one — and Knock Off is 1.5×
   exactly when there was something to remove. Item and ability now come from the event.

A seventh omission turned up in the same sweep and was not a miss, only a latent one:
`overrideDefensiveStat` is absent from the dex export, so Psyshock — Special, and checked against
*Defence* — would have been measured against the wrong stat. That is the third export field found
missing after `multihit` and `overrideOffensiveStat`.

## What is left

0.178% of Pokémon still have the truth outside the region. At event level the residue is 0.98% and
no single move, ability or item accounts for more than a few of them. One hypothesis was checked
and ruled out: `@smogon/calc` does **not** auto-evolve a Mega Stone holder, so passing a base forme
with its stone is inert rather than silently wrong.

---

# Phase 8, step 1 — three channels joined by the budget

_Measured 2026-09-20 on 1,000 battles of the sampled-spread corpus (3,783 opposing Pokémon).
Script: [`scripts/analysis/sp_belief.py`](../scripts/analysis/sp_belief.py); result:
[`data/analysis/sp_belief_spreads.json`](../data/analysis/sp_belief_spreads.json)._

Reported side by side, the channels are separate facts about the same Pokémon that never speak to
each other. They are not independent: a spread is **one allocation of 66 points over six stats, at
most 32 each**, so every point one channel proves they bought is a point another's stat cannot
also have.

| | rules only | + dead stat | **shipped** |
| --- | --- | --- | --- |
| `dead_zero` / `spend_all` | off / off | on / off | **on / on** |
| Pokémon checked | 3,783 | 3,783 | 3,783 |
| **silently wrong** | **6 (0.159%)** | 6 (0.159%) | **6 (0.159%)** |
| contradicted by the budget | 0 | 0 | 0 |
| mean share of allocations ruled out | 36.3% | 35.6% | **35.4%** |
| narrowed at all | 77.7% | 77.7% | **77.7%** |
| **bulk bounded** | 60.4% | 60.4% | **65.5%** |

Both build conventions are **confirmed against play rather than measured here** — every point is
spent, and a stat no move scales off gets nothing — and both stay parameters, because the corpus
cannot referee either: `prior.sample_spread` satisfies both by construction. They are worth 233× of
the allocation space between them, more than all three evidence channels manage, so what they rest
on is worth stating plainly.

## The joint adds no unsoundness of its own

All three columns are identical to the unit, and that is provable rather than lucky. Under
`rules only` the only constraints are the channels' own sets and `sum ≤ 66`, which a real spread
always satisfies, so every one of the 6 violations belongs to a channel and the budget propagation
contributes none.

## Adding the third channel roughly doubled it

The same gate before `vgc.belief.bulk` existed, on 2,000 battles:

| | two channels | three |
| --- | --- | --- |
| silently wrong | 0.623% | **0.159%** |
| allocations ruled out | 19.9% | **35.4%** |
| narrowed at all | 50.7% | **77.7%** |
| bulk bounded | 28.1% | **65.5%** |

Soundness improved four-fold at the same time as power nearly doubled, which is not the usual
trade: six of the faults the bulk channel exposed were shared with the damage channel, so building
it made the existing evidence more trustworthy as well as adding new evidence.

## What the budget buys, and what it cannot

**It never narrows the two stats a channel observed directly.** The cap is 32 and the budget is 66,
so Speed and one offensive stat can both be maxed (64 ≤ 66) and no pair of observations on those
two can rule out a value of either. That is also why `contradicted by the budget` is 0: the three
bulk stats hold 96 points between them, so nothing the other channels say can conflict. The bulk
channel is what makes the guard reachable — it bounds bulk from *below* — and the guard did not
fire here, which is worth watching rather than concluding from.

**Everything else the budget adds lands on bulk**, and that is now the smaller half of the story:
65.5% of Pokémon have their bulk bounded, against 28.1% before, because most of it is read directly
off your own damage rather than inferred from what is left over.

## Calibration measures the prior's shape, not the channel

The plan asks that the truth land in the belief's 80% credible set about 80% of the time.

| | hp | atk | def | spa | spd | spe |
| --- | --- | --- | --- | --- | --- | --- |
| weighted by allocations (shipped) | 0.855 | 0.811 | 0.836 | 0.902 | 0.846 | **0.751** |
| weighted flat over the feasible set | 0.930 | 0.884 | 0.922 | 0.934 | 0.911 | 0.827 |

The second row answers the question this phase can answer — **is the feasible set the right size?**
At 0.83–0.93 it is, slightly conservatively. The first is a different question and unanswerable on
this corpus: `prior.sample_spread` draws Speed and the live offensive stat from a *flat* marginal
on purpose, so that a channel cannot score well by echoing the prior back. A belief weighted by
allocations is decreasing, not flat, so Speed at 0.751 is scoring the generator's shape against the
prior's. The tell is that turning on the spent-budget assumption, which moves the prior toward the
generator, lifts all six without any evidence being added (Speed 0.600 → 0.751).

The real question underneath is not an artefact. `vgc.belief.prior` measured a flat Speed marginal
as the best of the priors it scored on 19,255 **real** turn orders, and a joint distribution over a
budget cannot have flat marginals on all six stats. The validated marginal and the self-consistent
joint disagree, and the measured size of that disagreement on real data is 0.004 nats a pair —
small, and recorded rather than tuned away.

## What it produces

`SPBelief` carries the feasible region, exact per-stat marginals, the range still possible for any
subset of stats, and K particles drawn from one dynamic program rather than by rejection — so a
tight belief costs no more to sample than a wide one, which is what Phase 9's determinization needs
of it. Blocks rather than six independent stats because a bulk observation constrains HP and a
defensive stat *jointly*, and the projections are worth about a ninth of the region.

---

# Phase 8, a fourth channel found by designing the UI — switch-in order

_Measured 2026-09-21 on 3,000 battles of the sampled-spread corpus._

Designing entry for a cartridge turned up an evidence source none of the three channels reads.
**Abilities that announce themselves on switch-in fire in Speed order**, so a turn where two of
them go off is a Speed comparison — and it happens on turn 1, before a single move has been used,
which is precisely when the belief is widest and advice is least informed.

| | pairs | in Speed order |
| --- | --- | --- |
| consecutive `-ability` lines, naive | 437 | 91.3% |
| **switch-in announcements only** | **453** | **100.00%** |

The gap between the two rows is the finding. Reading every `-ability` line as a race is wrong 8.7%
of the time, and the counterexamples are all the same shape: Incineroar at 86 Speed announcing
before Kingambit at 97. That is not a race — it is **Intimidate, then Defiant answering it**. A
trigger and its response are adjacent in the log and causally ordered, so comparing them measures
the causality and calls it Speed.

Filtered to abilities that actually fire on arrival — `onStart` in the pinned build, the same
derivation the entry rules use — it is 453 of 453 with no counterexample.

**Trick Room inverts it**, confirmed from play as well as from `Pokemon.getActionSpeed()`, which
subtracts Speed from 10000 under it. Worth separating from the rest: this corpus contains **zero**
Trick Room pairs, so unlike everything else in this document it rests on the source and on the
user, not on a measurement. Ability priority tiers (`onSwitchInPriority`) are the remaining
unknown; none appeared, which is not the same as none existing.

## Folded into the belief, and what it is worth

| over 2,500 battles | moves only | + switch-in order |
| --- | --- | --- |
| Pokémon with any constraint | 6,826 | 6,884 |
| **silently wrong** | **0** | **0** |
| share of the prior ruled out | 12.96% | 13.14% |
| narrowed at all | 35.25% | 35.52% |
| constraints per Pokémon | 2.317 | 2.343 |

**The aggregate gain is about 1% and that is not the case for it.** 322 of the 371 pairs land on
**turn 0**, and in **12.0% of battles an ability pair is the first Speed evidence of any kind** —
it arrives before a move has resolved. That is the turn where the belief is widest, where the lead
decision is already made, and where every other channel has nothing to say. A channel worth 1% of
the total that fires when nothing else has fired is not the same as a channel worth 1%.

Six abilities are excluded for having a second trigger — Forecast and Mimicry react to weather and
terrain, Ice Face and Flash Fire to being hit, Shields Down to its own HP — since each announces
the same line from something that is not an arrival.

---

# The regime every gate was measured in was not the regime the app runs in

Building the entry adapter surfaced the most serious fault found in this phase, and it was
invisible from inside the corpus. The turn-order channel's soundness result — **0 silently wrong
over 6,000 battles** — was measured on self-play replays, which are Open Team Sheets games. A
`|showteam|` line prints the nature, so `Mon.nature` is always populated and `speed_stat` has one
answer for a given investment.

On a cartridge at Team Preview Only nothing is printed. `speed_stat` read the missing nature as
`Serious`, and a neutral reading of an unknown nature is not a default — it is an assumption, and
it can exclude the truth. A Pokémon whose investment puts it at 85 at neutral is anywhere from 76
to 93, so a Timid opponent really does outrun something the neutral reading says it cannot.

## 4.3% wrong, in the only regime the app has

Scored over the same 2,500 battles and the same 6,884 opposing Pokémon, with the opponent's
natures blanked to match what the app actually sees:

| Team Preview Only | unknown nature read as neutral | unknown nature spans the natures |
| --- | --- | --- |
| **silently wrong** | **296 (4.30%)** | **0 (0.00%)** |
| contradicted | 81 (1.18%) | 0 |
| share of the prior ruled out | 14.23% | 4.81% |
| narrowed at all | 37.64% | 17.90% |
| constraints used | 16,130 | 16,130 |

The same number of observations is read either way. What changes is what each one is allowed to
conclude: the order test now asks whether **some** nature permits what was seen, comparing the
first mover's best case against the second's worst. Where the nature is known — your own side
always, an opponent's under Open Team Sheets — the band is a single point and the comparison is
the plain inequality it replaced. Re-running the Open Team Sheets gate confirms this: the output
is **byte-identical** over 2,500 battles.

So the cost is real and it is paid where the belief was already weakest — 14.23% of the prior
ruled out becomes 4.81%, and a third of Pokémon narrowed becomes a sixth. That is the trade this
package makes everywhere, and 4.3% of beliefs excluding the truth is not a price worth paying for
three times the narrowing.

## What it says about the other channels

The transferable point is not about Speed. **A soundness number is a property of a channel *and*
a regime, and this document reported it as a property of a channel.** Every gate here ran on
self-play, every self-play game is Open Team Sheets, and Team Preview Only differs in more than
the nature: the item and the ability are hidden too, and both feed the damage and bulk channels
directly. Those channels were not re-gated in this regime and are not yet known to be sound in
it. `vgc.web.live` therefore runs only the turn-order channel live, which is defensible on cost
grounds and is now also the only one whose Team Preview Only behaviour has been measured.

---

# Step 2 — the set prior, which is a ranking and not a bound

Every other channel in `vgc.belief` produces a **bound**: a feasible set the truth is never allowed
to leave, bought by abstaining whenever the evidence does not settle something. `vgc.belief.sets`
is a different kind of object and is gated on different terms.

| | `speed` / `damage` / `bulk` / `sp` | `sets` |
| --- | --- | --- |
| what it says | what is *possible* | what is *likely* |
| where it comes from | this battle | 21,376 other people's sheets |
| when it is wrong | a bug, measured as `silently_wrong` | a tap |

The contract that keeps them apart: **usage may never make anything impossible.** Only a sound
observation can — seeing the item, watching the ability fire, an ability that would have announced
and did not. Every legal option keeps a non-zero share however rare it is, because somebody is
always running the thing nobody runs.

## Held out by team: the first option is right 94.3% of the time

Held out by **team**, using the frozen 15% in `data/splits/reg_mc.json` — the right unit, because
one player's team appears in every game they played, and the question is whether the prior
generalises to somebody else's team rather than whether it can recite its own corpus. 18,094
training sheets, 3,282 held out, 19,677 opposing Pokémon scored.

| | likeliest first | alphabetical | 
| --- | --- | --- |
| **ability, top-1** | **94.3%** | 59.1% |
| item, top-1 | 68.1% | 8.1% |
| whole set, top-1 | 12.6% | — |
| **truth in the support** | **1.000** | — |

Alphabetical is the honest baseline because it is what the pop-up did before, and what the person
was actually tapping through — Incineroar's menu led with Blaze, and 99.7% of Incineroar are
Intimidate. `truth in the support` at 1.000 is the number that would be a bug rather than a
disappointment: the floor is doing its job and nothing legal is ever reported as impossible.

**The whole set is right 12.6% of the time**, and that is the finding that matters most. It is not
a weakness of the prior — Incineroar has 268 distinct sets in the corpus and its most common one
is 17.7% of them. It means a point estimate over the mode is the wrong *object*: it answers a
question about a set five opponents in six are not holding.

## Averaging over draws beats guessing one set, on every model tried

So win probability became an average over `k` complete opponents drawn from the belief rather than
one guess. Scored on 400 held-out human OTS replays and 4,075 positions, against what actually
happened, with the set prior counted from training teams only. Four arms: `true` is the model
given the real sheets, `mode` fills each species with its single most common set, `particles`
averages over 24 draws, `open` hands over the masked position unfilled.

| log-loss | true | mode | particles | open |
| --- | --- | --- | --- | --- |
| `wp-v1-sw-split-small` *(served)* | 0.5900 | 0.5910 | **0.5860** | 0.5830 |
| `wp-v1-set-full` | 0.5826 | 0.5819 | **0.5799** | 0.5846 |
| `wp-v1-gbt` | 0.5661 | 0.5661 | 0.5661 | 0.5661 |

**`particles` beats `mode` on every model tested**, which is the claim the change rests on and the
only one that is stable across them. It is a small win — 0.005 of log-loss on the served model —
and it is a win in the right direction for the right reason.

## Three things this gate says that were not the question

**`open` and `particles` swap places between models.** Leaving the opponent unknown beats averaging
on the served model and loses to it on `wp-v1-set-full`, so neither dominates and the app reports
both. It is not a shrinkage artefact: shrinking `true` toward 0.5 until it is exactly as confident
as the particle arm leaves it at 0.5891, still behind both.

**`wp-v1-gbt` is completely insensitive to the opponent's sets.** All four arms agree to four
decimals — masking their entire sheet changes nothing it predicts — and it still scores better
than either set model here. Worth knowing before any more weight is put on set features.

**Filling the opponent's spread made things worse, and the first version of this gate hid it.**
An opponent's `stats` is `None` in *every* training row: a player row carries your own and nobody
else's, a spectator row carries none at all. The elegant design was for a particle to carry a
spread drawn from `vgc.belief.sp`, so that both halves of Phase 8 met in one object — but setting
that field flips a feature no model has seen set, and both the gate's filled arms and the app were
doing it. Removing it is what turned the gate's first reading around. The SP belief earns its keep
on screen, in the Speed read and the belief panel; it is kept out of the model that cannot use it.

---

# Phase 8, the prerequisite — the training mix, and what it actually cost

_Measured 2026-09-21. The manifest is `wp-v1c-train` (43,359 battles, 1,323,270 snapshots); the
dataset is `wp-v1c`. Scripts: [`training_mix.py`](../scripts/analysis/training_mix.py),
[`partial_information.py`](../scripts/analysis/partial_information.py); results in
[`data/analysis/`](../data/analysis/)._

Finding 8 said the model had never seen an unknown opponent, and PLAN-v2 made that the thing
blocking the phase rather than a caveat on it. The fix was supposed to be one line of manifest.
It was, and the manifest was the smallest part of what the measurement found.

## The known-flags reproduce, and the revealed-flags are worse

`training_mix.py` exists because finding 8's central number was counted by hand once and then
decided the shape of a phase. On the old manifest it reproduces exactly — the opponent's item,
ability and moves are known on **1.000** of in-battle player rows, in both shards.

It also reports a column the hand-count did not have. Counting only what the *battle* revealed —
`item_source` is not `"sheet"` — the rates are **0.0003 for items and 0.000 for abilities**. The
model has not merely never seen an unknown opponent; it has never seen an item or an ability
*become* known. When a sheet is open the observer never has to attribute a reveal, so the entire
mechanism by which a closed-sheet game delivers information is absent from the corpus.

None of this was ever a representation problem. `Featurizer._lookup` has had an `UNK` token from
the start and `_mon_num` carries `float(m["item"] is not None)` as an explicit known-flag. The
slots were built and never filled.

## The Makefile was the whole cause

`$(HUMAN)` named `$(FORMAT)bo3/train.jsonl.gz` alone, while the recipe that produces it is
`vgc data human --format both`. So the closed-sheet shard was generated on every run, written to
disk, and never passed to `vgc data manifest`. 654 battles and 19,238 snapshots at snapshot
VERSION 3, sitting beside the shard that was being trained on, for the want of a second line.

## The other regime had no held-out set either, which is why nobody noticed

Worth separating from the training half, because it is the more embarrassing one: there was no
way to *score* a model at Team Preview Only. `EVAL_SETS` named only the Bo3 shards, so the
closed-sheet held-out data had never been featurized and no gate had ever been computed on it.
A model could fail completely in the regime the app runs in and every number on its card would
still be green.

`eval_human_closed` is now that set: **5,266 rows over 131 battles**, with `closed_in_battle_*`
gates grouped as `closed_sheet_pass`. The n matters and is carried on every verdict — PLAN-v2's
"779 battles" is the whole closed-sheet shard, and the held-out-by-replay-group slice of it is
131 battles.

## Adding the shard helped, and not for the stated reason

Three models on identical closed-sheet rows. `wp-v1c-gbt` is the same recipe as `wp-v1-gbt` with
the shard added and nothing else changed; the OTS eval arrays are byte-identical between the two
datasets, verified by sha256, so the OTS column is a genuine like-for-like.

| | OTS spectator | closed spectator | closed ECE | t7+ beats 50% |
| --- | --- | --- | --- | --- |
| `wp-v1-gbt` | 0.54428 | 0.60275 | 0.0463 | no, `[-0.262, +0.174]` |
| `wp-v1c-gbt` | 0.5439 | **0.58517** | **0.0272** | no, `[-0.263, +0.038]` |
| `wp-v1-sw-split-small` *(served)* | 0.5648 | 0.5873 | 0.0277 | **yes**, `[-0.276, -0.013]` |

0.018 nats and half the calibration error in the closed regime, for nothing on OTS. **All three
still fail `closed_sheet_pass`, and all three fail it on calibration rather than discrimination** —
worst-bucket ECE 0.099 to 0.124 against a 0.03 threshold, with respectable log losses underneath.

The gain is real and the attribution in PLAN-v2 was wrong. `wp-v1c-gbt` cannot have learned what
an unknown means, because it does not read the fields that go unknown — see below. What the shard
gave it is the ladder Bo1 *population*: different players, ratings and forfeit behaviour from the
Bo3 shard. Worth having, and not what finding 8 predicted.

## The partial-information penalty is real, non-monotone, and invisible on the wrong model

The first explanation offered for the late-game closed-sheet failure was that the model goes
off-distribution on partial information. That has a confound the gate cannot rule out:
`human_closed` is the ladder shard and `human_ots_all` is the Bo3 shard, so the difference could
be the population. `partial_information.py` asks the question on **one corpus** — held-out OTS
games masked against themselves, same players, same labels, only what is known varies. Hiding is
per Pokémon and takes item, ability, sheet moves and nature together, because that is how a real
game reveals; `moves_used` is never touched.

On `wp-v1c-gbt` the answer is nothing at all. Hiding the opponent's entire team moves log loss
0.5752 → 0.5759 and ECE 0.0112 → 0.0134, and every turn bucket is flat. The mask was verified to
bite: 50% of `item` and `ability` feature cells change, ~47% of move cells, species untouched.
This is PLAN-v2's existing note about `wp-v1-gbt` reproduced on a retrained model and a different
corpus — **the GBT is inert to the opponent's sets** — and it means the hypothesis could never
have been tested there. A null on a model that does not read the inputs is not evidence.

On `wp-v1-sw-split-small`, which does read them, the curve is the finding:

| hidden | all log loss | all ECE | t5-6 ECE | t7+ ECE |
| --- | --- | --- | --- | --- |
| 0.00 *(open sheets)* | 0.5978 | 0.0316 | 0.029 | 0.039 |
| **0.25** | **0.6189** | **0.0529** | 0.076 | **0.110** |
| 0.50 | 0.6151 | 0.0481 | 0.077 | 0.115 |
| 0.75 | 0.6097 | 0.0441 | 0.055 | 0.096 |
| 0.90 | 0.6078 | 0.0427 | 0.040 | 0.082 |
| 1.00 *(all hidden)* | 0.6079 | 0.0467 | 0.038 | 0.072 |
| `closed_curve` | 0.6121 | 0.0462 | 0.056 | **0.104** |

**Hiding a quarter of the opponent costs more than hiding all of it.** Full knowledge is best,
full ignorance second, partial knowledge worst — and the ordering is not what "never seen an
unknown" naively predicts, which is monotone degradation. It is why the failure concentrates late
in a closed-sheet game: that is where partial knowledge lives.

`closed_curve` masks at the rate a real game reveals at, per turn bucket, from
`training_mix.py --by-turn`. It lands at t7+ ECE **0.104** against **0.124** observed on the
genuine closed-sheet shard. Reproducing the magnitude of the real failure from synthetic masking
on a different corpus is the evidence that this is the mechanism and not a coincidence.

## Which gives WP_v2 the argument its own gate could not make

Step 3 measured that `particles` beats `mode` by 0.005 of log loss and could not say why, and
that `open` beat both on the served model, which looked like a contradiction. The curve resolves
it. A real closed-sheet position sits in the 0.25–0.50 hole. Filling it with complete draws moves
it to `hidden_0`; masking it outright moves it to `hidden_1`. **Both escape the hole, and filling
escapes it further** — so both beating the naive arm is the expected result rather than a puzzle,
and averaging is preferred for a reason stronger than the 0.005 it was resting on.

## Two things the step-3 gate's masking gets wrong

`wp_closed_sheet.py`'s `mask()` blanks an OTS replay for the whole battle, and a real Team Preview
Only game is not like that. Measured per turn bucket, with `--mask` applying the gate's own
masking to the OTS shard for comparison:

| bucket | real closed: item / ability / moves | gate's mask: item / ability / moves |
| --- | --- | --- |
| t1-2 | 0.079 / 0.106 / 0.040 | 0.000 / 0.000 / 0.037 |
| t3-4 | 0.223 / 0.208 / 0.159 | 0.000 / 0.000 / 0.156 |
| t5-6 | 0.294 / 0.249 / 0.236 | 0.000 / 0.000 / 0.232 |
| t7+ | 0.392 / 0.305 / 0.356 | 0.000 / 0.000 / 0.316 |

Moves track closely — `mask()` clears `moves` but leaves `moves_used`, and `_known_moves` reads
the union — so that channel is right. Item and ability are pinned at zero for the whole game
against a real 0.392 and 0.305 by t7+. The gate's `open` arm is not a Team Preview Only position;
it is the turn-0 information state held fixed to the end, which is `hidden_1` on the curve above
and therefore *out* of the hole a real position is in.

Two consequences. PLAN-v2's criterion that v2 "moves toward the oracle as information arrives"
cannot be tested by that gate at all, because in the masked arm no item or ability information
ever arrives. And the belief is only ever exercised at its widest, never at the point where
`vgc.belief.sets` has 40% of the items to condition on and should be sharpest.

The fix is not a cleverer synthetic mask — reveal timing is not recoverable from an OTS replay,
since the observer files everything as `"sheet"` and never attributes a reveal (0.000 above). The
two measurements answer different questions and both are needed: only an OTS replay carries the
truth, so only it can compare `particles` against `mode`; only the genuine closed-sheet shard can
ask whether the model is calibrated in the regime, and it needs no truth to do so.

## The eval sets were a glob, and something walked into them

Not a modelling finding, but it was found the same way and it invalidates numbers. `EVAL_SETS`
globbed `selfplay/*`, so the two belief-gating runs generated on 2026-09-20 — after `wp-v1` was
built — entered `eval_selfplay_*` and took it from 395,774 to 460,296 rows under an unchanged
name. The training manifest could never have taken them: `--spreads sampled` prints "must not be
manifested" and a manifest names its files one at a time. No such guard existed on the evaluation
side.

The contamination is the serious half rather than the comparability. A `-spreads` run has every
spread redrawn from `vgc.belief.prior` and is deliberately *not* the meta, so win probability was
being scored against a distribution built to stress the belief layer. Tag-excluded now
(`SELFPLAY_EVAL_EXCLUDE`), which also stops a `-closed` self-play run from being pooled with
open-sheet self-play under one name — gate rule 7, and step 4 would have hit it immediately. The
durable fix is an eval manifest that names its shards the way the training manifest does.

Nothing gated was affected: the gates read `human_ots_all` and `human_closed`, whose arrays are
byte-identical or new. `eval_selfplay_*` on `wp-v1c` should not be read.

## After the retrain: the hole closed, and the honest unknown now wins

_2026-09-27. `wp-v1c-sw-split-small` is the served recipe trained on `wp-v1c`; torch 2.2.2 on this
machine, where the served model was trained on 2.10.0 — a second difference, recorded rather than
controlled. Results in `data/analysis/partial_information_wp-v1c-sw-split-small.json` and
`data/analysis/wp_closed_sheet_*.json`._

The same curve on the retrained model:

| hidden | log loss, served | log loss, retrained | t7+ ECE, served | t7+ ECE, retrained |
| --- | --- | --- | --- | --- |
| 0.00 | 0.5978 | 0.5953 | 0.039 | 0.029 |
| 0.25 | 0.6189 | 0.5984 | 0.110 | 0.052 |
| 0.50 | 0.6151 | 0.5965 | 0.115 | 0.044 |
| 1.00 | 0.6079 | 0.5940 | 0.072 | 0.026 |

The partial-information bump fell from +0.021 to +0.003 nats — sevenfold, from 24,492 closed-sheet
rows in a 662k-row mix. And the ordering of the ends flipped: full ignorance now edges full
knowledge. Hidden rows take the `human_closed` temperature and unhidden ones do not, so the ECE
columns are partly rescaling; log loss was shown insensitive to that temperature (below), so the
log-loss columns are the clean comparison, and they say the same thing.

Step 3's gate agrees with the curve rather than with the plan:

| log loss | true | mode | particles | open |
| --- | --- | --- | --- | --- |
| `wp-v1-sw-split-small` | 0.5900 | 0.5910 | 0.5860 | 0.5830 |
| `wp-v1c-sw-split-small` | 0.5852 | 0.5867 | 0.5810 | **0.5696** |
| `wp-v1c-gbt` | 0.5618 | 0.5618 | 0.5618 | 0.5619 |

The shard improved `open` by 0.013 and the filled arms by 0.005. It taught the model what a
closed-sheet position is — so presenting one honestly now beats filling it with draws, by 0.011
where it was 0.003.

## A confound checked rather than argued

The regime-split temperature treats the arms differently: `fill()` sets `sheet = True` and gets
`human` (0.8924); `mask()` sets `sheet = False` and gets `human_closed` (0.9901). On an overconfident
model a softer temperature flatters log loss, so `open`'s widened margin could have been the
calibration change rather than the information. Stripping `human_closed` from a copy of the model
so all four arms share one temperature:

| | true | mode | particles | open | `open` confidence |
| --- | --- | --- | --- | --- | --- |
| regime-split temperatures | 0.5852 | 0.5867 | 0.5810 | 0.5696 | 0.1724 |
| one temperature | 0.5852 | 0.5867 | 0.5810 | 0.5696 | 0.1841 |

Confidence moved 6.8% and log loss did not move at the fourth decimal: both temperatures sit near
the flat bottom of the loss in temperature. The margin is information.

## Calibration was the thing the regime split was for

What the retrain did not do is pass `closed_sheet_pass`. Worst-bucket ECE on the real closed shard
is 0.093 for the retrained set model, against 0.124 served — every model fails on calibration and
none on discrimination. The split temperature is most of that improvement, and it has a clean
reading: fitted separately, open-sheet human play wants a temperature of 0.8924 and closed-sheet
play 0.9901, so the single fit had been sharpening closed-sheet predictions ~11% more than they
warrant. It could not be fitted before this step, because it needs closed-sheet training rows.
`GBTModel.predict` ignores calibration entirely, so the model the app actually serves in-battle
does not yet receive it.

## The closed-sheet ECE gate was reading its own noise

Step 4 was going to decide between two readings of that 0.093: too little closed-sheet data, or a
confidence fault a temperature per turn bucket would fix. Neither, it turns out. The measurement
that should have come first is whether a gate of `ECE < 0.03` can be read on 131 battles at all
(gate rule 3), and it cannot.

`scripts/analysis/closed_calibration.py` redraws the outcomes from the model's own probabilities
on the same rows, so the model is calibrated by construction, and records the ECE it gets. One
uniform per battle is shared by all of its rows, because a battle has one winner:

| bucket | battles | calibrated model's ECE (median) | calibrated model < 0.03 | observed | calibrated model scores worse |
| --- | --- | --- | --- | --- | --- |
| t1-2 | 131 | 0.060 | 3% | 0.028 | 98% |
| t3-4 | 119 | 0.066 | 1% | 0.051 | 82% |
| t5-6 | 101 | 0.075 | 0% | 0.074 | 54% |
| t7+ | 63 | 0.118 | 0% | 0.093 | 80% |

(`wp-v1c-sw-split-small`, [data](../data/analysis/closed_calibration_wp-v1c-sw-split-small.json).)
No model could pass the gate on this set, and the observed ECE is inside what a calibrated model
produces in every bucket. On the open-sheet held-out set, with ~7× the battles, the same floor is
0.025–0.047: it shrinks with n as it should, and sits near 0.03 even there.

Temperatures do not move it either. One per bucket, fitted on the 24,492 closed-sheet training
rows, comes out 0.92–1.02 and leaves t7+ at 0.098; cross-fitted on the held-out set itself — not
shippable, but the ceiling for any temperature — 0.093, which is where the single shipped
temperature already is.

So there was no measured calibration gap for closed-sheet self-play (step 4) or a GBT calibration
path (step 6) to close, and the gate is rewritten instead. `closed_in_battle_ece` now asks whether
the ECE is worse than a calibrated model plausibly scores on the same rows (Bonferroni over the
four buckets, α = 0.05), and records the test's power: how often the same rule catches a model
whose logits are 1.25× or 1.5× too sharp. A test that would miss 1.5× more than 20% of the time
cannot pass anything, so not rejecting under it is **undecided** rather than a pass, with the
reason written into the card. On 131 battles the power against 1.5× is 28% for the set encoder
and 41% for the GBT — undecided, and saying why, is the honest verdict.

What would decide it is battles. Resampling the held-out battles up to larger sets (the power
depends on the predictions and the battle structure, not the observed outcomes, so this is sound
for power and for nothing else — duplicated battles make the verdict itself reject spuriously):

| held-out battles | 131 | 262 | 524 | 1,048 | 2,096 |
| --- | --- | --- | --- | --- | --- |
| set encoder, power vs 1.5× | 0.24 | 0.44 | 0.67 | 0.97 | 1.00 |
| set encoder, power vs 1.25× | 0.11 | 0.11 | 0.23 | 0.42 | 0.78 |
| GBT, power vs 1.5× | 0.39 | 0.63 | 0.91 | 1.00 | 1.00 |

So ~450–750 closed-sheet held-out battles decide the gate at 1.5×, and ~2,000 at 1.25×, against
131 today and 779 in the whole shard. That is scraping, not self-play — calibration measured on
the bot does not carry to human games.

## The battles were already on disk

The gate above said ~450–750 closed-sheet held-out battles would decide it. The replay cache held
7,645 closed-sheet games; the shard held 793. Everything scraped after the last extraction
(2026-09-19) — 6,845 closed-sheet and 2,917 open-sheet replays, mostly from the per-player scrape —
had never been turned into snapshots. Re-extracting kept every earlier battle in its split (0 moved,
0 missing) and took the closed-sheet held-out set from **131 to 1,088 battles**, training from 654
to 6,480. The new games lean stronger (median rating 1248 against 1120; 4,253 players against
941, where the old 800 were one day of uploads with one bot in 101 of them), so every closed-sheet
number from here carries that population.

`wp-v1d`, same recipes, scored on the grown held-out sets:

| | open log loss | closed log loss | `in_battle_pass` | `closed_sheet_pass` | power vs 1.5× |
| --- | --- | --- | --- | --- | --- |
| `wp-v1c-gbt` | 0.5439 | 0.5852 | ✓ | ✗ | 41% |
| `wp-v1c-sw-split-small` | 0.5630 | 0.5813 | ✗ | undecided | 28% |
| `wp-v1d-gbt` | **0.5415** | **0.5593** | ✓ | ✓ | 99.8% |
| `wp-v1d-sw-split-small` | 0.5523 | 0.5633 | ✓ | ✓ | 99.8% |

(`wp-v1c` rows are on their own 131-battle set; the two datasets' held-out sets differ, so compare
within a row's dataset.) The set encoder is the first to pass both regimes; its open-sheet pass is
a hair (played-out ECE 0.02987) and its preview pass (0.005 nats, interval upper −0.0003) is the
width of the field and should be read that way. With sheets hidden it also beats the constant at
preview, by 0.014 [−0.025, −0.002].

The GBT is better in both regimes, and all of that is from turn 5 on: the set encoder wins preview
and turns 1–2, ties 3–4, and loses 0.02–0.03 nats a bucket after. It is also set-blind — on a live
position every drawn opponent gets the identical number, so there is no band, and `wp_open` equals
`wp`. **It is not served for that reason**: a WP model that gives the same answer whatever the
opponent is holding cannot be trusted with a position that turns on what they are holding. Whether
the set encoder uses that information *correctly* is the next question — on the same live position
its averaged and sets-unknown numbers were 11–14 points apart — and the decided-endgame benchmark
is being built to answer it.

## Speed orderings as a model input: sound, once the log is read right

Phase 8 step 8 feeds the model the orderings themselves — "this Pokémon was seen to be at least as
fast as that one, in persistent speed" — because the belief's P(faster) is empty on human rows,
where neither spread is known. `scripts/analysis/speed_orderings.py` checks every ordering against
the true nature, Stat Points and item on 20,000 sampled-spread self-play battles, in both regimes
(hidden sheets by stripping the sheet lines, as a spectator sees a cartridge game):

| | orderings / battle | false | rate |
| --- | --- | --- | --- |
| first cut, open | 4.44 | 33 of 88,758 | 0.037% |
| first cut, hidden | 4.35 | 55 of 87,087 | 0.063% |
| **final, open** | 3.97 | **5 of 79,459** | **0.006%** |
| **final, hidden** | 3.91 | **6 of 78,176** | **0.008%** |

Every fix between the two was the log being misread, not the persistent-speed logic — the same
lesson as the damage channel's eleven faults:

- **Illusion.** A Zoroark-Hisui disguised as Torkoal "outran" a Kingambit: the log files its moves
  under the disguise. A side that brought a Zoroark gives no orderings.
- **Trace was credited to the wrong Pokémon.** `[from] ability: Trace|[of] X` names who was copied
  *from*, and the generic tag rule gave Trace to X. So a Sneasler whose Unburden had been traced
  was recorded holding Trace, and every filter that checks for Unburden passed it. This was in the
  observation itself: **50 of 300 sampled replay streams** carried a wrong ability, in every
  snapshot built so far. The re-extraction for snapshots v4 removes it.
- **Quick Claw's activation was ignored** (`-activate` was on the parser's ignore list), so a
  hidden Quick Claw left the move logged with no item and the order read as Speed.
- **Items change.** A Scarf knocked off makes an earlier ordering stale; an Iron Ball knocked off
  *during* the turn decided that turn's order while still held. Move events now keep the item as
  it stood at the turn mark, beside the boosts and weather they already kept, and only an item
  that bears on order (Scarf, Iron Ball, Quick Claw…) makes a pair unstable — a berry eaten
  mid-turn does not, which is what brought the power back from 3.0 to 3.9 a battle.

The Speed channel's own gate re-runs identically (0 silently wrong over 2,500 battles, same power),
so none of this cost the channel anything. What is left — five and six orderings, each a single
pairing — is below that channel's own gate rate and is recorded rather than chased.

## wp-v1e, and what calibration was and was not doing

`wp-v1e` is the first model with the orderings, the last move and the weather/terrain timers
(featurizer 3, snapshots 4; 942k training rows, same manifest shape as `wp-v1d`). Scoring it turned
into a series of calibration faults, found in this order (2026-09-28).

**1. Temperatures were fitted on training rows.** A model scores its own training battles more
confidently than new ones, so the fit sharpened it: `wp-v1e`'s training rows asked for 0.82 (open)
and 0.78 (closed), its validation rows for 1.18 and 0.96. Fitted on training rows it failed six
gates with an unchanged log loss; fitted on validation it was open 0.5502 / closed 0.5633 against
`wp-v1d`'s 0.5523 / 0.5633, played-out ECE 0.021 against 0.030, and the preview interval clear of
zero. `wp-v1d` had the same fault (0.89 / 0.95 on training rows, 1.12 / 1.07 on validation). A
re-run of `calibrate` also fitted on top of the previous `calibration.json`; it now fits raw logits.

**2. The 0.03 threshold was a coin toss, and the test that replaced it fails everything.** The
open-sheet t7+ bucket was 0.03005 for `wp-v1e` and 0.02987 for `wp-v1d`. `in_battle_ece` became
the closed-sheet gate's test — ECE against a calibrated model on the same rows, battle-clustered,
Bonferroni, with power — and on 4,127 held-out battles it has 100% power and rejects both models at
t3 onward (ECE 0.024–0.030 against a floor of 0.012–0.016). The fixed threshold had been too
lenient here, as it had been too strict on 131 closed-sheet battles.

**3. The validation split leaked Bo3 siblings.** Best-fit temperature by turn, open sheets:

| Rows | preview | t1-2 | t3-4 | t5-6 | t7+ |
|---|---|---|---|---|---|
| validation, drawn per battle | 1.90 | 1.52 | 1.26 | 1.10 | 1.05 |
| held-out players (by group) | 1.37 | 1.07 | 0.99 | 0.99 | 1.08 |
| held-out teams | 1.57 | 1.18 | 1.08 | 1.02 | 0.96 |

A turn-dependent temperature fitted on the first row failed t1-4 on the others. Validation was
drawn per battle and `heldout_human` per group, so a validation game's sibling games — same teams,
same players — were in training, and the model was confidently wrong whenever a series split.
Validation is now drawn per group.

**4. Then it is too small.** Drawn per group, 5% of human groups is 287 open-sheet battles in 137
series and ~300 closed-sheet battles. Retrained on it, `wp-v1e` stopped at epoch 4 instead of 3 and
scored 0.5596 on open sheets (worse), with temperatures 1.28 / 1.55; recalibrated on it, `wp-v1d`
asked 0.87 — but its weights were trained under the per-battle split, so the group split includes
battles it trained on, and that recalibration was discarded (`wp-v1d` is as committed). A
temperature fitted on ~140 independent series cannot pass a test judged on 4,127 battles.

**5. The shape is not a temperature's.** With no calibration at all, `wp-v1e` fails t1-2 at 0.044.
Its reliability on held-out open-sheet spectator rows, t1-2:

| predicted | 0.06 | 0.16 | 0.26 | 0.35 | 0.45 | 0.55 | 0.65 | 0.75 | 0.84 | 0.94 |
|---|---|---|---|---|---|---|---|---|---|---|
| won | 0.07 | 0.21 | 0.30 | 0.40 | 0.46 | 0.51 | 0.57 | 0.66 | 0.79 | 0.94 |

Over-confident in the middle, right at the tails, weaker but the same at t3-6. A temperature scales
all of it, so every variant — single, per regime, per turn — traded the middle against the tails.
A small side offset adds to the measured ECE: p1 won 48.5% of held-out open-sheet battles against
50.3% in training (≈1.9 SE, likely noise; the model is side-neutral by construction).

**6. A tolerance does not rescue it.** The null is now "miscalibrated by up to 1.1× in logit
scale". On 6,000 synthetic battles a perfect null already passes a model 1.1× too sharp and rejects
1.15×, so the tolerance moves the line little; the real miss is larger than that.

**7. Played-out calibration conditioned on the future.** Whether a game will end in a KO is not
known when the prediction is made, and played-out games are the closer ones, so a model calibrated
on all games is over-confident on that slice by construction. It is a report line now, not part of
`in_battle_pass`.

What this leaves: the temperature's original job — self-play-trained models over-confident on
humans — is mostly done by the data now (held-out groups ask ~1.04 in battle). What remains is a
mid-range, early-game over-confidence that looks like memorised teams or players, to be treated in
training first; a two-parameter calibrator with ~20% of human groups for validation if that is not
enough. `wp-v1e` as committed is the epoch-4 retrain, uncalibrated; `wp-v1d` stays served.

## wp-v1f: open-sheet calibration came from validation size, not from training

Plan v3 step 1 (2026-09-28). Every number below the "Held-out" heading was read once, for a model
chosen beforehand on validation.

**1. Validation is 20% of human groups.** `vgc wp resplit` redraws validation from an existing
dataset without re-featurizing (train and val are one pool indexed against the same battles), and
`VAL_RATE` is now per source: human 20%, self-play 5% (a model is never selected on self-play).
`wp-v1f` is `wp-v1e`'s rows with 2,487 human validation battles (1,267 open-sheet, 1,220
closed-sheet) instead of ~590. The price is 15% fewer human training battles.

**2. No training treatment beat seed noise.** Same recipe as `wp-v1e`, scored on human validation
with no calibrator (`vgc wp valcheck`, which undoes the temperature `set_torch` bakes in):

| run | change | open logloss | open t1-2 ECE | T wanted (open) | closed logloss |
|---|---|---|---|---|---|
| base | none | 0.5339 | 0.054 | 1.15 | 0.5505 |
| nopre | WP loss 0 on preview/bring rows | 0.5279 | 0.047 | 1.10 | 0.5522 |
| nopre, seed 1 | same | 0.5321 | 0.024 | **0.87** | 0.5495 |
| idp5 | identity dropout 0.5 on preview/bring rows too | 0.5304 | 0.039 | 1.10 | 0.5518 |

Two seeds of one recipe want temperatures of 1.10 and 0.87. Every run stops at epoch 3 or 4, with
validation loss rising from the next epoch while training loss keeps falling, so how confident a
model is depends mostly on where early stopping lands. Removing or masking the identity signal on
preview rows is directionally right (each beat base on open sheets), but the gap is the size of the
seed spread. It is not shown to fix calibration.

**3. The turn-slope temperature that already existed is enough once validation is big enough.**
Two-fold by group on validation: fit on one half, test on the other.

| model | calibrator | open ECE | open test | closed ECE | closed test |
|---|---|---|---|---|---|
| base | none | 0.026 | fail (t1-2 0.054) | 0.034 | fail |
| base | temperature + turn slope | 0.012 | pass | 0.016 | pass |
| base | beta + turn slope | 0.013 | pass | 0.016 | pass |
| nopre | temperature + turn slope | 0.014 | pass | 0.016 | pass |
| nopre | beta + turn slope | 0.014 | pass | 0.016 | pass |

Beta calibration adds nothing over the two-parameter temperature, so no new calibrator was built.
What failed before was the fit, not the curve: ~140 series cannot pin down a turn slope.

**4. Held-out, once.** `idp5` was chosen on validation. It ties `nopre`, passes closed sheets
uncalibrated, and keeps the preview WP head trained, which the bring/lead ranking in
`vgc wp preview` and the Library page sorts on (a `nopre` model could only be pinned in battle). It
was calibrated with `vgc wp calibrate` on the 20% validation (open: T 1.27 at turn 0, slope −0.058
per turn, so 0.85 from turn 7; closed: T 0.91, slope +0.027) and scored:

| | wp-v1d (served) | wp-v1e, uncalibrated | wp-v1f-idp5 |
|---|---|---|---|
| open spectator logloss | 0.5523 | 0.5605 | 0.5528 |
| closed spectator logloss | 0.5633 | 0.5653 | 0.5665 |
| open ECE t1-2 / t3-4 / t5-6 / t7+ | 0.024 / 0.021 / 0.018 / 0.022 | 0.044 / 0.027 / 0.024 / 0.022 | 0.019 / 0.015 / 0.019 / 0.033 |
| `in_battle_ece` (open, power 1.0 at 1.5×) | pass, t1-2 by 0.0006 | **fail** at t1-2 | pass, t7+ by 0.0001 |
| `closed_sheet_pass` | pass | pass | pass |
| `preview_beats_constant` 95% CI | [−0.0092, −0.0003] | fail | [−0.0107, −0.0036] |
| `all_pass` | fail (`player_beats_spectator`) | fail | **pass** |

**5. The plan's premise was stale.** "The powered test fails every model" was measured before the
1.1× tolerance (point 6 of the `wp-v1e` section) went in, and the committed cards had not been
re-scored since. Re-carded under the current test, `wp-v1d` passes open sheets too, by 0.0006 at
t1-2. Both cards now carry the same test. The t1-2 miss the plan was aimed at is fixed in `idp5`
(0.019 against 0.024 for `wp-v1d`), but it passes t7+ by a hair: the negative slope sharpens late
turns, and the played-out report rejects t7+ (report only, point 7). Neither pass has margin to
spare in every bucket.

**6. Preview.** `idp5` is the first model whose preview interval clears zero by more than a
rounding error: −0.007 nats, one seed. That does not reverse finding 1, which is about magnitude,
but it does refute the reason preview rows were spared identity dropout ("masking costs the
preview signal"), and the code comments now say so.

## The decided-endgame benchmark, first scores: the models do not follow the deciding fact

Plan v3 step 2 (2026-09-28). `vgc wp benchmark` builds each variant of
[`decided_endgames.yaml`](../benchmarks/wp/reg_mc/decided_endgames.yaml) as a hand-entry journal,
the taps a person would make, and scores it through the app's own WP (`live.wp`, 24 opponents
drawn from the belief). The Speed pairs and choice locks the variants name reach the model input
(`evidence`), which `tests/test_wp_benchmark.py` pins. `expected` is still hand-reasoned, but
direction and invariance do not use it, and mixing compares a model with itself.

**The benchmark tells a set-blind model apart.** `wp-v1e-gbt` moves by exactly 0 on every fact
about a set. It moves only on Trick Room (0.21 of an expected 0.85, since Trick Room is one of its
hand features) and on terrain. Every set encoder moves a little on most facts.

WP moved between the two variants, against the gap in `expected`:

| pair | fact | expected | gbt | v1d | v1e | v1f-idp5 |
|---|---|---|---|---|---|---|
| F1 A→B | Life Orb → Choice Scarf, on the sheet | −1.00 | 0 | −0.03 | −0.09 | −0.07 |
| F1 D→E | Speed order: they were faster → we were | +1.00 | 0 | +0.00 | +0.04 | +0.07 |
| F2 A→B | locked into Rage Fist → Close Combat | +1.00 | 0 | 0 | −0.01 | +0.00 |
| F3 A→B | sun 4 turns left → 1 | −0.80 | 0 | 0 | −0.00 | −0.00 |
| F4 A→B | Trick Room 3 turns left → 1 | −0.85 | −0.21 | −0.03 | −0.02 | −0.01 |
| F5 A→B | Extreme Speed on the sheet → not | +1.00 | 0 | −0.01 | −0.01 | −0.00 |
| F6 A→B | Focus Sash held → consumed | +0.90 | 0 | +0.05 | +0.11 | +0.09 |
| F7 A→C | Grassy Terrain 3 turns left → none | −0.85 | +0.01 | +0.00 | +0.01 | +0.03 |
| F8a A→B | Speed order in the Kingambit mirror | +1.00 | 0 | 0 | +0.00 | +0.02 |
| F8b B→C | Speed order: they were faster → we were | +1.00 | 0 | 0 | +0.09 | +0.06 |

Over all 26 direction pairs, the right way: gbt 15%, `wp-v1d` 46%, `wp-v1e` 69%,
`wp-v1f-idp5` 85%. Each set of new inputs made the direction more often right, and never much
bigger: the mean share of the gap moved is 0.02–0.04 for every model.

- **Plan step 2's question: does `wp-v1e` move on F1-D/E, F2 and F8a/b?** No, not usefully.
  0–9% of the way, and on F2 not at all. The inputs arrive and the model has learned almost
  nothing from them.
- **The timers are read and not used.** Featurizer 2 and 3 models have weather and terrain turns
  as columns, and F3 and F7 are flat. `wp-v1f-idp5` moves F7 the wrong way.
- **Mixing.** With the fact hidden, `wp-v1f-idp5` sits below its own mix of the known cases in all
  three families (F1 −0.03, F5 −0.14, F6 −0.07). With so little movement between the known
  cases, that says more about a closed-sheet offset than about mixing.
- **Invariance.** A fainted Pokémon's used move moves `wp-v1f-idp5` by 0.020 (F9/B). The other
  changes move nothing.
- **Distance from `expected`.** About 0.42 for every model. These are decided positions, and
  every model answers them near its prior for a 1v1 at those HP values.
- **The served closed-sheet pin.** `wp-v1d` is featurizer 1 and cannot see a Speed order, a last
  move or a timer at all. That costs little on this benchmark today, because `wp-v1f-idp5` barely
  uses them either.

What it means: a WP learned from ~1100-rated human games does not carry the mechanics that decide
an endgame. They are rare in the data, and when they happen the outcome is already mostly in HP
and numbers. Training alone is unlikely to close a 20× gap. The benchmark supports the route Phase 9
already plans, where a revealed Scarf or choice lock acts through the engine in search and WP is
read at the leaves. The solver that supplies `expected` is that route's first piece.
