#!/bin/bash
# Quick instance connectivity check

INSTANCE_IP="${1:-137.131.52.69}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/waydroid_oci}"

echo "Checking instance connectivity: $INSTANCE_IP"
echo ""

if ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=5 ubuntu@$INSTANCE_IP "echo 'Connected' && uname -a" 2>/dev/null; then
    echo "✓ Instance is accessible"
    echo ""
    echo "You can now run:"
    echo "  ./scripts/test-redroid-complete.sh"
    exit 0
else
    echo "✗ Instance is not accessible"
    echo ""
    echo "Possible reasons:"
    echo "  1. Instance is stopped/rebooting"
    echo "  2. Network/firewall issue"
    echo "  3. Security group blocking SSH"
    echo ""
    echo "Check Oracle Cloud Console to verify instance status"
    exit 1
fi








