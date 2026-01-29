#!/bin/bash
# test-system.sh
# Comprehensive system tests for redroid cloud phone
# Run this before creating a golden image to ensure everything works
#
# Usage: sudo ./test-system.sh [--verbose]

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

VERBOSE=false
if [[ "${1:-}" == "--verbose" ]] || [[ "${1:-}" == "-v" ]]; then
    VERBOSE=true
fi

PASSED=0
FAILED=0
WARNINGS=0

log_test() {
    local status=$1
    local test_name=$2
    local message="${3:-}"

    case $status in
        PASS)
            echo -e "  ${GREEN}✓${NC} $test_name"
            ((PASSED++))
            ;;
        FAIL)
            echo -e "  ${RED}✗${NC} $test_name"
            if [[ -n "$message" ]]; then
                echo -e "      ${RED}$message${NC}"
            fi
            ((FAILED++))
            ;;
        WARN)
            echo -e "  ${YELLOW}○${NC} $test_name"
            if [[ -n "$message" ]]; then
                echo -e "      ${YELLOW}$message${NC}"
            fi
            ((WARNINGS++))
            ;;
    esac
}

echo -e "${BLUE}=========================================="
echo "Redroid Cloud Phone System Tests"
echo "==========================================${NC}"
echo ""

# 1) Kernel modules
echo -e "${BLUE}[1/6] Kernel Modules${NC}"
if lsmod | grep -q v4l2loopback; then
    log_test PASS "v4l2loopback module loaded"
else
    log_test FAIL "v4l2loopback module" "Run: sudo modprobe v4l2loopback"
fi

if lsmod | grep -q snd_aloop; then
    log_test PASS "snd-aloop module loaded"
else
    log_test FAIL "snd-aloop module" "Run: sudo modprobe snd-aloop"
fi

if mountpoint -q /dev/binderfs 2>/dev/null; then
    log_test PASS "binderfs mounted"
else
    log_test FAIL "binderfs" "Run: sudo mount /dev/binderfs"
fi

echo ""

# 2) Docker/Redroid
echo -e "${BLUE}[2/6] Docker + Redroid${NC}"
if systemctl is-active --quiet docker; then
    log_test PASS "docker service running"
else
    log_test FAIL "docker service" "Run: sudo systemctl start docker"
fi

if docker ps --format '{{.Names}}' | grep -q '^redroid$'; then
    log_test PASS "redroid container running"
else
    log_test FAIL "redroid container" "Run: sudo systemctl start redroid-container"
fi

echo ""

# 3) Devices
echo -e "${BLUE}[3/6] Virtual Devices${NC}"
if [ -e /dev/video42 ]; then
    log_test PASS "/dev/video42 exists"
else
    log_test FAIL "/dev/video42" "v4l2loopback not loaded?"
fi

if aplay -l 2>/dev/null | grep -q Loopback; then
    log_test PASS "ALSA Loopback present"
else
    log_test FAIL "ALSA Loopback" "snd-aloop not loaded?"
fi

echo ""

# 4) Services
echo -e "${BLUE}[4/6] Services${NC}"
for svc in nginx-rtmp ffmpeg-bridge control-api redroid-container; do
    if systemctl is-active --quiet "$svc"; then
        log_test PASS "$svc running"
    else
        log_test WARN "$svc" "Start: sudo systemctl start $svc"
    fi
 done

echo ""

# 5) Ports
echo -e "${BLUE}[5/6] Ports${NC}"
for p in 1935 5555 5900 8080; do
    if ss -tlnp | grep -q ":${p} "; then
        log_test PASS "port $p listening"
    else
        log_test WARN "port $p" "Not listening"
    fi
 done

echo ""

# 6) API + ADB
echo -e "${BLUE}[6/6] API + ADB${NC}"
if curl -s http://127.0.0.1:8080/health | grep -q 'healthy'; then
    log_test PASS "API /health ok"
else
    log_test WARN "API /health" "Check control-api service"
fi

if command -v adb &>/dev/null; then
    if adb devices | tail -n +2 | grep -q 'device'; then
        log_test PASS "adb device connected"
    else
        log_test WARN "adb device" "adb connect 127.0.0.1:5555"
    fi
else
    log_test WARN "adb" "adb not installed"
fi

echo ""
echo -e "${BLUE}=========================================="
echo "Summary"
echo "==========================================${NC}"
echo "Passed: $PASSED | Failed: $FAILED | Warnings: $WARNINGS"

echo ""
if [[ $FAILED -gt 0 ]]; then
    exit 1
fi
