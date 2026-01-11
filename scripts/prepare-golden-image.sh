#!/bin/bash
# prepare-golden-image.sh
# Prepares an OCI instance for creating a custom golden image
#
# Usage: Run this script on the instance before creating a custom image in OCI Console
# After running, shutdown the instance and create the custom image

set -euo pipefail

echo "=========================================="
echo "Preparing instance for golden image..."
echo "=========================================="

# Check if running as root or with sudo
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root or with sudo" 
   exit 1
fi

echo "[1/7] Stopping all waydroid services..."
systemctl stop waydroid-cloud-phone.target || true
systemctl stop waydroid-container.service || true
systemctl stop waydroid-session.service || true
systemctl stop xvnc.service || true
systemctl stop ffmpeg-bridge.service || true
systemctl stop nginx-rtmp.service || true
systemctl stop control-api.service || true

echo "[2/7] Cleaning journal logs (reduces image size)..."
journalctl --vacuum-time=1d || true
rm -rf /var/log/*.gz /var/log/*.1 /var/log/*.old 2>/dev/null || true

echo "[3/7] Cleaning package cache..."
apt clean || true
apt autoremove -y || true

echo "[4/7] Clearing bash history..."
if [[ -n "${SUDO_USER:-}" ]]; then
    USER_HOME=$(eval echo ~$SUDO_USER)
    history -c || true
    rm -f "$USER_HOME/.bash_history" 2>/dev/null || true
fi
history -c || true
rm -f /root/.bash_history 2>/dev/null || true
rm -f ~/.bash_history 2>/dev/null || true

echo "[5/7] Clearing cloud-init (will run fresh on new instances)..."
cloud-init clean --logs || true

echo "[6/7] Clearing temporary files..."
rm -rf /tmp/* /var/tmp/* 2>/dev/null || true

echo "[7/7] Optional: Remove SSH authorized_keys (uncomment if desired)..."
# Uncomment the following lines if you want to remove SSH keys
# (You'll need to add them back per instance)
# if [[ -n "${SUDO_USER:-}" ]]; then
#     USER_HOME=$(eval echo ~$SUDO_USER)
#     rm -f "$USER_HOME/.ssh/authorized_keys" 2>/dev/null || true
# fi
# rm -f /root/.ssh/authorized_keys 2>/dev/null || true

echo ""
echo "=========================================="
echo "Preparation complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Review the cleanup above"
echo "2. Shutdown the instance: sudo shutdown -h now"
echo "3. In OCI Console: Compute → Instances → [Your Instance] → More Actions → Create Custom Image"
echo "4. Name it: waydroid-cloud-phone-v1"
echo "5. Wait 10-20 minutes for image to be available"
echo ""
echo "After creating the image, you can launch new instances using:"
echo "  ./scripts/launch-fleet.sh"
echo ""

