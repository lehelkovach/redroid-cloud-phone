#!/bin/bash
# Comprehensive test script - tests all approaches
# Redroid, Redroid, different kernels, etc.

set -euo pipefail

INSTANCE_IP="${1:-137.131.52.69}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/redroid_oci}"

echo "=========================================="
echo "  Comprehensive Android Container Test"
echo "  Oracle Cloud ARM Instance"
echo "=========================================="
echo ""
echo "Instance: $INSTANCE_IP"
echo ""

# Check connectivity
echo "=== Checking Instance Connectivity ==="
if ! ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=5 ubuntu@$INSTANCE_IP "echo 'Connected'" 2>/dev/null; then
    echo "✗ Instance not accessible"
    echo ""
    echo "Please check:"
    echo "  1. Instance is running in Oracle Cloud Console"
    echo "  2. Security group allows SSH (port 22)"
    echo "  3. Instance has public IP"
    exit 1
fi
echo "✓ Instance accessible"
echo ""

# Get system info
echo "=== System Information ==="
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP << 'ENDSSH'
cat /etc/os-release | grep -E "PRETTY_NAME|VERSION_ID"
uname -r
free -h | head -2
df -h / | tail -1
ENDSSH
echo ""

# Menu
echo "What would you like to test?"
echo ""
echo "1. Test Redroid (current setup)"
echo "2. Test Redroid (Docker-based)"
echo "3. Test Kernel/Binder compatibility"
echo "4. Test Virtual Devices (v4l2loopback, ALSA)"
echo "5. Full test (all of the above)"
echo ""
read -p "Choice [5]: " choice
choice="${choice:-5}"

case "$choice" in
    1)
        echo ""
        echo "=== Testing Redroid ==="
        ./scripts/test-ubuntu-20.04.sh "$INSTANCE_IP" || echo "Redroid test failed"
        ;;
    2)
        echo ""
        echo "=== Testing Redroid ==="
        ./scripts/test-redroid-complete.sh "$INSTANCE_IP" || echo "Redroid test failed"
        ;;
    3)
        echo ""
        echo "=== Testing Kernel/Binder ==="
        ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP << 'ENDSSH'
        echo "Kernel version: $(uname -r)"
        echo ""
        echo "Binder modules:"
        lsmod | grep binder || echo "No binder modules loaded"
        echo ""
        echo "Binderfs:"
        mount | grep binderfs || echo "Binderfs not mounted"
        echo ""
        echo "Binder devices:"
        ls -la /dev/binderfs/ 2>/dev/null || echo "No binderfs devices"
        echo ""
        echo "Kernel binder support:"
        if [ -d /sys/module/binder_linux ]; then
            echo "✓ binder_linux module available"
        else
            echo "✗ binder_linux module not available"
        fi
ENDSSH
        ;;
    4)
        echo ""
        echo "=== Testing Virtual Devices ==="
        ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP << 'ENDSSH'
        echo "v4l2loopback:"
        if lsmod | grep -q v4l2loopback; then
            echo "✓ Module loaded"
            ls -la /dev/video* 2>/dev/null || echo "No video devices"
        else
            echo "✗ Module not loaded"
            echo "Loading..."
            sudo modprobe v4l2loopback devices=1 video_nr=42 2>&1 || echo "Failed to load"
        fi
        echo ""
        echo "ALSA Loopback:"
        if lsmod | grep -q snd_aloop; then
            echo "✓ Module loaded"
            aplay -l 2>/dev/null | grep -i loopback || echo "No loopback devices"
        else
            echo "✗ Module not loaded"
            echo "Loading..."
            sudo modprobe snd-aloop index=10 id=Loopback 2>&1 || echo "Failed to load"
        fi
ENDSSH
        ;;
    5)
        echo ""
        echo "=== Running Full Test Suite ==="
        echo ""
        
        echo "[1/4] Testing Kernel/Binder..."
        ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP << 'ENDSSH'
        echo "Kernel: $(uname -r)"
        lsmod | grep binder || echo "No binder modules"
        mount | grep binderfs || echo "No binderfs"
ENDSSH
        echo ""
        
        echo "[2/4] Testing Virtual Devices..."
        ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP << 'ENDSSH'
        sudo modprobe v4l2loopback devices=1 video_nr=42 2>/dev/null || true
        sudo modprobe snd-aloop index=10 id=Loopback 2>/dev/null || true
        [ -e /dev/video42 ] && echo "✓ /dev/video42 exists" || echo "✗ /dev/video42 missing"
        aplay -l 2>/dev/null | grep -q Loopback && echo "✓ ALSA Loopback exists" || echo "✗ ALSA Loopback missing"
ENDSSH
        echo ""
        
        echo "[3/4] Testing Redroid..."
        ./scripts/test-ubuntu-20.04.sh "$INSTANCE_IP" 2>&1 | tail -20 || echo "Redroid test incomplete"
        echo ""
        
        echo "[4/4] Testing Redroid..."
        ./scripts/test-redroid-complete.sh "$INSTANCE_IP" 2>&1 | tail -20 || echo "Redroid test incomplete"
        echo ""
        ;;
    *)
        echo "Invalid choice"
        exit 1
        ;;
esac

echo ""
echo "=========================================="
echo "  Test Complete"
echo "=========================================="
echo ""
echo "Review the output above to determine:"
echo "  1. Which kernel version is running"
echo "  2. If binder modules are working"
echo "  3. If Redroid/Redroid can start"
echo "  4. If virtual devices are available"
echo ""








