#!/bin/bash
# deploy-golden-fleet.sh
# Deploy a pool of golden-image VMs.
# Default platform is redroid (automation). Use --platform cuttlefish for ingest.
#
# Usage:
#   ./scripts/deploy-golden-fleet.sh [OPTIONS]
#
# Options:
#   --count N                 Number of devices to deploy (default: 3)
#   --name-prefix PREFIX      Instance name prefix (default: phone)
#   --platform NAME           cuttlefish (required value)
#   --ocpus N                 OCPUs (default: 4)
#   --memory N                Memory GB (default: 24)
#   --image-id OCID           Golden image OCID (or use GOLDEN_IMAGE_ID env)
#   --wait-check              Run basic health check post-deploy
#   --run-tests               Run post-deploy tests
#   --verify-ingest           For cuttlefish, run ingest verification after deploy
#   --parallel N              Parallel deploy workers (default: 1)
#   --help                    Show help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

COUNT="3"
NAME_PREFIX="phone"
PLATFORM="redroid"
OCPUS=""
MEMORY_GB=""
IMAGE_ID="${GOLDEN_IMAGE_ID:-}"
WAIT_CHECK="false"
RUN_TESTS="false"
VERIFY_INGEST="false"
PARALLEL="1"

usage() {
    cat <<'EOF'
Usage:
  ./scripts/deploy-golden-fleet.sh [OPTIONS]

Options:
  --count N                 Number of devices to deploy (default: 3)
  --name-prefix PREFIX      Instance name prefix (default: phone)
  --platform NAME           redroid (default, automation pool) | cuttlefish (camera ingest)
  --ocpus N                 OCPUs (redroid default 2, cuttlefish default 4)
  --memory N                Memory GB (redroid default 8, cuttlefish default 24)
  --image-id OCID           Golden image OCID (or GOLDEN_IMAGE_ID / REDROID_GOLDEN_IMAGE_ID)
  --wait-check              Run basic health check post-deploy
  --run-tests               Run post-deploy tests
  --verify-ingest           For cuttlefish only: run ingest verification after deploy
  --parallel N              Parallel deploy workers (default: 1)
  --help                    Show help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --count) COUNT="${2:-$COUNT}"; shift 2 ;;
        --name-prefix) NAME_PREFIX="${2:-$NAME_PREFIX}"; shift 2 ;;
        --platform) PLATFORM="${2:-$PLATFORM}"; shift 2 ;;
        --ocpus) OCPUS="${2:-}"; shift 2 ;;
        --memory) MEMORY_GB="${2:-}"; shift 2 ;;
        --image-id) IMAGE_ID="${2:-}"; shift 2 ;;
        --wait-check) WAIT_CHECK="true"; shift ;;
        --run-tests) RUN_TESTS="true"; shift ;;
        --verify-ingest) VERIFY_INGEST="true"; shift ;;
        --parallel) PARALLEL="${2:-1}"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

if [[ "$PLATFORM" != "cuttlefish" && "$PLATFORM" != "redroid" ]]; then
    echo "Unsupported --platform $PLATFORM (redroid|cuttlefish)." >&2
    exit 1
fi

if [[ -z "$IMAGE_ID" && "$PLATFORM" == "redroid" ]]; then
    IMAGE_ID="${REDROID_GOLDEN_IMAGE_ID:-}"
fi
if [[ -z "$IMAGE_ID" && "$PLATFORM" == "cuttlefish" ]]; then
    IMAGE_ID="${CUTTLEFISH_GOLDEN_IMAGE_ID:-${GOLDEN_IMAGE_ID:-}}"
fi
if [[ -z "$IMAGE_ID" ]]; then
    echo "Golden image id required (--image-id or GOLDEN_IMAGE_ID / REDROID_GOLDEN_IMAGE_ID)." >&2
    exit 1
fi

if [[ -z "$OCPUS" ]]; then
    if [[ "$PLATFORM" == "redroid" ]]; then OCPUS="2"; else OCPUS="4"; fi
fi
if [[ -z "$MEMORY_GB" ]]; then
    if [[ "$PLATFORM" == "redroid" ]]; then MEMORY_GB="8"; else MEMORY_GB="24"; fi
fi

if [[ "$PARALLEL" -lt 1 ]]; then
    echo "--parallel must be >= 1" >&2
    exit 1
fi

OUT_FILE="/tmp/golden-fleet-${NAME_PREFIX}-$(date +%Y%m%d-%H%M%S).txt"
touch "$OUT_FILE"

deploy_one() {
    local idx="$1"
    local name="${NAME_PREFIX}-${idx}"
    local cmd=(
        "$SCRIPT_DIR/deploy-from-golden.sh"
        --name "$name"
        --image-id "$IMAGE_ID"
        --platform "$PLATFORM"
        --ocpus "$OCPUS"
        --memory "$MEMORY_GB"
    )
    [[ "$WAIT_CHECK" == "true" ]] && cmd+=(--wait-check)
    [[ "$RUN_TESTS" == "true" ]] && cmd+=(--run-tests)

    echo "[fleet] deploying $name ..."
    "${cmd[@]}"

    local info_file="/tmp/instance-${name}.json"
    local ip=""
    if [[ -f "$info_file" ]]; then
        ip="$(python3 - <<'PY' "$info_file"
import json,sys
with open(sys.argv[1]) as f:
    d=json.load(f)
print(d.get("public_ip",""))
PY
)"
    fi
    echo "${name}|${ip}" >> "$OUT_FILE"

    if [[ "$VERIFY_INGEST" == "true" && "$PLATFORM" == "cuttlefish" && -n "$ip" ]]; then
        echo "[fleet] verifying ingest for $name ($ip) ..."
        "$SCRIPT_DIR/verify-cuttlefish-ingest.sh" --vm "$ip" || true
    fi
    if [[ "$VERIFY_INGEST" != "true" && "$PLATFORM" == "redroid" && -n "$ip" && "$WAIT_CHECK" == "true" ]]; then
        echo "[fleet] verifying Redroid phone for $name ($ip) ..."
        "$SCRIPT_DIR/verify-redroid-phone.sh" --vm "$ip" || true
    fi
}

running_jobs() {
    jobs -r | wc -l | tr -d ' '
}

for i in $(seq 1 "$COUNT"); do
    deploy_one "$i" &
    while [[ "$(running_jobs)" -ge "$PARALLEL" ]]; do
        sleep 1
    done
done
wait

echo ""
echo "Fleet deployment complete."
echo "Results: $OUT_FILE"
echo "Format: instance_name|public_ip"
