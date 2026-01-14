#!/bin/bash
# Redroid Cloud Phone Installer
# For Oracle Cloud ARM (Ampere A1 Flex) - Ubuntu 22.04/24.04
#
# This script installs Redroid (Docker-based Android) instead of Waydroid
# Redroid is recommended due to better compatibility with newer kernels.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Check root
if [[ $EUID -ne 0 ]]; then
    log_error "This script must be run as root (use sudo)"
    exit 1
fi

# Check architecture
ARCH=$(uname -m)
if [[ "$ARCH" != "aarch64" ]]; then
    log_error "This script requires ARM64 (aarch64). Detected: $ARCH"
    exit 1
fi

echo -e "${BLUE}========================================"
echo "  Redroid Cloud Phone Installer"
echo "  Oracle Cloud ARM (Always Free)"
echo "========================================${NC}"
echo ""

# Detect Ubuntu version
. /etc/os-release
log_info "Detected: $PRETTY_NAME"
log_info "Kernel: $(uname -r)"

# ============================================
# Step 1: System packages
# ============================================
log_info "[1/7] Installing system packages..."

apt-get update
apt-get install -y \
    curl wget gnupg2 ca-certificates lsb-release apt-transport-https \
    build-essential dkms linux-headers-$(uname -r) \
    alsa-utils \
    nginx libnginx-mod-rtmp \
    ffmpeg \
    adb \
    python3 python3-pip python3-venv \
    git jq htop net-tools \
    iptables iproute2

# ============================================
# Step 2: Install Docker
# ============================================
log_info "[2/7] Installing Docker..."

if ! command -v docker &> /dev/null; then
    # Install Docker
    curl -fsSL https://get.docker.com | sh
    
    # Start Docker
    systemctl enable docker
    systemctl start docker
    
    # Add current user to docker group
    usermod -aG docker ubuntu 2>/dev/null || true
    
    log_info "Docker installed successfully"
else
    log_info "Docker already installed"
fi

# Verify Docker is running
if ! systemctl is-active --quiet docker; then
    systemctl start docker
fi
log_info "Docker version: $(docker --version)"

# ============================================
# Step 3: Virtual Device Modules (Optional)
# ============================================
log_info "[3/7] Configuring virtual device modules..."

# v4l2loopback config
cat > /etc/modprobe.d/v4l2loopback.conf << 'EOF'
options v4l2loopback devices=1 video_nr=42 card_label="VirtualCam" exclusive_caps=1
EOF

# ALSA loopback config
cat > /etc/modprobe.d/snd-aloop.conf << 'EOF'
options snd-aloop index=10 id=Loopback pcm_substreams=1
EOF

# Load modules at boot
cat > /etc/modules-load.d/redroid-cloud-phone.conf << 'EOF'
v4l2loopback
snd-aloop
EOF

# Try to install v4l2loopback (may fail on kernel 6.8+)
if apt-get install -y v4l2loopback-dkms v4l2loopback-utils 2>&1; then
    log_info "v4l2loopback-dkms installed successfully"
else
    log_warn "v4l2loopback-dkms failed to build"
    log_warn "Virtual camera will not be available"
    log_warn "Run fix-v4l2loopback.sh after installation to try to fix this"
fi

# Try to load modules now
modprobe v4l2loopback devices=1 video_nr=42 card_label="VirtualCam" exclusive_caps=1 2>/dev/null || log_warn "v4l2loopback will load after reboot (if compatible)"
modprobe snd-aloop index=10 id=Loopback pcm_substreams=1 2>/dev/null || log_warn "snd-aloop will load after reboot"

# ============================================
# Step 4: Configure nginx-rtmp
# ============================================
log_info "[4/7] Configuring nginx-rtmp..."

cp "$SCRIPT_DIR/config/nginx-rtmp.conf" /etc/nginx/nginx.conf

# ============================================
# Step 5: Install Control API
# ============================================
log_info "[5/7] Setting up Control API..."

mkdir -p /opt/waydroid-api
cp "$SCRIPT_DIR/api/server.py" /opt/waydroid-api/
cp "$SCRIPT_DIR/api/requirements.txt" /opt/waydroid-api/

# Create virtual environment
python3 -m venv /opt/waydroid-api/venv
/opt/waydroid-api/venv/bin/pip install --upgrade pip
/opt/waydroid-api/venv/bin/pip install -r /opt/waydroid-api/requirements.txt

# ============================================
# Step 6: Copy scripts
# ============================================
log_info "[6/7] Installing scripts..."

mkdir -p /opt/waydroid-scripts
cp "$SCRIPT_DIR/scripts/"*.sh /opt/waydroid-scripts/
chmod +x /opt/waydroid-scripts/*.sh

# ============================================
# Step 7: Start Redroid
# ============================================
log_info "[7/7] Starting Redroid container..."

# Create data directory
mkdir -p /opt/redroid-data
chmod 777 /opt/redroid-data

# Pull Redroid image
log_info "Pulling Redroid image (this may take a few minutes)..."
docker pull redroid/redroid:latest

# Stop existing container if any
docker stop redroid 2>/dev/null || true
docker rm redroid 2>/dev/null || true

# Start Redroid container with VNC enabled
log_info "Starting Redroid container..."
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

# Wait for container to start
log_info "Waiting for Redroid to boot..."
sleep 15

# Enable ADB
docker exec redroid sh -c 'setprop service.adb.tcp.port 5555' 2>/dev/null || true
docker exec redroid sh -c 'stop adbd; start adbd' 2>/dev/null || true

# ============================================
# Summary
# ============================================
echo ""
echo -e "${BLUE}========================================"
echo "  Installation Complete!"
echo "========================================${NC}"
echo ""

# Check status
if docker ps | grep -q redroid; then
    echo -e "${GREEN}✓ Redroid container is running${NC}"
else
    echo -e "${RED}✗ Redroid container failed to start${NC}"
    echo "  Check: docker logs redroid"
fi

if [ -e /dev/video42 ]; then
    echo -e "${GREEN}✓ Virtual camera (/dev/video42) available${NC}"
else
    echo -e "${YELLOW}○ Virtual camera not available (kernel compatibility issue)${NC}"
    echo "  Run: sudo /opt/waydroid-scripts/fix-v4l2loopback.sh"
fi

echo ""
echo "Access methods:"
echo ""
echo "  ADB:"
echo "    adb connect $(hostname -I | awk '{print $1}'):5555"
echo ""
echo "  VNC (via SSH tunnel):"
echo "    ssh -L 5900:localhost:5900 ubuntu@$(hostname -I | awk '{print $1}') -N"
echo "    Then: vncviewer localhost:5900"
echo "    Password: redroid"
echo ""
echo "  Health check:"
echo "    sudo /opt/waydroid-scripts/health-check.sh"
echo ""
echo "  Full test suite:"
echo "    /opt/waydroid-scripts/test-redroid-full.sh $(hostname -I | awk '{print $1}')"
echo ""

# Optional: Start nginx-rtmp for streaming
systemctl enable nginx 2>/dev/null || true
systemctl start nginx 2>/dev/null || true

log_info "Installation complete!"
