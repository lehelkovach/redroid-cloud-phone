#!/bin/bash
# test-full-suite.sh
# Comprehensive test suite for redroid cloud phone
# Tests installation, automation, streaming, and service recovery
#
# Usage:
#   Local:  sudo ./test-full-suite.sh
#   Remote: ./test-full-suite.sh <PUBLIC_IP> [SSH_KEY]

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

REMOTE_MODE=false
PUBLIC_IP=""
SSH_KEY="${HOME}/.ssh/redroid_oci"
SSH_USER="ubuntu"

if [[ $# -ge 1 ]] && [[ "$1" != "--local" ]]; then
    REMOTE_MODE=true
    PUBLIC_IP="$1"
    SSH_KEY="${2:-${HOME}/.ssh/redroid_oci}"
fi

run_local() {
    bash -lc "$1"
}

run_remote() {
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" "$1"
}

run_remote_sudo() {
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" "sudo $1"
}

section() {
    echo -e "${BLUE}=========================================="
    echo "$1"
    echo -e "==========================================${NC}"
}

section "Redroid Cloud Phone - Full Test Suite"
if [[ "$REMOTE_MODE" == true ]]; then
    echo "Mode: Remote (${PUBLIC_IP})"
    echo "SSH Key: ${SSH_KEY}"
else
    echo "Mode: Local"
fi

echo ""

section "1) System Health"
if [[ "$REMOTE_MODE" == true ]]; then
    run_remote_sudo "/opt/redroid-scripts/health-check.sh" || true
else
    sudo /opt/redroid-scripts/health-check.sh || true
fi

echo ""

section "2) Connectivity + API"
if [[ "$REMOTE_MODE" == true ]]; then
    PUBLIC_IP="$PUBLIC_IP" SSH_KEY="$SSH_KEY" SSH_USER="$SSH_USER" python3 ./tests/test_connectivity.py || true
    python3 ./tests/test_agent_api.py --api-url "http://${PUBLIC_IP}:8080" || true
else
    python3 ./tests/test_agent_api.py --api-url "http://127.0.0.1:8080" || true
fi

echo ""

section "3) RTMP Mock Stream"
if [[ "$REMOTE_MODE" == true ]]; then
    run_remote "ffmpeg -hide_banner -loglevel error -re \
      -f lavfi -i testsrc2=size=1080x1920:rate=15 \
      -f lavfi -i sine=frequency=440:sample_rate=44100 \
      -t 5 -c:v libx264 -preset veryfast -pix_fmt yuv420p \
      -c:a aac -ar 44100 -b:a 128k \
      -f flv rtmp://127.0.0.1/live/cam" || true
else
    ffmpeg -hide_banner -loglevel error -re \
      -f lavfi -i testsrc2=size=1080x1920:rate=15 \
      -f lavfi -i sine=frequency=440:sample_rate=44100 \
      -t 5 -c:v libx264 -preset veryfast -pix_fmt yuv420p \
      -c:a aac -ar 44100 -b:a 128k \
      -f flv rtmp://127.0.0.1/live/cam || true
fi

echo ""

section "4) Service Recovery"
if [[ "$REMOTE_MODE" == true ]]; then
    run_remote_sudo "systemctl restart redroid-container" || true
    run_remote_sudo "systemctl restart ffmpeg-bridge" || true
    run_remote_sudo "systemctl restart control-api" || true
else
    sudo systemctl restart redroid-container || true
    sudo systemctl restart ffmpeg-bridge || true
    sudo systemctl restart control-api || true
fi

echo ""

echo -e "${GREEN}Test suite finished${NC}"
