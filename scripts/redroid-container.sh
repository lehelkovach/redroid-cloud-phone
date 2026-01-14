#!/bin/bash
# Idempotent Redroid container launcher (for systemd / automation)
#
# Configuration sources (in priority order):
#   1. Environment variables
#   2. /opt/redroid-env.conf (sourced if exists)
#   3. /etc/cloud-phone/config.json (parsed if exists)
#   4. Default values
#
# Environment overrides:
#   REDROID_CONTAINER_NAME   (default: redroid)
#   REDROID_IMAGE            (default: redroid/redroid:latest)
#   REDROID_DATA_DIR         (default: /opt/redroid-data)
#   REDROID_ADB_PORT         (default: 5555)
#   REDROID_VNC_PORT         (default: 5900)
#   REDROID_WIDTH            (default: 1280)
#   REDROID_HEIGHT           (default: 720)
#   REDROID_FPS              (default: 30)
#   REDROID_DPI              (default: 240)
#   REDROID_GPU_MODE         (default: guest)
#   REDROID_VNC_ENABLED      (default: 1)

set -euo pipefail

# Load configuration from env file if exists
if [[ -f /opt/redroid-env.conf ]]; then
    source /opt/redroid-env.conf
fi

# Load from JSON config if exists and jq available
CONFIG_FILE="${CONFIG_FILE:-/etc/cloud-phone/config.json}"
if [[ -f "$CONFIG_FILE" ]] && command -v jq &>/dev/null; then
    # Only set if not already set via env
    [[ -z "${REDROID_IMAGE:-}" ]] && REDROID_IMAGE=$(jq -r '.redroid.image // empty' "$CONFIG_FILE")
    [[ -z "${REDROID_WIDTH:-}" ]] && REDROID_WIDTH=$(jq -r '.redroid.width // empty' "$CONFIG_FILE")
    [[ -z "${REDROID_HEIGHT:-}" ]] && REDROID_HEIGHT=$(jq -r '.redroid.height // empty' "$CONFIG_FILE")
    [[ -z "${REDROID_FPS:-}" ]] && REDROID_FPS=$(jq -r '.redroid.fps // empty' "$CONFIG_FILE")
    [[ -z "${REDROID_DPI:-}" ]] && REDROID_DPI=$(jq -r '.redroid.dpi // empty' "$CONFIG_FILE")
    [[ -z "${REDROID_VNC_PORT:-}" ]] && REDROID_VNC_PORT=$(jq -r '.redroid.vnc_port // empty' "$CONFIG_FILE")
    [[ -z "${REDROID_ADB_PORT:-}" ]] && REDROID_ADB_PORT=$(jq -r '.redroid.adb_port // empty' "$CONFIG_FILE")
    [[ -z "${REDROID_VNC_ENABLED:-}" ]] && REDROID_VNC_ENABLED=$(jq -r '.redroid.vnc_enabled // empty' "$CONFIG_FILE")
fi

# Apply defaults
NAME="${REDROID_CONTAINER_NAME:-redroid}"
IMAGE="${REDROID_IMAGE:-redroid/redroid:latest}"
DATA_DIR="${REDROID_DATA_DIR:-/opt/redroid-data}"
ADB_PORT="${REDROID_ADB_PORT:-5555}"
VNC_PORT="${REDROID_VNC_PORT:-5900}"
WIDTH="${REDROID_WIDTH:-1280}"
HEIGHT="${REDROID_HEIGHT:-720}"
FPS="${REDROID_FPS:-30}"
DPI="${REDROID_DPI:-240}"
GPU_MODE="${REDROID_GPU_MODE:-guest}"
VNC_ENABLED="${REDROID_VNC_ENABLED:-1}"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not found"
  exit 1
fi

mkdir -p "$DATA_DIR"

if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  echo "Starting existing container: $NAME"
  docker start "$NAME" >/dev/null
  exit 0
fi

echo "Creating and starting container: $NAME"

# Pull image if not present
if ! docker images --format '{{.Repository}}:{{.Tag}}' | grep -q "^${IMAGE}$"; then
    echo "Pulling image: $IMAGE"
    docker pull "$IMAGE"
fi

BOOT_ARGS=(
  "androidboot.redroid_gpu_mode=${GPU_MODE}"
  "androidboot.redroid_width=${WIDTH}"
  "androidboot.redroid_height=${HEIGHT}"
  "androidboot.redroid_fps=${FPS}"
  "androidboot.redroid_dpi=${DPI}"
)

if [ "$VNC_ENABLED" = "1" ] || [ "$VNC_ENABLED" = "true" ]; then
  BOOT_ARGS+=(
    "androidboot.redroid_vnc=1"
    "androidboot.redroid_vnc_port=${VNC_PORT}"
  )
fi

docker run -itd \
  --privileged \
  --restart=unless-stopped \
  --name "$NAME" \
  -p "${ADB_PORT}:5555" \
  -p "${VNC_PORT}:5900" \
  -v "${DATA_DIR}:/data" \
  "$IMAGE" \
  "${BOOT_ARGS[@]}" >/dev/null

echo "Container started: $NAME"
