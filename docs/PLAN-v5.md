# VGC Reg M-C Model & Advisor — Plan v5

_Written 2026-10-04._ **Supersedes [PLAN-v4.md](PLAN-v4.md)**, which is kept as the archive of steps
1–5 as they ran (2026-10-01 to 2026-10-04): the doubles endgame finished, the Phase 9 policy, Phase 6
re-run against it, and the loose ends. Code cites "PLAN-v4 step N", so v4 keeps its step numbers
and stays where it is. [PLAN-v3.md](PLAN-v3.md), [PLAN-v2.md](PLAN-v2.md) and [PLAN.md](PLAN.md)
are the older archives.

v5 restates only what is needed to decide what to do next. Steps restart at 1.

**Revised 2026-10-04 evening, after a review of the open steps** ("Next, in order" opens with what
changed). Step numbers are kept, because code cites them; the new work is step 0 and new items
inside steps 3 and 4.

---

## Where things stand

| Capability | State |
| --- | --- |
| Engine, calc, regulation config, battle data pipeline | ✅ Pinned Showdown, `@smogon/calc`, frozen splits, snapshots v4, eval manifests and an eval-set cache |
| Battle state (L1b) | ✅ One state object, two adapters: Showdown logs (`Observer`) and a person's taps (`battle.entry`) |
| Deterministic team tools | ✅ `vgc team weakness` (breakpoints in Stat Points), `vgc meta usage` |
| Belief over hidden sets | ✅ Speed (reads the item), switch-in order, damage, bulk, SP budget, set prior. Damage and bulk are sound in both regimes (closed sheets: 0.12% and 0.21% silently wrong, by the union over what the set belief allows) but run offline only; the live page runs the Speed channel |
| In-battle win probability | ✅ Pinned per regime: `wp-v1f-idp5` open, `wp-v1f-ens5` closed. 🟡 Both **fail** their calibration gate on `wp-v1g`'s larger held-out sets. `wp-v1g-ens5` (trained on four times the human games) passes every gate and is better by 0.030 nats open and 0.033 closed: built, not pinned (step 0). Neither follows what decides an endgame |
| Endgame engine, 1v1 | ✅ Depth 3 on real games. Leads the page in both regimes: open Brier 0.095 against 0.206, closed log loss 0.345 against 0.649 |
| Endgame engine, 2v1 / 1v2 / 2v2 | ✅ Open sheets: leads the page, log loss 0.384 against the model's 0.513 (674 games), median 2.8 s through the page's own path. 🟡 Closed sheets: the model leads |
| Battle policy (Phase 9) | ✅ EWP over pruned joint choices, opponents weighted as people play (`vgc.policy.ewp`, the people reading). Beats the heuristic 0.678 and a held-out opponent 0.692, median 0.74 s a decision on one core; picks a human's exact choice 11.5% of the time. Not on the page |
| Human corpus | ✅ 103,529 replays: M-B 15,000, M-C 88,729, scraped to 2026-10-05 (step 0). 13 automated accounts' games (17,823) are left out of training. Weekly scrapes with `scripts/scrape_replays.py` |
| Pre-battle (preview) win probability | 🟡 Learnable but too small to show: the preview head passes Phase 6's own check (AUC 0.562, recalibrated −0.0066), worth about 0.007 nats. Preview advice is "what to bring", not "you are favoured" |
| Simulator as a measure of team strength | 🟡 With the bot's brings, twice no (AUC 0.512, 0.514). **With the humans' own brings, it passes** (0.590 [0.562, 0.620], recalibrated −0.011) and adds 0.02 nats to the preview head and to the WP model at turn 1 on held-out games (step 1). Whether that is the simulator or the players' choice of four is step 3d's question |
| Web app | 🟡 Library, brings ranking, and the live Battle page in open, closed and Watching modes, with the engine leading in endgames where it passed. Deployed at [vgc-live-battle-calculator.fly.dev](https://vgc-live-battle-calculator.fly.dev) behind a password. No move advice above two a side. An MCP server over the same API (`python -m vgc.mcp`) lets an agent drive it |

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
   A solver output computed under the imputed spread (the stack in step 4, a learned leaf in
   step 3a) is allowed when it is computed the same way in training and serving. The spread itself
   never reaches the model, and the number is gated like any other.
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

**What the review changed (2026-10-04 evening).** The open steps were rigorous gate by gate but
aimed badly between gates. Most of the effort left went to Phase 6, a check whose ceiling sits near
its pass line and which asks a harder question than Phase 10 needs. Meanwhile the checks that would
put move advice on the page failed for lack of power and on noisy labels, not necessarily for lack
of signal. Five changes:

1. **Fresh data first (step 0).** The corpus is two weeks stale. Games after 2026-09-20 are unseen
   by every model and fit, so they are a free test set for each gate that stalled on power.
2. **Trick Room is a leaf problem before it is a depth problem (step 3a).** The race at the leaf
   already runs Trick Room and Tailwind out by their turns left, but carries weight 0.137 against
   HP share's 1.725 (`BENCH_BLEND`). Setting Trick Room moves no HP, count or stage, so it is worth
   almost nothing at the leaf. That fits the policy setting it 18% of the time against people's 41%.
   A leaf that learns from features comes before two-turn search.
3. **The action-table null has not shown its predictor was measured (step 4).** The gap is built
   from 4 draws and an argmax over near-ties (median best-to-second gap 0.003), so measure its
   test–retest reliability first. Score advantages against the next turn, not only against the
   end of the game.
4. **Phase 10's validity question is within a matchup (step 3d).** A Bo3 series holds the players
   and teams fixed while the brings change. Testing within series removes the player effect that
   dominates the cross-team test (rating alone: AUC 0.598).
5. **The people model is behaviour cloning, reframed (step 3e).** "BC clones ~1100 play" is a
   reason it cannot make a strong policy. It is exactly what an opponent model, pruning and
   people-like self-play need.

**Order:**
- step 0, then collect step 1 (done 2026-10-05: it passes);
- step 4's reliability check and the re-scoring on fresh games (laptop, about a day);
- step 3d, promoted by step 1's pass (Kaggle, beside the laptop work);
- step 3a's leaf (laptop, 2–3 days);
- step 3e (Kaggle GPU).

The full Phase 6 re-run against a temperature-sampled policy, the old 3a, is dropped.

### 0. Fresh data: the corpus since 2026-09-20

**Started 2026-10-04 about 21:50.** Both M-C formats are fetched newest-first back to the cached
range, into a staging directory, `data/replays-new/<format>/`. The runner stops after three
consecutive pages that are already cached, retries transient errors, skips deleted replays and
resumes when re-run. It runs on the laptop, bound by the network, with 0.3 s between replays. It
reuses one HTTPS connection, which halves each fetch (0.16 s to 0.08 s) and spares the server a
handshake per replay. That gives about 150 replays a minute per format, against 60 with a new
connection each. So roughly 3 hours for Bo3 and 5 for Bo1, by the early hours of 2026-10-05.
Kaggle was considered and not used: the laptop is not the limit, and going faster there would
mean several sessions hitting Showdown's volunteer-run replay server at once. The runner is
now `scripts/scrape_replays.py`, for the weekly scrapes to rotation and M-D's daily ones (step 5).
**Done 2026-10-05 12:46:** 70,467 replays, no errors, none deleted.
- **Fresh,** uploaded after the old cache's newest game: 44,146 Bo1 (median rating 1181) and
  22,820 Bo3 (1102).
- **Gap-fill,** from the weeks the models trained on: 2,303 Bo1 and 1,198 Bo3. These are training
  data, not part of the fresh test set.

The edges are 2026-09-20 23:35:48 (Bo1) and 22:49:32 (Bo3).

- **Why staged, not in `data/replays/`.** Several analyses sample from the cache, and the step 3b
  merge replays against a packed list. New files there would change their inputs silently.
- **Rates, sampled the evening of 2026-10-04:** about 4,800 Bo1 a day (median rating 1206) and
  1,800 Bo3 a day. That was a Sunday evening, so read these as an upper range. Even a third of it
  doubles or triples the M-C corpus, and the regulation runs to 2026-12-02.
- **What the new games are for:**
  - **A fresh test set.** No model or fit has seen a game after 2026-09-20. Each re-test is
    declared here before it is scored, scored once on the new games alone, and reported beside the
    old result, so that it is not a second look at the same gate. Candidates:
    - the EWP gate, −0.017 [−0.039, +0.004] on 369 series;
    - `policy_value`;
    - closed-sheet 2v2, 135 games;
    - self-play stacked on the preview head, 767 policy games.
  - **More training rows**, the frozen split's hash rule deciding train or held out as before:
    the leaf (3a), the people model (3e), the WP model, and the closed-sheet set prior (more open
    sheets).
  - **Not a fix for preview WP.** That needs orders of magnitude more data (phase4-findings §1).
- ~~**Before use.**~~ **Done 2026-10-05** (`scripts/analysis/fresh_corpus.py`).
  - **Bo3 is the same population.** Bo1 is not: accounts playing 65–368 games a day had arrived
    after 2026-09-20 and were in about 40% of its new games.
    `scripts/analysis/automated_accounts.py` lists 13 of them, and `extract_human` leaves their
    games out of every split.
  - **The games joined `data/replays/`.** The fresh shard is a filter on upload time against each
    format's edge, recorded in `fresh_corpus.json`, not a separate folder.
- **New models: `wp-v1g`, done 2026-10-06** ([phase8-findings](phase8-findings.md), "wp-v1g").
  - **Built:** the same recipe on 50,613 human training battles instead of 12,682. Five seeds
    trained on a Kaggle CPU session, read as `wp-v1g-ens5`.
  - **On `wp-v1g`'s held-out games** (`scripts/analysis/wp_compare.py`, all four models on the
    same rows), against the served open-sheet model: **−0.030** [−0.033, −0.027] nats on open
    sheets and **−0.033** [−0.040, −0.027] on closed. It is better on games before the edge as well
    as after. `wp-v1g-ens5` passes every gate, and both served models fail their calibration gate
    there.
  - **Pinning it is a separate decision.** It switches off the page's policy-value stack until
    `policy_value.py` is refitted on the new model, it needs the engine's per-kind lead re-measured
    (principle 11), and it needs the endgames index rebuilt.

### 1. Close out Phase 6: the brings run, and the ceiling

Two runs have failed, and both are bounded nulls. Before a third run or a stronger policy, this
step settles whether the check can be passed on this corpus at all.

- ~~**The brings run.**~~ **Done 2026-10-05: it passes** ([phase6-findings](phase6-findings.md) §11,
  `scripts/analysis/step3_brings.py score` and `stack`).
  - **The run:** 1,500 human games, one a series, each played 8 times by the Phase 9 policy with the
    four each player brought and the two they led with. 12,000 battles, no errors. Merged from a
    worktree at `9049d90` after 12 of 12 check battles replayed identically.
  - **Against the constant:** AUC **0.590** [0.562, 0.620], recalibrated **−0.011**
    [−0.019, −0.002]. The first self-play to pass. On the 621 games §8 also played, the same policy
    with the bot's brings scored 0.511.
  - **Beside the learned models, 579 held-out games:**
    - self-play alone: AUC 0.628;
    - the preview head: 0.581;
    - the WP model at turn 1: 0.559.

    Stacked, self-play adds **−0.020** [−0.036, −0.003] nats to the preview head and **−0.021**
    [−0.038, −0.003] to the turn-1 model.
  - **Still open:** neither baseline sees the backs. So the gain is the simulator plus the players'
    choice of their back two, and these tests cannot split them. Step 3d can.
- ~~**The ceiling.**~~ **Done 2026-10-04** ([phase6-findings](phase6-findings.md) §9,
  `scripts/analysis/phase6_ceiling.py`). On held-out games the preview head **passes** Phase 6's
  check: AUC 0.562 [0.537, 0.584], recalibrated −0.0066 [−0.0115, −0.0013]. It beats heuristic
  self-play on the same 1,794 games by +0.036 [+0.001, +0.071]. Against the policy, the 767 shared
  held-out games cannot separate them (+0.020 [−0.028, +0.070]). So the check can be passed here,
  and the third branch below is closed. The ceiling is low, though: Bo3 game 2 goes to game 1's
  winner 57.7% of the time, which puts an oracle for team plus player near AUC 0.72. The sheets
  alone reach 0.56.
- **How to read it:**
  - **The brings run passes. This is the branch taken (2026-10-05).** The policy's self-play
    measures team strength once it is given what people bring. **Review caveat:** brings are chosen
    after team preview and carry the players' skill, so a pass against the constant is not enough.
    - **Met in part:** stacked on the WP model at turn 1 (both sheets and the leads), self-play
      still adds 0.021 nats.
    - **Not met:** no learned baseline sees the backs. So Phase 10 is unblocked only for the
      question 3d answers: does the simulator rank brings within a matchup with the players held
      fixed? 3d moves from "only if Phase 10 still matters" to next for Phase 10.
    - **For the product:** the run knew both sides' four, and a player at preview knows only their
      own. Matchup evaluation averages over the opponent's likely fours, which still has to be
      gated.
  - **It fails, and the preview head clears what the policy did not.** The policy is the gap. No
    more Kaggle self-play for team strength until a policy differs in kind, not only in depth.
  - ~~**It fails, and the preview head is no better.**~~ Closed by the ceiling: the preview head
    passes on this corpus.
- **Cost:** four Kaggle sessions (7–10 hours each), done.

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

  **A mechanism, from the review.** At the policy's leaf (`BENCH_BLEND`,
  `sidecar/showdown/endgame-solver.js`), the race is the only term that sees field conditions, and
  its weight is 0.137 on logit(race) against 1.725 on logit(HP share). The fit shrank it because
  the raw race is far too sure (log loss 1.04), and that also shrank what only the race knows:
  speed control, field turns left, KO order. Setting Trick Room changes no HP, count or stage, so
  the leaf values it at almost nothing. Fake Out's chip moves HP share directly. This is an
  inference from the weights and the archetype rates, not yet a measurement. Step 3a tests it.

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

  **Revised by the review: a leaf that learns, before depth or temperature.** The plan above
  samples at a temperature and then re-runs Phase 6 in full. That is about four Kaggle sessions
  against a check whose ceiling (0.56 for anything that sees only the two teams) sits near its pass
  line. It is dropped. The leaf is the cheaper target, and it sits under the policy, the value
  number in step 4, and any action table.
  - **The features**, each cheap to compute inside the solver:
    - the race's margin (turns to KO, HP left at the end), not only its saturated win fraction;
    - the field's share of the race: the race with and without Trick Room, Tailwind and screens;
    - field turns left, per side;
    - KO threats on the field and in the back, and priority available;
    - HP share, the count and stages, as now.
  - **The fit:** a logistic, or a small GBT exported to JS, on `bench_race.py`'s training states,
    with the step 0 games added. Score held-out log loss against `BENCH_BLEND` and the served model,
    per state kind.
  - **The check that it fixed the right thing:** a self-play sample of Trick Room pairings, read
    by `archetype_play.py`. The Trick Room set rate should move towards people's 41%, and the turn-1
    Fake Out rate towards 43%. Fake Out is the less certain half: the leaf explains Trick Room more
    clearly than it explains Fake Out.
  - **Then depth,** two turns while Trick Room is legal, only if the learned leaf leaves the gap.
  - A change of answers takes a new `race_doubles` name, and the step 4 gates re-run on it.
  - **Cost:** 2–3 laptop days. The self-play sample takes an evening on 8 workers.
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
- **3d. Brings within a series: Phase 10's own question** (review). Phase 10 asks whether the
  simulator ranks brings and leads *within one matchup*. Phase 6 asks whether it ranks *matchups
  across teams and players*. The second question is dominated by the players: rating alone reaches
  AUC 0.598 and the sheets alone 0.56. A Bo3 series holds both players and both teams fixed while
  the brings and leads change between games.
  - **The test:** conditional logistic regression with one stratum per series. In a split series,
    did each player win the game where the simulator favoured their brings? Only split series
    inform it: about 690 in the old data (game 2 went to game 1's winner 57.7% of 1,628 series),
    and more after step 0.
  - **The predictor:** self-play with each game's actual brings and leads. The brings run plays one
    game a series, so this needs every game of each series, at about the same Kaggle cost. The
    learned baselines (the bring head, the WP model at turn 1) are scored the same way beside it.
  - **Promoted 2026-10-05 by step 1.** Self-play with the brings passes and adds to both learned
    numbers, but it cannot tell the simulator from the players' choice of four. Holding the players
    fixed is what separates them.
    - **Its games:** the other games of the brings run's series that ended normally with both
      fours seen, plus the step 0 games once they join the corpus.
    - **Its export:** `step3_brings.py` already plays a game as its players brought it; it needs a
      mode that takes every game of a series.
  - **Gate:** the within-series coefficient's interval wholly above zero, and the stack's log loss
    below the WP model at turn 1 alone.
  - Phase 11 (comparing whole teams) still needs the cross-team test. For it, a Bradley–Terry with
    a player term on the larger corpus is the human-data alternative to self-play.
- **3e. A people model, learned** (review; reframes the "behaviour cloning" row under Deferred).
  Today's people model is a conditional logit on hand features. Its first pick matches people 34%
  of the time per Pokémon, and pruning keeps 54% of people's pairs at the top 12.
  - **The model:** a choice model per Pokémon over the observation, conditioned on rating, trained
    on M-B, M-C and the step 0 games. Kaggle GPU, free. It is a model of ~1100 opponents, which is
    what π_opp is meant to be.
  - **What it feeds:**
    - the root prior: scores passed into the solver, depth 1 only, so cheap;
    - the opponent model in EWP;
    - pruning recall;
    - people-like self-play, the goal of the old 3a.
  - **Gates:**
    - held-out log-likelihood against the conditional logit (1.833 nats);
    - pruning recall at the top 6, 8 and 12;
    - the policy gate (≥ 0.60 against the heuristic), unchanged.
- **What it unblocks.** Phase 10 takes whichever form passed: matchup evaluation from preview (3a),
  as a shift on the learned preview number (3b), from a position (3c), or as a ranking of brings
  and leads within the matchup (3d, the form Phase 10 actually needs). Phase 11 needs 3a or 3b, or
  the player-adjusted human fit under 3d, because team building compares whole teams. If none passes, the deterministic stack stays the
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
- **Review: the action-table null is not yet a measured null** (practice 5). The gap between the
  policy's top row and the human's comes from four draws and an argmax over near-ties. At stage 1,
  four draws picked the 32-draw reference's row in only 13–15 of 20 roots, and the median gap
  between the best and second row was 0.003. Much of the measured gap may be the winner's curse.
  If the gaps were real and calibrated, a mean of 0.07 should be worth roughly 0.01–0.02 nats
  beside the WP number, not +0.0003. Before an action table is ruled out:
  - **Reliability:** solve the same positions twice with independent dice, and correlate the two
    gaps, as Phase 6 §3 did for self-play. A low correlation makes the null a measurement null.
    Then re-solve a sample at 16–32 draws (Kaggle) and repeat the test.
  - **A lower-variance label:** the result of a game of about 7 turns is a very noisy verdict on
    one choice. Score the predicted advantage, EWP(human's row) − EWP(top row), against the
    realised one, the WP model after the turn minus before it. The WP model shares nothing with the
    solver's leaf, so this is not circular. The game's result stays the primary gate. The
    next-turn label says whether a signal is there to be powered.
  - **If the gaps hold at 32 draws,** the product problem becomes speed: that many draws within
    5 s. The levers already found are a cheaper battle copy (deserialising is 39% of a replay)
    and more solver processes.
- **`policy_value.py` (committed `2c02946`, results in the next item; the review's note).** It values the position by the
  policy's own choice (`ewp.max()`), so it does not condition on the human's action (principle 7).
  It is fitted on the WP model's validation games and scored on held-out games, per state kind.
  The review adds a second score, once, on the step 0 games. It and the learned leaf (3a) are the
  same number with different leaves, so they are run as a pair once 3a exists.
- **The policy's value as the number above two a side, run 2026-10-04**
  ([phase9-findings](phase9-findings.md), "the policy's value of a position"). The combination is
  fitted on the WP model's validation games and scored on 800 held-out games.
  - **Overall it passes:** −0.015 [−0.027, −0.002].
  - **By state kind:** nothing at 4v4 (−0.002), and about −0.03 once a side has lost a Pokémon.
    Only 4v3 clears alone, one of six intervals.
  - **Latency:** median 1.0 s, p99 2.7 s.

  **Next:**
  - **Confirmed 2026-10-04** (`policy_value.py --confirm`). The hypothesis was declared before
    scoring: "after the first faint, with more than two a side, the combined number beats the
    model". Scored once on the 939 held-out games the first run did not touch, with the same fit:
    **−0.039 [−0.051, −0.027]** (3,221 positions, 543 series), and every kind clears on its own
    (4v3 −0.031, 3v3 −0.029, 3v2 −0.072, 4v2 −0.035, 3v1 −0.024). At 4v4 it is −0.012
    [−0.024, +0.001], so the model stays the number there. The step 0 score stays a separate, later
    look.
  - **From the player's seat, as the page shows it** (`policy_value.py --seat`). The runs above
    used the model's number from the stands, and the page shows a player the model's number from
    their own seat. Refitted on validation games alone, with the policy values unchanged: after the
    first faint, −0.029 [−0.046, −0.012] on the first held-out set and −0.040 [−0.052, −0.028] on
    the fresh one; 4v4 level on both. Those weights ship: intercept −0.165, 0.482 on the model,
    0.523 on the policy.
  - **On the page, 2026-10-04** ([web-app](web-app.md), "the number above two a side"):
    - `PolicySolve` leads after the first faint, with more than two a side, from a player's seat on
      open sheets, the model underneath. It answers within 5 s through the warm pool (2.8 s on the
      fixture 3v3).
    - The Your four panel logs a `bring` tap, which sets the states a player's request sets.
    - It declines if the served open-sheet model is no longer the one the weights were fitted
      against.
    - **Timed on Fly, 2026-10-04,** through the MCP server ([deploy/README](../deploy/README.md),
      "measured on the deployed machine"). The first try returned 500 on every policy position: the
      image left out `bring_rates.json`, with the rest of `data/analysis`. With it in (release v5),
      the answer landed in 1.9 s on the fixture 3v3 and 1.8 s on a 4v3, uncached. Two positions,
      so a first look and not a p99.
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
      entry (`swap`), with a button in the Battle page's Switch pad ("Trade places with …").

### 5. The next regulation: what the old data is for, and how the new data phases in

Reg M-C rotates 2026-12-02; the next regulation is called M-D here. Being a few days late costs
little. Two questions decide the rotation: what M-C's games are worth on M-D's day 1, 7 and 30, and
when each component switches to M-D's own data. **They are answered live, on M-D's data as it
arrives** (decided 2026-10-04: no rehearsal on the M-B → M-C rotation beforehand). Expanded
2026-10-04 from v4's three bullets.

**A regulation is a regime** (principle 4, extended). Every number is re-gated on the new
regulation's held-out games. Until it is, the number is shown as *measured on M-C*, not as
calibrated. A component on M-D is always in one of four states:

| state | means | the page |
| --- | --- | --- |
| carried | gated on M-C, not yet on M-D | shown, labelled "measured on M-C" |
| undecided | M-D's gate run, power under 80% | the same, plus the gate's power |
| passed | gated on M-D | shown as now |
| failed | failed on M-D | not presented as calibrated (principle 10) |

The engine keeps leading where it led on M-C while both it and the model are carried. Its edge
comes from mechanics, which carry; the model's comes from data, which does not. Each state kind
moves to whatever M-D's own check says once that check has power (principle 11).

#### What M-C's data becomes

The components fall into four kinds by what they learned, and the gates make a fifth row. Each
has its own route to M-D.

| kind | components | after rotation |
| --- | --- | --- |
| **Mechanics, computed** | engine, calc, battle state and adapters, belief arithmetic (speed, damage, bulk, SP budget), the weakness report | carry once the Showdown pin is bumped and the parity, calc-vs-sim and validator tests pass. Their soundness is re-measured on M-D (`silently_wrong`) |
| **Few-parameter fits on generic features** | the leaves (`BENCH_BLEND`, `MELEE_*`, `FLOOR`), `doubles.TEMPER`, the people logit (`PEOPLE`), the `policy_value` combination, WP calibration | carried as M-C's fit, then refitted on M-D with M-C's fit as the prior (below). They read HP share, the count, damage and KOs, not species, so they should move little |
| **Counts over identities** | the set prior (`belief.sets`), the spread prior and Speed benchmarks (`belief.prior`), `bring_rates.json`, usage, the team pool | a species that continues starts from its M-C counts, shrunk towards M-D's as they arrive. A new species starts from its generic fallback. Benchmarks are recomputed from M-D usage, since what is worth outrunning moves with the meta |
| **Learned over identities** | the WP set encoder, the bring head, the preview head, the learned people model (3e) | trained on M-C and M-D together, M-C weighted by a fitted w, and refitted weekly. Needs the shared vocabulary first (below). M-C is dropped when an M-D-only model matches the mix on M-D's held-out games |
| **Gates and held-out shards** | every gate | regulation-specific, no carry. M-C's frozen held-out shard stays as a permanent benchmark |

#### How the new data phases in

Three mechanisms, one per fitted kind. None has a cliff: on day 1 each is M-C's answer, and it
moves to M-D's at the rate M-D's data earns it.
- **Few-parameter fits: a prior on M-C's fit.** Refit on M-D's training games with a Gaussian
  prior centred on M-C's parameters. The prior's strength is chosen by cross-validation on M-D's
  validation groups. Calibration (temperature and slope per turn bucket) goes first: it is two
  numbers a bucket and needs the fewest games.
- **Counts: shrinkage with a fitted pseudo-count.** The same form as `BRING_PRIOR_SIDES`:
  (M-D count + k · M-C rate) / (M-D total + k), with k fitted on M-D validation games instead of
  fixed. Within M-D, older weeks are down-weighted if step 5's drift check says the meta moves
  within a regulation.
- **Learned models: old data at a fitted weight.**
  - Train on M-C plus M-D with M-C's rows weighted by w ∈ {0, ¼, ½, 1}, chosen on M-D's validation
    groups. Retrain weekly, and retire M-C once w = 0 wins.
  - The GBT measurement (regulation-change, M-B → M-C) says what to expect. M-B alone came within
    0.01 of M-C's ceiling. With 3% of M-C, adding M-B was worth −0.052. With 10%, −0.012 open and
    nothing distinguishable closed. So M-C should carry M-D's first week or two.

#### Fitted live, with defaults until M-D can fit them

The phase-in is not scheduled in advance. Each weekly refit on M-D is the measurement: it chooses
the prior strength, k and w on M-D's validation groups, and the gates run on M-D's held-out games.
- **Before M-D has validation games to fit on** (roughly its first week), each mechanism runs at
  a fixed default:
  - the few-parameter fits use M-C's fit as it is;
  - the counts use k = 20, as `BRING_PRIOR_SIDES` does today;
  - the learned models use w = 1, all of M-C at full weight.

  Every component is "carried" then, so the defaults only have to be reasonable, not right.
- **Each refit is logged** (`data/analysis/reg_md/phase_in.jsonl`): the date, M-D's game count,
  the chosen prior strength, k and w per component, and each gate's verdict and power. That log is
  the phase-in schedule, measured on the rotation that matters. It becomes the runbook's table for
  the rotation after M-D.
- **What doing it live gives up:** M-D's first week runs on defaults that were never tested. A
  rotation that removes Pokémon or changes a mechanic shows up only in M-D's own data. The
  "carried" label and the weekly refits bound the cost: a bad carry is labelled from day 1 and
  weighted out within a week or two.

#### Drift inside a regulation (from step 0, nearly free)

Train on M-C games up to 2026-09-20 and score on the step 0 games. Compare with the same model on
held-out games from before 2026-09-20. The gap is how fast a model ages within a regulation. It
sets the within-regulation decay above, and says whether models need retraining during M-D, not
only at its start.

#### What to build before the rules are public

- **The shared vocabulary for the set encoder** (carried from v4): ids keyed by Showdown id across
  regulations, from Showdown's whole dex, plus about 10% identity dropout so the model leans on
  base stats, types and move summaries for what it has not seen. Without it, the set encoder cannot
  be trained on M-C and M-D together, and M-D's first weeks fall back to GBT.
- **Per-regulation fits out of the solver's source.** `PEOPLE`, `BENCH_BLEND`, the `MELEE_*`
  blends, `FLOOR` and `TEMPER` are constants in `endgame-solver.js` and `vgc.wp.doubles`. They move
  to a per-regulation fit file passed in with the search, so M-C and M-D can each be answered with
  their own fits.
- **The cache key learns the regulation and the pin.** `position_key` hashes the solver's declared
  `VERSION` and the position, not the Showdown pin or the regulation's fits. A pin bump for M-D
  could change an answer without a `VERSION` bump, and `check_cache` would not see it, because
  it re-solves under the same new pin. Add the pin's SHA and the fit file's hash to the key.
- **Two Showdown checkouts during the transition.** `check_pin` allows one pin at a time, but the
  phase-in needs both:
  - M-C's held-out benchmarks and the solver runs that fit M-C's leaves need M-C's pin;
  - M-D needs its own.

  Keep the old pin as a second checkout (`vendor/pokemon-showdown-<sha>`), chosen by the
  regulation's config. Snapshots are observations, so featurizing M-C games needs no pin.
- **One place for the default regulation.** `reg_mc` is hardcoded in five places:
  - `vgc.web.app`'s routes;
  - `frontend/src/App.tsx`'s `REG`;
  - `vgc.mcp.server`;
  - the CLI's `-r` default;
  - `vgc.policy.view.BRING_RATES`.

  `models/served.json` is already per regulation.
- **Rewrite the runbook** to this plan: the four component states, the five kinds and their
  defaults, and the weekly refit. [regulation-change](regulation-change.md) has gone stale since
  Phase 4:
  - its inventory misses everything after the WP models: the belief priors, the leaves, the
    people model, the bring rates, `served.json`, the policy and the engine's gates;
  - its gate list still has the retired "preview WP tracks simulated win rates";
  - it suggests deleting `data/replays`.

#### The data: keep, scrape, retire

- **Scrape M-C to its last day.** Its final weeks are the most mature meta and the closest in time
  to M-D. The step 0 runner is folded into `vgc meta scrape` (stop at the cached range, one reused
  connection) and run weekly. A final sweep runs the week after rotation for late uploads.
- **Keep every regulation's raw replays.** They are 129 MB today, and perhaps 0.5–1 GB by
  December. They are the pretraining corpus for every later regulation and cannot be fetched again
  once Showdown expires them. Only derived data (snapshots, features, self-play) is deleted, and
  only once M-D's models pass without M-C's.
- **Keep M-C's frozen split and held-out shard as a fixed benchmark.** A change to anything
  mechanics-only (engine, belief, adapters) is re-scored on it under M-C's pin. That is a
  regression check that does not wait for M-D's data.
- **On M-D's day 1:** freeze M-D's split with the hash rules (never re-freeze), and scrape daily
  for the first two weeks. Every component starts carried. At M-C's day 10 there were about 800 Bo3
  games, so expect the in-battle gates to be undecided for one to two weeks. The phase-in log
  records how long it actually took.

**Order:**
1. the drift check: on the step 0 games once they are in;
2. per-regulation fit files and the cache key;
3. the shared vocabulary;
4. the runbook rewrite;
5. day 0, when the rules are public, then a weekly refit.

Items 2 and 3 are code. Item 3 also needs a Kaggle GPU session to train and check the set encoder
on the shared vocabulary. None of it competes with steps 3–4 for Kaggle CPU.

### 6. Loose ends, taken when they block something

- ~~**The assumed non-Speed spread in doubles.**~~ **Done 2026-10-04**
  ([phase8-findings](phase8-findings.md), "the assumed non-Speed spread in doubles"). The 674 held-out
  games were re-solved with every guessed spread refilled HP-first and then defences-first. The answer
  moves (mean 0.04–0.07, the favoured side flips in 6–8% of games), but each refill alone is worse
  than the imputer's (+0.013, +0.018, intervals across zero). The mean of the three is level
  (−0.004 [−0.019, +0.010]). The number keeps the imputer's spread; the page shows the other two
  beside it, with their range on the bar.
- ~~**Time the doubles answer on the deployed machine.**~~ **Done 2026-10-04** through the MCP
  server, on fixture positions ([deploy/README](../deploy/README.md)). Uncached, the searched
  answer landed at 3.4 s in a 2v2 and 2.5 s in a 1v2, and the two other guesses at 8.2 s and
  4.5 s, on their own budget. A 1v1's first answer landed within 2 s. Still open: back-to-back
  answers on shared cores (burst credit), since the repeat round ran on cached answers.

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
| Learned preview / team-strength WP worth showing | A corpus 3+ orders of magnitude larger. Step 0 adds a few times the corpus, not orders of magnitude |
| Behaviour cloning *for strength* | A higher-rated corpus. The highest rating seen is 1610 (M-C). As a model of people, the opponent the policy assumes, it is not deferred: step 3e |
| Self-play generation for WP training | A policy that passes Phase 6, or the within-series test (step 3d) |
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
13. **Check that the corpus is current before a power-limited gate.** The M-C cache stopped at
    2026-09-20 while every gate in step 4 ran short of power (step 0).
14. **Ask whether a test asks the question the product needs.** Phase 6 tests matchups across
    teams; Phase 10 needs brings within one matchup (step 3d).

---

## Risks

| Risk | Handling |
| --- | --- |
| Phase 6 cannot be passed on this corpus by anything | Step 1 measured the ceiling (0.56, near the pass line). The full re-run is dropped; step 3d tests what Phase 10 needs with the players held fixed |
| Re-testing a narrowly failed gate on more data becomes a second look | Each re-test is declared in step 0 before scoring, scored once on the new games alone, and reported beside the old result |
| The new games differ from the old (a later meta, a shifted rating mix) | Checked against the corpus population before use (practice 2). A shift is reported, and a gate that holds across it is the stronger for it |
| The scrape is refused or rate-limited | Polite rate (0.3 s a replay), resumable; the staging directory keeps whatever arrived |
| The diagnostics find no single cause | Step 3 is built only where a step 2 check points; otherwise the floor holds and nothing more is spent on self-play |
| Move advice that misleads | Gated against human results first (step 4); open sheets only; the WP model stays on screen |
| The policy misses 5 s on shared cores | Fewer rows (K) above two a side, the quick answer kept, or a `performance-1x` machine |
| Reg M-C rotates 2026-12-02 | Step 5. Every component starts M-D "carried" with a label, so being late costs accuracy, not correctness. The fit files, the cache key and the shared vocabulary have to be built before then |
| The phase-in defaults are wrong for M-D (no rehearsal tested them) | Every component is labelled "carried" until M-D's own gate decides; the weekly refit chooses the weights on M-D's data |
| M-D removes Pokémon or changes a mechanic | The runbook's mechanics-changes table; the generic fallback for unseen identities; and M-C's weight w fitted on M-D's own data, so a bad carry is weighted out within a week or two |
| A Showdown pin bump changes cached answers silently | The pin's SHA and the fit file's hash join the cache key (step 5) |
| Showdown expires old replays | Raw replays for every regulation are kept on disk (step 5) |
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
