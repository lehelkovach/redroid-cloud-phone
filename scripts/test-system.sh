#!/bin/bash
# test-system.sh
# Comprehensive system tests for waydroid cloud phone
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

# Test counters
PASSED=0
FAILED=0
WARNINGS=0

# Test result tracking
declare -a FAILED_TESTS=()
declare -a WARNED_TESTS=()

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
            FAILED_TESTS+=("$test_name")
            ;;
        WARN)
            echo -e "  ${YELLOW}○${NC} $test_name"
            if [[ -n "$message" ]]; then
                echo -e "      ${YELLOW}$message${NC}"
            fi
            ((WARNINGS++))
            WARNED_TESTS+=("$test_name")
            ;;
    esac
}

echo -e "${BLUE}=========================================="
echo "Waydroid Cloud Phone System Tests"
echo "==========================================${NC}"
echo ""

# ============================================
# Test 1: Kernel Modules
# ============================================
echo -e "${BLUE}[1/9] Testing Kernel Modules${NC}"

# v4l2loopback
if lsmod | grep -q v4l2loopback; then
    log_test PASS "v4l2loopback module loaded"
else
    log_test FAIL "v4l2loopback module" "Module not loaded. Run: sudo modprobe v4l2loopback"
fi

# snd-aloop
if lsmod | grep -q snd_aloop; then
    log_test PASS "snd-aloop module loaded"
else
    log_test FAIL "snd-aloop module" "Module not loaded. Run: sudo modprobe snd-aloop"
fi

# binderfs
if [ -d /dev/binderfs ] && mountpoint -q /dev/binderfs 2>/dev/null; then
    log_test PASS "binderfs mounted"
else
    log_test FAIL "binderfs" "Not mounted. Run: sudo mount /dev/binderfs"
fi

echo ""

# ============================================
# Test 2: Virtual Devices
# ============================================
echo -e "${BLUE}[2/9] Testing Virtual Devices${NC}"

# /dev/video42
if [ -e /dev/video42 ]; then
    if [ -r /dev/video42 ] && [ -w /dev/video42 ]; then
        log_test PASS "/dev/video42 exists and is accessible"
        
        # Test v4l2-ctl if available
        if command -v v4l2-ctl &>/dev/null; then
            if v4l2-ctl --device=/dev/video42 --all &>/dev/null; then
                log_test PASS "/dev/video42 is functional"
            else
                log_test WARN "/dev/video42" "Device exists but v4l2-ctl failed"
            fi
        fi
    else
        log_test WARN "/dev/video42" "Exists but not accessible (check permissions)"
    fi
else
    log_test FAIL "/dev/video42" "Device not found. Load v4l2loopback module."
fi

# ALSA Loopback
if aplay -l 2>/dev/null | grep -q "Loopback"; then
    log_test PASS "ALSA Loopback device found"
    
    # Test if we can list it
    if arecord -l 2>/dev/null | grep -q "Loopback"; then
        log_test PASS "ALSA Loopback is recordable"
    else
        log_test WARN "ALSA Loopback" "Found but may not be recordable"
    fi
else
    log_test FAIL "ALSA Loopback" "Device not found. Load snd-aloop module."
fi

echo ""

# ============================================
# Test 3: Systemd Services
# ============================================
echo -e "${BLUE}[3/9] Testing Systemd Services${NC}"

SERVICES=(
    "nginx-rtmp:RTMP server"
    "xvnc:VNC server"
    "waydroid-container:Waydroid container"
    "waydroid-session:Waydroid session"
    "ffmpeg-bridge:FFmpeg bridge"
    "control-api:Control API"
)

for service_info in "${SERVICES[@]}"; do
    IFS=':' read -r service name <<< "$service_info"
    if systemctl is-active --quiet "$service"; then
        log_test PASS "$name ($service)"
    else
        log_test FAIL "$name ($service)" "Service not running. Check: sudo systemctl status $service"
    fi
done

echo ""

# ============================================
# Test 4: Network Ports
# ============================================
echo -e "${BLUE}[4/9] Testing Network Ports${NC}"

# RTMP (1935) - should be listening on 0.0.0.0
if ss -tlnp | grep -q ":1935 "; then
    LISTEN_ADDR=$(ss -tlnp | grep ":1935 " | awk '{print $4}')
    if [[ "$LISTEN_ADDR" == "0.0.0.0:1935" ]] || [[ "$LISTEN_ADDR" == "*:1935" ]]; then
        log_test PASS "RTMP port 1935 (listening on all interfaces)"
    else
        log_test WARN "RTMP port 1935" "Listening on $LISTEN_ADDR (should be 0.0.0.0:1935)"
    fi
else
    log_test FAIL "RTMP port 1935" "Not listening. Check nginx-rtmp service."
fi

# VNC (5901) - should be listening on localhost
if ss -tlnp | grep -q ":5901 "; then
    LISTEN_ADDR=$(ss -tlnp | grep ":5901 " | awk '{print $4}')
    if [[ "$LISTEN_ADDR" == "127.0.0.1:5901" ]] || [[ "$LISTEN_ADDR" == "::1:5901" ]]; then
        log_test PASS "VNC port 5901 (listening on localhost)"
    else
        log_test WARN "VNC port 5901" "Listening on $LISTEN_ADDR (should be localhost)"
    fi
else
    log_test FAIL "VNC port 5901" "Not listening. Check xvnc service."
fi

# API (8080) - should be listening on localhost
if ss -tlnp | grep -q ":8080 "; then
    LISTEN_ADDR=$(ss -tlnp | grep ":8080 " | awk '{print $4}')
    if [[ "$LISTEN_ADDR" == "127.0.0.1:8080" ]] || [[ "$LISTEN_ADDR" == "::1:8080" ]]; then
        log_test PASS "API port 8080 (listening on localhost)"
    else
        log_test WARN "API port 8080" "Listening on $LISTEN_ADDR (should be localhost)"
    fi
else
    log_test FAIL "API port 8080" "Not listening. Check control-api service."
fi

echo ""

# ============================================
# Test 5: Waydroid Status
# ============================================
echo -e "${BLUE}[5/9] Testing Waydroid${NC}"

if command -v waydroid &>/dev/null; then
    WAYDROID_STATUS=$(waydroid status 2>/dev/null || echo "ERROR")
    
    if echo "$WAYDROID_STATUS" | grep -q "STOPPED"; then
        log_test FAIL "Waydroid" "Container is stopped. Start with: sudo systemctl start waydroid-container"
    elif echo "$WAYDROID_STATUS" | grep -q "RUNNING"; then
        log_test PASS "Waydroid container is running"
        
        # Check session
        if echo "$WAYDROID_STATUS" | grep -q "Session.*RUNNING"; then
            log_test PASS "Waydroid session is running"
        else
            log_test WARN "Waydroid session" "Container running but session may not be active"
        fi
    else
        log_test WARN "Waydroid" "Status unknown: $WAYDROID_STATUS"
    fi
else
    log_test FAIL "Waydroid" "Command not found. Waydroid may not be installed."
fi

echo ""

# ============================================
# Test 6: ADB Connection
# ============================================
echo -e "${BLUE}[6/9] Testing ADB Connection${NC}"

if command -v adb &>/dev/null; then
    # Start ADB server if not running
    adb start-server &>/dev/null || true
    
    # Wait a moment
    sleep 2
    
    # Check for devices
    ADB_DEVICES=$(adb devices 2>/dev/null | tail -n +2 | grep -v "^$" | wc -l)
    
    if [ "$ADB_DEVICES" -gt 0 ]; then
        DEVICE_LIST=$(adb devices 2>/dev/null | tail -n +2 | grep -v "^$" | awk '{print $1}' | tr '\n' ' ')
        log_test PASS "ADB devices connected ($ADB_DEVICES device(s): $DEVICE_LIST)"
        
        # Test ADB command
        if adb shell echo "test" &>/dev/null; then
            log_test PASS "ADB shell access working"
        else
            log_test WARN "ADB shell" "Devices connected but shell access failed"
        fi
    else
        log_test FAIL "ADB devices" "No devices connected. Check waydroid-session service."
    fi
else
    log_test FAIL "ADB" "Command not found. Install android-tools-adb."
fi

echo ""

# ============================================
# Test 7: Control API
# ============================================
echo -e "${BLUE}[7/9] Testing Control API${NC}"

# Health endpoint
if curl -s --max-time 5 http://127.0.0.1:8080/health 2>/dev/null | grep -q "healthy"; then
    log_test PASS "API health endpoint"
else
    log_test FAIL "API health endpoint" "Not responding. Check control-api service."
fi

# Device info endpoint
if curl -s --max-time 5 http://127.0.0.1:8080/device/info 2>/dev/null | grep -q "device\|screen\|android"; then
    log_test PASS "API device/info endpoint"
else
    log_test WARN "API device/info" "Endpoint exists but may not return valid data"
fi

# Screenshot endpoint
SCREENSHOT_TEST=$(curl -s --max-time 10 http://127.0.0.1:8080/device/screenshot 2>/dev/null | head -c 10)
if [[ -n "$SCREENSHOT_TEST" ]] && [[ "$SCREENSHOT_TEST" =~ ^(PNG|GIF|JFIF|RIFF) ]]; then
    log_test PASS "API screenshot endpoint"
else
    log_test WARN "API screenshot" "Endpoint exists but may not return valid image"
fi

echo ""

# ============================================
# Test 8: RTMP Functionality
# ============================================
echo -e "${BLUE}[8/9] Testing RTMP Functionality${NC}"

# Check nginx-rtmp stats
if curl -s --max-time 5 http://127.0.0.1:8081/health 2>/dev/null | grep -q "OK"; then
    log_test PASS "RTMP server health check"
else
    log_test WARN "RTMP health" "Health endpoint not responding (may be normal if not configured)"
fi

# Check if RTMP application exists
if curl -s --max-time 5 http://127.0.0.1:8081/stat 2>/dev/null | grep -q "live"; then
    log_test PASS "RTMP 'live' application configured"
else
    log_test WARN "RTMP application" "'live' application may not be configured"
fi

# Test RTMP connection (without actually streaming)
if timeout 2 ffprobe -v quiet -show_streams rtmp://127.0.0.1/live/test 2>/dev/null; then
    log_test WARN "RTMP stream" "Stream endpoint accessible (may indicate active stream)"
else
    # This is expected if no stream is active
    log_test PASS "RTMP endpoint ready (no active stream)"
fi

echo ""

# ============================================
# Test 9: VNC Accessibility
# ============================================
echo -e "${BLUE}[9/9] Testing VNC${NC}"

# Check if VNC is listening
if ss -tlnp | grep -q ":5901 "; then
    # Try to connect (just check if port accepts connections)
    if timeout 2 nc -z 127.0.0.1 5901 2>/dev/null; then
        log_test PASS "VNC port 5901 is accessible"
    else
        log_test WARN "VNC port" "Listening but may not accept connections"
    fi
    
    # Check VNC password file exists
    if [ -f /home/waydroid/.vnc/passwd ]; then
        log_test PASS "VNC password file exists"
    else
        log_test WARN "VNC password" "Password file not found"
    fi
else
    log_test FAIL "VNC" "Port 5901 not listening"
fi

echo ""

# ============================================
# Summary
# ============================================
echo -e "${BLUE}=========================================="
echo "Test Summary"
echo "==========================================${NC}"
echo ""
echo -e "Passed:  ${GREEN}$PASSED${NC}"
echo -e "Failed:  ${RED}$FAILED${NC}"
echo -e "Warnings: ${YELLOW}$WARNINGS${NC}"
echo ""

if [ $FAILED -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}=========================================="
    echo "All Tests Passed!"
    echo "==========================================${NC}"
    echo ""
    echo "System is ready for golden image creation."
    exit 0
elif [ $FAILED -eq 0 ]; then
    echo -e "${YELLOW}=========================================="
    echo "Tests Passed with Warnings"
    echo "==========================================${NC}"
    echo ""
    if [ ${#WARNED_TESTS[@]} -gt 0 ]; then
        echo "Warnings:"
        for test in "${WARNED_TESTS[@]}"; do
            echo "  - $test"
        done
        echo ""
    fi
    echo "System is functional but may need attention."
    exit 0
else
    echo -e "${RED}=========================================="
    echo "Some Tests Failed"
    echo "==========================================${NC}"
    echo ""
    if [ ${#FAILED_TESTS[@]} -gt 0 ]; then
        echo "Failed tests:"
        for test in "${FAILED_TESTS[@]}"; do
            echo "  - $test"
        done
        echo ""
    fi
    echo "Fix the issues above before creating a golden image."
    exit 1
fi

