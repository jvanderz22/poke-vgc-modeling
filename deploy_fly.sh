#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Deploy the battle-companion web app to Fly.io (Dockerfile, fly.toml, deploy/README.md).

Usage:
  ./deploy_fly.sh --secret VGC_WEB_PASSWORD=...     # first deploy: the password is required
  ./deploy_fly.sh                                   # ...or with VGC_WEB_PASSWORD in .env
  ./deploy_fly.sh                                   # later deploys

Environment variables also work, from a gitignored .env beside this script (flags win):
  APP_NAME=vgc-live-battle-calculator REGION=iad ./deploy_fly.sh

Options:
  --app-name       default: vgc-live-battle-calculator  (must match fly.toml's `app`)
  --region         default: iad
  --volume-name    default: vgc_data       (must match fly.toml's mount source)
  --volume-size    default: 1  (GB: teams, battles and the solver cache)
  --secret KEY=VAL set a Fly secret (repeatable): VGC_WEB_PASSWORD, optionally VGC_WEB_USER
  --help

Steps:
  1. fly launch --no-deploy        (only if the app does not exist yet)
  2. fly volumes create            (only if no volume of that name exists)
  3. fly secrets set ...           (only if --secret was given)
  4. refuse to go on without VGC_WEB_PASSWORD: a public copy would run anybody's solves
  5. fly scale count 1             (one volume, one machine; it still stops when idle)
  6. fly deploy                    (built on Fly's builder from this working tree)

Budget: this does NOT set the account's spend limit. After the first deploy, open
Dashboard -> your org -> Billing -> Spend limits and set a hard monthly cap.
USAGE
}

if [[ -f "$(dirname "$0")/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$(dirname "$0")/.env"
  set +a
fi

APP_NAME="${APP_NAME:-vgc-live-battle-calculator}"
REGION="${REGION:-iad}"
VOLUME_NAME="${VOLUME_NAME:-vgc_data}"
VOLUME_SIZE="${VOLUME_SIZE:-1}"
SECRETS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-name) APP_NAME="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --volume-name) VOLUME_NAME="$2"; shift 2 ;;
    --volume-size) VOLUME_SIZE="$2"; shift 2 ;;
    --secret) SECRETS+=("$2"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

if ! command -v fly >/dev/null 2>&1 && ! command -v flyctl >/dev/null 2>&1; then
  echo "Fly CLI is not installed or not on PATH: https://fly.io/docs/hands-on/install-flyctl/" >&2
  exit 1
fi
FLY_CMD="$(command -v fly || command -v flyctl)"

cd "$(dirname "$0")"
for f in fly.toml Dockerfile .dockerignore; do
  [[ -f "$f" ]] || { echo "$f not found in $(pwd)." >&2; exit 1; }
done

# The image is built from this working tree, and the app needs what git does not carry.
for need in models/served.json data/teams data/replays/gen9championsvgc2026regmc \
            data/regulations vendor/pokemon-showdown/package.json; do
  [[ -e "$need" ]] || { echo "Missing $need: the image would be built without it." >&2; exit 1; }
done

if "$FLY_CMD" status --app "$APP_NAME" >/dev/null 2>&1; then
  echo "==> App '$APP_NAME' already exists; skipping 'fly launch'"
else
  echo "==> Creating Fly app from fly.toml"
  "$FLY_CMD" launch --no-deploy --name "$APP_NAME" --region "$REGION" --copy-config --yes
fi

echo "==> Ensuring the volume exists: $VOLUME_NAME"
# Volume names are labels, not keys: `fly volumes create` makes a new one every time. Create one
# only if none of this name exists, or every deploy leaks another billable volume.
EXISTING_VOLUMES="$("$FLY_CMD" volumes list --app "$APP_NAME" 2>/dev/null | grep -cw -- "$VOLUME_NAME" || true)"
if [[ "${EXISTING_VOLUMES:-0}" -eq 0 ]]; then
  "$FLY_CMD" volumes create "$VOLUME_NAME" --app "$APP_NAME" --size "$VOLUME_SIZE" --region "$REGION" --yes
else
  echo "    volume '$VOLUME_NAME' already exists ($EXISTING_VOLUMES); skipping create"
fi

# The password from the environment or .env, if the app does not have one yet.
if [[ -n "${VGC_WEB_PASSWORD:-}" ]] && ! printf '%s\n' "${SECRETS[@]:-}" | grep -q '^VGC_WEB_PASSWORD=' &&
   ! "$FLY_CMD" secrets list --app "$APP_NAME" 2>/dev/null | grep -qw VGC_WEB_PASSWORD; then
  SECRETS+=("VGC_WEB_PASSWORD=$VGC_WEB_PASSWORD")
fi

if [[ ${#SECRETS[@]} -gt 0 ]]; then
  echo "==> Setting secrets"
  "$FLY_CMD" secrets set --app "$APP_NAME" --stage "${SECRETS[@]}"
fi

if ! "$FLY_CMD" secrets list --app "$APP_NAME" 2>/dev/null | grep -qw VGC_WEB_PASSWORD; then
  echo "No VGC_WEB_PASSWORD secret on '$APP_NAME'. Without it the app is open to anyone, and runs" >&2
  echo "their solves on your bill. Rerun with --secret VGC_WEB_PASSWORD=..." >&2
  exit 1
fi

echo "==> Pinning the machine count to 1"
# A volume belongs to one machine; a second would start with empty teams and battles. With
# auto_stop_machines and min_machines_running = 0 in fly.toml, the one machine still stops when idle.
"$FLY_CMD" scale count 1 --app "$APP_NAME" --max-per-region 1 --yes || \
  "$FLY_CMD" scale count 1 --app "$APP_NAME" --yes || true

echo "==> Deploying"
"$FLY_CMD" deploy --app "$APP_NAME"

echo ""
echo "Deployed: https://$APP_NAME.fly.dev  (user: \${VGC_WEB_USER:-vgc}, the password you set)"
echo ""
echo "Check the guards:"
echo "  $FLY_CMD scale show --app $APP_NAME"
echo "  $FLY_CMD machine list --app $APP_NAME"
echo ""
echo "IMPORTANT budget backstop (once, by hand):"
echo "  Dashboard -> your org -> Billing -> Spend limits -> set a hard monthly cap."
