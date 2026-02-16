#!/bin/bash
# cuttlefish-phase1-validate.sh
# Validation gates for Cuttlefish ARM64 Phase 1.
#
# Gates:
# - CVD instance is present
# - ADB connectivity works
# - Android boot completed
# - WebRTC signaling port reachable
# - Camera service visible; front/back-like camera metadata detectable
#
# Usage:
#   ./scripts/cuttlefish-phase1-validate.sh [OPTIONS] [VM_HOST]
#
# Options:
#   --local                     Run on local host
#   --vm HOST                   Run remotely via SSH
#   --ssh-user USER             SSH user (default: ubuntu)
#   --ssh-key PATH              SSH key (default: ~/.ssh/redroid_oci)
#   --instance-name NAME        CVD instance name (default: cvd-arm64-1)
#   --webrtc-port PORT          WebRTC port (default: 8443)
#   --help                      Show help

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

ENV_VM_HOST="${VM_HOST:-${DEV_INSTANCE:-}}"
VM_HOST=""
RUN_MODE="local"

SSH_USER="ubuntu"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/redroid_oci}"
INSTANCE_NAME="cvd-arm64-1"
WEBRTC_PORT="8443"

PASS=0
FAIL=0
WARN=0

usage() {
    sed -n '1,34p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --local)
            RUN_MODE="local"
            VM_HOST=""
            shift
            ;;
        --vm)
            VM_HOST="${2:-}"
            RUN_MODE="remote"
            shift 2
            ;;
        --ssh-user)
            SSH_USER="${2:-ubuntu}"
            shift 2
            ;;
        --ssh-key)
            SSH_KEY="${2:-$HOME/.ssh/redroid_oci}"
            shift 2
            ;;
        --instance-name)
            INSTANCE_NAME="${2:-cvd-arm64-1}"
            shift 2
            ;;
        --webrtc-port)
            WEBRTC_PORT="${2:-8443}"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
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

if [[ -z "$VM_HOST" && -n "$ENV_VM_HOST" ]]; then
    VM_HOST="$ENV_VM_HOST"
    RUN_MODE="remote"
fi

SSH_CMD=()
if [[ "$RUN_MODE" == "remote" && -n "$VM_HOST" ]]; then
    SSH_OPTS=(-o ConnectTimeout=10 -o StrictHostKeyChecking=no)
    [[ -f "$SSH_KEY" ]] && SSH_OPTS+=(-i "$SSH_KEY")
    SSH_CMD=(ssh "${SSH_OPTS[@]}" "${SSH_USER}@${VM_HOST}")
fi

run_cmd() {
    if [[ ${#SSH_CMD[@]} -gt 0 ]]; then
        "${SSH_CMD[@]}" "$@"
    else
        "$@"
    fi
}

run_shell() {
    if [[ ${#SSH_CMD[@]} -gt 0 ]]; then
        "${SSH_CMD[@]}" "$1"
    else
        bash -c "$1"
    fi
}

pass() { echo -e "${GREEN}PASS${NC}: $1"; PASS=$((PASS + 1)); }
fail() { echo -e "${RED}FAIL${NC}: $1"; FAIL=$((FAIL + 1)); }
warn() { echo -e "${YELLOW}WARN${NC}: $1"; WARN=$((WARN + 1)); }

echo -e "${BLUE}=========================================="
echo "Cuttlefish Phase 1 Validation"
echo "==========================================${NC}"
[[ -n "$VM_HOST" ]] && echo "Target: ${SSH_USER}@${VM_HOST}" || echo "Target: localhost"
echo "Instance: $INSTANCE_NAME"
echo ""

if run_shell "command -v cvd >/dev/null 2>&1"; then
    pass "cvd command available"
else
    fail "cvd command missing"
fi

if run_shell "command -v adb >/dev/null 2>&1"; then
    pass "adb command available"
else
    fail "adb command missing"
fi

if run_shell "cvd fleet 2>/dev/null | grep -q $INSTANCE_NAME"; then
    pass "Instance appears in cvd fleet"
else
    fail "Instance not found in cvd fleet"
fi

# Discover adb serial from cvd output if possible.
ADB_SERIAL="$(run_shell "cvd fleet 2>/dev/null | awk '/$INSTANCE_NAME/ {print \$2}' | head -1" || true)"
if [[ -z "$ADB_SERIAL" ]]; then
    # Fallback often used by local launch_cvd.
    ADB_SERIAL="127.0.0.1:6520"
    warn "Could not auto-detect adb serial from cvd fleet; using fallback $ADB_SERIAL"
fi

run_shell "adb connect $ADB_SERIAL >/dev/null 2>&1 || true"
sleep 2

if run_shell "adb -s $ADB_SERIAL get-state 2>/dev/null | grep -q device"; then
    pass "ADB connected ($ADB_SERIAL)"
else
    fail "ADB not connected ($ADB_SERIAL)"
fi

BOOT="$(run_shell "adb -s $ADB_SERIAL shell getprop sys.boot_completed 2>/dev/null | tr -d '\r'" || true)"
if [[ "$BOOT" == "1" ]]; then
    pass "Android boot completed"
else
    warn "Android boot not complete yet (sys.boot_completed='${BOOT:-}')"
fi

if run_shell "ss -ltn 2>/dev/null | grep -q :$WEBRTC_PORT"; then
    pass "WebRTC port listening on host ($WEBRTC_PORT)"
else
    fail "WebRTC port not listening ($WEBRTC_PORT)"
fi

CAM_DUMP="$(run_shell "adb -s $ADB_SERIAL shell dumpsys media.camera 2>/dev/null || true")"
if echo "$CAM_DUMP" | grep -q "Number of camera devices"; then
    CAM_COUNT="$(echo "$CAM_DUMP" | awk -F: '/Number of camera devices/ {gsub(/ /, \"\", \$2); print \$2; exit}')"
    if [[ -n "$CAM_COUNT" && "$CAM_COUNT" != "0" ]]; then
        pass "Camera service reports devices (count=$CAM_COUNT)"
    else
        fail "Camera service reports zero devices"
    fi
else
    fail "Could not read camera service state (dumpsys media.camera)"
fi

if echo "$CAM_DUMP" | grep -Eiq "front|back|facing"; then
    pass "Camera metadata includes facing hints (front/back)"
else
    warn "No explicit front/back facing metadata found in dumpsys output"
fi

echo ""
echo -e "${BLUE}Summary:${NC} PASS=$PASS FAIL=$FAIL WARN=$WARN"
if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
