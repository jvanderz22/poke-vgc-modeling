#!/usr/bin/env bash
# The whole GPU sweep, start to finish: push the features and the trainer to Kaggle, run the
# notebook on a GPU, wait, pull the models back, and evaluate every one of them locally.
#
#   scripts/cloud/kaggle_sweep.sh [regulation] [dataset]
#
# Costs nothing: Kaggle gives 30 GPU-hours a week and this sweep uses about 0.3 of one. There is
# no balance to manage and nothing to shut down, which is why PLAN.md's spend controls say to try
# this before renting anything.
#
# One-time setup (Kaggle's current scheme — a KGAT_ token, not the old kaggle.json pair):
#   1. pip install kaggle          # 2.2.4+; older clients only speak the retired username+key auth
#   2. Kaggle → Settings → API → "Create New Token", copy the KGAT_… value (shown once)
#   3. mkdir -p ~/.kaggle && echo 'KGAT_…' > ~/.kaggle/access_token && chmod 600 ~/.kaggle/access_token
#      (or export KAGGLE_API_TOKEN=KGAT_…)
#   Check with: kaggle config view   → auth_method: ACCESS_TOKEN
#
# Evaluation is deliberately NOT done on Kaggle. The frozen split and every gate verdict stay on
# this machine; the cloud only ever sees training rows.
set -euo pipefail
cd "$(dirname "$0")/../.."

REG="${1:-reg_mc}"
DATASET="${2:-wp-v1}"
DIR="data/features/$REG/$DATASET"
WORK="${WORK:-/tmp/kaggle-sweep}"
POLL_SECONDS="${POLL_SECONDS:-60}"
MAX_MINUTES="${MAX_MINUTES:-90}"

KAGGLE="${KAGGLE:-kaggle}"
VGC="${VGC:-vgc}"
command -v "$KAGGLE" >/dev/null || { echo "kaggle CLI not found — pip install kaggle" >&2; exit 1; }
[ -d "$DIR" ] || { echo "no dataset at $DIR (run: vgc wp featurize)" >&2; exit 1; }
# `config view` is the only thing that knows the username: the KGAT_ token doesn't carry it.
KUSER=$("$KAGGLE" config view 2>/dev/null | awk '/^- username:/{print $3}')
[ -n "$KUSER" ] && [ "$KUSER" != "None" ] || {
  echo "not authenticated — see the header of this script, then: $KAGGLE config view" >&2; exit 1; }
# A public endpoint answers without credentials, so prove the token works on one that doesn't.
"$KAGGLE" kernels list --mine >/dev/null 2>&1 || {
  echo "token rejected on an authenticated call — regenerate it at kaggle.com/settings" >&2; exit 1; }
echo "==> authenticated as $KUSER"

# --- 1. features -----------------------------------------------------------------------------
# Only what the trainer reads. eval_*.npz are several times larger and must not leave the laptop.
echo "==> staging features"
rm -rf "$WORK"; mkdir -p "$WORK/features/$DATASET" "$WORK/src" "$WORK/nb"
for f in train.npz val.npz info.json vocab.json; do
  [ -f "$DIR/$f" ] || { echo "missing $DIR/$f" >&2; exit 1; }
  cp "$DIR/$f" "$WORK/features/$DATASET/$f"
done
du -sh "$WORK/features" | cut -f1

push_dataset() {  # dir, slug, title
  local dir="$1" slug="$2" title="$3"
  # Written for both paths: `datasets version` reads the id from this file, and fetching it with
  # `datasets metadata` is an extra failure mode when we already know what it should contain.
  cat > "$dir/dataset-metadata.json" <<JSON
{"title": "$title", "id": "$KUSER/$slug", "licenses": [{"name": "unknown"}]}
JSON
  if "$KAGGLE" datasets status "$KUSER/$slug" >/dev/null 2>&1; then
    echo "==> updating dataset $slug"
    "$KAGGLE" datasets version -p "$dir" -m "$(git rev-parse --short HEAD) $(date -u +%FT%TZ)" --dir-mode zip
  else
    echo "==> creating private dataset $slug"
    "$KAGGLE" datasets create -p "$dir" --dir-mode zip
  fi
}

push_dataset "$WORK/features" vgc-wp-features "VGC WP features"
cp src/vgc/wp/set_torch.py "$WORK/src/"
push_dataset "$WORK/src" vgc-set-torch "VGC set encoder trainer"

# A just-pushed version is not attachable until Kaggle finishes processing it, and a kernel that
# attaches too early sees the previous version's files. Wait for the content, not for a clock.
wait_for() {  # slug, filename
  local slug="$1" want="$2" i
  for i in $(seq 1 40); do
    "$KAGGLE" datasets files "$KUSER/$slug" 2>/dev/null | grep -q "^$want" && return 0
    sleep 15
  done
  echo "$want never appeared in $slug — see https://kaggle.com/$KUSER/datasets" >&2
  return 1
}
echo "==> waiting for datasets to finish processing"
wait_for vgc-wp-features train.npz
wait_for vgc-set-torch set_torch.py

# --- 2. the notebook -------------------------------------------------------------------------
KERNEL="vgc-wp-sweep"
cp scripts/cloud/kaggle_train_wp.ipynb "$WORK/nb/$KERNEL.ipynb"
cat > "$WORK/nb/kernel-metadata.json" <<JSON
{
  "id": "$KUSER/$KERNEL",
  "title": "VGC WP sweep",
  "code_file": "$KERNEL.ipynb",
  "language": "python",
  "kernel_type": "notebook",
  "is_private": true,
  "enable_gpu": true,
  "enable_internet": false,
  "dataset_sources": ["$KUSER/vgc-wp-features", "$KUSER/vgc-set-torch"],
  "competition_sources": [],
  "kernel_sources": []
}
JSON
echo "==> launching on GPU"
"$KAGGLE" kernels push -p "$WORK/nb"

# --- 3. wait ---------------------------------------------------------------------------------
DEADLINE=$(( $(date +%s) + MAX_MINUTES * 60 ))
while :; do
  STATUS=$("$KAGGLE" kernels status "$KUSER/$KERNEL" 2>&1 || true)
  case "$STATUS" in
    *complete*) echo "==> complete"; break ;;
    *error*|*cancel*) echo "$STATUS" >&2; echo "see https://kaggle.com/$KUSER/$KERNEL" >&2; exit 1 ;;
  esac
  [ "$(date +%s)" -gt "$DEADLINE" ] && { echo "still running after ${MAX_MINUTES}m — check the notebook" >&2; exit 1; }
  echo "    $STATUS"
  sleep "$POLL_SECONDS"
done

# --- 4. bring the models home ------------------------------------------------------------------
echo "==> downloading"
mkdir -p "$WORK/out"
"$KAGGLE" kernels output "$KUSER/$KERNEL" -p "$WORK/out"
ZIP=$(ls "$WORK/out"/*.zip 2>/dev/null | head -1) || true
[ -n "${ZIP:-}" ] && unzip -qo "$ZIP" -d "$WORK/out/models"
SRC="$WORK/out/models"; [ -d "$SRC" ] || SRC="$WORK/out"

FOUND=()
for d in "$SRC"/*/; do
  name=$(basename "$d")
  [ -f "$d/model.onnx" ] || continue
  mkdir -p "models/wp/$REG/$name"
  cp "$d"/* "models/wp/$REG/$name/"
  FOUND+=("$name")
done
[ ${#FOUND[@]} -eq 0 ] && { echo "no models in the output — check https://kaggle.com/$KUSER/$KERNEL" >&2; exit 1; }
echo "==> got: ${FOUND[*]}"

# --- 5. the part that actually decides ----------------------------------------------------------
# Against the local frozen split, every time. A sweep ranks on validation loss, which does not
# contain the preview gate at all, so the sweep's winner is a shortlist and nothing more.
for name in "${FOUND[@]}"; do
  echo; echo "===== $name ====="
  "$VGC" wp card         --regulation "$REG" --version "$name" --dataset "$DATASET"
  "$VGC" wp calibrate    --regulation "$REG" --version "$name" --dataset "$DATASET"
  "$VGC" wp check-preview --regulation "$REG" --version "$name"
  "$VGC" wp eval         --regulation "$REG" --version "$name" --dataset "$DATASET" \
      --baseline wp-v1-gbt --baseline wp-v1-logistic --baseline constant | tail -5
done

echo; echo "===== gates ====="
"$VGC" wp registry
