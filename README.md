# vgc — Pokémon Champions VGC advisor

Team-building and battle advice for Pokémon Champions VGC (currently **Regulation M-C**),
backed by a pinned local Pokémon Showdown as ground truth. See [PLAN-v2.md](PLAN-v2.md) for the
roadmap and progress ([PLAN.md](PLAN.md) is the superseded archive), and [docs/phase0-findings.md](docs/phase0-findings.md) for the
environment decisions. The local battle companion is documented in
[docs/web-app.md](docs/web-app.md); retraining for a new regulation is
[docs/regulation-change.md](docs/regulation-change.md), and renting hardware is
[docs/cloud-compute.md](docs/cloud-compute.md).

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
vgc team weakness team.txt                       # what the meta does to it: KO breakpoints, speed, holes
vgc calc --attacker "Garchomp @ Garchompite Z | Jolly Nature | EVs: 32 Atk / 32 Spe" --attacker-mega \
         --defender "Kingambit | Careful Nature | EVs: 32 HP / 32 Def" --move Earthquake
vgc server start | status | stop                 # local Showdown on :8000, --no-security

vgc meta scrape --format both --pages 8          # cache public replays (data/replays/)
vgc meta pool                                    # dated team pool from open team sheets
vgc meta usage                                   # what the corpus brings: species, items, abilities, natures, moves, partners
vgc meta usage --species Rillaboom               # the full detail for one species
vgc meta players                                 # who is in the cache, and how strong they got
vgc meta usage --skill-percentile 50             # usage over the top half of players
vgc meta scrape --players 50                     # fetch every replay of the top half, then snowball
vgc sim battle --team-a a.txt --team-b b.txt --n 50   # seeded, parallel; win rate with 95% CI
vgc sim selfplay --n 500 --policy-a heuristic --policy-b random
vgc sim validate                                 # does self-play predict real human results?

vgc data freeze                                  # freeze held-out teams/battles/replays (once)
vgc data generate --n 6000 --workers 7           # heuristic self-play, then snapshots
vgc data extract --run data/selfplay/<run_id>    # snapshots from any self-play run
vgc data human                                   # snapshots from cached human replays
vgc data manifest --name wp-v1-train             # training manifest, refused if it touches held-out data
vgc data parity --n 50                           # snapshots vs what poke-env showed live
vgc data stats

vgc wp featurize --manifest wp-v1-train          # feature arrays from a checked manifest
vgc wp train --kind set --epochs 12              # (baselines: --kind logistic | gbt)
vgc wp eval --version <v> --baseline wp-v1-gbt   # held-out human games; writes gate verdicts
vgc wp registry                                  # every model, with the gates it failed
vgc wp endgames                                  # held-out human games it called at 90%+ before the end

vgc belief speed <replay> --known p1             # turn order → a bound on their Speed Stat Points
vgc belief sp <replay> --known p1 --team a.txt   # both channels, over the whole 66-point allocation

vgc web                                          # battle companion on localhost:8001
```

`make` wraps the pipeline: `make data` (scrape → pool → self-play → snapshots → features),
`make models`, `make sweep` (free Kaggle GPU), `make gates`, `make endgames`, `make sim-validity`,
`make test`.

Team files use Showdown's export format. In Champions, the `EVs:` line holds **Stat Points**
(66 total, 32 max per stat, 1 SP = +1 stat at level 50). `SPs:` is accepted as an alias.

## Layout

```
configs/regulations/     regulation configs (L0): reg_mc.yaml, reg_mb.yaml
data/regulations/<id>/   legality snapshot exported from the pinned Showdown (vgc regulation export)
data/champions-data/     vbbjandrade/pokemon-champions-data (CC BY 4.0) — mechanics docs, cross-check
vendor/pokemon-showdown/ smogon/pokemon-showdown (MIT), pinned
data/teams/<id>/         team pools and usage reports built from open team sheets
data/splits/<id>.json    the frozen held-out split (tracked; never re-rolled)
data/selfplay/, data/snapshots/, data/replays/   generated / cached, not tracked
sidecar/                 Node helpers: calc server, Showdown dex export, damage sampler, battle runner
src/vgc/                 regulation · engine · teams · policy · meta · sim · data · building · belief · cli  (mcp to come)
```

## Docs

- [PLAN-v2.md](PLAN-v2.md): the live plan — phases, gates, what is deferred and why
- [PLAN.md](PLAN.md): superseded archive — the research and architecture behind the stack
- [docs/phase4-findings.md](docs/phase4-findings.md): what the WP corpus does and doesn't support
- [docs/phase6-findings.md](docs/phase6-findings.md): whether the simulator predicts human outcomes
- [docs/phase8-findings.md](docs/phase8-findings.md): turn order as a bound on their hidden Speed investment
- [docs/phase0-findings.md](docs/phase0-findings.md): environment spike results
- [docs/regulation-change.md](docs/regulation-change.md): runbook for moving to a new regulation and retraining
- [docs/cloud-compute.md](docs/cloud-compute.md): when renting hardware pays off, and the spend controls

## Attribution

Regulation data cross-checked against [pokemon-champions-data](https://github.com/vbbjandrade/pokemon-champions-data)
by vbbjandrade, CC BY 4.0. Simulator: [Pokémon Showdown](https://github.com/smogon/pokemon-showdown) (MIT).
Damage calculator: [@smogon/calc](https://github.com/smogon/damage-calc) (MIT).
