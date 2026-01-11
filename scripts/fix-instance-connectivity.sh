#!/bin/bash
# Fix instance connectivity issues

set -euo pipefail

INSTANCE_OCID="${INSTANCE_OCID:-ocid1.instance.oc1.phx.anyhqljrgmifkaqclk7h23un7agzbd6zay7muuqkoxbhm4xgxnsqsdt5w2eq}"

echo "=========================================="
echo "  Fix Instance Connectivity"
echo "=========================================="
echo ""

echo "=== Current Status ==="
STATE=$(oci compute instance get --instance-id "$INSTANCE_OCID" --query 'data."lifecycle-state"' --raw-output 2>&1 | tail -1)
echo "Instance State: $STATE"
echo ""

if [[ "$STATE" != "RUNNING" ]]; then
    echo "Instance is not RUNNING. Starting..."
    oci compute instance action --instance-id "$INSTANCE_OCID" --action START --wait-for-state RUNNING
    echo "✓ Instance started"
    echo ""
    echo "Waiting 30 seconds for SSH to be ready..."
    sleep 30
fi

echo "=== Testing SSH ==="
PUBLIC_IP=$(oci compute instance list-vnics --instance-id "$INSTANCE_OCID" --query 'data[0]."public-ip"' --raw-output 2>&1 | tail -1)
echo "Public IP: $PUBLIC_IP"
echo ""

if [[ -z "$PUBLIC_IP" ]] || [[ "$PUBLIC_IP" == "null" ]]; then
    echo "⚠ Could not get public IP"
    exit 1
fi

echo "Testing SSH connection..."
if timeout 10 ssh -i ~/.ssh/waydroid_oci -o StrictHostKeyChecking=no -o ConnectTimeout=10 ubuntu@$PUBLIC_IP 'echo "SSH OK"' 2>&1; then
    echo "✓ SSH is working!"
    echo ""
    echo "=== Testing VNC Port ==="
    if timeout 5 bash -c "echo > /dev/tcp/$PUBLIC_IP/5900" 2>&1; then
        echo "✓ VNC port 5900 is directly accessible"
        echo ""
        echo "You can connect directly:"
        echo "  vncviewer $PUBLIC_IP:5900"
    else
        echo "✗ VNC port 5900 not directly accessible"
        echo ""
        echo "Use SSH tunnel:"
        echo "  ssh -i ~/.ssh/waydroid_oci -L 5900:localhost:5900 ubuntu@$PUBLIC_IP -N"
        echo "  Then: vncviewer localhost:5900"
    fi
else
    echo "✗ SSH still not working"
    echo ""
    echo "Trying soft reboot..."
    oci compute instance action --instance-id "$INSTANCE_OCID" --action SOFTSTOP --wait-for-state STOPPED
    sleep 5
    oci compute instance action --instance-id "$INSTANCE_OCID" --action START --wait-for-state RUNNING
    echo ""
    echo "Waiting 60 seconds for instance to fully boot..."
    sleep 60
    echo ""
    echo "Try connecting again:"
    echo "  ssh -i ~/.ssh/waydroid_oci ubuntu@$PUBLIC_IP"
fi

echo ""
echo "=========================================="


