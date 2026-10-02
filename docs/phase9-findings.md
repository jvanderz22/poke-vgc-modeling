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
