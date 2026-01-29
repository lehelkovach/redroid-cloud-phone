#!/bin/bash
# Setup Redroid with virtual camera and audio devices
# For Ubuntu 20.04 (kernel 5.x) - better compatibility

set -euo pipefail

INSTANCE_IP="${1:-}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/redroid_oci}"

if [[ -z "$INSTANCE_IP" ]]; then
    echo "Usage: $0 <INSTANCE_IP>"
    echo "Example: $0 137.131.52.69"
    exit 1
fi

echo "=========================================="
echo "  Setup Redroid with Virtual Devices"
echo "  Instance: $INSTANCE_IP"
echo "=========================================="
echo ""

ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP << 'ENDSSH'
set -euo pipefail

echo "=== Checking Kernel Version ==="
KERNEL_VERSION=$(uname -r)
echo "Kernel: $KERNEL_VERSION"
MAJOR_VERSION=$(echo "$KERNEL_VERSION" | cut -d. -f1)
MINOR_VERSION=$(echo "$KERNEL_VERSION" | cut -d. -f2)

if [[ "$MAJOR_VERSION" -lt 5 ]] || [[ "$MAJOR_VERSION" -eq 5 && "$MINOR_VERSION" -lt 4 ]]; then
    echo "⚠ Kernel version may be too old for v4l2loopback"
elif [[ "$MAJOR_VERSION" -gt 6 ]] || [[ "$MAJOR_VERSION" -eq 6 && "$MINOR_VERSION" -ge 8 ]]; then
    echo "❌ ERROR: Kernel 6.8+ detected ($KERNEL_VERSION)"
    echo "   v4l2loopback is known to fail on this kernel (Oracle ARM)."
    echo "   Please use Ubuntu 20.04 (Kernel 5.x) for virtual device support."
    echo "   See HANDOFF.md for details."
    exit 1
else
    echo "✓ Kernel version compatible ($KERNEL_VERSION)"
fi
echo ""

echo "=== Installing Required Packages ==="
sudo apt-get update -qq

# Check and install Docker if missing
if ! command -v docker &> /dev/null; then
    echo "Docker not installed. Installing..."
    sudo apt-get install -y -qq docker.io
    sudo systemctl enable --now docker
    sudo usermod -aG docker ubuntu
fi

sudo apt-get install -y -qq \
    linux-headers-$(uname -r) \
    dkms \
    v4l2loopback-dkms \
    alsa-utils \
    2>&1 | tail -5
echo ""

echo "=== Loading Virtual Device Modules ==="
echo "Loading v4l2loopback..."
sudo modprobe v4l2loopback devices=1 video_nr=42 card_label="VirtualCam" exclusive_caps=1 2>&1 || {
    echo "⚠ v4l2loopback failed to load"
    echo "Attempting to build from source..."
    # This may fail on kernel 6.8
}

echo "Loading snd-aloop..."
sudo modprobe snd-aloop index=10 id=Loopback pcm_substreams=1 2>&1 || {
    echo "⚠ snd-aloop failed to load"
}

echo ""

echo "=== Checking Virtual Devices ==="
if [ -e /dev/video42 ]; then
    echo "✓ /dev/video42 exists"
    ls -la /dev/video42
else
    echo "✗ /dev/video42 not found"
fi

if aplay -l 2>/dev/null | grep -q Loopback; then
    echo "✓ ALSA Loopback device found"
    aplay -l | grep -i loopback
else
    echo "✗ ALSA Loopback not found"
fi
echo ""

echo "=== Stopping Existing Redroid Container ==="
docker stop redroid 2>/dev/null || true
docker rm redroid 2>/dev/null || true
echo ""

echo "=== Starting Redroid with Virtual Devices ==="
if [ -e /dev/video42 ] && aplay -l 2>/dev/null | grep -q Loopback; then
    echo "Starting with device passthrough..."
    docker run -itd \
      --privileged \
      --restart=unless-stopped \
      --name redroid \
      --device=/dev/video42 \
      --device=/dev/snd \
      -v /dev/snd:/dev/snd \
      -p 5555:5555 \
      -p 5900:5900 \
      -v /opt/redroid-data:/data \
      redroid/redroid:11.0.0-latest \
      androidboot.redroid_gpu_mode=guest \
      androidboot.redroid_width=1280 \
      androidboot.redroid_height=720 \
      androidboot.redroid_fps=30 \
      androidboot.redroid_vnc=1 \
      androidboot.redroid_vnc_port=5900
    
    echo "✓ Redroid started with virtual devices"
else
    echo "⚠ Virtual devices not available, starting without passthrough..."
    docker run -itd \
      --privileged \
      --restart=unless-stopped \
      --name redroid \
      -p 5555:5555 \
      -p 5900:5900 \
      -v /opt/redroid-data:/data \
      redroid/redroid:11.0.0-latest \
      androidboot.redroid_gpu_mode=guest \
      androidboot.redroid_width=1280 \
      androidboot.redroid_height=720 \
      androidboot.redroid_fps=30 \
      androidboot.redroid_vnc=1 \
      androidboot.redroid_vnc_port=5900
    
    echo "✓ Redroid started (without virtual devices)"
fi
echo ""

echo "=== Waiting for Android to Boot ==="
sleep 10

echo "=== Enabling ADB ==="
docker exec redroid sh -c 'setprop service.adb.tcp.port 5555' 2>&1 || true
docker exec redroid sh -c 'stop adbd; start adbd' 2>&1 || true
sleep 3

echo "=== Checking Devices Inside Container ==="
echo "Video devices:"
docker exec redroid sh -c 'ls -la /dev/video* 2>&1' || echo "  No video devices"
echo ""
echo "Audio devices:"
docker exec redroid sh -c 'ls -la /dev/snd/* 2>&1 | head -5' || echo "  Limited audio devices"
echo ""

echo "=== Container Status ==="
docker ps | grep redroid || docker ps -a | grep redroid
echo ""

echo "=========================================="
echo "  Setup Complete"
echo "=========================================="
echo ""
echo "To test ADB:"
echo "  adb connect $INSTANCE_IP:5555"
echo ""
echo "To test VNC:"
echo "  vncviewer $INSTANCE_IP:5900"
echo "  Password: redroid"
echo ""

ENDSSH

echo ""
echo "Setup script completed!"
echo ""
echo "Next steps:"
echo "  1. Test ADB: ./scripts/test-adb-vnc.sh $INSTANCE_IP"
echo "  2. Check virtual devices: ssh -i $SSH_KEY ubuntu@$INSTANCE_IP 'ls -la /dev/video42'"
echo "  3. Test Android camera app (if devices available)"





