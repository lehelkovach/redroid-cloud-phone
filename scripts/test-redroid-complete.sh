#!/bin/bash
# Complete Redroid Test Script with Virtual Device Passthrough
# Tests Redroid on Oracle Cloud ARM with virtual camera/audio support

set -euo pipefail

INSTANCE_IP="${1:-137.131.52.69}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/waydroid_oci}"

echo "=========================================="
echo "  Complete Redroid Test"
echo "  Oracle Cloud ARM Instance"
echo "=========================================="
echo ""
echo "Instance: $INSTANCE_IP"
echo ""

ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP << 'ENDSSH'
set -euo pipefail

echo "=== System Information ==="
uname -a
echo ""

echo "=== Checking Docker ==="
if ! command -v docker &> /dev/null; then
    echo "Docker not installed. Installing..."
    sudo apt update
    sudo apt install -y docker.io
    sudo systemctl enable docker
    sudo systemctl start docker
    sudo usermod -aG docker ubuntu
    echo "Docker installed. Note: You may need to log out/in for group changes."
    exit 1
fi

sudo docker --version
echo ""

echo "=== Checking Docker Service ==="
sudo systemctl is-active docker || sudo systemctl start docker
echo ""

echo "=== Checking Virtual Devices (v4l2loopback & ALSA) ==="
if lsmod | grep -q v4l2loopback; then
    echo "✓ v4l2loopback module loaded"
    ls -la /dev/video* 2>/dev/null || echo "No video devices found"
else
    echo "⚠ v4l2loopback not loaded (will load later)"
fi

if lsmod | grep -q snd_aloop; then
    echo "✓ snd-aloop module loaded"
    aplay -l 2>/dev/null | grep -i loopback || echo "No loopback devices found"
else
    echo "⚠ snd-aloop not loaded (will load later)"
fi
echo ""

echo "=== Loading Virtual Device Modules ==="
sudo modprobe v4l2loopback devices=1 video_nr=42 card_label="VirtualCam" exclusive_caps=1 2>/dev/null || echo "v4l2loopback load failed (may need DKMS)"
sudo modprobe snd-aloop index=10 id=Loopback pcm_substreams=1 2>/dev/null || echo "snd-aloop load failed"
echo ""

echo "=== Checking Virtual Devices After Load ==="
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

echo "=== Stopping Any Existing Redroid Containers ==="
sudo docker stop redroid 2>/dev/null || true
sudo docker rm redroid 2>/dev/null || true
echo ""

echo "=== Creating Data Directory ==="
sudo mkdir -p /opt/redroid-data
sudo chmod 777 /opt/redroid-data
echo ""

echo "=== Pulling Redroid Image ==="
sudo docker pull redroid/redroid:latest
echo ""

echo "=== Starting Redroid with Device Passthrough ==="
echo "Attempting to pass virtual devices to container..."
sudo docker run -itd \
  --privileged \
  --restart=unless-stopped \
  --name redroid \
  --device=/dev/video42 \
  --device=/dev/snd \
  -v /dev/snd:/dev/snd \
  -p 5555:5555 \
  -p 5900:5900 \
  -v /opt/redroid-data:/data \
  redroid/redroid:latest \
  androidboot.redroid_gpu_mode=guest \
  androidboot.redroid_width=1280 \
  androidboot.redroid_height=720 \
  androidboot.redroid_fps=30 || {
    echo "Failed to start with device passthrough. Trying without devices..."
    sudo docker run -itd \
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
      androidboot.redroid_fps=30
}
echo ""

echo "=== Waiting for Container to Start ==="
sleep 10

echo "=== Container Status ==="
sudo docker ps -a | grep redroid || echo "Container not found"
echo ""

echo "=== Container Logs (last 30 lines) ==="
sudo docker logs --tail 30 redroid 2>&1 || echo "Could not get logs"
echo ""

echo "=== Checking Device Visibility Inside Container ==="
echo "Checking /dev/video* devices:"
sudo docker exec redroid ls -la /dev/video* 2>&1 || echo "No video devices found in container"
echo ""

echo "Checking /dev/snd devices:"
sudo docker exec redroid ls -la /dev/snd/ 2>&1 || echo "No audio devices found in container"
echo ""

echo "=== Enabling ADB Over Network ==="
sudo docker exec redroid setprop service.adb.tcp.port 5555 2>&1 || echo "ADB property set (may need to wait for boot)"
sudo docker exec redroid start adbd 2>&1 || echo "ADB start attempted (may need to wait for boot)"
echo ""

echo "=== Waiting for Android to Boot ==="
echo "This may take 30-60 seconds..."
sleep 30

echo "=== Checking ADB Connection ==="
if command -v adb &> /dev/null; then
    adb connect 127.0.0.1:5555 2>&1 || true
    sleep 5
    adb devices
    echo ""
    
    echo "=== Android System Information ==="
    adb shell getprop ro.build.version.release 2>&1 || echo "Could not get Android version"
    adb shell getprop ro.product.model 2>&1 || echo "Could not get device model"
    echo ""
    
    echo "=== Checking Camera Devices in Android ==="
    adb shell dumpsys media.camera | grep -i "camera" | head -10 || echo "Could not check cameras"
    echo ""
    
    echo "=== Checking Audio Devices in Android ==="
    adb shell dumpsys audio | grep -i "input\|microphone" | head -10 || echo "Could not check audio"
else
    echo "ADB not installed locally. Install with:"
    echo "  sudo apt install android-tools-adb"
    echo ""
    echo "Then connect from your local machine:"
    echo "  adb connect $INSTANCE_IP:5555"
fi
echo ""

echo "=== Final Container Status ==="
sudo docker ps | grep redroid || sudo docker ps -a | grep redroid
echo ""

echo "=== Container Logs (last 20 lines) ==="
sudo docker logs --tail 20 redroid 2>&1
echo ""

echo "=========================================="
echo "  Redroid Test Complete"
echo "=========================================="
echo ""
echo "Container Status:"
sudo docker ps -a | grep redroid
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
echo "To test virtual devices:"
echo "  1. Start FFmpeg bridge on host (feeds /dev/video42)"
echo "  2. Check if Android sees camera: adb shell dumpsys media.camera"
echo "  3. Check if Android sees audio: adb shell dumpsys audio"
echo ""

ENDSSH

echo ""
echo "Test script completed!"
echo ""
echo "Next steps:"
echo "1. Check if Redroid container is running"
echo "2. Test ADB connection: adb connect $INSTANCE_IP:5555"
echo "3. Test VNC connection: vncviewer $INSTANCE_IP:5900"
echo "4. Test virtual device passthrough (check if Android sees /dev/video42)"








