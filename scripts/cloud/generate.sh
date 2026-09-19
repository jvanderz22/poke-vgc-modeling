#!/usr/bin/env bash
# Generate self-play battles + snapshots on a CPU box, then stop. Same cost controls as
# run_training.sh: MAX_MINUTES (default 120), SHUTDOWN=1, RATE.
#
#   bash scripts/cloud/generate.sh <battles> [seed] [regulation]
set -euo pipefail
N="${1:?usage: generate.sh <battles> [seed] [regulation]}"
SEED="${2:-1}"; REG="${3:-reg_mc}"
MAX_MINUTES="${MAX_MINUTES:-120}"
WORKERS="${WORKERS:-$(nproc 2>/dev/null || sysctl -n hw.ncpu)}"
START=$(date +%s)
set +e
timeout "${MAX_MINUTES}m" .venv/bin/vgc data generate -r "$REG" --n "$N" --seed "$SEED" --workers "$WORKERS"
CODE=$?
set -e
ELAPSED=$(( $(date +%s) - START ))
[ $CODE -eq 124 ] && echo "STOPPED at the ${MAX_MINUTES}m cap" >&2
printf 'elapsed %dm%02ds on %s workers' $((ELAPSED/60)) $((ELAPSED%60)) "$WORKERS"
[ -n "${RATE:-}" ] && awk -v s="$ELAPSED" -v r="$RATE" 'BEGIN{printf "  ≈ $%.2f at $%s/hr", s*r/3600, r}'
echo
if [ "${SHUTDOWN:-0}" = "1" ]; then
  echo "shutting down in 120s (ctrl-c to cancel) — rsync data/snapshots/ first"
  sleep 120 && sudo poweroff
fi
exit $CODE
