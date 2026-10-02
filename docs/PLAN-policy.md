# Phase 9: a policy that plays whole battles (EWP and search)

_Written 2026-10-02. Nothing built yet._ This is [PLAN-v4](PLAN-v4.md) step 2's first deliverable:
the stage plan, with the leaf and the bench worked out before anything is built. It builds on
[PLAN-endgame-doubles](PLAN-endgame-doubles.md), whose solver is this search's bottom layer.
Numbers marked _estimate_ are guesses until a stage measures them.

---

## Why

- **Step 3 needs a policy that predicts human results.** Heuristic self-play does not (AUC 0.512
  on 2,684 series, [phase6](phase6-findings.md)), and it measures that very precisely (split-half
  reliability 0.96). Matchup evaluation and team building (steps 6–7) wait on a policy that does.
- **The heuristic plays too sharply and too fast.** It ends a game in 5.81 turns, where played-out
  human games take 7.55, and 56% of its pairings land beyond 85/15 where human ones are coin flips.
  It scores each slot's choices with hand weights and never looks at the turn's outcome.
- **The machinery exists below two a side.** The doubles engine plays joint choices with targets,
  prunes to realistic play, samples chance at doubles scale, integrates Speed over four Pokémon,
  and has a horizon fitted on human games that beats the model there (log loss 0.384 against
  0.513). What it lacks is a bench: no switches, no replacements, no unrevealed Pokémon.

What "stronger" means here is the gate below. Whether stronger is also *more predictive of humans*
is step 3's question, and this plan does not get to assume it.

## The budget comes first, because step 3 sets it

The policy is worth building only at a cost step 3 can pay, so its design point comes from step 3's
power, not from the 45 s clock.

**Step 3's size.** Its outcome side is set by independent series. An AUC of 0.55 (weak, but enough
to build on) is detected with 92% power at 1,500 series and 79% at 1,000 (Hanley–McNeil standard
error). Its predictor side is set by battles per pairing, which attenuate the effect by
√(s² / (s² + 0.25/n)), where s is the spread of true simulated win rates across pairings. The
heuristic's s is 0.29. A policy that plays closer to humans will have a smaller s, and that is
where few battles per pairing costs most:

| s (spread of simulated WP) | 4 battles a pairing | 8 | 15 |
| --- | --- | --- | --- |
| 0.29 (the heuristic) | 0.76 | 0.85 | 0.91 |
| 0.15 | 0.51 | 0.65 | 0.76 |
| 0.10 | 0.37 | 0.49 | 0.61 |

So _about 1,500 pairings × 8 battles = 12,000 battles_; a pilot in stage 5 measures s and settles it.

**What that costs.** About 7.5 turns a battle, two decisions a turn: 15 decisions. At **1 second a
decision on one core**, 12,000 battles are 50 core-hours, an overnight run on the laptop's 8 cores.
At 3 seconds it is three nights on the laptop, or more on Kaggle, which runs a solver batch about
2.7× slower.

**So the design point is about 1 s a decision on one core, median.** The 45 s turn clock (the
gate) and the 5 s live answer (W4, principle 12) both follow from it. A deeper setting can exist
for the app; the policy that passes the gate at 1 s is the one step 3 runs.

## The design

`EWP(a) = Σ_b π_opp(b | o) · E_rng[WP(o′ | a, b)]`, one turn deep, as a matrix over pruned joint
choices, for each of a few determinizations of what the player cannot see.

### Choices, and realistic play with a bench

A slot's choices are its moves at each target (as now), plus a switch to each Pokémon in the back,
plus Mega Evolution where it is available. Before pruning, a 4v4 turn has _about 7 moves and 2
switches a slot, so about 80 joint choices a side_. Pruning, in two steps:

- **Per slot**, the endgame rules as measured (best attack at each foe, best priority attack,
  Protect while it works, Fake Out while it works, the rest by score to `k`), plus:
  - **one switch at most:** the Pokémon in the back that takes least from the foes' best attacks,
    and only when the Pokémon on the field is threatened (the same `threat` the scorer uses);
  - **Mega taken where it can be**, so the Mega and non-Mega versions of a choice are not both
    kept. Stage 3 measures how often people hold a Mega back, and this is revisited if often.
- **Per side**, the joint choices cut to the top `K` by the summed slot scores, after the overkill
  rule. Both sides use the same rules, so the answer is best play within realistic play.

### The opponent, π_opp

Two readings of the same matrix, and the gate chooses between them:

- **Nash**, as the endgame engine does: the minimax value of the pruned matrix. Hard to exploit;
  plays as if the opponent is good.
- **Fitted to people**: a softmax over the pruning scores, its temperature fitted on training-split
  human turns (stage 3). Plays as if the opponent is a ~1100-rated player, which is the population
  step 3 has to predict.

Both cost the same, since only the weighting differs. EWP against the heuristic itself is not a
candidate: it would be tuned to the opponent of the gate.

### Chance

Sampled per cell, stratified, as the doubles engine does (`search.sample`). PLAN-v4's "chance
enumerated, not sampled" is superseded: enumeration is exponential in the hits a turn, and a turn's
dice drawn after both sides choose is not the determinization that failed in the 1v1
(PLAN-endgame-doubles, stage 1).

New: **common random numbers across cells.** Today each cell's draws are seeded by its own choices,
so two of my choices are compared under different dice. Seeding by (node, draw) instead should cut
the noise in the differences that pick an action. Approximate, because different choices consume
the PRNG differently; measured in stage 1 as the variance of row differences, with and without.

### What the player cannot see: determinization

Each decision solves `D` positions, each a complete guess at the hidden parts, weighted:

- **Spreads, in every regime** (an open sheet hides the Stat Points). Speed drawn from the Speed
  prior and weighted by every turn order seen, as `doubles.speed_classes` does, extended to all
  Pokémon. The other stats from the spread prior (the budget spent, the unused attacking stat at 0).
- **Which Pokémon are in the back.** Preview shows six; until a Pokémon appears, which two of the
  unseen four were brought is unknown. Weighted by the served `bring` head given the six and what
  has been seen, or by usage if that head does not concentrate (top-4 overlap is 0.70 against
  usage's 0.67).
- **Sets, on closed sheets**, from `belief.sets.given`, as the doubles adapter does.

Nothing here touches a WP model's inputs: a guessed spread acts through the engine only. If the
model is ever the leaf, the opponent's spread is `None` in its input (principle 3).

### The leaf: worked out

What the leaf must do: value the position one turn after the root, so that the outcomes of
different choices are ranked correctly. Most of those positions still have three or four Pokémon a
side. The candidates:

| | Where it was measured | For | Against |
| --- | --- | --- | --- |
| The served WP model | Every turn of held-out games; passes calibration | Reads sets, field, turn | Does not follow what decides a fight (separation 0.07–0.15 against the solver's ≈ 0.95). A leaf that gives sibling outcomes the same number makes EWP flat. Each leaf needs an observation of a simulated state, built in Python: a process crossing per batch |
| HP share and the count | Nowhere above two a side | Free | Knows nothing of Speed, field or type matchups |
| **The damage race with reinforcements**, blended | Its ≤2 version beats the model (0.473 alone, 0.384 with the search) | Runs inside the solver at ~0 cost, knows Speed, Trick Room, Tailwind, spread moves and KOs | Untested above two a side. Ignores Protect, switching and status, which matter more early |

**The proposal: the race with reinforcements, blended, and the model as the bar it must beat.**
`melee` extended so a fallen Pokémon is replaced from the back (the one with the most damage
towards the foes on the field), with field effects expiring by their turns left (today the race
treats Tailwind and Trick Room as lasting), then blended with HP share over all four, the count
and the stages as `blend_boosts` is, and fitted by `race_calibration.py` on training-split games
at each turn with more than two a side. The model leads only if the race loses to it on held-out
games, and a test of the model at the leaf would then measure the cost of building observations
from simulated turns (the child's log through the `Observer` from the root's state).

**Two checks decide it (stage 3):**
- **Held-out log loss and Brier** at the first turn of each state kind (4v4, 4v3, 3v3, 3v2, 4v2)
  per regime, cluster bootstrap by group. What is hidden comes from the belief and the bring
  prior, never from what the replay reveals later (principle 7).
- **Spread across siblings** (principle 9): on a sample of roots, the spread of each leaf's values
  over the cells of the matrix. A leaf with near-zero spread cannot pick an action, however good
  its log loss.

Positions with two or fewer a side at the leaf use the same race (that is the doubles engine's
horizon). The full doubles engine runs only when the root itself is ≤2 a side, where it already
answers in about 2.8 s and leads the page.

### What a decision costs (_estimate_)

From the page's own path: a 2v2 at `prune` 2, eight draws, about 81 cells, in about 2–3 s, so
_about 3 ms a turn replayed with its leaf_. At 4v4:

| K a side | draws N | determinizations D | turns replayed | seconds |
| --- | --- | --- | --- | --- |
| 8 | 8 | 4 | 2,048 | ~6 |
| 6 | 4 | 2 | 288 | **~0.9** |
| 4 | 4 | 2 | 128 | ~0.4 |

So the design point is about K 6, N 4, D 2. Stage 1 measures the per-turn cost with a bench; the
gate says whether that is strong enough.

## Stages

Each stage ends in a check. Every new solver behaviour is a `search` key that is off by default,
so with it off the cached 1v1 and doubles answers stay bit-identical and `VERSION` does not move
(a bump means a depth-4 re-solve of the benchmark on Kaggle, practice 7).

### 0. The bar and the budget (½ day, laptop)

- **The bar.** The served models' log loss and Brier at the first turn of each state kind above
  two a side, per regime, on held-out games (the predictions exist). And the floor: HP share and
  the count, fitted on training games. If the floor already matches the model, the bar is low.
- **The budget table above, redone with this data**: turns per played-out game by regime, and how
  often each state kind occurs, so the cost of a battle is weighted by where its decisions fall.

### 1. The solver with a bench (2 days)

- **Position format.** Up to four a side: which are on the field, and per Pokémon in the back its
  HP, status, item consumed, and whether it has been out. Fainted Pokémon fainted in place, as now.
- **Choices.** Switches in `slotOptions` (the two slots never to the same Pokémon); Mega at the
  root and in search; `trapped` from the request (Shadow Tag, Arena Trap).
- **Requests inside a turn.** The end-of-turn replacement after a KO is both sides choosing at
  once: a small matrix (at most 2 × 2) valued by the leaf. A switch inside the turn (U-turn,
  Parting Shot, Eject Button, Eject Pack, Emergency Exit) is one side choosing: the best by the
  leaf.
- **Pruning** with the switch and Mega rules, and the side-level `K`.
- **Common random numbers** (`search.crn`).
- **Checks:**
  - with the bench off, cached 1v1 and doubles answers bit-identical (as stage 1 of the doubles
    plan: 60 of 60);
  - every listed choice accepted over a few hundred self-play turns with a bench;
  - one 4v4 and one 3v3 position hand-read through `debug`;
  - `stats`: turns replayed, cells, and ms a turn by state kind, which replaces the cost estimate;
  - CRN: variance of row differences on 20 roots, with and without.

### 2. Positions from a player's view (1½ days)

`vgc.wp.doubles.plan` extended above two a side, read from a `BattleState`. In self-play the
`SideView` also feeds an `Observer` from the same chunks and requests, so the policy sees exactly
what the app and the replays see, never the runner's battle.
- Own side exact, from the request. The opponent's revealed facts from the log, the rest from the
  determinization above. `D` positions, heaviest first, the rest as unsolved mass.
- **Checks:**
  - **no leak**: the positions built for p1 are identical whatever p2's hidden spread and back
    are (two runs of one battle with p2's hidden parts changed);
  - in self-play, where the truth is known: how often the true move order and the true back pair
    are among the `D` solved, and the value gap against the truth's own position;
  - planning time, which counts against the 1 s.

### 3. The leaf, and realistic play against what people chose (1½ days, laptop)

On human games, through stage 2's adapter (from the stands, as the endgame checks were):
- **The leaf**, as worked out above: the race with reinforcements fitted on training games, scored
  against the model and the floor on held-out games by state kind and regime, plus the sibling
  spread. Decides the leaf.
- **Pruning against people.** On held-out turns above two a side, how often each player's actual
  choice survives: per slot and joint, at `k` 2–3 and `K` 4–8, by kind of choice (attack, Protect,
  switch, Mega, status). A choice the log does not show (a flinch, full paralysis, sleep) is
  unknown, not a miss. A kind people choose often and pruning drops is fixed before stage 4.
- **π_opp fitted to people**: the softmax temperature on training-split turns, its held-out
  log-likelihood of human choices against a uniform choice over the kept set.

### 4. The policy (1½ days)

`vgc.policy.ewp.EWPPolicy`, a `Policy` like the heuristic.
- A decision: plan → `D` positions → the warm solver (`--serve`, one per self-play worker) → each
  position's matrix → combined by weight → the choice. Under Nash, the mixed strategy is sampled
  with the policy's seeded `rng`, so battles stay reproducible; under the fitted π_opp, the
  argmax of EWP.
- Roots with two or fewer a side go to the doubles engine's live search as it runs on the page.
- Forced replacements use the same machinery as any decision.
- **Team preview stays the heuristic's** for the gate, so the gate measures play, not brings. See
  the open questions.
- It writes an EWP table per decision (EWP, sampling error, worst reply): what W4 shows, and what
  the calibration check reads.
- **Checks:** a battle replays exactly from its seed; no invalid choices over 100 battles; the
  decision time by state kind on one core.

### 5. The gates (about 2 laptop-hours, or one Kaggle session)

Teams are human-corpus pairings, as in Phase 6. **Each pairing is played twice with the policies
swapped between the teams**, sides alternating, so team strength cancels. The unit is the pairing.

- **Against the heuristic: 500 battles (250 pairings).** It beats it when the whole 95% interval is
  above 0.5, and the point estimate must be at least 0.60. At 500 battles the interval is about
  ±0.045, so a true 0.60 clears 0.5 with power near 1, and the 0.60 is a margin rather than a
  significance bar: a true 0.62 meets it 80% of the time. Run for both π_opp readings.
- **Against a held-out opponent: 500 battles.** Not the heuristic and not π_opp: **the pruning
  scorer as a policy** (each slot's top-scored choice, the overkill rule), which is different code
  and different weights from `heuristic.py`. It holds when the interval is above 0.5. Plus random
  as a floor (≥ 0.95, the heuristic's is 0.984). This is the check VGC-Bench's "≈100% exploitable"
  asks for, at the strength this project can afford.
- **Latency:** median ≤ 1 s a decision on one laptop core (the design point), p99 under 45 s.
- **EWP against what happened:** the chosen action's EWP, binned, against the realised result in
  held-out self-play against the opponent π_opp assumes, with intervals. A test of the transitions
  and the leaf together. Under Nash, EWP is a lower bound against a weaker opponent, so a miss on
  the low side is expected there and reported as such.
- **Reported, not gated:** battles a second; turns a battle against 7.55 (human) and 5.81
  (heuristic); the share of pairings beyond 85/15; agreement with human choices on held-out turns.
- **Then the step 3 pilot:** 100 pairings × 16 battles, for split-half reliability and the spread
  s, which sizes step 3 from the table above.

Which π_opp ships: the one that passes both opponents; if both do, the fitted one, since step 3 is
about predicting people.

## Left out, unless a gate asks for it

- **Depth 2.** Only if depth 1 fails the gate and the budget allows it. The doubles work found the
  horizon worth more than depth: a better horizon beat a wider search, and a wider search on the
  same horizon added nothing.
- **Behaviour cloning.** Deferred in PLAN-v4: the corpus tops out at a 1578 rating.
- **Search over brings and leads.** A separate question, and preview is where the corpus says
  least.
- **The model at the leaf**, unless stage 3 says it beats the race.

## How it fits

- **Step 3** runs this policy, at the size the pilot gives.
- **W4** (the EWP action table) is stage 4's table on the Battle page, within 5 s at the design
  point. It waits on step 3, as the plan already says.
- **A by-product for the page:** stage 3 scores the race above two a side against the model on
  held-out games, by state kind and regime. If it wins somewhere, that is principle 11's evidence
  for the engine to lead there too, and the page could follow.

## Risks

| Risk | What happens |
| --- | --- |
| The race above two a side loses to the model and the model is flat across siblings | Neither leaf ranks actions; the policy is no better than its pruning. Stage 3 finds this before stage 4 is built, and the sibling spread says which way to go |
| Pruning drops what people do (switches, held Megas, status) | Measured in stage 3 by kind; the rules are search settings |
| 1 s a decision is not strong enough to pass | Spend the budget where the gate says (K, N or D), or pay step 3 in Kaggle nights instead |
| Strong but no more human-like, so step 3 fails again | The deterministic stack and the endgame engine stay the floor. The fitted π_opp and the turns-a-battle report are the early warnings |
| A leak from the runner's battle into the player's positions | Built from the `Observer` only, and stage 2's no-leak check |
| The determinization of the back is wrong early in the game | Weighted by the bring head and by what has been seen; reported as unsolved mass, and in self-play scored against the truth |

## Where it runs

One Kaggle CPU session (4 cores, 12 hours) does about 4–5 laptop-hours of work
([cloud-compute](cloud-compute.md), section C). At the design point:

| Work | Compute | Where |
| --- | --- | --- |
| Stages 0–4: building, the checks, the leaf fit (race leaves cost milliseconds) | minutes to an hour each | Laptop: it is development, and the checks are the verdicts |
| Stage 3's by-product: the one-turn search at held-out roots above two a side | about an hour, _estimate_ | Either. The existing `kaggle_solve.sh` path takes bench positions unchanged |
| Stage 5's gates: ~2,200 battles with one EWP side (~9 s each) and the pilot, 1,600 with two (~18 s) | ~14 core-hours, about 2 laptop-hours | One Kaggle session, to keep the laptop free |
| **Step 3's run**: ~12,000 battles with two EWP sides | ~60 core-hours, about 7½ laptop-hours | **Two Kaggle sessions side by side, one night**. The run that pays for the offload |

**Self-play on Kaggle is new.** `kaggle_solve.sh` ships only Node, the solver and a slice of
Showdown. A `kaggle_selfplay.sh` on the same pattern (½ day, in stage 5) would also ship the `vgc`
package, poke-env and its wheels, the team pairings and the policy settings. It should split a run
by battle index, since a battle's seed is (run seed, index) and a run's results do not depend on
how it is split, and merge the outputs. As with the solver, nothing merges unless the code matches
and a sample of battles replays here to the same outcome digest. Scoring stays on the laptop, so
nothing on Kaggle can move a verdict.

Only Kaggle is used, so the $0–5 budget is untouched.

## Estimate

About 9 working days plus a night or two on Kaggle: stage 0 ½ day, stage 1 2 days, stage 2 1½
days, stage 3 1½ days, stage 4 1½ days, stage 5 2 days (including the Kaggle self-play script). Two answers come
early: stage 0 says how high the bar is, and stage 1's `stats` say whether 1 s a decision buys
K 6, N 4, D 2.

## Open questions for the user

1. **Brings and leads in the gate and in step 3.** The heuristic's (proposed for the gate), the
   `bring` head, or, for step 3 only, the brings the humans actually made in that game (known at
   preview, not the result, but chosen by the players being predicted).
2. **The gate's reading.** Proposed: interval above 0.5 *and* point at least 0.60.
3. **The held-out opponent.** Proposed: the pruning scorer as a policy.
4. **The 1 s design point**, which follows from step 3's power at 8 battles a pairing.
