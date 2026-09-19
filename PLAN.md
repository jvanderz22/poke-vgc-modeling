# VGC Reg M-C Model & Advisor — Implementation Plan

## Progress

| Phase                       | Status         | Notes                                                                                  |
| --------------------------- | -------------- | -------------------------------------------------------------------------------------- |
| 0 — Environment spike       | ✅ Done 2026-09-19 | [findings](docs/phase0-findings.md): 36.5 battles/s (8 workers), pins set, ONNX ok |
| 1 — Foundation              | ✅ Done 2026-09-19 | `vgc` CLI, L0 loader, validator, calc sidecar; 42 tests; calc = simulator on 4 scenarios |
| 2 — Battle layer, tier 1    | ✅ Done 2026-09-19 | heuristic 98.4% vs random (500, CI 96.9–99.2%); seeded runner, identical across worker counts; 52 tests |
| 3 — Battle data             | ⏳ Next        | snapshots per perspective, human replay corpus, frozen held-out sets (split from old 3) |
| 4 — Win probability v1 (OTS) | —             | **new** — spectator + player perspectives, from team preview on                        |
| 5 — Win probability v2 (closed sheets) | —   | **new** — belief over opponent sets in player mode                                     |
| 6 — Expected WP, BC + search | —             | driven by expected win probability per action                                          |
| 7 — Team evaluation         | —              | was 3; now judged by the Phase 6 battle stack                                          |
| 8 — Team building           | —              | was 4                                                                                  |
| 9 — Interface               | —              | CLI/MCP **plus the battle-companion web app** (staged W1–W5)                           |

_Order changed 2026-09-19: battle strength first (3–6), then team evaluation and building (7–8) on top of it._

**Changes from the original plan (from Phase 0):**

- **Two venvs, not one pin set.** poke-env 0.16.1 needs numpy ≥2, and torch 2.2.2 can't exchange arrays
  with numpy 2. `.venv` (poke-env, onnxruntime, no torch) runs everything. `.venv-train` (torch 2.2.2 +
  numpy 1.26, no poke-env) only smoke-tests the BC trainer, which reads `.npz` and never imports poke-env.
- **Showdown pin is ours, not the dataset's.** `pokemon-champions-data` fetches M-C from Showdown `master`,
  so it records no SHA. Pinned `pokemon-showdown@2ddfa04` + `pokemon-champions-data@bc6d0a8` (same date) as
  submodules. Bump them together.
- **Showdown accepts a `Tera Type:` line in Champions** (it ignores it), so our validator must reject Tera.
- **SP lives in Showdown's `evs` field** (`EVs: 32 HP / 32 Atk / 2 Spe` means SP). Serialize at the boundary only.
- **Throughput is not a constraint** (~35× the 1/s/worker threshold). Set Showdown `simulator: 4`.

**Changes from the original plan (from Phase 1):**

- **Legality comes from the pinned Showdown, not the dataset.** `pokemon-champions-data` matches Showdown
  exactly on learnsets and base stats but is **missing 26 legal items** at this pin (Life Orb, Rocky Helmet,
  Expert Belt, Light Clay, Eject Button, terrain seeds…) because its M-B/M-C deltas never re-add items that
  became legal after M-A. `vgc regulation export` now writes `data/regulations/<id>/dex.json` from Showdown's
  own validator (`checkCanLearn`, rule table). The dataset remains a cross-check (`tests/test_regulation.py`
  asserts the known drift so we notice an upstream fix) and the source for the type chart and mechanics docs.
- **No damage-calc fork needed.** Upstream `@smogon/calc` **0.12.0** (2026-09-18) has native Champions
  mechanics (`gen 0`) and every M-C Mega. It's pinned exactly via npm in `sidecar/calc/`, not as a
  submodule. VGCHelper's fork is not used.
- **The calc is verified against the simulator, not a website.** ChampDex and the official calc run the same
  library, so matching them proves nothing. `tests/test_calc_vs_sim.py` instead plays scripted turns in the
  pinned Showdown over 300 seeds and requires every non-crit hit to be one of the calc's 16 rolls, covering the
  whole range. The scenarios are single-target, Grassy Terrain priority, Mega Salamence Aerilate spread, and
  spread super-effective Earthquake. All match exactly.
- **Items that matter:** Choice Band/Specs and Assault Vest are **illegal** in M-C (so the plan's
  `Rillaboom @ Choice Band` example was illegal). The calc **silently ignores** illegal items, so `vgc calc`
  warns about them.
- **Engine gotcha for Phase 2:** a raw `Battle` does not apply `Adjust Level = 50` (the validator does). Any
  harness that skips the server's validator must set level 50 itself.

**Changes from the original plan (from Phase 2):**

- **philmantatsky's bot was not vendored.** It's 43k lines built on a *forked* poke-env, a *forked* Showdown,
  torch 2.12 (no Intel-Mac wheel), sb3 and open_spiel, so it's incompatible with the Phase 0 environment. Upstream
  has also already done the M-C port itself (2026-09-09). We used it as a design reference and built our own on
  stock poke-env. Its idea of a 625-team pool from team-sheet replays is reproduced with our own extractor.
- **Battles run server-free, not through the websocket server.** `sidecar/showdown/battle-runner.js` drives
  Showdown's `Battle` directly with an explicit seed. Teams still go through `TeamValidator`, which applies level 50
  and rejects illegal teams. Each side's secret-split protocol stream is fed into a poke-env `DoubleBattle`. The
  outcome is a pure function of (seed, teams, policies), so runs are identical at any worker count, and a battle
  replays exactly from its `inputLog` plus its OTS flag. The server remains for poke-env-over-websocket use.
- **poke-env's move data is mainline Gen 9, not Champions.** 15 base powers (e.g. Meteor Assault 150→170), 3
  accuracies (Make It Rain 100→95%) and 1 target (Milk Drink `self`→`adjacentAllyOrSelf`) differ. The last one
  produced invalid choices. The policy layer now reads move facts from the regulation export and targets from
  Showdown's request (`legal_orders`). Damage always came from the Champions calc.
- **Hidden trapping is a legitimate retry, not an error.** Shadow Tag and Arena Trap are only revealed when a
  switch is rejected, so those are counted separately (`trapped_retries`).
- **The team pool exists now, not in Phase 3.** `vgc meta scrape` + `vgc meta pool`: 400 Bo3 replays gave 800
  team sheets and **263 distinct legal teams** (`data/teams/reg_mc/ots_pool_2026-09-19.json`, with imputed SP
  spreads because sheets hide them). Our validator accepted **800/800** real sheets, and Showdown agreed on a 60-team
  sample. That settles Phase 1's deferred "accept a real team" check.

## Context

You want a trained model that gives VGC team-building recommendations, fed by a local Pokémon
Showdown environment running self-play, and callable by a Claude agent. The four target questions:

1. Given 6 Pokémon, what are my weaknesses?
2. Given 4 Pokémon, which 2 complete the team?
3. What moves should each Pokémon run?
4. Given a team matchup: what 4 do I bring, what do I lead, what do I click each turn?
5. _(added 2026-09-19)_ What is the win probability of this battle state, from team preview onward? Asked
   either as a spectator (neither side's 4 known until revealed) or as a player (my side fully known, the
   opponent revealed over time). And what is the **expected** win probability of each turn action I'm
   considering?
6. _(added 2026-09-19)_ All of the above from a **web app** during a real game. Paste my teams, pick one, enter
   what the opponent reveals turn by turn, and get recommendations and probabilities as the battle goes.

You have a `vgc-advisor`
plugin whose three skills reason from web search and public datasets, with no simulator and no model
behind them. The intended outcome is a real stack: a pinned Champions simulator as ground truth, a
battle policy good enough that simulated outcomes mean something, and a team-building search layer
on top — all versioned by regulation so a rotation to Reg M-D is a config change plus a retrain,
not a rewrite.

**Decisions made:** vendor and port the MIT prior art; build the battle layer first; CLI as the real
API with a thin MCP wrapper; this Intel Mac for simulation and inference, rented GPU for training.

---

## What the research changed

Five findings from live research that materially reshape the approach. These are the reason the plan
looks the way it does.

**1. The pretrained checkpoints do not fit this format.** VGC-Bench's HuggingFace checkpoints
(`cameronangliss/vgc-bench-models`) are trained on **Regulation G and F** — Scarlet/Violet Gen 9 VGC.
Its 107-action space is `6 switches + 4 moves × 5 targets + terastallization variants`. Champions has
**no Terastallization**; Mega Evolution is the gimmick, and the dex differs. So the checkpoints are
not loadable as-is — the action space itself is wrong. We re-run behavior cloning on Reg M-C replays.

**2. But someone already did the Champions port.** `philmantatsky/VGC-Pokemon-Showdown-AI` (MIT)
extends VGC-Bench to **`[Gen 9] Champions VGC 2026 Reg M-B`** — one regulation behind our target —
with a PPO policy, an exact-simulation search stack, and a BC model from ~14k top-player games. It
peaked at 1365 Elo on ladder and explicitly "runs on a laptop, not a server farm." M-B → M-C is a
36-Pokémon / 6-Mega delta. **This, not VGC-Bench itself, is the right fork base.**

**3. Champions replaced EVs/IVs with Stat Points.** IVs are gone (fixed at 31). Each Pokémon gets
**66 SP**, max **32 per stat**, 1 SP = +1 to that stat at level 50. EV conversion: first SP in a
stat costs 4 EVs, each additional costs 8 (so 252 EVs = 32 SP). The advisor plugin gave EV-spread
guidance for this format until it was corrected in v0.3.0 — **the codebase must model SP natively,
never EVs with a conversion bolted on.** This also _helps_ us: SP spreads are a small discrete space
(compositions of 66 into 6 parts, each ≤32), so spread optimization becomes tractable search rather
than a 500-dimensional guess.

**4. The literature says do not train one big model over many teams.** VGC-Bench's headline results:
behavior cloning fine-tuned with self-play (BCSP) beat every pure-RL variant; and performance
_collapses_ as team count grows — the 64-team agent scored 0.36 against the 1-team agent even on
in-distribution matchups. The authors conclude the complexity "simply can't fit into the deep neural
network" and recommend **search-based approaches**. They also state team building is
**"left as an open challenge."** Training cost was 8× A40 GPUs for ~5M timesteps.

**5. A maintained, regulation-versioned Champions dataset exists.** `vbbjandrade/pokemon-champions-data`
(CC BY 4.0) ships roster/moves/abilities/items/learnsets as JSON with per-regulation delta files
(`data/regm-*/`), JSON schemas, mechanics docs (`sp-system.md`, `stat-formula.md`), and — critically —
**pinned Showdown commit SHAs per regulation**. This is our regulation layer; it solves the
extensibility requirement directly.

### The consequence

Don't train one model to answer all four questions. Build **a simulator you trust, a policy cheap
enough to run millions of turns, and search on top** — and reserve learning for the two places it
genuinely pays: cloning human play, and a surrogate that prunes team-building search.

---

## Hardware reality

|                 |                                                                                                  |
| --------------- | ------------------------------------------------------------------------------------------------ |
| CPU             | Intel i7-9750H, 6c/12t @ 2.6GHz                                                                  |
| RAM             | 16 GB                                                                                            |
| Disk            | **19 GB free of 233 GB (92% used)** — real constraint                                            |
| GPU             | None usable. No CUDA; no MPS (Intel Macs are unsupported)                                        |
| PyTorch ceiling | **2.2.2** — the last macOS x86_64 wheel. `torch>=2.3` has no Intel Mac build                     |
| Python          | System 3.9.6 is too old (poke-env needs ≥3.10). **pyenv 3.12.14 is installed** → poke-env 0.16.1 |
| Node            | v24.20.0 x64 — fine for Showdown                                                                 |

**Training/inference split.** The torch 2.2.2 ceiling would otherwise poison everything. Sidestep it:
train on the rented GPU with modern torch, then **export to ONNX and run inference on the Mac with
`onnxruntime`**, which publishes x86_64 macOS wheels. Never move `stable-baselines3` `.zip` pickles
across a torch major-version gap — they will break. Keep a plain `state_dict` + a standalone model
definition as the portable fallback.

---

## Cost

**Target: this plan runs at $0–5 total.** The convenient part is that the one genuinely expensive
thing — PPO self-play — is also the thing the research says works _worst_ at small scale. The budget
constraint and the technical recommendation point in the same direction, so you are not trading
quality for thrift by capping spend here.

### What costs nothing

Phases 0–4 are **entirely free**. Local Showdown, poke-env, the damage calculator, the self-play
harness, the matchup matrix, the team-building search and the MCP server all run on your Mac's CPU.
That includes every one of your four questions in a first working form. Data is free too: replays,
Pikalytics, the Champions dataset, the MIT repos and the HuggingFace checkpoints are all public
downloads. Disk and bandwidth are the only cost, and they're your own.

### The one line item: model training (Phases 4–6)

BC is a small supervised model over a few hundred thousand state-action pairs. On a rented RTX 4090
that's **roughly 10–30 minutes per run**. The WP set encoder (Phases 4–5) is the same scale: a small supervised model
over decision-point snapshots, trained the same way. It adds runs, not a new cost tier, and fits the same free
Kaggle quota.

| Option                    | Price                                 | Realistic total for BC |
| ------------------------- | ------------------------------------- | ---------------------- |
| **Kaggle Notebooks**      | **Free** — 30 GPU-hrs/week (P100/T4)  | **$0**                 |
| Google Colab free         | Free, ~15–30 hrs/week, not guaranteed | $0                     |
| Vast.ai spot RTX 4090     | ~$0.11/hr                             | well under $1          |
| RunPod Community RTX 4090 | ~$0.34/hr, billed per second          | ~$0.10–0.20/run        |
| RunPod Secure / A100      | $0.69–1.19/hr                         | unnecessary here       |

Budget **10–15 runs** for debugging and hyperparameter fiddling. Even at RunPod Community rates
that's **$2–5**; on Kaggle it's **$0**. A T4 or P100 is entirely adequate for a model this size —
paying for an A100 would buy you nothing.

### What would actually blow the budget

**PPO self-play fine-tuning.** The paper used 8× A40s for ~5M timesteps, which is hundreds of
GPU-hours — **$100+** at any price. Worse, it's a bad _purchase_: PPO here is bottlenecked on
**Showdown simulation on the CPU**, not on GPU math, so a rented GPU sits idle while the simulator
grinds. This is marked optional-and-last in the plan for technical reasons; the cost just confirms
it. **Skip it.**

If simulation throughput turns out to be the real constraint (Phase 0 will tell you), the useful
rental is **CPU, not GPU** — a 32-core box for a few hours at ~$0.20–0.50/hr, i.e. **$1–3**, to
generate a matchup matrix overnight. For this project CPU-hours are worth more than GPU-hours, which
is the opposite of most ML work and worth keeping in mind before you reflexively rent a GPU.

### Hard spend controls

1. **Prepay and don't attach a card for auto-refill.** RunPod and Vast.ai both work off a prepaid
   balance — put **$5** in and you cannot exceed it. This is the real answer to "how do I cap spend":
   make overspending structurally impossible rather than relying on discipline.
2. **Do all development locally first.** Get the training script running end-to-end on the Mac over
   ~1000 samples. Rent only once it's known-good. Debugging on rented hardware is how small bills
   become large ones.
3. **Never leave a pod idle.** This is the single most common cause of surprise charges — an
   instance left running overnight at $0.34/hr is ~$8 for nothing. End the script with an explicit
   shutdown, and check the dashboard after every session.
4. **Prefer per-second billing** (RunPod) over hourly minimums, and spot/community over secure tiers.
5. **Try Kaggle first.** 30 free GPU-hours a week is more than this project's entire training need.
   Only move to paid if a specific limitation actually blocks you.

**Bottom line:** plan on **$0** using Kaggle, or **$5 prepaid** on RunPod if you'd rather not deal
with notebook constraints. Nothing in Phases 0–4 requires spending anything, so you can defer the
decision until you've already got working answers to all four of your questions.

---

## Architecture

Six layers, each usable alone, each swappable per regulation.

```
L4  Team building      weakness report · slot completion · moveset & SP search
L3  Team evaluation    matchup matrix vs meta gauntlet · racing allocator
WP  Win probability    WP(observation) per perspective · belief over hidden sets · EWP(action)
L2  Battle policy      heuristic → behavior clone → search (expectiminimax, EWP at the leaves)
L1  Engine             pinned Showdown (Champions) · @smogon/calc fork · poke-env 0.16.1
L0  Regulation config  legal pool · clauses · mechanics flags · SP rules · format id
```

### L0 — Regulation config (the extensibility spine)

Everything reads from one object. Rotating regulations = new config + regenerated pool + retrain.

`configs/regulations/reg_mc.yaml`:

```yaml
id: reg_mc
showdown_format: gen9championsvgc2026regmc # bo3 variant: ...regmcbo3
window: [2026-09-09, 2026-12-02]
platform: champions
battle: { style: doubles, team_size: 6, bring: 4, level: 50 }
clauses: { species: true, item: true } # item clause: no two share a held item
mechanics: { mega: true, tera: false, dynamax: false, z_moves: false }
stats: { system: sp, budget: 66, per_stat_cap: 32, ivs: fixed_31 }
data_source:
  {
    repo: vbbjandrade/pokemon-champions-data,
    tag: regm-c,
    dataset_sha: bc6d0a8c8498d7f8817857dec6625904a201dac9,
    showdown_sha: 2ddfa0476f8207e12e204b1c69f7c7683b17633c, # pinned in Phase 0
  }
```

A `Regulation` dataclass loads this; **no species list, mechanic flag, or stat rule is hardcoded
anywhere else in the codebase.** Add a `reg_md.yaml` later and the whole stack follows.

### L1 — Engine

- `vendor/pokemon-showdown` as a git submodule, pinned to `2ddfa04` (the dataset records no M-C SHA;
  see Phase 0). Run `node pokemon-showdown start --no-security` on localhost.
- ~~`vendor/damage-calc` — Champions-capable `@smogon/calc` fork.~~ **Superseded:** upstream `@smogon/calc@0.12.0`
  supports Champions natively; pinned via npm in `sidecar/calc/` (Phase 1).
- `poke-env==0.16.1` (0.15.0 added Champions data; `DoublesEnv` + VGC teampreview already exist).
- A thin Node sidecar exposing the calculator over stdio JSON, so Python calls damage calcs without
  a per-call process spawn.

> **Licensing note:** VGCHelper (`ychen022/VGCHelper`, 28 MCP tools, very close to what you want)
> **states no license.** Read it for design ideas; do not vendor its code. VGC-Bench, philmantatsky,
> Showdown and `@smogon/calc` are MIT; `pokemon-champions-data` is CC BY 4.0 (attribute it).

### L2 — Battle policy (question 4)

Three tiers, built in order. Each is independently useful, and tier 1 alone unblocks L3/L4.

1. **Heuristic baseline** — poke-env's `SimpleHeuristicsPlayer` adapted to doubles + Mega, plus
   damage-calc-aware target selection. Fast, deterministic, no training. **This is the workhorse for
   bulk simulation**, and the honest floor to measure everything else against.
2. **Behavior clone** — scrape Reg M-C replays (`replay.pokemonshowdown.com`, format
   `gen9championsvgc2026regmc`; filter `--min_rating`, open team sheets), convert to state-action
   trajectories, train a small policy. Supervised, cheap, and the paper's best-performing base.
   Train on rented GPU, export ONNX, infer on the Mac.
3. **Search** — shallow expectiminimax over the real simulator for decisive turns, with the BC policy
   as move-ordering prior and a calibrated win-probability head at the leaves. This is
   philmantatsky's architecture and what the VGC-Bench authors recommend as the way past the RL wall.
   Budget the search by wall-clock (they used 8s/turn with a 10s hard limit).

PPO fine-tuning on top of BC is **optional and last** — only if the cloud GPU budget justifies it,
and never as the starting point.

### WP — Win probability and expected win probability (question 5)

_Added 2026-09-19._ One model family that answers "who is winning, and by how much?" at every decision
point from team preview to the last turn. It also answers "which action raises my chances most?"
Search needs exactly this: the plan's "calibrated value head at the leaves" **is** this model, so it's
built as its own layer, before search, and both come before team evaluation.

**What it outputs.** `WP(o) = P(p1 wins | o)`, where `o` is what a given **perspective** can observe at a
**decision point**: team preview, each turn's choice, and each forced switch.

| Perspective | Knows | Doesn't know |
| --- | --- | --- |
| `spectator` | both previews (6 species each); under OTS both sheets; everything publicly revealed | which 4 each side brought until each one appears; exact HP (only %) |
| `player` | own brought 4 and exact own state; opponent preview; under OTS their sheet | which 4 they brought until revealed; their exact HP; (v2) their sets |

Player-perspective inputs are side-canonicalised ("me vs them"), and the output is always the WP of a named side.

**Versions, by information regime.**

- **v1 — Open Team Sheets.** Both sheets are known: species, items, abilities, moves, natures. Stat Points are never
  shown, even under OTS, so the model sees a spread *prior* rather than a spread. The hidden information is which 4
  were brought and what hasn't been revealed yet. Unrevealed Pokémon enter as "maybe brought" tokens, and an
  auxiliary **bring head** predicts which 4 were brought. At team preview this gives a directly useful output:
  **WP for each of my 15 possible brings (× leads) against the opponent's predicted bring distribution.** That's
  question 4's "what do I bring" with a number attached.
- **v2 — closed sheets, player mode (the Champions ladder).** Only the opponent's species are known at preview.
  Items, abilities, moves and natures appear as they're used. Uncertainty over each opponent member's set is
  explicit. There's a **set prior** from usage (our replay corpus plus Pikalytics:
  `P(item, ability, moves, nature | species)`). It's updated into a **belief** by hard reveals, and by damage
  observations: a calc-based likelihood that narrows item, nature and bulk. The belief is represented as K complete-set
  particles per Pokémon, and `WP_v2(o) = E_belief[WP_v1(o completed by particle)]`, which reuses v1 as the inner
  model. A directly trained v2 model is the comparison baseline.

**Expected win probability of actions (EWP).** For my candidate joint actions `a` and the opponent's `b`:
`EWP(a) = Σ_b π_opp(b | o) · E_rng[ WP(o′ | a, b) ]`. Transitions are **exact**, computed by the simulator
from a serialized Showdown state (`Battle.toJSON` / `fromJSON`, clone and step). There are K RNG seeds per
(a, b) pair for rolls, crits, accuracy and secondaries, shared across actions as common random numbers. In live
play the hidden information is *determinized*: sample the unrevealed bench and (v2) sets from the belief, then
simulate each sample. `π_opp` is the heuristic now and BC later. The output is a per-action table: EWP, a 90%
interval, the worst-case (min over `b`) value, and the opponent replies that matter. Both sides are pruned to top-k
candidates by the policy prior (k≈8, so 64 pairs × K seeds). This must fit the 45 s turn clock, measured, not assumed.
One-ply EWP maximization is the first search. Deeper expectiminimax uses WP at the leaves.

**Model.** v0 is gradient-boosted trees on hand features (remaining Pokémon, HP sums, speed control, field). It's
the baseline, and cheap to keep honest. v1 is a permutation-invariant **set encoder**: one token per Pokémon
(species, types, base stats, known item/ability/moves with *unknown* masks, HP%, status, boosts, Mega state,
slot: active/bench/unrevealed/fainted/not-brought) plus field tokens (weather, terrain, Trick Room, Tailwind turns,
screens, turn number). It has a WP head and a bring head, is trained on Kaggle/rented GPU and exported to ONNX,
the same path as BC.

**Data.** Every self-play battle is replayable from its `inputLog` (Phase 2). Re-simulation yields the spectator
channel and each player's channel, so decision-point snapshots for *every* perspective come free, with exact
labels for which 4 were brought. Human replays anchor the model to real play: Bo3 open-team-sheet games for v1,
Bo1 closed-sheet games for v2. Public replays are spectator logs, so player-perspective human data is only
reconstructable where all 4 of a side appear. **WP is policy-dependent:** WP under heuristic-vs-heuristic play is
not WP under human play. Train on both, report calibration separately, and treat human-replay calibration as the
number that matters. Split by battle, and for the generalization test by team.

**Tracking.** Every WP model is versioned under `models/wp/<regulation>/<version>/` with a model card: regulation id,
perspectives and info regime supported, a training-data manifest (self-play run ids and their policies, replay
snapshot dates), git SHA, and metrics per perspective and per turn bucket. `models/registry.json` lists all versions
so any two can be compared. Once a WP model exists, self-play records and `vgc wp replay` also carry a per-turn
**WP trajectory**, the curve of the battle, so swings can be attributed to specific turns.

### L3 — Team evaluation

The bridge from "a policy" to "team-building advice".

- **Meta gauntlet**: 20–40 representative Reg M-C teams from Pikalytics usage, MetaVGC rentals,
  `crob.at`, Limitless and RK9 lists, each weighted by real usage. Versioned as a dated snapshot file
  so results are reproducible and re-runnable when the meta shifts.
- **Matchup matrix**: `winrate(team_A, team_B)` under a fixed policy, over N battles.
- **Racing allocator** — the key trick for a laptop. Naively, separating a 55% win rate from 50% at
  p<0.05 needs ~385 battles _per cell_; a 30×30 matrix would be ~350k battles. Instead: screen every
  pair at n≈20, then use successive halving to spend the remaining budget only on cells that are
  still close or still matter. Pair matchups with **common random numbers** (same RNG seeds for both
  orderings, alternate who's player 1) to cancel variance. This buys 5–10× effective throughput.

### L4 — Team building (questions 1–3)

**Q1 — Weaknesses.** Two tiers, and the analytic one needs no model at all:

- _Analytic_: type-coverage matrix, speed tiers under Tailwind/Trick Room, and real damage calcs
  against the top-30 meta threats — "nothing on your team OHKOs X", "Y OHKOs three of your six",
  "you lose to Trick Room", "no answer to redirection". Deterministic and explainable.
- _Empirical_: your row of the matchup matrix — which gauntlet teams actually beat you, and the
  turn-level loss attribution from those replays.

**Q2 — Completing a 4 → 6.** Candidate pool = legal species filtered by usage, scored by
`Δ expected win rate vs the usage-weighted gauntlet`. Full simulation of every candidate is
impossible, so: a **surrogate regressor** (gradient-boosted trees or a small MLP over team
embeddings, trained on the matchup-matrix outcomes) ranks hundreds of candidates in milliseconds,
then **the top ~15 are verified by real simulation**. Surrogate for breadth, simulator for truth —
never ship a surrogate number as an answer.

**Q3 — Movesets and SP spreads.** Same pattern at finer grain: legal movepool from the regulation
data, marginal contribution measured by simulated ablation. SP spreads are a small enough space that
breakpoint search (survive X's best move / outspeed Y at +0) plus local search beats learning.

### L5 — Interface

`vgc` CLI is the real API; MCP server is a thin wrapper over the same functions, so everything is
testable without an agent in the loop.

```
vgc sim battle --team-a a.txt --team-b b.txt --n 100 --policy heuristic
vgc team analyze --team team.txt          # Q1
vgc team complete --team four.txt --top 10 # Q2
vgc team moves --team team.txt --pokemon rillaboom  # Q3
vgc battle advise --state state.json      # Q4 + Q5: joint actions ranked by EWP
vgc wp preview --team-a a.txt --team-b b.txt   # Q5: WP per bring/lead choice at team preview
vgc wp replay <replay-id|log> --perspective spectator|p1|p2   # Q5: WP trajectory by turn
vgc wp eval --model <version>              # calibration report → model card / registry
vgc calc --attacker ... --defender ...
vgc meta refresh --regulation reg_mc
```

Then rewrite the three `vgc-advisor` skills to call these instead of web-searching — keeping their
retrieval discipline for genuinely live questions (bans, errata, this week's usage), but replacing
every "hand off to damage-calc skill" with a real calculation, and every roster/learnset fetch with
a local lookup once the data layer is in place.

### L5b — Battle-companion web app (question 6)

_Added 2026-09-19._ A local web app for use *during* a game. Champions is played in the game itself, not on
Showdown, so there's no log to read: you tell it what happened, and it answers with recommendations and
probabilities from the same functions the CLI uses. `vgc web` serves it on `localhost`. There's an opt-in LAN mode
with an access token, so a phone next to the Switch can drive it while the Mac does the work. It stays local:
the simulator, calc sidecar and ONNX models all live on the Mac, and nothing is hosted.

**Team library.** Paste a team in Showdown/PokéPaste text, or a `pokepast.es` link that it fetches and parses.
Every team is validated against the active regulation on save (SP budget, clauses, pool, no Tera), with stats and
Mega formes shown. Teams are stored locally with a name and notes and can be edited, duplicated or archived. You
pick one as the **active team** for a battle.

**Battle session**, one per game, with player perspective and your side fully known:

1. **Team preview.** Enter the opponent's 6 species (search-as-you-type over the legal pool), or paste their sheet
   if it's an open-team-sheet game (Bo3). Output: WP for each of your 15 bring choices and leads against their
   predicted bring, with the recommended 4 and 2 leads highlighted. It also shows which of their Pokémon threaten
   yours (calc-backed).
2. **Each turn.** A fast, mostly-click input for what you saw. Which opponent Pokémon are on the field (revealing
   their brought 4 as they appear), HP % (slider or the in-game bar), status, stat stages, items, abilities and
   moves as they're revealed, Mega Evolution, weather, terrain, Trick Room, Tailwind, screens, and faints. Your own
   side is pre-filled from the team and needs only HP, status and boosts corrected when the app can't infer them.
   Output: current WP, and your joint actions ranked by **expected WP** with intervals and the worst-case reply.
   Every recommended move has a calc panel (rolls, KO chance) and shows the opponent replies that matter.
3. **Belief panel** (from v2). For each opponent Pokémon, the likely item, ability, spread and moves, and how that
   changed with each reveal or damage observation. You can correct it by hand ("it's Choice Scarf").
4. **Timeline.** A WP curve by turn, undo and edit any earlier turn (everything downstream recomputes), and a
   post-game review that flags the turns where the chosen action's EWP was well below the best.

**Architecture.** A FastAPI backend calls `vgc.*` directly: validation, calc, WP, EWP, belief. A light frontend
(server-rendered with htmx, or Preact without a build step) is mobile-first for one-thumb use. The API is
documented, so the MCP server and the app share one surface. The hard part is **state reconstruction**: turning
manual input into a Showdown `Battle` state that the EWP evaluator can clone and step. That needs its own parity
tests (a reconstructed state from a self-play log's public information vs the true state: same legal actions, same
damage rolls, EWP within tolerance). The app only shows EWP for states that pass reconstruction checks, and it says
so when one doesn't.

**Sessions are data.** Every game you log is saved as a player-perspective record (inputs, recommendations shown,
what you actually clicked, the result). Those are exactly the real player-perspective human games that public
replays can't provide, so with your opt-in they feed WP calibration and a personal review of your decisions.

---

## Phases

Each phase ends in something runnable. Do not start a phase before its predecessor's verification
passes.

### Phase 0 — Environment spike (~half a day). _Do this before committing to anything above._ ✅ Done

> **Completed 2026-09-19** → [docs/phase0-findings.md](docs/phase0-findings.md).
> 1 ✅ split into `.venv` / `.venv-train` (numpy conflict), sb3 dropped · 2 ✅ ONNX round trip, 6e-8 max diff ·
> 3 ✅ Showdown `2ddfa04`, format id confirmed, SP validated (Tera is **not** rejected by Showdown) ·
> 4 ✅ 11.4/s at 1 worker, 36.5/s at 8 workers with `simulator: 4` · 5 ✅ ~1.4 GB project; 14 GB free.

Purpose is to kill the three risks that would invalidate the plan, cheaply.

1. pyenv 3.12.14 venv; resolve the `poke-env 0.16.1` + `torch 2.2.2` + `stable-baselines3` pin set.
   **sb3 2.9 likely requires torch ≥2.3 — expect to pin an older sb3, or drop sb3 entirely** and use
   plain torch for BC (preferable regardless).
2. Confirm `onnxruntime` has a macOS x86_64 wheel and round-trips a toy model.
3. Clone Showdown at the Reg M-C SHA, `npm i`, start with `--no-security`, confirm the format id
   `gen9championsvgc2026regmc` exists and validates a 6-Pokémon Champions team with SP syntax.
4. **Benchmark throughput**: two random doubles players, 200 battles, 1 / 4 / 8 parallel workers.
   Record battles/sec. Every simulation budget below is a guess until this number exists.
5. **Measure disk**: Showdown + node_modules + torch + a replay sample. 19 GB is the ceiling.

_Verification:_ a `docs/phase0-findings.md` with the throughput number, the working pin set, and
disk footprint. If throughput is under ~1 battle/sec/worker, revisit L3's budget before proceeding.

### Phase 1 — Foundation

Rewrite `pyproject.toml` (name `vgc`, `requires-python = ">=3.10"`, real dependencies — keeping the
existing src-layout and `pythonpath`/`testpaths` pytest config) and replace `README.md`.

Type effectiveness comes from the dataset's `data/mechanics/effectiveness.json`, not a hand-written
chart — same "derive, don't quote" rule the advisor plugin now follows.

Layout:

```
src/vgc/  regulation/  engine/  policy/  teams/  building/  cli/  mcp/
configs/regulations/reg_mc.yaml
vendor/   pokemon-showdown/  damage-calc/  (submodules, pinned)
data/     champions-data/  replays/  gauntlet/  matrices/
```

L0 regulation loader + L1 engine wrapper + damage-calc sidecar. Team parsing/validation:
Species Clause, Item Clause, SP budget (66 / 32 cap), legality against the Reg M-C pool, Mega rules,
**and rejecting Tera**.

_Verification:_ `vgc calc` reproduces a known damage roll from ChampDex or Porygon Labs to the exact
16-roll range; `vgc team validate` correctly accepts a real rental team and rejects one with a
duplicate item, an illegal species, a 67-SP spread, and a Tera type.

> **✅ Completed 2026-09-19.** `pyproject.toml` (name `vgc`, py ≥3.10, no torch) and README rewritten. Built
> `src/vgc/{regulation,engine,teams,cli}` with `policy/building/mcp` stubs. `configs/regulations/reg_mc.yaml`
> plus a `reg_mb.yaml` stub prove the spine; both load and both have snapshots. Team model is SP-native, with
> Showdown text I/O (`EVs:` only at the boundary). The validator covers Species Clause (by dex number, so
> Indeedee/Indeedee-F collide), Item Clause, 66/32 SP, fixed IVs, pool/learnset/ability/item legality, Mega
> stone checks and **Tera rejection**. Also built: calc sidecar (`sidecar/calc`), Showdown server lifecycle
> (`vgc server start` writes `simulator: 4`), and CLI `vgc regulation | team validate [--showdown] | team stats | calc | server`.
> **Verification:** the calc matches the simulator exactly (see above, stronger than the ChampDex check).
> Validator parity with Showdown holds on all 6 fixtures; the only divergence is Tera, which we reject and
> Showdown ignores. The valid fixture is a hand-built team, not a published rental. **42 tests pass.**
> Deferred: validating against a real published rental team. **Resolved in Phase 2:** 800/800 real team sheets accepted.

### Phase 2 — Battle layer, tier 1 (question 4, first cut)

Fork `philmantatsky/VGC-Pokemon-Showdown-AI` into `vendor/`, strip to what's needed, port M-B → M-C
(regulation config, dex delta, Mega handling in the action encoder). Implement the heuristic player.
Parallel self-play harness with seeding and structured battle logs.

_Verification:_ heuristic beats random ≥85% over 500 battles; a full 100-battle self-play run
completes unattended with reproducible results from a fixed seed.

> **✅ Completed 2026-09-19** (deviations are listed under "Changes from the original plan (from Phase 2)").
> Built: the server-free seeded runner (`sidecar/showdown/battle-runner.js`, `vgc.engine.runner`) with an exact
> `replay` op. The damage-calc-aware `HeuristicPolicy` (`vgc.policy.heuristic`) batches ~30–60 real calcs per
> turn; it scores damage, KO chance, outspeed-to-KO, ally damage, Protect vs incoming threat, Fake Out, Tailwind/Trick
> Room, redirection, status and support. It picks the best joint order with an overkill correction, Megas
> immediately, and brings 4 at preview by calc'd matchup. Own spreads are recovered exactly from request stats;
> opponent spreads are guessed. Also built: the parallel harness (`vgc sim battle | selfplay`, gzipped JSONL
> with inputLog and the omniscient log, Wilson CIs, an outcome digest) and the replay scraper and team pool (`vgc meta`).
> **Verification, on real pool teams:** heuristic vs random **98.4%** over 500 battles (95% CI 96.9–99.2%), 0 errors,
> 0 invalid choices, 38.8 battles/s on 8 workers. 100 heuristic-vs-heuristic battles give the same digest on 8 and
> on 3 workers, with identical inputLogs (mirror win rate 43%, CI 34–53%). **52 tests pass.**
> Known limits for later tiers: no voluntary switching beyond hopeless positions; opponent spreads are a single
> guess; no prediction of opponent Protect or target choice.

> **Reordered 2026-09-19:** battle strength first, team building second. Win probability, expected WP and
> search (old Phases 5–7) now come *before* team evaluation and team building (old Phases 3–4). Team rankings
> only mean something under a policy that plays well, so the strongest battle stack is built and verified first,
> then used to judge and build teams. The old Phase 3's snapshot extraction is split out as the new Phase 3,
> because the battle phases need that data.

### Phase 3 — Battle data: snapshots and replay corpus

_Split out of the old Phase 3, 2026-09-19._ A **decision-point snapshot extractor**. It re-simulates battles from
`inputLog` (Phase 2 `replay` op), emits spectator/p1/p2 channels, and writes one featurized snapshot per perspective
per decision point, labelled with the winner and the true brought-4. A human-replay parser writes the same snapshot
format: Bo3 open-team-sheet games for v1, Bo1 closed-sheet games for v2 (`vgc meta scrape --format bo1`), with
rating kept for filtering. A standing self-play generator (heuristic vs heuristic across the team pool) produces the
first training set. **Frozen held-out sets** are fixed here, before any model sees data: held-out battles,
held-out teams and held-out human replays.

_Verification:_ snapshots re-derived from the same `inputLog` are byte-identical. For self-play, the player snapshot
matches what poke-env's `DoubleBattle` showed that player at the time (parity with the live run). No held-out battle
or team appears in any training manifest (checked by the manifest tool, not by eye).

### Phase 4 — Win probability v1: Open Team Sheets (question 5)

_New 2026-09-19._ Featurizer shared by all perspectives. v0 GBT baseline. Set-encoder WP model with a bring head,
trained on self-play snapshots plus Bo3 OTS human replays (Kaggle/rented GPU → ONNX). Uses the frozen held-out sets
from Phase 3. `vgc wp preview` (WP per bring/lead), `vgc wp replay` (trajectory), `vgc wp eval`. Model cards and
registry.

_Verification:_ on held-out human replays, per perspective: log loss and Brier beat both the 50% baseline and a
remaining-Pokémon/HP logistic baseline; **ECE < 0.03** with reliability diagrams per turn bucket. On the same states
the **player perspective scores lower log loss than the spectator** (more information must help; if it doesn't,
the hidden-information handling is broken). Preview WP agrees with a direct simulated win rate for a sample of
pairings (an independent estimate of the same quantity). The bring head beats a usage-frequency baseline at
predicting the brought 4.

### Phase 5 — Win probability v2: closed sheets, player mode

_New 2026-09-19._ Set prior from the replay corpus and usage data. Belief tracker with hard reveals plus a damage-roll
likelihood from the calc; particles per opponent Pokémon. `WP_v2 = E_belief[WP_v1]`, compared against a
directly trained v2.

_Verification:_ on held-out closed-sheet games, v2 log loss is between v1-with-oracle-sets (lower bound) and
v1-with-prior-only (upper bound), and moves toward the oracle as sets are revealed. Belief calibration: the true
item and nature land in the belief's 80% set ~80% of the time.

### Phase 6 — Expected WP, behavior cloning + search (questions 4 and 5, properly)

_Restructured 2026-09-19 around EWP._ **EWP action evaluator** first: serialized-state clone and step in the runner,
top-k pruning of both sides by the policy prior, K common-random-number seeds, determinization from the v1
unrevealed-bench prior or the v2 belief, and the per-action EWP table in `vgc battle advise`. Then train BC on the
Phase 3 human corpus (rented GPU → ONNX) as `π_opp` and move-ordering prior. Then deeper expectiminimax with WP at
the leaves. Finally, **refit WP on self-play from the stronger policy** and re-check calibration, because WP learned
from heuristic play describes heuristic play.

_Verification:_ the EWP-greedy policy (heuristic prior) beats the heuristic ≥60% over 500 battles; BC beats the
heuristic ≥60%; search with the BC prior beats BC ≥60%; turn latency stays under the 45 s VGC clock with margin.
EWP sanity: an action's EWP matches the realized win rate of that action in held-out self-play within its interval.
Also measure each policy's **battles/s**, because Phase 7 has to pick one it can afford to run at matrix scale.

### Phase 7 — Team evaluation (unlocks 1–3)

_Was Phase 3; now runs on the Phase 6 battle stack._ Build the dated meta gauntlet from the team pool plus
usage-weighted external lists. Matchup matrix generator with the racing allocator and common random numbers.
Team-embedding feature extractor. The matrix policy is the **strongest policy affordable at matrix scale**: full
search is too slow for ~100k battles, so expect BC or EWP-greedy with small K, chosen from Phase 6's strength and
throughput numbers. Preview WP (Phase 4) gives a free first pass on every cell. The racing allocator spends
simulation only where WP is uncertain or the cell matters.

_Verification:_ **calibration gate**. Matrix rankings correlate positively with real-world results, and
top-usage/tournament-winning gauntlet teams should land in the upper half. **Policy-artifact check, built in:**
compute a subset of cells under both the heuristic and the matrix policy. If rankings move a lot between them, the
weaker policy was misjudging teams, and that's the reason this phase now comes after the battle work. Do not build
L4 on a matrix that fails the gate.

### Phase 8 — Team building (questions 1–3)

_Was Phase 4._ Analytic weakness report first (no model, immediately useful). Then the surrogate regressor trained on
Phase 7 outcomes, with preview WP as a feature, slot-completion search with simulated verification of the top-k, then
moveset and SP optimization. All candidate verification uses the Phase 7 matrix policy.

_Verification:_ held-out test — remove one Pokémon from 10 known-strong Reg M-C teams and check
whether `vgc team complete` recovers the real member in its top 5. Weakness reports on those teams
should match published tournament-report commentary.

### Phase 9 — Interface

MCP server wrapping the CLI. Rewrite the three skills against it. Fix the SP/EV errors. Finish the
battle-companion web app (L5b).

_Verification:_ in a fresh Claude Code session, ask each of your questions and confirm every
numeric claim traces to a real tool call, not a web guess.

**Web app, staged.** Each stage ships as soon as the phase that powers it lands, so the app is usable
early and grows with the model. With battles first, the in-battle companion arrives before the team-building tools:

| Stage | After | Adds |
| --- | --- | --- |
| **W1** | Phase 3 | `vgc web`, team library (paste / pokepast.es, validation, stats), active team, calc panel, battle session input, heuristic move suggestions (no probabilities yet) |
| **W2** | Phase 4 | Team-preview bring/lead recommendations with WP; per-turn current WP; WP timeline |
| **W3** | Phase 5 | Belief panel for closed-sheet games; manual belief corrections |
| **W4** | Phase 6 | Expected-WP action table with intervals and worst-case replies (determinized over the belief); state-reconstruction parity checks; post-game review |
| **W5** | Phase 8 | Team tools in the library: weakness report, complete-my-team, moveset/SP suggestions |

_Web app verification:_ replay 20 held-out self-play battles through the app's input forms, entering only public
information from the player's perspective. The reconstructed states must pass parity (same legal actions and calc
rolls as the true state), and the app's WP and EWP must match `vgc wp` and `vgc battle advise` on the same inputs.
Entering a full turn takes under ~20 seconds on a phone (timed), well inside the in-game turn timer.
Recommendations come back within the turn clock with margin.

---

## Model-building practices worth holding to

You asked for the modelling insight, so — the ones that actually decide whether this works:

1. **Don't train what you can search.** At this compute scale, a shallow search over an exact
   simulator with a cheap prior beats a large learned policy. That's both the VGC-Bench authors'
   own conclusion and what the laptop-scale Reg M-B bot demonstrates.
2. **Behavior cloning before reinforcement learning, always.** BC is supervised, CPU-trainable,
   converges in hours not days, and every strong result in the paper is BC-initialized. Pure
   self-play from random is where laptop projects go to die.
3. **Freeze an evaluation set on day one** — held-out teams and held-out replays, never tuned on.
   The paper used 72 held-out teams for exactly this. Without it you cannot tell learning from
   memorization, and with a self-generated matchup matrix the temptation to overfit is severe.
4. **The simulator is ground truth; the learned model is a search-pruner.** Any recommendation that
   reaches the user should be confirmed by real simulation, not surrogate output.
5. **Calibrate the value head, don't just maximize accuracy.** For turn decisions you need
   _P(win) is actually 70% when it says 70%_ — argmax accuracy is the wrong target for a search leaf
   evaluator. Check with a reliability diagram and Brier score, not accuracy.
6. **Budget variance before compute.** Paired sampling with common random numbers, plus successive
   halving, is worth more than a 10× faster machine here. Always report confidence intervals on win
   rates; a 30-battle 55% result is noise.
7. **Watch for policy-induced artifacts.** A matchup matrix computed under one policy measures teams
   _under that policy_. Trick Room teams in particular tend to be undervalued by weak bots that
   misplay the setup turn. Phase 7 computes a subset of cells under both the heuristic and the strong policy
   and compares them; that's how you detect this.
   The same applies to the WP model: WP learned from heuristic self-play measures heuristic play, so
   report calibration on human replays separately.
8. **Tag every artifact with its regulation id.** Checkpoints, matrices, gauntlets, surrogates. When
   Reg M-D lands you want to know exactly what's stale.
9. **Log battles in a replayable format from day one.** Debugging a policy you can't replay is
   miserable, and the logs are training data later.

---

## Risks

| Risk                                                                                                       | Handling                                                                                                                        |
| ---------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Showdown's Reg M-C format differs from what poke-env 0.16.1 expects (Mega in the action space, SP parsing) | Phase 0 spike item 3. This is the likeliest thing to break; find out in hour one, not week three                                |
| Not enough Reg M-C replays yet — format opened 2026-09-09, ~10 days ago                                    | Bootstrap BC from Reg M-B replays (much larger corpus, same platform/mechanics) and fine-tune on M-C as it accumulates          |
| Simulation throughput too low for the matchup matrix                                                       | Racing allocator; shrink the gauntlet; a damage-calc-only fast approximate turn model for the surrogate's inner loop only       |
| Policy too weak → meaningless team rankings                                                                | Phase 7 calibration gate is a hard stop                                                                                         |
| WP model learns the bot, not the game (self-play labels reflect heuristic play)                             | Human-replay calibration is the reported number; player-vs-spectator consistency check; re-fit after Phase 6                    |
| 19 GB free disk                                                                                            | Compress replay corpus; keep one checkpoint per regulation; don't vendor all of VGC-Bench                                       |
| torch 2.2.2 ceiling breaks a dependency                                                                    | Plain-torch BC instead of sb3; ONNX for inference                                                                               |
| Reg M-C rotates 2026-12-02 (~10 weeks out)                                                                 | L0 config spine is exactly the mitigation — validate it by adding a stub `reg_mb.yaml` early and confirming the stack runs both |

---

## End-to-end verification

When all phases land, this sequence should work from a cold start:

```bash
pyenv local 3.12.14 && python -m venv .venv && source .venv/bin/activate && pip install -e .[dev]
git submodule update --init --recursive && (cd vendor/pokemon-showdown && npm i)
vgc server start                                  # local Showdown, --no-security
vgc calc --attacker "Rillaboom @ Miracle Seed | Adamant Nature | EVs: 32 Atk" --move "Grassy Glide" --defender "..." --terrain Grassy
vgc team validate team.txt --regulation reg_mc    # SP budget, clauses, no Tera
vgc sim battle --team-a team.txt --team-b gauntlet/01.txt --n 50   # CI reported
vgc team analyze team.txt                         # Q1
vgc team complete four.txt --top 10               # Q2
vgc team moves team.txt --pokemon rillaboom       # Q3
vgc battle advise --state state.json              # Q4
pytest -q
```

Then in a fresh Claude Code session, with the MCP server registered, ask all four of your original
questions and confirm each answer's numbers came from tool calls.

---

## Sources

- [Cloud GPU pricing](https://getdeploying.com/gpus) · [Kaggle GPU quotas](https://www.kaggle.com/docs/efficient-gpu-usage) · [RunPod pricing](https://www.runpod.io/pricing)
- [VGC-Bench paper (arXiv 2506.10326)](https://arxiv.org/html/2506.10326.pdf) · [cameronangliss/vgc-bench](https://github.com/cameronangliss/vgc-bench) (MIT) · [checkpoints](https://huggingface.co/cameronangliss/vgc-bench-models)
- [philmantatsky/VGC-Pokemon-Showdown-AI](https://github.com/philmantatsky/VGC-Pokemon-Showdown-AI) (MIT) — Reg M-B Champions port
- [vbbjandrade/pokemon-champions-data](https://github.com/vbbjandrade/pokemon-champions-data) (CC BY 4.0) — regulation-versioned dataset
- [poke-env](https://github.com/hsahovic/poke-env) · [smogon/damage-calc](https://github.com/smogon/damage-calc) · [ychen022/VGCHelper](https://github.com/ychen022/VGCHelper) (unlicensed — reference only)
- [MetaVGC Reg M-C](https://metavgc.com/regulations/regulationm-c) · [Victory Road](https://victoryroad.pro/champions-regulations/) · [Pikalytics Reg M-C](https://www.pikalytics.com/pokedex/gen9championsvgc2026regmc) · [ChampDex Stat Points](https://champdex.com/guides/stat-points)
