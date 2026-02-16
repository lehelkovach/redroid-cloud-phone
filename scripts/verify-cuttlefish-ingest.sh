#!/bin/bash
# verify-cuttlefish-ingest.sh
# Verifies Cuttlefish ingest pipeline for both video and audio bridge paths.
#
# Checks:
# 1) Phase 1 runtime: instance up, adb, boot, camera service present
# 2) Phase 2 ingest: RTMP -> bridge -> front/back video sinks + mic audio sink
#
# Usage:
#   ./scripts/verify-cuttlefish-ingest.sh [OPTIONS] [VM_HOST]
#
# Options:
#   --local                     Run locally
#   --vm HOST                   Run remotely via SSH
#   --ssh-user USER             SSH user (default: ubuntu)
#   --ssh-key PATH              SSH key (default: ~/.ssh/redroid_oci)
#   --instance-name NAME        CVD instance name (default: cvd-arm64-1)
#   --webrtc-port PORT          WebRTC signaling port (default: 8443)
#   --duration SEC              Stream test duration (default: 20)
#   --keep-artifacts            Keep test artifacts
#   --help                      Show help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VM_HOST=""
RUN_MODE="local"
SSH_USER="ubuntu"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/redroid_oci}"
INSTANCE_NAME="cvd-arm64-1"
WEBRTC_PORT="8443"
DURATION="20"
KEEP_ARTIFACTS="false"

usage() {
    cat <<'EOF'
Usage:
  ./scripts/verify-cuttlefish-ingest.sh [OPTIONS] [VM_HOST]

Options:
  --local                     Run locally
  --vm HOST                   Run remotely via SSH
  --ssh-user USER             SSH user (default: ubuntu)
  --ssh-key PATH              SSH key (default: ~/.ssh/redroid_oci)
  --instance-name NAME        CVD instance name (default: cvd-arm64-1)
  --webrtc-port PORT          WebRTC signaling port (default: 8443)
  --duration SEC              Stream test duration (default: 20)
  --keep-artifacts            Keep test artifacts
  --help                      Show help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --local) RUN_MODE="local"; VM_HOST=""; shift ;;
        --vm) VM_HOST="${2:-}"; RUN_MODE="remote"; shift 2 ;;
        --ssh-user) SSH_USER="${2:-ubuntu}"; shift 2 ;;
        --ssh-key) SSH_KEY="${2:-$HOME/.ssh/redroid_oci}"; shift 2 ;;
        --instance-name) INSTANCE_NAME="${2:-$INSTANCE_NAME}"; shift 2 ;;
        --webrtc-port) WEBRTC_PORT="${2:-$WEBRTC_PORT}"; shift 2 ;;
        --duration) DURATION="${2:-$DURATION}"; shift 2 ;;
        --keep-artifacts) KEEP_ARTIFACTS="true"; shift ;;
        --help|-h) usage; exit 0 ;;
        -*)
            echo "Unknown option: $1" >&2
            usage
            exit 1
            ;;
        *)
            if [[ -z "$VM_HOST" ]]; then
                VM_HOST="$1"
                RUN_MODE="remote"
            fi
            shift
            ;;
    esac
done

COMMON_ARGS=()
if [[ "$RUN_MODE" == "remote" ]]; then
    COMMON_ARGS+=(--vm "$VM_HOST" --ssh-user "$SSH_USER" --ssh-key "$SSH_KEY")
else
    COMMON_ARGS+=(--local)
fi

echo "=== [1/2] Phase 1 runtime validation ==="
"$SCRIPT_DIR/cuttlefish-phase1-validate.sh" \
    "${COMMON_ARGS[@]}" \
    --instance-name "$INSTANCE_NAME" \
    --webrtc-port "$WEBRTC_PORT"

echo ""
echo "=== [2/2] RTMP ingest A/V bridge validation ==="
BRIDGE_ARGS=("${COMMON_ARGS[@]}" --duration "$DURATION")
if [[ "$KEEP_ARTIFACTS" == "true" ]]; then
    BRIDGE_ARGS+=(--keep-artifacts)
fi
"$SCRIPT_DIR/test-cuttlefish-rtmp-bridge.sh" "${BRIDGE_ARGS[@]}"

echo ""
echo "Verification complete: runtime + ingest A/V bridge checks passed."
