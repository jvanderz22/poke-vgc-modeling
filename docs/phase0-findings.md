# Phase 0 — Environment spike findings

Date: 2026-09-19 · Machine: i7-9750H (6c/12t), 16 GB, macOS 15 (Darwin 24.6), Intel.

**Verdict: all three plan-killing risks cleared.** Throughput is ~35× the plan's
"revisit" threshold, the Reg M-C format exists and validates SP teams, and the
training/inference split works via ONNX. Two design changes fall out (see §1 and §3).

---

## 1. Python pin set

**Hard conflict: `poke-env 0.16.1` requires numpy ≥ 2; `torch 2.2.2` is compiled
against numpy 1.x.** With both installed, torch imports and runs forward passes, but
`torch.from_numpy()` and `tensor.numpy()` raise `RuntimeError: Numpy is not available`.
This isn't a pin we can resolve, so the environment is split in two:

| Env           | Purpose                                   | Pins                                                                                                   |
| ------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `.venv`       | Sim, search, inference, CLI, MCP (**main**) | Python 3.12.14 · `poke-env==0.16.1` · `numpy 2.5.3` · `onnxruntime 1.23.2` · `onnx 1.23.0` · `gymnasium 1.3.0` · `pettingzoo 1.27.0` · `websockets 16.1.1` · **no torch** |
| `.venv-train` | Local smoke-tests of the BC trainer only  | Python 3.12.14 · `torch==2.2.2` · `numpy 1.26.4` · `onnx` · **no poke-env**                            |

Consequences:

- **The BC trainer must not import poke-env.** Feature extraction (replay → state/action
  arrays) runs in `.venv` and writes `.npz`; the trainer reads `.npz` only. That boundary
  makes the trainer portable to Kaggle/RunPod unchanged anyway.
- **stable-baselines3 is dropped** (the plan already preferred plain torch).
- On the rented GPU, use modern torch; the Mac never needs torch at inference time.

## 2. ONNX round trip ✅

A toy MLP exported from torch 2.2.2 (`.venv-train`, opset 17, dynamic batch axis) loads
in `onnxruntime 1.23.2` (`.venv`, x86_64 macOS wheel). Max abs diff vs torch output:
**6e-8**. Dynamic batch sizes work.

## 3. Showdown + Reg M-C format ✅

- **Pin: `smogon/pokemon-showdown@2ddfa0476f8207e12e204b1c69f7c7683b17633c`**
  (2026-09-17, master HEAD on 2026-09-19). Vendored at `vendor/pokemon-showdown`.
- **The dataset does not pin a Showdown SHA for Reg M-C.** `pokemon-champions-data`
  fetches M-C and M-B from `master`; only M-A is pinned. We pin ourselves to the Showdown
  commit current at the dataset's fetch time (`fetchedAt: 2026-09-18T03:20:06Z` →
  `2ddfa04`, same as HEAD; last `data/mods/champions` change was `aa6d5f085`, 2026-09-13).
  Dataset pinned at `vbbjandrade/pokemon-champions-data@bc6d0a8` (2026-09-18).
  **When bumping either pin, bump both together.**
- Format name `[Gen 9 Champions] VGC 2026 Reg M-C` → id **`gen9championsvgc2026regmc`**
  (Bo3: `gen9championsvgc2026regmcbo3`), mod `champions`, ruleset
  `Flat Rules, VGC Timer, Open Team Sheets`.
- Build: `npm i && node build` — clean. Start: `node pokemon-showdown start --no-security`.
- **SP syntax:** Showdown stores Stat Points in the `evs` field, and team text keeps the
  `EVs:` label with SP values: `EVs: 32 HP / 32 Atk / 2 Spe`. The validator enforces
  66 total / 32 per stat for any `champions*` mod. Stat formula at L50 (from
  `data/mods/champions/scripts.ts`): `HP = base + SP + 75`, `other = floor((base + SP + 20) × nature)`.
  → Our code models SP natively and serializes to `EVs:` **only** at the Showdown boundary.

Validator results (`./pokemon-showdown validate-team gen9championsvgc2026regmc`), fixtures in
`tests/fixtures/teams/`:

| Fixture                   | Result                                                        |
| ------------------------- | ------------------------------------------------------------- |
| `valid_basic.txt`         | ✅ accepted                                                   |
| `bad_dup_item.txt`        | ✅ rejected — Item Clause                                     |
| `bad_67_sp.txt`           | ✅ rejected — 67 total Stat Points > 66                       |
| `bad_33_in_stat.txt`      | ✅ rejected — > 32 Stat Points in Attack                      |
| `bad_illegal_species.txt` | ✅ rejected — Flutter Mane does not exist                     |
| `bad_tera.txt`            | ⚠️ **accepted** — Showdown silently ignores `Tera Type:`      |

→ **Phase 1's validator must reject Tera itself**; Showdown won't. Also note Champions
learnsets differ from SV (e.g. Incineroar has no Knock Off) — always validate against the
regulation learnsets, never general knowledge.

## 4. Throughput ✅ (far above threshold)

`scripts/bench_throughput.py` — two poke-env `RandomPlayer`s, doubles, Reg M-C, the
`valid_basic` team mirror; each worker is its own OS process.

| Showdown `simulator` subprocesses | Workers | Battles | Wall  | Battles/s | Per worker |
| --------------------------------- | ------- | ------- | ----- | --------- | ---------- |
| 1 (default)                       | 1       | 200     | 17.1s | 11.7      | 11.7       |
| 1                                 | 4       | 200     | 10.3s | 19.4      | 4.9        |
| 1                                 | 8       | 200     | 10.5s | 19.1      | 2.4        |
| 4                                 | 1       | 400     | 35.0s | 11.4      | 11.4       |
| 4                                 | 4       | 400     | 13.6s | 29.3      | 7.3        |
| 4                                 | 8       | 400     | 11.0s | **36.5**  | 4.6        |

0 errors in 1,400 battles. Sanity sample (50 battles): mean 13.1 turns (5–39), Mega
Evolution occurred in 26/50, no ties.

Takeaways:

- **Set `simulator: 4` in `vendor/pokemon-showdown/config/config.js`**. With the default 1,
  throughput plateaus at ~19/s. (This file is gitignored by Showdown, so `vgc server start`
  must write it.)
- These are **random-policy** numbers, i.e. an upper bound. The heuristic player adds
  per-decision Python + damage-calc cost; re-benchmark in Phase 2. Even a 10× slowdown
  (~3.5/s) is far above the 1/s/worker threshold.
- At ~35/s the naive 350k-battle 30×30 matrix is ~3 h under a random policy. The racing
  allocator is still worth building, but L3's budget does not need revisiting.

## 5. Disk

| Component                                  | Size    |
| ------------------------------------------ | ------- |
| `vendor/pokemon-showdown` (total)          | 327 MB  |
| · `node_modules`                           | 137 MB  |
| · `dist` (build output)                    | 119 MB  |
| · `.git` (blobless clone)                  | 27 MB   |
| `.venv` (main)                             | 191 MB  |
| `.venv-train` (torch 2.2.2 is 583 MB)      | 849 MB  |
| `data/champions-data`                      | 11 MB   |
| **Project total**                          | **~1.4 GB** |
| One Reg M-C replay log                     | 6.3 KB raw / 1.5 KB gz |

Free space went 17 GB → 14 GB during the spike. Besides the project, the global caches grew:
`~/Library/Caches/pip` is 498 MB and `~/.npm` is 1.1 GB. `pip cache purge` and
`npm cache clean --force` reclaim ~1.6 GB if needed. A 100k-replay corpus is ~150 MB
gzipped, so the replay layer fits comfortably. **Keep replays gzipped on disk.**

## 6. Replay supply (risk-table check)

`replay.pokemonshowdown.com/search.json?format=gen9championsvgc2026regmc`: the latest 51
public replays span ~19 minutes (≈150/hour, ~3.5k/day), 47/51 rated, max rating on the page
1358. Bo3 format is live too. Reg M-B replays still upload. The corpus is growing fast, but
top-rated games are a small fraction, so the M-B bootstrap in the risk table is still the
right fallback for BC.

---

## Changes to the plan

1. Two venvs instead of one pin set (§1). The BC trainer reads `.npz` and never imports poke-env.
2. `reg_mc.yaml` `showdown_sha` = `2ddfa0476f8207e12e204b1c69f7c7683b17633c`, and the dataset is pinned by our own submodule SHA, not a dataset-recorded Showdown SHA.
3. The team validator rejects `Tera Type:` itself.
4. `vgc server start` writes `simulator: 4` into Showdown's `config/config.js`.
