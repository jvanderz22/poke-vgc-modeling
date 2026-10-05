# VGC Reg M-C Model & Advisor — Plan v5

_Written 2026-10-04._ **Supersedes [PLAN-v4.md](PLAN-v4.md)**, which is kept as the archive of steps
1–5 as they ran (2026-10-01 to 2026-10-04): the doubles endgame finished, the Phase 9 policy, Phase 6
re-run against it, and the loose ends. Code cites "PLAN-v4 step N", so v4 keeps its step numbers
and stays where it is. [PLAN-v3.md](PLAN-v3.md), [PLAN-v2.md](PLAN-v2.md) and [PLAN.md](PLAN.md)
are the older archives.

v5 restates only what is needed to decide what to do next. Steps restart at 1.

---

## Where things stand

| Capability | State |
| --- | --- |
| Engine, calc, regulation config, battle data pipeline | ✅ Pinned Showdown, `@smogon/calc`, frozen splits, snapshots v4, eval manifests and an eval-set cache |
| Battle state (L1b) | ✅ One state object, two adapters: Showdown logs (`Observer`) and a person's taps (`battle.entry`) |
| Deterministic team tools | ✅ `vgc team weakness` (breakpoints in Stat Points), `vgc meta usage` |
| Belief over hidden sets | ✅ Speed (reads the item), switch-in order, damage, bulk, SP budget, set prior. Damage and bulk are sound in both regimes (closed sheets: 0.12% and 0.21% silently wrong, by the union over what the set belief allows) but run offline only; the live page runs the Speed channel |
| In-battle win probability | ✅ Pinned per regime: `wp-v1f-idp5` open, `wp-v1f-ens5` closed. Both pass their regime's powered calibration test. Neither follows what decides an endgame |
| Endgame engine, 1v1 | ✅ Depth 3 on real games. Leads the page in both regimes: open Brier 0.095 against 0.206, closed log loss 0.345 against 0.649 |
| Endgame engine, 2v1 / 1v2 / 2v2 | ✅ Open sheets: leads the page, log loss 0.384 against the model's 0.513 (674 games), median 2.8 s through the page's own path. 🟡 Closed sheets: the model leads |
| Battle policy (Phase 9) | ✅ EWP over pruned joint choices, opponents weighted as people play (`vgc.policy.ewp`, the people reading). Beats the heuristic 0.678 and a held-out opponent 0.692, median 0.74 s a decision on one core; picks a human's exact choice 11.5% of the time. Not on the page |
| Pre-battle (preview) win probability | ❌ Not learnable from this corpus. Preview advice is "what to bring", not "you are favoured" |
| Simulator as a measure of team strength | ❌ Twice: the heuristic's self-play (AUC 0.512) and the Phase 9 policy's (0.514 [0.488, 0.541]) do not predict human results. With the humans' own brings: running (step 1) |
| Web app | 🟡 Library, brings ranking, and the live Battle page in open, closed and Watching modes, with the engine leading in endgames where it passed. Deployed at [vgc-live-battle-calculator.fly.dev](https://vgc-live-battle-calculator.fly.dev) behind a password. No move advice above two a side |

What got here, in one line each (detail in [PLAN-v4](PLAN-v4.md) and the findings docs):

| v4 step | Outcome |
| --- | --- |
| 1. Finish the doubles endgame | The fitted horizon and temperature take held-out log loss 0.421 → 0.384. Leads the page on open sheets; closed sheets stay with the model |
| 2. Phase 9, policy strength | [PLAN-policy](PLAN-policy.md) stages 0–5. The people reading passes; the Nash reading does not (0.584 against the heuristic) |
| 3. Phase 6 against the Phase 9 policy | Fails: AUC 0.514 on 1,207 series, a bounded null against the 0.55 the run was sized for. Re-run with the humans' brings, carried into step 1 below |
| 4. Next regulation | Not started; no deadline |
| 5. Loose ends | The seed ensemble pinned for closed sheets; legal benchmark items (values unchanged); damage and bulk sound on closed sheets. The doubles bulk spread is carried into step 4 below |

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
    kind and per regime, and the model stays on screen underneath.
12. **A live answer lands within about 5 seconds, planning included.**

---

## Architecture

```
L4  Team building      weakness report · slot completion · moveset & SP search
L3  Team evaluation    on-demand matchup evaluation (the precomputed matrix is cancelled)
WP  Win probability    in-battle WP per regime · belief over hidden sets · endgame engine (≤2 a side) · EWP(action)
L2  Battle policy      heuristic · EWP policy (one turn over pruned joint choices, the people reading)
L1b Battle state       vgc.battle.state · Observer and entry adapters · vgc.battle.rules · vgc.policy.view
L1  Engine             pinned Showdown (Champions) · @smogon/calc 0.12.0 · poke-env 0.16.1
L0  Regulation config  legal pool · clauses · mechanics flags · SP rules · format id
```

**Versioned interfaces:** `observation()` (golden-tested); `snapshots.VERSION` 4; `Featurizer.VERSION`
3. Every dataset and model card records the featurizer version, and scoring refuses a mismatch. The
solver's cache key hashes the version the solver declares (`const VERSION` in
`endgame-solver.js`), not its source: bump it with any change that could move an answer, or the
cache keeps the old answers (`check_cache` re-solves a sample to catch a forgotten bump). A bump
invalidates the benchmark's depth-4 truth, which must then be re-solved (hours, on Kaggle). New
solver behaviour goes behind an opt-in `setup` or `search` key, so cached answers stay valid.

---

## Served models (`models/served.json`)

| Role | Model | Status |
| --- | --- | --- |
| `bring` | `wp-v1f-idp5` | Top-4 overlap 0.702 vs usage 0.672 |
| `in_battle_open` | `wp-v1f-idp5` | Passes `in_battle_ece` (4,127 battles, power 1.0); t7+ passes by 0.0001 |
| `in_battle_closed` | `wp-v1f-ens5` | Passes `closed_sheet_pass`; closed log loss 0.5557 against the previous pin's 0.5633. Five seeds of `wp-v1f-idp5` read as one; 0.8 s a live answer |

The endgame engine is not a registered model. Where it leads is decided per state kind and regime
in `vgc.web.solving` (`doubles_reason`). Its doubles horizon (`MELEE_BLEND_BOOSTS` in the solver)
and the answer's temperature (`doubles.TEMPER`) are fitted on training games by
`scripts/analysis/race_calibration.py`; a change of answers takes a new `race_doubles` name.

---

## Next, in order

### 1. Close out Phase 6: the brings run, and the ceiling

Two runs have failed, and both are bounded nulls. Before a third run or a stronger policy, this
step settles whether the check can be passed on this corpus at all.

- **The brings run.** `step3b-0` to `step3b-3` on Kaggle, started 2026-10-04 about 16:00 (about 9
  hours each): 1,500 human games, one a series, each played 8 times with the four each player brought
  and the two they led with (`scripts/analysis/step3_brings.py`). Collect with
  `kaggle_selfplay.sh step3b-$i --collect`. The merge replays against the packed code, commit
  `9049d90`, so run it from a worktree at that commit or with the changed `src/vgc` files checked out
  from it. Then `step3_brings.py score`, written up as [phase6-findings](phase6-findings.md) §11.
- ~~**The ceiling.**~~ **Done 2026-10-04** ([phase6-findings](phase6-findings.md) §9,
  `scripts/analysis/phase6_ceiling.py`). On held-out games the preview head **passes** Phase 6's
  check: AUC 0.562 [0.537, 0.584], recalibrated −0.0066 [−0.0115, −0.0013]. It beats heuristic
  self-play on the same 1,794 games by +0.036 [+0.001, +0.071]. Against the policy, the 767 shared
  held-out games cannot separate them (+0.020 [−0.028, +0.070]). So the check can be passed here,
  and the third branch below is closed. The ceiling is low, though: Bo3 game 2 goes to game 1's
  winner 57.7% of the time, which puts an oracle for team plus player near AUC 0.72. The sheets
  alone reach 0.56.
- **How to read it:**
  - **The brings run passes.** The policy's self-play measures team strength once it is given what
    people bring. Phase 10 (on-demand matchup evaluation) is unblocked, with the brings as an input,
    and the brings model ranks them.
  - **It fails, and the preview head clears what the policy did not.** The policy is the gap. No
    more Kaggle self-play for team strength until a policy differs in kind, not only in depth.
  - ~~**It fails, and the preview head is no better.**~~ Closed by the ceiling: the preview head
    passes on this corpus.
- **Cost:** the Kaggle sessions already running.

### 2. Why self-play misses: the Phase 6 diagnostics

The verdict so far says self-play does not order human results. It does not say why. These four
checks take that apart. All but the last run on battles already played: 50,580 heuristic, 12,000
policy, and 12,000 with the brings. Each one names the remedy in step 3 that it would point to.

- **Team level instead of pairing level.** Phase 6 scores each pairing, and almost no pairing
  recurs across series (44 of 3,372). Fit a Bradley–Terry strength per team twice: once from
  self-play, once from human games (series-weighted, the human fit cross-fitted by group). Then
  correlate the two. A team plays many series, so this pools what the pairing test spreads thin.
  A correlation that clears its interval means the signal exists and the pairing test could not
  see it (remedy 3b). None means the gap is real at every grain.
- **Where the two disagree.** The residual is the human result minus the self-play prediction,
  summed per team and per archetype: Trick Room, weather, setup, Fake Out pressure,
  redirection, and Megas. These are read from the sheets by rule. A residual concentrated in a few
  archetypes points at what the policy cannot play: a one-turn search undervalues setup and
  Trick Room turns. That is a policy bias, and remedy 3a can target it. A residual spread evenly
  points at noise or at the players.
- **The players, held fixed.** Human results are partly the players. On rated games, compare the
  rating difference alone with the rating difference plus the self-play WP (cross-fitted logistic,
  cluster bootstrap). Rated games were the one subset just above 0.5 (0.536). If the self-play WP
  adds to the rating, the team signal is there underneath the players.
- **From where the humans were, not from team preview.** Rollouts of the policy from human
  positions at turns 1, 3, 5 and 7: the human game's state, both sides' sheets, imputed spreads, 8
  rollouts a position. The rollout win rate is scored against the human result by turn, beside the
  WP model on the same positions. This splits "can the policy play a position" from "can it judge a
  matchup from the start". It needs self-play to start from a composed position, which the solver
  already builds (`vgc.policy.view.compose`). It runs on Kaggle, about two nights for 1,500 games ×
  4 turns × 8 rollouts (shorter than whole battles). A pass from some turn on gives the simulator a
  scope where it can be trusted (remedy 3c).
- **Cost:** the first three take a day on the laptop. The rollouts take two Kaggle nights after a
  day of building.
- **(a)–(c) done 2026-10-04** ([phase6-findings](phase6-findings.md) §10,
  `scripts/analysis/phase6_diagnostics.py`):
  - **Team level:** pooling does not help (heuristic 0.507 against 0.512 per pairing), and 83% of
    teams in two or more series had one player.
  - **Archetypes:** self-play undervalues Trick Room and Fake Out against humans (heuristic z 3.1 and
    4.0 after Bonferroni; the policy leans the same way). Both pay off after the turn they are played
    in.
  - **Players:** the rating difference alone reaches AUC 0.598, and self-play adds nothing beside it.

  That points at 3a, aimed at Trick Room and Fake Out. Rollouts (3c) remain.

### 3. Handling it: a simulator signal that is gated on people

Each candidate is built only if a step 2 check points at it, and each is gated against human
results on held-out series. No simulator number reaches the page or Phase 10 any other way
(principle 1).

- **3a. Self-play that plays as people do.** The policy takes the argmax of EWP. People don't, and
  a matchup that turns on precise play is not the matchup people play. Sample actions from the
  people reading at a temperature fitted to how often humans pick each row (`policy_vs_people.py`
  already measures this). Keep the humans' brings. If step 2 found an archetype bias, deepen the
  search where it bites, for example two turns while a setup or Trick Room move is legal. Gate:
  the Phase 6 check unchanged, on a fresh draw of series. Kaggle, about 4 sessions.
  **First look, 2026-10-04** ([phase6-findings](phase6-findings.md) §10,
  `scripts/analysis/archetype_play.py`):
  - **Trick Room is mostly unplayed.** In the policy's Phase 6 runs a Trick Room user reached the
    field for 8% of sides (people 27%), and set it 18% of the time when it did (people 41%, most often
    on turn 1). When it is set, it wins as often as people's.
  - **Fake Out is overused.** It comes on turn 1 from 77% of leads, against people's 43%.

  The brings run tests the team-preview half. The leaf not seeing Trick Room's remaining turns is
  the search half.
- **3b. Self-play as one input, not the answer.** Promoted by the ceiling: the preview head passes
  alone, so the question is whether self-play adds to it. Stack the self-play WP onto the preview head: a
  logistic over the two logits, fitted out of fold by series on held-out games only. The preview
  head trained on the training series, so a stack fitted there would over-trust it. Self-play must
  cover the held-out pairings: the heuristic's already does (1,794 games). The same goes for team strengths: shrink the
  human-fitted Bradley–Terry towards the self-play one, with the weight fitted out of fold. Gate:
  held-out log loss below the preview head alone, whole interval below zero. This is the cheapest
  way to use a weak signal honestly. It can pass when the signal is too weak to stand on its own.
  **Tested 2026-10-04 on the battles already played: self-play adds nothing.** Heuristic +0.0004
  [−0.0008, +0.0015] nats on 1,794 held-out games; policy −0.0000 [−0.0028, +0.0027] on 767
  ([phase6-findings](phase6-findings.md) §9). It stays the test for any later policy.
- **3c. A simulator with a scope.** If rollouts predict human results from some turn on, that turn
  is where self-play may be trusted. Matchup evaluation then becomes "from this position", not
  "from team preview", and the move advice in step 4 gets a deeper check than one turn. The scope
  is a gate like the engine's per state kind (principle 11): by turn and regime, re-measured on
  every new policy.
- **What it unblocks.** Phase 10 takes whichever form passed: matchup evaluation from preview (3a),
  as a shift on the learned preview number (3b), or from a position (3c). Phase 11 needs 3a or 3b,
  because team building compares whole teams. If none passes, the deterministic stack stays the
  floor, and the Deferred row for a stronger reference corpus is the remaining route.

### 4. Move advice on the live page (W4), gated against people

The Phase 9 policy is the one thing that passed in v4 and that a player cannot get elsewhere: what
to choose this turn. It does not wait on steps 1–3, which ask a different question (team strength
over whole games). It runs on the laptop while their battles run on Kaggle. Beating the heuristic in self-play is not evidence that its numbers are right
about human games, though (principle 1), so this step gates that first.

- **The gate against people** (a new `scripts/analysis/ewp_vs_people.py`, on `policy_vs_people.py`'s
  positions). Held-out open-sheet games, positions with more than two a side, from each side's
  view.
  - **Gated:** the EWP of the joint choice the human made predicts the result better than the WP
    before the turn. That means log loss with a cluster bootstrap by series, and the whole interval
    below zero. If an action's value says nothing about how the game went, a table of action values
    is not shown.
  - **Reported:** the gap between the top row's EWP and the chosen row's, against the result. Also
    EWP's calibration on these positions. The pilot bracketed it: under-confident against weaker
    opponents, over-confident against itself.
- **The adapter.** The page's journal (`battle.entry`) feeds the policy's position
  (`vgc.policy.view`). The parity test: the same battle, entered as taps and read from its
  Showdown log, composes the same solver position. [web-app](web-app.md) has said from the start
  that advice waits on this.
- **What the page shows.** The top rows of the people reading with their EWP, the opponent replies
  that weigh most, and the WP model underneath, as in endgames. Open sheets only: the policy was
  gated with sheets open. At two or fewer a side, the doubles engine already answers.
- **Latency on the deployed machine.** 0.74 s median and 3.1 s p99 on one laptop core. The Fly
  machine has three solver processes on shared cores. Time it as `page_path.py --gap` does
  (principle 12).
- **Cost:** a few days on the laptop. The human check takes minutes on 8 workers.
- **The gate, run 2026-10-04: fails, narrowly** ([phase9-findings](phase9-findings.md), "the policy's
  numbers against human games"; 3,024 positions in 369 series).
  - **The gate:** recalibrated, the EWP of the human's choice against the WP model is −0.017
    [−0.039, +0.004].
  - **What passed:** stacked with the WP number, the EWP adds −0.025 [−0.039, −0.011].
  - **What an action table claims:** the gap to the policy's top row adds nothing (+0.0003
    [−0.0000, +0.0007]).

  **No action table.** The policy's value of a position, as the number above two a side, is the
  candidate left. It would be gated per state kind as the doubles engine was.
- **The policy's value as the number above two a side, run 2026-10-04**
  ([phase9-findings](phase9-findings.md), "the policy's value of a position"). The combination is
  fitted on the WP model's validation games and scored on 800 held-out games.
  - **Overall it passes:** −0.015 [−0.027, −0.002].
  - **By state kind:** nothing at 4v4 (−0.002), and about −0.03 once a side has lost a Pokémon.
    Only 4v3 clears alone, one of six intervals.
  - **Latency:** median 1.0 s, p99 2.7 s.

  **Next:** confirm "after the first faint" on the 939 untouched held-out games, with the weights
  frozen. Then the page needs the player to mark their four.
- **The adapter, built 2026-10-04.** `vgc.policy.view.EntryView` reads a page battle as `PlayerView`
  reads a log. `vgc.battle.from_log` turns a log into the taps a careful person would make, and
  `tests/test_policy_entry.py` holds the two to the same solver positions at every turn, from both
  seats, on the fixture games with both sheets. Illusion is left out, as the log adapter's parity
  already records.
  - **Wider sweep** (`scripts/analysis/entry_parity.py`, 400 replays, 5,346 positions): 87.5%
    identical. The rest is what taps cannot carry:
    - Speed evidence from the order of end-of-turn effects: 6.0%;
    - volatiles the solver cannot set up, which the log path declines on and the page has no tap
      for: 6.1%;
    - both paths declining, for different reasons: 0.3%;
    - Revival Blessing: 0.15%.

    One position (0.02%) is left listed.
  - **Three bugs found and fixed on the way:**
    - **The Protect counter.** A move tap names a slot, so the live doubles answer had ignored the
      Protect counter on hand-entered battles. Entry replay now records each move's Pokémon
      (`Battle.named_journal`), and `doubles.facts` reads it.
    - **Seed Sower.** It restarted the Grassy Terrain it was already under: the rules re-set the
      active weather or terrain, which the game refuses.
    - **Ally Switch.** It had no tap, so every read after one crossed the two slots. It is now an
      entry (`swap`), but has no button yet.

### 5. Ready for the next regulation

Carried unchanged from v4 step 4. Reg M-C rotates 2026-12-02. There is no deadline, since being
late costs little, so this is taken when it is wanted.
- **L0 config for the next regulation** as soon as its rules are public
  ([regulation-change](regulation-change.md)).
- **The set encoder's shared vocabulary,** so `wp-v1f`'s successor can warm-start on M-C games.
  GBT already showed the old regulation's games are worth nearly a new regulation's.
- **What carries over without retraining:** the team tools, the belief channels (re-gated per
  regime on the new data), the endgame engine and the policy. The engine and the policy need only
  the pinned simulator to know the new mechanics; the policy's pruning prior is a people model
  and does need retraining. List what else does not carry over.

### 6. Loose ends, taken when they block something

- ~~**The assumed non-Speed spread in doubles.**~~ **Done 2026-10-04**
  ([phase8-findings](phase8-findings.md), "the assumed non-Speed spread in doubles"). The 674 held-out
  games were re-solved with every guessed spread refilled HP-first and then defences-first. The answer
  moves (mean 0.04–0.07, the favoured side flips in 6–8% of games), but each refill alone is worse
  than the imputer's (+0.013, +0.018, intervals across zero). The mean of the three is level
  (−0.004 [−0.019, +0.010]). The number keeps the imputer's spread; the page shows the other two
  beside it, with their range on the bar.
- **Time the doubles answer on the deployed machine.** Carried from v4; taken by hand. Step 4
  measures the same machine anyway.

### 7–8. Blocked behind steps 1–3

- **Phase 10: matchup evaluation.** On demand only: the one matchup in front of the user across
  its bring/lead combinations.
- **Phase 11: team building.** Slot completion and moveset/SP search, verified by recovering a
  removed member of 10 strong teams in the top 5.

Phase 12 (an MCP server, every numeric claim traced to a tool call). **Built 2026-10-04** over the
web API rather than the CLI, so an agent sees exactly what the page sees and can test the deployed
server: `python -m vgc.mcp`, 20 tools, registered in `.mcp.json` as `vgc` (deployed) and
`vgc-local` ([web-app](web-app.md), "an agent over the same API"). `solve` returns its timeline,
which times the doubles answer on the deployed machine (the open item in step 6).

---

## Parallel track: the web app

Independent of the steps above. Design and API: [web-app](web-app.md).

| Stage | Missing |
| --- | --- |
| W1 library, validation, bring/lead ranking | pokepast.es import, calc panel |
| W2 in-battle WP with gate banner | per-turn WP timeline |
| W2b weakness and usage reports in the library | all of it |
| W3 live battle | damage snapped to calc buckets; closed-sheet doubles (the model leads there) |
| W4 EWP action table | step 4. On-demand bring/lead simulation waits on steps 1–3 |
| W5 complete-my-team, moveset/SP suggestions | behind Phase 11 |
| Video mode | [PLAN-video](PLAN-video.md): WP following a cartridge video of an open-sheet battle. Nothing built |
| Cloud | Deployed at [vgc-live-battle-calculator.fly.dev](https://vgc-live-battle-calculator.fly.dev) ([deploy/README](../deploy/README.md)): one `shared-cpu-4x` 2 GB machine that stops when idle, three solver processes, HTTP Basic password, a 1 GB volume. `./deploy_fly.sh` redeploys from the working tree. Missing: the doubles answer timed on shared cores |

---

## Gates, as implemented

- **WP models** (`vgc.wp.evaluate`):
  - **Per regime.** `in_battle_pass` needs `in_battle_beats_constant` and `in_battle_ece`.
    `closed_sheet_pass` is the same pair on the TPO shard.
  - **`beats_constant`:** a battle-clustered 95% interval, wholly below zero. Preview is gated
    separately.
  - **ECE:** the model's ECE per turn bucket against what a model miscalibrated by up to 1.1×
    would score on the same rows; one outcome draw per battle, Bonferroni over buckets at α 0.05.
    A pass needs ≥80% power against logits 1.5× too sharp, else *undecided*.
  - **Calibration** (`vgc wp calibrate`): fit on the validation split (20% of groups), a
    temperature at turn 0 plus a slope per turn, per context and regime.
- **The engine against the model** (`scripts/analysis/solver_vs_humans.py`): log loss and Brier,
  cluster bootstrap by group, by state kind and regime, with 2v1 and 1v2 judged pooled
  (`2v1|1v2`). `--temper` scores the answer as the page shows it. `page_path.py` checks the page's
  own path (time and score).
- **The policy** (`scripts/analysis/policy_gate.py`): at least 60% against the heuristic over 500
  battles and against a held-out opponent, Wilson interval; replays identical; latency on one core.
  Step 4 adds the gate against people; step 3 adds the gates for a simulator signal.
- **The simulator** (`vgc.sim.validity`): AUC and recalibrated log loss against the constant, on
  games that ended normally, cluster bootstrap by series. Standing: re-run against every new
  policy before anything is built on its numbers.

---

## Deferred, pending evidence

| Deferred | Unblocked by |
| --- | --- |
| Phase 6 against a stronger reference (results between known team lists at a higher rating, e.g. tournaments, if they exist for Champions) | Steps 1–3 showing this corpus cannot judge, or nothing in step 3 passing |
| The Nash reading of the policy | A gate it passes (0.584 against the heuristic) |
| Learned preview / team-strength WP | A corpus 3+ orders of magnitude larger |
| Behaviour cloning | A higher-rated corpus. The highest rating seen is 1578 |
| Self-play generation for WP training | A policy that passes Phase 6 (step 3a) |
| The belief's own P(faster) as a model input | Training rows where a spread is known (self-play only) |
| Damage and bulk channels on the live page | A sweep cheap enough to run between taps |
| Double oracle over the full doubles matrix | A pruning gap too large to accept |
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
   cost: it stops when idle and sits under a hard spend cap.
9. **A split by which side is p1 is not a split.** A 2v1 and a 1v2 are one state; judge them
   pooled.
10. **Fit a calibration where the answer is made.**
11. **Before blaming the predictor for a null, score the best predictor available on the same
    test.** Phase 6 ran twice before its ceiling was asked about (step 1).
12. **A Kaggle merge replays against the code that was packed.** Note the commit when packing, and
    merge from it.

---

## Risks

| Risk | Handling |
| --- | --- |
| Phase 6 cannot be passed on this corpus by anything | Step 1 measures the ceiling before more self-play is spent; step 3b can pass on a signal too weak to stand alone |
| The diagnostics find no single cause | Step 3 is built only where a step 2 check points; otherwise the floor holds and nothing more is spent on self-play |
| Move advice that misleads | Gated against human results first (step 4); open sheets only; the WP model stays on screen |
| The policy misses 5 s on shared cores | Fewer rows (K) above two a side, the quick answer kept, or a `performance-1x` machine |
| Reg M-C rotates 2026-12-02 | Step 5, when it is wanted; being late costs little |
| The closed-sheet belief does not concentrate enough | Happened (v4 step 1). The model keeps leading closed doubles |
| The cloud machine never stops, or its solves run long | Auto-stop with no minimum, one machine, a password, and a hard spend cap |
| Disk (18 GB free) | Retired models and unreferenced eval sets (`vgc wp prune-eval-cache`) go first |

---

## Documents

- [PLAN-v4](PLAN-v4.md): archive of steps 1–5 (2026-10-01 to 2026-10-04). [PLAN-v3](PLAN-v3.md):
  steps 1–9 before that. [PLAN-v2](PLAN-v2.md): Phases 4–8. [PLAN](PLAN.md): original research,
  full architecture, phases 0–4.
- [PLAN-endgame-doubles](PLAN-endgame-doubles.md): the solver for 2v1, 1v2 and 2v2.
- [PLAN-policy](PLAN-policy.md): Phase 9, the policy over whole battles.
- [PLAN-video](PLAN-video.md): video mode.
- [deploy/README](../deploy/README.md): the Fly.io deployment, its sizing and cost.
- Findings: [phase0](phase0-findings.md) · [phase4](phase4-findings.md) · [phase6](phase6-findings.md) ·
  [phase8](phase8-findings.md) · [phase9](phase9-findings.md).
- [regulation-change](regulation-change.md) · [cloud-compute](cloud-compute.md) ·
  [web-app](web-app.md).
- Sources: [VGC-Bench](https://arxiv.org/html/2506.10326.pdf) ·
  [philmantatsky port](https://github.com/philmantatsky/VGC-Pokemon-Showdown-AI) ·
  [Champions data](https://github.com/vbbjandrade/pokemon-champions-data) ·
  [poke-env](https://github.com/hsahovic/poke-env) · [damage-calc](https://github.com/smogon/damage-calc).
