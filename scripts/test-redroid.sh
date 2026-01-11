#!/bin/bash
# test-redroid.sh
# Quick test script to try Redroid on Oracle Cloud ARM instance

set -euo pipefail

INSTANCE_IP="${1:-161.153.55.58}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/waydroid_oci}"

echo "========================================"
echo "  Testing Redroid on Oracle Cloud ARM"
echo "========================================"
echo ""
echo "Instance: $INSTANCE_IP"
echo ""

ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP << 'ENDSSH'
set -euo pipefail

echo "=== Checking Docker installation ==="
if ! command -v docker &> /dev/null; then
    echo "Docker not installed. Installing..."
    sudo apt update
    sudo apt install -y docker.io
    sudo systemctl enable docker
    sudo systemctl start docker
    sudo usermod -aG docker ubuntu
    echo "Docker installed. You may need to log out and back in."
    echo "Or run: newgrp docker"
    exit 1
fi

echo "Docker is installed: $(docker --version)"

echo ""
echo "=== Checking if user is in docker group ==="
if ! groups | grep -q docker; then
    echo "Adding user to docker group..."
    sudo usermod -aG docker ubuntu
    echo "Please run: newgrp docker"
    exit 1
fi

echo ""
echo "=== Pulling Redroid ARM64 image ==="
docker pull redroid/redroid:latest-arm64 || {
    echo "Failed to pull image. Trying alternative tag..."
    docker pull redroid/redroid:11.0.0-arm64 || {
        echo "Failed to pull ARM64 image. Available tags:"
        echo "Visit: https://hub.docker.com/r/redroid/redroid/tags"
        exit 1
    }
}

echo ""
echo "=== Stopping any existing Redroid container ==="
docker stop redroid 2>/dev/null || true
docker rm redroid 2>/dev/null || true

echo ""
echo "=== Creating data directory ==="
sudo mkdir -p /opt/redroid-data
sudo chmod 777 /opt/redroid-data

echo ""
echo "=== Starting Redroid container ==="
docker run -itd \
  --privileged \
  --restart=unless-stopped \
  --name redroid \
  -p 5555:5555 \
  -p 5900:5900 \
  -v /opt/redroid-data:/data \
  redroid/redroid:latest-arm64 \
  androidboot.redroid_gpu_mode=guest \
  androidboot.redroid_width=1280 \
  androidboot.redroid_height=720 \
  androidboot.redroid_fps=30 || {
    echo "Failed to start container. Trying with alternative image..."
    docker run -itd \
      --privileged \
      --restart=unless-stopped \
      --name redroid \
      -p 5555:5555 \
      -p 5900:5900 \
      -v /opt/redroid-data:/data \
      redroid/redroid:11.0.0-arm64 \
      androidboot.redroid_gpu_mode=guest \
      androidboot.redroid_width=1280 \
      androidboot.redroid_height=720 \
      androidboot.redroid_fps=30
}

echo ""
echo "=== Waiting for container to start ==="
sleep 10

echo ""
echo "=== Checking container status ==="
docker ps | grep redroid || {
    echo "Container not running. Checking logs..."
    docker logs redroid
    exit 1
}

echo ""
echo "=== Enabling ADB over network ==="
docker exec redroid setprop service.adb.tcp.port 5555 || true
docker exec redroid start adbd || true

echo ""
echo "=== Waiting for Android to boot ==="
echo "This may take 30-60 seconds..."
sleep 30

echo ""
echo "=== Checking ADB connection ==="
if command -v adb &> /dev/null; then
    adb connect localhost:5555 || true
    sleep 5
    adb devices
else
    echo "ADB not installed locally. Install with:"
    echo "  sudo apt install android-tools-adb"
    echo ""
    echo "Then connect from your local machine:"
    echo "  adb connect $INSTANCE_IP:5555"
fi

echo ""
echo "=== Container logs (last 20 lines) ==="
docker logs --tail 20 redroid

echo ""
echo "========================================"
echo "  Redroid Test Complete"
echo "========================================"
echo ""
echo "Container Status:"
docker ps -a | grep redroid
echo ""
echo "To connect via ADB from your local machine:"
echo "  adb connect $INSTANCE_IP:5555"
echo ""
echo "To connect via VNC (password: redroid):"
echo "  vncviewer $INSTANCE_IP:5900"
echo ""
echo "To view logs:"
echo "  docker logs -f redroid"
echo ""
echo "To stop:"
echo "  docker stop redroid"
echo "  docker rm redroid"
echo ""
ENDSSH

echo ""
echo "Test script completed!"
echo ""
echo "Next steps:"
echo "1. Connect via ADB: adb connect $INSTANCE_IP:5555"
echo "2. Connect via VNC: vncviewer $INSTANCE_IP:5900 (password: redroid)"
echo "3. Check if Android is running: adb shell getprop ro.build.version.release"








