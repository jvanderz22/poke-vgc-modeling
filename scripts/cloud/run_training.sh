#!/usr/bin/env bash
# Train one WP set-encoder model on a rented box, then stop. Cost controls:
#   MAX_MINUTES  hard cap on training time (default 60) — a hung run cannot bill for hours
#   SHUTDOWN=1   power the machine off when finished (the usual cause of surprise bills is an
#                idle instance, not the training itself)
#   RATE         $/hour, only used to print what the run cost
#
#   bash scripts/cloud/run_training.sh [regulation] [dataset] [-- extra trainer flags]
set -euo pipefail
REG="${1:-reg_mc}"; DATASET="${2:-wp-v1}"; shift 2 || true
[ "${1:-}" = "--" ] && shift || true
MAX_MINUTES="${MAX_MINUTES:-60}"
OUT="models/wp/$REG/cloud-$(date +%Y%m%d-%H%M%S)"
python -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
START=$(date +%s)
set +e
timeout "${MAX_MINUTES}m" env PYTHONPATH=src python -u -m vgc.wp.set_torch \
  --data "data/features/$REG/$DATASET" --out "$OUT" --device auto "$@"
CODE=$?
set -e
ELAPSED=$(( $(date +%s) - START ))
[ $CODE -eq 124 ] && echo "STOPPED at the ${MAX_MINUTES}m cap — raise MAX_MINUTES if that was too short" >&2
printf 'elapsed %dm%02ds' $((ELAPSED/60)) $((ELAPSED%60))
[ -n "${RATE:-}" ] && awk -v s="$ELAPSED" -v r="$RATE" 'BEGIN{printf "  ≈ $%.2f at $%s/hr", s*r/3600, r}'
echo
RESULT="/tmp/wp-result-$(basename "$OUT").tgz"
tar czf "$RESULT" "$OUT" 2>/dev/null && echo "download: $RESULT" && shasum -a 256 "$RESULT" 2>/dev/null || sha256sum "$RESULT"
if [ "${SHUTDOWN:-0}" = "1" ]; then
  echo "shutting down in 60s (ctrl-c to cancel) — make sure the result is downloaded"
  sleep 60 && sudo poweroff
fi
exit $CODE
