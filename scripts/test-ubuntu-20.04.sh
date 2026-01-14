#!/bin/bash
# Test Waydroid on Ubuntu 20.04 (older kernel 5.x) on Oracle Cloud ARM
# This tests if older kernel avoids binder VMA errors

set -euo pipefail

INSTANCE_IP="${1:-}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/waydroid_oci}"

if [ -z "$INSTANCE_IP" ]; then
    echo "Usage: $0 <INSTANCE_IP>"
    echo "Example: $0 137.131.52.69"
    exit 1
fi

echo "=========================================="
echo "  Testing Waydroid on Ubuntu 20.04"
echo "  Oracle Cloud ARM Instance"
echo "=========================================="
echo ""
echo "Instance: $INSTANCE_IP"
echo "Note: This assumes you have Ubuntu 20.04 installed"
echo ""

ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP << 'ENDSSH'
set -euo pipefail

echo "=== System Information ==="
cat /etc/os-release | grep -E "PRETTY_NAME|VERSION_ID"
uname -r
echo ""

echo "=== Checking Kernel Version ==="
KERNEL_VERSION=$(uname -r | cut -d. -f1-2)
echo "Kernel version: $KERNEL_VERSION"
if [[ "$KERNEL_VERSION" == "5."* ]]; then
    echo "✓ Using kernel 5.x (should work better with Waydroid)"
elif [[ "$KERNEL_VERSION" == "6."* ]]; then
    echo "⚠ Using kernel 6.x (may have binder issues)"
else
    echo "? Unknown kernel version"
fi
echo ""

echo "=== Checking Binder Modules ==="
if lsmod | grep -q binder_linux; then
    echo "✓ binder_linux module loaded"
    lsmod | grep binder
else
    echo "⚠ binder_linux module not loaded"
fi
echo ""

echo "=== Checking Binderfs ==="
if mount | grep -q binderfs; then
    echo "✓ binderfs mounted"
    mount | grep binderfs
    ls -la /dev/binderfs/ 2>/dev/null || echo "No binderfs devices"
else
    echo "⚠ binderfs not mounted"
fi
echo ""

echo "=== Checking Waydroid Installation ==="
if command -v waydroid &> /dev/null; then
    echo "✓ Waydroid installed"
    waydroid --version || echo "Could not get version"
else
    echo "⚠ Waydroid not installed"
    echo "Installing Waydroid..."
    curl -s https://repo.waydro.id/waydroid.gpg | sudo gpg --dearmor -o /usr/share/keyrings/waydroid.gpg
    echo "deb [signed-by=/usr/share/keyrings/waydroid.gpg] https://repo.waydro.id/ $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/waydroid.list
    sudo apt update
    sudo apt install -y waydroid
fi
echo ""

echo "=== Checking Waydroid Status ==="
if [ -f /var/lib/waydroid/waydroid.cfg ]; then
    echo "✓ Waydroid initialized"
    waydroid status || echo "Waydroid not running"
else
    echo "⚠ Waydroid not initialized"
    echo "Would need to run: sudo waydroid init"
fi
echo ""

echo "=== Testing Container Start ==="
echo "Stopping any running containers..."
sudo waydroid container stop 2>/dev/null || true
sleep 2

echo "Starting container..."
sudo waydroid container start 2>&1 | tee /tmp/waydroid-start.log || {
    echo "Container start failed. Checking logs..."
    cat /tmp/waydroid-start.log
    echo ""
    echo "Checking for binder errors..."
    grep -i "binder\|vma\|zygote" /tmp/waydroid-start.log || echo "No binder errors found in output"
}

sleep 5

echo ""
echo "=== Container Status ==="
waydroid status || echo "Waydroid not running"

echo ""
echo "=== Checking for Binder Errors ==="
if sudo journalctl -u waydroid-container --no-pager -n 50 2>/dev/null | grep -i "binder.*vma\|binder_alloc\|no vma"; then
    echo "⚠ Binder VMA errors found"
else
    echo "✓ No binder VMA errors in recent logs"
fi

echo ""
echo "=== Checking Zygote ==="
if sudo journalctl -u waydroid-container --no-pager -n 50 2>/dev/null | grep -i "zygote"; then
    echo "Zygote activity found in logs"
    sudo journalctl -u waydroid-container --no-pager -n 50 | grep -i "zygote" | tail -5
else
    echo "No zygote activity found"
fi

echo ""
echo "=== Kernel Binder Support ==="
if [ -d /sys/module/binder_linux ]; then
    echo "✓ binder_linux module available"
    cat /sys/module/binder_linux/version 2>/dev/null || echo "Version unknown"
else
    echo "⚠ binder_linux module not available"
fi

echo ""
echo "=========================================="
echo "  Test Complete"
echo "=========================================="
echo ""
echo "Summary:"
echo "  Kernel: $(uname -r)"
echo "  OS: $(cat /etc/os-release | grep PRETTY_NAME | cut -d'"' -f2)"
echo "  Waydroid: $(waydroid --version 2>/dev/null || echo 'Not installed')"
echo "  Container Status: $(waydroid status 2>/dev/null || echo 'Not running')"
echo ""

ENDSSH

echo ""
echo "Test completed!"
echo ""
echo "If kernel 5.x works better, consider:"
echo "  1. Creating new instance with Ubuntu 20.04"
echo "  2. Deploying Waydroid on Ubuntu 20.04"
echo "  3. Testing if binder issues are resolved"








