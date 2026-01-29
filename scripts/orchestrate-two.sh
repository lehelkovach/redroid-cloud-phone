#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

NAME_PREFIX="orchestrated-phone"
IMAGE_ID="${GOLDEN_IMAGE_ID:-}"
PROXY_URL=""
WAIT_CHECK=true
RUN_TESTS=false
ADB_CMD="getprop ro.build.version.release"
API_TOKEN=""

usage() {
  cat <<EOF
Launch two VMs from a golden image and relay a command to both.

Usage:
  $0 --image-id <ocid> [options]

Options:
  --name-prefix NAME     Instance name prefix (default: $NAME_PREFIX)
  --image-id OCID        Golden image OCID (or set GOLDEN_IMAGE_ID env)
  --proxy URL            Proxy URL to configure (optional)
  --api-token TOKEN      API token for Control API (optional)
  --adb-cmd CMD          ADB shell command to run on both instances
  --wait-check           Wait for instance readiness (default: true)
  --run-tests            Run post-deploy tests
  --help                 Show this help

Example:
  $0 --image-id ocid1.image... --proxy socks5://proxy:1080 --adb-cmd "getprop ro.product.model"
EOF
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name-prefix) NAME_PREFIX="$2"; shift 2 ;;
    --image-id) IMAGE_ID="$2"; shift 2 ;;
    --proxy) PROXY_URL="$2"; shift 2 ;;
    --api-token) API_TOKEN="$2"; shift 2 ;;
    --adb-cmd) ADB_CMD="$2"; shift 2 ;;
    --wait-check) WAIT_CHECK=true; shift ;;
    --run-tests) RUN_TESTS=true; shift ;;
    --help|-h) usage ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

if [[ -z "$IMAGE_ID" ]]; then
  echo "ERROR: --image-id required (or GOLDEN_IMAGE_ID env)" >&2
  exit 1
fi

DEPLOY_CMD_BASE=("$SCRIPT_DIR/deploy-from-golden.sh" --image-id "$IMAGE_ID")
if [[ -n "$PROXY_URL" ]]; then
  DEPLOY_CMD_BASE+=(--proxy "$PROXY_URL")
fi
if [[ "$WAIT_CHECK" == "true" ]]; then
  DEPLOY_CMD_BASE+=(--wait-check)
fi
if [[ "$RUN_TESTS" == "true" ]]; then
  DEPLOY_CMD_BASE+=(--run-tests)
fi

NAME1="${NAME_PREFIX}-1"
NAME2="${NAME_PREFIX}-2"

echo "Launching $NAME1..."
"${DEPLOY_CMD_BASE[@]}" --name "$NAME1" >/tmp/instance-$NAME1.log 2>&1 &
PID1=$!

echo "Launching $NAME2..."
"${DEPLOY_CMD_BASE[@]}" --name "$NAME2" >/tmp/instance-$NAME2.log 2>&1 &
PID2=$!

wait $PID1
wait $PID2

INFO1="/tmp/instance-$NAME1.json"
INFO2="/tmp/instance-$NAME2.json"

if [[ ! -f "$INFO1" || ! -f "$INFO2" ]]; then
  echo "ERROR: instance info files not found. Check /tmp/instance-$NAME*.log" >&2
  exit 1
fi

IP1=$(python3 - <<PY "$INFO1"
import json,sys
print(json.load(open(sys.argv[1]))["public_ip"])
PY
)
IP2=$(python3 - <<PY "$INFO2"
import json,sys
print(json.load(open(sys.argv[1]))["public_ip"])
PY
)

echo "Instance 1: $NAME1 -> $IP1"
echo "Instance 2: $NAME2 -> $IP2"

API1="http://$IP1:8080"
API2="http://$IP2:8080"

echo "Submitting job to both instances..."
ARGS=("--api-url")
if [[ -n "$API_TOKEN" ]]; then
  ARGS+=(--token "$API_TOKEN")
fi

JOB1=$("$SCRIPT_DIR/control-client.py" "${ARGS[@]}" "$API1" \
  job-submit --type adb_shell --payload "{\"command\":\"$ADB_CMD\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')
JOB2=$("$SCRIPT_DIR/control-client.py" "${ARGS[@]}" "$API2" \
  job-submit --type adb_shell --payload "{\"command\":\"$ADB_CMD\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')

echo "Job 1: $JOB1"
echo "Job 2: $JOB2"

echo "Polling results..."
"$SCRIPT_DIR/control-client.py" "${ARGS[@]}" "$API1" job-poll --job-id "$JOB1"
"$SCRIPT_DIR/control-client.py" "${ARGS[@]}" "$API2" job-poll --job-id "$JOB2"

echo "Done."
