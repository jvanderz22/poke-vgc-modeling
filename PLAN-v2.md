# VGC Reg M-C Model & Advisor — Plan v2

**Supersedes [PLAN.md](PLAN.md)**, which is kept as the archive: it holds the original research, the
phase 0–4 completion notes, and the post-mortems that produced this rewrite. Nothing here contradicts
its *architecture*; what changes is the order of work, the gates, and what is expected to come from
learning rather than computation.

v2 exists because Phase 4 produced a measurement that invalidates the plan's spine: **the quantity
Phases 7 and 8 were going to be built on cannot be estimated from this corpus, and the simulator's
version of it has never been checked against reality.** The evidence is in
[docs/phase4-findings.md](docs/phase4-findings.md); the consequences are here.

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
| 6 — Simulator validity | ⏳ Next | the fork in the road; ~25 min of laptop time |
| 7 — Deterministic team tools | — | weakness report + usage report; no model, no gate |
| 8 — Closed-sheet belief | — | was Phase 5; the regime the product actually runs in |
| 9 — Policy strength (EWP + search) | — | was Phase 6, minus behaviour cloning |
| 10 — Matchup evaluation | — | was Phase 7; form depends on Phase 6's answer |
| 11 — Team building | — | was Phase 8 |
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
human rows alone (§3 below). The third because the one transfer test that has been run came back
*negative* (§2).

---

## The questions, and what can answer them today

| | Question | Status under v2 |
| --- | --- | --- |
| 1 | Given 6 Pokémon, what are my weaknesses? | **Phase 7, analytically. No model.** Type coverage, speed tiers, calcs vs the top-30 threats. Buildable now. |
| 2 | Given 4 Pokémon, which 2 complete the team? | **Blocked on Phase 6.** Needs a team-strength signal; the two candidate sources are unvalidated (simulator) or absent (corpus). |
| 3 | What moves should each Pokémon run? | **Partly Phase 7** (legal movepool, coverage gaps, breakpoints are analytic); ranking by win rate is blocked with Q2. |
| 4 | What do I bring, lead, and click? | **Phases 9–10.** Bring/lead is on-demand simulation of *this* matchup, not a learned function. |
| 5 | Win probability | **In-battle: works, ship it (Phase 5). At preview: does not exist** and is retired as a target until Phase 6 says otherwise. |
| 6 | All of the above in a web app during a real game | Staged; W2 lands with Phase 5, and **closed-sheet support (Phase 8) is the real unlock** — see the regime note in §5. |

---

## What the evidence changed

Six findings, each measured on the frozen split. Full detail and the scripts:
[docs/phase4-findings.md](docs/phase4-findings.md), [`scripts/analysis/`](scripts/analysis/).

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
evaluation from it) is blocked, not caveated, until Phase 6 returns.*

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

### Phase 6 — Simulator validity: the fork in the road

**The single highest-value experiment available, and it gates four phases.** Everything downstream of
L3 assumes heuristic-vs-heuristic matchup win rate reflects real play. That has never been tested, and
finding 2 is a reason to doubt it.

The data is already on disk and better powered than anyone assumed:

```
8,252 human battles with a preview snapshot
7,455 with both team sheets resolvable to the pool   (vgc.meta.replays.team_id)
3,372 distinct real-meta pairings, 2,796 seen ≥2 times, max 12
```

Simulate each pairing under the heuristic for N battles; score simulated WP as a predictor of the
*actual* human result of those games — **log loss and AUC against a 0.5 constant**, not a Pearson
correlation. At 1,000 pairings × 50 battles and ~35 battles/s that is ~25 minutes.

This replaces `vgc wp check-preview`, which decides the same question on 30 pairings, 15 held out — a
correlation whose 95% interval is roughly ±0.5, applied against a gate of 0.5. It cannot distinguish
"no signal" from "passing signal" and should not be used for a verdict again.

_Verification, as a fork:_

- **Simulated WP beats the constant** → the simulator is a valid team evaluator. Phase 10 proceeds,
  self-play generation resumes with coverage over replication (`--per-pair 1`, ~60,000 distinct
  matchups for the same wall clock; label pairings with continuous simulated WP and regress, rather
  than emitting 20 binary rows per input).
- **It does not** → Phase 10's matrix form is dead and so is any team-strength model distilled from
  it. Phase 9 (a stronger policy) becomes the prerequisite, and the experiment re-runs against that
  policy before anything is built on it.

Re-run this against every new policy. It is the project's standing check that the simulator measures
the game rather than the bot.

### Phase 7 — Deterministic team tools _(independent of Phase 6)_

v1 opened Phase 8 with "analytic weakness report first (no model, immediately useful)" and then put
four phases of ML in front of it. It moves here, alongside a second tool the corpus already supports.

**Weakness report (Q1, and part of Q3).** Type-coverage matrix, speed tiers under Tailwind and Trick
Room, and real damage calcs against the top-30 meta threats: "nothing on your team OHKOs X", "Y OHKOs
three of your six", "you lose to Trick Room", "no answer to redirection". Deterministic, explainable,
every number traceable to the pinned calc.

**Usage report.** 15,028 open team sheets already cached: species usage, item and ability
distributions, move frequencies, common partners, imputed spread clusters. Descriptive and correct by
construction. This appears to be the only corpus of its kind for Champions, and it is what players
get from Pikalytics in other formats.

_Verification:_ every claim in a weakness report resolves to a calc or a dex lookup, checked against
the simulator on a sample; usage numbers reconcile to sheet counts; both run offline with no model.

### Phase 8 — Closed-sheet belief _(was Phase 5)_

Moves up because of finding 5: the model and every eval set cover **open** sheets (7,459 battles), and
the game the product is used in runs **closed** (779 battles). The app's current stopgap fills each
species with its most common set and evaluates one guessed team at full confidence — a wrong item is
wrong, not uncertain.

Build: set prior from the sheet corpus and usage (`P(item, ability, moves, nature | species)`); belief
updated by hard reveals and by a calc-based damage likelihood; K complete-set particles per opponent
Pokémon; `WP_v2(o) = E_belief[WP_v1(o completed by particle)]`, reusing the in-battle model as the
inner model.

_Verification:_ on held-out closed-sheet games, v2 log loss sits between v1-with-oracle-sets (lower
bound) and v1-with-prior-only (upper bound), and moves toward the oracle as sets reveal. Belief
calibration: the true item and nature land in the belief's 80% set ~80% of the time. **Report the
closed-sheet sample size on every verdict** — 779 battles is small, and a gate that ignores that will
mislead.

### Phase 9 — Policy strength: EWP and search _(was Phase 6, minus BC)_

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

### Phase 10 — Matchup evaluation _(was Phase 7)_

Form depends on Phase 6.

**Default — on-demand, no generalization.** The app faces one matchup at a time. Simulate *that*
pairing across all 90 bring/lead combinations under the strongest affordable policy, with the racing
allocator and common random numbers. Exact under the policy, needs no learned team function, and
answers Q4's "what do I bring" with a number and an interval. v1 listed this as a fallback; findings
1 and 2 make it the primary.

**Only if Phase 6 passes — the precomputed matrix.** Dated meta gauntlet, `winrate(A, B)` over the
pool, racing allocator, successive halving. Keep v1's policy-artifact check: compute a subset of cells
under two policies and compare rankings.

_Verification:_ bring/lead recommendations carry intervals and the number of battles behind them;
rankings correlate positively with real-world results; top-usage and tournament-winning teams land in
the upper half. Do not build Phase 11 on a matrix that fails.

### Phase 11 — Team building _(was Phase 8)_

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
   outcomes — the n=15 correlation against a 0.5 gate is the standing example. A gate records its n
   and the interval of its statistic.
4. **Simulator-derived quantities are validated against human outcomes before use.** Phase 6 is the
   instance; the rule is general, and re-runs whenever the policy changes.
5. **Strength gates include a held-out opponent.** Beating the fixed prior you trained against is not
   evidence of strength.
6. **A failing gate is recorded in the model card and the UI refuses to present that number.** Carried
   unchanged from v1 — it worked, and it is why finding 1 surfaced instead of shipping.

---

## Deferred, pending evidence

Not cancelled — waiting on a specific measurement, named here so it is not rediscovered.

| Deferred | Unblocked by |
| --- | --- |
| **Learned preview / team-strength WP** | A corpus three-plus orders of magnitude larger, or Phase 6 passing so the simulator can be the target instead of human games. (Findings 1, 2.) |
| **Behaviour cloning** | A higher-rated corpus. VGC-Bench's BC worked on 700,000 logs from *high-rating* players; this corpus is 58% unrated, median 1101. (Finding 5.) |
| **Self-play generation for WP training** | Phase 6. The rows are not paying for themselves. (Finding 3.) |
| **Hyperparameter sweeps on the set encoder** | Nothing — closed off. Every config selected epoch 1 or 2 of 12; the constraint is coverage, not regularization. GPU time is better spent elsewhere. |
| **PPO self-play fine-tuning** | Nothing. Optional-and-last in v1 for technical reasons; cost and the paper's results both confirm it. |
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
   afternoon of measurement turned it into a blocker.
8. **Tag every artifact with its regulation id.** _(unchanged.)_
9. **Log battles in a replayable format from day one.** _(unchanged.)_
10. **New — measure what a data source buys before scaling it.** A three-way ablation costs an hour
    and would have caught finding 3 before 60,000 battles were generated.
11. **New — check the corpus population before trusting the metric computed on it.** Rating
    distribution, participant concentration, how games end. All three changed the reading here.
12. **New — assert on the environment you think you are paying for.** The sweep ran on CPU for hours
    with `enable_gpu: true` set and nothing checking.

---

## Risks

| Risk | Handling |
| --- | --- |
| **Phase 6 returns negative and the simulator does not predict human outcomes** | The product falls back to the deterministic stack — calc, weakness report, usage report, in-battle WP, closed-sheet belief. Still a real tool. Phase 9 then becomes the route back, not a rewrite. |
| **The closed-sheet corpus (779 battles) is too small to gate Phase 8** | Report n on every verdict; widen with continued scraping of the Bo1 format; treat belief calibration as directional until the sample grows. |
| Policy too weak → meaningless team rankings | Phase 6 is now the hard stop, and it runs *before* the matrix rather than as a check inside it. |
| Not enough Reg M-C replays | Established: more replays do not fix preview (finding 1). They still help in-battle WP and the closed-sheet regime, which is where scraping effort should go. |
| Reg M-C rotates 2026-12-02 | L0 spine is the mitigation; [docs/regulation-change.md](docs/regulation-change.md). Note that the deterministic tools (Phase 7) port with the dex and need no retrain — another reason to build them first. |
| 19 GB free disk | Finding 3 helps: 412 MB of self-play snapshots are not earning their keep for training. |
| torch 2.2.2 ceiling | Unchanged: ONNX for inference, plain torch for training. |

---

## Cost and hardware

Unchanged from PLAN.md and still **$0–5 total**, with one correction: the free Kaggle quota was never
actually consumed (finding 6), so the training budget is fully intact. The standing advice holds — for
this project **CPU-hours are worth more than GPU-hours**, which is the opposite of most ML work.
Phases 6, 9 and 10 are CPU-bound simulation; a 32-core box for a few hours (~$1–3) is the rental that
would actually help, not a GPU.

Spend controls unchanged: prepay, no auto-refill, develop locally first, never leave a pod idle,
Kaggle before paid.

---

## Web app staging

| Stage | After | Adds |
| --- | --- | --- |
| W1 | Phase 3 ✅ partial | `vgc web`, team library, validation, active team, bring/lead ranking. **Still missing:** pokepast.es fetching, calc panel, per-turn input, WP timeline |
| **W2** | **Phase 5** 🟡 | In-battle WP routed to a gate-passing model and banner-flagged ✅; **left:** the per-turn WP timeline |
| W2b | Phase 7 | Weakness report and usage report in the library |
| W3 | Phase 8 | Belief panel for closed-sheet games; manual corrections; the stopgap's share display survives |
| W4 | Phases 9–10 | EWP action table with intervals and worst-case replies; on-demand bring/lead simulation; state-reconstruction parity checks; post-game review |
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
  [docs/phase4-findings.md](docs/phase4-findings.md) then measured.

Read v2 for what to do next. Read PLAN.md for why the stack is built the way it is.

---

## Sources

- [VGC-Bench (arXiv 2506.10326)](https://arxiv.org/html/2506.10326.pdf), Angliss, Cui, Hu, Rahman & Stone, AAMAS 2026 · [code](https://github.com/cameronangliss/vgc-bench) (MIT)
- [philmantatsky/VGC-Pokemon-Showdown-AI](https://github.com/philmantatsky/VGC-Pokemon-Showdown-AI) (MIT) — Reg M-B Champions port
- [vbbjandrade/pokemon-champions-data](https://github.com/vbbjandrade/pokemon-champions-data) (CC BY 4.0)
- [poke-env](https://github.com/hsahovic/poke-env) · [smogon/damage-calc](https://github.com/smogon/damage-calc) · [ychen022/VGCHelper](https://github.com/ychen022/VGCHelper) (unlicensed — reference only)
- [MetaVGC Reg M-C](https://metavgc.com/regulations/regulationm-c) · [Victory Road](https://victoryroad.pro/champions-regulations/) · [Pikalytics Reg M-C](https://www.pikalytics.com/pokedex/gen9championsvgc2026regmc) · [ChampDex Stat Points](https://champdex.com/guides/stat-points)
- In-repo: [docs/phase4-findings.md](docs/phase4-findings.md) · [docs/phase0-findings.md](docs/phase0-findings.md) · [docs/cloud-compute.md](docs/cloud-compute.md) · [docs/regulation-change.md](docs/regulation-change.md) · [docs/web-app.md](docs/web-app.md)
