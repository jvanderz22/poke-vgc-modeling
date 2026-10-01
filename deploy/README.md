# Deploying the web app to Fly.io

The battle companion (`vgc web`) as one Fly machine that stops when nobody is using it. Deployed
2026-10-01 at https://vgc-live-battle-calculator.fly.dev. Modelled
on `~/dev/my-radio`, with the same budget rules: one machine, scale to zero, a 1 GB volume, a
hard spend cap set by hand.

```sh
./deploy_fly.sh --secret VGC_WEB_PASSWORD='something long'   # first time
./deploy_fly.sh                                               # every time after
```

Then open `https://vgc-live-battle-calculator.fly.dev`; the browser asks for user `vgc` and the password.

## What goes where

| | Where | Why |
| --- | --- | --- |
| The app, models, set corpus, Reg M-C replays, dex | the image, from this working tree | gitignored, so they exist only here; `deploy_fly.sh` refuses if they are missing |
| Pinned Showdown, calc sidecar | built in the image from the submodule's source | the local `node_modules` hold macOS builds |
| Your teams, your battles, the solver's cache | the volume at `/data` (`data/library`, `data/battles`, `.vgc` link to it) | they must survive a restart and a redeploy |
| Training data (snapshots, features, self-play, analysis) | left out (`.dockerignore`) | about a gigabyte the app never reads |

New models or a new regulation reach the cloud the same way: build them locally, then
`./deploy_fly.sh`.

## Sizing, and what it costs

The doubles answer runs six solver processes at once on a laptop, and each holds a copy of the
simulator: about 220 MB idle and up to about 460 MB mid-search, measured. That rules out
my-radio's 512 MB machine.

| | fly.toml | Why |
| --- | --- | --- |
| Machine | `shared-cpu-4x`, 2 GB | three solver processes (`VGC_SOLVER_WORKERS = 3`) and the app fit in 2 GB at their worst; four cores for the quick answer and the search side by side |
| 1v1 depth | `VGC_SOLVE_MAX_DEPTH = 3` | depth 4 can run for hours with the page polling every 2 s, which would keep the machine awake and busy |
| Machines | 1, `auto_stop_machines = 'stop'`, `min_machines_running = 0` | billed per second only while serving |

Fly's prices (IAD, October 2026): `shared-cpu-4x` with 2 GB is $0.00000324 a second, which is
$8.64 a month only if it never stopped. A few hours of use a week is well under $1. Stopped, it
costs the image's disk ($0.15 per GB-month) and the volume ($0.15 a month for 1 GB).

**Set the spend cap.** Dashboard → your org → Billing → Spend limits. The script cannot, and it
is the only thing that bounds a mistake (a machine that never stops, a second volume).

## Not yet known

- **The 5-second answer on shared cores.** Shared vCPUs run at a small baseline and burst above
  it on accumulated credit. A doubles answer is a few seconds of all four cores, which should fit
  in a burst, but it has only been timed on the laptop
  (`scripts/analysis/page_path.py`). Time a few positions on the deployed page; if answers come
  in late, either raise the machine (`performance-1x`, 2 GB, is $34 a month running, same
  per-second rule) or accept the quick answer more often.
- **Cold start.** A stopped machine starts on the first request (a few seconds), then warms its
  solver processes in the background.

## Password

HTTP Basic, set by the `VGC_WEB_PASSWORD` secret (and `VGC_WEB_USER`, default `vgc`). Without it
the app is open, which is right on localhost and wrong here: anybody could run solves and
simulations on your bill. `deploy_fly.sh` will not deploy without it. `/api/health` stays open.
