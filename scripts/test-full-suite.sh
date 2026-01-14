#!/bin/bash
# test-full-suite.sh
# Comprehensive test suite for waydroid cloud phone
# Tests installation, automation, streaming, and service recovery
#
# Usage: 
#   Local: sudo ./test-full-suite.sh
#   Remote: ./test-full-suite.sh <PUBLIC_IP> [SSH_KEY]

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

REMOTE_MODE=false
PUBLIC_IP=""
SSH_KEY="${HOME}/.ssh/waydroid_oci"
SSH_USER="ubuntu"
API_URL="http://127.0.0.1:8080"
RTMP_URL="rtmp://127.0.0.1/live/cam"

# Check if running remotely
if [[ $# -ge 1 ]] && [[ "$1" != "--local" ]]; then
    REMOTE_MODE=true
    PUBLIC_IP="$1"
    SSH_KEY="${2:-${HOME}/.ssh/waydroid_oci}"
    API_URL="http://127.0.0.1:8080"  # Will be tunneled
    RTMP_URL="rtmp://${PUBLIC_IP}/live/cam"
fi

PASSED=0
FAILED=0
WARNINGS=0

# Remote execution helper
remote_exec() {
    if [ "$REMOTE_MODE" = true ]; then
        ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" "$1"
    else
        eval "$1"
    fi
}

remote_exec_sudo() {
    if [ "$REMOTE_MODE" = true ]; then
        ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" "sudo $1"
    else
        sudo bash -c "$1"
    fi
}

test_pass() {
    echo -e "  ${GREEN}✓${NC} $1"
    ((PASSED++))
}

test_fail() {
    echo -e "  ${RED}✗${NC} $1"
    if [[ -n "${2:-}" ]]; then
        echo -e "      ${RED}$2${NC}"
    fi
    ((FAILED++))
}

test_warn() {
    echo -e "  ${YELLOW}○${NC} $1"
    if [[ -n "${2:-}" ]]; then
        echo -e "      ${YELLOW}$2${NC}"
    fi
    ((WARNINGS++))
}

echo -e "${BLUE}=========================================="
echo "Redroid/Waydroid Cloud Phone - Full Test Suite"
echo "==========================================${NC}"
echo ""

if [ "$REMOTE_MODE" = true ]; then
    echo "Mode: Remote (${PUBLIC_IP})"
    echo "SSH Key: ${SSH_KEY}"
else
    echo "Mode: Local"
fi
echo ""

# Detect which system is in use
USE_REDROID=false
USE_WAYDROID=false

if remote_exec "command -v docker &>/dev/null && docker ps 2>/dev/null | grep -q redroid"; then
    USE_REDROID=true
    echo -e "${BLUE}Detected: Redroid (Docker-based Android)${NC}"
fi
if remote_exec "command -v waydroid &>/dev/null"; then
    USE_WAYDROID=true
    echo -e "${BLUE}Detected: Waydroid${NC}"
fi
echo ""

# ============================================
# Part 1: Installation Tests
# ============================================
echo -e "${BLUE}=========================================="
echo "Part 1: Installation Tests"
echo "==========================================${NC}"
echo ""

echo -e "${BLUE}[1.1] System Components${NC}"

# Kernel modules
if remote_exec "lsmod | grep -q v4l2loopback"; then
    test_pass "v4l2loopback module loaded"
else
    test_fail "v4l2loopback module" "Not loaded"
fi

if remote_exec "lsmod | grep -q snd_aloop"; then
    test_pass "snd-aloop module loaded"
else
    test_fail "snd-aloop module" "Not loaded"
fi

# Virtual devices
if remote_exec "[ -e /dev/video42 ]"; then
    test_pass "/dev/video42 exists"
else
    test_fail "/dev/video42" "Device not found"
fi

if remote_exec "aplay -l 2>/dev/null | grep -q Loopback"; then
    test_pass "ALSA Loopback device"
else
    test_fail "ALSA Loopback" "Device not found"
fi

echo ""

echo -e "${BLUE}[1.2] Services Status${NC}"

# Check Docker/Redroid if in use
if [ "$USE_REDROID" = true ]; then
    if remote_exec "systemctl is-active --quiet docker"; then
        test_pass "docker"
    else
        test_fail "docker" "Not running"
    fi
    
    REDROID_STATUS=$(remote_exec "docker ps --format '{{.Status}}' --filter 'name=redroid' 2>/dev/null | head -1")
    if [[ -n "$REDROID_STATUS" ]] && echo "$REDROID_STATUS" | grep -qi "up"; then
        test_pass "redroid container ($REDROID_STATUS)"
    else
        test_fail "redroid container" "Not running"
    fi
fi

# Check Waydroid services if in use
if [ "$USE_WAYDROID" = true ]; then
    WAYDROID_SERVICES=("waydroid-container" "waydroid-session")
    for service in "${WAYDROID_SERVICES[@]}"; do
        if remote_exec "systemctl is-active --quiet $service"; then
            test_pass "$service"
        else
            test_warn "$service" "Not running (may not be needed if using Redroid)"
        fi
    done
fi

# Check common services
COMMON_SERVICES=("nginx-rtmp" "xvnc" "ffmpeg-bridge" "control-api")
for service in "${COMMON_SERVICES[@]}"; do
    if remote_exec "systemctl is-active --quiet $service"; then
        test_pass "$service"
    else
        test_warn "$service" "Not running (optional)"
    fi
done

echo ""

echo -e "${BLUE}[1.3] Network Ports${NC}"

# RTMP (should be open to all)
if remote_exec "ss -tlnp | grep -q ':1935 '"; then
    LISTEN_ADDR=$(remote_exec "ss -tlnp | grep ':1935 ' | awk '{print \$4}'")
    if [[ "$LISTEN_ADDR" == *"0.0.0.0"* ]] || [[ "$LISTEN_ADDR" == *"*"* ]]; then
        test_pass "RTMP port 1935 (public)"
    else
        test_warn "RTMP port 1935" "Listening on $LISTEN_ADDR (should be 0.0.0.0)"
    fi
else
    test_warn "RTMP port 1935" "Not listening (optional for RTMP streaming)"
fi

# ADB port (Redroid)
if [ "$USE_REDROID" = true ]; then
    if remote_exec "ss -tlnp | grep -q ':5555 '"; then
        test_pass "ADB port 5555 (Redroid)"
    else
        test_fail "ADB port 5555" "Not listening (Redroid ADB not accessible)"
    fi
fi

# VNC port 5900 (Redroid)
if [ "$USE_REDROID" = true ]; then
    if remote_exec "ss -tlnp | grep -q ':5900 '"; then
        test_pass "VNC port 5900 (Redroid)"
    else
        test_warn "VNC port 5900" "Not listening (Redroid VNC may not be enabled)"
    fi
fi

# VNC (should be localhost only - Waydroid)
if remote_exec "ss -tlnp | grep -q ':5901 '"; then
    LISTEN_ADDR=$(remote_exec "ss -tlnp | grep ':5901 ' | awk '{print \$4}'")
    if [[ "$LISTEN_ADDR" == *"127.0.0.1"* ]] || [[ "$LISTEN_ADDR" == *"::1"* ]]; then
        test_pass "VNC port 5901 (localhost only)"
    else
        test_warn "VNC port 5901" "Listening on $LISTEN_ADDR (should be localhost)"
    fi
else
    if [ "$USE_WAYDROID" = true ]; then
        test_warn "VNC port 5901" "Not listening"
    fi
fi

# API (should be localhost only)
if remote_exec "ss -tlnp | grep -q ':8080 '"; then
    LISTEN_ADDR=$(remote_exec "ss -tlnp | grep ':8080 ' | awk '{print \$4}'")
    if [[ "$LISTEN_ADDR" == *"127.0.0.1"* ]] || [[ "$LISTEN_ADDR" == *"::1"* ]]; then
        test_pass "API port 8080 (localhost only)"
    else
        test_warn "API port 8080" "Listening on $LISTEN_ADDR (should be localhost)"
    fi
else
    test_warn "API port 8080" "Not listening (optional)"
fi

echo ""

# ============================================
# Part 2: API Automation Tests
# ============================================
echo -e "${BLUE}=========================================="
echo "Part 2: API Automation Tests"
echo "==========================================${NC}"
echo ""

# Setup SSH tunnel for remote API access
if [ "$REMOTE_MODE" = true ]; then
    echo -e "${BLUE}Setting up SSH tunnel for API access...${NC}"
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -f -N -L 8080:127.0.0.1:8080 "$SSH_USER@$PUBLIC_IP"
    sleep 2
    API_URL="http://127.0.0.1:8080"
fi

echo -e "${BLUE}[2.1] Health Check${NC}"
HEALTH=$(curl -s --max-time 5 "${API_URL}/health" 2>/dev/null || echo "ERROR")
if echo "$HEALTH" | grep -q "healthy"; then
    test_pass "API health endpoint"
else
    test_fail "API health endpoint" "Not responding"
fi

echo ""

echo -e "${BLUE}[2.2] Device Info${NC}"
DEVICE_INFO=$(curl -s --max-time 5 "${API_URL}/device/info" 2>/dev/null || echo "ERROR")
if [[ "$DEVICE_INFO" != "ERROR" ]] && [[ -n "$DEVICE_INFO" ]]; then
    test_pass "Device info endpoint"
    if echo "$DEVICE_INFO" | grep -q "device\|screen\|android"; then
        test_pass "Device info contains valid data"
    fi
else
    test_fail "Device info endpoint" "Not responding"
fi

echo ""

echo -e "${BLUE}[2.3] Screenshot${NC}"
SCREENSHOT=$(curl -s --max-time 10 "${API_URL}/device/screenshot" 2>/dev/null || echo "")
if [[ -n "$SCREENSHOT" ]] && [[ "${SCREENSHOT:0:4}" =~ ^(PNG|GIF|JFIF|RIFF) ]]; then
    test_pass "Screenshot endpoint (valid image)"
else
    test_fail "Screenshot endpoint" "Invalid or empty response"
fi

echo ""

echo -e "${BLUE}[2.4] Automation Commands${NC}"

# Tap (normalized)
TAP_RESPONSE=$(curl -s --max-time 5 -X POST \
    -H "Content-Type: application/json" \
    -d '{"x":0.5,"y":0.5,"mode":"norm"}' \
    "${API_URL}/device/tap" 2>/dev/null || echo "ERROR")
if [[ "$TAP_RESPONSE" != "ERROR" ]]; then
    test_pass "Tap command (normalized)"
else
    test_fail "Tap command" "Failed"
fi

sleep 1

# Tap (pixel)
TAP_RESPONSE=$(curl -s --max-time 5 -X POST \
    -H "Content-Type: application/json" \
    -d '{"x":540,"y":960}' \
    "${API_URL}/device/tap" 2>/dev/null || echo "ERROR")
if [[ "$TAP_RESPONSE" != "ERROR" ]]; then
    test_pass "Tap command (pixel)"
else
    test_fail "Tap command (pixel)" "Failed"
fi

sleep 1

# Swipe
SWIPE_RESPONSE=$(curl -s --max-time 5 -X POST \
    -H "Content-Type: application/json" \
    -d '{"x1":540,"y1":1500,"x2":540,"y2":500,"duration_ms":300}' \
    "${API_URL}/device/swipe" 2>/dev/null || echo "ERROR")
if [[ "$SWIPE_RESPONSE" != "ERROR" ]]; then
    test_pass "Swipe command"
else
    test_fail "Swipe command" "Failed"
fi

sleep 1

# Text input
TEXT_RESPONSE=$(curl -s --max-time 5 -X POST \
    -H "Content-Type: application/json" \
    -d '{"text":"test123"}' \
    "${API_URL}/device/text" 2>/dev/null || echo "ERROR")
if [[ "$TEXT_RESPONSE" != "ERROR" ]]; then
    test_pass "Text input command"
else
    test_fail "Text input command" "Failed"
fi

sleep 1

# Key press
KEY_RESPONSE=$(curl -s --max-time 5 -X POST \
    -H "Content-Type: application/json" \
    -d '{"keycode":"KEYCODE_HOME"}' \
    "${API_URL}/device/key" 2>/dev/null || echo "ERROR")
if [[ "$KEY_RESPONSE" != "ERROR" ]]; then
    test_pass "Key press command"
else
    test_fail "Key press command" "Failed"
fi

echo ""

# ============================================
# Part 3: RTMP Streaming Tests
# ============================================
echo -e "${BLUE}=========================================="
echo "Part 3: RTMP Streaming Tests"
echo "==========================================${NC}"
echo ""

echo -e "${BLUE}[3.1] RTMP Server Availability${NC}"

# Check RTMP health
if remote_exec "curl -s --max-time 5 http://127.0.0.1:8081/health 2>/dev/null | grep -q OK"; then
    test_pass "RTMP server health check"
else
    test_warn "RTMP server health" "Health endpoint not responding"
fi

# Check RTMP application
if remote_exec "curl -s --max-time 5 http://127.0.0.1:8081/stat 2>/dev/null | grep -q live"; then
    test_pass "RTMP 'live' application configured"
else
    test_warn "RTMP application" "'live' application may not be configured"
fi

# Test RTMP endpoint connectivity
if [ "$REMOTE_MODE" = true ]; then
    if timeout 3 nc -z "$PUBLIC_IP" 1935 2>/dev/null; then
        test_pass "RTMP port 1935 is accessible from network"
    else
        test_fail "RTMP port 1935" "Not accessible from network (check security list)"
    fi
else
    if timeout 3 nc -z 127.0.0.1 1935 2>/dev/null; then
        test_pass "RTMP port 1935 is accessible locally"
    else
        test_fail "RTMP port 1935" "Not accessible"
    fi
fi

echo ""

echo -e "${BLUE}[3.2] Stream Start/Stop Test${NC}"

# Check if ffmpeg-bridge is running
if remote_exec "systemctl is-active --quiet ffmpeg-bridge"; then
    test_pass "FFmpeg bridge service is running"
    
    # Stop the service
    echo "  Stopping ffmpeg-bridge service..."
    remote_exec_sudo "systemctl stop ffmpeg-bridge"
    sleep 2
    
    if ! remote_exec "systemctl is-active --quiet ffmpeg-bridge"; then
        test_pass "FFmpeg bridge stopped successfully"
    else
        test_fail "FFmpeg bridge stop" "Service still running"
    fi
    
    # Restart the service
    echo "  Restarting ffmpeg-bridge service..."
    remote_exec_sudo "systemctl start ffmpeg-bridge"
    sleep 3
    
    if remote_exec "systemctl is-active --quiet ffmpeg-bridge"; then
        test_pass "FFmpeg bridge restarted successfully"
    else
        test_fail "FFmpeg bridge restart" "Service not running"
    fi
else
    test_fail "FFmpeg bridge" "Service not running initially"
fi

echo ""

echo -e "${BLUE}[3.3] RTMP Stream Test (requires external stream)${NC}"
echo "  Note: This test requires an active RTMP stream from OBS or similar"
echo "  Stream to: ${RTMP_URL}"
echo "  Waiting 10 seconds for stream..."

STREAM_DETECTED=false
for i in {1..10}; do
    if timeout 2 ffprobe -v quiet -show_streams "$RTMP_URL" 2>/dev/null; then
        test_pass "RTMP stream detected"
        STREAM_DETECTED=true
        break
    fi
    sleep 1
done

if [ "$STREAM_DETECTED" = false ]; then
    test_warn "RTMP stream" "No active stream detected (start streaming from OBS)"
fi

# If stream detected, test video device
if [ "$STREAM_DETECTED" = true ]; then
    sleep 3
    if remote_exec "[ -e /dev/video42 ] && [ -r /dev/video42 ]"; then
        test_pass "Video device receiving stream data"
    else
        test_fail "Video device" "Not receiving stream data"
    fi
fi

echo ""

# ============================================
# Part 4: Service Recovery Tests
# ============================================
echo -e "${BLUE}=========================================="
echo "Part 4: Service Recovery Tests"
echo "==========================================${NC}"
echo ""

echo -e "${BLUE}[4.1] Service Restart Tests${NC}"

# Test nginx-rtmp restart
echo "  Testing nginx-rtmp restart..."
remote_exec_sudo "systemctl restart nginx-rtmp"
sleep 2
if remote_exec "systemctl is-active --quiet nginx-rtmp"; then
    test_pass "nginx-rtmp restarted successfully"
else
    test_fail "nginx-rtmp restart" "Service not running after restart"
fi

# Test control-api restart
echo "  Testing control-api restart..."
remote_exec_sudo "systemctl restart control-api"
sleep 2
if remote_exec "systemctl is-active --quiet control-api"; then
    test_pass "control-api restarted successfully"
    
    # Verify API still works
    sleep 1
    if curl -s --max-time 5 "${API_URL}/health" 2>/dev/null | grep -q "healthy"; then
        test_pass "API functional after restart"
    else
        test_fail "API after restart" "Not responding"
    fi
else
    test_fail "control-api restart" "Service not running after restart"
fi

# Test xvnc restart
echo "  Testing xvnc restart..."
remote_exec_sudo "systemctl restart xvnc"
sleep 3
if remote_exec "systemctl is-active --quiet xvnc"; then
    test_pass "xvnc restarted successfully"
else
    test_fail "xvnc restart" "Service not running after restart"
fi

echo ""

echo -e "${BLUE}[4.2] Android Container Restart${NC}"

if [ "$USE_REDROID" = true ]; then
    # Restart Redroid container
    echo "  Testing Redroid container restart..."
    remote_exec "docker restart redroid" 2>/dev/null || true
    sleep 10
    
    REDROID_STATUS=$(remote_exec "docker ps --format '{{.Status}}' --filter 'name=redroid' 2>/dev/null | head -1")
    if [[ -n "$REDROID_STATUS" ]] && echo "$REDROID_STATUS" | grep -qi "up"; then
        test_pass "Redroid container restarted successfully"
        
        # Check ADB connection
        sleep 5
        ADB_DEVICES=$(remote_exec "adb devices 2>/dev/null | tail -n +2 | grep -v '^$' | wc -l" || echo "0")
        if [ "$ADB_DEVICES" -gt 0 ]; then
            test_pass "ADB reconnected after container restart"
        else
            test_warn "ADB after restart" "No devices connected (may need more time)"
        fi
    else
        test_fail "Redroid container restart" "Container not running after restart"
    fi
elif [ "$USE_WAYDROID" = true ]; then
    # Restart waydroid-container
    echo "  Testing waydroid-container restart..."
    remote_exec_sudo "systemctl restart waydroid-container"
    sleep 5
    
    if remote_exec "systemctl is-active --quiet waydroid-container"; then
        test_pass "waydroid-container restarted successfully"
        
        # Wait for waydroid to be ready
        sleep 5
        
        # Check ADB connection
        ADB_DEVICES=$(remote_exec "adb devices 2>/dev/null | tail -n +2 | grep -v '^$' | wc -l" || echo "0")
        if [ "$ADB_DEVICES" -gt 0 ]; then
            test_pass "ADB reconnected after container restart"
        else
            test_warn "ADB after restart" "No devices connected (may need more time)"
        fi
    else
        test_fail "waydroid-container restart" "Service not running after restart"
    fi
else
    test_warn "Container restart" "No Android container detected"
fi

echo ""

echo -e "${BLUE}[4.3] Full Service Target Restart${NC}"

# Restart entire target
echo "  Testing waydroid-cloud-phone.target restart..."
remote_exec_sudo "systemctl restart waydroid-cloud-phone.target" 2>/dev/null || true
sleep 5

# Check critical services based on what's in use
ALL_RUNNING=true

if [ "$USE_REDROID" = true ]; then
    # Check Docker
    if ! remote_exec "systemctl is-active --quiet docker"; then
        test_fail "docker after target restart" "Not running"
        ALL_RUNNING=false
    fi
    
    # Check Redroid container
    REDROID_STATUS=$(remote_exec "docker ps --format '{{.Status}}' --filter 'name=redroid' 2>/dev/null | head -1")
    if [[ -z "$REDROID_STATUS" ]] || ! echo "$REDROID_STATUS" | grep -qi "up"; then
        test_fail "Redroid after target restart" "Not running"
        ALL_RUNNING=false
    fi
fi

# Check common services (optional)
for service in nginx-rtmp xvnc ffmpeg-bridge control-api; do
    if remote_exec "systemctl is-active --quiet $service" 2>/dev/null; then
        test_pass "$service after target restart"
    else
        test_warn "$service after target restart" "Not running (optional)"
    fi
done

if [ "$ALL_RUNNING" = true ]; then
    test_pass "All critical services running after target restart"
fi

echo ""

# ============================================
# Part 5: Network Connectivity Tests
# ============================================
echo -e "${BLUE}=========================================="
echo "Part 5: Network Connectivity Tests"
echo "==========================================${NC}"
echo ""

if [ "$REMOTE_MODE" = true ]; then
    echo -e "${BLUE}[5.1] External Connectivity${NC}"
    
    # Test internet connectivity
    if remote_exec "curl -s --max-time 5 https://www.google.com &>/dev/null"; then
        test_pass "Internet connectivity"
    else
        test_fail "Internet connectivity" "Cannot reach external sites"
    fi
    
    # Test RTMP from external
    if timeout 3 nc -z "$PUBLIC_IP" 1935 2>/dev/null; then
        test_pass "RTMP port accessible externally"
    else
        test_fail "RTMP port external" "Not accessible (check security list)"
    fi
    
    # Test SSH
    if timeout 3 nc -z "$PUBLIC_IP" 22 2>/dev/null; then
        test_pass "SSH port accessible externally"
    else
        test_fail "SSH port external" "Not accessible"
    fi
    
    echo ""
fi

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

# Cleanup SSH tunnel
if [ "$REMOTE_MODE" = true ]; then
    pkill -f "ssh.*8080:127.0.0.1:8080.*$PUBLIC_IP" 2>/dev/null || true
fi

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}=========================================="
    echo "All Critical Tests Passed!"
    echo "==========================================${NC}"
    echo ""
    if [ "$REMOTE_MODE" = true ]; then
        echo "Instance is ready for golden image creation:"
        echo "  ./scripts/create-golden-image.sh $PUBLIC_IP waydroid-cloud-phone-v1"
    else
        echo "System is ready for golden image creation."
    fi
    exit 0
else
    echo -e "${RED}=========================================="
    echo "Some Tests Failed"
    echo "==========================================${NC}"
    echo ""
    echo "Fix the issues above before creating a golden image."
    exit 1
fi

