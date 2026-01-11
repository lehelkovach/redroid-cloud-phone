#!/bin/bash
# Health check script for Waydroid Cloud Phone

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

check_service() {
    local service=$1
    if systemctl is-active --quiet "$service"; then
        echo -e "  ${GREEN}✓${NC} $service"
        return 0
    else
        echo -e "  ${RED}✗${NC} $service"
        return 1
    fi
}

check_device() {
    local device=$1
    if [ -e "$device" ]; then
        echo -e "  ${GREEN}✓${NC} $device"
        return 0
    else
        echo -e "  ${RED}✗${NC} $device"
        return 1
    fi
}

echo "========================================"
echo "  Waydroid Cloud Phone Health Check"
echo "========================================"
echo ""

# Kernel modules
echo "Kernel Modules:"
if lsmod | grep -q v4l2loopback; then
    echo -e "  ${GREEN}✓${NC} v4l2loopback"
else
    echo -e "  ${RED}✗${NC} v4l2loopback"
fi

if lsmod | grep -q snd_aloop; then
    echo -e "  ${GREEN}✓${NC} snd-aloop"
else
    echo -e "  ${RED}✗${NC} snd-aloop"
fi

if [ -d /dev/binderfs ]; then
    echo -e "  ${GREEN}✓${NC} binderfs"
else
    echo -e "  ${RED}✗${NC} binderfs"
fi

echo ""

# Devices
echo "Virtual Devices:"
check_device "/dev/video42"
if aplay -l 2>/dev/null | grep -q Loopback; then
    echo -e "  ${GREEN}✓${NC} ALSA Loopback"
else
    echo -e "  ${RED}✗${NC} ALSA Loopback"
fi

echo ""

# Services
echo "Systemd Services:"
check_service "nginx-rtmp"
check_service "xvnc"
check_service "waydroid-container"
check_service "waydroid-session"
check_service "ffmpeg-bridge"
check_service "control-api"

echo ""

# Network ports
echo "Network Ports:"
if ss -tlnp | grep -q ":1935 "; then
    echo -e "  ${GREEN}✓${NC} RTMP (1935)"
else
    echo -e "  ${YELLOW}○${NC} RTMP (1935) - not listening"
fi

if ss -tlnp | grep -q ":5901 "; then
    echo -e "  ${GREEN}✓${NC} VNC (5901)"
else
    echo -e "  ${YELLOW}○${NC} VNC (5901) - not listening"
fi

if ss -tlnp | grep -q ":8080 "; then
    echo -e "  ${GREEN}✓${NC} API (8080)"
else
    echo -e "  ${YELLOW}○${NC} API (8080) - not listening"
fi

echo ""

# Waydroid status
echo "Waydroid Status:"
if command -v waydroid &> /dev/null; then
    waydroid status 2>/dev/null | sed 's/^/  /'
else
    echo -e "  ${RED}✗${NC} waydroid not installed"
fi

echo ""

# ADB status
echo "ADB Devices:"
adb devices 2>/dev/null | tail -n +2 | grep -v "^$" | sed 's/^/  /' || echo "  (none)"

echo ""

# SOCKS5 status
echo "SOCKS5 Proxy:"
if systemctl is-active --quiet tun2socks; then
    echo -e "  ${GREEN}✓${NC} ENABLED"
    if [ -f /etc/tun2socks.env ]; then
        source /etc/tun2socks.env
        echo "  Proxy: $SOCKS5_HOST:$SOCKS5_PORT"
    fi
else
    echo -e "  ${YELLOW}○${NC} DISABLED (direct connection)"
fi

echo ""

# Quick API test
echo "API Health:"
if curl -s http://127.0.0.1:8080/health 2>/dev/null | grep -q "healthy"; then
    echo -e "  ${GREEN}✓${NC} API responding"
else
    echo -e "  ${RED}✗${NC} API not responding"
fi

echo ""
echo "========================================"
