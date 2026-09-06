#!/bin/bash
# install-redroid-cloud-phone.sh
# Install Docker Redroid + Control API on Ubuntu ARM64 (OCI A1).
# GApps/Play is installed when GAPPS_ZIP or /opt/gapps/gapps.zip is present.
# Virtual camera/mic is Cuttlefish-only — this installer mounts no /dev/video*.
#
# Usage:
#   sudo ./scripts/install-redroid-cloud-phone.sh [OPTIONS]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

NAME="${REDROID_NAME:-redroid}"
ADB_PORT="${ADB_PORT:-5555}"
IMAGE="${REDROID_IMAGE:-redroid/redroid:11.0.0-latest}"
SKIP_PULL="false"
SKIP_START="false"
DRY_RUN="false"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

usage() {
    cat <<'EOF'
Usage:
  sudo ./scripts/install-redroid-cloud-phone.sh [OPTIONS]

Options:
  --name NAME         Redroid container name (default: redroid)
  --adb-port PORT     Host ADB port (default: 5555)
  --image IMAGE       Redroid image tag
  --skip-pull         Do not docker pull (image already local)
  --skip-start        Install units and files only; do not start
  --dry-run           Print actions only
  --help              Show help

Env: REDROID_NAME, ADB_PORT, REDROID_IMAGE, GAPPS_ZIP, GAPPS_ZIP_URL
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name) NAME="${2:-$NAME}"; shift 2 ;;
        --adb-port) ADB_PORT="${2:-$ADB_PORT}"; shift 2 ;;
        --image) IMAGE="${2:-$IMAGE}"; shift 2 ;;
        --skip-pull) SKIP_PULL="true"; shift ;;
        --skip-start) SKIP_START="true"; shift ;;
        --dry-run) DRY_RUN="true"; shift ;;
        --help|-h) usage; exit 0 ;;
        *) log_error "Unknown option: $1"; usage; exit 1 ;;
    esac
done

if [[ "$DRY_RUN" == "true" ]]; then
    echo "Dry run configuration:"
    echo "  runtime: redroid"
    echo "  name: $NAME"
    echo "  adb_port: $ADB_PORT"
    echo "  image: $IMAGE"
    echo "  gapps_zip: ${GAPPS_ZIP:-/opt/gapps/gapps.zip}"
    echo "  camera_devices: none"
    exit 0
fi

if [[ $EUID -ne 0 ]]; then
    log_error "Run as root (sudo)."
    exit 1
fi

ARCH="$(uname -m)"
if [[ "$ARCH" != "aarch64" && "$ARCH" != "arm64" ]]; then
    log_warn "Detected architecture '$ARCH' (expected ARM64)."
fi

log_info "[1/8] Installing baseline packages..."
apt-get update -y
apt-get install -y \
    curl wget ca-certificates jq unzip \
    android-tools-adb \
    python3 python3-venv python3-pip \
    docker.io docker-compose-v2 || apt-get install -y \
    curl wget ca-certificates jq unzip \
    android-tools-adb \
    python3 python3-venv python3-pip \
    docker.io docker-compose

systemctl enable --now docker.service || true

log_info "[2/8] Installing scripts to /opt/cloud-phone-scripts..."
mkdir -p /opt/cloud-phone-scripts/lib /opt/cloud-phone/docker /opt/gapps
cp "$PROJECT_ROOT/scripts/"*.sh /opt/cloud-phone-scripts/
if [[ -d "$PROJECT_ROOT/scripts/lib" ]]; then
    cp "$PROJECT_ROOT/scripts/lib/"*.sh /opt/cloud-phone-scripts/lib/
fi
cp "$PROJECT_ROOT/docker/redroid-compose.yml" /opt/cloud-phone/docker/redroid-compose.yml
chmod +x /opt/cloud-phone-scripts/*.sh

log_info "[3/8] Installing Control API..."
mkdir -p /opt/cloud-phone-api
cp "$PROJECT_ROOT/api/server.py" /opt/cloud-phone-api/
cp "$PROJECT_ROOT/api/cloudphone_logging.py" /opt/cloud-phone-api/
cp "$PROJECT_ROOT/api/ui_control.py" /opt/cloud-phone-api/
cp "$PROJECT_ROOT/api/viewport.py" /opt/cloud-phone-api/
cp "$PROJECT_ROOT/api/requirements.txt" /opt/cloud-phone-api/
mkdir -p /opt/cloud-phone/config/device-profiles
if [[ -d "$PROJECT_ROOT/config/device-profiles" ]]; then
    cp "$PROJECT_ROOT/config/device-profiles/"*.prop /opt/cloud-phone/config/device-profiles/ || true
fi
python3 -m venv /opt/cloud-phone-api/venv
/opt/cloud-phone-api/venv/bin/pip install --upgrade pip >/dev/null
/opt/cloud-phone-api/venv/bin/pip install -r /opt/cloud-phone-api/requirements.txt >/dev/null

log_info "[4/8] Installing Redroid systemd units..."
cp "$PROJECT_ROOT/systemd/redroid-container.service" /etc/systemd/system/
cp "$PROJECT_ROOT/systemd/redroid-cloud-phone.target" /etc/systemd/system/
cp "$PROJECT_ROOT/systemd/control-api-redroid.service" /etc/systemd/system/

mkdir -p /etc/default
cat > /etc/default/redroid-cloud-phone <<EOF
REDROID_NAME="$NAME"
ADB_PORT="$ADB_PORT"
REDROID_IMAGE="$IMAGE"
REDROID_COMPOSE_FILE="/opt/cloud-phone/docker/redroid-compose.yml"
GAPPS_ZIP="${GAPPS_ZIP:-/opt/gapps/gapps.zip}"
CLOUD_PHONE_RUNTIME=redroid
EOF

log_info "[5/8] Reloading systemd..."
systemctl daemon-reload
systemctl enable docker.service
systemctl enable redroid-container.service
systemctl enable control-api-redroid.service
systemctl enable redroid-cloud-phone.target

if [[ "$SKIP_PULL" != "true" ]]; then
    log_info "[6/8] Pulling Redroid image $IMAGE ..."
    docker pull "$IMAGE" || log_warn "docker pull failed; start may still work if the image is cached"
else
    log_info "[6/8] Skipping docker pull"
fi

if [[ "$SKIP_START" == "true" ]]; then
    log_warn "[7/8] Skipping start (--skip-start)"
else
    log_info "[7/8] Starting Redroid stack..."
    systemctl start redroid-cloud-phone.target || true
    sleep 3
fi

log_info "[8/8] Optional GApps install..."
GAPPS_PATH="${GAPPS_ZIP:-/opt/gapps/gapps.zip}"
if [[ -n "${GAPPS_ZIP_URL:-}" && ! -s "$GAPPS_PATH" ]]; then
    log_info "Downloading GAPPS_ZIP_URL -> $GAPPS_PATH"
    mkdir -p "$(dirname "$GAPPS_PATH")"
    curl -fsSL --max-time 180 "$GAPPS_ZIP_URL" -o "$GAPPS_PATH" || log_warn "GApps download failed"
fi
if [[ -s "$GAPPS_PATH" ]]; then
    log_info "Waiting for ADB then installing GApps from $GAPPS_PATH"
    for _ in {1..30}; do
        if adb connect "127.0.0.1:${ADB_PORT}" >/dev/null 2>&1 && \
           adb -s "127.0.0.1:${ADB_PORT}" get-state 2>/dev/null | grep -q device; then
            break
        fi
        sleep 3
    done
    /opt/cloud-phone-scripts/install-gapps-redroid.sh \
        --zip "$GAPPS_PATH" \
        --name "$NAME" \
        --adb "127.0.0.1:${ADB_PORT}" || log_warn "GApps install did not validate; rerun gapps-install after boot"
else
    log_warn "No GApps zip at $GAPPS_PATH — Play will be missing until the operator supplies GAPPS_ZIP"
    log_warn "See docs/GAPPS.md. Empty zip is refused on purpose."
fi

echo ""
echo -e "${BLUE}==========================================${NC}"
echo -e "${BLUE}Redroid (GApps automation) install complete${NC}"
echo -e "${BLUE}==========================================${NC}"
echo "Target: redroid-cloud-phone.target"
echo "Config: /etc/default/redroid-cloud-phone"
echo "ADB:    127.0.0.1:${ADB_PORT}"
echo ""
echo "Check:"
echo "  sudo systemctl status redroid-container.service"
echo "  sudo systemctl status control-api-redroid.service"
echo "  /opt/cloud-phone-scripts/install-gapps-redroid.sh --validate-only --adb 127.0.0.1:${ADB_PORT}"
echo "  curl -s http://127.0.0.1:8080/health"
echo ""
echo "This image has no nginx-rtmp / virtual camera. Use Cuttlefish for ingest."
