#!/bin/bash
# Redroid Cloud Phone Installer
# For Oracle Cloud ARM (Ampere A1 Flex) - Ubuntu 22.04/24.04
#
# This installer sets up:
# - Docker + Redroid container (ADB 5555, VNC 5900)
# - Optional RTMP ingest (nginx-rtmp) + FFmpeg bridge (host virtual devices)
# - Control API (HTTP on 127.0.0.1:8080, ADB-backed)
#
# Notes:
# - Virtual camera/audio require host kernel module support (v4l2loopback, snd-aloop).
# - Control API defaults to ADB_CONNECT=127.0.0.1:5555 (host-mapped ADB).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

if [[ $EUID -ne 0 ]]; then
  log_error "This script must be run as root (use sudo)"
  exit 1
fi

ARCH=$(uname -m)
if [[ "$ARCH" != "aarch64" ]]; then
  log_warn "This installer is intended for ARM64 (aarch64). Detected: $ARCH"
fi

echo "========================================"
echo "  Redroid Cloud Phone Installer"
echo "  Oracle Cloud ARM (Always Free)"
echo "========================================"
echo ""

log_info "[1/6] Installing system packages..."
apt-get update
apt-get install -y \
  curl wget ca-certificates \
  docker.io \
  nginx libnginx-mod-rtmp \
  ffmpeg \
  android-tools-adb \
  alsa-utils \
  python3 python3-pip python3-venv \
  git jq net-tools iproute2

log_info "[2/7] Enabling Docker..."
systemctl enable docker
systemctl start docker

log_info "[3/7] Setting up virtual devices (if kernel supports)..."
KERNEL_VERSION=$(uname -r)
KERNEL_MAJOR=$(echo "$KERNEL_VERSION" | cut -d. -f1)
KERNEL_MINOR=$(echo "$KERNEL_VERSION" | cut -d. -f2)

if [[ "$KERNEL_MAJOR" -eq 5 ]] || [[ "$KERNEL_MAJOR" -lt 6 ]]; then
  log_info "Kernel $KERNEL_VERSION detected - installing virtual device modules"
  
  # Install build dependencies
  apt-get install -y linux-headers-$(uname -r) dkms build-essential
  
  # Install v4l2loopback
  if apt-get install -y v4l2loopback-dkms v4l2loopback-utils; then
    log_info "v4l2loopback installed successfully"
  else
    log_warn "v4l2loopback installation failed"
  fi
  
  # Configure modules
  cat > /etc/modprobe.d/v4l2loopback.conf << 'EOF'
options v4l2loopback devices=1 video_nr=42 card_label="VirtualCam" exclusive_caps=1
EOF
  
  cat > /etc/modprobe.d/snd-aloop.conf << 'EOF'
options snd-aloop index=10 id=Loopback pcm_substreams=1
EOF
  
  cat > /etc/modules-load.d/redroid-virtual-devices.conf << 'EOF'
v4l2loopback
snd-aloop
EOF
  
  # Try to load modules now
  modprobe v4l2loopback devices=1 video_nr=42 card_label="VirtualCam" exclusive_caps=1 2>/dev/null || log_warn "v4l2loopback will load after reboot"
  modprobe snd-aloop index=10 id=Loopback pcm_substreams=1 2>/dev/null || log_warn "snd-aloop will load after reboot"
  
  # Check if devices were created
  if [ -e /dev/video42 ]; then
    log_info "Virtual camera /dev/video42 is ready"
  else
    log_warn "Virtual camera not available yet - reboot may be needed"
  fi
else
  log_warn "Kernel $KERNEL_VERSION detected (6.8+) - virtual devices not supported"
  log_warn "Use Ubuntu 20.04 (Kernel 5.x) for virtual camera/audio support"
  log_warn "See: scripts/deploy-ubuntu20-redroid.sh"
fi

log_info "[4/7] Configuring nginx-rtmp..."
cp "$SCRIPT_DIR/config/nginx-rtmp.conf" /etc/nginx/nginx.conf
systemctl disable nginx.service 2>/dev/null || true

log_info "[5/7] Installing Control API..."
mkdir -p /opt/cloud-phone-api
cp "$SCRIPT_DIR/api/server.py" /opt/cloud-phone-api/
cp "$SCRIPT_DIR/api/requirements.txt" /opt/cloud-phone-api/
ln -sfn /opt/cloud-phone-api /opt/redroid-api

python3 -m venv /opt/cloud-phone-api/venv
/opt/cloud-phone-api/venv/bin/pip install --upgrade pip
/opt/cloud-phone-api/venv/bin/pip install -r /opt/cloud-phone-api/requirements.txt

log_info "[6/9] Installing scripts..."
mkdir -p /opt/redroid-scripts
cp "$SCRIPT_DIR/scripts/"*.sh /opt/redroid-scripts/
chmod +x /opt/redroid-scripts/*.sh

log_info "[7/9] Installing configuration..."
mkdir -p /etc/cloud-phone
if [[ ! -f /etc/cloud-phone/config.json ]]; then
    cp "$SCRIPT_DIR/config/cloud-phone-config.example.json" /etc/cloud-phone/config.json
    log_info "Created default config at /etc/cloud-phone/config.json"
fi
cp "$SCRIPT_DIR/config/cloud-phone-config.schema.json" /etc/cloud-phone/

log_info "[8/9] Setting up logging..."
mkdir -p /var/log/cloud-phone
chmod 755 /var/log/cloud-phone
# Create empty log files
touch /var/log/cloud-phone/{cloud-phone,redroid,logcat,adb,streaming}.log

log_info "[9/9] Installing systemd units..."
cp "$SCRIPT_DIR/systemd/nginx-rtmp.service" /etc/systemd/system/
cp "$SCRIPT_DIR/systemd/ffmpeg-bridge.service" /etc/systemd/system/
cp "$SCRIPT_DIR/systemd/control-api.service" /etc/systemd/system/
cp "$SCRIPT_DIR/systemd/redroid-container.service" /etc/systemd/system/
cp "$SCRIPT_DIR/systemd/log-collector.service" /etc/systemd/system/
cp "$SCRIPT_DIR/systemd/redroid-cloud-phone.target" /etc/systemd/system/

systemctl daemon-reload

systemctl enable nginx-rtmp.service
systemctl enable ffmpeg-bridge.service
systemctl enable control-api.service
systemctl enable redroid-container.service
systemctl enable log-collector.service
systemctl enable redroid-cloud-phone.target

echo ""
echo "========================================"
echo "  Installation Complete!"
echo "========================================"
echo ""
echo "Configuration:"
echo "  Config file:  /etc/cloud-phone/config.json"
echo "  Log directory: /var/log/cloud-phone/"
echo ""
echo "Start everything:"
echo "  sudo systemctl start redroid-cloud-phone.target"
echo ""
echo "Check status:"
echo "  sudo /opt/redroid-scripts/health-check.sh"
echo ""
echo "View logs:"
echo "  tail -f /var/log/cloud-phone/redroid.log"
echo "  sudo journalctl -u redroid-container -f"
echo ""
echo "VNC (SSH tunnel recommended):"
echo "  ssh -L 5900:localhost:5900 ubuntu@YOUR_IP -N"
echo "  vncviewer localhost:5900  # password: redroid"
echo ""
echo "ADB:"
echo "  adb connect YOUR_IP:5555"
echo ""
echo "Control API (via SSH tunnel):"
echo "  ssh -L 8080:localhost:8080 ubuntu@YOUR_IP -N"
echo "  curl http://localhost:8080/health"
echo ""
