# Phase 4 review — what the corpus supports, and what it doesn't

Date: 2026-09-20 · Reviewer pass over the WP work, independent of the sweep post-mortem already in
[PLAN.md](../PLAN.md). Every number below was measured in this repo on the frozen split. The four
scripts that produced them are in [`scripts/analysis/`](../scripts/analysis/) and re-run under
`.venv` with `PYTHONPATH=src`:

| script | section |
| --- | --- |
| `preview_signal.py` | §1 Bradley-Terry, §2 self-play transfer |
| `preview_learning_curve.py` | §1 learning curve |
| `win_drivers.py` | §1 player vs team, §3 forfeits |
| `selfplay_value.py` | §4 ablation |

**Verdict: the Phase 4 gate set is failing for a reason no model change can fix.** Pre-battle win
probability is not learnable from this corpus, the self-play corpus transfers *negatively* to human
outcomes at team level, and the mid-battle model already passes the criteria it is being judged
against. PLAN.md's sweep block reaches much of this from the sweep alone; this pass adds the
measurements that turn "we think the corpus is the constraint" into a number, and adds three findings
it does not contain: that the self-play corpus adds nothing measurable to the model that does work,
the corpus population, and that the sweep never ran on a GPU.

---

## 1. There is no pre-battle signal, and more replays will not create it

Three independent measurements, none of which involve the set encoder.

**Bradley-Terry over team composition.** `x = (my six one-hot) − (their six one-hot)`, `y = I won`,
no intercept, so the model is antisymmetric by construction. Trained on human preview rows, scored on
the frozen held-out human preview rows (n=1,730):

| trained on | held-out human preview log loss | accuracy |
| --- | --- | --- |
| constant 0.5 | 0.6931 | — |
| human preview rows (4,345 battles) | **0.6900** | 0.509 |
| self-play preview rows (108,918 rows) | **0.7327** | 0.490 |

**Learning curve, 5-fold CV over all 8,252 human games.** If the deficit were sample size, the lift
would still be climbing steeply:

| share of games | n | CV log loss | lift vs 0.6931 |
| --- | --- | --- | --- |
| 25% | 2,063 | 0.6900 | +0.0031 |
| 50% | 4,126 | 0.6878 | +0.0053 |
| 75% | 6,189 | 0.6880 | +0.0051 |
| 100% | 8,252 | 0.6865 | +0.0066 |

Quadrupling the corpus bought 0.0035 nats. Extrapolating that rate, a lift worth showing a user
(~0.03, i.e. a genuine 57/43 read on an average matchup) needs the corpus to grow by three to four
orders of magnitude. **Scraping more replays is not the fix.**

**What is knowable before turn 1, at all.** Same 5-fold CV, same games, comparing team composition
against *player identity*:

| pre-battle predictor | CV log loss |
| --- | --- |
| constant 0.5 | 0.6931 |
| player identity (Bradley-Terry, 3,433 players) | 0.6835 |
| team composition | 0.6869 |
| both | 0.6826 |

Everything knowable before the first turn — who is playing *and* what they brought — is worth about
0.01 nats, and the player matters at least as much as the team. A model that perfectly knew team
strength would still be reading a game whose outcome is dominated by play and variance.

Two controls worth recording. Restricting to games that were played out (`ended_by: normal`,
≥6 turns, n=4,076) *reduces* the lift to +0.0029, so the flat result is not forfeits washing out a
real signal. And `wp-v1-gbt` — gradient-boosted trees on hand features, sharing no inductive bias
with the set encoder — scores 0.69315 at preview, the constant to five decimals. Three unrelated model
families all find exactly nothing.

## 2. The self-play corpus transfers negatively at team level

The second row of the first table is the finding that should change the plan. A team-strength model
fit on 108,918 self-play preview rows scores **0.7327 on held-out human preview — materially worse
than answering 0.5** — at 49.0% accuracy. Its ordering of which team beats which is not merely
uninformative about human play; it is slightly inverted.

PLAN.md records the related observation that "67% of pairings land ≥85/15 under heuristic-vs-heuristic
play, which measures the heuristic's blind spots as much as the matchup," and files it as a caveat to
re-read after Phase 6. It should be a blocker. Phase 7 proposes building the matchup matrix under a
policy of this class and deriving team evaluation from it, and Phase 8 builds team building on that
matrix. The one measurement available today says the quantity that pipeline produces is not the
quantity the user cares about.

Caveat in both directions: this test conflates two things — the self-play *policy* being unlike human
play, and the self-play *team pairings* being drawn differently from the real meta. It does not
separate them. It is, however, the cheapest available read on PLAN.md's revised-plan item 1, and it
points the same way.

### The test that settles it

PLAN.md already names the right experiment ("validate the simulator against human outcomes before
building on it"). It is better powered than anyone has assumed, because the data is already on disk:

```
8,252 human battles with a preview snapshot
7,455 with both team sheets resolvable to the pool   (vgc.meta.replays.team_id)
3,372 distinct real-meta pairings, 2,796 of them seen ≥2 times, max 12
```

Simulate each pairing under the heuristic for N battles, then score the simulated WP as a predictor of
the *actual* human result of those 7,455 games — log loss and AUC against constant 0.5, not a Pearson
correlation on 30 points. At 1,000 pairings × 50 battles and ~35 battles/s that is roughly 25 minutes
of laptop time.

This matters because the existing `vgc wp check-preview` uses 30 pairings, 15 held out. A correlation
on n=15 has a 95% interval of about ±0.5, so `PREVIEW_CORR_GATE = 0.5` is being applied to a statistic
that cannot distinguish "no signal" from "passing". The MAE-against-constant line in the same output is
the trustworthy part, and it fails cleanly.

Read the outcome as a fork:

- **Simulated WP beats the constant on real games** → the simulator is a valid team evaluator, Phase 7
  lives, and the right move is coverage (`--per-pair 1`, continuous WP targets) as PLAN.md proposes.
- **It does not** → Phase 7 as designed is dead, and so is any learned team-strength model distilled
  from it. The product then rests on the deterministic stack in §6, which is still a real tool.

Either way the answer is worth more than any further sweep.

## 3. The corpus is the wrong population for the stated goal

Counted over all 8,300 cached replays:

| | |
| --- | --- |
| Rated | 3,473 (42%) — **58% carry `rating: null`** |
| Rating distribution (rated only) | min 1000 · p25 1050 · **median 1101** · p75 1171 · p95 1314 · max 1578 |
| Distinct players | 3,433, of whom **28.7% appear in exactly one game**; median 2 games |
| Notable participant | `pcrlbot12d159c39a`, 101 games — a bot is in the training corpus |
| Games ending in forfeit | **38.4%** |

Every gate in Phase 4 is calibrated against near-starting-Elo ladder play, while the product is aimed
at competitive VGC. Two consequences:

- **The headline number is flattered by forfeits.** `wp-v1-set-full` scores 0.5365 on forfeited games
  against 0.5776 on played-out ones; 34% of held-out rows are forfeits. Players quit from lopsided
  positions, so part of the measured skill is quit detection. A gate should score `ended_normal`
  separately rather than pooling — this is also PLAN.md's conclusion.
- **It caps Phase 6's behaviour cloning.** BC on this corpus clones ~1100-rated play. VGC-Bench's BC
  worked because it imitated *high-rating* players out of 700,000 logs (§5).

## 4. Mid-battle WP is the piece that works

Nothing in this pass contradicts PLAN.md's by-bucket table: `wp-v1-gbt` beats the constant in every
in-battle bucket, improves monotonically as the board reveals itself, and holds ECE < 0.03 throughout.
Restricted to in-battle rows, the Phase 4 verification criteria pass today, and the all-or-nothing gate
set was reporting a working model as failing because a different task, sharing the same model, failed.

_Resolved in f180164 and 16a2a03, the same day as this pass:_ `gates()` now returns `in_battle_pass`
from three row-scoped gates — beat the constant in every turn bucket, hold ECE < 0.03 in the *worst*
bucket rather than on average, and hold it again on `ended_normal` alone. `wp-v1-gbt` passes all three
and is carded (spectator n=30,698: 0.54428 / ECE 0.01245; played-out n=20,312: 0.55478 / ECE 0.02013);
`wp-v1-set-full` does not (t7+ 0.031, played-out 0.039), so the split discriminates rather than
relabels. `web.app.in_battle_version` routes the Battle and Simulate WP track to a model whose
in-battle gates pass.

### The self-play corpus contributes nothing to it either

The same GBT recipe on hand features, trained three ways and scored on the frozen held-out human
shard (`eval_human_ots`):

| training rows | spectator (n=9,034) | ECE | player_approx (n=17,468) | ECE |
| --- | --- | --- | --- | --- |
| human + self-play (637,508 rows) | 0.5533 | 0.0198 | 0.5776 | 0.0123 |
| **human only (178,339 rows)** | **0.5525** | **0.0158** | **0.5742** | **0.0083** |
| self-play only (459,169 rows) | 0.5793 | 0.0451 | 0.6378 | 0.0940 |

Human-only matches the mixture on log loss — the 0.0008 gap is well inside noise — and is
*better calibrated* in both perspectives. The honest reading is not "human-only wins" but
**"60,000 self-play battles bought no measurable improvement on the task that works."** 4,345 human
battles do the job that 42,705 mixed battles do. Self-play alone is clearly worse, and in the player
view it is badly miscalibrated (ECE 0.094) and produces 0.8374 at preview — worse than a coin flip
again, the same negative transfer as §2 from the other direction.

The generation pipeline is 412 MB of snapshots, 81 MB of runs and an evening of compute. For WP
training specifically, it is not earning that. Its remaining justification is as the *simulator*
behind on-demand matchup evaluation (§7.5) — which is precisely what the experiment in §2 tests.


## 5. Operational findings

**The Kaggle sweep never touched a GPU.** All seven runs report `"torch": "2.10.0+cpu"` and
`"device": "cpu"` in `train.json`, at 485 s/epoch. `enable_gpu: true` *is* set in the kernel metadata
(`scripts/cloud/kaggle_sweep.sh`), but the image's torch wheel is CPU-only, so `--device auto` fell
back silently. The notebook prints `torch.cuda.is_available()` and nothing asserts on it. Roughly
three hours of wall clock, against a docstring that still promises "2–3 minutes".

Corrected in the archive the same day by 4f2150b, which adds the part this pass missed: the tell was
visible and was read backwards — the weekly GPU quota still showing 0.00h used was taken as good news
about the runs that had failed, rather than as evidence that no GPU was ever attached.

This does not change the "stop sweeping" call — §1 and §2 stand on their own — but it means the 30
GPU-hours/week are unspent and **this project has no demonstrated GPU path**. The check is
`train.json: device`, not the kernel metadata, which records only what was requested.

**Five stale claims that misdescribe the data.** `set_torch.py` said human rows were "~3% of the
data" and "a few percent"; the notebook and `docs/cloud-compute.md` said validation was "~89%
self-play". Measured on `data/features/reg_mc/wp-v1`, human rows are **28.0%** of both train and val
— more than their 10.7% share of battles, because self-play is thinned by
`features.train_orientations` while human games are kept whole with both orientations. At
`--human-weight 4` that is ~61% of the loss. Anyone reasoning about the balance from those comments
was off by an order of magnitude. *Fixed 2026-09-20.*

**The sweep's models came home uncarded.** At the time of this pass, `models/registry.json` listed
`wp-v1-sw-split-small` with an empty `headline` and empty `gates`, and six further `wp-v1-sw-*`
directories held `model.onnx` + `train.json` with no card at all. *Settled in 16a2a03:* six keep
`train.json` only, as the tracked evidence for two conclusions that cost real time — that
hyperparameter tuning is closed off, and that the run never touched a GPU; `wp-v1-sw-split-small`
keeps a full card; `wp-v1-sw-uniform-05`'s half-finished card was dropped rather than committed,
so a registry row reading "(not evaluated)" could not be mistaken for an oversight.

## 6. Against VGC-Bench (arXiv 2506.10326)

Angliss, Cui, Hu, Rahman and Stone, *"VGC-Bench: Towards Mastering Diverse Team Strategies in
Competitive Pokémon"*, AAMAS 2026. PLAN.md cites it as prior art; this section records where the
repo's own results agree with it, and the one place they go further.

**Agreement, and it should be leaned on harder.** The paper's central negative result is that
performance on any one team degrades "considerably and consistently" as training team count grows
(1→4 teams drops win rate 0.699→0.594), and the authors conclude the complexity "simply can't fit into
the deep neural network". The sweep reproduced exactly that shape: human validation loss turns upward
at epoch 1–2 in *every* configuration while self-play loss keeps falling, and identity dropout is the
only thing holding the model together. That is not a tuning miss — it is the paper's finding appearing
in a 575k-parameter model. Their recommendation is search over an accurate simulator. This repo already
has the accurate simulator, which is the expensive half of that recommendation.

**Where this repo can contribute a result the paper does not have.** The paper's proposal for team
building is to use trained agents to "evaluate candidate teams and provide a reward signal for
searching the vast team configuration space" — which is Phase 7. Their generalization test is cross-play
between agents on 72 unseen teams: entirely inside the self-play world, never against human outcomes.
§2 is evidence that the step from agent-evaluated matchups to real team strength is exactly where it
breaks. The experiment in §2 would produce the missing number: *does agent-derived team evaluation
predict human results at all?* Nobody appears to have published it.

**Where the paper's scale explains this repo's ceiling.** 700,000 OTS battle logs and 8×A40 against
8,300 replays and no GPU. Their BC cloned high-rating players; §3 shows this corpus is 58% unrated with
a median of 1101. The gap in §1 is corpus, not method — consistent with their scale being three orders
of magnitude larger on the axis that matters.

**One warning for Phase 6's gates.** The paper finds that in almost all cases their agents are
"approximately 100% exploitable" by a trained best-response. Phase 6 currently gates on "EWP-greedy
beats the heuristic ≥60% over 500 battles". Beating a fixed, weak, deterministic opponent is close to
the measurement the paper shows can be near-meaningless; the gate should include play against a
held-out policy, or at minimum against the human corpus.

## 7. What this implies for the order of work

The stack's genuine asset is a pinned, verified simulator and an exact damage calculator. The evidence
above says learning is currently paying off in exactly one place (mid-battle WP from board state) and
failing in the place the plan spends the most on (team strength). So the deterministic capabilities
should ship first, not last.

1. ~~**Ship in-battle WP now**, with bucket-scoped gates and `ended_normal` reported separately.~~
   **Done 2026-09-20** in f180164 + 16a2a03 — see §4. The one leftover is the pair of stale docstrings
   in §5.
2. **Run the §2 experiment before anything else is built on the simulator.** It is ~25 minutes of
   laptop time and it decides whether Phases 7 and 8 exist in their current form. Until it returns,
   **stop generating self-play for WP training** — §4 shows that spend is not converting into
   accuracy, and its only live justification is the simulator role the experiment is testing.
3. **Move the analytic weakness report forward.** Phase 8 opens with "analytic weakness report first
   (no model, immediately useful)" and it sits behind four phases of ML. Type coverage, speed tiers,
   what OHKOs you, which common threats you lose to — pure computation over the regulation dex and the
   usage pool. It answers question 1 today, with no model and no gate.
4. **Ship usage statistics from the 15,028 team sheets.** Descriptive, correct by construction, and
   directly useful for team building. It is what players get from Pikalytics for other formats, and
   this repo appears to hold the only corpus of its kind for Champions.
5. **On-demand matchup simulation instead of a learned team-strength function.** PLAN.md's revised item
   4 has this as a fallback; §1 argues it is the answer. The app faces one matchup at a time and can
   simulate *that* pairing across all 90 bring combinations on demand — exact under the policy, no
   generalization required. This converts an impossible learning problem into a tractable computation.
   It is still gated on item 2: if the heuristic's outcomes do not track human ones, a better policy is
   a prerequisite, not a nicety.
6. **Spend the recovered GPU budget on the policy, not on WP.** Per §5 the 30 GPU-hours/week were never
   used. Per §3 and §6, BC on this corpus clones ~1100-rated play, so the honest expectation from BC
   here is a better-than-heuristic opponent model, not a strong one.

---

## Changes to the plan

1. **The preview gate is unreachable from this corpus** (§1). Either retire it, or restate it against
   simulated WP once §2's experiment says the simulator is a valid target.
2. **Self-play → human transfer is a Phase 7 blocker, not a Phase 6 caveat** (§2). Do not build the
   matchup matrix before the validation experiment runs.
3. **Self-play generation for WP training pauses** (§4). 60,000 battles bought no measurable
   accuracy over the 4,345 human battles alone, and cost calibration in both perspectives.
4. **Gates are scored per bucket, and `ended_normal` separately** (§3, §4).
5. **Assert on the accelerator in the Kaggle notebook** (§5).
6. **Phase 8's analytic weakness report and a usage report move ahead of Phases 5–7** (§7).
7. **Phase 6's strength gate needs a held-out opponent**, not only the heuristic (§6).
