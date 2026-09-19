#!/usr/bin/env bash
# Pack everything a GPU box needs to train a WP model: the feature dataset, the trainer, and the
# run script. Nothing else from the repo is required — the trainer imports only torch and numpy.
#
#   scripts/cloud/pack_training.sh [regulation] [dataset]   → /tmp/wp-train-<reg>-<dataset>.tgz
set -euo pipefail
cd "$(dirname "$0")/../.."
REG="${1:-reg_mc}"
DATASET="${2:-wp-v1}"
DIR="data/features/$REG/$DATASET"
[ -d "$DIR" ] || { echo "no dataset at $DIR (run: vgc wp featurize)" >&2; exit 1; }
OUT="/tmp/wp-train-$REG-$DATASET.tgz"
tar czf "$OUT" "$DIR" src/vgc/wp/set_torch.py scripts/cloud/run_training.sh
echo "$OUT"
du -h "$OUT" | cut -f1
echo "upload it, then on the box:  tar xzf $(basename "$OUT") && bash scripts/cloud/run_training.sh $REG $DATASET"
