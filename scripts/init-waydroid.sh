#!/bin/bash
# Initialize Waydroid (run once after installation)

set -e

echo "========================================"
echo "  Waydroid Initialization"
echo "========================================"

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    echo "Error: Run with sudo"
    exit 1
fi

# Check binder
if [ ! -d /dev/binderfs ]; then
    echo "Error: /dev/binderfs not mounted"
    echo "Did you reboot after installation?"
    exit 1
fi

# Check if already initialized
if [ -f /var/lib/waydroid/waydroid.cfg ]; then
    echo "Waydroid already initialized."
    read -p "Reinitialize? This will reset Android data. (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 0
    fi
    
    # Stop services
    systemctl stop waydroid-session.service 2>/dev/null || true
    systemctl stop waydroid-container.service 2>/dev/null || true
fi

# Choose image type
echo ""
echo "Select Android image type:"
echo "  1) GAPPS - With Google Play Services (larger)"
echo "  2) VANILLA - Without Google (smaller, faster)"
echo ""
read -p "Choice [1]: " choice

case "$choice" in
    2)
        IMAGE_TYPE="VANILLA"
        ;;
    *)
        IMAGE_TYPE="GAPPS"
        ;;
esac

echo ""
echo "Initializing Waydroid with $IMAGE_TYPE image..."
echo "This will download ~800MB-1.5GB. Please wait..."
echo ""

# Initialize Waydroid
waydroid init -s "$IMAGE_TYPE"

# Configure for virtual display
WAYDROID_CFG="/var/lib/waydroid/waydroid.cfg"

if [ -f "$WAYDROID_CFG" ]; then
    # Enable multi-window mode
    if ! grep -q "multi_windows" "$WAYDROID_CFG"; then
        sed -i '/\[waydroid\]/a multi_windows=true' "$WAYDROID_CFG"
    else
        sed -i 's/multi_windows=.*/multi_windows=true/' "$WAYDROID_CFG"
    fi
fi

echo ""
echo "Starting Waydroid services..."

# Start services
systemctl start waydroid-container.service
sleep 5
systemctl start waydroid-session.service

echo ""
echo "Waiting for Android to boot (this takes ~30-60 seconds)..."
sleep 30

# Check status
echo ""
echo "Waydroid status:"
waydroid status || true

# Configure ADB
echo ""
echo "Configuring ADB access..."

# Enable ADB in Android
waydroid shell settings put global adb_enabled 1 2>/dev/null || true
waydroid shell setprop service.adb.tcp.port 5555 2>/dev/null || true

# Restart ADB
waydroid shell stop adbd 2>/dev/null || true
sleep 1
waydroid shell start adbd 2>/dev/null || true

# Connect ADB
sleep 3
adb connect 192.168.240.112:5555 2>/dev/null || echo "ADB will be available after full boot"

# Grant camera permissions to camera app
echo ""
echo "Setting up camera permissions..."
waydroid shell pm grant com.android.camera android.permission.CAMERA 2>/dev/null || true

echo ""
echo "========================================"
echo "  Initialization Complete!"
echo "========================================"
echo ""
echo "Access via SSH tunnel:"
echo "  ssh -L 5901:localhost:5901 -L 8080:localhost:8080 ubuntu@YOUR_IP"
echo ""
echo "Then connect VNC to localhost:5901"
echo ""
echo "Stream from OBS to: rtmp://YOUR_IP/live/cam"
echo ""
echo "Control API available at: http://localhost:8080"
echo ""
