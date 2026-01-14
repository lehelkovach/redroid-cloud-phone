#!/bin/bash
# Viewing Control Script for Cloud Phone
#
# Supports multiple viewing methods:
# - VNC (built into Redroid)
# - scrcpy (low-latency, requires USB/ADB)
# - WebRTC (browser-based, requires additional setup)
# - none/headless (for automation)
#
# Usage:
#   ./viewing-control.sh vnc [start|stop|status]
#   ./viewing-control.sh scrcpy [start|stop]
#   ./viewing-control.sh webrtc [start|stop]
#   ./viewing-control.sh headless

set -euo pipefail

METHOD="${1:-status}"
ACTION="${2:-status}"
ADB_TARGET="${ADB_CONNECT:-127.0.0.1:5555}"
VNC_PORT="${VNC_PORT:-5900}"
SCRCPY_PORT="${SCRCPY_PORT:-8000}"
WEBRTC_PORT="${WEBRTC_PORT:-8188}"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

# =============================================================================
# VNC Control
# =============================================================================

vnc_start() {
    log_info "Starting VNC..."
    
    # VNC is built into Redroid, just verify it's running
    if docker ps --format '{{.Names}}' | grep -qx "redroid"; then
        # Check if VNC is enabled
        if ss -tlnp | grep -q ":$VNC_PORT "; then
            log_info "VNC already running on port $VNC_PORT"
        else
            log_warn "VNC not listening. Ensure Redroid was started with VNC enabled:"
            echo "  androidboot.redroid_vnc=1"
            echo "  androidboot.redroid_vnc_port=$VNC_PORT"
        fi
    else
        log_error "Redroid container not running"
        return 1
    fi
    
    echo ""
    echo "Connect via VNC:"
    echo "  1. Create SSH tunnel:"
    echo "     ssh -L $VNC_PORT:localhost:$VNC_PORT ubuntu@YOUR_IP -N"
    echo ""
    echo "  2. Connect VNC client:"
    echo "     vncviewer localhost:$VNC_PORT"
    echo "     Password: redroid"
}

vnc_stop() {
    log_info "VNC is built into Redroid container"
    log_info "To disable VNC, restart Redroid without VNC parameters"
}

vnc_status() {
    echo "VNC Status:"
    if ss -tlnp | grep -q ":$VNC_PORT "; then
        echo -e "  ${GREEN}✓${NC} VNC listening on port $VNC_PORT"
    else
        echo -e "  ${RED}✗${NC} VNC not listening"
    fi
}

# =============================================================================
# scrcpy Control (low-latency screen mirroring)
# =============================================================================

scrcpy_start() {
    log_info "Starting scrcpy..."
    
    # Check if scrcpy is installed
    if ! command -v scrcpy &>/dev/null; then
        log_warn "scrcpy not installed. Installing..."
        apt-get update && apt-get install -y scrcpy 2>/dev/null || {
            # Manual install for ARM64
            log_info "Installing scrcpy from source..."
            apt-get install -y ffmpeg libsdl2-2.0-0 adb \
                libavcodec-extra libavformat58 libavutil56 libswresample3 \
                libusb-1.0-0 || true
            
            # Download scrcpy binary
            local scrcpy_url="https://github.com/Genymobile/scrcpy/releases/latest/download/scrcpy-linux-arm64.tar.gz"
            wget -q -O /tmp/scrcpy.tar.gz "$scrcpy_url" || {
                log_error "Could not download scrcpy"
                return 1
            }
            tar xzf /tmp/scrcpy.tar.gz -C /usr/local/bin/
            rm /tmp/scrcpy.tar.gz
        }
    fi
    
    # Ensure ADB connected
    adb connect "$ADB_TARGET" 2>/dev/null || true
    sleep 1
    
    # Start scrcpy in background
    # --no-display for headless server mode
    # --tcpip for network mode
    log_info "Starting scrcpy server..."
    
    # For headless: use scrcpy with recording
    if [[ "${HEADLESS:-false}" == "true" ]]; then
        scrcpy -s "$ADB_TARGET" --no-display --record /tmp/screen-recording.mp4 &
        echo $! > /var/run/scrcpy.pid
        log_info "scrcpy recording to /tmp/screen-recording.mp4"
    else
        # Interactive mode
        scrcpy -s "$ADB_TARGET" \
            --max-size 1280 \
            --bit-rate 4M \
            --max-fps 30 \
            --window-title "Cloud Phone" &
        echo $! > /var/run/scrcpy.pid
    fi
    
    log_info "scrcpy started (PID: $(cat /var/run/scrcpy.pid))"
}

scrcpy_stop() {
    if [[ -f /var/run/scrcpy.pid ]]; then
        kill "$(cat /var/run/scrcpy.pid)" 2>/dev/null || true
        rm /var/run/scrcpy.pid
        log_info "scrcpy stopped"
    else
        log_warn "scrcpy not running (no PID file)"
        pkill -x scrcpy 2>/dev/null || true
    fi
}

scrcpy_status() {
    echo "scrcpy Status:"
    if [[ -f /var/run/scrcpy.pid ]] && kill -0 "$(cat /var/run/scrcpy.pid)" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} scrcpy running (PID: $(cat /var/run/scrcpy.pid))"
    elif pgrep -x scrcpy &>/dev/null; then
        echo -e "  ${GREEN}✓${NC} scrcpy running"
    else
        echo -e "  ${YELLOW}○${NC} scrcpy not running"
    fi
}

# =============================================================================
# WebRTC Control (browser-based viewing)
# =============================================================================

webrtc_start() {
    log_info "Starting WebRTC server..."
    
    # Check for webrtc-streamer
    if ! command -v webrtc-streamer &>/dev/null; then
        log_info "Installing webrtc-streamer..."
        
        # Download ARM64 binary
        local version="v0.6.5"
        local url="https://github.com/mpromonet/webrtc-streamer/releases/download/${version}/webrtc-streamer-${version}-Linux-arm64-Release.tar.gz"
        
        wget -q -O /tmp/webrtc-streamer.tar.gz "$url" || {
            log_error "Could not download webrtc-streamer"
            log_info "Falling back to alternative WebRTC setup..."
            webrtc_alternative
            return
        }
        
        tar xzf /tmp/webrtc-streamer.tar.gz -C /usr/local/bin/
        rm /tmp/webrtc-streamer.tar.gz
    fi
    
    # Start WebRTC streamer
    # Uses the Android screen as video source
    log_info "Note: WebRTC requires additional setup for screen capture"
    
    # Alternative: Use scrcpy with web server
    webrtc_alternative
}

webrtc_alternative() {
    log_info "Setting up web-based viewing with ws-scrcpy..."
    
    # ws-scrcpy provides web-based scrcpy
    if ! command -v node &>/dev/null; then
        log_info "Installing Node.js..."
        curl -fsSL https://deb.nodesource.com/setup_18.x | bash -
        apt-get install -y nodejs
    fi
    
    local ws_scrcpy_dir="/opt/ws-scrcpy"
    
    if [[ ! -d "$ws_scrcpy_dir" ]]; then
        log_info "Installing ws-scrcpy..."
        git clone https://github.com/nicholaschum/ws-scrcpy.git "$ws_scrcpy_dir"
        cd "$ws_scrcpy_dir"
        npm install
        npm run build
    fi
    
    cd "$ws_scrcpy_dir"
    
    # Start ws-scrcpy
    ADB_SERVER_HOST="$ADB_TARGET" node dist/index.js &
    echo $! > /var/run/ws-scrcpy.pid
    
    log_info "ws-scrcpy started on http://localhost:$WEBRTC_PORT"
    echo ""
    echo "Access via browser:"
    echo "  1. SSH tunnel: ssh -L $WEBRTC_PORT:localhost:$WEBRTC_PORT ubuntu@YOUR_IP -N"
    echo "  2. Open: http://localhost:$WEBRTC_PORT"
}

webrtc_stop() {
    if [[ -f /var/run/ws-scrcpy.pid ]]; then
        kill "$(cat /var/run/ws-scrcpy.pid)" 2>/dev/null || true
        rm /var/run/ws-scrcpy.pid
    fi
    pkill -f "ws-scrcpy" 2>/dev/null || true
    pkill -f "webrtc-streamer" 2>/dev/null || true
    log_info "WebRTC server stopped"
}

webrtc_status() {
    echo "WebRTC Status:"
    if [[ -f /var/run/ws-scrcpy.pid ]] && kill -0 "$(cat /var/run/ws-scrcpy.pid)" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} ws-scrcpy running on port $WEBRTC_PORT"
    else
        echo -e "  ${YELLOW}○${NC} WebRTC server not running"
    fi
}

# =============================================================================
# Headless Mode
# =============================================================================

headless_setup() {
    log_info "Configuring headless mode..."
    
    # In headless mode:
    # - VNC can still be enabled but not actively used
    # - scrcpy runs without display
    # - All interaction via API
    
    # Ensure API is running
    if ! ss -tlnp | grep -q ":8080 "; then
        log_warn "Control API not running. Start with:"
        echo "  sudo systemctl start control-api"
    else
        log_info "Control API running on port 8080"
    fi
    
    echo ""
    echo "Headless Mode Features:"
    echo "  - Control via REST API (port 8080)"
    echo "  - ADB commands via /adb/shell endpoint"
    echo "  - Screenshots via /device/screenshot endpoint"
    echo "  - Input events via /device/input endpoint"
    echo ""
    echo "Example API calls:"
    echo "  # Take screenshot"
    echo "  curl -o screen.png http://localhost:8080/device/screenshot"
    echo ""
    echo "  # Tap screen"
    echo '  curl -X POST http://localhost:8080/device/input -H "Content-Type: application/json" -d '\''{"type":"tap","x":500,"y":500}'\'
    echo ""
    echo "  # Run shell command"
    echo '  curl -X POST http://localhost:8080/adb/shell -H "Content-Type: application/json" -d '\''{"command":"pm list packages"}'\'
}

# =============================================================================
# Status
# =============================================================================

show_status() {
    echo "========================================"
    echo "  Viewing Methods Status"
    echo "========================================"
    echo ""
    
    vnc_status
    echo ""
    scrcpy_status
    echo ""
    webrtc_status
    echo ""
    
    echo "API Status:"
    if ss -tlnp | grep -q ":8080 "; then
        echo -e "  ${GREEN}✓${NC} Control API on port 8080"
    else
        echo -e "  ${YELLOW}○${NC} Control API not running"
    fi
}

# =============================================================================
# Main
# =============================================================================

case "$METHOD" in
    vnc)
        case "$ACTION" in
            start) vnc_start ;;
            stop) vnc_stop ;;
            status|*) vnc_status ;;
        esac
        ;;
    scrcpy)
        case "$ACTION" in
            start) scrcpy_start ;;
            stop) scrcpy_stop ;;
            status|*) scrcpy_status ;;
        esac
        ;;
    webrtc)
        case "$ACTION" in
            start) webrtc_start ;;
            stop) webrtc_stop ;;
            status|*) webrtc_status ;;
        esac
        ;;
    headless|none)
        headless_setup
        ;;
    status|*)
        show_status
        ;;
esac
