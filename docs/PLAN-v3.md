# VGC Reg M-C Model & Advisor — Plan v3

_Written 2026-09-28._ **Supersedes [PLAN-v2.md](PLAN-v2.md)**, which is kept as the archive of how
we got here: every measurement, dead end and correction, with its reasoning. [PLAN.md](PLAN.md) is
the older archive (original research, architecture detail, phases 0–4). v3 restates only what is
needed to decide what to do next. Detail lives in the findings docs, linked where it matters.

---

## Where things stand

| Capability | State |
| --- | --- |
| Engine, calc, regulation config, battle data pipeline | ✅ Built. Pinned Showdown, `@smogon/calc`, frozen splits, snapshots v4 |
| Battle state (L1b) | ✅ One state object fed by two adapters: Showdown logs (`Observer`) and a person's taps (`battle.entry`) |
| Deterministic team tools | ✅ `vgc team weakness` (breakpoints in Stat Points), `vgc meta usage` |
| Belief over hidden sets | ✅ Speed, switch-in order, damage, bulk, SP budget, set prior. All gated for soundness |
| In-battle win probability | ✅ Served and pinned per regime: `wp-v1f-idp5` on open sheets, `wp-v1d-sw-split-small` on closed sheets. Both pass the powered calibration test |
| Endgame solver (1v1) | ✅ `vgc wp solve`: minimax over the pinned engine, with chance enumerated and crits included. ⏳ Not in the app yet (step 3) |
| Pre-battle (preview) win probability | ❌ Not learnable from this corpus (finding 1). Preview advice is "what to bring", not "you are favoured" |
| Simulator as a measure of team strength | ❌ Heuristic self-play does not predict human results (AUC 0.512). Blocks matchup and team evaluation until a stronger policy passes the same check |
| Web app | 🟡 Library, brings ranking, and the live Battle page with belief panels and WP |

---

## What has been done

Short summaries. For detail, see [PLAN-v2.md](PLAN-v2.md) and the findings docs.

- **Phases 0–3: foundation.** Environment spike (36.5 battles/s), `vgc` CLI, regulation loader
  (L0), calc sidecar, heuristic battle policy (98.4% vs random), seeded self-play runner, and
  snapshot extraction for every perspective. The held-out splits are frozen in
  `data/splits/reg_mc.json`: held-out teams, held-out human groups (a Bo3 series or a player
  pair), and held-out self-play battles. [phase0](phase0-findings.md)
- **Phase 4: WP v1.** In-battle WP works; preview WP does not. Eight findings reshaped the plan
  ([phase4](phase4-findings.md)). The ones that still steer work:
  - **Preview WP is not learnable here.** Every model family lands within 0.004 nats of the
    constant.
  - **Self-play adds nothing to the human-scored model.** Human-only training matches the full
    mix.
  - **The corpus is mostly ~1100-rated play,** and 38% of games end in a forfeit.
  - **An open team sheet hides the 66 Stat Points,** so speed order and exact damage are unknown
    in both regimes.
- **Phase 5: in-battle WP shipped.** Bucketed gates, routing to a gate-passing model, and a gate
  banner in the app.
- **Phase 6: simulator validity, negative.** Heuristic-vs-heuristic win rate is precise
  (split-half reliability 0.96) and does not predict who wins the human game (AUC 0.512 over
  2,684 series). The precomputed matchup matrix is cancelled.
  [phase6](phase6-findings.md)
- **Phase 7: deterministic team tools.** A weakness report stated as breakpoints ("OHKOes your
  Incineroar from 0 Atk SP, always from 14"), and usage stats from 15,028 sheets.
- **Phase 8: belief over hidden sets** ([phase8](phase8-findings.md)).
  - **Five channels, all gated for soundness.**
    - Speed from turn order: 0.029% silently wrong.
    - Switch-in order: 0 of 6,000 battles.
    - Damage: 0 of 2,866.
    - Bulk: 0.178%.
    - The 66-point budget joins them and adds no error of its own.
  - **Set prior:** the likeliest ability is right 94.3% of the time on held-out teams.
  - **The app's WP averages over 24 drawn opponents**, with a band showing what their hidden sets
    are worth.
  - **The closed-sheet corpus is in training:** 6,845 cached replays extracted, 1,088 held-out
    battles.
  - **Speed orderings and the last move used are model inputs** (featurizer v3): false 0.006% of
    the time with sheets open and 0.008% with them hidden. Building them fixed four log-reading
    faults, one of which (Trace) corrupted 1 replay stream in 6.
- **Open-sheet calibration** ([phase8, "wp-v1f"](phase8-findings.md)). Human validation is 20%
  of groups (2,487 battles, up from ~590), and with it the existing turn-slope temperature passes
  the powered test out of fold in both regimes. No training treatment beat seed noise.
  `wp-v1f-idp5` passes every gate. Re-carded under the current test, `wp-v1d` passes too: "fails
  every model" predated the 1.1× tolerance.
- **Served models are pinned** per role and regime in `models/served.json`. The regimes are named
  once (`SHEETS`, `REGIME_GATES` in `vgc.wp.models`), and the app's open/closed toggle is built
  from that list.
- **The decided-endgame benchmark is scored** (`vgc wp benchmark`,
  [phase8, "decided-endgame benchmark"](phase8-findings.md)). Each variant is a hand-entry journal
  scored through the app's WP. The set-blind GBT moves by exactly 0 on every set fact. The set
  encoders move the right way on 41–69% of pairs, but only **2–3% of the distance**. The truth
  now comes from **a solver over the engine** (crits included). Against it, no model separates a
  lost 1v1 from a won one: 0.07–0.15, against ≈ 0.95.

---

## Standing principles

These come from measurements or explicit decisions. Each one has been broken at least once.

1. **Compute what can be computed. Learn only where a measurement shows learning pays.** Never
   build on a simulator-derived quantity that hasn't been checked against human outcomes.
2. **A WP model must use revealed set information.** A set-blind model is not served, however
   good its log loss. `wp-v1d-gbt` scores better than the set encoder and is not served.
3. **Never put an opponent's spread into a model input.** It is `None` in every training row.
4. **Every number is a property of the regime it was measured in:** open team sheets (OTS) or
   team preview only (TPO). A channel that was 0% wrong under OTS was 4.3% wrong under TPO.
5. **Count independent units, not rows.** The unit is the Bo3 series or player pair. Anything
   drawn from human data (held-out sets, validation, bootstraps) is drawn by group.
6. **State the power before the threshold.** "Beats X" means the whole interval does. A test
   that could not have failed returns *undecided*, not pass.
7. **A gate may not condition on the future.** For example, whether a game will be played out or
   forfeited isn't known at prediction time.
8. **Fit anything post-hoc (temperatures, calibrators) on rows the model never trained on,**
   drawn the way the gate draws, with enough of them to match the gate's precision.
9. **Test a mechanism on a model that reads its inputs.** Check that the features moved and the
   predictions moved before believing a null result.
10. **A failing gate is recorded on the model card, and the app does not present that number as
    calibrated.**

---

## Architecture

```
L4  Team building      weakness report · slot completion · moveset & SP search
L3  Team evaluation    on-demand matchup evaluation (the precomputed matrix is cancelled)
WP  Win probability    in-battle WP per regime · belief over hidden sets · 1v1 endgame solver · EWP(action)
L2  Battle policy      heuristic → search (expectiminimax, EWP at the leaves)
L1b Battle state       vgc.battle.state · Observer and entry adapters · vgc.battle.rules
L1  Engine             pinned Showdown (Champions) · @smogon/calc 0.12.0 · poke-env 0.16.1
L0  Regulation config  legal pool · clauses · mechanics flags · SP rules · format id
```

**Versioned interfaces:**
- `observation()`: golden-tested.
- `snapshots.VERSION` 4: adds the `evidence` field (Speed orderings and the last move used).
- `Featurizer.VERSION` 3:
  - v2 adds weather and terrain turns remaining;
  - v3 adds the evidence columns.

Every dataset and model card records the featurizer version it was built with, and scoring
refuses a mismatch.

---

## Served models (`models/served.json`)

| Role | Model | Status |
| --- | --- | --- |
| `bring` | `wp-v1f-idp5` | Top-4 overlap 0.702 vs usage 0.672; preview interval [−0.0107, −0.0036] |
| `in_battle_open` | `wp-v1f-idp5` | Passes `in_battle_ece` (4,127 battles, power 1.0): t1-2 ECE 0.019, t7+ passes by 0.0001 |
| `in_battle_closed` | `wp-v1d-sw-split-small` | Passes `closed_sheet_pass` (1,085 battles, power 0.98). Log loss 0.5633 against `wp-v1f-idp5`'s 0.5665 |

`wp-v1d` also passes open sheets under the current test (t1-2 by 0.0006, re-carded). It fails
`player_beats_spectator`.

Registered and not pinned:
- `wp-v1e-sw-split-small`: an uncalibrated epoch-4 retrain that fails open t1-2.

---

## Next, in order

### 1. Make in-battle WP calibrated on open sheets: done

Details: [phase8 findings, "wp-v1f"](phase8-findings.md).
- ✅ **Treat the cause in training.** Tried; nothing beat seed noise. Two seeds of one recipe
  asked for temperatures of 1.10 and 0.87. Masking identity on preview rows
  (`--id-dropout-preview 0.5`) is the recipe now, on direction and because it costs nothing.
- ✅ **A calibrator on 20% of human groups.** The existing turn-slope temperature passes out of
  fold. Beta calibration added nothing, so it was not built.
- ✅ **Pin by the gate** (2026-09-28).
  - `wp-v1f-idp5` for open sheets and brings. It fixes the early-turn miss, passes `all_pass` and
    reads the Speed orderings. It passes open t7+ by 0.0001.
  - `wp-v1d` stays on closed sheets, where it is 0.003 nats better and also passes.
- **The fragility is early stopping.** Every run stops at epoch 3–4, and where it lands sets the
  confidence. A seed ensemble would reduce it, but serving one needs `SetModel` to take several
  exports.

### 2. Check that the model uses what is revealed: done

Details: [phase8, "decided-endgame benchmark" and "endgame solver"](phase8-findings.md).
- ✅ **The benchmark runner** (`vgc wp benchmark`). It scores direction, distance, mixing,
  invariance, and decided positions.
- ✅ **The solver** (`vgc wp solve`). It is 1v1 minimax over simultaneous moves on the pinned
  engine:
  - chance enumerated, crits included;
  - their Speed integrated over the prior;
  - positions cached as they finish.
- ✅ **The answer: no.** The models move 0–9% of the way on Speed orderings and choice lock. Against
  the solver, no model separates a lost 1v1 from a won one: separation is 0.07–0.15, against
  ≈ 0.95. `wp-v1f-idp5` says 0.69 where the answer is 1 in 24.
- ⛔ **The featurizer-v2 ablation is moot.** Nothing moves the models, so there is no effect to
  split.
- **2v2 families wait for Phase 9's search.** A second active Pokémon a side multiplies the
  branching by about 16.

### 3. The solver's answer beside the model's in the app, for 1v1 endgames

In decided 1v1s the model's WP barely depends on the position. The solver gives the engine's
answer under best play, which is a different claim from what a human game will do. Until the
solver has been checked against human outcomes (principle 1), the Battle page shows **both**,
labelled, and neither is presented as *the* number.

1. **When:** both sides have exactly one Pokémon left. The model's number stays on screen
   throughout; the solver's appears beside it.
2. **The position from the live state.** An adapter from `BattleState` to a solver position:
   - ours exactly, from the team we built;
   - theirs from the sheet or what has been revealed: item, ability, moves, nature. On a closed
     sheet, the belief's likeliest sets, solved each and weighted, with the rest reported as
     unsolved mass;
   - their Speed from the SP belief, narrowed by this battle's orderings, not the bare prior;
   - HP, stat stages, status, items consumed, the choice lock, weather, terrain and Trick Room
     turns, the fainted counts (Last Respects), and whether each Pokémon just came in (Fake Out).
3. **The same answer twice.** Run every benchmark variant through its hand-entry journal, the
   live state and the adapter. The result must reproduce `solved.json` within `leaf_mass`. That
   checks the adapter against positions whose answers are already known.
4. **Latency.** A solve takes seconds to hours; the page cannot wait.
   - Solve in the background with iterative deepening (depth 1, 2, 3, …).
   - Show the deepest finished answer with its depth and `leaf_mass`, and update it as it deepens.
   - Reuse the position cache.
5. **Display.** Two labelled numbers:
   - the model's, with its gate verdict as now;
   - the engine's, with the assumptions under it: best play on both sides, their spread's
     non-Speed stats assumed, the depth searched.
   
   When the two disagree by more than ~20 points, say so rather than averaging.
6. **The check that decides what the numbers become.** Held-out human 1v1 endgames from replays:
   log loss and calibration of the solver against the model, clustered by battle. The solver
   assumes best play, and the corpus is ~1100-rated, so this can come out either way. Until it
   is run, both numbers stay second opinions of each other.

### 4. Condition the Speed prior on the item

A Choice Scarf set with under 4 Speed SP is not real, but `speed_prior` gives it 27% (F1-B), and the
solver inherits that. The prior should read the item, as the belief already does for nature.

### 5. Solver speed

F6 alone took 2 h 50 min, and step 3's latency depends on this. It is also Phase 9's search. The
levers:
- split one position across workers;
- reuse results across depths (iterative deepening wants this anyway);
- prune dominated moves.

### 6. Remaining belief-to-app work

1. **`wp` vs `wp_open` on the real closed-sheet shard,** before changing which one the Battle page
   leads with. On a live position the two were 11–14 points apart.
2. **The brings preview still guesses one set** (`web/prior.compose`). Replace it with the
   particle average the Battle page uses.
3. **Re-gate damage and bulk under TPO.** Both read the opponent's item and ability off a sheet a
   cartridge doesn't show. The app doesn't run them live until they pass.

### 7. Housekeeping that pays on every retrain

- **An eval manifest, then an eval-set cache** keyed on shard sha256 and featurizer version.
  Self-play eval sets already come from the training manifest's runs, so this is the durable
  version of that.
- **`vgc wp valcheck`** scores a model's uncalibrated reliability on human validation. Use it to
  compare recipes without reading the held-out gate.
- **Caches that go stale:**
  - the stored decided-endgames list must be regenerated whenever the served model or its
    calibration changes (`tests/test_endgames.py` catches it);
  - the `wp-v1`/`wp-v1c` feature datasets (~586 MB, untracked) can be deleted.

### 8. Phase 9: policy strength (EWP and search)

`EWP(a) = Σ_b π_opp(b | o) · E_rng[WP(o′ | a, b)]`:
- exact transitions from a serialized Showdown state (the endgame solver already does this);
- chance enumerated, not sampled. The solver found sampling biased: with few seeds a node, each
  player effectively saw the dice before choosing. Its `ScriptedPRNG` is the starting point;
- top-k pruning by the heuristic prior;
- determinization over the Phase 8 belief, spreads included. This is where a hidden Scarf acts
  through the engine rather than having to be learned.

The gates:
- EWP-greedy beats the heuristic ≥60% over 500 battles **and** holds against a held-out opponent;
- latency under the 45 s clock;
- EWP matches realised win rate within its interval.

**Then re-run Phase 6 against this policy.**

### 9. Blocked behind Phase 9

- **Phase 10: matchup evaluation.** On demand only: simulate the one matchup in front of the user
  across its bring/lead combinations. Gated on Phase 6 passing for the Phase 9 policy.
- **Phase 11: team building.** Slot completion and moveset/SP search. Verified by recovering a
  removed member of 10 strong teams in the top 5.
- **Phase 12: interface.** An MCP server wrapping the CLI. Every numeric claim traces to a tool
  call.

---

## Gates, as implemented (`vgc.wp.evaluate`)

- **Per regime.** `in_battle_pass` needs `in_battle_beats_constant` and `in_battle_ece`.
  `closed_sheet_pass` is the same pair on the TPO shard.
- **`beats_constant`:** a battle-clustered 95% interval, and the whole interval must be below
  zero. Preview is gated separately (`preview_beats_constant`).
- **ECE:**
  - **The test:** the model's ECE per turn bucket against the ECE a model miscalibrated by up
    to 1.1× would score on the same rows. Outcomes are redrawn with one draw per battle, with
    Bonferroni correction over buckets at α 0.05.
  - **Power:** a pass needs ≥80% power against logits 1.5× too sharp; below that the verdict is
    *undecided*.
  - **Pooled `ece_spectator` / `ece_player`** keep the 0.03 threshold and feed only `all_pass`.
- **Reported, not gated:** played-out-game calibration (`played_out_calibration`), forfeit vs
  played-out splits, `player_beats_spectator`.
- **Calibration** (`vgc wp calibrate`):
  - fits on the validation split, drawn per group (20% of human groups), on raw logits;
  - a temperature at turn 0 plus a slope per turn, per context and regime;
  - a calibration file without slopes reads as before.

---

## Deferred, pending evidence

| Deferred | Unblocked by |
| --- | --- |
| Learned preview / team-strength WP | A corpus 3+ orders of magnitude larger. The simulator route failed (Phase 6) |
| Behaviour cloning | A higher-rated corpus. The highest battle rating ever seen is 1578 |
| Self-play generation for WP training | A policy that passes Phase 6. The rows don't pay in training |
| Hyperparameter sweeps on the set encoder | Closed. Every config selected epoch 1–2; the limit is coverage |
| A GBT calibration path | Only if a GBT is ever served again (it is set-blind) |
| The belief's own P(faster) as a model input | Training rows where a spread is known (self-play only) |
| Regulation-portable models | A model worth porting. The M-B transfer measurement is worth having before the 2026-12-02 rotation |
| PPO fine-tuning, the precomputed matchup matrix | Cancelled |

---

## Practices (the short list)

1. **Measure what a data source buys before scaling it.** An ablation costs an hour.
2. **Check the corpus population before trusting a metric on it:** rating, concentration, how
   games end.
3. **Assert on the environment you're paying for:** `train.json: device`, not the kernel
   metadata.
4. **Check what a format actually reveals before calling it full information.**
5. **Before concluding "no signal", show the predictor was measured** (its reliability).
6. **Tag every artifact with regulation, snapshot version and featurizer version.**
7. **Log battles in a replayable format.** Freeze eval sets early.
8. **CPU-hours are worth more than GPU-hours here.** Phases 9–10 are CPU-bound simulation. The
   budget is $0–5 total: prepay, no auto-refill, Kaggle before paid. See
   [cloud-compute](cloud-compute.md).

---

## Risks

| Risk | Handling |
| --- | --- |
| Calibration can't be fixed without more human data | The closed-sheet corpus grew 10× from cached replays. Scrape more Bo1/Bo3 before generating self-play |
| Phase 9's policy is stronger but still fails Phase 6 | The deterministic stack is the floor, and the check is already built |
| Reg M-C rotates 2026-12-02 | L0 spine and [regulation-change](regulation-change.md). Phase 7 tools port with the dex |
| Disk (19 GB free) | Retired models are deleted; stale feature datasets are next |

---

## Web app

| Stage | State |
| --- | --- |
| W1 library, validation, bring/lead ranking | ✅ partial. Missing: pokepast.es import, calc panel |
| W2 in-battle WP with gate banner | ✅. Missing: per-turn WP timeline |
| W2b weakness and usage reports in the library | ⏳ |
| W3 live battle (journal, belief pop-ups, Speed read, WP band, open/closed toggle) | ✅. Missing: damage snapped to calc buckets, and the solver's answer beside the model's in 1v1 endgames (step 3) |
| W4 EWP action table, on-demand bring/lead simulation | Behind Phases 9–10 |
| W5 complete-my-team, moveset/SP suggestions | Behind Phase 11 |

Design and API: [docs/web-app.md](web-app.md).

---

## Documents

- [PLAN-v2.md](PLAN-v2.md): archive of every Phase 4–8 measurement and decision, with reasoning.
- [PLAN.md](PLAN.md): original research, full architecture, phases 0–4.
- Findings: [phase0](phase0-findings.md) · [phase4](phase4-findings.md) ·
  [phase6](phase6-findings.md) · [phase8](phase8-findings.md).
- [regulation-change](regulation-change.md) · [cloud-compute](cloud-compute.md) ·
  [web-app](web-app.md).
- Sources: [VGC-Bench](https://arxiv.org/html/2506.10326.pdf) ·
  [philmantatsky port](https://github.com/philmantatsky/VGC-Pokemon-Showdown-AI) ·
  [Champions data](https://github.com/vbbjandrade/pokemon-champions-data) ·
  [poke-env](https://github.com/hsahovic/poke-env) · [damage-calc](https://github.com/smogon/damage-calc).
