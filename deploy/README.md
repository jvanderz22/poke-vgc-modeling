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
| `bring_rates.json`, `endgames.json` from `data/analysis/reg_mc` | the image, as exceptions to the line above | the policy and the Endgames page read them; without them a policy solve is a 500 and the page is empty |

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

## Measured on the deployed machine (2026-10-01)

- **It stops when idle.** Left alone after the deploy, it stopped by itself about 6 minutes after
  its last request (23:41 → 23:47 UTC).
- **Cold start.** From stopped, the first request answered in 3.8 s, the next in 0.1 s. The solver
  processes warm in the background: three of them, about 55 MB each idle, with the app at about
  65 MB and 1.7 GB of the 2 GB free.

## Measured on the deployed machine (2026-10-04)

Through the MCP server (`vgc.mcp.server`'s tools against the deployed URL,
`scripts/analysis/fly_timing.py`), on the fixture games' positions from p1's seat, warm machine, 10 s between positions. "First seen" is uncached: the
solver's cache is on the volume, so a position asked again (even after a redeploy) answers in
about 0.4 s.

| Call | Time |
| --- | --- |
| Reads (`health`, `models`, `pool`, `teams`, `battles`, `endgames`) | 47–79 ms median |
| Create a battle, append 25–70 taps, read it, its trajectory | 0.2–0.6 s each |
| Model + policy, 3v3 / 4v3, first seen | 1.9 s / 1.8 s (release v5; a 500 on v4, see above) |
| 2v2: quick, searched, the other two guesses | 0.7 s, 3.4 s, 8.2 s |
| 1v2: searched, the other two guesses | 2.5 s, 4.5 s |
| 1v1: first answer | 1.1–2.0 s; a deep one reached depth 2 at 17 s and depth 3 by 75 s |

Every answer the page leads with landed within 5 s. Once, a single `solve` request on the deep
1v1 got no reply for 120 s; two more runs of that position (about 300 requests each) never took
over 0.22 s.

## Not yet known

- **The 5-second answer on back-to-back turns.** Shared vCPUs run at a small baseline and burst
  above it on accumulated credit. One answer a turn fits (above), but the back-to-back round ran
  on cached answers, so a run of uncached answers has not been timed. If they come in late,
  either raise the machine (`performance-1x`, 2 GB, is $34 a month running, same per-second
  rule) or accept the quick answer more often.

## Password

HTTP Basic, set by the `VGC_WEB_PASSWORD` secret (and `VGC_WEB_USER`, default `vgc`). Without it
the app is open, which is right on localhost and wrong here: anybody could run solves and
simulations on your bill. `deploy_fly.sh` will not deploy without it. `/api/health` stays open.
