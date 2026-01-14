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

log_info "[2/6] Enabling Docker..."
systemctl enable docker
systemctl start docker

log_info "[3/6] Configuring nginx-rtmp..."
cp "$SCRIPT_DIR/config/nginx-rtmp.conf" /etc/nginx/nginx.conf

# Prefer our dedicated unit; disable distro nginx unit if present
systemctl disable nginx.service 2>/dev/null || true

log_info "[4/6] Installing Control API..."
mkdir -p /opt/cloud-phone-api
cp "$SCRIPT_DIR/api/server.py" /opt/cloud-phone-api/
cp "$SCRIPT_DIR/api/requirements.txt" /opt/cloud-phone-api/
ln -sfn /opt/cloud-phone-api /opt/waydroid-api

python3 -m venv /opt/cloud-phone-api/venv
/opt/cloud-phone-api/venv/bin/pip install --upgrade pip
/opt/cloud-phone-api/venv/bin/pip install -r /opt/cloud-phone-api/requirements.txt

log_info "[5/6] Installing scripts..."
mkdir -p /opt/waydroid-scripts
cp "$SCRIPT_DIR/scripts/"*.sh /opt/waydroid-scripts/
chmod +x /opt/waydroid-scripts/*.sh

log_info "[6/6] Installing systemd units..."
cp "$SCRIPT_DIR/systemd/nginx-rtmp.service" /etc/systemd/system/
cp "$SCRIPT_DIR/systemd/ffmpeg-bridge.service" /etc/systemd/system/
cp "$SCRIPT_DIR/systemd/control-api.service" /etc/systemd/system/
cp "$SCRIPT_DIR/systemd/redroid-container.service" /etc/systemd/system/
cp "$SCRIPT_DIR/systemd/redroid-cloud-phone.target" /etc/systemd/system/

systemctl daemon-reload

systemctl enable nginx-rtmp.service
systemctl enable ffmpeg-bridge.service
systemctl enable control-api.service
systemctl enable redroid-container.service
systemctl enable redroid-cloud-phone.target

echo ""
echo "========================================"
echo "  Installation Complete!"
echo "========================================"
echo ""
echo "Start everything:"
echo "  sudo systemctl start redroid-cloud-phone.target"
echo ""
echo "Check status:"
echo "  sudo /opt/waydroid-scripts/health-check.sh"
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
