# Phase 9 findings: a policy over whole battles

Measurements behind [PLAN-policy](PLAN-policy.md), newest last.

---

## The bar above two a side, and what a battle costs (stage 0)

2026-10-02, `scripts/analysis/policy_bar.py` (under a minute on 8 workers; rows in
`data/analysis/reg_mc/policy_bar_rows.jsonl`, the result in `policy_bar.json`).

Each human game at the first turn of each state kind with more than two Pokémon left on a side,
from the stands. Two predictors:
- **the model**, the served one for the regime, calibrated as the page shows it (`wp-v1f-idp5`
  open, `wp-v1d-sw-split-small` closed);
- **the floor**, sigmoid(a · logit(HP share) + c · count lead + e · net stat stages + d), fitted
  per regime on training-split games at the same turns (8,773 open games, 6,106 closed). HP share
  is over every Pokémon left, those not yet seen at full HP, so nothing comes from later in the
  replay.

Scored on held-out games, floor minus model, cluster bootstrap by group:

| | states | log loss model / floor | floor − model | Brier model / floor |
| --- | --- | --- | --- | --- |
| **open, all** | 5,496 (793 groups) | 0.548 / 0.556 | +0.008 [−0.001, +0.017], not distinguishable | 0.189 / 0.193, not distinguishable |
| open 4v4 | 1,703 | 0.686 / 0.696 | +0.010 [+0.002, +0.018], model better | 0.246 / 0.251 |
| open 4v3 | 1,218 | 0.584 / 0.599 | +0.015 [−0.001, +0.031] | 0.199 / 0.206 |
| open 3v3 | 735 | 0.639 / 0.648 | +0.010 [−0.011, +0.030] | 0.224 / 0.229 |
| open 3v2 | 924 | 0.510 / 0.513 | +0.002 [−0.018, +0.022] | 0.169 / 0.171 |
| **closed, all** | 3,271 (994 groups) | 0.547 / 0.568 | +0.020 [+0.006, +0.034], model better | 0.187 / 0.196, model better |
| closed 4v4 | 1,025 | 0.674 / 0.695 | +0.021 [+0.007, +0.035], model better | 0.241 / 0.251 |
| closed 4v3 | 732 | 0.585 / 0.626 | +0.041 [+0.020, +0.062], model better | 0.200 / 0.217 |
| closed 3v3 | 423 | 0.620 / 0.644 | +0.024 [−0.004, +0.052] | 0.216 / 0.226 |
| closed 3v2 | 525 | 0.520 / 0.525 | +0.005 [−0.020, +0.030] | 0.172 / 0.175 |

(4v2, 4v1 and 3v1 are not distinguishable in either regime, and are nearly decided: log loss 0.05–0.37.)

- **On open sheets the bar is low.** Above two a side, the model is worth 0.008 nats more than HP
  share and the count, and that is not distinguishable. The one kind where it is better is 4v4,
  where both are close to a coin flip (0.686 and 0.696, against 0.693 for 0.5).
- **On closed sheets the model knows more than the count**, by 0.02 nats overall and 0.04 at 4v3.
  The floor knows how many Pokémon are left, not which ones. The open-sheet model could know that
  too and does not show it, so this is worth a look before reading too much into it.
- **What it means for the leaf.** The race with reinforcements has to beat a number that is mostly
  the count. Below two a side the calibrated race alone beat the model by 0.04 nats (0.473 against
  0.513), so it is a fair bet, and on closed sheets the bar is 0.02 nats higher.

**The budget**, held-out games that were played out:

| | games | turns a game | turn points a game (turns and replacements) |
| --- | --- | --- | --- |
| open | 1,012 | 7.47 | 10.35 |
| closed | 541 | 7.69 | 10.61 |

Share of turns by state kind (open; closed within 0.02 of it): 4v4 32%, 4v3 17%, 3v2 12%, 3v3 11%,
4v2 5%, 3v1 4.5%, 4v1 1.4%; two or fewer a side 18% (2v1 7%, 2v2 6.5%, 1v1 4%).

- **A side decides about 9 times a game:** 7.5 turns and about 1.5 replacements. A replacement is
  a matrix of at most 2 × 2 valued by the leaf, so nearly free. That is about 15 full decisions a
  battle, as the plan assumed.
- **The 18% at two or fewer a side do not fit the 1 s budget as the page runs them.** The page's
  doubles answer takes a median 2.8 s across three solver processes. Weighted by their share, that
  would roughly double the cost of a battle. In bulk play, roots at two or fewer a side get one
  move order and a 1 s cap, with the quick value (forced-win check and race) when that runs out.

## The solver with a bench (stage 1)

2026-10-02. The solver now takes positions with Pokémon in the back (`bench: {p1, p2}`; a field
about a Pokémon lists those on the field, then those in the back), and new search settings, all
off by default: `switches`, `mega`, `side_k` (a side's pairs cut to the K with the highest summed
scores), `crn`, `salt`, `fast_dice` and `melee_runs`. Replacements (after a KO, or in the middle of
a turn after U-turn, Parting Shot, an Eject Button or Eject Pack) are their own node: both sides'
ways to fill the empty slots, valued before the horizon and costing no depth. `walk` plays random
listed choices on from a position with real dice and reports any the simulator rejects.

**Nothing that was there moved.** Three samples of 60 cached answers (40 doubles, 20 1v1, nodes
≤ 3,000), re-solved without the cache, matched bit for bit on value, leaf mass, nodes, matrix,
choices and sampling error, except three rows. Those three were cached under `race_stages`, the
experiment PLAN-endgame-doubles dropped, whose key no longer does anything; the solver at HEAD
does not reproduce them either. They are stale, not changed. `VERSION` stays at 1.

**Every listed choice is one the simulator accepts.** 480 positions from pool teams (4v4 to 2v3,
a third of them on the first turn out), six walks of up to 30 turns each: 2,880 games, about
21,700 steps, 5,400 replacements, 11,400 switches and 4,000 Megas, none rejected. Four faults
were found and fixed on the way:
- who is waiting has to be read before either side chooses: the side owing a mid-turn replacement
  finishes the turn when it chooses, and the other side then has a move request, not a wait;
- with two Pokémon on the field able to Mega Evolve, offering only the Mega versions left no legal
  pair (a side Mega Evolves once), so both versions are offered then;
- Revival Blessing's request is a replacement asking for a fainted Pokémon;
- the walk has to clear the battle log each turn, as the search does, or Showdown stops it at
  1,000 lines (the walk's fault, not the search's).

**What a decision costs** (25 positions from pool teams, 4v4 to 3v2, one guess at the hidden
parts; `prune` 2, 4 draws, `fast_race`, `fast_dice`, replacements searched, `blend_boosts` at the
horizon; a warm `--serve` process):

| K a side | median | p90 | max | turns replayed (median) |
| --- | --- | --- | --- | --- |
| 4 | 0.44 s | 0.60 s | 1.1 s | 104 |
| 6 | 0.78 s | 1.10 s | 1.9 s | 201 |
| 8 | 1.20 s | 1.74 s | 3.0 s | 323 |

- **About 3.7–4.0 ms a turn replayed**, against the 3 ms estimated, and **replacements add about
  40% to the turns**, which the estimate left out. A cold process adds about 0.6 s; the policy
  keeps its solvers warm.
- **So two guesses at K 4–6 cost a median 0.9–1.6 s a decision** above two a side: near the 1 s
  design point, not over it by a multiple.
- **Where the time went, and what was cut** (one 4v4, K 6): replacement cells were sampled four
  times though bringing a Pokémon in rarely rolls dice. Enumerated first, as a 1v1 turn is, they
  took the turns replayed from 1,080 to 602. Showdown's PRNG (ChaCha20) was a fifth of what was
  left; `fast_dice` (sfc32 for the race and the sampled draws) took the position from 3.1 s to
  2.4 s cold. Racing 16 times instead of 64 adds little once the race reads its damage from tables.

**Common random numbers do not help.** Against a 32-draw reference on 20 roots, the RMS error of a
difference between two rows of the matrix was 0.0416 with them and 0.0409 without. Different
choices use the dice differently, and stratified draws already take most of the noise out of a
cell. `crn` stays off.

**Four draws are noisy for choosing.** In the same check, the row a best response to the
reference's reply would pick matched the 32-draw reference's pick in 13 (with CRN) and 15
(without) of 20 roots. Whether more draws or more choices buy more strength is for the gate to say
(stage 5), but it is the first place the budget would go.

## Positions from a player's view (stage 2)

2026-10-02. `vgc.policy.view`: a `PlayerView` is an `Observer` for one side fed its own channel and
requests, its own six as built, and the opponent's open sheet. `plan` builds the positions one
decision solves: every Pokémon left, the two on the field then the back, with what is hidden
guessed. Which of the opponent's unseen sheets are in the back is weighed by how often each species
is brought when it is on a sheet (`scripts/analysis/bring_rates.py`: 23,830 training-split sides,
rates 0.56–0.80 around a base of 0.67). The move order on the field comes from
`doubles.speed_classes`, now able to order only those on the field and give the back its commonest
investment. The heaviest two (back, move order) are solved. `doubles.facts` and `speed_classes`
gained an optional argument each, and with it left out their output is unchanged. The solver
gained `megaUsed`, for a side whose Mega has fainted.

**Nothing leaks** (`tests/test_policy_view.py`). One battle traced three ways (as played, with p2's
unrevealed back given other Stat Points, and with p2 bringing two other Pokémon to the back):
while p1's channel is the same, p1's positions are identical. The game reveals p2's back at the
end of turn 1, so this compares only the first one or two decisions. It is a guard more than a
measurement: the positions are a function of the view by construction. (The runner's `trace` gained
`partial`, for an input log changed by hand that stops fitting its battle.)

**Against the truth**, from heuristic self-play on pool teams with open sheets
(`scripts/analysis/policy_view_check.py`). 150 battles: 1,138 of 1,172 move decisions above two a
side were built. Not built: 16 for Revival Blessing, 16 for a volatile the solver cannot set up
(Protean-style type change, confusion, Throat Chop, Supreme Overlord's count), 2 for sleep.

| | decisions | true back among the 2 solved | weight on the true back | true move order solved | weight unsolved | planning, median / p90 |
| --- | --- | --- | --- | --- | --- | --- |
| all | 1,138 | 41% (of 908 with an unseen back) | 0.23 | 66% | 0.48 | 99 / 202 ms |
| 4v4 | 356 | 30% | 0.16 (uniform: 0.17) | 54% | 0.70 | 120 / 287 ms |
| 3v3 | 154 | 52% | 0.32 | 68% | 0.45 | 99 / 127 ms |
| 3v2 | 111 | (none unseen) | | 81% | 0.12 | 98 / 127 ms |

And on a second run of 80 battles, every other decision solved (one-turn search at the design
point) and set against the true position (true back, true spreads), with the true position solved
again with other dice as the noise floor:

| | valued | value gap, mean / median | gap over 0.1 | same move chosen |
| --- | --- | --- | --- | --- |
| the guessed positions | 302 | 0.073 / 0.037 | 85 | 147 (49%) |
| the noise floor | 302 | 0.012 / 0.002 | 2 | 239 (79%) |
| 4v4, guessed | 126 | 0.089 / 0.057 | 44 | 47 (37%) |
| 4v4, floor | 126 | 0.012 / 0.003 | 0 | 94 (75%) |

- **What is hidden changes the move in about half the decisions, against a fifth from the dice
  alone.** Some of that cannot be removed: the player does not know either. But two parts of it
  can be priced.
- **The bring prior is no better than uniform at 4v4** (0.16 on the truth against 0.17). Species
  rates say little about which four of six a team brings, and the heuristic opponent brings by its
  own calculation, not as people do. Later in the game the rest of the six is often seen, and the
  guess improves (0.32 at 3v3).
- **Two positions carry 30% of the weight at 4v4**, against 88% at 3v2. The start of a game is
  where more positions (D) would buy most, and where the budget is tightest.
- The floor covers the sampled turns only: the race at the horizon draws the same dice either way.
- Planning costs a median 0.1 s of the 1 s a decision.

## The leaf, the model against the count, and pruning against people (stage 3)

2026-10-02. Four checks on open-sheet human games, each fitted on training-split games and scored on
held-out ones (cluster bootstrap by group).

### Does the model add anything to the count? (`model_vs_count.py`)

At every turn above two a side, the floor (HP share, count lead, net stages), the served model, and
both together, the two fits on the validation split (20% of training groups, which the model did
not train on):

| | rows (groups) | log loss model / floor / both | both − floor | both − model |
| --- | --- | --- | --- | --- |
| open | 9,926 (793) | 0.5745 / 0.5809 / 0.5713 | −0.0097 [−0.016, −0.003], better | −0.003, not distinguishable |
| closed | 5,921 (994) | 0.5664 / 0.5893 / 0.5624 | −0.027 [−0.043, −0.010], better | −0.004, not distinguishable |

- **On open sheets the model knows about 0.01 nats more than the count**: real, and too small to
  show against the floor alone (+0.006, not distinguishable). Fitted together, the weight on the
  model is 0.69, with HP share 0.64 and the count 0.36.
- **On closed sheets the model contains the count**: together, the count's weights go slightly
  negative.

### The leaf: the race with the back (`bench_race.py`)

`meleeBench` (`race_bench`) races everything left: a fallen Pokémon's slot is filled at the end of the
turn from the back, spread damage takes its 0.75 only against two, and Tailwind and Trick Room run out
by their turns left. Each game at the first turn of each kind above two a side, from the stands,
both backs guessed by bring rate (never from later in the replay), the heaviest four (backs × move
order) raced and averaged. 26,635 training states, 5,144 held out (790 groups; 2,164 dropped for
a volatile, sleep or Revival Blessing).

| | log loss model / floor / raw race / race blend | race blend − model |
| --- | --- | --- |
| **all** | 0.554 / 0.562 / 1.037 / **0.548** | −0.006 [−0.016, +0.004], not distinguishable |
| 4v4 | **0.686** / 0.696 / 1.286 / 0.702 | +0.016 [+0.005, +0.027], model better |
| 4v3 | 0.588 / 0.601 / 1.089 / 0.579 | −0.008, not distinguishable |
| 4v2 | 0.347 / 0.353 / 0.667 / 0.330 | −0.017, not distinguishable |
| 3v3 | 0.639 / 0.648 / 1.222 / 0.621 | −0.018, not distinguishable |
| 3v2 | 0.509 / 0.513 / 0.927 / **0.478** | −0.032 [−0.054, −0.011], race blend better |

- **The blend: sigmoid(0.137 · logit(race) + 1.725 · logit(HP share) + 0.460 · count lead + 0.100 ·
  stages + 0.022).** It beats the floor by 0.014 overall and ties the model; better than the
  model at 3v2, worse at 4v4.
- **The raw race is far too sure** (log loss 1.04), as it was at two or fewer a side, and its weight
  in the blend is small. At 4v4, before anything has fallen, a race that leaves out Protect, Fake Out
  and speed control is worse than no race.
- **The leaf is the race blend** (`race_doubles: 'policy'`: this blend with a back, `blend_boosts`
  without). The model is no better overall and would need an observation built for every leaf.

### Does the leaf tell siblings apart? (`leaf_spread.py`)

30 positions from pool teams, one-turn search at the policy's settings, with the race blend and with
the floor at the horizon: matrix spread a median 0.12 and 0.13; the gap between p1's best and second
choice against p2's equilibrium reply a median 0.003 and 0.001; the same choice in 20 of 30. Neither
leaf is flat. The tiny gaps say many choices are near-ties, which is what makes four draws noisy for
choosing (stage 1).

### Pruning against what people chose (`prune_vs_people.py`)

Every turn above two a side of all held-out games and 4,000 training games: 36,237 decisions, the
choice read from the log (a switch before any move, a Mega, the slot's move and its target; flinches,
sleep and blocked moves unknown), against the solver's lists for the position with the decider's
own back as brought.

Today's pruning (`prune`), the share of people's choices it keeps, per Pokémon:

| kind | choices | kept at k 2 | kept at k 3 |
| --- | --- | --- | --- |
| attack | 27,348 | 74% | 78% |
| switch | 8,285 | 36% | 36% |
| Protect | 7,156 | 98% | 98% |
| **status** (Tailwind, Trick Room, Follow Me, …) | 6,853 | **5%** | 19% |
| Mega | 4,586 | 58% | 60% |
| Fake Out | 2,866 | 100% | 100% |

- 1,117 choices were not listed at all: the player held the Mega back (724 of them Protects), and
  the list Mega Evolves wherever it can.
- Of people's pairs, 37% survive the per-Pokémon cut and 28% the side's top 6 (31% at 8).

**A model of people choosing** (`vgc.policy.people`): a conditional logit per Pokémon over what the
solver says each choice is (damage, KO, priority, spread, Protect and the danger it is in, Protect
again, Fake Out while it works, speed control and whether it is already up, redirection, Helping
Hand, screens, disruption, status inflicted, setup, healing, a switch and the HP it saves, Mega).
Fitted on 33,468 training choices, scored on 24,839 held out: log-likelihood 1.833 against 2.062 for
a uniform choice; the person's choice is its first 34% of the time, in its top 3 69%. Its largest
weights: damage +2.14, Protect when threatened +1.37, Protect again −1.32, setting up what is already
up −1.32, switching for HP saved +0.92, spread +0.83, Mega +0.83. Per kind, its top 3 keeps status
moves 79% of the time (against 19%), switches 38%, attacks 69%, Protect 82%, Mega 70%.

**The union** (each Pokémon keeps what `prune` 2 keeps and the model's top 3, Mega both ways, the
side's pairs ranked by the model): 68% of people's pairs survive the per-Pokémon cut, and **35% the
top 6, 43% the top 8, 54% the top 12** (against 28%, 31% and, untested, for `prune`). Built as
`prune_by: 'people'` with `mega: 'both'`; the policy's default. The summed scores are also the
opponent model fitted to people: P(pair) ∝ exp(summed score) over the K kept.

**What it costs** (stage 1's 25 positions, warm, one guess, N 4, the race with the back at the
horizon): K 4 median 0.43 s, K 6 0.93 s, K 8 1.77 s; about 5 ms a turn against 3.8 (the features,
and the longer race). Two guesses at K 6 are about 1.9 s a decision; at K 4 about 0.9 s.

---

## The policy (stage 4)

2026-10-02, `vgc.policy.ewp` (`EWPPolicy`, and `ScorerPolicy`, stage 5's held-out opponent),
`scripts/analysis/policy_check.py` (about 4½ minutes for 100 battles on 4 workers; the results in
`data/analysis/reg_mc/policy_check_people-k4.json` and `-k6.json`).

**What a decision is.** `view.plan` gives the positions (two guesses at the opponent's back and
spreads above two a side, one move order at two or fewer). The heaviest is solved first and fixes
the player's rows; the others solve those same rows (`search.root_keep`), so every guess can be
combined row by row (871 of 871 decisions did). Each answer carries each kept choice's people score
and each cell's variance (`search.root_detail`). Then:
- **nash**: the guesses are one game in which the opponent knows which guess is true: the player's
  rows against a column for every combination of replies, C[a, (b₁, b₂)] = Σⱼ wⱼ Mⱼ[a, bⱼ], solved
  as a linear program and sampled with the battle's seeded rng;
- **people**: each guess's opponent chooses as the people model says, P(b) ∝ exp(score), and the
  policy plays the argmax of EWP.
- A **replacement** is a root of its own (`setup.empty`: the fainted Pokémon's slot holds a filler
  and the root asks for replacements, mid-turn, as the simulator does after KOs), each way to fill
  the slots valued by the turn after it. When both sides lost a Pokémon, the opponent's choice is
  in the matrix too.
- The record of each decision is the EWP table: every row's EWP, sampling error and worst reply.

**The checks:** 100 battles against the heuristic, 50 human-corpus pairings each played twice with
the policies swapped, open sheets, 4 battles at once on the laptop.

| | K 4 (the policy) | K 6 |
| --- | --- | --- |
| invalid choices | **0** | 6 (before the two fixes below) |
| errors | 0 | 0 |
| battles replayed from their seeds, same inputs | **4 of 4** | 4 of 4 |
| decisions searched | 871 of 894 (97.4%) | 759 of 783 |
| a decision, median / p90 / p99 | **0.99 / 2.1 / 4.6 s** | 2.1 / 4.1 / 8.8 s |
| at 4v4 | 1.5 s | 2.9 s |
| at a replacement, both sides, 3v3 | 4.1 s | 8.0 s |
| at 2v2, 1v1 | 0.5 s, 0.2 s | 1.0 s, 0.2 s |
| won against the heuristic (not the gate) | 0.70 | 0.75 |
| battles a second, one side searching | 0.38 | 0.22 |

The times are with four battles running at once, which is how bulk play runs. The two win rates are
not distinguishable at 100 battles (±0.09), so the policy keeps K 4, which meets the 1 s median,
and the gate says whether that is enough.

**Fallbacks** (the heuristic plays the turn): 23 of 894. A switch in the middle of a turn (U-turn,
Parting Shot, Eject Button: the solver's root cannot be mid-turn) 9; No Retreat, Throat Chop or a
type change, volatiles the solver does not set up, 10; sleep and freeze, whose counters are hidden,
4. No timeouts.

**Found and fixed on the way:**
- **A locked move** (the charging turn of Electro Shot, a Hyper Beam recharge): the request lists
  the one move without a target, and the battle refuses a choice that names one. The solver does
  not know the lock, so the policy now writes the choice as the request asks.
- **PP.** Two long battles (20 and 34 turns) ran a Pokémon out of a move's PP, which the solver
  does not track, and the policy chose it. The player's own request shows its PP, so a position now
  says which moves are out (`setup.nopp`), and the solver does not offer them. Struggle is written
  like a lock.
- Both are new position keys, off unless present: 60 cached answers re-solved without the cache
  match bit for bit except two stale `race_stages` rows, which the committed solver gives the same
  new value for.

**Where it departs from the plan:**
- **No 1 s cap at two or fewer a side.** A cap on wall time makes a battle depend on the machine.
  The search's size is the budget instead, so a battle is a function of its seed, and those roots
  answer in 0.2–0.5 s anyway. A 30 s timeout is the only clock, and none fired.
- **Those roots use the policy's own search**, people pruning and the race horizon, not the page's
  (`prune` 2, the forced-win check): the people reading needs the people scores, and the forced-win
  check answers without a matrix.
- **Replacements cost the most**: each way to fill the slots is a full turn searched, four at a
  double replacement. They are 18% of decisions (158 of 894), and move the p99 more than the median.

---

## The gates and the pilot (stage 5)

2026-10-02 to 03, `scripts/analysis/policy_gate.py` (verdicts in
`data/analysis/reg_mc/policy_gate.json`), played on Kaggle through `scripts/cloud/kaggle_selfplay.sh`
except the random floor and the latency run, played on the laptop. Every Kaggle run was merged only
after its three shortest battles replayed on the laptop to the same inputs, choice by choice (18 of
18 did). Pairings are human-corpus pairings, open sheets, each played twice with the policies
swapped between the teams; the unit is the pairing, intervals by bootstrap over pairings. The policy
is K 4, two guesses above two a side.

| | against the heuristic (≥ 0.60, interval above 0.5) | against the scorer (interval above 0.5) | against random (≥ 0.95) |
| --- | --- | --- | --- |
| **people** | **0.678** [0.640, 0.716] **passes** | **0.692** [0.654, 0.730] **passes** | 1.00 (50 pairings) passes |
| nash | 0.584 [0.546, 0.622] fails (under 0.60) | 0.560 [0.520, 0.600] passes | 0.98 [0.95, 1.00] passes |

250 pairings each against the heuristic and the scorer, 50 against random. **The people reading
ships**: it passes both opponents, and step 3 is about predicting people. It also beats the scorer by
more than Nash does (0.69 against 0.56): playing as if the opponent is a ~1100 player pays against
players that are not minimax.

**Latency** (laptop, one battle at a time, 20 battles, 166 decisions): a median **0.74 s**, p99
3.1 s, the longest 3.6 s. Passes (median ≤ 1 s, p99 under 45 s). With four battles at once the
median is 0.9–1.0 s; on Kaggle's CPUs 1.8–2.4 s.

**EWP against what happened** (the chosen row's EWP, binned, against the result):

| EWP of the choice | against the heuristic: won | against the scorer: won | against itself (pilot): won |
| --- | --- | --- | --- |
| 0.0–0.1 | 0.075 | 0.074 | 0.017 |
| 0.2–0.3 | 0.33 | 0.33 | 0.15 |
| 0.4–0.5 | 0.50 | 0.60 | 0.35 |
| 0.5–0.6 | 0.59 | 0.66 | 0.41 |
| 0.6–0.7 | 0.74 | 0.77 | 0.59 |
| 0.8–0.9 | 0.88 | 0.89 | 0.82 |
| 0.9–1.0 | 0.95 | 0.97 | 0.96 |
| ECE / Brier | 0.048 / 0.142 | 0.056 / 0.139 | 0.062 / 0.137 |

The gate as planned (in self-play against the opponent π_opp assumes) cannot be run: π_opp assumes
people, and nothing in self-play plays as people do. What the three runs show instead is EWP
bracketed from both sides, as it should be if it is right about a people-strength opponent: against
weaker opponents the policy wins more than its EWP says (by up to 0.15 in the middle bins), and
against itself, stronger than people, less (by 0.07–0.14). Part of the miss against itself is the
winner's curse of taking the argmax of noisy rows. Both ends hold. Not a pass, and not a sign
of a broken leaf.

**The pilot:** the policy against itself, 100 pairings × 16 battles (1,600, two Kaggle sessions
of 1.7 and 2.6 hours, no errors).

| | |
| --- | --- |
| split-half reliability (8 against 8) | r = 0.921, **0.959** at 16 (Spearman–Brown) |
| spread of the pairings' win rates, noise removed | **s = 0.356** |
| pairings beyond 85/15 | 56% (the heuristic: 67%) |
| turns a battle | **7.31** (humans 7.55, the heuristic 5.81) |
| decisions the heuristic made instead | 3% (Revival Blessing and Pawmot's type change most) |

So step 3's design holds: at s 0.356 the attenuation is √(s² / (s² + 0.25/n)) = **0.90 at 8 battles
a pairing** (0.82 at 4), better than the heuristic's 0.85. 1,500 pairings × 8 battles = 12,000
battles with both sides searching: on Kaggle about 2.2 hours a 800-battle session, so about 33
session-hours, three sessions side by side for one night. The policy plays battles as long as people
do, but its matchups are still lopsided. Whether its win rates track human results is step 3's
question.

**Agreement with people** (held out, `policy_vs_people.py`, 1,557 decisions of 199 groups, from the
stands as stage 3 built them, one guess): the policy's choice is the human's joint choice **11.5%**
[10.0, 13.1] of the time, against 7.8% [6.5, 9.2] for the people model's own first pair and 2.0%
for a uniform choice; the human's pair is among its four rows 26% of the time. Searching moves the
policy towards what people do, not away from it.

**Found by the gates, and fixed after them:**
- **A hidden trap.** The solver's root never worked out trapping (Shadow Tag, Arena Trap), so it
  offered switches the battle refuses. The battle does not count a refused switch as invalid (the
  trap is hidden), and the policy chose the same switch again until the 2,000-decision cap: 5 of
  the 2,200 gate battles errored that way (scored as the one battle of their pairing that finished).
  Now the solver runs the simulator's own trap check where switches are searched, a position carries
  the `trapped` its request shows, and the runner lets the simulator choose after three refusals. The
  pilot ran with the fix: no errors in 1,600.
- **A failed battle's decisions** were left to the next battle's record by `selfplay._play`, which
  had muddled the calibration of the runs with an error. Each decision names its battle; scoring
  keeps a battle's own, and `_play` now clears them first.
- On Kaggle the code archive arrives unpacked and the file listing is paged, so a push is awaited by
  a stamp file that sorts first, and the kernel's slug cannot be its dataset's.

## The policy's numbers against human games (PLAN-v5 step 4, 2026-10-04)

Move advice on the page would show the policy's rows with their EWP. Before that, the question is
whether those numbers say anything about human games. `scripts/analysis/ewp_vs_people.py` uses
`policy_vs_people.py`'s positions: 800 held-out open-sheet games, every turn with more than two a
side, from each side's view, where the log shows the human's joint choice whole. That gives 3,024
positions in 369 series. Each is solved with the policy's search. The human's choice was among the
policy's rows 28% of the time; otherwise it was solved again with that choice kept at the root
(`root_keep`), so every position has a value for what was played. That value is set beside the
served WP model's number at the same turn, from the stands. Result:
`data/analysis/reg_mc/ewp_vs_people.json`.

| for this side's result | AUC | log loss (raw) | log loss, recalibrated out of fold |
| --- | --- | --- | --- |
| WP model, before the turn | 0.709 [0.664, 0.751] | 0.6205 | 0.6195 |
| EWP of the human's choice | 0.728 [0.693, 0.762] | 0.6084 | 0.6023 |
| EWP of the policy's own choice | 0.734 [0.699, 0.768] | 0.6205 | |
| WP and EWP of the choice, together | | | **0.5948** |

**The gate fails, narrowly.** Recalibrated, the EWP of what the human did against the WP model is
−0.017 [−0.039, +0.004]: better on the point, but the interval reaches zero.

**As a value of the position, it adds to the model.** Stacked with the WP number it improves on it
by −0.025 [−0.039, −0.011]. The policy's one-turn search, valued at the leaves by the race with
reinforcements, knows something about these positions that the model does not.

**As a ranking of actions, it shows nothing.** The gap between the policy's top row and the human's
choice is zero in 36% of positions and 0.07 on average. Beside the WP number it has the right sign
(−0.11) but adds nothing: +0.0003 [−0.0000, +0.0007]. A player who picked a row the policy rates
lower did not lose more often than the position says. That is the claim an action table makes, and
the human games do not support it.

Raw, the EWP is too sure past 0.6: answers of 0.65, 0.75 and 0.85 won 57%, 64% and 76%. That is
the over-confidence the pilot found against itself. Below 0.5 it is slightly under-sure.

**So no action table.** What the policy ranks between rows is not yet shown to matter to how human
games go, and the page does not show it. What passed is narrower: the policy's value of a position
adds to the WP model's. That is a candidate for the number above two a side (the engine already
leads at two or fewer), gated as the doubles engine was: per state kind, recalibrated on training
games, scored on held-out ones.
