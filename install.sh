#!/bin/bash
# Waydroid Cloud Phone Installer
# For Oracle Cloud ARM (Ampere A1 Flex) - Ubuntu 22.04/24.04

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
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

echo "========================================"
echo "  Waydroid Cloud Phone Installer"
echo "  Oracle Cloud ARM (Always Free)"
echo "========================================"
echo ""

# Detect Ubuntu version
. /etc/os-release
log_info "Detected: $PRETTY_NAME"

# ============================================
# Step 1: System packages
# ============================================
log_info "[1/8] Installing system packages..."

apt-get update
apt-get install -y \
    curl wget gnupg2 ca-certificates lsb-release apt-transport-https \
    linux-modules-extra-$(uname -r) \
    build-essential dkms linux-headers-$(uname -r) \
    alsa-utils \
    tigervnc-standalone-server tigervnc-common \
    xfce4 xfce4-terminal dbus-x11 \
    nginx libnginx-mod-rtmp \
    ffmpeg \
    adb \
    python3 python3-pip python3-venv \
    git jq htop net-tools \
    iptables iproute2

# Try to install v4l2loopback-dkms, but don't fail if it doesn't build
# (kernel 6.8+ has compatibility issues with older v4l2loopback versions)
log_info "Installing v4l2loopback..."

# Clean up any broken v4l2loopback-dkms installation first
if dpkg -l | grep -q "v4l2loopback-dkms.*ii"; then
    log_info "v4l2loopback-dkms already installed, skipping"
elif dpkg -l | grep -q "v4l2loopback-dkms"; then
    log_warn "Found broken v4l2loopback-dkms installation, cleaning up..."
    apt-get remove -y v4l2loopback-dkms 2>/dev/null || true
    apt-get purge -y v4l2loopback-dkms 2>/dev/null || true
    rm -rf /var/lib/dkms/v4l2loopback 2>/dev/null || true
    dpkg --remove --force-remove-reinstreq v4l2loopback-dkms 2>/dev/null || true
fi

# Try to install, but don't fail if it doesn't build
if apt-get install -y -f v4l2loopback-dkms v4l2loopback-utils 2>&1 | tee /tmp/v4l2loopback-install.log; then
    log_info "v4l2loopback-dkms installed successfully"
else
    log_warn "v4l2loopback-dkms failed to build (will be fixed by fix-v4l2loopback.sh)"
    log_warn "Continuing with installation - v4l2loopback will be fixed later"
    # Install utils anyway and fix broken package state
    apt-get install -y -f v4l2loopback-utils || true
    apt-get install -y -f || true  # Fix any broken dependencies
fi

# ============================================
# Step 2: Install Waydroid
# ============================================
log_info "[2/8] Installing Waydroid..."

if ! command -v waydroid &> /dev/null; then
    curl -s https://repo.waydro.id/waydroid.gpg | gpg --dearmor -o /usr/share/keyrings/waydroid.gpg
    echo "deb [signed-by=/usr/share/keyrings/waydroid.gpg] https://repo.waydro.id/ $VERSION_CODENAME main" > /etc/apt/sources.list.d/waydroid.list
    apt-get update
    apt-get install -y waydroid
else
    log_info "Waydroid already installed"
fi

# ============================================
# Step 3: Install tun2socks for SOCKS5
# ============================================
log_info "[3/8] Installing tun2socks..."

TUN2SOCKS_VERSION="v2.5.2"
TUN2SOCKS_BIN="/usr/local/bin/tun2socks"

if [ ! -f "$TUN2SOCKS_BIN" ]; then
    cd /tmp
    wget -q "https://github.com/xjasonlyu/tun2socks/releases/download/${TUN2SOCKS_VERSION}/tun2socks-linux-arm64.zip"
    unzip -o tun2socks-linux-arm64.zip
    mv tun2socks-linux-arm64 "$TUN2SOCKS_BIN"
    chmod +x "$TUN2SOCKS_BIN"
    rm -f tun2socks-linux-arm64.zip
    cd "$SCRIPT_DIR"
else
    log_info "tun2socks already installed"
fi

# ============================================
# Step 4: Kernel modules
# ============================================
log_info "[4/8] Configuring kernel modules..."

# v4l2loopback config
cat > /etc/modprobe.d/v4l2loopback.conf << 'EOF'
options v4l2loopback devices=1 video_nr=42 card_label="VirtualCam" exclusive_caps=1
EOF

# ALSA loopback config
cat > /etc/modprobe.d/snd-aloop.conf << 'EOF'
options snd-aloop index=10 id=Loopback pcm_substreams=1
EOF

# Load modules at boot
cat > /etc/modules-load.d/waydroid-cloud-phone.conf << 'EOF'
v4l2loopback
snd-aloop
EOF

# Waydroid binder setup
mkdir -p /etc/modules-load.d/
echo "binder_linux" >> /etc/modules-load.d/waydroid-cloud-phone.conf 2>/dev/null || true

# Try to load modules now
modprobe v4l2loopback devices=1 video_nr=42 card_label="VirtualCam" exclusive_caps=1 2>/dev/null || log_warn "v4l2loopback will load after reboot"
modprobe snd-aloop index=10 id=Loopback pcm_substreams=1 2>/dev/null || log_warn "snd-aloop will load after reboot"

# Binderfs mount
if ! grep -q "binderfs" /etc/fstab; then
    mkdir -p /dev/binderfs
    echo "binder /dev/binderfs binder nofail 0 0" >> /etc/fstab
fi
mount /dev/binderfs 2>/dev/null || true

# ============================================
# Step 5: Configure nginx-rtmp
# ============================================
log_info "[5/8] Configuring nginx-rtmp..."

cp "$SCRIPT_DIR/config/nginx-rtmp.conf" /etc/nginx/nginx.conf

# ============================================
# Step 6: Setup VNC
# ============================================
log_info "[6/8] Setting up VNC..."

# Create VNC user (non-root for desktop)
VNC_USER="waydroid"
if ! id "$VNC_USER" &>/dev/null; then
    useradd -m -s /bin/bash "$VNC_USER"
    usermod -aG video,audio,render "$VNC_USER"
fi

# VNC directory
VNC_HOME="/home/$VNC_USER"
mkdir -p "$VNC_HOME/.vnc"

# VNC password (default: waydroid - CHANGE IN PRODUCTION)
echo "waydroid" | vncpasswd -f > "$VNC_HOME/.vnc/passwd"
chmod 600 "$VNC_HOME/.vnc/passwd"

# VNC startup script
cp "$SCRIPT_DIR/config/xvnc-xstartup" "$VNC_HOME/.vnc/xstartup"
chmod +x "$VNC_HOME/.vnc/xstartup"

chown -R "$VNC_USER:$VNC_USER" "$VNC_HOME/.vnc"

# ============================================
# Step 7: Install Control API
# ============================================
log_info "[7/8] Setting up Control API..."

mkdir -p /opt/waydroid-api
cp "$SCRIPT_DIR/api/server.py" /opt/waydroid-api/
cp "$SCRIPT_DIR/api/requirements.txt" /opt/waydroid-api/

# Create virtual environment
python3 -m venv /opt/waydroid-api/venv
/opt/waydroid-api/venv/bin/pip install --upgrade pip
/opt/waydroid-api/venv/bin/pip install -r /opt/waydroid-api/requirements.txt

# ============================================
# Step 8: Install systemd units
# ============================================
log_info "[8/8] Installing systemd services..."

# Copy all systemd units
cp "$SCRIPT_DIR/systemd/"*.service /etc/systemd/system/
cp "$SCRIPT_DIR/systemd/"*.target /etc/systemd/system/

# Copy scripts
mkdir -p /opt/waydroid-scripts
cp "$SCRIPT_DIR/scripts/"*.sh /opt/waydroid-scripts/
chmod +x /opt/waydroid-scripts/*.sh

# Reload systemd
systemctl daemon-reload

# Enable services (but don't start - need reboot for modules)
systemctl enable nginx-rtmp.service
systemctl enable xvnc.service
systemctl enable waydroid-container.service
systemctl enable waydroid-session.service
systemctl enable control-api.service
systemctl enable ffmpeg-bridge.service
systemctl enable waydroid-cloud-phone.target

# Disable default nginx (we use custom config)
systemctl disable nginx.service 2>/dev/null || true

echo ""
echo "========================================"
echo "  Installation Complete!"
echo "========================================"
echo ""
echo "Next steps:"
echo ""
echo "1. REBOOT to load kernel modules:"
echo "   sudo reboot"
echo ""
echo "2. After reboot, initialize Waydroid:"
echo "   sudo /opt/waydroid-scripts/init-waydroid.sh"
echo ""
echo "3. Start all services:"
echo "   sudo systemctl start waydroid-cloud-phone.target"
echo ""
echo "4. Access via SSH tunnel:"
echo "   ssh -L 5901:localhost:5901 -L 8080:localhost:8080 ubuntu@YOUR_IP"
echo ""
echo "5. Stream from OBS:"
echo "   rtmp://YOUR_IP/live/cam"
echo ""
echo "Default VNC password: waydroid"
echo "CHANGE THIS IN PRODUCTION!"
echo ""
