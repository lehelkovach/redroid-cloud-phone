#!/bin/bash
# Comprehensive Redroid Test Suite
# Full test coverage for Redroid deployment on Oracle Cloud ARM

set -euo pipefail

INSTANCE_IP="${1:-137.131.52.69}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/waydroid_oci}"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

PASSED=0
FAILED=0
WARNINGS=0

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
echo "  Redroid Full Test Suite"
echo "==========================================${NC}"
echo ""
echo "Instance: $INSTANCE_IP"
echo ""

# Test 1: Instance Connectivity
echo -e "${BLUE}[1/10] Instance Connectivity${NC}"
if timeout 5 ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=5 ubuntu@$INSTANCE_IP 'echo "connected"' &>/dev/null; then
    test_pass "SSH connection to instance"
else
    test_fail "SSH connection" "Cannot connect to instance"
    echo ""
    echo "Cannot proceed without SSH access. Exiting."
    exit 1
fi

# Test 2: Docker Status
echo -e "${BLUE}[2/10] Docker Status${NC}"
DOCKER_STATUS=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP 'sudo systemctl is-active docker 2>&1' || echo "inactive")
if [[ "$DOCKER_STATUS" == "active" ]]; then
    test_pass "Docker service is running"
else
    test_fail "Docker service" "Not running (status: $DOCKER_STATUS)"
fi

DOCKER_VERSION=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP 'sudo docker --version 2>&1' || echo "")
if [[ -n "$DOCKER_VERSION" ]]; then
    test_pass "Docker installed ($DOCKER_VERSION)"
else
    test_fail "Docker" "Not installed or not accessible"
fi

# Test 3: Redroid Container Status
echo -e "${BLUE}[3/10] Redroid Container Status${NC}"
CONTAINER_STATUS=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP 'sudo docker ps -a --format "{{.Names}}:{{.Status}}" | grep redroid || echo "not found"')
if echo "$CONTAINER_STATUS" | grep -q "Up\|running"; then
    test_pass "Redroid container is running"
    echo "      Status: $CONTAINER_STATUS"
else
    test_fail "Redroid container" "Not running (status: $CONTAINER_STATUS)"
fi

CONTAINER_ID=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP 'sudo docker ps --format "{{.ID}}" --filter "name=redroid" | head -1' || echo "")
if [[ -n "$CONTAINER_ID" ]]; then
    test_pass "Redroid container ID: ${CONTAINER_ID:0:12}"
else
    test_fail "Redroid container" "Container not found"
fi

# Test 4: Container Ports
echo -e "${BLUE}[4/10] Container Port Mappings${NC}"
PORTS=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP 'sudo docker port redroid 2>&1' || echo "")
if echo "$PORTS" | grep -q "5555"; then
    test_pass "ADB port 5555 mapped"
    echo "$PORTS" | grep "5555" | sed 's/^/      /'
else
    test_fail "ADB port 5555" "Not mapped"
fi

if echo "$PORTS" | grep -q "5900"; then
    test_pass "VNC port 5900 mapped"
    echo "$PORTS" | grep "5900" | sed 's/^/      /'
else
    test_fail "VNC port 5900" "Not mapped"
fi

# Test 5: Container Logs Health
echo -e "${BLUE}[5/10] Container Logs Health${NC}"
LOG_ERRORS=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP 'sudo docker logs redroid 2>&1 | tail -50 | grep -i "error\|fatal\|panic\|crash" | head -5' || echo "")
if [[ -z "$LOG_ERRORS" ]]; then
    test_pass "No critical errors in container logs"
else
    test_warn "Container logs" "Found potential issues:"
    echo "$LOG_ERRORS" | sed 's/^/      /'
fi

LOG_LAST=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP 'sudo docker logs redroid 2>&1 | tail -3' || echo "")
if [[ -n "$LOG_LAST" ]]; then
    echo "      Last log lines:"
    echo "$LOG_LAST" | sed 's/^/        /'
fi

# Test 6: ADB Connectivity
echo -e "${BLUE}[6/10] ADB Connectivity${NC}"
if ! command -v adb &> /dev/null; then
    echo "  Installing ADB..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq adb 2>&1 | tail -1
fi

adb kill-server 2>/dev/null || true
sleep 1

ADB_CONNECT=$(adb connect "$INSTANCE_IP:5555" 2>&1 || echo "failed")
if echo "$ADB_CONNECT" | grep -q "connected\|already"; then
    test_pass "ADB connection established"
else
    test_fail "ADB connection" "Failed to connect"
    echo "      Output: $ADB_CONNECT"
fi

sleep 3

ADB_DEVICES=$(adb devices 2>&1 | grep "$INSTANCE_IP:5555" || echo "")
if echo "$ADB_DEVICES" | grep -q "device"; then
    test_pass "ADB device shows as 'device'"
else
    test_warn "ADB device status" "Device may still be connecting"
    echo "      Status: $ADB_DEVICES"
fi

# Test 7: Android System Information
echo -e "${BLUE}[7/10] Android System Information${NC}"
ANDROID_VERSION=$(adb shell getprop ro.build.version.release 2>&1 | head -1 || echo "")
if [[ -n "$ANDROID_VERSION" ]] && [[ "$ANDROID_VERSION" =~ ^[0-9] ]]; then
    test_pass "Android version: $ANDROID_VERSION"
else
    test_warn "Android version" "Could not retrieve (may still be booting)"
fi

DEVICE_MODEL=$(adb shell getprop ro.product.model 2>&1 | head -1 || echo "")
if [[ -n "$DEVICE_MODEL" ]] && [[ "$DEVICE_MODEL" != "getprop:"* ]]; then
    test_pass "Device model: $DEVICE_MODEL"
else
    test_warn "Device model" "Could not retrieve"
fi

SDK_VERSION=$(adb shell getprop ro.build.version.sdk 2>&1 | head -1 || echo "")
if [[ -n "$SDK_VERSION" ]] && [[ "$SDK_VERSION" =~ ^[0-9]+$ ]]; then
    test_pass "SDK version: $SDK_VERSION"
else
    test_warn "SDK version" "Could not retrieve"
fi

# Test ADB shell command
SHELL_TEST=$(adb shell 'echo "test"' 2>&1 | head -1 || echo "")
if [[ "$SHELL_TEST" == "test" ]]; then
    test_pass "ADB shell command execution"
else
    test_warn "ADB shell" "Command execution may be limited"
fi

# Test 8: VNC Port Accessibility
echo -e "${BLUE}[8/10] VNC Port Accessibility${NC}"
VNC_PORT_CHECK=$(timeout 5 bash -c "echo > /dev/tcp/$INSTANCE_IP/5900" 2>&1 || echo "failed")
if [[ "$VNC_PORT_CHECK" != "failed" ]]; then
    test_pass "VNC port 5900 is accessible"
else
    test_warn "VNC port 5900" "Not accessible (may need security list rule)"
    echo "      Note: VNC may still work via SSH tunnel"
fi

VNC_LISTEN=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP 'sudo ss -tlnp | grep ":5900 " || echo ""')
if [[ -n "$VNC_LISTEN" ]]; then
    test_pass "VNC port 5900 is listening on host"
    echo "$VNC_LISTEN" | sed 's/^/      /'
else
    test_fail "VNC port 5900" "Not listening on host"
fi

# Test 9: Container Resource Usage
echo -e "${BLUE}[9/10] Container Resource Usage${NC}"
CONTAINER_STATS=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP 'sudo docker stats redroid --no-stream --format "CPU: {{.CPUPerc}} | Memory: {{.MemUsage}}" 2>&1' || echo "")
if [[ -n "$CONTAINER_STATS" ]]; then
    test_pass "Container resource stats retrieved"
    echo "      $CONTAINER_STATS"
else
    test_warn "Container stats" "Could not retrieve"
fi

# Test 10: Virtual Device Support (if available)
echo -e "${BLUE}[10/10] Virtual Device Support${NC}"
V4L2_MODULE=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP 'lsmod | grep v4l2loopback || echo "not loaded"')
if [[ "$V4L2_MODULE" != "not loaded" ]]; then
    test_pass "v4l2loopback module loaded on host"
else
    test_warn "v4l2loopback" "Not loaded (kernel 6.8 compatibility issue on Oracle ARM)"
    echo "      Note: This is expected on Kernel 6.8. Requires Ubuntu 20.04 (Kernel 5.x)."
fi

VIDEO42_EXISTS=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP '[ -e /dev/video42 ] && echo "exists" || echo "not found"')
if [[ "$VIDEO42_EXISTS" == "exists" ]]; then
    test_pass "/dev/video42 exists on host"
else
    test_warn "/dev/video42" "Not found (virtual camera not available)"
fi

ALSA_LOOPBACK=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP 'lsmod | grep snd_aloop || echo "not loaded"')
if [[ "$ALSA_LOOPBACK" != "not loaded" ]]; then
    test_pass "snd-aloop module loaded on host"
else
    test_warn "snd-aloop" "Not loaded (virtual audio not available)"
fi

# Check if devices are visible inside container
CONTAINER_VIDEO=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP 'sudo docker exec redroid ls -la /dev/video* 2>&1 | head -3 || echo "none"' || echo "none")
if echo "$CONTAINER_VIDEO" | grep -q "video"; then
    test_pass "Video devices visible inside container"
    echo "$CONTAINER_VIDEO" | sed 's/^/      /'
else
    test_warn "Container video devices" "No video devices found in container"
fi

CONTAINER_AUDIO=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP 'sudo docker exec redroid ls -la /dev/snd/ 2>&1 | head -5 || echo "none"' || echo "none")
if echo "$CONTAINER_AUDIO" | grep -q "snd"; then
    test_pass "Audio devices visible inside container"
else
    test_warn "Container audio devices" "Audio devices may not be passed through"
fi

# Summary
echo ""
echo -e "${BLUE}=========================================="
echo "  Test Summary"
echo "==========================================${NC}"
echo ""
echo -e "Passed:  ${GREEN}$PASSED${NC}"
echo -e "Failed:  ${RED}$FAILED${NC}"
echo -e "Warnings: ${YELLOW}$WARNINGS${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}=========================================="
    echo "  All Critical Tests Passed!"
    echo "==========================================${NC}"
    echo ""
    echo "Redroid is fully operational!"
    echo ""
    echo "Next steps:"
    echo "  1. Connect via VNC: ssh -i $SSH_KEY -L 5900:localhost:5900 ubuntu@$INSTANCE_IP -N"
    echo "     Then: vncviewer localhost:5900 (password: redroid)"
    echo "  2. Use ADB: adb connect $INSTANCE_IP:5555"
    echo "  3. Address virtual device support (kernel 6.8 compatibility)"
    exit 0
else
    echo -e "${RED}=========================================="
    echo "  Some Tests Failed"
    echo "==========================================${NC}"
    echo ""
    echo "Review failures above and fix issues."
    exit 1
fi


