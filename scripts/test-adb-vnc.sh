#!/bin/bash
# Test ADB and VNC connections to Redroid instance

set -euo pipefail

INSTANCE_IP="${1:-137.131.52.69}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/redroid_oci}"

echo "=========================================="
echo "  Test ADB and VNC Connections"
echo "  Instance: $INSTANCE_IP"
echo "=========================================="
echo ""

# Check if ADB is installed
ADB_AVAILABLE=true
if ! command -v adb &> /dev/null; then
    echo "⚠ ADB not installed. Installing..."
    if command -v apt-get &> /dev/null; then
        sudo apt-get update -qq || true
        sudo apt-get install -y -qq android-tools-adb 2>/dev/null || sudo apt-get install -y -qq adb 2>/dev/null || true
    fi
    echo ""
fi
if ! command -v adb &> /dev/null; then
    ADB_AVAILABLE=false
    echo "⚠ Could not install adb; skipping ADB tests."
    echo ""
fi

if [ "$ADB_AVAILABLE" = true ]; then
    echo "=== Testing ADB Connection ==="
    adb kill-server 2>/dev/null || true
    sleep 1

    echo "Connecting to ADB..."
    adb connect "$INSTANCE_IP:5555" 2>&1 || true

    sleep 3

    echo ""
    echo "=== ADB Devices ==="
    adb devices 2>&1 || true

    if adb devices | grep -q "$INSTANCE_IP:5555.*device"; then
        echo ""
        echo "✓ ADB connected successfully!"
        echo ""
        echo "=== Android System Information ==="
        adb shell getprop ro.build.version.release 2>&1 | sed 's/^/  Android Version: /' || true
        adb shell getprop ro.product.model 2>&1 | sed 's/^/  Device Model: /' || true
        adb shell getprop ro.build.version.sdk 2>&1 | sed 's/^/  SDK Version: /' || true
        echo ""
        
        echo "=== Testing ADB Shell ==="
        echo "Running: adb shell 'echo Hello from Android'"
        adb shell 'echo Hello from Android' 2>&1 || true
    else
        echo ""
        echo "✗ ADB connection failed"
        echo ""
        echo "Troubleshooting:"
        echo "  1. Check instance is running: oci compute instance get --instance-id <OCID>"
        echo "  2. Check security list allows port 5555"
        echo "  3. Check ADB daemon: ssh -i $SSH_KEY ubuntu@$INSTANCE_IP 'docker exec redroid sh -c \"pgrep -f adbd\"'"
    fi
fi

echo ""
echo "=== Testing VNC Port ==="
if timeout 5 bash -c "echo > /dev/tcp/$INSTANCE_IP/5900" 2>&1; then
    echo "✓ VNC port 5900 is accessible"
    echo ""
    echo "To connect via VNC:"
    echo "  vncviewer $INSTANCE_IP:5900"
    echo "  Password: redroid"
else
    echo "✗ VNC port 5900 not accessible"
    echo ""
    echo "Troubleshooting:"
    echo "  1. Check security list allows port 5900"
    echo "  2. Wait 2-3 minutes for security list propagation"
    echo "  3. Check port is listening: ssh -i $SSH_KEY ubuntu@$INSTANCE_IP 'sudo ss -tlnp | grep 5900'"
fi

echo ""
echo "=========================================="
echo "  Test Complete"
echo "=========================================="





