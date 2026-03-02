#!/bin/bash
# install-cuttlefish-cloud-phone.sh
# Install Cuttlefish + RTMP ingest bridge stack on Ubuntu ARM64 (OCI A1).
#
# Stack composition:
# - nginx-rtmp ingest service
# - Cuttlefish launch workflow
# - RTMP->front/back bridge service
#
# Usage:
#   sudo ./scripts/install-cuttlefish-cloud-phone.sh [OPTIONS]
#
# Options:
#   --instance-name NAME      Cuttlefish instance name (default: cvd-arm64-1)
#   --webrtc-port PORT        WebRTC signaling port (default: 8443)
#   --rtmp-url URL            RTMP input URL (default: rtmp://127.0.0.1/live/cam)
#   --front-sink URI          Front sink URI
#   --back-sink URI           Back sink URI
#   --mic-sink URI            Mic sink URI
#   --skip-tools-check        Do not fail if launch_cvd/cvd are missing
#   --help                    Show help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTANCE_NAME="cvd-arm64-1"
WEBRTC_PORT="8443"
RTMP_URL="rtmp://127.0.0.1/live/cam"
FRONT_SINK_URI="udp://127.0.0.1:23000?pkt_size=1316"
BACK_SINK_URI="udp://127.0.0.1:23001?pkt_size=1316"
MIC_SINK_URI="udp://127.0.0.1:23010?pkt_size=1316"
SKIP_TOOLS_CHECK="false"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

usage() {
    cat <<'EOF'
Usage:
  sudo ./scripts/install-cuttlefish-cloud-phone.sh [OPTIONS]

Options:
  --instance-name NAME      Cuttlefish instance name (default: cvd-arm64-1)
  --webrtc-port PORT        WebRTC signaling port (default: 8443)
  --rtmp-url URL            RTMP input URL (default: rtmp://127.0.0.1/live/cam)
  --front-sink URI          Front sink URI
  --back-sink URI           Back sink URI
  --mic-sink URI            Mic sink URI
  --skip-tools-check        Do not fail if launch_cvd/cvd are missing
  --help                    Show help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --instance-name) INSTANCE_NAME="${2:-$INSTANCE_NAME}"; shift 2 ;;
        --webrtc-port) WEBRTC_PORT="${2:-$WEBRTC_PORT}"; shift 2 ;;
        --rtmp-url) RTMP_URL="${2:-$RTMP_URL}"; shift 2 ;;
        --front-sink) FRONT_SINK_URI="${2:-$FRONT_SINK_URI}"; shift 2 ;;
        --back-sink) BACK_SINK_URI="${2:-$BACK_SINK_URI}"; shift 2 ;;
        --mic-sink) MIC_SINK_URI="${2:-$MIC_SINK_URI}"; shift 2 ;;
        --skip-tools-check) SKIP_TOOLS_CHECK="true"; shift ;;
        --help|-h) usage; exit 0 ;;
        *) log_error "Unknown option: $1"; usage; exit 1 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    log_error "Run as root (sudo)."
    exit 1
fi

ARCH="$(uname -m)"
if [[ "$ARCH" != "aarch64" && "$ARCH" != "arm64" ]]; then
    log_warn "Detected architecture '$ARCH' (expected ARM64)."
fi

CPU_COUNT="$(nproc --all || echo 0)"
MEM_GB="$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo 0)"
if [[ "$CPU_COUNT" -lt 4 ]] || [[ "$MEM_GB" -lt 24 ]]; then
    log_warn "Host resources are below recommended Cuttlefish baseline (4 OCPU / 24GB)."
    log_warn "Detected: ${CPU_COUNT} OCPU, ${MEM_GB}GB RAM"
fi

log_info "[1/8] Installing baseline packages..."
apt-get update -y
apt-get install -y \
    curl wget ca-certificates jq \
    nginx libnginx-mod-rtmp \
    ffmpeg android-tools-adb \
    qemu-kvm bridge-utils dnsmasq iptables iproute2 \
    python3 python3-venv python3-pip

log_info "[2/8] Configuring nginx-rtmp..."
cp "$PROJECT_ROOT/config/nginx-rtmp.conf" /etc/nginx/nginx.conf
systemctl disable nginx.service 2>/dev/null || true

log_info "[3/8] Installing scripts to /opt/cloud-phone-scripts..."
mkdir -p /opt/cloud-phone-scripts
cp "$PROJECT_ROOT/scripts/"*.sh /opt/cloud-phone-scripts/
chmod +x /opt/cloud-phone-scripts/*.sh

log_info "[4/8] Installing Control API..."
mkdir -p /opt/cloud-phone-api
cp "$PROJECT_ROOT/api/server.py" /opt/cloud-phone-api/
cp "$PROJECT_ROOT/api/requirements.txt" /opt/cloud-phone-api/
mkdir -p /opt/cloud-phone/config/device-profiles
cp "$PROJECT_ROOT/config/device-profiles/"*.prop /opt/cloud-phone/config/device-profiles/
python3 -m venv /opt/cloud-phone-api/venv
/opt/cloud-phone-api/venv/bin/pip install --upgrade pip >/dev/null
/opt/cloud-phone-api/venv/bin/pip install -r /opt/cloud-phone-api/requirements.txt >/dev/null

log_info "[5/8] Installing Cuttlefish systemd units..."
cp "$PROJECT_ROOT/systemd/nginx-rtmp.service" /etc/systemd/system/
cp "$PROJECT_ROOT/systemd/cuttlefish-launch.service" /etc/systemd/system/
cp "$PROJECT_ROOT/systemd/cuttlefish-rtmp-bridge.service" /etc/systemd/system/
cp "$PROJECT_ROOT/systemd/cuttlefish-cloud-phone.target" /etc/systemd/system/
cp "$PROJECT_ROOT/systemd/control-api.service" /etc/systemd/system/

mkdir -p /etc/default
cat > /etc/default/cuttlefish-cloud-phone <<EOF
CF_INSTANCE_NAME="$INSTANCE_NAME"
CF_WEBRTC_PORT="$WEBRTC_PORT"
RTMP_URL="$RTMP_URL"
FRONT_SINK_URI="$FRONT_SINK_URI"
BACK_SINK_URI="$BACK_SINK_URI"
MIC_SINK_URI="$MIC_SINK_URI"
EOF

log_info "[6/8] Reloading and enabling services..."
systemctl daemon-reload
systemctl enable nginx-rtmp.service
systemctl enable cuttlefish-launch.service
systemctl enable cuttlefish-rtmp-bridge.service
systemctl enable control-api.service
systemctl enable cuttlefish-cloud-phone.target

log_info "[7/8] Verifying Cuttlefish prerequisites..."
if [[ ! -e /dev/kvm ]]; then
    log_error "/dev/kvm is missing; Cuttlefish cannot start."
    exit 1
fi

if ! command -v launch_cvd >/dev/null 2>&1 || ! command -v cvd >/dev/null 2>&1; then
    if [[ "$SKIP_TOOLS_CHECK" == "true" ]]; then
        log_warn "launch_cvd/cvd not found (skipped by flag)."
    else
        log_error "launch_cvd/cvd not found. Install Cuttlefish host tools first, then rerun."
        exit 2
    fi
else
    log_info "Cuttlefish host tools detected."
fi

log_info "[8/8] Starting stack..."
systemctl start cuttlefish-cloud-phone.target || true
sleep 3

echo ""
echo -e "${BLUE}==========================================${NC}"
echo -e "${BLUE}Cuttlefish stack installation complete${NC}"
echo -e "${BLUE}==========================================${NC}"
echo "Target: cuttlefish-cloud-phone.target"
echo "Config: /etc/default/cuttlefish-cloud-phone"
echo ""
echo "Check services:"
echo "  sudo systemctl status cuttlefish-launch.service"
echo "  sudo systemctl status cuttlefish-rtmp-bridge.service"
echo "  sudo systemctl status control-api.service"
echo "  sudo systemctl status nginx-rtmp.service"
echo ""
echo "Validate:"
echo "  /opt/cloud-phone-scripts/cuttlefish-phase1-validate.sh --local --instance-name $INSTANCE_NAME --webrtc-port $WEBRTC_PORT"
