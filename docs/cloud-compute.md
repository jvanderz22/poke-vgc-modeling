# Running on rented hardware

Everything here also runs on the laptop. Rent only to go faster, and only when the table below
says it's worth it. **Plan on $0 (Kaggle) or a $5 prepaid balance**, per PLAN.md's spend controls.

## Spending rules (from PLAN.md, restated because this is where they get broken)

1. **Prepay; never attach a card for auto-refill.** RunPod and Vast.ai run off a prepaid balance.
   Put **$5** in and overspending becomes impossible rather than a matter of discipline.
2. **Try Kaggle first.** 30 free GPU-hours a week is more than this project's entire training need.
3. **Debug locally, rent only known-good runs.** Every script here runs unchanged on the laptop
   first (`--device cpu`, small `--epochs`). Debugging on rented hardware is how small bills grow.
4. **Never leave an instance idle.** This, not compute, is the usual cause of a surprise bill. Every
   script takes `SHUTDOWN=1` to power the box off when it finishes, and `MAX_MINUTES` as a hard cap
   so a hung job can't bill overnight. Check the dashboard after every session anyway.
5. **Prefer per-second billing and spot/community tiers.**
6. **CPU-hours are worth more than GPU-hours here.** The simulator is the bottleneck in most phases.
   Renting a GPU when the job is simulation buys nothing.

Every script prints its elapsed time, and its cost when given `RATE` (dollars per hour).

## Is it worth renting?

| Job | Laptop | Rented | Worth it? |
| --- | --- | --- | --- |
| Self-play, 40k battles | 16 min (8 cores) | ~2 min (64 cores) | only as part of a bigger run |
| Self-play, 500k battles (Phase 7 matrix) | ~3.5 h | ~25 min | **yes** — the clearest CPU win |
| Snapshot extraction + featurize | ~13 min per 40k | scales with cores | comes free with the above |
| Train one WP model | ~45 min | 2–3 min | **yes when sweeping**, not for a single run |
| Tuning sweep (8 configs) | ~6 h | ~20 min | **yes** — this is the real gain |
| Evaluation, preview checks | minutes | same | no |

Rules of thumb: **rent CPU** for simulation (Phases 3, 6, 7), **rent GPU** for a sweep of training
runs (Phases 4–6), and **don't rent** for anything measured in minutes locally.

---

## A. GPU: training WP (and later BC) models

The trainer (`src/vgc/wp/set_torch.py`) imports only torch and numpy, reads one dataset directory,
and writes `model.onnx` + `train.json`. That's the whole contract, so the box needs nothing else.

```bash
# local: pack the dataset + trainer (~110 MB for wp-v1)
scripts/cloud/pack_training.sh reg_mc wp-v1          # → /tmp/wp-train-reg_mc-wp-v1.tgz
# upload, then on the box:
tar xzf wp-train-reg_mc-wp-v1.tgz
MAX_MINUTES=30 RATE=0.34 SHUTDOWN=1 bash scripts/cloud/run_training.sh reg_mc wp-v1 -- \
    --epochs 12 --d 128 --layers 3 --dropout 0.2 --id-dropout 0.3 --bs 2048
# download /tmp/wp-result-*.tgz, then locally:
tar xzf wp-result-cloud-20261202-1200.tgz            # lands in models/wp/reg_mc/<version>/
vgc wp card      --version <version>                 # writes the model card + registry entry
vgc wp calibrate --version <version>                 # per-context temperatures (train/val rows only)
vgc wp eval      --version <version> --baseline wp-v1-gbt --baseline constant
```

Notes:
- `--device auto` uses the GPU when present. On a GPU, raise `--bs` (2048–4096); the model is tiny
  and small batches leave it idle.
- **The ONNX export is always written from a CPU copy**, so a cloud-trained artifact is identical in
  form to a local one and runs the same way under onnxruntime.
- Cloud boxes have modern torch; the local `.venv-train` is pinned to 2.2.2 only because this Mac is
  Intel. Opset 17 is compatible either way. The card records the torch version and device used.
- A sweep is just several `run_training.sh` calls with different flags and `--out` names; run them
  sequentially on one box and compare **`val_wp_logloss_human`** in each `train.json` — not
  `val_wp_logloss`. Validation is ~89% self-play, and the two diverge: on Reg M-C the epoch that
  minimised overall validation loss scored 0.638 on human rows where the previous epoch scored
  0.581. Pass `--human-weight 4` so `set_torch` also selects its checkpoint that way.
- **No validation number decides a model.** The gates are measured locally on the frozen held-out
  human games, and the preview gate is not in the validation loss at all. Bring the top few
  candidates home and run `vgc wp eval` and `vgc wp check-preview` on each.

**Kaggle variant (free, and the one to try first):** use
[`scripts/cloud/kaggle_train_wp.ipynb`](../scripts/cloud/kaggle_train_wp.ipynb). Upload
`data/features/<reg>/<dataset>` and `src/vgc/wp/set_torch.py` as two private Datasets, set the
accelerator to GPU, and run it. Its sweep tests `--id-dropout-preview` against uniform
`--id-dropout` controls: identity dropout is what stops the encoder memorising repeated self-play
pairings, but team identity is the only input that exists at preview, so one uniform rate cannot
serve both (see PLAN.md Phase 4 status). It has a single-run cell and a sweep cell that ranks configurations
by validation log loss. Results download as a zip; import them with `vgc wp card`.

## B. CPU: generating self-play battles

The simulator is CPU-bound and single-threaded per battle, so this scales with core count.
Everything needed to reproduce the local setup is tracked in git: the regulation config, the
legality export, the team pool and the frozen split.

The repo has no git remote, so the working copy is **rsynced** from the laptop — no GitHub account
and nothing published. Run this from the laptop (the box needs git, node ≥18, python ≥3.10):

```bash
scripts/cloud/setup_worker.sh user@host          # rsync, Showdown build at the pinned SHA, venv, smoke test
ssh user@host 'cd vgc && MAX_MINUTES=90 RATE=0.50 SHUTDOWN=1 bash scripts/cloud/generate.sh 500000 3'
rsync -avz user@host:vgc/data/snapshots/ data/snapshots/
vgc data manifest --name wp-v2-train data/snapshots/reg_mc/selfplay/*/train.jsonl.gz ...
```

The rsync sends code, configs, the legality export, the team pool and the frozen split, and skips
venvs, replays, snapshots, features and models. Showdown is cloned on the box from its own upstream
at the SHA the laptop has checked out, so the pins match.

`setup_worker.sh` ends with a 200-battle heuristic-vs-random run. If that isn't a near-total win with
zero invalid choices, stop and fix it locally before paying for a long run.

**Seeds make this safe:** a battle's outcome is a pure function of (seed, teams, policies), so a
cloud-generated run is identical to one generated locally with the same seed, at any worker count.
Use a **different `--seed` from local runs** or you will regenerate the same battles. Run ids record
the seed; `summary.json` carries the outcome digest to confirm a rerun matches.

## What never leaves the laptop

- **Held-out data and the frozen split** are tracked, so a cloud box has them, but nothing on a cloud
  box decides what is held out. Splits are assigned by a salted hash of ids; manifests are re-checked
  locally before training.
- **Evaluation** runs locally, against the local frozen split. A model's card records where it was
  trained; its verdict comes from the same checks whatever produced it.
