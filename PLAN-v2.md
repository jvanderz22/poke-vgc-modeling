# VGC Reg M-C Model & Advisor — Plan v2

**Supersedes [PLAN.md](PLAN.md)**, which is kept as the archive: it holds the original research, the
phase 0–4 completion notes, and the post-mortems that produced this rewrite. Nothing here contradicts
its *architecture*; what changes is the order of work, the gates, and what is expected to come from
learning rather than computation.

v2 exists because Phase 4 produced a measurement that invalidates the plan's spine: **the quantity
Phases 7 and 8 were going to be built on cannot be estimated from this corpus, and the simulator's
version of it had never been checked against reality.** The evidence is in
[docs/phase4-findings.md](docs/phase4-findings.md); the consequences are here.

**It has since been checked, and it failed** ([docs/phase6-findings.md](docs/phase6-findings.md)).
Heuristic-vs-heuristic win rate is a precise measurement — split-half reliability 0.96 — of something
that does not predict who wins the real game: AUC 0.5119 over 2,684 independent series, and 0.0003
nats after the best out-of-fold rescaling. So the quantity is not merely unestimated from the corpus;
the simulator's substitute for it does not exist either. That is what reorders the phases below.

---

## Progress

| Phase | Status | Notes |
| --- | --- | --- |
| 0 — Environment spike | ✅ 2026-09-19 | [findings](docs/phase0-findings.md): 36.5 battles/s, pins set, ONNX ok |
| 1 — Foundation | ✅ 2026-09-19 | `vgc` CLI, L0 loader, validator, calc sidecar; calc = simulator on 4 scenarios |
| 2 — Battle layer, tier 1 | ✅ 2026-09-19 | heuristic 98.4% vs random; seeded runner, identical across worker counts |
| 3 — Battle data | ✅ 2026-09-19 | snapshots for every perspective; parity exact on 3,089 live decisions; split frozen |
| 4 — Win probability v1 (OTS) | ⚠️ Partial 2026-09-20 | **in-battle WP works and is shippable; preview WP does not exist and cannot be made to.** [findings](docs/phase4-findings.md) |
| 5 — Ship in-battle WP | ✅ Done 2026-09-20 | f180164 + 16a2a03: bucket gates, `ece_spectator_played_out`, `eval_dataset` fingerprints, `wp-v1-gbt` carded (`in_battle_pass: true`), app wired; composition docstrings corrected |
| 6 — Simulator validity | ✅ 2026-09-20 | **negative, decisively.** The heuristic does not predict human results; the fork takes its second branch. [findings](docs/phase6-findings.md) |
| 7 — Deterministic team tools | ✅ 2026-09-20 | `vgc meta usage` + `vgc team weakness`; no model, no gate. KO and speed thresholds in Stat Points, because their spread is hidden |
| 8 — Belief over hidden sets | 🟡 In progress | speed channel built and gated: 0.014% silently wrong on 62,581 self-play Pokémon ([findings](docs/phase8-findings.md)). Damage channel next |
| 9 — Policy strength (EWP + search) | — | was Phase 6, minus behaviour cloning; **promoted to the prerequisite for 10–11** by Phase 6 |
| 10 — Matchup evaluation | ⛔ Blocked | was Phase 7; the precomputed matrix is dead, and on-demand evaluation waits on Phase 9 |
| 11 — Team building | ⛔ Blocked | was Phase 8; behind Phase 10 |
| 12 — Interface / MCP | — | was Phase 9 |
| 🟡 Web app | W1–W2 partial | see the staging table at the end |

---

## The thesis

v1's first modelling principle was **"don't train what you can search."** It was right, and the phase
order did not follow it: every deterministic, immediately-useful capability sat behind four phases of
model work, and the model work was aimed at a target that turned out not to exist.

v2's ordering rule is narrower and testable:

> **Compute what can be computed. Learn only where a measurement shows learning is paying. Never
> build on a simulator-derived quantity that has not been checked against human outcomes.**

All three clauses come from evidence, not taste. The first because the analytic tools were always
buildable and are still unbuilt. The second because a 637k-row training set is matched by its 178k
human rows alone (§3 below). The third because both tests that have been run came back *negative*:
the transfer test in §2, and then Phase 6's direct check, which is the one the rule was written for.

---

## The questions, and what can answer them today

| | Question | Status under v2 |
| --- | --- | --- |
| 1 | Given 6 Pokémon, what are my weaknesses? | **Phase 7, analytically. No model.** Type coverage, speed tiers, calcs vs the top-30 threats. Buildable now. |
| 2 | Given 4 Pokémon, which 2 complete the team? | **Blocked behind Phase 9.** Needs a team-strength signal; Phase 6 measured both candidate sources and neither exists — the corpus has none, and the simulator's is uncorrelated with real results. |
| 3 | What moves should each Pokémon run? | **Partly Phase 7** (legal movepool, coverage gaps, breakpoints are analytic); ranking by win rate is blocked with Q2. |
| 4 | What do I bring, lead, and click? | **Phases 9–10.** Bring/lead is on-demand simulation of *this* matchup, not a learned function — but Phase 6 means the simulation has to be run by a policy stronger than the heuristic to mean anything. |
| 5 | Win probability | **In-battle: works, shipped (Phase 5). At preview: does not exist**, and Phase 6 closed the remaining route to it. In-battle still improves on closed sheets (0.693 → 0.475 by t5–6) but calibrates worse, and finding 8 says why: it has never been trained through an unknown. |
| 6 | All of the above in a web app during a real game | Staged; W2 lands with Phase 5, and **Phase 8 is the real unlock** — not only for closed sheets: finding 8 shows the spread is hidden at open sheets too, so speed and damage inference pay in every game. |

---

## What the evidence changed

Eight findings, each measured on the frozen split. Full detail and the scripts:
[docs/phase4-findings.md](docs/phase4-findings.md), [docs/phase6-findings.md](docs/phase6-findings.md),
[`scripts/analysis/`](scripts/analysis/).

**1. Pre-battle win probability is not learnable from this corpus, at any scale it can reach.**
Bradley-Terry over team composition: 0.6900 against a 0.6931 constant. The learning curve says why
that will not improve — quadrupling the corpus (2,063 → 8,252 games) bought 0.0035 nats, so a lift
worth showing a user needs three to four orders of magnitude more games. Three unrelated model
families (Bradley-Terry, GBT on hand features, the set encoder) all land within 0.004 of the constant
at preview. *Consequence: `PREVIEW_CORR_GATE` is retired as a model gate; scraping more replays is not
a fix for it.*

**2. Self-play transfers negatively at team level.** A team-strength model fit on 108,918 self-play
preview rows scores **0.7327 on held-out human preview — worse than answering 0.5** — at 49.0%
accuracy. *Consequence: v1's Phase 7 (build the matchup matrix under a heuristic policy, derive team
evaluation from it) is blocked, not caveated. Finding 7 closed it.*

**3. The self-play corpus adds nothing to the model that does work.** Same GBT recipe, three training
sets, scored on held-out human: mixture (637,508 rows) 0.5533 / ECE 0.0198, **human only (178,339
rows) 0.5525 / ECE 0.0158**, self-play only 0.5793 / ECE 0.0451. 4,345 human battles do what 42,705
mixed battles do, with better calibration. *Consequence: self-play generation for WP training pauses.
Its live justification is the simulator role Phase 6 tests, not the training rows.*

**4. Everything knowable before turn 1 is worth ~0.01 nats, and the player matters as much as the
team.** Player identity 0.6835, team composition 0.6869, both 0.6826. *Consequence: even a perfect
team-strength model reads a game whose outcome is dominated by play and variance. Preview advice
should be framed as "what to bring against this", not "you are favoured".*

**5. The corpus is the wrong population, and the wrong information regime.** 58% of replays are
unrated; of the rated, median 1101, p95 1314. 28.7% of players appear once; a bot has 101 games;
38.4% of games end in forfeit and the model scores *better* on those (0.536 vs 0.578). Separately:
every eval set and the trained model use the Bo3 **open-sheet** shard — 7,459 battles — while the
closed-sheet regime the actual game runs in has **779**. *Consequence: gates report `ended_normal`
separately; behaviour cloning is deferred (§ Deferred); Phase 8 moves up.*

**6. The Kaggle sweep never touched a GPU.** All seven runs report `torch 2.10.0+cpu`, `device: cpu`,
485 s/epoch, despite `enable_gpu: true` — the image's wheel is CPU-only and `--device auto` fell back
silently. Corrected in the archive by 4f2150b, which also records the misread tell: the weekly GPU
quota still showing 0.00h used was taken as good news about failed runs. *Consequence: this project
has no demonstrated GPU path; the 30 GPU-hours/week are unspent; the check is `train.json: device`,
not the kernel metadata, which records only what was requested.*

**7. The simulator does not measure the game. It measures the bot.** 3,372 real-meta pairings, 15
heuristic-vs-heuristic battles each, scored against the human result of those same 7,455 games. On
played-out games — 4,579, spanning 2,684 independent series — simulated win rate orders the outcome
at **AUC 0.5119, 95% CI [0.4948, 0.5296]**, and after the best out-of-fold rescaling it is worth
**0.0003 nats, 95% CI [−0.0010, +0.0004]**. This is not a sampling problem: the simulated win rate's
own split-half reliability is **0.96**, so the quantity is measured precisely and is simply the wrong
quantity. *Consequence: the fork below takes its second branch — Phase 10's matrix is dead, Phase 9
becomes the prerequisite, self-play generation stays paused. [Findings](docs/phase6-findings.md).*

**8. An open team sheet is not full information, and the pipeline treats it as if it were.** The
sheet carries species, item, ability, moves and nature — and **not the 66 Stat Points**
(`unpack_sheet`: "no stats: sheets don't carry them"; the pool records spreads as *imputed*). Measured
on the opponent's in-battle rows: nature known **1.000**, exact stats known **0.000**, in training
and in the held-out set alike. So their speed order and their exact damage are undetermined at OTS,
exactly as they are at closed sheets — and nothing infers either. `_move_summary` is documented as
"independent of move order" and no who-moved-first feature exists; `_on_damage` records the
defender's new HP and draws no conclusion about the attacker. Separately, the opponent's
item/ability/move known-flags are **1.000 / 1.000 / 1.000** over 433,052 in-battle training rows
against **0.454 / 0.417 / 0.348** on closed-sheet games, so those flags are constant in training and
the model has never had the chance to learn what "unknown" means. *Consequence: Phase 8 is rescoped
— belief over hidden sets is needed in **both** regimes, and its Stat-Point half can be gated on the
7,459-battle open-sheet shard rather than the 779 closed-sheet one. The training mix gains a reason
to include closed sheets that is independent of the closed-sheet product.*

---

## Architecture

The six-layer spine from PLAN.md is unchanged and still correct:

```
L4  Team building      weakness report · slot completion · moveset & SP search
L3  Team evaluation    matchup evaluation vs meta gauntlet · racing allocator
WP  Win probability    WP(observation) per perspective · belief over hidden sets · EWP(action)
L2  Battle policy      heuristic → search (expectiminimax, EWP at the leaves)
L1  Engine             pinned Showdown (Champions) · @smogon/calc 0.12.0 · poke-env 0.16.1
L0  Regulation config  legal pool · clauses · mechanics flags · SP rules · format id
```

Three amendments:

- **L2 drops behaviour cloning from the critical path.** v1 had BC as tier 2 and the search prior.
  Finding 5 says BC on this corpus clones ~1100-rated play. The heuristic remains the prior; BC
  returns only if a higher-rated corpus appears (§ Deferred).
- **L3 is "matchup evaluation", not "matchup matrix".** A precomputed 30×30 matrix under a fixed
  policy is one implementation, and finding 2 puts it in doubt. On-demand evaluation of the single
  matchup in front of the user is the other, needs no generalization, and is what the product
  actually asks for.
- **WP splits into two models with separate verdicts**, because they are two tasks that happen to
  share a featurizer: an in-battle model (works) and a pre-battle model (does not exist). One
  all-or-nothing gate set across both is what made a working capability report as failing.

---

## Phases

Each phase ends in something runnable. Phases 5 and 6 are independent and can run in either order;
everything from 7 on depends on 6's answer or is deliberately independent of it.

### Phase 5 — Ship in-battle WP ✅

The capability exists and is being hidden by a gate about a different task. `wp-v1-gbt` on held-out
human OTS games, spectator, by bucket:

| bucket | n | log loss | ECE |
| --- | --- | --- | --- |
| preview | 2,899 | 0.69315 | 0.0081 |
| t1–2 | 7,907 | 0.63983 | 0.0137 |
| t3–4 | 8,190 | 0.53694 | 0.0130 |
| t5–6 | 6,133 | 0.46091 | 0.0196 |
| t7+ | 5,569 | 0.43374 | 0.0225 |

(`models/wp/reg_mc/wp-v1-gbt/eval.json`, spectator, n=30,698 overall: 0.54428 / ECE 0.01245.)

**Done 2026-09-20.** `vgc wp eval` gained `in_battle_beats_constant`, `in_battle_ece`,
`ece_spectator_played_out` and an `in_battle_pass` roll-up; `wp-v1-gbt` is carded and passes all of
them (worst in-battle ECE 0.0225 at t7+; played-out spectator 0.5548 / ECE 0.0201 over n=20,312);
`web.app.in_battle_version` routes the Battle and Simulate WP track to a model whose in-battle gates
pass rather than the newest set encoder, and `GateBanner` says so. `player_beats_spectator` still
fails (0.5603 vs 0.5582 symmetrized) and `preview_tracks_sim` reads "not run" — both correct.

_Verification (met):_ beats the constant and the logistic baseline in every in-battle bucket; ECE
< 0.03 in every in-battle bucket and on `ended_normal` rows alone; the app does not display a WP
whose bucket failed its gate.

The `wp-v1-sw-*` question was settled separately in 16a2a03 and needs no further work: six of the
seven keep `train.json` only, as the tracked evidence that hyperparameter tuning is closed off and
that the run never touched a GPU; `wp-v1-sw-split-small` keeps a full card; `wp-v1-sw-uniform-05`'s
half-finished card was dropped rather than committed. Weights stay gitignored.

_Closed 2026-09-20._ The five stale claims about corpus composition are corrected in
`set_torch.py` (×3), `kaggle_train_wp.ipynb` and `docs/cloud-compute.md`: human rows are 28.0% of
train and val, not "~3%"/"a few percent", and validation is 72% self-play, not ~89%.

### Phase 6 — Simulator validity: the fork in the road ✅ _negative_

**Done 2026-09-20 — and it forked the roadmap.** [docs/phase6-findings.md](docs/phase6-findings.md);
result in [`data/analysis/reg_mc/sim_validity.json`](data/analysis/reg_mc/sim_validity.json); re-run
with `make sim-validity`.

Every human battle whose two open sheets resolve to the pool gives a real-meta pairing. All 3,372 of
them were played 15 times under the heuristic, sides alternating, and the simulated win rate scored
as a predictor of who won the human game — log loss and AUC against a 0.5 constant, with cluster
bootstraps over Bo3 series.

```
7,455 human games · 3,372 pairings · 3,431 series · 50,580 battles · 0 errors · 30 min
```

| on played-out games (n=4,579 / 2,684 series) | | |
| --- | --- | --- |
| AUC | **0.5119** | 95% CI [0.4948, 0.5296] |
| log loss vs constant | +0.40831 | 95% CI [+0.374, +0.441] |
| after out-of-fold rescaling | **−0.00027** | 95% CI [−0.0010, +0.0004] |
| split-half reliability of the simulated win rate | **0.961** | it is a precise measurement of the wrong thing |

Two verdicts rather than one, because they have different consumers. `pass` turns on the scale-free
evidence (AUC and the recalibrated log loss), since a matchup matrix is consumed for *ranking* and a
pure scale error could be fitted out downstream. `usable_as_is` is the stricter question of whether
the raw number can be shown to a user; the simulator fails that too, and for a reason the first
verdict deliberately looks past — 55.8% of pairings land beyond 85/15 while the human games they
produced are coin flips.

_Two corrections to this plan's own sketch of the experiment, both from measuring first:_

- **The power was overstated.** "2,796 pairings seen ≥2 times" is almost all Bo3 repetition — only
  **44 pairings recur across two distinct series**, and a series shares both teams *and both
  players*. The unit of independence is the series, and every interval above is a cluster bootstrap
  over the 3,431 of them.
- **1,000 pairings × 50 battles was the wrong allocation.** Outcome-side precision is linear in
  series covered; extra battles per pairing only shrink predictor noise, and the split-half number
  shows how little of that there was. All pairings × 15 covers every game in the corpus for the same
  wall clock and is worth about 1.7× the power.

_A caveat finding 8 adds, recorded rather than argued away:_ the pool's spreads are **imputed**
from nature and moves, because the sheets do not carry Stat Points. So the pairings were simulated
with guessed allocations, not the ones those players actually ran. The result is therefore about the
simulator as it can actually be run against this corpus — the true spreads are not recoverable from
a replay, so no version of this experiment can control for it. It is a reason to expect the measured
signal to be an underestimate, not a reason to read the verdict differently: a split-half reliability
of 0.96 says the imputed-spread matchup is being measured precisely, and it is uncorrelated with the
outcome.

_The fork, resolved:_ **it does not beat the constant.** Phase 10's matrix form is dead and so is any
team-strength model distilled from it. Phase 9 becomes the prerequisite, and this check re-runs
against that policy before anything is built on it. Self-play generation stays paused — finding 3
took away its training justification, and this takes away the other one.

This also replaced `vgc wp check-preview`, which decided the same question on 30 pairings, 15 held
out — a correlation whose 95% interval is roughly ±0.5, applied against a gate of 0.5. It and the
`preview_tracks_sim` gate are removed; the substantive question it asked is now
`preview_beats_constant`, scored on the 2,899 held-out preview rows against the constant with its n
recorded. The last readings of the retired check are kept in the Phase 6 findings.

Re-run this against every new policy. It is the project's standing check that the simulator measures
the game rather than the bot, and it has now earned that description.

### Phase 7 — Deterministic team tools ✅ _independent of Phase 6, which is why it survived it_

v1 opened Phase 8 with "analytic weakness report first (no model, immediately useful)" and then put
four phases of ML in front of it. It moves here, alongside a second tool the corpus already supports.

**Weakness report ✅ (Q1, and part of Q3)** (`vgc team weakness`, `vgc.building.weakness`).
Type pressure, speed tiers, and real calcs against the top-30 threats, in ~2s: "Sylveon OHKOes 5 of
your six", "nothing on your team can OHKO Kingambit even uninvested (best roll 98%)". Deterministic,
explainable, every number traceable to the pinned calc.

**Finding 8 changed its shape, and for the better.** Their spread is hidden, so "Kingambit OHKOs your
Sinistcha" is not a fact — it is a fact about an assumed spread. Rather than assume one, the report
states the **breakpoint**: the fewest Stat Points they must have in the attacking stat for the KO to
exist, and the fewest for it to be guaranteed. *Arcanine-Hisui's Head Smash OHKOes your Incineroar
from 0 Atk SP, and always from 14.* That has no free parameter in it, it is what a player wants
anyway, and it is Phase 8's damage channel run forwards — so this is a down payment on that phase
rather than something to redo.

The asymmetry is the design. Incoming, the unknown is *one* number, because your own spread is known,
so the answer is an exact threshold. Outgoing, it is two — their HP and the relevant defence — and no
single threshold separates the cases, so that half reports a bracket between an uninvested and a fully
invested defender instead of inventing a spread to sit between them. Speed is one number too, so it
gets thresholds: *Sneasler needs 28 Speed SP to outrun your Salamence.*

Three things it says out loud rather than burying: a threat is **one set, usually a minority one**
(Kingambit's most common is 22.4% of its sheets, and the share is printed next to every row); the
**field is empty** (no terrain, weather, screens, Intimidate or boosts, though 80% of teams bring a
terrain setter); and battle-scaling abilities are evaluated at base.

**Usage report ✅** (`vgc meta usage`, `vgc.meta.usage`). 15,028 sheets from 7,514 replays, 2,523
players, 3,347 distinct teams: species usage, item / ability / nature distributions, move frequencies
and partners with lift. Descriptive and correct by construction. This appears to be the only corpus
of its kind for Champions, and it is what players get from Pikalytics in other formats. Three
counting decisions, each measured rather than assumed:

- **It counts the replay sheets, not the pool.** `pool.team_key` is species + item + moves and
  ignores ability and nature, so sheets differing only in a Modest/Timid choice collapse into one
  entry with a single representative. Against the sheets that representative mis-assigns nature on
  1.78% of Pokémon rows and ability on 0.57%, touching 9.8% of sheets. Fine for a pool of teams to
  play; wrong for a distribution. (`web/prior.py` counts from the pool and inherits this; it is a
  stopgap Phase 8 replaces, so it is recorded here rather than patched.)
- **Two denominators, both reported.** Per game (a player who played 115 games counts 115 times)
  and per player (each name once). They disagree enough to matter — Basculegion 17.9% vs 22.2%,
  Garchomp 15.7% vs 19.3% — and they reorder the top five, so neither is called "usage" alone.
- **No spread clusters**, which is what v1 listed here. Finding 8: sheets do not carry Stat Points,
  and the pool's spreads are `impute_sp`'s deterministic function of nature and moves. Clustering
  them would publish our own guess as a measurement of what people play. Nature is on the sheet, so
  nature is counted; the spread belongs to Phase 8, which infers it rather than assuming it.

_Verification:_ every claim in a weakness report resolves to a calc or a dex lookup
(`tests/test_weakness.py` re-runs the calc at N and N−1 for every threshold the report states, so a
bug in the sweep cannot agree with itself; two breakpoints were also cross-checked by hand against
`vgc calc`). Usage numbers reconcile to sheet counts (`tests/test_usage.py` — species counts sum to
6 × sheets, every distribution sums to its species, partner counts are symmetric). Both run offline
with no model.

### Phase 8 — Belief over hidden set information _(was Phase 5; "closed-sheet belief")_

Moved up by finding 5, and **rescoped by finding 8: this is not a closed-sheet feature.** An Open
Team Sheet carries species, item, ability, moves and nature — and *not* the 66 Stat Points. So in
both regimes the opponent's speed order and exact damage output are unknown, and in both regimes
nothing in the pipeline infers them. The difference between the regimes is how much else is hidden,
not whether anything is.

Two information channels, neither of which exists today, and both of which are pure computation
against the pinned calc rather than anything learned:

- **Turn order → a bound on speed. ✅ Built and gated** (`vgc belief speed`, `vgc.belief.speed`,
  [findings](docs/phase8-findings.md)). Their nature, item and ability are on the sheet, so their
  Speed stat is a known function of one unknown integer in 0..32, and every equal-priority pair is
  an inequality on it. A tie is never ruled out, because Showdown breaks ties at random.

  Soundness is the claim, so it is the gate: **9 of 62,581 opposing Pokémon (0.014%) had the truth
  silently excluded** over 25,000 self-play battles, where the spread is in the `input_log` and so
  is actually known. A further 0.035% proved themselves contradictory and widened back to the prior,
  which is reported separately because those state nothing false. Getting there took five fixes and
  **every one produced a wrong inference rather than a missing one**: Mega formes on the hidden side
  and on the known side, Grassy Glide's conditional +1, a Mega's ability differing from its sheet's,
  and state that moved inside the turn. Quick Claw is abstained on outright.

  Power is the separate question and it is modest: 7.2–7.8 racing pairs a game, 28–31% of their
  Pokémon narrowed at all, 13.8–16.8% of the prior ruled out on average. The self-play figure is a
  *floor* and a bad one — the pool's spreads are `impute_sp`'s output, so the truth takes two
  values and the channel is being asked an easy question. A randomized-spread self-play run is the
  next measurement.

  The prerequisite this phase already had now has a second reason: the evidence log lives outside
  `observation()` deliberately, because snapshots embed it and are fingerprinted at VERSION 3.
- **Damage magnitude → a likelihood over spread and item.** _Next._ The evidence is already
  recorded — `vgc.data.observe` now attributes each `|-damage|` to the move that caused it, with the
  field, both sides' boosts, crits, spread flags and a `fainted` marker for the right-censored case
  — and `vgc team weakness` already runs this arithmetic forwards. A move that did 71% to a known defender
  narrows the attacker's offensive SP and item jointly, through the same calc the rest of the stack
  treats as ground truth. `_on_damage` currently writes the defender's new HP and nothing else.
  PLAN-v2 already listed "a calc-based damage likelihood" in this phase; finding 8 says it applies at
  open sheets too, which is where the model is actually trained and gated.

Build, in the order the evidence supports:

1. **SP belief, open sheets.** Everything but the spread is on the sheet, so the belief is over one
   object: the allocation of 66 points, ≤32 per stat. Prior from `impute_sp` and the sheet corpus,
   updated by the two channels above. This is the piece that can be gated on **7,459 battles** —
   though the Speed half was gated on 25,000 self-play battles instead, because soundness needs a
   truth the human corpus does not contain.

   The Speed half also produced a diagnostic worth keeping: when the belief contradicts itself, it
   is usually because the spread *you* supplied for your own team is wrong. The contradiction rate
   is 0.035% against true spreads and 9.8–12.2% against assumed ones, so the flag is a detector for
   bad input and the app should show it rather than swallow it.
2. **Full-set belief, closed sheets.** Set prior from the sheet corpus and usage
   (`P(item, ability, moves, nature | species)`), updated by hard reveals *and* the same two
   channels; K complete-set particles per opponent Pokémon.
3. `WP_v2(o) = E_belief[WP_v1(o completed by particle)]`, reusing the in-battle model as the inner
   model. The app's current stopgap — fill each species with its most common set and evaluate one
   guessed team at full confidence — is replaced; a wrong item becomes uncertain rather than wrong.

_Verification:_ v2 log loss sits between v1-with-oracle-sets (lower bound) and v1-with-prior-only
(upper bound), and moves toward the oracle as information arrives. Belief calibration: the true
value lands in the belief's 80% set ~80% of the time — for SP on open sheets, where the truth is
*not* recoverable from the replay, calibrate instead on self-play, where it is. **Report the sample
size on every verdict**; the closed-sheet half has 779 battles and a gate that ignores that will
mislead, while the SP half has 7,459 and does not have that problem.

_A prerequisite this phase inherits:_ the training mix has to stop being degenerate first. Finding 8
measures the opponent's item/ability/move known-flags at **1.000** across 433,052 in-battle training
rows, so the model has never seen an unknown and cannot have learned what one means. Adding the
closed-sheet shard, and self-play generated with `ots=False`, comes before the belief layer is
evaluated on top of it.

### Phase 9 — Policy strength: EWP and search _(was Phase 6, minus BC — now the prerequisite for 10 and 11)_

`EWP(a) = Σ_b π_opp(b | o) · E_rng[WP(o′ | a, b)]`, with exact transitions from a serialized Showdown
state, common random numbers across actions, both sides pruned to top-k by the policy prior, and
determinization over the Phase 8 belief. `π_opp` is the heuristic. One-ply EWP maximization first,
deeper expectiminimax with WP at the leaves after.

_Verification:_ EWP-greedy beats the heuristic ≥60% over 500 battles **and** holds up against a
held-out opponent that was not its prior. VGC-Bench finds agents "approximately 100% exploitable" by a
trained best-response, so beating one fixed weak deterministic opponent is close to the measurement
that paper shows can be meaningless. Turn latency under the 45 s clock with margin, measured. An
action's EWP matches the realized win rate of that action in held-out self-play within its interval.
Record battles/s — Phase 10 has to afford it.

Then **re-run Phase 6 against this policy**, before Phase 10 uses it.

### Phase 10 — Matchup evaluation ⛔ _(was Phase 7; blocked behind Phase 9)_

**Phase 6 decided the form: on-demand only, and not yet.**

**The precomputed matrix is cancelled.** Dated meta gauntlet, `winrate(A, B)` over the pool, racing
allocator, successive halving — all of it rests on heuristic-vs-heuristic win rate being a valid
team-strength signal, and finding 7 measured that at AUC 0.512 with a reliability of 0.96. There is
nothing to precompute.

**On-demand evaluation survives, behind Phase 9.** The app faces one matchup at a time, so simulating
*that* pairing across all 90 bring/lead combinations needs no generalization and no learned team
function. But it inherits finding 7 exactly: run under the heuristic, those 90 numbers describe what
the bot would do. It waits on a policy whose version of Phase 6 comes back positive.

_Verification:_ re-run Phase 6 against the policy first — that is the gate, not an afterthought.
Then: bring/lead recommendations carry intervals and the number of battles behind them; rankings
correlate positively with real-world results; top-usage and tournament-winning teams land in the
upper half.

### Phase 11 — Team building ⛔ _(was Phase 8; blocked behind Phase 10)_

Q2 and the ranking half of Q3, on top of whatever Phase 10 produced. Surrogate regressor over team
embeddings for breadth, real simulation for the top ~15, never a surrogate number shown as an answer.
SP spreads by breakpoint search plus local search.

_Verification:_ remove one Pokémon from 10 known-strong Reg M-C teams; `vgc team complete` recovers the
real member in its top 5. Weakness reports match published tournament commentary.

### Phase 12 — Interface _(was Phase 9)_

MCP server wrapping the CLI; the three `vgc-advisor` skills rewritten against it; the SP/EV errors
fixed. _Verification:_ in a fresh session, every numeric claim traces to a tool call, not a web guess.

---

## Gates — the rules that apply to all of them

Phase 4 failed its gates for reasons the gates could not express. These rules are the fix.

1. **A gate is scored on the rows it applies to.** In-battle WP and preview WP are separate verdicts.
   One model failing one task does not invalidate the other. (Finding 1.)
2. **`ended_normal` is reported separately from forfeits.** 38.4% of human games end in a forfeit and
   the model scores better on them; pooling flatters every number.
3. **State the power before the threshold.** No verdict from a statistic that cannot distinguish the
   outcomes — the n=15 correlation against a 0.5 gate is the standing example, and it has now been
   removed rather than left in place. A gate records its n and the interval of its statistic, and
   **"beats X" means the whole interval does**. Its replacement needed this applied to it too: the
   first version compared two preview log losses as point estimates and passed `wp-v1-set-full` on
   0.004 nats, which finding 1 says is the width of the whole field. Every `beats_constant` gate now
   carries a battle-clustered interval (`vs_constant`).
4. **Simulator-derived quantities are validated against human outcomes before use.** Phase 6 is the
   instance; the rule is general, and re-runs whenever the policy changes. It has been run once and
   came back negative, so the rule is currently *blocking*, not advisory.
4b. **Count the independent units, not the rows.** Three games of one Bo3 series share both teams and
   both players. Phase 6's intervals are cluster bootstraps over series, because resampling games
   would have claimed precision the corpus does not have. (Finding 7.)
5. **Strength gates include a held-out opponent.** Beating the fixed prior you trained against is not
   evidence of strength.
6. **A failing gate is recorded in the model card and the UI refuses to present that number.** Carried
   unchanged from v1 — it worked, and it is why finding 1 surfaced instead of shipping.

---

## Deferred, pending evidence

Not cancelled — waiting on a specific measurement, named here so it is not rediscovered.

| Deferred | Unblocked by |
| --- | --- |
| **Learned preview / team-strength WP** | A corpus three-plus orders of magnitude larger. The second route — Phase 6 passing, so the simulator could be the target instead of human games — is closed: it failed. (Findings 1, 2, 7.) |
| **Behaviour cloning** | A higher-rated corpus. VGC-Bench's BC worked on 700,000 logs from *high-rating* players; this corpus is 58% unrated, median 1101. (Finding 5.) |
| **Self-play generation for WP training** | A policy that passes Phase 6. The rows do not pay for themselves in training (finding 3), and finding 7 removed the other justification. Generating more under *this* policy buys nothing. |
| **Hyperparameter sweeps on the set encoder** | Nothing — closed off. Every config selected epoch 1 or 2 of 12; the constraint is coverage, not regularization. GPU time is better spent elsewhere. |
| **PPO self-play fine-tuning** | Nothing. Optional-and-last in v1 for technical reasons; cost and the paper's results both confirm it. |
| **The precomputed 30×30 matchup matrix** | Cancelled outright, not deferred. Finding 7 removed the quantity it would have been made of. |
| **Regulation-portable models** (global vocabulary, pretrain on M-B, fine-tune) | Was "do after the Phase 4 gates pass". Now: do after there is a model worth porting. The M-B transfer measurement is still worth having before the 2026-12-02 rotation. |

---

## Model-building practices

v1's nine practices stand. Four are amended or added by the evidence:

1. **Don't train what you can search.** _(unchanged, and now the ordering rule — see The thesis.)_
2. **Behaviour cloning before reinforcement learning** — _amended:_ and only on a corpus whose skill
   level you have measured. Cloning the median ladder game produces a median ladder opponent.
3. **Freeze an evaluation set on day one.** _(unchanged; it is why this review was possible.)_
4. **The simulator is ground truth; the learned model is a search-pruner.** _(unchanged.)_
5. **Calibrate the value head, don't just maximize accuracy.** _(unchanged.)_
6. **Budget variance before compute.** _(unchanged.)_
7. **Watch for policy-induced artifacts** — _amended:_ and *measure* them against human outcomes
   rather than noting them. v1 recorded the heuristic's 85/15 spread as a caveat for eight weeks; one
   afternoon of measurement turned it into a blocker. Phase 6 then found the spread was not even the
   main problem — the *ordering* was chance too.
8. **Tag every artifact with its regulation id.** _(unchanged.)_
9. **Log battles in a replayable format from day one.** _(unchanged.)_
10. **New — measure what a data source buys before scaling it.** A three-way ablation costs an hour
    and would have caught finding 3 before 60,000 battles were generated.
11. **New — check the corpus population before trusting the metric computed on it.** Rating
    distribution, participant concentration, how games end. All three changed the reading here.
12. **New — assert on the environment you think you are paying for.** The sweep ran on CPU for hours
    with `enable_gpu: true` set and nothing checking.
13. **New — check what a format actually reveals before calling it full information.** "Open team
    sheet" reveals five of six things; the sixth is the spread, and the whole speed-and-damage layer
    of the game hangs off it. Eight phases of plan treated OTS as though nothing were hidden.
14. **New — before concluding "no signal", show the predictor was measured.** Phase 6's split-half
    reliability of 0.96 is what makes its negative a statement about the simulator rather than about
    a 15-battle budget. A null result from an unmeasured predictor says nothing.

---

## Risks

| Risk | Handling |
| --- | --- |
| ~~Phase 6 returns negative~~ **— it did** | **Realized 2026-09-20.** The handling stands as written: the product falls back to the deterministic stack — calc, weakness report, usage report, in-battle WP, closed-sheet belief. Still a real tool, and Phases 7 and 8 are untouched. Phase 9 is the route back, not a rewrite. |
| **The closed-sheet corpus (779 battles) is too small to gate Phase 8** | Partly relieved by finding 8: the Stat-Point half of the belief is needed at open sheets too and gates on 7,459 battles. For the closed-sheet half, report n on every verdict, widen by scraping Bo1, and treat belief calibration as directional until the sample grows. |
| Policy too weak → meaningless team rankings | **Confirmed, not a risk any more.** Phase 6 is the hard stop and it runs *before* the matrix; it has already stopped it once. Nothing ranks teams until a policy clears it. |
| Phase 9 produces a stronger policy that still fails Phase 6 | Possible — VGC-Bench's agents are "approximately 100% exploitable", so strength against a fixed opponent need not mean realism. The deterministic stack is the floor either way, and the failure would be cheap to detect because the check is already built. |
| Not enough Reg M-C replays | Established: more replays do not fix preview (finding 1). They still help in-battle WP and the closed-sheet regime, which is where scraping effort should go. |
| Reg M-C rotates 2026-12-02 | L0 spine is the mitigation; [docs/regulation-change.md](docs/regulation-change.md). Note that the deterministic tools (Phase 7) port with the dex and need no retrain — another reason to build them first. |
| 19 GB free disk | Finding 3 helps: 412 MB of self-play snapshots are not earning their keep for training. |
| torch 2.2.2 ceiling | Unchanged: ONNX for inference, plain torch for training. |

---

## Cost and hardware

Unchanged from PLAN.md and still **$0–5 total**, with one correction: the free Kaggle quota was never
actually consumed (finding 6), so the training budget is fully intact. The standing advice holds — for
this project **CPU-hours are worth more than GPU-hours**, which is the opposite of most ML work.
Phases 9 and 10 are CPU-bound simulation; a 32-core box for a few hours (~$1–3) is the rental that
would actually help, not a GPU. Phase 6 cost 30 minutes on this laptop at 27.8 battles/s and needed
no rental at all — and it is worth re-reading that against the seven CPU-hours the sweep spent on a
GPU that was never there.

Spend controls unchanged: prepay, no auto-refill, develop locally first, never leave a pod idle,
Kaggle before paid.

---

## Web app staging

| Stage | After | Adds |
| --- | --- | --- |
| W1 | Phase 3 ✅ partial | `vgc web`, team library, validation, active team, bring/lead ranking. **Still missing:** pokepast.es fetching, calc panel, per-turn input, WP timeline |
| **W2** | **Phase 5** 🟡 | In-battle WP routed to a gate-passing model and banner-flagged ✅; **left:** the per-turn WP timeline |
| **W2b** | **Phase 7** ⏳ | Weakness report and usage report in the library — **the next web work** now that both exist on the CLI, and Phase 6 made it the next *useful* work too |
| W3 | Phase 8 | Belief panel — **for open-sheet games too**, where the unknown is the spread: inferred speed ranges and damage-implied SP, with manual corrections. The stopgap's share display survives |
| W4 | Phases 9–10 | EWP action table with intervals and worst-case replies; on-demand bring/lead simulation; state-reconstruction parity checks; post-game review. Gated on Phase 6 passing against the Phase 9 policy — an app that ranks brings under a policy that fails it would be presenting the bot's opinion as the game's |
| W5 | Phase 11 | Complete-my-team and moveset/SP suggestions |

_Verification (unchanged):_ replay 20 held-out self-play battles through the app's forms using only
public information; reconstructed states must pass parity and the app's numbers must match the CLI's.

---

## Relationship to PLAN.md

PLAN.md is the archive and stays in the repo. It holds what v2 does not repeat:

- the original research findings (VGC-Bench, philmantatsky, the Champions dataset, the SP system);
- the L0–L5b architecture detail, which v2 amends but does not restate;
- the phase 0–4 completion notes and the deviations recorded against each;
- the Phase 4 sweep post-mortem, which reached the coverage-not-regularization diagnosis that
  [docs/phase4-findings.md](docs/phase4-findings.md) then measured;
- PLAN.md's observation that "67% of pairings land ≥85/15 under heuristic-vs-heuristic", which is the
  caveat [docs/phase6-findings.md](docs/phase6-findings.md) finally turned into a measurement.

Read v2 for what to do next. Read PLAN.md for why the stack is built the way it is.

---

## Sources

- [VGC-Bench (arXiv 2506.10326)](https://arxiv.org/html/2506.10326.pdf), Angliss, Cui, Hu, Rahman & Stone, AAMAS 2026 · [code](https://github.com/cameronangliss/vgc-bench) (MIT)
- [philmantatsky/VGC-Pokemon-Showdown-AI](https://github.com/philmantatsky/VGC-Pokemon-Showdown-AI) (MIT) — Reg M-B Champions port
- [vbbjandrade/pokemon-champions-data](https://github.com/vbbjandrade/pokemon-champions-data) (CC BY 4.0)
- [poke-env](https://github.com/hsahovic/poke-env) · [smogon/damage-calc](https://github.com/smogon/damage-calc) · [ychen022/VGCHelper](https://github.com/ychen022/VGCHelper) (unlicensed — reference only)
- [MetaVGC Reg M-C](https://metavgc.com/regulations/regulationm-c) · [Victory Road](https://victoryroad.pro/champions-regulations/) · [Pikalytics Reg M-C](https://www.pikalytics.com/pokedex/gen9championsvgc2026regmc) · [ChampDex Stat Points](https://champdex.com/guides/stat-points)
- In-repo: [docs/phase6-findings.md](docs/phase6-findings.md) · [docs/phase4-findings.md](docs/phase4-findings.md) · [docs/phase0-findings.md](docs/phase0-findings.md) · [docs/cloud-compute.md](docs/cloud-compute.md) · [docs/regulation-change.md](docs/regulation-change.md) · [docs/web-app.md](docs/web-app.md)
