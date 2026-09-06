#!/bin/bash
# Prepare an OCI instance for custom image capture.
# --platform redroid     stop Docker Redroid + control API
# --platform cuttlefish  stop KVM ingest stack

set -euo pipefail

PLATFORM="redroid"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform)
            PLATFORM="${2:-redroid}"
            shift 2
            ;;
        --help|-h)
            echo "Usage: sudo $0 [--platform redroid|cuttlefish]"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

[[ "$PLATFORM" == "cuttlefish" || "$PLATFORM" == "redroid" ]] || {
    echo "Unsupported platform: $PLATFORM"
    exit 1
}
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }

echo "=========================================="
echo "Preparing $PLATFORM instance for imaging"
echo "=========================================="

if [[ "$PLATFORM" == "redroid" ]]; then
    echo "[1/6] Stopping Redroid services..."
    systemctl stop redroid-cloud-phone.target || true
    systemctl stop control-api-redroid.service || true
    systemctl stop redroid-container.service || true
    docker ps -aq --filter name=redroid | xargs -r docker stop || true
else
    echo "[1/6] Stopping Cuttlefish services..."
    systemctl stop cuttlefish-cloud-phone.target || true
    systemctl stop cuttlefish-rtmp-bridge.service || true
    systemctl stop cuttlefish-launch.service || true
    systemctl stop nginx-rtmp.service || true
    cvd stop --clear_instance_dirs --instance_name "${CF_INSTANCE_NAME:-cvd-arm64-1}" >/dev/null 2>&1 || true
    rm -rf /tmp/cuttlefish-* /tmp/cvd* /tmp/cf-* 2>/dev/null || true
fi

echo "[2/6] Cleaning logs..."
journalctl --vacuum-time=1d || true
rm -rf /var/log/*.gz /var/log/*.1 /var/log/*.old 2>/dev/null || true

echo "[3/6] Cleaning package cache..."
apt clean || true
apt autoremove -y || true

echo "[4/6] Clearing cloud-init and temp files..."
cloud-init clean --logs || true
rm -rf /tmp/* /var/tmp/* 2>/dev/null || true

echo "[5/6] Clearing shell history..."
if [[ -n "${SUDO_USER:-}" ]]; then
    USER_HOME=$(eval echo "~$SUDO_USER")
    rm -f "$USER_HOME/.bash_history" 2>/dev/null || true
fi
rm -f /root/.bash_history 2>/dev/null || true
history -c || true

echo "[6/6] Syncing filesystem..."
sync

echo ""
echo "Preparation complete."
echo "Next:"
echo "  1) Shutdown instance: sudo shutdown -h now"
echo "  2) Create OCI custom image from this instance"
if [[ "$PLATFORM" == "redroid" ]]; then
    echo "Do not bake a Cuttlefish/KVM overlay onto this Redroid golden."
fi
