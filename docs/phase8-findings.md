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

## The endgame solver, and the benchmark against the engine's answer

Plan v3 step 2 (2026-09-29). `vgc wp solve` replaces the benchmark's hand-reasoned `expected` with
search over the pinned engine (`sidecar/showdown/endgame-solver.js`, `vgc.wp.solver`), stored in
`benchmarks/wp/reg_mc/solved.json`. Scoring prefers it where it exists.

**How it solves.** A 1v1 is a simultaneous-move game with chance. Each node is a matrix game over
both sides' moves, solved for its minimax value, four turns deep. Three things were tried before
this held up:

- **Sampling chance was biased.** With a few seeds a node, a 90% Rock Slide came out at 67%.
  With one or two deeper down, each player saw the dice before choosing, and two runs of the F4
  stall gave 1.0 and 0.89. Chance is now **enumerated**. A scripted PRNG replays the turn down every
  branch of every random call, with its true probability, and outcomes that reach the same state
  are merged. Damage rolls use 2 representative rolls, and percentage rolls use 10.
- **Crits count.** A position lost unless a 1-in-24 crit lands is worth 1 in 24, not 0. That is
  the thin out a WP most needs to get right. What keeps crits affordable is a path-probability
  cutoff: a line below 0.1% is scored by HP share instead of searched on, and its mass is
  reported in `leaf_mass`. A crit that wins on the spot ends the battle and is counted exactly.
  F11 was added for this: Garchomp's Dragon Claw KOs the Incineroar only on a crit, and it solves
  to **0.0417 = 1/24** with Focus Sash and **0.125 = 1/8** with Scope Lens.
- **Their hidden spread is integrated, not drawn.** It matters here through move order, so the
  Speed prior (`belief.prior.speed_prior`) is summed over three classes (faster, tied, slower),
  one solve each, with a named Speed ordering removing what it rules out. The rest of the spread
  maxes the main offensive stat, then HP. Maxing every live offensive stat had given an Incineroar
  with Snarl 32 SpA and 2 HP, and turned F11 into a plain KO.

It took 3 h 13 min on 8 workers, 2 h 50 min of it F6. Both Pokémon are at full HP there, so the
tree is deep. Every position is cached as it finishes (`.vgc/endgame-solver.jsonl`, keyed on the
position and the solver's source), so a stopped solve keeps its work.

**Where the engine corrected the hand reasoning:** F5-A needs the Sneasler to have been out a
while, or Fake Out saves it (0.12). F7-B is 0.00, not 0.20. F4-A/C are 0.90/0.91; the first
sampled solve had said 0.68. F8a-D, the Chople Berry mirror, is 0.04: our crit. F1-B is 0.27, not
0.0, because the Speed prior puts 27% of a Scarf Basculegion's weight below the 4 Speed SP it needs
to outspeed. The prior is not conditioned on the item, and for a Scarf set that is unrealistic.
Fixing it belongs in `belief.prior`, not here.

**Scored against it, no model separates a lost position from a won one.** Mean WP where the engine
says lost (≤ 5%, 14 variants) and won (≥ 95%, 10 variants):

| model | WP when lost | WP when won | separation (ideal ≈ 0.95) | direction right |
|---|---|---|---|---|
| wp-v1e-gbt | 0.44 | 0.51 | 0.07 | 14% |
| wp-v1d | 0.40 | 0.50 | 0.10 | 41% |
| wp-v1e | 0.38 | 0.53 | 0.15 | 59% |
| wp-v1f-idp5 | 0.64 | 0.72 | 0.08 | 69% |
| as served (both pins) | 0.59 | 0.68 | 0.09 | |

- **A per-model offset dominates.** `wp-v1f-idp5` sits near 0.65 in these 1v1 endgames whatever
  the position; `wp-v1e` sits near 0.35. The offset is larger than anything a deciding fact moves.
- **F11, lost unless it crits:** `wp-v1f-idp5` says 0.69 where the truth is 0.04. A Scope Lens
  should raise it to 0.125; `wp-v1e` and `wp-v1f-idp5` both move down instead.
- **Invariance holds** except the 0.02 `wp-v1f-idp5` gives a fainted Pokémon's used move.

This does not contradict the held-out calibration gates. Those average over real games, where
1v1s decided by one fact are rare and short. It says the served WP should not be read as an answer
in exactly the positions where a player most wants one. The solver itself is the thing that
answers them.

## The solver in the app: a live 1v1 as solver positions

Plan v3 step 3 (2026-09-29). `vgc.wp.endgame` turns a battle in which each side has one Pokémon
left into the positions `vgc.wp.solver` searches. `vgc.web.solving` solves them in the background,
one turn deeper at a time. The Battle page shows the result under the model's number, labelled
separately.

**One writer for both.** The benchmark and a live battle write positions through the same
`solver.compose`. Empty entries are left out (no stages, not consumed), so the same position is
written the same way either way. Our HP is written as the shortest percentage that gives back the
exact HP: a whole 30 where the benchmark wrote 30. The written form is the cache key. The existing cache (34 positions, one
of them 2 h 50 min of F6) was re-keyed rather than re-solved. The solver gained two setup fields:
non-volatile status (burn, paralysis, poison) and side conditions with their turns left
(Tailwind, screens). Before the re-key, six cached positions were solved again with the edited
solver. Values, node counts and strategies were identical.

**The same answer twice.** `endgame.check` runs every solved variant through its hand-entry
journal, a replay and the adapter. All 32 give the positions and Speed-class weights
`solver.plan` wrote, and values within `leaf_mass` of `solved.json`. A closed-sheet variant is
checked with its set pinned to the truth's. Getting there found three problems:

- **A choice lock the truth left out.** In F1-D and F5-D their Choice item holder makes its
  evidence move on turn 1 and never leaves the field, so it is locked into that move. The solved
  positions had it free. Solved with the lock, the answers are unchanged: F1-D 0.0744 (was
  0.0743), F5-D 0.0006. The benchmark now writes the lock.
- **F2-C's journal contradicted the variant.** "Not locked, just came in" was built with its
  Annihilape arriving before five idle turns. It now arrives on the last turn. The model's number
  for F2-C is unchanged (0.4843).
- **Speed ties are the engine's, not the float's.** A Scarf on 101 Speed is 151 in Showdown, a tie
  with 151, not 151.5. `solver.position_speed` follows the engine's arithmetic, including
  paralysis, which halves after the other modifiers. A paralysed Scarf holder on 101 is 75, where
  chaining the factors says 76. No benchmark class moved, and the test checks against the engine.

**Found and not fixed: three benchmark items are illegal in Reg M-C.** F5's Arcanine-Hisui holds
a Choice Band, F11's Incineroar an Assault Vest, and F1's filler Gholdengo Choice Specs. None of
them are in the regulation's dex, and `validate_set` rejects all three. The engine simulates them
anyway, so their solved values are real answers, but for sets nobody can bring. Replacing them
changes the calcs each family was built on, so it is a redesign of F5 and F11, not a substitution.

**A closed sheet.** The set belief's sets (`belief.sets.given`) are reweighted by the turn order:
each set's share times the prior mass of Speed investments that could have produced this
battle's orderings *with that set's item and nature*. The journal is replayed with the set on the
sheet, because on a closed sheet an unseen Scarf is invisible to the Speed channel. The top three
are solved, and the rest is reported as unsolved. On F1-D, with nothing pinned, every set left is
a Choice Scarf (Jolly 70%). With no evidence (F1-C), Life Orb leads at 32%.

**Depth.** One turn deep takes 1–9 s and is mostly HP share: F1-B's two Speed classes both read
0.63, against 0.99 and 0.02 at three turns (about 20 s). The page shows the deepest finished
answer with the share of it that rests on HP share, and fades it while that share is over half.

## Observer mode: watching an open-sheet game, both spreads hidden

Plan v3 step 4 (2026-09-29). A third mode on the Battle page, **Watching**, for someone else's
open-sheet game: two sheets, a spectator's `BattleState`, HP as percentages on both sides, and WP
oriented to player 1. It uses the open-sheet model and gate. `in_battle_ece` is scored on
spectator rows, so this is the seat the served model was gated from; the registry and pins are
unchanged.

**Speed with two unknowns.** The turn-order channel narrows an unknown against a known Speed and
defers a pair of two unknowns, which from the stands is every pair. `belief.speed.joint` keeps the
two Pokémon of a 1v1 jointly:
- their two priors multiplied;
- an ordering between them is an indicator on the pair;
- an ordering with anyone else is that Pokémon's prior mass under which it could have happened.

On F8b-B, watched, no pair with the Gholdengo faster keeps any weight. The player view is the case
where one side is a point mass. It goes through the same code and still reproduces all 32
benchmark variants exactly. A closed sheet's candidates are weighted by the joint's total.
Memoising it per item, ability, nature and Speed prior took a closed-sheet plan from ~9 s to well
under one.

**The solver over both spreads** (`solver.partition_joint`) is still at most three solves a
position. Pairs of Speed investments are grouped by the move order they give, and each class is
represented by its heaviest pair. Both sides' other stats are `spread`'s assumption. Watched, F1-B
puts 94% on the Scarf Basculegion moving first once the Mega Charizard's Speed is hidden too.
F8a-C, the Kingambit mirror, splits 45/45 with a 10% tie. In the running app it solves to 0.50
(3% leaf mass at three turns), where the model says 0.68 for player 1.

**A leak, fixed.** A search's node processes outlived a deleted battle and the server itself.
Deleting a battle now cancels its search, and shutting the app down kills whatever is running.

## The Speed prior reads the item

Plan v3 step 5 (2026-09-29). `speed_prior` gave a Choice Scarf Basculegion 27% below the 4 Speed
SP it needs to outspeed a Mega Charizard Y, which nobody builds, and the solver inherited it.
A human's spread is not observable, so the candidates were scored on its consequences:
`scripts/analysis/spread_prior.py` over every cached replay, 135,245 racing pairs. The 6,220 with
a Scarf holder are where the effect lives. Nats per pair against today's prior, cluster bootstrap
over replays:

| variant | Scarf pairs | close pairs | all pairs |
|---|---|---|---|
| A: the item multiplies what each investment outruns | **+0.018** [+0.016, +0.021] | +0.002 | +0.001 |
| B: A, and a Scarf's uninvested extreme goes to max | −0.002 [−0.005, +0.001] | −0.000 | −0.000 |
| **C: only the extreme moves** | **−0.015** [−0.019, −0.011] | −0.0018 [−0.0023, −0.0013] | −0.0007 [−0.0009, −0.0005] |

A was the obvious fix and is worse. With the multiplier, clearing a benchmark is cheaper, so the
class minima fall and weight moves to low Speed, while Scarf holders in the data are fast. C wins
on every cut and is what `speed_prior(item=...)` does now: a Speed-raising item takes the 0-SP
extreme's weight to 32. Scarf Basculegion below 4 SP goes from 27% to 10%, and at 32 from 18% to
35%.

In the benchmark, F1-B (open sheet, Scarf) goes from 0.27 to **0.097**, F1-D 0.074 → 0.061, and
F1-C's mix 0.70 → 0.63. F2's representative Annihilape moves to 32 Speed and its answers are
unchanged. The served model's separation of lost from won 1v1s is 0.087 (was 0.09).

## The engine against how human 1v1s end

Plan v3 step 3.6 (2026-09-29). Every held-out, human, open-sheet replay that reaches a 1v1 is
stopped at its first turn mark with one Pokémon a side and asked twice, from the stands: the
served open-sheet model (`live.wp`), and the engine (`vgc.wp.endgame` through the Observer, both
spreads integrated, two turns deep). Both are scored against who won. Of 1,703 held-out games, 217
reach a 1v1. 12 cannot be set up (7 volatiles, 5 with a Pokémon never shown), and 31 have a
position that did not finish in 180 s (30) or crashed the simulator (one game). That leaves **174
games in 166 groups** (`scripts/analysis/solver_vs_humans.py`,
`data/analysis/reg_mc/solver_vs_humans.json`). Intervals are cluster bootstraps over groups:

| | games | Brier engine / model | log loss engine / model | engine − model, log loss |
|---|---|---|---|---|
| all | 174 | **0.090** / 0.197 | **0.330** / 0.580 | −0.250 [−0.358, −0.130], engine better |
| settled (leaf mass ≤ 0.1) | 50 | **0.086** / 0.161 | 0.355 / 0.507 | −0.152 [−0.43, +0.18], not distinguishable |
| unsettled | 124 | **0.092** / 0.212 | **0.320** / 0.610 | −0.289 [−0.378, −0.199], engine better |
| played out | 155 | **0.092** / 0.200 | **0.337** / 0.585 | engine better |
| forfeits | 19 | **0.079** / 0.178 | **0.275** / 0.542 | engine better |

**When the engine calls it (95%+ either way), the called side won 48 of 50.** The model, on the
same 50 games, gave that side 0.73 on average. The two it lost are both fully settled (leaf mass 0):
Venusaur against Garchomp (engine 0.96) and Sneasler against Rillaboom (1.00). Either a player
missed the line or the assumed spreads are wrong there. On settled positions the Brier difference
is clear, and log loss is not only because those two confident misses cost ~3 nats each.

The engine is, if anything, **underconfident** in the middle. Positions it put at 0.17 were won by
player 1 10% of the time, and positions at 0.83 were won 92% of the time. The model is calibrated
(0.20 → 0.21, 0.78 → 0.76) but rarely leaves 0.2–0.8. This is the finding from the decided-endgame
benchmark, now on real games: the model does not follow what decides a 1v1, and the engine does.

What it does not say:
- **Depth 2 only.** Deeper search is out of reach at scale until the solver is faster (PLAN-v3 step 8).
  Even the unsettled answers, which rest ~47% on HP share, beat the model. So the search's
  first turns, not the HP heuristic, are where the gain is.
- **The hard positions are missing.** The 31 excluded games are the ones the solver could not
  finish, so this is the engine on the 1v1s it can solve in three minutes.
- **~1100-rated players.** The engine assumes best play. Here that is still the better predictor,
  and a stronger field would presumably only help it.

**A bug the check found.** The first run scored the model at ~0.50 on 173 of 174 games. A
spectator record featurizes as two rows, one per seat, and `live.wp` read them as two draws, so
every position from the stands averaged with its mirror. The Watching page showed the same
number. It now pairs them as `vgc wp eval` does (`c2744f8`). The table above is the fixed model.

Open: one game (`gen9championsvgc2026regmcbo3-2683090653`) crashes the simulator inside
`BattleActions.useMove` for both of its positions. Its error is kept in the results file. Fixed
since: see "Solver speed, first pass" below.

## Which number leads on a closed sheet: the position as shown

Plan v3 step 6, item 1 (2026-09-29). The Battle page led with `wp`, the served model averaged over
`k` opponents drawn from the set belief, and showed `wp_open` beside it: the position with what has
not been revealed left unknown. On a live position the two were 11–14 points apart. The masked-sheet
comparison above favoured `open` by 0.011, but its mask holds turn-0 information fixed for the whole
game, and a real Team Preview Only game is not like that. So this asks the genuine closed-sheet
games, against who won (`scripts/analysis/wp_open_vs_particles.py`,
`data/analysis/reg_mc/wp_open_vs_particles.json`). It uses every held-out, human, closed-sheet
spectator snapshot, the served closed-sheet model (`wp-v1d-sw-split-small`), and 8 draws a
position, each filling only what the game had not revealed, conditioned on what it had. That gives
1,025 games in 994 groups and 10,624 positions, with a paired cluster bootstrap:

| | positions | log loss, drawn | log loss, as shown | as shown − drawn |
| --- | --- | --- | --- | --- |
| all | 10,624 | 0.580 | **0.565** | −0.015 [−0.027, −0.004], as shown better |
| played out | 6,280 | 0.596 | **0.577** | −0.020 [−0.034, −0.005], as shown better |
| preview | 3,685 | 0.570 | **0.557** | −0.013, as shown better |
| t1-2 / t3-4 / t5-6 | | | | −0.018 / −0.017 / −0.015, as shown better in each |
| t7+ | 1,635 | 0.521 | **0.505** | −0.016 [−0.034, +0.002], not distinguishable |

The two are equally confident (0.203 against 0.203 from 0.5), so this is information, not
calibration. The models have been trained on closed-sheet rows since `wp-v1c`, so a position with
unknowns in it is one they know. A drawn set, by contrast, is a guess presented to them as fact.

**What changed:**
- `live.wp` returns the position as shown as `wp`, which the page, the per-turn curve and the
  benchmark all read. The draws are still made: their mean is `drawn`, and their 10th-to-90th spread
  is the band showing what the hidden sets are worth.
- The closed-sheet brings preview (step 6, item 2) builds the legal stand-in team the simulator
  needs, then hides its sets again. It had taken each species' most common set as fact.
- On an open sheet nothing is unknown but the spread, which no model reads, so nothing changes
  there. On the decided-endgame benchmark the served models' mixing error fell from 0.289 to 0.152,
  and the lost/won separation went from 0.087 to 0.101.

The preview's evidence is the preview bucket above, which is the closed-sheet in-battle model from
the stands. The bring ranking uses the default model from the player's seat, which was not measured
separately.

## Damage and bulk with sheets hidden: not sound, and not run

Plan v3 step 6, item 3 (2026-09-29). Both channels were gated with open sheets. With the sheet
lines stripped from the same 1,000 sampled-spread battles, as a spectator sees a cartridge game
(`--regime closed` on `damage_belief.py` and `bulk_belief.py`, the way `speed_orderings.py`
re-earned the Speed orderings), they fail:

| channel | silently wrong, sheets open | silently wrong, sheets hidden | contradicted, hidden |
| --- | --- | --- | --- |
| damage (offensive SP) | 0 of 2,866 | **189 of 2,867 (6.6%)** | 430 |
| bulk (HP × defensive SP) | 6 of 3,373 (0.18%) | **74 of 3,377 (2.2%)** | 50 |

(`data/analysis/damage_belief_spreads_closed.json`, `bulk_belief_spreads_closed.json`.) The misses
are unrevealed damage modifiers read as investment. A Rillaboom's hidden Miracle Seed makes 14
Attack SP look like at least 16, and Kingambit's Defiant and Baxcalibur's Mega show the same
pattern. With the sheet, the channel knows the item. Without it, "no item" is an assumption, and
it is wrong often enough to matter. This is soundness per regime again: 0% wrong under open sheets
is 6.6% under Team Preview Only.

Nothing in the app changes, because neither channel runs live: the SP belief behind the Speed read
is combined from the Speed channel alone. To make them sound with sheets hidden, an unrevealed item
or ability would have to be bounded over everything the set belief still allows, taking the union
of the feasible sets. That costs power, and nothing waits on it.

## Solver speed, first pass: where the time goes

Plan v3 step 8 (2026-09-30). This pass fixed the crash, then profiled before building anything.
Profiling used an instrumented copy of `endgame-solver.js`, which timed each phase of a replay and
counted branches by the kind of random call that opened them. It ran on two sets: the heaviest Speed
class of each of the 31 games that did not finish, capped at 90 s, and 18 positions from 12 finished
games, capped at 240 s. Both sets used six at a time, as the check does.

**The crash was the solver's own position, in two ways.**
- `setUp` wrote a choice lock's last move as a plain `Move`. `State.serializeBattle` writes that
  as `[DataMove:id]`, which it cannot read back, so after one copy `lastMove` was a string, and
  Encore read `.flags` off it. It is now an `ActiveMove`, as the simulator leaves it.
- The root request was made without the `DisableMove` pass that `endTurn` runs before every later
  one. A choice-locked Pokémon was offered all four moves at the root, and anything but the lock
  failed. The pass now runs. The benchmark's five locked positions give the same values and leaf
  mass, with 1.1–60× fewer turns simulated (F5-D slower: 5,003 → 84).
- The game itself is a No Guard Mega Raichu Y against a Scarf Annihilape locked into Phantom Force
  at 6%. Zap Cannon lands through the dig, so the engine gives player 1 a certain win, and player 1
  lost. The game is a test fixture now.

**Where the time goes** (the 31 slow positions, ~2,500 s):

| | share |
| --- | --- |
| simulating the turn, replays that finish | 48% |
| simulating the turn, replays that stop at a branch | 10% |
| deserializing the battle for each replay | 39% |
| serializing children, position keys, matrix solves | ~1% |

The cost is ~2.5 ms a replay, and it is steady (the cache's 386 solved positions: 3.0 ms median,
p10–p90 2.3–3.9). So the time is the replay count. **992k replays reached only 40k distinct
outcomes: 95% of finished replays merged into an outcome already found.** The waste has one source:
68% of all replays were spawned by 10-way percentage rolls, 88% of those from
`BattleActions.secondaries` and 12% from `selfDrops`. Both do `random(100) < chance`, and they roll
even when the chance is 100% or absent. The 10 representatives are then 10 identical branches, and
within a turn the rolls of both moves multiply. The within-solve cache hit 52 times against 679
misses, and the matrix solves cost nothing.

**Measured, not built: the secondaries as `randomChance(chance, 100)`.** This is the same
distribution, and exact for every chance, where the 10 representatives were exact only for
multiples of 10. It was prototyped in the scratch profiler:

| | before | after |
| --- | --- | --- |
| slow set, finished inside 90 s | 6 of 31 | **21 of 31** |
| sample, total time at depth 2 | 1,225 s (capped) | **186 s** (6.6×; median position 2.5×, heavy ones 9–13×) |
| sample, depth 3 | out of reach | **1,245 s, all 18 finished**; 1–10× depth 2, median 3× |
| values | | identical to four decimals on all 22 positions finished both ways |

**Depth 3 costs about what depth 2 cost before,** because most lines of a 1v1 end inside the extra
turn, where the plan had guessed tens of times more work. It also matters. The typical depth-2
answer rests a third on HP share (leaf mass 0.333: a Protect stall's third), and at depth 3 that
falls to 0.037. Answers move with it: 0.72 → 0.97, 0.82 → 0.98, 0.15 → 0.06.

**Also found: the 2–5-hit distribution is wrong.** A 2–5-hit move samples a 20-item array (7× 2,
7× 3, 3× 4, 3× 5). Twenty is over 16, so it is taken as a percentage roll, and its 10 representatives
give 0.40 / 0.30 / 0.10 / 0.20 against the true 0.35 / 0.35 / 0.15 / 0.15. A `sample` that groups
equal items would be exact, with 4 branches instead of 10.

**The plan's levers, re-ranked by this.**
- Reusing results across depths saves the shallower searches, a small share of the next depth's
  cost.
- Pruning dominated moves is the riskiest, and the matrix is 4×4 at most.
- Splitting across workers helps only the page.
- The chance tree is where the replays are: the secondaries, then the multi-hit `sample`, then
  deserialization (39%, a cheaper copy of the battle).

One caveat for "only the time may change": the cache key has no `reach`, so a subtree solved under
one `cutoff` budget is reused under another. A lever that changes the order of visits can move an
answer within its leaf mass.

## Solver speed, built: depth 3 on real 1v1s, and three bugs the new check found

Plan v3 step 8 (2026-09-30), after the first pass above. The two exact levers are built in
`endgame-solver.js`:
- **Secondaries and self drops are one `randomChance`.** Two branches or none, where there were
  ten.
- **`sample` groups equal items.** A 2–5-hit move is now four branches at its true odds.

**The answers did not move where nothing else changed.** 295 positions had an answer from before
the first pass (273 from step 3.6, 22 from the benchmark), and each was solved again. 274 are
identical. The benchmark's differences are only rounding: those entries were rebuilt from a run log
at three decimals. Every one of the 20 that changed contains a move aimed at the ally slot (18) or a
move locked in mid-search (Phantom Force, Electro Shot), which is where the fixes below apply. A
choice lock or Fake Out alone changed nothing. The human positions ran 3.8× faster (median) with
4.6× fewer turns simulated.

**The solver now throws when the simulator rejects a choice.** It used to score the unplayed turn
as if it had happened. That check found three bugs at once:
- **Helping Hand and Coaching** were offered with no target. The simulator rejects that, and the
  search read it as a certain win for the side that chose it. On the cartridge the move can be
  chosen and fails. It is now aimed at the empty ally slot and does exactly that. 18 of the 204 games
  carry one of the two.
- **A move locked in** (the second turn of Phantom Force, Electro Shot, Outrage) comes with no target
  in the request, and the solver gave it one, which was rejected. Targets now come from the request.
  The crash game's non-winning lines fell from 0.83 to 0 or 0.5 as a result; its answer is still 1.
- **Revival Blessing** asks for a teammate to revive, and the solver's fainted teammates are
  stand-ins. `vgc.wp.endgame` does not build such a position (Pawmot, one game), and on a closed
  sheet it leaves sets with the move unsolved.

**Step 3.6 again, at depths 2 and 3** (`solver_vs_humans.py --depths 2,3`, cap 180 s, 6 workers,
79 min for depth 3's 320 positions). 204 games qualify, and **192 are solved (174 before), 144 of
them at depth 3.** 12 still have a position past the cap.

| | games | Brier engine / model | log loss engine / model | engine − model, log loss |
| --- | --- | --- | --- | --- |
| all | 192 | **0.095** / 0.206 | **0.364** / 0.601 | −0.237 [−0.369, −0.081], engine better |
| settled (leaf mass ≤ 0.1) | 134 | **0.076** / 0.187 | **0.334** / 0.558 | −0.225 [−0.412, −0.018], engine better |
| unsettled | 58 | **0.138** / 0.248 | **0.435** / 0.700 | −0.264, engine better |
| played out | 171 | **0.099** / 0.207 | **0.386** / 0.603 | engine better |
| forfeits | 21 | **0.057** / 0.195 | **0.191** / 0.581 | engine better |

- **More of the answers are settled:** 134 games have leaf mass at most 0.1, against 50 before.
  Mean leaf mass is 0.19.
- **More confident calls:** the engine says 95% or more on 111 games (50 before), and the called
  side won 104 of them (93.7%).
- **So it is slightly overconfident at the ends** (0.985 said, 0.944 seen), as best play against
  ~1100-rated players should be. Two of the seven misses are known human errors. One is the Raichu
  game; the other is Incineroar against Milotic, where the old solver's phantom Helping Hand had put
  the answer at 0.75 with it all resting on HP share.

**Depth 3 against depth 2, on the same 144 games and the same solver:** Brier 0.069 against 0.079
(−0.009 [−0.020, +0.002]) and log loss 0.310 against 0.336 (−0.026 [−0.077, +0.038]). Better on
both, and **not distinguishable** on either. Depth 3 moved 65 of the 144 answers by more than 0.05,
so it changes what the page says, but this corpus cannot show that the change predicts ~1100-rated
games better. The new solver against the old one at depth 2 is the same kind of result: 11 of 173
answers moved by more than 0.01, and the difference is within noise.

What this leaves:
- **The page is unchanged.** It already deepens 1 → 4 in the background and shows the deepest
  finished answer, so the speed-up reaches it without a change.
- **The benchmark's stored truth, re-solved at depth 4, is unchanged.** 22 positions were already
  cached. The other 14, the heaviest, went to Kaggle (`scripts/cloud/kaggle_solve.sh`, its first
  real job) and came back in 1 h 46 min on 4 workers. The merge re-solved three of them locally
  (78k–129k turns each) to the same answer. All 39 variants match the old `solved.json`. The one
  difference is F1/B, 0.0969 → 0.0967, where the old entry was rebuilt from a run log at three
  decimals.
- **The overconfidence at the ends** could be taken off with a shrink fitted on held-out games
  (principle 8). It is not built: the ordering is what matters for leading the page, and 7 misses
  are too few to fit on.


## The engine against how human 1v1s end, on closed sheets

PLAN-endgame-doubles stage 0 (2026-09-30). Step 3.6's check, rerun on the regime the Battle page
also leads with the engine in: held-out closed-sheet games seen from the stands, where neither
side's sets are shown (`solver_vs_humans.py --sheets closed --depths 2`, cap 180 s locally; the
1,905 positions were solved on Kaggle in 7 h 7 min on 4 workers, cap 360 s, and merged with three
re-solved locally to the same answer). Each side's sets are its likeliest from the belief, paired
and weighted jointly with the turn order, heaviest first to 90% of the weight and at most 16 pairs
a game (`endgame.pair_candidates`).

Of 1,025 held-out games, 95 reach a buildable 1v1 (907 never reach one; 23 are dropped: 12 with
no set to solve, 8 with a volatile the adapter does not carry, 3 not shown). **86 are scored**; 9
have a pair still unsolved, past the cap or among the 5 positions (two games, by their setups)
that crashed (below).

| | games | Brier engine / model | log loss engine / model | engine − model, log loss |
| --- | --- | --- | --- | --- |
| all | 86 | **0.105** / 0.228 | **0.345** / 0.649 | −0.304 [−0.420, −0.179], engine better |
| settled (leaf mass ≤ 0.1) | 20 | **0.066** / 0.156 | **0.217** / 0.482 | −0.265 [−0.520, +0.079], not distinguishable |
| unsettled | 66 | **0.117** / 0.250 | **0.384** / 0.700 | −0.316 [−0.445, −0.184], engine better |
| belief covered (≥ 90%) | 50 | | **0.352** / 0.597 | −0.245 [−0.393, −0.081], engine better |
| belief partly covered | 36 | | **0.336** / 0.722 | −0.385 [−0.572, −0.195], engine better |

- **The engine keeps the lead on closed sheets**, by as much as on open ones (−0.237 there, at
  depths 2–3). The served closed-sheet model (`wp-v1d-sw-split-small`) is the weaker of the two
  models, which accounts for part of the wider gap.
- **Guessed sets did not make it confidently wrong.** It called 22 games at 95% or more, and the
  called side won 21. Averaging over set pairs makes it call fewer games than on open sheets.
- **Coverage does not matter here.** Games whose belief was only partly solved score as well as
  the rest, so the 90% cut costs nothing measurable.
- **It is less settled:** mean leaf mass 0.35 at depth 2, so most answers still rest partly on HP
  share at the horizon. Depth 3 was not distinguishable on open sheets and was not run.
- **The five crashes are one bug:** Imprison (Farigiraf, Indeedee) against a Pokémon sharing
  Protect. The simulator shows a side's last Pokémon the blocked move as usable and refuses it
  when chosen. Fixed on the `solver-pruning` branch, where the cache is keyed on a declared
  version and the fix changes no cached answer; the five positions are solved there once merged.

**The page is unchanged:** it already leads with the engine on a closed-sheet 1v1, and this is the
check that had been missing for it.

## The engine against how human doubles endgames end: two or fewer a side, open sheets

PLAN-endgame-doubles stage 3 (2026-10-01). Each held-out open-sheet game at its first turn with
two or fewer Pokémon a side that is not a 1v1 (`solver_vs_humans.py --endgame doubles --depths 1`),
planned by `vgc.wp.doubles` and solved at depth 1 with pruning 3, stratified sampling (16), the KO
extension and the damage race for every 1v1 reached. 1,791 positions over three Kaggle sessions
side by side, 8½ hours each, cap 900 s: 1,776 solved, 15 past the cap, nine re-solved locally to
the same answer.

Of 1,703 held-out games, 736 reach such a turn and 674 are built (38 dropped for a volatile, 22
for sleep or bad poison, 2 for Revival Blessing). **664 are scored**, 10 with a position past the cap.

| | games | Brier engine / model | engine − model, Brier | log loss engine / model | engine − model, log loss |
| --- | --- | --- | --- | --- | --- |
| all | 664 | **0.149** / 0.170 | −0.021 [−0.037, −0.004], engine better | 0.486 / 0.509 | −0.023 [−0.081, +0.035], not distinguishable |
| 2v2 | 400 | **0.170** / 0.215 | −0.045 [−0.067, −0.023], engine better | 0.552 / 0.617 | −0.064 [−0.137, +0.017], not distinguishable |
| 2v1 | 122 | 0.106 / 0.102 | not distinguishable | 0.336 / 0.339 | not distinguishable |
| 1v2 | 142 | 0.130 / 0.102 | +0.028 [−0.004, +0.056], not distinguishable | 0.428 / 0.353 | not distinguishable |
| settled (leaf mass ≤ 0.1) | 68 | **0.044** / 0.096 | engine better | 0.313 / 0.330 | not distinguishable |

- **The engine is better on Brier in 2v2s and overall, not yet on log loss.** It called 112 games
  at 95% or more and the called side won 94.6%; in 2v2s 88.6% of 44, so it is overconfident at the
  ends there, which log loss punishes.
- **Its answers rest mostly on the horizon:** mean leaf mass 0.73. For a 2v1 or 2v2 the horizon
  is HP share, and HP share undervalues a Pokémon count lead.
- **2v1 and 1v2 are the same result mirrored, not a bug.** The side ahead won 88.5% and 87.3%;
  the engine gave it 0.715 and 0.726 on average, the model 0.862 and 0.875. Two healthy Pokémon
  against one is 0.67 by HP share.
- **But the engine finds the upsets.** Where it favoured the side behind (35 games of 264), that
  side won 16 (46%), against 12% for the side behind overall. That is the part of a 2v1 the plan
  wanted from it; the count lead is the part the model already has.
- So the lever is the horizon for positions with more than one Pokémon a side: a value that
  knows a count lead, as the damage race knows a 1v1. The page keeps leading with the model in
  2v1, 1v2 and 2v2 until it does.

## A horizon that knows a count lead, and a doubles answer within 5 seconds

PLAN-endgame-doubles stages 3 and 4 (2026-10-01), on the same 674 held-out open-sheet games.

**The damage race for more than one Pokémon a side** (`search.race_doubles`): the trade of blows
played out 64 times with seeded dice, each Pokémon firing the attack that does most towards a KO,
spread moves into both foes, priority then Speed, a fallen target's attack going to the other
foe. Alone it ranks positions as the model does (Brier 0.174 against 0.172) but calls 70% of
games at 95% or more and is right 87% of the time: log loss 0.782. **Calibrated**
(sigmoid(0.403 · logit(race) − 0.024), fitted on 1,500 training-split games, never the held-out
ones), a sure race is 0.88, which is how often the side ahead wins a 2v1, and the race alone
scores log loss 0.473 against the model's 0.513 and Brier 0.152 against 0.172.

**The live answer has 5 seconds**, so the search is a light one: one turn, the two or three
choices a Pokémon would consider (`prune` 2), eight sampled draws of a wide turn with no attempt to
enumerate it first, no KO extension, the calibrated race at the horizon. Before it, a check for a
win either side forces on this very turn: every opposing reply answered, every chance event
played likeliest first down to 10% of the turn, Protect counted as a delay because its odds fall
each turn running, and choices that cannot KO every foe at the lowest roll ruled out by a damage
calculation. Scored exactly as the page runs it (the heaviest three move orders):

| | games | log loss engine / model | engine − model | Brier engine / model | engine − model |
| --- | --- | --- | --- | --- | --- |
| all | 674 | **0.421** / 0.513 | −0.092 [−0.144, −0.039], engine better | **0.130** / 0.172 | −0.042 [−0.060, −0.024], engine better |
| 2v2 | 410 | **0.527** / 0.621 | engine better | **0.169** / 0.217 | engine better |
| 2v1 and 1v2 pooled | 264 | **0.257** / 0.347 | −0.090 [−0.165, −0.010], engine better | **0.069** / 0.102 | −0.033 [−0.055, −0.011], engine better |
| _2v1 alone_ | 122 | 0.245 / 0.339 | not distinguishable | 0.064 / 0.102 | not distinguishable |
| _1v2 alone_ | 142 | **0.267** / 0.353 | engine better | **0.074** / 0.102 | engine better |

- It called 169 games at 95% or more and the called side won 96.4%; the model gave that side
  0.87 on average.
- It beats the deeper search that waited on Kaggle with an HP-share horizon (log loss 0.486): the
  horizon mattered more than the depth.
- Through the page's own path (`solving.request`, warm solver processes, nothing cached), on 60
  of these games: first answer median 1.2 s, final median 2.3 s, never past 5.1 s; 52 of 60 had
  every move order searched in time, the rest kept the quick value for the orders that had not.

## The live doubles answer on closed sheets

PLAN-endgame-doubles stage 3, closed sheets (2026-10-01). The same check on held-out closed-sheet
games seen from the stands, both sides' sets from the belief (`vgc.wp.doubles._plan_closed`: each
hidden Pokémon's likeliest sets, combinations weighed by the turn order with them shown), scored
as the page would run it (live search, heaviest three positions).

**A bug first.** A Mega shows its Mega's ability (Drought, Fairy Aura), and the set belief read it
as the ability on the sheet; the ability test is the one constraint never given up, so no set
matched and 55 of the games that reach such a turn had nothing to solve, all of them games with a
Mega out. Fixed in `belief.sets.given`, which the 1v1 closed adapter and the live view's set
display also read. Of 413 that reach the turn, **349 are scored** (24 dropped for a volatile, 9 for
sleep or bad poison, 30 where no combination of likely sets fits the logged turn order, 1 with no
set at all).

| | games | log loss engine / model | engine − model | Brier engine / model | engine − model |
| --- | --- | --- | --- | --- | --- |
| all | 349 | 0.474 / 0.540 | −0.066 [−0.143, +0.017], not distinguishable | **0.146** / 0.182 | −0.036 [−0.061, −0.010], engine better |
| 2v2 | 214 | 0.555 / 0.628 | not distinguishable | 0.184 / 0.219 | not distinguishable |
| 2v1 and 1v2 pooled | 135 | 0.347 / 0.402 | −0.055 [−0.205, +0.120], not distinguishable | 0.087 / 0.122 | −0.035 [−0.073, +0.003], not distinguishable |
| _2v1 alone_ | 70 | **0.253** / 0.467 | engine better | **0.078** / 0.147 | engine better |
| _1v2 alone_ | 65 | 0.448 / 0.332 | not distinguishable | 0.096 / 0.094 | not distinguishable |

- **Weaker than on open sheets**, as the 1v1 was not: the answers average over guessed sets, and
  the solved positions carry little of the belief (the weight is spread over many combinations;
  the heaviest three positions carry a median of about 40% of it).
- It called 61 games at 95% or more and the called side won 93.4%.
- **By the plan's rule (the engine leads only where it beats the model), no closed-sheet kind
  passes yet.** A 2v1 and a 1v2 are one state with p1 and p2 named the other way round (from the
  stands, p1 is no one in particular), so they are judged pooled, and pooled neither score is
  distinguishable. The 2v1 half passing alone was noise in how the games fell between the labels:
  the 1v2's worse point estimate rests on two confident misses in 20 calls. On the page the label
  would only say which seat the user sat in. (First written as "closed 2v1s pass"; corrected the
  same day, and `solver_vs_humans.py` now reports the pool as `2v1|1v2`.)

## Grouping closed-sheet sets by what changes the fight: tried, reverted

PLAN-endgame-doubles stage 5 (2026-10-01). Each hidden Pokémon's sets were grouped by a signature
(item, ability, the nature's direction on Speed and on the attacking stat, and the moves realistic
play would consider against the foes on the field: near-best attack per foe, priority attacks,
Protect and its kin, Fake Out, speed control, moves on itself), each class solved by its heaviest
member. Planned only (no solving) on 36 held-out closed-sheet games, then an equivalence check on
65 classes. The code was reverted.

- **Concentration barely moved.** The three positions solved carried a median 6.3% of the whole
  belief before and 9.4% after (Protect and Detect merged too), against an aim of 75%. Of the
  enumerated combinations they carried 38% before and 40% after. Few sets differ in nothing a
  fight shows: the spread is real (Kingambit's Chople Berry, Focus Sash, Black Glasses and Life
  Orb; Indeedee's Speed-down nature for Trick Room), multiplied over four hidden Pokémon.
- **And it was not sound.** Solving a class by its second-heaviest member instead of its heaviest:
  79% of 65 agreed within 0.02, 11 differed by more than 0.05 (one 0.12 against 0.87). What the
  signature missed: Grassy Glide (priority 0 in the dex, +1 in Rillaboom's own terrain), support
  and field moves (Helping Hand against Heal Pulse, Grassy Terrain), secondary effects (Rock
  Slide's flinch against Throat Chop), and defensive natures (Calm against Bold), because at
  `prune` 2 the solver still fills spare choices by score and the forced-win check reads every move.
- **Planning time** was unchanged (median 1.1 s, max 1.9 s).
- So closed-sheet doubles stay with the model. The check from the stands hides all four Pokémon,
  where the page hides two, so it is harsher than what the page faces.

## The race blended with HP share and the count

PLAN-endgame-doubles stage 3 (2026-10-01). At the horizon a doubles answer is about 95% the race
(mean leaf mass 0.95 in 2v2s), so the race's accuracy is most of a 2v2's.
`scripts/analysis/race_calibration.py` values the raw race on 3,542 training-split open-sheet games
at their first turn with two or fewer a side (2,109 of them 2v2s), never the held-out ones, and
compares maps out of fold (grouped 5-fold):

| map (training games, out of fold) | 2v2 log loss / Brier | 2v1 and 1v2 log loss / Brier |
| --- | --- | --- |
| `calibrated`, the live map: sigmoid(0.403 · logit(race) − 0.024) | 0.572 / 0.192 | 0.315 / 0.088 |
| refit by kind | 0.563 / 0.189 | 0.298 / 0.085 |
| HP share and count, no race | 0.640 / 0.224 | 0.318 / 0.094 |
| **`blend`: race, HP share and count** | **0.547 / 0.183** | **0.266 / 0.077** |
| `blend` plus 11 mechanics (Protect, support, speed control, drops, boosts, ...) | 0.542 / 0.181 | 0.259 / 0.076 |

- **`blend`** is sigmoid(0.289 · logit(race) + 0.818 · logit(HP share) + 0.797 · count lead +
  0.018): the race alone is too sure, and a count lead is worth more than the extra attacker it
  gives the race. By kind it adds nothing once HP share and the count are in.
- **What the race misses, by the residuals in 2v2s:** stat boosts (a side +4 stages ahead wins 13
  points more than predicted), its own drops never applied (Draco Meteor, Close Combat: a side
  carrying them is overrated by a few points), speed control and foe drops a little. Protect,
  redirection, Helping Hand, choice items, healing and status moves show nothing. Modelling all of
  them adds 0.004 in 2v2s.

**Held out, at the live configuration** (674 games, `--tag live_blend`, the same games as the live
run with `calibrated`; paired, grouped bootstrap):

| | log loss blend / calibrated / model | blend − calibrated | Brier blend / calibrated / model | blend − calibrated |
| --- | --- | --- | --- | --- |
| all | **0.407** / 0.421 / 0.513 | −0.014 [−0.030, +0.003] | **0.125** / 0.130 / 0.172 | −0.005 [−0.010, −0.000] |
| 2v2 (410) | **0.503** / 0.527 / 0.621 | −0.024 [−0.044, −0.004] | **0.162** / 0.169 / 0.217 | −0.007 [−0.014, −0.001] |
| 2v1 and 1v2 (264) | 0.258 / 0.257 / 0.347 | +0.001 [−0.025, +0.031] | 0.067 / 0.069 / 0.102 | −0.003 [−0.010, +0.005] |

- **2v2s improve on both scores; 2v1s and 1v2s are level.** Out of fold on training games 2v1s
  and 1v2s gained 0.049 at the position itself; one turn of search already takes most of what the
  count adds there. The engine beats the model in every kind.
- 2v2 calls at 95% or more are still too sure: 26 games at 0.976 won 0.885.
- **The page's live search uses `blend` from here** (`vgc.web.solving.LIVE`); `doubles.SEARCH`
  and the Kaggle reference runs keep `calibrated`.

**The race applying stat stages: tried, reverted.** The race was made to apply the stages its moves
change as it plays (a user's own drops and boosts, a foe's from a sure secondary, with Contrary,
Clear Body and its kin, Defiant, Competitive and Sheer Force), behind a flag so the existing answers
did not move (checked: identical with it off). On the same 3,542 training games, `blend` refitted on
that race was level: log loss +0.001 [−0.002, +0.005] overall, +0.003 in 2v2s, and +0.011
[−0.021, +0.044] on the 375 games whose race it changed. The race is mostly decided before a drop
matters, and the boost residual did not move with it.

## Boosts in the blend, and an answer that is not too sure

PLAN-endgame-doubles stage 3 (2026-10-01), the two misses `blend` left.

- **Boosts.** The race already sees stat stages in its damage and Speed, but the blend shrinks
  them with everything else: a side +3 or more ahead won about 10 points more than `blend` gave it.
  `blend_boosts` adds the net stages (p1's summed over its Pokémon standing, minus p2's):
  sigmoid(0.280 · logit(race) + 0.916 · logit(HP share) + 0.860 · count lead + 0.148 · stages +
  0.013). Out of fold on the 3,542 training games: log loss −0.008 [−0.013, −0.003] against
  `blend`, in 2v2s and in 2v1s and 1v2s alike. Offence, defence and Speed apart add nothing.
- **Too sure after the search.** Taking the best of noisy leaf values pushes the answer outward,
  which a horizon fitted at depth 0 cannot see. The live configuration with `blend_boosts` on 1,500
  training games (`race_calibration.py --answers 1500`, about an hour): 2v2 answers above 0.95
  averaged 0.978 and won 0.904. A temperature on the answer, sigmoid(0.813 · logit(answer) −
  0.009) (`doubles.TEMPER`): out of fold −0.013 [−0.024, −0.003], forced wins included (left out,
  or fitted by kind, it does worse).

**Held out at the live configuration** (674 games, `--tag live_blend_boosts`, scored `--temper`;
paired against `blend`, grouped bootstrap):

| | log loss | − `blend` | Brier | − `blend` |
| --- | --- | --- | --- | --- |
| `blend` | 0.407 | | 0.125 | |
| `blend` + temperature | 0.391 | −0.017 [−0.037, −0.001] | 0.124 | −0.001 [−0.003, +0.001] |
| `blend_boosts` | 0.400 | −0.007 [−0.014, −0.000] | 0.123 | −0.002 [−0.005, +0.001] |
| **`blend_boosts` + temperature** | **0.384** | **−0.024 [−0.045, −0.006]** | **0.122** | **−0.003 [−0.006, −0.000]** |
| model | 0.513 | | 0.172 | |

- By kind with both: 2v2s 0.485 (`blend` 0.503, −0.019 [−0.041, −0.001]; model 0.621); 2v1s and
  1v2s 0.227 (`blend` 0.258, −0.031 [−0.073, +0.001]; model 0.347). The engine beats the model
  in every kind on both scores.
- 2v2 calibration with both: 0.195 predicted won 0.185, 0.504 won 0.500, 0.815 won 0.812; the 16
  answers above 0.95 (0.970) won 0.875.
- **The page uses both** (`LIVE` with `race_doubles: 'blend_boosts'`, the answer and each move
  order's value through `doubles.temper`).

**Through the page's own path** (`scripts/analysis/page_path.py`: every 6th held-out game, 112, through
`solving.request` with warm solver processes on the laptop and nothing cached, the answer as the
page shows it within its 5 seconds): first answer median 1.3 s (max 3.3 s), final median 2.8 s
(p90 5.0 s, max 5.1 s); 93 of 112 had every move order searched in time, and the rest kept the quick
value for the late ones. The page's answer differed from the no-deadline one by 0.012 on average
(0.072 where an order was late), and scored log loss 0.347 against 0.343 with no deadline and
0.546 for the model on the same games: the deadline and tempering a quick value cost about 0.005.

