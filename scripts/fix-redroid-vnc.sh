#!/bin/bash
# Fix Redroid VNC connection issues

set -euo pipefail

INSTANCE_IP="${1:-137.131.52.69}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/waydroid_oci}"

echo "=========================================="
echo "  Fix Redroid VNC"
echo "=========================================="
echo ""

ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP << 'ENDSSH'
set -euo pipefail

echo "=== Stopping Existing Redroid ==="
docker stop redroid 2>/dev/null || true
docker rm redroid 2>/dev/null || true
echo ""

echo "=== Starting Redroid with VNC Enabled ==="
docker run -itd \
  --privileged \
  --restart=unless-stopped \
  --name redroid \
  -p 5555:5555 \
  -p 5900:5900 \
  -v /opt/redroid-data:/data \
  redroid/redroid:latest \
  androidboot.redroid_gpu_mode=guest \
  androidboot.redroid_width=1280 \
  androidboot.redroid_height=720 \
  androidboot.redroid_fps=30 \
  androidboot.redroid_vnc=1 \
  androidboot.redroid_vnc_port=5900

echo "✓ Redroid started with VNC"
echo ""

echo "=== Waiting for Android to Boot ==="
sleep 15

echo "=== Checking VNC Status ==="
if timeout 2 bash -c 'echo > /dev/tcp/localhost/5900' 2>&1; then
    echo "✓ VNC port 5900 is listening"
else
    echo "⚠ VNC port may still be starting..."
fi
echo ""

echo "=== Container Status ==="
docker ps | grep redroid || docker ps -a | grep redroid
echo ""

ENDSSH

echo ""
echo "=========================================="
echo "  VNC Fixed!"
echo "=========================================="
echo ""
echo "Now create SSH tunnel:"
echo "  ssh -i $SSH_KEY -L 5900:localhost:5900 ubuntu@$INSTANCE_IP -N"
echo ""
echo "Then connect:"
echo "  vncviewer localhost:5900"
echo ""
echo "Password: redroid"
echo ""


