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
| Endgame solver (1v1) | ✅ `vgc wp solve`: minimax over the pinned engine, with chance enumerated and crits included. ✅ Checked against 174 held-out human 1v1s: it predicts the winner better than the model (Brier 0.090 vs 0.197; 48 of 50 confident calls right). ✅ Leads the Battle page in a 1v1, deepening in the background, with the model underneath. ✅ Depth 3 at scale: 192 of 204 held-out 1v1s solved, 144 at depth 3 (step 8) |
| Pre-battle (preview) win probability | ❌ Not learnable from this corpus (finding 1). Preview advice is "what to bring", not "you are favoured" |
| Simulator as a measure of team strength | ❌ Heuristic self-play does not predict human results (AUC 0.512). Blocks matchup and team evaluation until a stronger policy passes the same check |
| Web app | 🟡 Library, brings ranking, and the live Battle page with belief panels and WP, for your games and for watching someone else's open-sheet game |

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
- **The engine in the app, checked on real games** (2026-09-29, steps 3–5 below;
  [phase8](phase8-findings.md)). In a 1v1 the Battle page solves the live position in the
  background. A **Watching** mode takes two open sheets with both spreads hidden, which is also how
  held-out replays are asked. On 174 held-out human 1v1s the engine predicts the winner better than
  the model, so it leads the page there. Along the way:
  - the Speed prior reads the item;
  - Watching mode's number is fixed. It had averaged each position with its mirror.
- **Regulation transfer measured** (M-B → M-C, [regulation-change](regulation-change.md)): train
  on the old regulation's games as well as the new one's.
- **On a closed sheet the number is the position as shown** (step 6). It beat the average over
  drawn sets by 0.015 nats on 1,025 held-out closed-sheet games, and it now leads the page and the
  brings preview. The damage and bulk channels fail soundness with sheets hidden and stay off.
- **Eval sets are named and cached** (step 7). A dataset's eval sets come from an eval manifest
  that names its shards by hash, featurized once per shards and featurizer. `vgc wp valcheck`
  compares recipes on shared validation rows and refuses a model that trained on them.

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

### 3. The solver's answer in the app, for 1v1 endgames: done

All six items are done (2026-09-29; [phase8, "the solver in the app" and "the engine against how
human 1v1s end"](phase8-findings.md)):
- `vgc.wp.endgame` is the adapter. It writes positions through the same `solver.compose` as the
  benchmark, and a closed sheet's sets are reweighted by the turn order under each set's item.
- `endgame.check` reproduces all 32 solved variants from their journals: same positions, same
  weights, values within `leaf_mass`.
- `vgc.web.solving` deepens from 1 to 4 turns in the background; `/api/battles/<id>/solve`.
- The page labels both numbers and flags a gap of more than 20 points.
- The check fixed a choice lock missing from F1-D/F5-D's truth (values unchanged) and F2-C's
  journal.
- It also found three items illegal in Reg M-C in the benchmark (F5's Choice Band, F11's Assault
  Vest, a filler's Choice Specs). Not fixed: replacing them is a redesign of those families.
- Item 6, the check against human 1v1s, came out for the engine. The page now leads with it
  (decided 2026-09-29).

In decided 1v1s the model's WP barely depends on the position. The solver gives the engine's
answer under best play, which is a different claim from what a human game will do. Principle 1
said to show both, labelled, until the solver had been checked against human outcomes. It has
been, and it is the better predictor, so it leads in a 1v1 and the model stays underneath as
the second opinion.

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
   assumes best play, and the corpus is ~1100-rated, so this can come out either way. It runs on
   step 4's observer adapter.

   **Done (2026-09-29; [phase8, "the engine against how human 1v1s end"](phase8-findings.md)).** On
   174 games at depth 2, the engine beats the model: Brier 0.090 against 0.197, log loss 0.33
   against 0.58, and the called side won 48 of 50 confident calls. It is, if anything,
   underconfident. The run also found that Watching mode averaged each position with its mirror,
   which is now fixed.

   **What it changed:** the engine's number leads the page in a 1v1, and the model's sits under it.
   A shallow answer is no longer faded, because the answers resting about half on HP share also
   beat the model. The page still says how much of each answer rests on HP share.

### 4. Observer mode: a battle watched with both sheets open: done

A third way to run the Battle page, beside open and closed: watching someone else's open-sheet
game (on stream, at an event, a replay). Both sheets are known and **neither side's Stat Points
are**. It is also the view every public replay gives, so it is what step 3.6 is measured through.

Items 1–4 are done (2026-09-29; [phase8, "observer mode"](phase8-findings.md)): the Watching mode,
`belief.speed.joint`, `solver.partition_joint`, and the page. The player view goes through the same
joint path and still reproduces all 32 benchmark variants. Item 5 is done too: step 3.6 ran
through it, 174 replays stopped at their first 1v1.

It is a *perspective* on open sheets, not a third information regime. The model and gate are the
open-sheet ones (`wp-v1f-idp5`, `in_battle_pass`), and `in_battle_ece` is already scored on
spectator rows. So the registry and pins do not change. An observer battle is the setting the served
open-sheet model was gated in, arguably more exactly than the player view.

1. **The battle.** A spectator `BattleState` from two open sheets: HP as percentages on both
   sides, nothing exact. The WP is P(player 1 wins) (spectator records are oriented to p1), and the
   page says "P1"/"P2" instead of "you"/"them".
2. **Speed with both spreads hidden.** The turn-order channel narrows one unknown against a known
   Speed and defers a pair of two unknowns. That is every pair here. For the two Pokémon of a
   1v1, keep the joint weight over both Speed investments:
   - the two speed priors multiplied;
   - an ordering between the two is an indicator on the pair;
   - an ordering with any other Pokémon is a likelihood, marginalised over that Pokémon's prior.

   The player view becomes the special case where one side's Speed is a point mass, and it has to
   reproduce the benchmark check exactly as now.
3. **The solver averages over both spreads.** The Speed classes are the pair's move order
   (faster / tied / slower), so it is still up to three solves a position, not nine. Each class is
   represented by its heaviest cell. Both sides' other stats are `solver.spread`'s assumption, and
   the display says so for both.
4. **The page.** A third mode button. The form takes two sheets instead of your team and theirs.
   The Speed read and the engine row work the same, over two unknowns.
5. **Then step 3.6** on held-out open-sheet replays through this adapter, fed by `Observer`
   instead of taps.

### 5. Condition the Speed prior on the item: done

A Choice Scarf set with under 4 Speed SP is not real, but `speed_prior` gave it 27% (F1-B), and the
solver inherited it. Scored on 135,245 observed turn orders, the fix that works is moving a
Speed-raising item's 0-SP extreme to 32. It beats today's prior on every cut, most on Scarf
pairs (−0.015 nats a pair). Reading the multiplier into the benchmark classes was *worse*. F1-B
is now 0.097. [phase8, "the Speed prior reads the item"](phase8-findings.md).

### 6. Remaining belief-to-app work: done

Done (2026-09-29; [phase8, "which number leads on a closed sheet" and "damage and bulk with
sheets hidden"](phase8-findings.md)):

1. ✅ **`wp` vs `wp_open` on the real closed-sheet shard.** On 1,025 held-out closed-sheet games
   the position as shown beats the average over drawn sets by 0.015 nats, at equal confidence. The
   page, the per-turn curve and the benchmark now lead with it. The draws remain as the band.
2. ✅ **The brings preview no longer guesses one set.** Its closed-sheet opponent is hidden again
   after the simulator builds it, the same finding applied at preview.
3. ✅ **Re-gate damage and bulk under TPO: both fail.** Damage is 6.6% silently wrong with sheets
   hidden (0% open) and bulk 2.2% (0.18%), because an unrevealed damage modifier is read as
   investment. Neither runs live, so nothing changes. Making them sound would mean bounding over
   the items and abilities the set belief still allows, and that is deferred.

### 7. Housekeeping that pays on every retrain: done

- **Eval manifests.** `vgc wp featurize` builds its eval sets from an eval manifest
  (`manifests/<name>-eval.json`), not from globs. The manifest names each held-out shard by
  sha256, and every record's split is recomputed as it is written. `--eval-manifest <name>` scores
  a new dataset on exactly the rows an older one was scored on. `vgc data check` re-derives an eval
  manifest's splits record by record.
- **An eval-set cache** (`data/features/<reg>/_eval/`), keyed on the shards' sha256, the featurizer
  version and the vocabulary. Datasets hard-link into it.
  - A retrain that keeps its eval shards no longer featurizes them again. That was ~1M rows on
    M-C, more than training.
  - Checked on real data: the cached `human_closed_team` is array-for-array `wp-v1e`'s, and
    rebuilding `wp-mb1` from its manifest through the new path reproduced every array.
  - `--fresh` rebuilds regardless. `vgc wp prune-eval-cache` removes sets no dataset links to.
- **`vgc wp valcheck` compares recipes.** Repeat `--version`, and each is compared with the first
  on the same human validation rows: log loss after one temperature, with a 95% interval
  resampled by battle.
  - It refuses a model that trained on any of those rows. `wp-v1e`'s models, scored on `wp-v1f`'s
    validation, read 0.05 nats better than `wp-v1f-idp5`: 1,900 of the 4,326 battles were in their
    training rows.
  - `--dataset` defaults to the one the model was trained on, for `eval` and `calibrate` too.
    `featurize --name`, `train --dataset` and `card --dataset` are now required. The default was
    `wp-v1`, the oldest dataset.
- **Stale caches:**
  - the decided-endgames list is regenerated whenever the served model or its calibration
    changes. `tests/test_endgames.py` catches a stale one;
  - the `wp-v1`/`wp-v1c` feature datasets (586 MB) are deleted (2026-09-30). No model card named
    them, and their training manifests' shards had been re-extracted, so they could not be rebuilt.
    `scripts/analysis/preview_signal.py` and `selfplay_value.py` read `wp-v1`, and say so.

### 8. Solver speed: done

Now that the engine leads the page in a 1v1, how deep it gets is how good that number is. It is
also Phase 9's search. What step 3.6 measured:
- **Depth 2 is what's affordable at scale.** Real 1v1s average about 35 s of wall time a position
  at depth 2, with 6 workers. Depth 3 was out of reach for the check.
- **The hard positions drop out.** 30 of 205 games had a position that did not finish in 180 s,
  so the check scores the engine on the 1v1s it can solve. F6 alone took 2 h 50 min.
- **One game crashed the simulator** (`gen9championsvgc2026regmcbo3-2683090653`). It was a solver
  bug, fixed in the first pass below.

The levers:
- **reuse results across depths.** The solver's cache is keyed on position *and* depth
  (`endgame-solver.js`, `key(battle) + '#' + depth`), so a depth-2 search does nothing for depth 3.
  Iterative deepening wants this anyway;
- **prune dominated moves.** This carries the most risk to correctness;
- **split one position across workers.** This makes the live page answer sooner. It does not
  speed up the check, which already keeps 6 workers busy on different games.

Each extra turn multiplies the work by both sides' moves times the chance outcomes, likely tens of
times. Reuse and pruning may buy a few times that. So depth 3 over all 205 games may stay out of
reach, and the goal may become depth 3 on the 1v1s with few options, or faster depth 2 for the page.

**How to go at it: a first pass, then a decision.**
1. ✅ **First pass** (2026-09-30; [phase8, "solver speed, first pass"](phase8-findings.md)):
   - **The crash is fixed.** A choice lock's last move did not survive the battle copy, and the
     root offered a locked Pokémon all four moves. The benchmark's five locked positions give the
     same answers, with up to 60× less work. The game is a test fixture now.
   - **The time is the replay count** (~2.5 ms each: 39% deserializing, 58% simulating). 95% of
     finished replays duplicate an outcome already found. 68% of replays come from 10-way
     percentage rolls on move secondaries, which branch even when the chance is 100%.
   - **Prototyped, not built: the secondaries as one `randomChance`.** It is the same
     distribution. Values are identical on 22 positions, 21 of the 31 timed-out positions finish in
     90 s (6 before), and the sample's depth 2 is 6.6× faster. **Depth 3 finished on all 18 sample
     positions** for about what depth 2 cost before (1–10× depth 2, median 3×, not tens of times).
     The typical leaf mass falls from 0.333 to 0.037.
   - **Also found:** 2–5-hit moves are approximated wrongly (0.40/0.30/0.10/0.20 for
     0.35/0.35/0.15/0.15). Grouping equal items in `sample` is exact and branches 4 ways, not 10.
2. ✅ **Decided: depth 3** (2026-09-30), and built ([phase8, "solver speed, built"](phase8-findings.md)).
   The two exact levers are in. The new rejected-choice check found three more bugs: Helping Hand
   with no target, locked moves given a target, and Revival Blessing. The rerun solved 192 of 204
   games, 144 at depth 3. The engine still beats the model (Brier 0.095 against 0.206). Depth 3
   against depth 2 on the same games is better on both scores, but not distinguishable. The
   benchmark's depth-4 truth, re-solved on Kaggle, is unchanged on all 39 variants.

   What was recommended at the time: chase depth 3. It is now affordable, and it
   settles most of what depth 2 leaves to HP share. The levers, re-ranked by the profile:
   1. secondaries and self-drops as `randomChance` (measured above);
   2. an exact multi-hit `sample` (a correctness fix as well);
   3. a cheaper battle copy than deserializing per replay (39% of the time);
   4. the plan's original three (reuse across depths, pruning, splitting across workers) only if
      depth 3 still leaves too many positions out. The profile gives each little.

   Any edit to the solver invalidates its whole cache (the key hashes the source), and
   `test_the_same_positions_give_the_stored_answers` skips until the benchmark is re-solved at
   depth 4 (hours; F6 alone was 2 h 50 min). So land the levers together and re-solve once. That
   skip applies now, after the crash fix.
3. **Every lever must leave the answers unchanged.** Each is checked against the benchmark's
   stored truth (`vgc wp solve`) and a sample of step 3.6's solved positions at the same depth.
   Only the time may change. The multi-hit fix is the exception: it is meant to change answers
   with a 2–5-hit move in them, and a change is expected only there. The cache key has no `reach`,
   so a lever that changes the order of visits can move an answer within its leaf mass.
4. **Measure with the step 3.6 run itself:** the same 205 games
   (`scripts/analysis/solver_vs_humans.py`), now at `--depths 2,3`, with fewer timeouts.

Estimate: the first pass took the half day. Levers 1–2 and the benchmark re-solve are about a day,
most of it compute. Lever 3 is ½–1 day if it is wanted.

### 9. Phase 9: policy strength (EWP and search)

**First milestone: the endgame solver beyond 1v1** ([PLAN-endgame-doubles](PLAN-endgame-doubles.md),
proposed 2026-09-30). With at most two Pokémon left a side there is no bench, so 2v1, 1v2 and 2v2 are
move-only states. Solving them under realistic play on both sides (pruned choices, a KO extension)
reruns the step 3.6 check where more games are decided, and becomes this search's oracle once a line
reaches two or fewer a side, on open and closed sheets. About 5½ days plus overnight Kaggle runs.

It starts by checking the 1v1 on closed sheets. The page leads with the engine there, but step
3.6's check ran on open-sheet games only (principle 4).

**Progress, 2026-09-30.** The closed-sheet 1v1 check passed: log loss 0.345 against the model's
0.649. The solver plays 2v1, 1v2 and 2v2 with pruning, sampled chance and a damage race for the 1v1s
it reaches, and `vgc.wp.doubles` builds those positions from open-sheet games. The human check on
674 open-sheet games is running on Kaggle; closed sheets in the adapter are next.

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

### 10. Blocked behind Phase 9

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
| Damage and bulk channels on closed sheets | Bounding an unrevealed item or ability over what the set belief allows. Both fail soundness with sheets hidden (6.6%, 2.2%) and neither runs live |
| Regulation-portable models | A model worth porting. The M-B → M-C measurement is done (2026-09-29, `docs/regulation-change.md`): for GBT, the old regulation's games are worth nearly a whole new regulation's, and warm-starting with them beats the new regulation's first 3–10% alone. The set encoder (shared vocabulary) is the remaining piece before the 2026-12-02 rotation |
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
| Reg M-C rotates 2026-12-02 | L0 spine and [regulation-change](regulation-change.md). Phase 7 tools port with the dex. Warm-start on the old regulation's games (measured on M-B → M-C); the set encoder's shared vocabulary is still to do |
| Disk (19 GB free) | Retired models are deleted; stale feature datasets are next |

---

## Web app

| Stage | State |
| --- | --- |
| W1 library, validation, bring/lead ranking | ✅ partial. Missing: pokepast.es import, calc panel |
| W2 in-battle WP with gate banner | ✅. Missing: per-turn WP timeline |
| W2b weakness and usage reports in the library | ⏳ |
| W3 live battle (journal, belief pop-ups, Speed read, WP band, open/closed/watching modes, the engine's answer leading in 1v1s) | ✅. Missing: damage snapped to calc buckets |
| W4 EWP action table, on-demand bring/lead simulation | Behind Phases 9–10 |
| W5 complete-my-team, moveset/SP suggestions | Behind Phase 11 |

Design and API: [docs/web-app.md](web-app.md).

---

## Documents

- [PLAN-v2.md](PLAN-v2.md): archive of every Phase 4–8 measurement and decision, with reasoning.
- [PLAN.md](PLAN.md): original research, full architecture, phases 0–4.
- Findings: [phase0](phase0-findings.md) · [phase4](phase4-findings.md) ·
  [phase6](phase6-findings.md) · [phase8](phase8-findings.md).
- [PLAN-endgame-doubles](PLAN-endgame-doubles.md): the solver for 2v1, 1v2 and 2v2 (Phase 9's first milestone).
- [regulation-change](regulation-change.md) · [cloud-compute](cloud-compute.md) ·
  [web-app](web-app.md).
- Sources: [VGC-Bench](https://arxiv.org/html/2506.10326.pdf) ·
  [philmantatsky port](https://github.com/philmantatsky/VGC-Pokemon-Showdown-AI) ·
  [Champions data](https://github.com/vbbjandrade/pokemon-champions-data) ·
  [poke-env](https://github.com/hsahovic/poke-env) · [damage-calc](https://github.com/smogon/damage-calc).
