#!/bin/bash
# test-cuttlefish-rtmp-bridge.sh
# Run-level validation for OBS RTMP bridge flow:
# source -> nginx-rtmp -> cuttlefish-rtmp-bridge -> front/back sink captures.
#
# Usage:
#   ./scripts/test-cuttlefish-rtmp-bridge.sh [OPTIONS] [VM_HOST]
#
# Options:
#   --local                     Run locally
#   --vm HOST                   Run remotely via SSH
#   --ssh-user USER             SSH user (default: ubuntu)
#   --ssh-key PATH              SSH key (default: ~/.ssh/android_arm_cloud_phone_oci)
#   --duration SEC              Stream duration (default: 20)
#   --rtmp-url URL              Input RTMP URL (default: rtmp://127.0.0.1/live/cam)
#   --front-sink URI            Front sink URI (default: file:/tmp/cf-front.ts)
#   --back-sink URI             Back sink URI (default: file:/tmp/cf-back.ts)
#   --mic-sink URI              Mic sink URI (default: file:/tmp/cf-mic.ts)
#   --keep-artifacts            Keep generated files/logs
#   --help                      Show help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ENV_VM_HOST="${VM_HOST:-${DEV_INSTANCE:-}}"
VM_HOST=""
RUN_MODE="local"
SSH_USER="ubuntu"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/android_arm_cloud_phone_oci}"

DURATION="20"
RTMP_URL="rtmp://127.0.0.1/live/cam"
FRONT_SINK="file:/tmp/cf-front.ts"
BACK_SINK="file:/tmp/cf-back.ts"
MIC_SINK="file:/tmp/cf-mic.ts"
KEEP_ARTIFACTS="false"

PASS=0
FAIL=0
WARN=0

usage() {
    cat <<'EOF'
Usage:
  ./scripts/test-cuttlefish-rtmp-bridge.sh [OPTIONS] [VM_HOST]

Options:
  --local                     Run locally
  --vm HOST                   Run remotely via SSH
  --ssh-user USER             SSH user (default: ubuntu)
  --ssh-key PATH              SSH key (default: ~/.ssh/android_arm_cloud_phone_oci)
  --duration SEC              Stream duration (default: 20)
  --rtmp-url URL              Input RTMP URL (default: rtmp://127.0.0.1/live/cam)
  --front-sink URI            Front sink URI (default: file:/tmp/cf-front.ts)
  --back-sink URI             Back sink URI (default: file:/tmp/cf-back.ts)
  --mic-sink URI              Mic sink URI (default: file:/tmp/cf-mic.ts)
  --keep-artifacts            Keep generated files/logs
  --help                      Show help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --local) RUN_MODE="local"; VM_HOST=""; shift ;;
        --vm) VM_HOST="${2:-}"; RUN_MODE="remote"; shift 2 ;;
        --ssh-user) SSH_USER="${2:-ubuntu}"; shift 2 ;;
        --ssh-key) SSH_KEY="${2:-$HOME/.ssh/android_arm_cloud_phone_oci}"; shift 2 ;;
        --duration) DURATION="${2:-20}"; shift 2 ;;
        --rtmp-url) RTMP_URL="${2:-$RTMP_URL}"; shift 2 ;;
        --front-sink) FRONT_SINK="${2:-$FRONT_SINK}"; shift 2 ;;
        --back-sink) BACK_SINK="${2:-$BACK_SINK}"; shift 2 ;;
        --mic-sink) MIC_SINK="${2:-$MIC_SINK}"; shift 2 ;;
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

echo -e "${BLUE}==========================================${NC}"
echo -e "${BLUE}Cuttlefish RTMP Bridge Test${NC}"
echo -e "${BLUE}==========================================${NC}"
[[ -n "$VM_HOST" ]] && echo "Target: ${SSH_USER}@${VM_HOST}" || echo "Target: localhost"
echo "Duration: ${DURATION}s"
echo "RTMP URL: $RTMP_URL"
echo "Front sink: $FRONT_SINK"
echo "Back sink: $BACK_SINK"
echo "Mic sink: $MIC_SINK"
echo ""

run_shell "command -v ffmpeg >/dev/null 2>&1" && pass "ffmpeg available" || fail "ffmpeg missing"
run_shell "command -v ffprobe >/dev/null 2>&1" && pass "ffprobe available" || fail "ffprobe missing"

if run_shell "curl -s --max-time 3 http://127.0.0.1:8081/health 2>/dev/null | grep -q OK"; then
    pass "nginx-rtmp health endpoint responds OK"
else
    warn "nginx-rtmp health check did not return OK"
fi

run_shell "rm -f /tmp/cf-front.ts /tmp/cf-back.ts /tmp/cf-mic.ts /tmp/cf-bridge-test.log /tmp/cf-source-test.log"

BRIDGE_CMD="timeout $((DURATION + 20)) /bin/bash \"$SCRIPT_DIR/cuttlefish-rtmp-bridge.sh\" --rtmp-url \"$RTMP_URL\" --front-sink \"$FRONT_SINK\" --back-sink \"$BACK_SINK\" --mic-sink \"$MIC_SINK\" --log-dir /tmp/cf-bridge-test"
SOURCE_CMD="timeout ${DURATION} ffmpeg -hide_banner -loglevel warning -re -f lavfi -i testsrc2=size=1280x720:rate=30 -f lavfi -i sine=frequency=880:sample_rate=44100 -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p -g 60 -keyint_min 60 -c:a aac -ar 44100 -b:a 128k -shortest -f flv \"$RTMP_URL\""

run_shell "$BRIDGE_CMD >/tmp/cf-bridge-test.log 2>&1 & echo \$! > /tmp/cf-bridge-test.pid"
sleep 4
run_shell "$SOURCE_CMD >/tmp/cf-source-test.log 2>&1 || true"
sleep 4

if run_shell "test -f /tmp/cf-front.ts"; then
    FRONT_SIZE="$(run_shell "stat -c%s /tmp/cf-front.ts 2>/dev/null || echo 0" | tr -d '\r')"
    if [[ "${FRONT_SIZE:-0}" -gt 10000 ]]; then
        pass "front sink produced data (${FRONT_SIZE} bytes)"
    else
        fail "front sink output too small (${FRONT_SIZE} bytes)"
    fi
else
    fail "front sink file not produced"
fi

if run_shell "test -f /tmp/cf-back.ts"; then
    BACK_SIZE="$(run_shell "stat -c%s /tmp/cf-back.ts 2>/dev/null || echo 0" | tr -d '\r')"
    if [[ "${BACK_SIZE:-0}" -gt 10000 ]]; then
        pass "back sink produced data (${BACK_SIZE} bytes)"
    else
        fail "back sink output too small (${BACK_SIZE} bytes)"
    fi
else
    fail "back sink file not produced"
fi

if run_shell "ffprobe -v error -show_streams /tmp/cf-front.ts 2>/dev/null | grep -q codec_type=video"; then
    pass "front sink contains a video stream"
else
    fail "front sink missing valid video stream"
fi

if run_shell "ffprobe -v error -show_streams /tmp/cf-back.ts 2>/dev/null | grep -q codec_type=video"; then
    pass "back sink contains a video stream"
else
    fail "back sink missing valid video stream"
fi

if run_shell "test -f /tmp/cf-mic.ts"; then
    MIC_SIZE="$(run_shell "stat -c%s /tmp/cf-mic.ts 2>/dev/null || echo 0" | tr -d '\r')"
    if [[ "${MIC_SIZE:-0}" -gt 10000 ]]; then
        pass "mic sink produced data (${MIC_SIZE} bytes)"
    else
        fail "mic sink output too small (${MIC_SIZE} bytes)"
    fi
else
    fail "mic sink file not produced"
fi

if run_shell "ffprobe -v error -show_streams /tmp/cf-mic.ts 2>/dev/null | grep -q codec_type=audio"; then
    pass "mic sink contains an audio stream"
else
    fail "mic sink missing valid audio stream"
fi

run_shell "if [ -f /tmp/cf-bridge-test.pid ]; then kill \$(cat /tmp/cf-bridge-test.pid) 2>/dev/null || true; fi"

if [[ "$KEEP_ARTIFACTS" == "true" ]]; then
    pass "artifacts kept under /tmp (cf-front.ts, cf-back.ts, cf-mic.ts, cf-bridge-test.log)"
else
    run_shell "rm -f /tmp/cf-front.ts /tmp/cf-back.ts /tmp/cf-mic.ts /tmp/cf-source-test.log /tmp/cf-bridge-test.pid"
fi

echo ""
echo -e "${BLUE}Summary:${NC} PASS=$PASS FAIL=$FAIL WARN=$WARN"
if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
