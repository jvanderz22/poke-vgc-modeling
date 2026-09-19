# vgc — Pokémon Champions VGC advisor

Team-building and battle advice for Pokémon Champions VGC (currently **Regulation M-C**),
backed by a pinned local Pokémon Showdown as ground truth. See [PLAN.md](PLAN.md) for the
roadmap and progress, and [docs/phase0-findings.md](docs/phase0-findings.md) for the
environment decisions.

## Setup

Needs pyenv Python 3.12 and Node ≥ 20.

```bash
git submodule update --init --recursive
(cd vendor/pokemon-showdown && npm i && node build)   # simulator (pinned in configs/regulations/*.yaml)
(cd sidecar/calc && npm ci)                           # @smogon/calc 0.12.0 (Champions mode)

pyenv local 3.12.14
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

`.venv` intentionally has **no torch**: poke-env needs numpy ≥ 2, and torch 2.2.2 (the last
Intel-Mac wheel) can't exchange arrays with numpy 2. Models run through `onnxruntime`.
Training uses a separate `.venv-train` (see the Phase 0 findings).

## Usage

```bash
vgc regulation                                   # what Reg M-C allows, and the pins
vgc team validate team.txt --showdown            # SP budget, clauses, pool, no Tera; cross-checks Showdown
vgc team stats team.txt                          # level-50 stats, including Mega formes
vgc calc --attacker "Garchomp @ Garchompite Z | Jolly Nature | EVs: 32 Atk / 32 Spe" --attacker-mega \
         --defender "Kingambit | Careful Nature | EVs: 32 HP / 32 Def" --move Earthquake
vgc server start | status | stop                 # local Showdown on :8000, --no-security

vgc meta scrape --format both --pages 8          # cache public replays (data/replays/)
vgc meta pool                                    # dated team pool from open team sheets
vgc sim battle --team-a a.txt --team-b b.txt --n 50   # seeded, parallel; win rate with 95% CI
vgc sim selfplay --n 500 --policy-a heuristic --policy-b random

vgc data freeze                                  # freeze held-out teams/battles/replays (once)
vgc data generate --n 6000 --workers 7           # heuristic self-play, then snapshots
vgc data extract --run data/selfplay/<run_id>    # snapshots from any self-play run
vgc data human                                   # snapshots from cached human replays
vgc data manifest --name wp-v1-train             # training manifest, refused if it touches held-out data
vgc data parity --n 50                           # snapshots vs what poke-env showed live
vgc data stats
```

Team files use Showdown's export format. In Champions, the `EVs:` line holds **Stat Points**
(66 total, 32 max per stat, 1 SP = +1 stat at level 50). `SPs:` is accepted as an alias.

## Layout

```
configs/regulations/     regulation configs (L0): reg_mc.yaml, reg_mb.yaml
data/regulations/<id>/   legality snapshot exported from the pinned Showdown (vgc regulation export)
data/champions-data/     vbbjandrade/pokemon-champions-data (CC BY 4.0) — mechanics docs, cross-check
vendor/pokemon-showdown/ smogon/pokemon-showdown (MIT), pinned
data/teams/<id>/         team pools built from open team sheets
data/splits/<id>.json    the frozen held-out split (tracked; never re-rolled)
data/selfplay/, data/snapshots/, data/replays/   generated / cached, not tracked
sidecar/                 Node helpers: calc server, Showdown dex export, damage sampler, battle runner
src/vgc/                 regulation · engine · teams · policy · meta · sim · data · cli  (building · mcp to come)
```

## Attribution

Regulation data cross-checked against [pokemon-champions-data](https://github.com/vbbjandrade/pokemon-champions-data)
by vbbjandrade, CC BY 4.0. Simulator: [Pokémon Showdown](https://github.com/smogon/pokemon-showdown) (MIT).
Damage calculator: [@smogon/calc](https://github.com/smogon/damage-calc) (MIT).
