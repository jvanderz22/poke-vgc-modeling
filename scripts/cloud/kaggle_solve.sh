#!/usr/bin/env bash
# Endgame-solver positions solved on a free Kaggle CPU session, then merged into the local solver
# cache (`vgc.wp.offload`). The commands that read the answers then run unchanged and find them
# cached, so the laptop stays free for the long part.
#
#   scripts/cloud/kaggle_solve.sh JOBS.jsonl [--cap 180] [--max-minutes 690] [--no-wait]
#   scripts/cloud/kaggle_solve.sh --collect          # fetch and merge the last run's output
#
# JOBS.jsonl comes from an export, which writes only positions not already cached:
#   .venv/bin/python scripts/analysis/solver_vs_humans.py --depths 2,3 --export .vgc/jobs/humans.jsonl
#   vgc wp solve --export .vgc/jobs/benchmark.jsonl
#
# --no-wait launches and returns: close the laptop, and collect in the morning. The kernel is
# Kaggle's, so walking away from it loses nothing. Without it the script polls until the run ends.
#
# What goes out: the positions (teams and HP at a 1v1, from public replays), the solver, and the
# engine it runs on (a Linux Node binary and a 12 MB slice of the pinned Showdown build, pushed
# again only when either pin changes). What comes back: the engine's answers, merged only if they
# came from this exact solver and a few of them re-solve here to the same answer. Scoring stays here.
#
# Kaggle's CPU session: ~4 cores, 12 hours, and output kept only if the session ends normally, so
# the runner stops itself by --max-minutes (690) with everything it finished written. A position
# cut off then is exported again next time. Auth is the KGAT_ token set up in kaggle_sweep.sh.
set -euo pipefail
cd "$(dirname "$0")/../.."

JOBS=""; CAP=180; MAX_MINUTES=690; WAIT=1; COLLECT_ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --cap) CAP="$2"; shift 2 ;;
    --max-minutes) MAX_MINUTES="$2"; shift 2 ;;
    --no-wait) WAIT=""; shift ;;
    --collect) COLLECT_ONLY=1; shift ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) JOBS="$1"; shift ;;
  esac
done
[ -n "$COLLECT_ONLY" ] || [ -f "$JOBS" ] || { echo "usage: $0 JOBS.jsonl [--cap S] [--max-minutes M] [--no-wait] | --collect" >&2; exit 2; }

# Kept across reboots: an overnight run is collected the next day, and the merge needs the jobs sent.
WORK="${WORK:-.vgc/kaggle-solve}"
POLL_SECONDS="${POLL_SECONDS:-120}"
KERNEL="vgc-endgame-solve"
KAGGLE="${KAGGLE:-.venv/bin/kaggle}"
VGC="${VGC:-.venv/bin/vgc}"
command -v "$KAGGLE" >/dev/null || { echo "kaggle CLI not found — pip install -e '.[cloud]'" >&2; exit 1; }

mkdir -p "$WORK"
LOCK="$WORK/lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  OTHER=$(cat "$LOCK/pid" 2>/dev/null || echo "?")
  if [ "$OTHER" != "?" ] && kill -0 "$OTHER" 2>/dev/null; then
    echo "another kaggle_solve is running (pid $OTHER)" >&2; exit 1
  fi
  rm -rf "$LOCK"; mkdir "$LOCK"
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

KUSER=$("$KAGGLE" config view 2>/dev/null | awk '/^- username:/{print $3}')
[ -n "$KUSER" ] && [ "$KUSER" != "None" ] || {
  echo "not authenticated — see the header of scripts/cloud/kaggle_sweep.sh" >&2; exit 1; }
"$KAGGLE" kernels list --mine >/dev/null 2>&1 || {
  echo "token rejected on an authenticated call — regenerate it at kaggle.com/settings" >&2; exit 1; }
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

# A new version is attachable only once Kaggle has processed it, and a kernel pushed before then
# runs on the previous version. Each push carries a file named for it, and that is what is awaited:
# waiting for a filename both versions share would pass at once.
wait_for() {  # slug, filename
  local slug="$1" want="$2" i
  for i in $(seq 1 60); do
    "$KAGGLE" datasets files "$KUSER/$slug" 2>/dev/null | grep -q "$want" && return 0
    sleep 15
  done
  echo "$want never appeared in $slug — see https://kaggle.com/$KUSER/datasets" >&2
  return 1
}

if [ -z "$COLLECT_ONLY" ]; then
# --- 1. the engine: Node and the slice of Showdown the solver loads -------------------------------
NODE_V=$(node --version)                  # the laptop's, so both ends run the same engine
SD_SHA=$(git -C vendor/pokemon-showdown rev-parse HEAD)
STAMP="engine-${NODE_V}-${SD_SHA:0:12}"
if [ "$(cat "$WORK/engine-pushed" 2>/dev/null)" != "$STAMP" ]; then
  rm -rf "$WORK/engine"; mkdir -p "$WORK/engine" .vgc/cache
  TARBALL="node-$NODE_V-linux-x64.tar.xz"
  if [ ! -f ".vgc/cache/$TARBALL" ]; then
    echo "==> fetching $TARBALL"
    curl -sfL "https://nodejs.org/dist/$NODE_V/$TARBALL" -o ".vgc/cache/$TARBALL.part"
    WANT=$(curl -sfL "https://nodejs.org/dist/$NODE_V/SHASUMS256.txt" | awk -v f="$TARBALL" '$2==f{print $1}')
    GOT=$(shasum -a 256 ".vgc/cache/$TARBALL.part" | cut -d' ' -f1)
    [ -n "$WANT" ] && [ "$WANT" = "$GOT" ] || { echo "checksum mismatch for $TARBALL" >&2; exit 1; }
    mv ".vgc/cache/$TARBALL.part" ".vgc/cache/$TARBALL"
  fi
  cp ".vgc/cache/$TARBALL" "$WORK/engine/"
  # What `endgame-solver.js` loads: the simulator, its data and libs, and the PRNG's one package.
  tar -C vendor/pokemon-showdown -czf "$WORK/engine/showdown.tar.gz" \
      package.json dist/sim dist/lib dist/data dist/config node_modules/ts-chacha20
  echo "{\"node\": \"$NODE_V\", \"showdown\": \"$SD_SHA\"}" > "$WORK/engine/$STAMP.json"
  du -sh "$WORK/engine" | cut -f1
  push_dataset "$WORK/engine" vgc-solver-engine "VGC solver engine"
  wait_for vgc-solver-engine "$STAMP.json"
  echo "$STAMP" > "$WORK/engine-pushed"
else
  echo "==> engine $STAMP already on Kaggle"
fi

# --- 2. this run: the jobs, the solver, and its settings -----------------------------------------
RUN_ID="run-$(date -u +%Y%m%dT%H%M%SZ)"
rm -rf "$WORK/run" "$WORK/out"; mkdir -p "$WORK/run" "$WORK/nb"
cp "$JOBS" "$WORK/jobs.jsonl"             # kept here: the merge checks results against what was sent
cp "$JOBS" "$WORK/run/jobs.jsonl"          # the runner looks for this name
cp sidecar/showdown/endgame-solver.js "$WORK/run/"
SOLVER_SHA=$(shasum -a 256 sidecar/showdown/endgame-solver.js | cut -d' ' -f1)
cat > "$WORK/run/run.json" <<JSON
{"run_id": "$RUN_ID", "cap": $CAP, "max_minutes": $MAX_MINUTES, "solver_sha256": "$SOLVER_SHA",
 "jobs": $(grep -c . "$JOBS"), "commit": "$(git rev-parse --short HEAD)"}
JSON
cp "$WORK/run/run.json" "$WORK/run/$RUN_ID.json"
cp "$WORK/run/run.json" "$WORK/launched.json"
echo "==> $RUN_ID: $(grep -c . "$JOBS") positions, cap ${CAP}s, stop by ${MAX_MINUTES} min"
push_dataset "$WORK/run" vgc-solver-jobs "VGC solver jobs"
wait_for vgc-solver-jobs "$RUN_ID.json"

# --- 3. the kernel: the runner itself, on CPU, no internet ---------------------------------------
cp scripts/cloud/solve_batch.py "$WORK/nb/"
cat > "$WORK/nb/kernel-metadata.json" <<JSON
{
  "id": "$KUSER/$KERNEL",
  "title": "VGC endgame solve",
  "code_file": "solve_batch.py",
  "language": "python",
  "kernel_type": "script",
  "is_private": true,
  "enable_gpu": false,
  "enable_internet": false,
  "dataset_sources": ["$KUSER/vgc-solver-engine", "$KUSER/vgc-solver-jobs"],
  "competition_sources": [],
  "kernel_sources": []
}
JSON
echo "==> launching on CPU"
"$KAGGLE" kernels push -p "$WORK/nb"

if [ -z "$WAIT" ]; then
  echo "==> launched. Watch https://kaggle.com/code/$KUSER/$KERNEL, and collect with:"
  echo "  bash scripts/cloud/kaggle_solve.sh --collect"
  exit 0
fi
fi  # end of the launch phase

# --- 4. wait --------------------------------------------------------------------------------------
DEADLINE=$(( $(date +%s) + (MAX_MINUTES + 60) * 60 ))
while :; do
  STATUS=$("$KAGGLE" kernels status "$KUSER/$KERNEL" 2>&1 | tr "[:upper:]" "[:lower:]" || true)
  case "$STATUS" in
    *complete*) echo "==> complete"; break ;;
    *error*|*cancel*) echo "$STATUS" >&2; echo "see https://kaggle.com/code/$KUSER/$KERNEL" >&2; exit 1 ;;
  esac
  if [ -n "$COLLECT_ONLY" ]; then echo "not finished: $STATUS" >&2; exit 1; fi
  if [ "$(date +%s)" -gt "$DEADLINE" ]; then
    echo "still running past its own deadline — see https://kaggle.com/code/$KUSER/$KERNEL" >&2; exit 1
  fi
  echo "    $STATUS"
  sleep "$POLL_SECONDS"
done

# --- 5. bring the answers home --------------------------------------------------------------------
rm -rf "$WORK/out"; mkdir -p "$WORK/out"
for try in 1 2 3; do
  "$KAGGLE" kernels output "$KUSER/$KERNEL" -p "$WORK/out" && break
  echo "    download attempt $try failed — retrying in 30s" >&2; sleep 30
done
[ -f "$WORK/out/results.jsonl" ] || { echo "no results.jsonl in the output — see the kernel's log" >&2; exit 1; }
# The kernel's output is its last run's, which need not be the run launched from here.
GOT_RUN=$(python3 -c "import json; print(json.load(open('$WORK/out/summary.json'))['run'].get('run_id'))")
WANT_RUN=$(python3 -c "import json; print(json.load(open('$WORK/launched.json'))['run_id'])")
[ "$GOT_RUN" = "$WANT_RUN" ] || { echo "the output is from $GOT_RUN, not $WANT_RUN" >&2; exit 1; }
python3 -c "import json; s=json.load(open('$WORK/out/summary.json')); print('==>', {k: s[k] for k in ('solved','timeout','error','cut_by_deadline','not_started','elapsed_s','workers')})"
"$VGC" wp merge-solves "$WORK/out/results.jsonl" --jobs "$WORK/jobs.jsonl"
