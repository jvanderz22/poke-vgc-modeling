#!/usr/bin/env bash
# Set up a CPU box to generate self-play battles (the simulator is CPU-bound and single-threaded
# per battle; a GPU is useless here). Needs git, node >=18, python >=3.10 on the box.
#
# The repo has no remote, so the working copy is rsynced from the laptop. Run this FROM THE LAPTOP:
#
#   scripts/cloud/setup_worker.sh user@host [remote dir]
#
# It copies only what the box needs — code, configs, the legality export, the team pool and the
# frozen split, all of which are tracked — and skips venvs, replays, snapshots, features and
# models. Showdown is cloned on the box from its own upstream at the pinned SHA.
set -euo pipefail
cd "$(dirname "$0")/../.."
HOST="${1:?usage: setup_worker.sh user@host [remote dir]}"
DIR="${2:-vgc}"
SHA="$(git -C vendor/pokemon-showdown rev-parse HEAD)"

rsync -az --delete \
  --exclude '.git' --exclude '.venv*' --exclude 'vendor' --exclude 'node_modules' \
  --exclude 'data/replays' --exclude 'data/snapshots' --exclude 'data/features' \
  --exclude 'data/selfplay' --exclude 'models' --exclude '__pycache__' --exclude '.pytest_cache' \
  ./ "$HOST:$DIR/"

ssh "$HOST" "set -euo pipefail
  cd '$DIR'
  [ -d vendor/pokemon-showdown ] || git clone https://github.com/smogon/pokemon-showdown vendor/pokemon-showdown
  cd vendor/pokemon-showdown && git fetch --all --quiet && git checkout --quiet '$SHA' && npm ci --silent && npm run build
  cd '$HOME/$DIR' 2>/dev/null || cd ~/'$DIR'
  cd sidecar/calc && npm ci --silent && cd -
  python3 -m venv .venv && .venv/bin/pip install -q -e .
  .venv/bin/vgc regulation show
  .venv/bin/python -m pytest -q tests/test_regulation.py tests/test_teams.py
  .venv/bin/vgc sim selfplay --n 200 --policy-a heuristic --policy-b random --workers \$(nproc)
"
cat <<EOF

ready on $HOST:$DIR — the smoke run above must be a near-total win with 0 invalid choices.

generate:  ssh $HOST 'cd $DIR && MAX_MINUTES=90 RATE=<\$/hr> SHUTDOWN=1 bash scripts/cloud/generate.sh 500000 3'
collect:   rsync -avz $HOST:$DIR/data/snapshots/ data/snapshots/
EOF
