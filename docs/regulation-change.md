# Runbook: moving to a new regulation

Reg M-C ends **2026-12-02**. This is the checklist for bringing up the next regulation (called
`reg_md` below) and retraining everything that depends on the regulation. Timings are measured
on the dev laptop (i7-9750H, 12 threads).

Keep this doc current: when a phase adds a regulation-specific artifact, add its rebuild step here.

---

## What is regulation-specific, and what isn't

Everything a regulation touches is tagged with its id, so nothing is overwritten. M-C artifacts stay
usable (and comparable) after M-D exists.

| Artifact | Where | Regulation-specific? | On a new regulation |
| --- | --- | --- | --- |
| Code (`src/vgc/`), sidecars | repo | no | reused; see "Mechanics changes" below |
| Regulation config | `configs/regulations/<id>.yaml` | yes | **write a new one** |
| Showdown pin | `vendor/pokemon-showdown` submodule | one pin at a time | **bump**, then re-verify everything |
| Damage calc | `sidecar/calc` (`@smogon/calc`, exact npm pin) | no (but versioned) | bump if the new regulation needs it |
| Legality snapshot | `data/regulations/<id>/dex.json` | yes | **export** |
| Replay cache | `data/replays/<showdown format>/` | yes (by format id) | **scrape** |
| Team pool | `data/teams/<id>/ots_pool_<date>.json` | yes | **build** |
| Frozen held-out split | `data/splits/<id>.json` (tracked) | yes | **freeze once**, never re-roll |
| Self-play runs | `data/selfplay/<run_id>/` | yes (teams + format) | **generate** |
| Snapshots | `data/snapshots/<id>/` | yes | **extract** |
| Manifests | `data/snapshots/<id>/manifests/` | yes | **build + check** |
| Features | `data/features/<id>/<dataset>/` | yes (the vocabulary comes from the dex) | **featurize** |
| WP models | `models/wp/<id>/<version>/` + `models/registry.json` | yes | **retrain + evaluate** |

**Models don't transfer as-is (yet).** The feature vocabulary (species, items, abilities, moves) is
built from the regulation's dex, so a new pool shifts every embedding id. An M-C model will load but
give wrong answers on M-D inputs. Until the "Regulation-portable models" TODO in PLAN.md is done,
always retrain.

---

## 0. Before you start (the day the rules are announced)

- Read the official rules and note every difference from the previous regulation: the pool (species,
  Megas, items), the SP rules, clauses, level, bring count, and any mechanic turned on or off.
- Wait until Showdown supports the format. `pokemon-champions-data` fetches from Showdown `master`, so it
  won't lead Showdown. Find the format id in Showdown's `config/formats.ts` (M-C:
  `gen9championsvgc2026regmc`, and a `…bo3` variant).
- Check for **mechanics changes** (below). If there are none, the rest of this runbook is mechanical.

## 1. Pins and config (~30 min)

```bash
cd vendor/pokemon-showdown && git fetch && git checkout <sha that has the format> && npm i && npm run build && cd -
cd data/champions-data && git fetch && git checkout <matching sha> && cd -
cp configs/regulations/reg_mc.yaml configs/regulations/reg_md.yaml   # then edit
```

In `reg_md.yaml`, update `id`, `name`, `showdown_format`, `window`, `battle`, `clauses`, `mechanics`,
`stats`, and `data_source` (`showdown_sha`, `dataset_sha`, `dataset_dir`).

**Only one Showdown pin can be checked out at a time.** Once the submodule moves, `reg_mc` refuses
to export or simulate (`check_pin`) until you check its pin out again. Commit the bump and the new
config together, so any old regulation can be reproduced by checking out an older commit.

## 2. Legality snapshot and its verification (~15 min)

```bash
vgc regulation export -r reg_md          # data/regulations/reg_md/dex.json from Showdown's own validator
vgc regulation show -r reg_md            # sanity-check counts: species, Megas, items, moves
pytest -q                                # everything must pass on the old regulation's fixtures too
```

Then add or adjust tests for M-D:

- `tests/test_regulation.py`: the dataset cross-check. Expect a new list of known drift (at M-C the
  dataset was missing 26 legal items). Record the actual list; don't just silence the test.
- `tests/test_validate_parity.py`: our validator vs Showdown's on fixture teams. Add an M-D-legal and
  an M-D-illegal fixture team (`tests/fixtures/teams/`).
- `tests/test_calc_vs_sim.py`: rerun the damage-roll scenarios. If new Megas or items change damage,
  add a scenario that covers one.
- Parity with the simulator (`vgc data parity`, step 4) re-verifies the observer on the new pool.

**Gate:** the full suite is green, and `vgc team validate --showdown` agrees with Showdown on a real M-D team.

## 3. Human data and the frozen split (~20 min, mostly polite scraping)

```bash
vgc meta scrape -r reg_md --format both --pages 16    # ~1 replay/s; Bo3 (open sheets) + Bo1 ladder
vgc meta pool -r reg_md                               # team pool from open team sheets
vgc data freeze -r reg_md                             # held-out teams + replay groups, frozen
git add data/teams/reg_md data/splits/reg_md.json     # both are tracked
```

- **Early in a format there are few replays.** At M-C's day 10 there were ~800 Bo3 games. If the pool
  is thin, re-scrape weekly and rebuild the pool under a new date. The split rules are hash-based, so
  new teams and replays are assigned automatically. **Never re-freeze.** A new freeze invalidates
  every evaluation made so far. `freeze` refuses to overwrite for this reason.
- The pool file is dated. Keep old dates (a run records which pool it used).

## 4. Self-play data (~30–40 min)

```bash
vgc sim selfplay -r reg_md --n 500 --policy-a heuristic --policy-b random   # smoke test: expect ≫85%, 0 invalid choices
vgc data parity -r reg_md --n 200                                          # must print "parity: exact"
vgc data generate -r reg_md --n 40000 --workers 8 --seed 1                 # ~26 battles/s, then extraction
vgc data human -r reg_md                                                   # snapshots from the scraped replays
vgc data manifest -r reg_md --name wp-v1-train \
    data/snapshots/reg_md/selfplay/<run>/train.jsonl.gz \
    data/snapshots/reg_md/human/<format>bo3/train.jsonl.gz                 # refuses if it touches held-out data
```

**Gates:**
- Heuristic vs random: 0 errors and 0 invalid choices. An invalid choice usually means a move whose
  target or data differs from poke-env's Gen 9 data (see Phase 2's Milk Drink case).
- Parity is exact.
- The manifest is written (the check passed).

## 5. Win-probability models (~1 h)

```bash
vgc wp featurize -r reg_md --manifest wp-v1-train --name wp-v1
vgc wp train -r reg_md --kind logistic
vgc wp train -r reg_md --kind gbt
vgc wp train -r reg_md --kind set --epochs 20
vgc wp eval  -r reg_md --version wp-v1-set --baseline wp-v1-gbt --baseline wp-v1-logistic --baseline constant
vgc wp check-preview -r reg_md --version wp-v1-set
vgc wp registry
```

**Gates** (the same as Phase 4's verification). On held-out human open-sheet games:
- log loss and Brier beat the constant and logistic baselines
- ECE < 0.03
- the player view beats the spectator view on shared points
- preview WP tracks the simulated win rates
- the bring head beats usage frequency

A model that misses a gate is not used by the CLI, the web app or search. Say so in its model card.

## 6. Later phases (fill in as they're built)

Each of these will add a rebuild step here:

- **Phase 5, WP v2:** the set prior from Bo1 and usage data (`vgc` command TBD), and belief calibration.
- **Phase 6, BC, search and EWP:** retrain BC on the new human corpus, then refit WP on self-play from
  the stronger policy.
- **Phase 7, team evaluation:** a dated meta gauntlet and the matchup matrix. This is the most compute-heavy
  rebuild, so budget hours.
- **Phase 8, team building:** the surrogate, retrained on the new matrix.
- **Web app:** switches regulation by config and reads the registry for the newest model that passed its gates.

## 7. Cut over and archive

- Make the new regulation the default: the `-r` default in `src/vgc/cli/main.py`, and the app's
  regulation setting.
- Keep the old regulation's tracked files (config, dex export, pool, split). They're small, and they
  are what makes old results reproducible.
- Untracked bulk (`data/selfplay`, `data/snapshots`, `data/features`, `data/replays`) can be
  compressed or deleted. The pieces needed to regenerate them are the git commit (pins and code), the
  pool, the split, and the run seeds recorded in `summary.json` and the model cards.

---

## Mechanics changes: where the code has to change

Most regulation changes only shrink or grow the pool. If a *mechanic* changes, these are the
places that encode it:

| Change | Touch |
| --- | --- |
| Tera re-enabled | `teams/validate.py` (currently rejects Tera), `data/observe.py` (`-terastallize` is ignored), `wp/features.py` (add Tera type/state), heuristic policy (choice encoding) |
| Megas removed or changed | `regulation.Dex.mega_forme`, the heuristic's Mega bonus, `features._can_mega` |
| A different SP budget or cap | config only (`stats`); `policy/state.infer_spread` reads it |
| A different bring size or level | config only (`battle`); check `observe.empty_slot_needs_switch` and the `team_size or 4` defaults |
| A new public-HP format | `data/observe._hp` (Champions shows floored %, with a colour letter at 20% and 50%) |
| Open team sheets on or off | OTS is per game (`ots` flag), so there's no code change; v1 vs v2 models cover both |
| New field effects (terrain, room, weather) | `data/observe.PSEUDO_WEATHER`, `wp/features` (`WEATHERS`, `TERRAINS`, `SIDE_CONDS`) |

After any mechanics change, bump `snapshots.VERSION`, then re-extract and re-featurize. Old
snapshot files are not mixed with new ones.
