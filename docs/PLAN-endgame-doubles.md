# The endgame solver beyond 1v1: 2v1, 1v2 and 2v2

_Written 2026-09-30. **Progress (2026-10-01):** stages 0 and 1 done; stage 2 built for open and
closed sheets; stage 3 done for both (open: the engine beats the model, log loss 0.421 against
0.513; closed: better on Brier only, and no state kind on both); stage 4 built for open sheets,
answering within 5 s; stage 5 tried and reverted (the belief does not concentrate by grouping).
Closed-sheet doubles stay with the model. The race at the horizon, about 95% of a 2v2 answer, is
now blended with HP share, the count and the net stat stages (`'blend_boosts'`), and the answer
is tempered: open-sheet log loss 0.384 against the model's 0.513 (2v2s 0.485 against 0.621); the
page uses both._
It extends [PLAN-v3](PLAN-v3.md) steps 3 and 8 (the
1v1 solver and its speed) and is meant as the first milestone of step 9 (Phase 9, policy
strength), carried on as [PLAN-v4](PLAN-v4.md) step 1. Numbers marked _estimate_ are guesses until stage 1's built-in stats measure them.

---

## Why

- **The 1v1 engine beats the model on real games.** On held-out human 1v1s its Brier score was
  0.090, against the model's 0.197, and the side it called won 48 of 50 confident calls
  ([phase8](phase8-findings.md)). The model does not follow what decides an endgame; the engine does.
- **But few games reach a 1v1, and three and a half times as many reach two or fewer a side**
  (counted 2026-09-30 over held-out human games that finished, by the first turn mark in each state):

  | | open sheets (1,703) | closed sheets (1,025) |
  | --- | --- | --- |
  | two or fewer each | **764 (45%)** | **429 (42%)** |
  | a 2v2 | 456 (27%) | 260 (25%) |
  | a 2v1 or 1v2 | 508 (30%) | 297 (29%) |
  | a 1v1 | 217 (13%) | 118 (12%) |
  | the side ahead at 2v1 wins | 88% | 85% |

  A 2v1 is mostly decided by the Pokémon count, so the engine's worth there is in finding the
  12–15% that are not. The 2v2 is where games are still open, and a quarter of all games pass
  through one.
- **Phase 9 needs the same machinery at doubles scale**: joint choices with targets, chance
  enumerated, and a matrix game solved at a size the 1v1 never reaches. Building it here, on
  positions with a checkable answer, is cheaper than building it inside a full-battle search.

## Why this is a bounded step

**With four brought and at most two left a side, there is no bench, so there are no switches.**
Every state here is 2v1, 1v2 or 2v2, and every choice is a move and a target. Switching arrives
with a third Pokémon left, and that belongs to Phase 9. The simulator already plays the doubles
rules exactly (spread damage, redirection, Helping Hand, retargeting after a KO mid-turn). What
has to change is how the solver sets up a position, lists the choices and pays for the search.

**How big it gets** (_estimate_):

| | choices a side | cells in a turn's matrix | attacks a turn | cost of one turn vs 1v1 |
| --- | --- | --- | --- | --- |
| 1v1 | ~4 | ~16 | ~2 | 1× |
| 2v1 / 1v2 | ~4 and ~25–49 | ~100–200 | ~3 | ~20–50× |
| 2v2 | ~25–49 | ~600–2,400 | ~4 | ~10³–10⁴× |

A slot has ~5–7 choices: Protect, each single-target attack at each live foe, spread and status
moves. A side's choice is the pair of its slots' choices. Chance compounds per attack, although
merging outcomes by state (95% of replays were duplicates in the 1v1 profile) takes much of it
back. **So a 2v2 turn searched exactly costs about what 2–3 turns of 1v1 did.** Depth 1 plus
something better than HP share at the leaves would be the target for exact search. Stage 1
measures the estimate, and realistic-play pruning (below) is how the plan avoids depending on it.

## Stages

Revised the same day: **realistic play on both sides is the approach, not the fallback.** Each
Pokémon's choices are cut to the few a good player would consider before the matrix is built.
The answer is the value of that restricted game, and how far it can be from the exact value is
measured, starting with the 1v1s already solved. At ~3 choices a slot, a 2v2 turn has ~81 cells
instead of ~2,400, so the solver can fill the matrix as it does now. Double oracle and the F10
benchmark leave the critical path. Closed sheets stay on it, because the page has to work in
both regimes (decided 2026-09-30).

Each stage ends in a check. With pruning off, the 1v1 answers must stay bit-identical throughout:
the benchmark's stored truth, and a sample of step 3.6's positions.

### 0. The 1v1 on closed sheets, checked (½ day, plus a night on Kaggle)

**Done 2026-09-30: the engine keeps the lead.** On 86 held-out closed-sheet games at their first
1v1, log loss 0.345 against the model's 0.649 (−0.304 [−0.420, −0.179]); it called 22 games at
95% or more and the called side won 21; partly covered beliefs score as well as covered ones.
The page keeps leading with the engine (phase8-findings, "The engine against how human 1v1s end,
on closed sheets"). Built: `endgame.pair_candidates` and `solver_vs_humans.py --sheets closed`;
1,905 positions at depth 2 solved on Kaggle overnight, pairs heaviest first to 90% of the
belief, at most 16 a game.

The Battle page already leads with the engine's 1v1 number on a closed sheet, but the check
behind that (step 3.6) ran on open-sheet games only. Principle 4 says a number belongs to the
regime it was measured in, so this comes first. It is also the closed-sheet machinery that
stages 2 and 3 need.

- **Both sides from the belief.** A closed-sheet replay seen from the stands hides both sides'
  sets, and `endgame.candidates` refuses that case today. Extend it:
  - each side's likeliest sets, as the player view already weighs them;
  - the pair weighted jointly by the turn order (`speed_joint` over both);
  - at most 3 × 3 set pairs per answer, times the Speed classes.
- **The check.** Held-out closed-sheet games (1,025 from the stands) at their first 1v1:
  - the engine against the served closed-sheet model (`wp-v1d-sw-split-small`);
  - the same scoring as step 3.6, reported by how much of the set belief was left unsolved;
  - exported with `--export`, solved overnight on Kaggle.
- **What it decides:** whether the engine keeps leading the 1v1 on closed sheets. If it loses,
  the page leads with the model there and shows the engine underneath, as the page did before
  step 3.6.

### 1. The solver plays doubles positions, with realistic-play pruning (1½ days)

**Done 2026-09-30**, merged into main. The default search for a doubles position is depth 1,
`prune` 3, `sample` 16 (stratified), the KO extension, and the damage race for every 1v1 it
reaches (`vgc.wp.doubles.SEARCH`). What each lever measured is under "Measured so far" below.

- **Position format.** A side lists one or two active Pokémon, each with its own HP, stat stages,
  status, item consumed, choice lock, "just arrived" (Fake Out) and Protect counter. `setUp`
  leads with them and faints the fillers behind.
- **Choices.** Each slot's usable moves: at each live foe for single-target moves, untargeted for
  spread and self moves, at the ally only where that means something (Helping Hand, Pollen
  Puff). The solver still throws on any choice the simulator rejects.
- **Realistic-play pruning.** Choices are scored in the solver with Showdown's own damage
  calculation (`actions.getDamage` at a middle roll, no crit), so there is no call out to Python.
  Per slot, keep:
  - a move that KOs a foe, and the best-damage attack at each live foe;
  - Protect, unless it fails for having been used the turn before;
  - Fake Out if it would work, plus speed control and support by category (Tailwind, Trick Room,
    Icy Wind, Follow Me, Rage Powder, Helping Hand, Snarl);
  - then cut to `k` by score (default 3, a search setting).

  For the pair, drop two attacks aimed at a foe that one of them KOs outright (overkill), keeping
  the pair that KOs and hits the other foe. The same rule applies to both sides, so the answer
  is best play within realistic play.
- **The KO extension.** Each KO adds a turn to the search budget. A line that trades Pokémon is
  then searched on into the smaller state, where the solver is cheap and strong, instead of
  stopping at HP share. (This is the old "depth by material" at a fraction of the code.)
- **`stats` in the output**: replays, distinct outcomes, matrix sizes per state kind, and cells
  pruned. This is how the estimates above get measured, at no extra cost.
- **Check, before any doubles code: pruning on the 1v1s we already have.** About 300 1v1
  positions have exact answers in the cache. Solve them again with pruning on and report how often
  each side's exact best move survives, and the value gap (mean, worst, and how many are off by
  more than 0.05). In a 1v1 there are only ~4 moves, so `k = 3` is a real test of the scoring. If
  it drops the right move often, fix the scoring before scaling it up.
- **Check, after:** hand-read debug logs for one position of each kind; every listed choice
  accepted over a round of self-play turns.

**Measured so far (2026-09-30, on the `solver-pruning` branch).**
- **Pruning on the 1v1s, `k = 3`, three passes.** Against 543 exact answers: 500 identical, mean
  gap 0.004, 13 off by more than 0.05, and the exact root play kept on both sides in 477 of 518
  (92%), at 2.6× less time.
  - The damage score alone dropped Fake Out, priority attacks, and setup or recovery. The rules
    above now keep a usable Fake Out and the best priority attack.
  - Setup and healing score high only when the foe's best attack takes under half this
    Pokémon's HP.
  - What is left is mostly second attacks whose value is a side effect (Iron Head's flinch).
- **The solver plays doubles positions.** One or two actives a side, per-Pokémon fields as lists,
  the Protect counter, choices per slot with targets, per-slot pruning with the overkill rule, the
  KO extension and `stats`. With pruning off it reproduces the cached 1v1 answers bit for bit (60
  of 60: value, leaf mass, nodes, choices).
- **A 2v2 at depth 1 is not affordable as the 1v1 is.** Four 2v2 positions built from real sets,
  each with `k = 3`:

  | position | KO extension | chance | seconds | leaf mass |
  | --- | --- | --- | --- | --- |
  | 0 | on | exact | 339 | 0.63 |
  | 0 | off | exact | 19 | 0.99 |
  | 0 | on | `cutoff` 1e-2 | 344 | 0.63 |
  | 0 | on | `min_prob` 1e-3, one damage roll | 58 | 0.65 |
  | 3 | either | any | over 900, or no answer | |

  - Without the extension one turn is cheap but settles nothing (leaf mass 0.99).
  - With it, most of the time goes on the 1v1s that a second KO opens.
  - Position 3's spread moves put up to eight hits in a turn, each branching on accuracy, crit,
    roll and secondary effect. No single outcome is then as likely as 1e-3, so a floor on branch
    probability either keeps everything or drops every branch of a cell. Dropping them all left
    the cell with no weight and the answer empty; the likeliest branch of each chance point is now
    always kept.
- **So enumerating chance exactly does not scale to doubles.** It is exponential in the hits a
  turn, and doubles doubles them. The next step is chance handled differently in positions with
  more than two Pokémon: a turn's chance events sampled, fresh each turn and many times per cell,
  with the value's sampling error reported beside leaf mass. Sampling each turn is not the
  determinization that failed in the 1v1: a player still chooses before that turn's dice, and
  sees them only after, as in a real game. It is checked against exact enumeration on the 2v1
  positions small enough to enumerate. The 1v1 keeps exact enumeration.
- **Sampled chance, built (`search.sample = N`), partly checked.**
  - Drawing with the simulator's own PRNG drew all sixteen damage rolls, so no two sampled turns
    merged. Draws now come from the representatives the enumeration branches on.
  - Sixteen draws of a 1v1 turn cost more than all its outcomes, and one position ran past 15
    minutes. A turn is now enumerated while that takes at most N replays, and sampled only past it.
  - Against the exact 1v1 answers, `N = 16`, 67 of a random 180 so far: the favoured side agrees
    in 65 of 65 positions at least 0.1 from even; all 27 near-certain answers (≥ 0.95 or ≤ 0.05)
    come back within 0.05; none comes back near-certain wrongly; mean gap 0.019, none over 0.2;
    0.62× the exact time. That is the bar asked of it: advantages and near-certain results, not
    the digits.
  - All 180 (178 solved; 2 benchmark positions that take 44 and 63 minutes exactly passed the
    10-minute cap): the favoured side agrees in 168 of 168; 81 of 87 near-certain answers within
    0.05 and the other six still lopsided (worst 0.958 → 0.875); none near-certain wrongly; mean
    gap 0.019, 9 over 0.1, none over 0.2; 0.60× the exact time.
  - **Stratified draws** (each of the N draws given its own N-th of every chance event, so a Speed
    tie splits eight and eight): mean gap 0.009 against 0.019 on the same 178 (difference
    [−0.015, −0.006]), 2 over 0.1, the same time, the same 168/168 and 81/87. Adopted. The
    reported error is still a little small (153 of 178 within twice it).
- **2v2 at depth 1 with sampling (`N = 16`, stratified), `k = 3`, KO extension on**, the four
  positions run side by side:

  | position | value | exact value | seconds | leaf mass | sampling error |
  | --- | --- | --- | --- | --- | --- |
  | 0 | 0.610 | 0.617 | 168 | 0.63 | 0.056 |
  | 1 | 0.696 | | 165 | 0.68 | 0.009 |
  | 2 | 0.612 | | 152 | 0.61 | 0.057 |
  | 3 | 0.566 | no answer | 326 | 0.70 | 0.032 |

  - Position 3, the spread-move one, now has an answer; position 0 is within 0.01 of exact at
    half the time.
  - **Depth 1 does not settle a 2v2:** leaf mass 0.6–0.7, so most of each answer is HP share at
    the horizon. Most of the time goes on the 1v1s a second KO opens (300–600 small matrices).
  - So the next question is what settles it affordably: depth 2 on the cheaper 2v1 and 1v2
    positions first, a smaller N, and memoizing the 1v1 subgames across the search.
- **Memoizing the 1v1 subgames would not pay.** In position 1's search, 337 1v1 searches are 263
  distinct states; rounding HP to 5% still leaves 172. They are different positions, not repeats.
- **The damage race (`search.race`, `search.race_1v1`) values a 1v1 without searching it.** Each
  side's attacks, from the simulator's damage on the move as it is used, raced turn by turn over
  both HP totals (accuracy, rolls, crits, priority, Speed, Sash, recoil, drain, Sitrus, residual
  damage), and the pairs solved as a matrix game. Against the exact 1v1 answers:

  | | favoured side right | near-certain within 0.05 | time against exact |
  | --- | --- | --- | --- |
  | HP share, no search | 344 / 498 | 23 / 277 | 1% |
  | race, no search | 464 / 498 | 254 / 277 | 2% |
  | HP share at the horizon, depth 1 | 127 / 169 | 43 / 88 | 4% |
  | race at the horizon, depth 1 | 162 / 169 | 83 / 88 | 4% |

  - Building it found that pruning's damage had ignored type-changing abilities: an Aerilate
    Double-Edge was read as Normal and doing nothing to a Ghost. Both now use the move as the
    simulator prepares it.
  - What the race leaves out is what a race does: Protect, status moves, boosts, two-turn moves
    beyond their charge turn. The search's own turn covers those near the root.
- **2v2 at depth 1 with the 1v1s raced:** 93–180 s, about half the time. Positions 0–2 moved from
  0.61–0.70 to 0.86–0.88; those answers had rested on one-turn 1v1 searches scored by HP share,
  which pick the favoured side 75% of the time. Which is right is stage 3's question.
- **2v1 and 1v2** (each 2v2 with one Pokémon fewer on a side; N = 16, k = 3, raced 1v1s; eight run
  side by side): about 6 s at depth 1, 3–8 minutes at depth 2. The 2v1s here are decided at depth
  1 (value 1 or 0.92–0.98, leaf mass under 0.2). The 1v2s rest wholly on the horizon at depth 1
  (0.33, leaf mass 1) and mostly not at depth 2 (0.10–0.24, leaf mass 0.30–0.62).
  - Depth 3 passed the 10-minute cap on all eight.
  - So depth 1 with the race is the default for the page and for stage 3's check, which it makes
    cheap enough to run locally. Depth 2 is for Kaggle, and for the 1v2 side of a check.

### 2. The adapter, for open sheets, closed sheets and Watching mode (2 days)

**Open sheets built 2026-09-30** (`vgc.wp.doubles`, its own module beside `vgc.wp.endgame`):
- **Which positions.** Neither side has more than two left, every one left is on the field, and it
  is not a 1v1. Volatiles, sleep and bad poison are refused, as in the 1v1.
- **Per Pokémon, in field-slot order.** HP, stages, status, consumed item, choice lock, Fake Out
  freshness, and the Protect counter, counted from stalling moves on consecutive turns in the log
  (the replay journal now records each move's Pokémon and move). One Mega a side.
- **Speed, differently from the plan above.** Not classes per pair multiplied: seeded draws of
  every unknown investment from its prior, weighted by each turn order the log showed (an order
  between two of these Pokémon is an indicator on the draw, one with a Pokémon outside them a
  likelihood, as `belief.speed.joint` does for two), grouped by the move order they give this
  turn. The heaviest orders are solved until 90% of the weight or six orders; each stands for
  itself by every Pokémon's commonest investment within it. Seeded, so a battle writes the same
  positions again.
- **On 150 held-out open-sheet games:** 59 reach such a position, 57 are built (39 2v2, 9 2v1,
  9 1v2), at 0.2 s a plan and one to six move orders a game.
- Noticed on the way: the Speed prior puts its two largest weights on 0 and 32 for species as
  unlike as Basculegion and Rillaboom. Not changed here; worth a look of its own.

**Closed sheets: in progress.** Watching mode follows once both are checked.

- **`vgc.wp.endgame`.** Accept at most two Pokémon left a side. Per Pokémon: Fake Out
  freshness, the Protect counter, a choice lock, items consumed.
- **The Protect counter.** `stall` is a volatile, so today such a position is not built. It is
  too common in doubles to refuse, so the solver learns to set it up.
- **Speed.** Each hidden Pokémon's Speed class is taken against each Pokémon it could move before
  or after. The classes are multiplied, and the heaviest are solved until 90% of the weight is
  covered; the rest is reported as unsolved mass. The pair case reproduces `partition_joint`
  exactly.
- **Closed sheets.** Each hidden Pokémon has its likeliest sets from the belief, weighted by the
  turn order, as stage 0 does for one.
  - Two hidden Pokémon multiply: up to 9 set pairs per side, times the Speed orders, and 81 when
    both sides are hidden (the check's view from the stands).
  - The combinations are solved heaviest first until 90% of the weight is covered, or a cap on
    positions is reached (about 12 for the page, more for the overnight check). The rest is
    reported as unsolved mass, as the 1v1 does now.
  - Pruning cuts the cost of each position, not the count, so the cap is what keeps a closed-sheet
    2v2 answerable. The page says how much of the belief its answer covers.

### 3. The human check (½ day, plus overnight on Kaggle)

**Open sheets launched 2026-09-30.** `solver_vs_humans.py --endgame doubles --depths 1`: 674
held-out open-sheet games reach a buildable position, 1,791 positions (1,260 in 2v2s, 531 in 2v1s
and 1v2s), solved on Kaggle in three runs side by side (`--name doubles-a|b|c`, cap 900 s), and
scored by kind on the laptop once merged. Depth 2 waits on what depth 1 shows.

- **The step 3.6 check again, in both regimes**, at each held-out game's first 2v1/1v2 and first
  2v2 (open-sheet games, and closed-sheet games as stage 0 reads them), through
  `--export` and `scripts/cloud/kaggle_solve.sh`. Engine against model, log loss and Brier,
  cluster bootstrap by group, reported by state kind, by leaf mass, and by how many choices were
  pruned.
- **Pruning in doubles.** On the 2v1/1v2 positions small enough to solve exactly, the same
  comparison as stage 1's (best move kept, value gap).
- **The decision is per state kind and per regime.** The engine leads the page only where it beats the model,
  as in the 1v1. Realistic play is closer to how people play than full best play, but the field
  is ~1100-rated, so each state kind has to earn its place.

**Done for open sheets, 2026-10-01**, in three steps (phase8-findings, "the engine against how
human doubles endgames end" and "a horizon that knows a count lead"):
- Depth 1 with the KO extension and HP share at the horizon: better than the model on Brier, not
  on log loss. HP share gave a 2v1 lead 0.72 where the side ahead wins 88%.
- So the horizon became a damage race for several Pokémon, calibrated on 1,500 training-split
  games. Alone it already edges the model.
- The 5-second budget (below) chose the search: one turn, `prune` 2, eight sampled draws, a
  forced-win check first. Exactly as the page runs it: log loss 0.421 against 0.513 and Brier 0.130
  against 0.172, engine better; 2v2s, and 2v1s and 1v2s pooled, engine better on their own. The Kaggle runs at depth 1 with the KO extension (`doubles2-a|b|c`), a
  reference for what a wider search would add, were scored the same evening: nothing (log loss
  +0.017 [−0.015, +0.052] against the live search on the same horizon; phase8-findings, "a wider
  one-turn search"). No deeper background search.

**Closed sheets, 2026-10-01** (phase8-findings, "the live doubles answer on closed sheets"). The
live configuration on 349 held-out closed-sheet games, after fixing the set belief's reading of a
Mega's ability (55 games had had no set to solve):
- all: Brier 0.146 against 0.182, engine better; log loss 0.474 against 0.540, not
  distinguishable;
- 2v2, and 2v1 and 1v2 pooled (135 games: log loss 0.347 against 0.402, Brier 0.087 against
  0.122): not distinguishable.
- So by this stage's rule no closed-sheet kind passes yet. A 2v1 and a 1v2 are one state with the
  sides named the other way round, so they are judged pooled; the 2v1 half alone came out engine
  better on both (log loss 0.253 against 0.467), which was first read as a pass and was noise in
  how the games fell between the labels. The answers average over guessed sets and the three
  positions solved carry little of the belief; what would help most is a belief that concentrates
  (sets grouped by what changes the fight), then the check again.

### 4. The app (1 day, only for what passed)

**Built for open sheets, 2026-10-01.** A live answer has 5 seconds, planning included (the
user's budget). Warm solver processes (`vgc.wp.pool`, `endgame-solver.js --serve`); a quick
answer (forced-win check and race) and the one-turn search side by side, each move order's search
replacing its quick value if it lands in time; the heaviest three move orders. On 60 held-out games
through the page's own path: first answer median 1.2 s, final median 2.3 s, never past 5.1 s.
Closed sheets: no state kind passed its check, so the model keeps leading every closed doubles
position until stage 5's belief and the check again say otherwise.

- The engine's row appears in 2v1, 1v2 and 2v2 for the state kinds that passed, with 2v1 and 1v2
  one kind (the same state from either seat).
- It shows the depth, the leaf mass, "realistic play, k choices a Pokémon", and the unsolved mass
  from Speed orders and, on a closed sheet, from the set belief.
- It deepens in the background as now, and shows only a finished answer.

### 5. A closed-sheet belief that concentrates (1 day, plus a local check)

**Tried and reverted, 2026-10-01** (phase8-findings, "grouping closed-sheet sets by what changes the
fight"). Grouping took the three solved positions from 6.3% of the belief to 9.4%, not 75%: the
sets mostly differ in ways a fight does show. And 11 of 65 classes disagreed by more than 0.05
between members (Grassy Glide's terrain priority, support moves, secondary effects, defensive
natures). Closed-sheet doubles stay with the model. What follows is the plan as written.

**Why.** On closed sheets the doubles answer is better than the model on Brier only (349 games),
where on open sheets it is better on both. The difference is the sets. Each hidden Pokémon keeps
its two or three likeliest sheets, the combinations multiply, and the weight spreads thin: the
three positions the page solves carry a median of about 40% of the belief, and six carry 59%. Many
of the sheets kept apart do not differ in anything this fight can show (Protect against Detect, a
fourth move pruning would never pick, a nature that moves neither Speed nor the attacking stat).
So the solved positions are near-copies of one another while real alternatives go unsolved.

**What.** Group a hidden Pokémon's sheets by what changes this fight, before taking the heaviest:
- **The signature of a set, against the Pokémon actually across from it:**
  - item and ability, which act on damage, Speed and survival (Sash, berries, Scarf, Intimidate);
  - the nature's direction on Speed and on the stat its attacks use, not its name;
  - the moves realistic play would consider: each damaging move kept only if its damage against
    one of the foes on the field is close to that set's best on that foe (base power, STAB,
    effectiveness), plus Protect and its kin, Fake Out, priority attacks, and speed control
    (Tailwind, Trick Room). Other status moves drop out.
- **A class is the sheets that share a signature**, with their counts summed. It is solved by its
  heaviest member, and its weight is the class's.
- The turn-order weighing and the Speed draws are unchanged; they already depend only on item,
  ability and nature.
- **The 30 games where no combination fits the logged turn order** are counted by the priors alone,
  as the open-sheet path does, instead of being dropped.

**Checks.**
- **Equivalence:** for a sample of classes with two or more members, solve two members at the
  live configuration; their values should agree within the sampling error (about 0.02). A class
  that does not is split on whatever differs.
- **Concentration:** the share of the belief the three solved positions carry, against today's
  ~40%. The aim is 75% or more.
- **Time:** closed-sheet planning stays within about 1.5 s, so the 5 s budget still holds.
- **The closed-sheet check again** (`solver_vs_humans.py --endgame doubles --sheets closed --top 3
  --search <live> --tag live`, local, about 30 minutes), by kind: 2v2, and `2v1|1v2` pooled. The page then leads with the
  engine wherever closed sheets now pass, and keeps the model leading where they do not.

### Later, only if a check asks for them

- **Double oracle over the full matrix**, if the pruning gap is too large to accept and the full
  2v2 matrix is too slow to fill. It gives the exact value while visiting only the cells the
  answer depends on.
- **The F10 benchmark's truth** from the solver, with a person reading each principal line. It
  tests the models, not the solver, so it waits.
- **Revival Blessing**, with the real fainted teammates of an open sheet in place of fillers.

## How it fits Phase 9

Phase 9 (PLAN-v3 step 9, [PLAN-v4](PLAN-v4.md) step 2) is a policy over whole battles: `EWP(a)` with exact transitions, chance
enumerated, a belief over hidden sets, and the learned WP at the leaves. This plan builds its
bottom layer:

- **The same machinery:** joint choices with targets, realistic-play pruning (Phase 9's "top-k
  pruning by the heuristic prior", built and measured here first), chance at doubles scale, and
  Speed integrated over four Pokémon.
- **An exact oracle for its leaves.** Once a line reaches two or fewer a side, Phase 9 can ask
  the solver instead of the model, in the state kinds where the solver passed the human check.
  That is where the model is weakest.
- **So the order is:** 1v2 and 2v1 first (about 20–50× a 1v1), then 2v2, then Phase 9 above
  them. I would make this Phase 9's first milestone rather than a separate step.

## Risks, and what would stop it

| Risk | What happens |
| --- | --- |
| Realistic-play pruning drops the move that decides the game | Measured first on ~300 exact 1v1s, then on exact 2v1/1v2s; `k` and the keep rules are search settings; double oracle over the full matrix is the exact fallback |
| HP share is a poor leaf when four Pokémon are on the field | The KO extension moves most leaves into smaller states; leaf mass is reported per state kind |
| The assumed non-Speed spread matters more (spread moves put more KOs on a threshold) | Flagged on the page. Integrating the bulk thresholds the way Speed is integrated is a later step |
| Positions per answer explode on closed sheets (sets × Speed orders; 81 set combinations when both sides are hidden) | Heaviest first to 90% of the weight or a cap, the rest reported as unsolved mass, and the check reported by that mass |
| The engine loses to the model on closed sheets | Stage 0 finds out for the 1v1 before anything is built. The model keeps the page in that regime |
| Best play is the wrong model of ~1100-rated players in 2v2 | Stage 3 measures it per state kind and regime, and the model keeps the page where the engine loses |

## Estimate

About 5½ working days plus two or three overnight Kaggle runs: stage 0 ½ day plus the night,
stage 1 1½ days, stage 2 2 days, stage 3 ½ day plus the night, stage 4 1 day. Two answers come on
day one:
- stage 0's run says the closed-sheet 1v1 keeps leading (done);
- stage 1's pruning check on the cached 1v1s says whether the scoring keeps the right move. If it
  does not, that is fixed before anything is built on it.
