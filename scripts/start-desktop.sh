#!/bin/bash
# start-desktop.sh
# Starts Weston, Waydroid, and XFCE for VNC session

set -e

export DISPLAY=:1
export HOME=/home/waydroid
export USER=waydroid

# Get actual user ID
USER_ID=$(id -u)
export XDG_RUNTIME_DIR="/run/user/${USER_ID}"

# Create runtime directory
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

# Start dbus session if not already running
if [ -z "$DBUS_SESSION_BUS_ADDRESS" ]; then
    eval $(dbus-launch --sh-syntax)
    export DBUS_SESSION_BUS_ADDRESS
fi

# Start Weston (Wayland compositor) on X11
export WAYLAND_DISPLAY=wayland-0
weston --backend=x11-backend.so --xwayland &
WESTON_PID=$!
sleep 3

# Start Waydroid session
waydroid session start &
WAYDROID_PID=$!
sleep 5

# Start XFCE desktop
exec startxfce4

