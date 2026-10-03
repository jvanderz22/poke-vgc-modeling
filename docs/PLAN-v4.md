# VGC Reg M-C Model & Advisor — Plan v4

_Written 2026-10-01; updated through 2026-10-02 (step 1 closed out, the app deployed, the reference runs scored, the 2v2 tail checked)._ **Supersedes [PLAN-v3.md](PLAN-v3.md)**, which is kept as the archive of steps
1–9 as they ran (2026-09-28 to 2026-10-01): open-sheet calibration, the set-use benchmark, the 1v1
engine in the app, Watching mode, the Speed prior, closed-sheet routing, eval manifests, solver speed,
and the start of the doubles endgame. Code and tests cite "PLAN-v3 step N", so v3 keeps its step
numbers and stays where it is. [PLAN-v2.md](PLAN-v2.md) and [PLAN.md](PLAN.md) are the older
archives.

v4 restates only what is needed to decide what to do next. Steps restart at 1.

---

## Where things stand

| Capability | State |
| --- | --- |
| Engine, calc, regulation config, battle data pipeline | ✅ Pinned Showdown, `@smogon/calc`, frozen splits, snapshots v4, eval manifests and an eval-set cache |
| Battle state (L1b) | ✅ One state object, two adapters: Showdown logs (`Observer`) and a person's taps (`battle.entry`) |
| Deterministic team tools | ✅ `vgc team weakness` (breakpoints in Stat Points), `vgc meta usage` |
| Belief over hidden sets | ✅ Speed (reads the item), switch-in order, damage, bulk, SP budget, set prior. Damage and bulk are sound on open sheets only and do not run on closed ones |
| In-battle win probability | ✅ Pinned per regime: `wp-v1f-idp5` open, `wp-v1d-sw-split-small` closed. Both pass the powered calibration test. Neither follows what decides an endgame |
| Endgame engine, 1v1 | ✅ Depth 3 on real games. Leads the page in both regimes: open Brier 0.095 against 0.206, closed log loss 0.345 against 0.649 |
| Endgame engine, 2v1 / 1v2 / 2v2 | ✅ Open sheets: leads the page, log loss 0.384 against the model's 0.513 (674 games; 2v2s 0.485 against 0.621), within 5 s through the page's own path (median 2.8 s). 🟡 Closed sheets: no state kind beats the model, so the model leads there |
| Pre-battle (preview) win probability | ❌ Not learnable from this corpus. Preview advice is "what to bring", not "you are favoured" |
| Simulator as a measure of team strength | ❌ Heuristic self-play does not predict human results (AUC 0.512). Blocks matchup and team evaluation until a stronger policy passes the same check |
| Web app | 🟡 Library, brings ranking, and the live Battle page in open, closed and Watching modes, with the engine leading in endgames where it passed. Deployed at [vgc-live-battle-calculator.fly.dev](https://vgc-live-battle-calculator.fly.dev) behind a password |

What got here, in one line each (detail in [PLAN-v3](PLAN-v3.md) and the findings docs):

| v3 step | Outcome |
| --- | --- |
| 1. Open-sheet calibration | 20% of groups as validation; the turn-slope temperature passes out of fold. `wp-v1f-idp5` pinned |
| 2. Does the model use what is revealed? | No. Separation 0.07–0.15 against the solver's ≈ 0.95 on decided 1v1s |
| 3. The engine in the app, 1v1 | Beats the model on 174 human 1v1s; leads the page |
| 4. Watching mode | Both sheets open, both spreads hidden; also how replays are scored |
| 5. Speed prior reads the item | A Speed item's 0-SP extreme moves to 32; better on every cut |
| 6. Belief-to-app | On a closed sheet the position as shown leads (−0.015 nats). Damage and bulk fail soundness there |
| 7. Housekeeping | Eval manifests, eval-set cache, `vgc wp valcheck`, stale datasets deleted |
| 8. Solver speed | Secondaries as one chance node, exact multi-hit, three choice bugs fixed. Depth 3 on 144 of 204 games |
| 9. Phase 9, first milestone (in progress) | [PLAN-endgame-doubles](PLAN-endgame-doubles.md) stages 0–4 done for open sheets; carried into step 1 below |

---

## Standing principles

These come from measurements or explicit decisions. Each one has been broken at least once.

1. **Compute what can be computed. Learn only where a measurement shows learning pays.** Never
   build on a simulator-derived quantity that hasn't been checked against human outcomes.
2. **A WP model must use revealed set information.** A set-blind model is not served, however
   good its log loss.
3. **Never put an opponent's spread into a model input.** It is `None` in every training row.
4. **Every number is a property of the regime it was measured in:** open team sheets (OTS) or
   team preview only (TPO). A channel that was 0% wrong under OTS was 4.3% wrong under TPO.
5. **Count independent units, not rows.** The unit is the Bo3 series or player pair. Anything
   drawn from human data is drawn by group.
6. **State the power before the threshold.** "Beats X" means the whole interval does. A test
   that could not have failed returns *undecided*, not pass.
7. **A gate may not condition on the future.**
8. **Fit anything post-hoc on rows the model never trained on,** drawn the way the gate draws.
9. **Test a mechanism on a model that reads its inputs.** Check that the features and the
   predictions moved before believing a null result.
10. **A failing gate is recorded on the model card, and the app does not present that number as
    calibrated.**
11. **The engine leads the page only where it beat the model on held-out human games,** per state
    kind and per regime, and the model stays on screen underneath. (v3 steps 3 and 9.)
12. **A live answer lands within about 5 seconds, planning included.**

---

## Architecture

```
L4  Team building      weakness report · slot completion · moveset & SP search
L3  Team evaluation    on-demand matchup evaluation (the precomputed matrix is cancelled)
WP  Win probability    in-battle WP per regime · belief over hidden sets · endgame engine (≤2 a side) · EWP(action)
L2  Battle policy      heuristic → search (expectiminimax, EWP at the leaves)
L1b Battle state       vgc.battle.state · Observer and entry adapters · vgc.battle.rules
L1  Engine             pinned Showdown (Champions) · @smogon/calc 0.12.0 · poke-env 0.16.1
L0  Regulation config  legal pool · clauses · mechanics flags · SP rules · format id
```

**Versioned interfaces:** `observation()` (golden-tested); `snapshots.VERSION` 4; `Featurizer.VERSION`
3. Every dataset and model card records the featurizer version, and scoring refuses a mismatch. The
solver's cache key hashes the version the solver declares (`const VERSION` in
`endgame-solver.js`), not its source: bump it with any change that could move an answer, or the
cache keeps the old answers (`check_cache` re-solves a sample to catch a forgotten bump). A bump
invalidates the benchmark's depth-4 truth, which must then be re-solved (hours, on Kaggle).

---

## Served models (`models/served.json`)

| Role | Model | Status |
| --- | --- | --- |
| `bring` | `wp-v1f-idp5` | Top-4 overlap 0.702 vs usage 0.672 |
| `in_battle_open` | `wp-v1f-idp5` | Passes `in_battle_ece` (4,127 battles, power 1.0); t7+ passes by 0.0001 |
| `in_battle_closed` | `wp-v1d-sw-split-small` | Passes `closed_sheet_pass` (1,085 battles, power 0.98) |

The endgame engine is not a registered model. Where it leads is decided per state kind and regime
in `vgc.web.solving` (`doubles_reason`). Its doubles horizon (`MELEE_BLEND_BOOSTS` in the solver)
and the answer's temperature (`doubles.TEMPER`) are fitted on training games by
`scripts/analysis/race_calibration.py`; a change of answers takes a new `race_doubles` name.

---

## Next, in order

### 1. Finish the doubles endgame (done 2026-10-01)

[PLAN-endgame-doubles](PLAN-endgame-doubles.md) has the detail; phase8-findings has the numbers.

**Done:**
- **Closed sheets stay with the model.** Closed 2v1s had seemed to pass, but a 2v1 and a 1v2 are
  one state with the sides named the other way round, and pooled (135 games) neither score is
  distinguishable. `solver_vs_humans.py` now reports the pool (`2v1|1v2`).
- **Grouping the closed-sheet set belief (stage 5): tried and reverted.** It took the solved share
  of the belief from 6.3% to 9.4% (aim 75%), and 11 of 65 classes disagreed between members by
  more than 0.05. The spread is real uncertainty over four hidden Pokémon, not duplicate sets.
- **The horizon, which is about 95% of a 2v2 answer.** The race blended with HP share, the count
  and the net stat stages (`race_doubles: 'blend_boosts'`), and the answer through a temperature
  (`doubles.temper`): taking the best of noisy leaf values had made it too sure. All fitted on
  training games (`scripts/analysis/race_calibration.py`, which reproduces every map). Held out:
  log loss 0.421 → 0.384, Brier 0.130 → 0.122; 2v2s 0.527 → 0.485. The page uses both.
- **Tried and dropped:** the race applying the stat stages its moves change (level on training
  games).
- **The page's own path** (`scripts/analysis/page_path.py`, 112 games, laptop): first answer
  median 1.3 s, final median 2.8 s, max 5.1 s; the deadline costs about 0.005 in log loss.
- **2v2 answers at 95% or more are too sure, and a fix does not pay.** Folded to the favoured side,
  128 games (training and held out) answered about 0.975 and won about 0.93: real, 2v2 searched
  answers only. The one read closely was a player missing the engine's winning line. A 2v2 cap
  fitted on training games is level held out (−0.001 [−0.012, +0.007]); not shipped.
- **No deeper background search.** The Kaggle reference runs (`doubles2-{a,b,c}`: the KO
  extension, `prune` 3, 16 draws; 1,760 positions) on the same horizon as the live search: log loss
  +0.017 [−0.015, +0.052], Brier level, and no better with a cross-fitted temperature. More search
  below the horizon adds nothing (phase8-findings, "a wider one-turn search").

**Open:**
- **Time the doubles answer on the deployed machine.** Taken by hand, outside this plan's steps.
  Shared cores burst on credit; if answers land late, more of them keep the quick value (see the
  cloud row below). `page_path.py --gap` paces the games as turns would.

### 2. Phase 9: policy strength (EWP and search)

The doubles engine is this search's bottom layer: joint choices with targets, realistic-play
pruning, chance at doubles scale, Speed integrated over four Pokémon, and an oracle for any line
that reaches two or fewer a side, in the state kinds where it passed.

`EWP(a) = Σ_b π_opp(b | o) · E_rng[WP(o′ | a, b)]`:
- exact transitions from a serialized Showdown state (the solver already does this);
- chance enumerated, not sampled per node: with few seeds a node, each player effectively sees
  the dice before choosing. The solver's `ScriptedPRNG` is the starting point;
- top-k pruning by the heuristic prior, measured as the doubles pruning was;
- determinization over the set belief, spreads included;
- the engine at leaves with two or fewer a side, the model above that.

**Gates:**
- EWP-greedy beats the heuristic ≥60% over 500 battles **and** holds against a held-out opponent;
- latency under the 45 s turn clock;
- EWP matches realised win rate within its interval.

The first deliverable is a written stage plan, as PLAN-endgame-doubles was, with the leaf choice
and the bench (switches return above two a side) worked out before anything is built.
**Written 2026-10-02: [PLAN-policy](PLAN-policy.md).** In short: a design point of about 1 s a
decision on one core, set by what step 3 can afford (about 12,000 battles); one turn deep over
pruned joint choices with switches; the damage race extended with reinforcements as the leaf,
with the model as the bar it must beat on held-out games; Nash and a π_opp fitted to people both
run through the gate. About 9 days plus a night or two on Kaggle, most of it step 3's run.
**Done 2026-10-03** ([phase9-findings](phase9-findings.md)): the policy fitted to people passes,
0.678 against the heuristic and 0.692 against a held-out opponent, at a median 0.74 s a decision.
The pilot's spread (s = 0.356, split-half 0.96) keeps step 3 at 1,500 pairings × 8 battles.

### 3. Re-run Phase 6 against the Phase 9 policy

The simulator-validity check, unchanged: does policy-vs-policy win rate between two teams predict
the human series result? It is built (`phase6-findings`). A pass unblocks steps 6–7; a fail means
the deterministic stack stays the floor.

### 4. Ready for the 2026-12-02 rotation

Has a date, so it runs alongside steps 2–3 and must be done by mid-November.
- **L0 config for the next regulation** as soon as its rules are public
  ([regulation-change](regulation-change.md)).
- **The set encoder's shared vocabulary,** so `wp-v1f`'s successor can warm-start on M-C games.
  GBT already showed the old regulation's games are worth nearly a new regulation's.
- **What carries over without retraining:** the team tools, the belief channels (re-gated per
  regime on the new data), and the endgame engine, which needs only the pinned simulator to know
  the new mechanics. List what does not.

### 5. Loose ends, taken when they block something

- **A seed ensemble for the WP models.** Every run stops at epoch 3–4, and where it stops sets the
  confidence. Serving one needs `SetModel` to take several exports.
- **The benchmark's illegal items** (F5's Choice Band, F11's Assault Vest, a filler's Choice
  Specs). Replacing them redesigns those families and means a depth-4 re-solve.
- **Damage and bulk on closed sheets** (6.6% and 2.2% silently wrong). Bounding an unrevealed item
  or ability over what the set belief allows.
- **The assumed non-Speed spread in doubles,** where spread moves put more KOs on a threshold.
  Integrating the bulk thresholds the way Speed is integrated.

### 6–7. Blocked behind step 3

- **Phase 10: matchup evaluation.** On demand only: the one matchup in front of the user across
  its bring/lead combinations.
- **Phase 11: team building.** Slot completion and moveset/SP search, verified by recovering a
  removed member of 10 strong teams in the top 5.

Phase 12 (an MCP server wrapping the CLI, every numeric claim traced to a tool call) is not
blocked and can be taken whenever it is wanted.

---

## Parallel track: the web app

Independent of the steps above. Design and API: [web-app](web-app.md).

| Stage | Missing |
| --- | --- |
| W1 library, validation, bring/lead ranking | pokepast.es import, calc panel |
| W2 in-battle WP with gate banner | per-turn WP timeline |
| W2b weakness and usage reports in the library | all of it |
| W3 live battle | damage snapped to calc buckets; closed-sheet doubles (the model leads there) |
| W4 EWP action table, on-demand bring/lead simulation | behind steps 2–3 |
| W5 complete-my-team, moveset/SP suggestions | behind step 7 |
| Video mode | [PLAN-video](PLAN-video.md): WP following a cartridge video of an open-sheet battle. Nothing built |
| Cloud | **Deployed 2026-10-01** at [vgc-live-battle-calculator.fly.dev](https://vgc-live-battle-calculator.fly.dev) ([deploy/README](../deploy/README.md)): one `shared-cpu-4x` 2 GB machine that stops when idle, three solver processes, 1v1s to depth 3, HTTP Basic password, a 1 GB volume for teams, battles and the solver cache. `./deploy_fly.sh` redeploys from the working tree. Stops by itself about 6 min after its last request; a cold start answers in 3.8 s. Missing: the doubles answer timed on shared cores |

---

## Gates, as implemented (`vgc.wp.evaluate`)

- **Per regime.** `in_battle_pass` needs `in_battle_beats_constant` and `in_battle_ece`.
  `closed_sheet_pass` is the same pair on the TPO shard.
- **`beats_constant`:** a battle-clustered 95% interval, wholly below zero. Preview is gated
  separately.
- **ECE:** the model's ECE per turn bucket against what a model miscalibrated by up to 1.1× would
  score on the same rows; one outcome draw per battle, Bonferroni over buckets at α 0.05. A pass
  needs ≥80% power against logits 1.5× too sharp, else *undecided*.
- **Reported, not gated:** played-out calibration, forfeit vs played-out splits,
  `player_beats_spectator`.
- **Calibration** (`vgc wp calibrate`): fit on the validation split (20% of groups), a temperature
  at turn 0 plus a slope per turn, per context and regime.
- **The engine against the model** (`scripts/analysis/solver_vs_humans.py`): log loss and Brier,
  cluster bootstrap by group, by state kind and regime, with 2v1 and 1v2 judged pooled
  (`2v1|1v2`). `--temper` scores the answer as the page shows it. This decides where the engine
  leads; `scripts/analysis/page_path.py` checks the page's own path (time and score).

---

## Deferred, pending evidence

| Deferred | Unblocked by |
| --- | --- |
| Learned preview / team-strength WP | A corpus 3+ orders of magnitude larger |
| Behaviour cloning | A higher-rated corpus. The highest rating seen is 1578 |
| Self-play generation for WP training | A policy that passes Phase 6 |
| The belief's own P(faster) as a model input | Training rows where a spread is known (self-play only) |
| Double oracle over the full doubles matrix | A pruning gap too large to accept |
| The F10 benchmark's truth from the solver | It tests the models, not the solver |
| Revival Blessing with real fainted teammates | A position that needs it |
| Hyperparameter sweeps on the set encoder, a GBT calibration path, PPO, the precomputed matchup matrix | Closed or cancelled |

---

## Practices (the short list)

1. **Measure what a data source buys before scaling it.** An ablation costs an hour.
2. **Check the corpus population before trusting a metric on it:** rating, concentration, how
   games end.
3. **Assert on the environment you're paying for.**
4. **Check what a format actually reveals before calling it full information.**
5. **Before concluding "no signal", show the predictor was measured.**
6. **Tag every artifact with regulation, snapshot version and featurizer version.**
7. **Land solver changes together and re-solve the benchmark once.**
8. **CPU-hours are worth more than GPU-hours here.** The budget is $0–5 total: Kaggle before paid,
   no auto-refill. See [cloud-compute](cloud-compute.md). The deployed app is the one standing
   cost: it stops when idle (about $0.30 a month stopped) and sits under a hard spend cap.
9. **A split by which side is p1 is not a split.** A 2v1 and a 1v2 are one state; judge them
   pooled, or a half that passes by chance gets shipped.
10. **Fit a calibration where the answer is made.** The horizon fitted at depth 0 could not see
    that the search's max over noisy leaves pushes answers outward; a temperature fitted on
    answers produced exactly as the page produces them could.

---

## Risks

| Risk | Handling |
| --- | --- |
| Phase 9's policy is stronger but still fails Phase 6 | The deterministic stack and the endgame engine are the floor |
| Phase 9 misses the 45 s clock | The doubles work already answers ≤2 a side in 5 s; prune harder above that, and report depth |
| Reg M-C rotates 2026-12-02 | Step 4, with a mid-November target |
| The closed-sheet belief does not concentrate enough | Happened (stage 5). The model keeps leading closed doubles; the engine's answer stays open-sheet only |
| The cloud machine never stops, or its solves run long | Auto-stop with no minimum, one machine, 1v1s capped at depth 3, a password, and a hard spend cap in the dashboard |
| Shared cores miss the 5 s doubles answer | The page keeps the quick answer for late move orders (it costs about 0.005 on the laptop); a `performance-1x` machine is the next size up |
| Disk (18 GB free) | Retired models and unreferenced eval sets (`vgc wp prune-eval-cache`) go first |

---

## Documents

- [PLAN-v3](PLAN-v3.md): archive of steps 1–9 (2026-09-28 to 2026-10-01). [PLAN-v2](PLAN-v2.md):
  Phases 4–8. [PLAN](PLAN.md): original research, full architecture, phases 0–4.
- [PLAN-endgame-doubles](PLAN-endgame-doubles.md): the solver for 2v1, 1v2 and 2v2 (step 1).
- [PLAN-policy](PLAN-policy.md): Phase 9, the policy over whole battles (step 2).
- [PLAN-video](PLAN-video.md): video mode.
- [deploy/README](../deploy/README.md): the Fly.io deployment, its sizing and cost.
- Findings: [phase0](phase0-findings.md) · [phase4](phase4-findings.md) · [phase9](phase9-findings.md) ·
  [phase6](phase6-findings.md) · [phase8](phase8-findings.md).
- [regulation-change](regulation-change.md) · [cloud-compute](cloud-compute.md) ·
  [web-app](web-app.md).
- Sources: [VGC-Bench](https://arxiv.org/html/2506.10326.pdf) ·
  [philmantatsky port](https://github.com/philmantatsky/VGC-Pokemon-Showdown-AI) ·
  [Champions data](https://github.com/vbbjandrade/pokemon-champions-data) ·
  [poke-env](https://github.com/hsahovic/poke-env) · [damage-calc](https://github.com/smogon/damage-calc).
