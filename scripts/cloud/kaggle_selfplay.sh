#!/usr/bin/env bash
# Self-play battles on a free Kaggle CPU session (PLAN-policy stage 5), merged here only after a
# sample of them replays here to the same inputs.
#
#   scripts/cloud/kaggle_selfplay.sh NAME [--no-wait]     # a run exported by policy_gate.py
#   scripts/cloud/kaggle_selfplay.sh NAME --collect       # fetch its output and merge it
#
# A run is exported and packed first (scripts/analysis/policy_gate.py export/pack, which writes
# .vgc/jobs/selfplay-NAME/upload): the `vgc` package, the sidecars, the few data files a battle
# reads, the matchups and the run's settings. Wheels of the laptop's versions of what a Kaggle image
# lacks are a dataset of their own, pushed only when a version moves. Each NAME has its own kernel and dataset, so runs go side by side; a big run is split
# into several names by battle index (a battle's seed is the run's seed and its index, so where it
# is played changes nothing).
#
# What goes out: team sheets from public replays and the code. What comes back: battle records,
# merged by `policy_gate.py merge`, which refuses output from other code and replays the shortest
# battles here first. Scoring stays here, so nothing on Kaggle can move a verdict.
#
# Kaggle's CPU session: ~4 cores, 12 hours, output kept only if the session ends normally, so the
# runner stops itself by the run's max_minutes (690). A battle cut off is played by a rerun.
# The engine dataset (Node and the Showdown slice) is the one kaggle_solve.sh pushes, and is pushed
# from here too when its pin has moved. Auth is the KGAT_ token set up in kaggle_sweep.sh.
set -euo pipefail
cd "$(dirname "$0")/../.."

NAME=""; WAIT=1; COLLECT_ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-wait) WAIT=""; shift ;;
    --collect) COLLECT_ONLY=1; shift ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) NAME="$1"; shift ;;
  esac
done
[ -n "$NAME" ] || { echo "usage: $0 NAME [--no-wait] | NAME --collect" >&2; exit 2; }

RUN=".vgc/jobs/selfplay-$NAME"
UP="$RUN/upload"
WORK=".vgc/kaggle-selfplay-$NAME"
SLUG="vgc-selfplay-$(echo "$NAME" | tr '[:upper:]_' '[:lower:]-')"
KERNEL="$SLUG"
POLL_SECONDS="${POLL_SECONDS:-180}"
KAGGLE="${KAGGLE:-.venv/bin/kaggle}"
PY="${PY:-.venv/bin/python}"
command -v "$KAGGLE" >/dev/null || { echo "kaggle CLI not found — pip install -e '.[cloud]'" >&2; exit 1; }
mkdir -p "$WORK"

KUSER=$("$KAGGLE" config view 2>/dev/null | awk '/^- username:/{print $3}')
[ -n "$KUSER" ] && [ "$KUSER" != "None" ] || {
  echo "not authenticated — see the header of scripts/cloud/kaggle_sweep.sh" >&2; exit 1; }
echo "==> authenticated as $KUSER"

push_dataset() {  # dir, slug, title
  local dir="$1" slug="$2" title="$3"
  cat > "$dir/dataset-metadata.json" <<JSON
{"title": "$title", "id": "$KUSER/$slug", "licenses": [{"name": "unknown"}]}
JSON
  if "$KAGGLE" datasets status "$KUSER/$slug" >/dev/null 2>&1; then
    echo "==> updating dataset $slug"
    "$KAGGLE" datasets version -p "$dir" -m "$(git rev-parse --short HEAD) $(date -u +%FT%TZ)"
  else
    echo "==> creating private dataset $slug"
    "$KAGGLE" datasets create -p "$dir"
  fi
}

wait_for() {  # slug, filename (see kaggle_solve.sh: a kernel pushed too early runs on the old version)
  local slug="$1" want="$2" i
  for i in $(seq 1 60); do
    "$KAGGLE" datasets files "$KUSER/$slug" 2>/dev/null | grep -q "$want" && return 0
    sleep 15
  done
  echo "$want never appeared in $slug — see https://kaggle.com/$KUSER/datasets" >&2
  return 1
}

if [ -z "$COLLECT_ONLY" ]; then
[ -f "$UP/run.json" ] || { echo "no $UP/run.json — run policy_gate.py export and pack first" >&2; exit 1; }
# --- 1. the engine, shared with kaggle_solve.sh ------------------------------------------------
NODE_V=$(node --version)
SD_SHA=$(git -C vendor/pokemon-showdown rev-parse HEAD)
STAMP="engine-${NODE_V}-${SD_SHA:0:12}"
if [ "$(cat .vgc/kaggle-engine-pushed 2>/dev/null)" != "$STAMP" ]; then
  echo "==> the engine on Kaggle is not $STAMP: push it with kaggle_solve.sh's step 1 first" >&2
  echo "    (any small solve run does it: bash scripts/cloud/kaggle_solve.sh JOBS --no-wait)" >&2
  exit 1
fi
echo "==> engine $STAMP already on Kaggle"

# --- 2. the wheels, pushed when their versions move ---------------------------------------------
WHEELS=$($PY -c "import json; print(json.load(open('$UP/run.json'))['wheels'])")
if [ "$(cat .vgc/kaggle-wheels-pushed 2>/dev/null)" != "$WHEELS" ]; then
  push_dataset .vgc/jobs/selfplay-wheels/upload vgc-selfplay-wheels "VGC selfplay wheels"
  wait_for vgc-selfplay-wheels "$WHEELS.json"
  echo "$WHEELS" > .vgc/kaggle-wheels-pushed
else
  echo "==> wheels $WHEELS already on Kaggle"
fi

# --- 3. the run --------------------------------------------------------------------------------
RUN_ID=$($PY -c "import json; print(json.load(open('$UP/run.json'))['run_id'])")
STAMPED=$(ls "$UP" | grep -E "^$RUN_ID-[0-9]+\.json$" | sort | tail -1)
cp "$UP/run.json" "$WORK/launched.json"
echo "==> $RUN_ID: $(grep -c . "$UP/matchups.jsonl") battles"
push_dataset "$UP" "$SLUG" "VGC selfplay $NAME"
wait_for "$SLUG" "$STAMPED"

# --- 4. the kernel -----------------------------------------------------------------------------
mkdir -p "$WORK/nb"
cp scripts/cloud/selfplay_batch.py "$WORK/nb/"
cat > "$WORK/nb/kernel-metadata.json" <<JSON
{
  "id": "$KUSER/$KERNEL",
  "title": "VGC selfplay $NAME",
  "code_file": "selfplay_batch.py",
  "language": "python",
  "kernel_type": "script",
  "is_private": true,
  "enable_gpu": false,
  "enable_internet": false,
  "dataset_sources": ["$KUSER/vgc-solver-engine", "$KUSER/vgc-selfplay-wheels", "$KUSER/$SLUG"],
  "competition_sources": [],
  "kernel_sources": []
}
JSON
echo "==> launching on CPU"
"$KAGGLE" kernels push -p "$WORK/nb"
if [ -z "$WAIT" ]; then
  echo "==> launched. Watch https://kaggle.com/code/$KUSER/$KERNEL, and collect with:"
  echo "  bash scripts/cloud/kaggle_selfplay.sh $NAME --collect"
  exit 0
fi
fi

# --- 5. wait -----------------------------------------------------------------------------------
while :; do
  STATUS=$("$KAGGLE" kernels status "$KUSER/$KERNEL" 2>&1 | tr "[:upper:]" "[:lower:]" || true)
  case "$STATUS" in
    *complete*) echo "==> complete"; break ;;
    *error*|*cancel*) echo "$STATUS" >&2; echo "see https://kaggle.com/code/$KUSER/$KERNEL" >&2; exit 1 ;;
  esac
  if [ -n "$COLLECT_ONLY" ]; then echo "not finished: $STATUS" >&2; exit 1; fi
  echo "    $STATUS"
  sleep "$POLL_SECONDS"
done

# --- 6. bring the battles home -----------------------------------------------------------------
rm -rf "$WORK/out"; mkdir -p "$WORK/out"
for try in 1 2 3; do
  "$KAGGLE" kernels output "$KUSER/$KERNEL" -p "$WORK/out" && break
  echo "    download attempt $try failed — retrying in 30s" >&2; sleep 30
done
[ -f "$WORK/out/battles.jsonl" ] || { echo "no battles.jsonl in the output — see the kernel's log" >&2; exit 1; }
GOT_RUN=$($PY -c "import json; print(json.load(open('$WORK/out/summary.json'))['run']['run_id'])")
WANT_RUN=$($PY -c "import json; print(json.load(open('$WORK/launched.json'))['run_id'])")
[ "$GOT_RUN" = "$WANT_RUN" ] || { echo "the output is from $GOT_RUN, not $WANT_RUN" >&2; exit 1; }
$PY -c "import json; s=json.load(open('$WORK/out/summary.json')); print('==>', {k: s[k] for k in ('played','error','not_started','elapsed_s','workers','versions','installed')})"
PYTHONPATH=scripts/analysis $PY scripts/analysis/policy_gate.py merge --name "$NAME" "$WORK/out/battles.jsonl"
