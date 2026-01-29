#!/bin/bash
# Test Redroid on Ubuntu 20.04 (older kernel 5.x) on Oracle Cloud ARM
# This tests if older kernel avoids binder/VMA issues for container Android

set -euo pipefail

INSTANCE_IP="${1:-}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/redroid_oci}"

if [ -z "$INSTANCE_IP" ]; then
    echo "Usage: $0 <INSTANCE_IP>"
    echo "Example: $0 137.131.52.69"
    exit 1
fi

echo "=========================================="
echo "  Testing Redroid on Ubuntu 20.04"
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
    echo "✓ Using kernel 5.x (recommended for virtual devices)"
elif [[ "$KERNEL_VERSION" == "6."* ]]; then
    echo "⚠ Using kernel 6.x (virtual devices may be limited)"
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

echo "=== Checking Docker ==="
if command -v docker &>/dev/null; then
    echo "✓ docker installed"
    sudo systemctl is-active --quiet docker && echo "✓ docker running" || echo "⚠ docker not running"
else
    echo "⚠ docker not installed"
fi
echo ""

echo "=== Checking Redroid Container ==="
if docker ps --format '{{.Names}}:{{.Status}}' | grep -q '^redroid:'; then
    docker ps --format '  {{.Names}}  {{.Status}}  {{.Ports}}' | grep '^redroid' || true
else
    echo "⚠ redroid container not running"
fi
echo ""

echo "=== Recent Redroid Logs ==="
docker logs --tail 50 redroid 2>/dev/null || echo "No redroid logs available"
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
echo "  Redroid Container: $(docker ps --format '{{.Names}}' | grep -q '^redroid$' && echo 'running' || echo 'not running')"
echo ""

ENDSSH

echo ""
echo "Test completed!"
echo ""
echo "If kernel 5.x works better, consider:"
echo "  1. Creating new instance with Ubuntu 20.04"
echo "  2. Deploying Redroid on Ubuntu 20.04"
echo "  3. Testing if binder issues are resolved"
