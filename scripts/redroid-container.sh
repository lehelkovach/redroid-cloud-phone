#!/bin/bash
# Idempotent Redroid container launcher (for systemd / automation)
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
#   REDROID_GPU_MODE         (default: guest)
#   REDROID_VNC_ENABLED      (default: 1)

set -euo pipefail

NAME="${REDROID_CONTAINER_NAME:-redroid}"
IMAGE="${REDROID_IMAGE:-redroid/redroid:latest}"
DATA_DIR="${REDROID_DATA_DIR:-/opt/redroid-data}"
ADB_PORT="${REDROID_ADB_PORT:-5555}"
VNC_PORT="${REDROID_VNC_PORT:-5900}"
WIDTH="${REDROID_WIDTH:-1280}"
HEIGHT="${REDROID_HEIGHT:-720}"
FPS="${REDROID_FPS:-30}"
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

BOOT_ARGS=(
  "androidboot.redroid_gpu_mode=${GPU_MODE}"
  "androidboot.redroid_width=${WIDTH}"
  "androidboot.redroid_height=${HEIGHT}"
  "androidboot.redroid_fps=${FPS}"
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
