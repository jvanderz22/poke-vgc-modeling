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
