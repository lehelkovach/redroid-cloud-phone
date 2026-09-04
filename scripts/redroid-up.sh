#!/bin/bash
# Launch / stop Redroid Android containers for GApps mobile IO.
#
# These are Playwright-like phone containers. They do NOT attach v4l2loopback
# or camera devices — virtual cam/mic is Cuttlefish-only.
#
# Usage:
#   ./scripts/redroid-up.sh --name phone-1 --adb-port 5555
#   ./scripts/redroid-up.sh --name phone-1 --down
#   ./scripts/redroid-up.sh --dry-run --json --name phone-1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="${REDROID_COMPOSE_FILE:-$PROJECT_ROOT/docker/redroid-compose.yml}"

NAME="${REDROID_NAME:-redroid}"
ADB_PORT="${ADB_PORT:-5555}"
IMAGE="${REDROID_IMAGE:-redroid/redroid:11.0.0-latest}"
DRY_RUN="false"
JSON="false"
ACTION="up"

usage() {
    cat <<'EOF'
Usage:
  ./scripts/redroid-up.sh [OPTIONS]

Options:
  --name NAME         Container / compose project name (default: redroid)
  --adb-port PORT     Host ADB port (default: 5555)
  --image IMAGE       Redroid image (default: redroid/redroid:11.0.0-latest)
  --down              Stop and remove the container
  --dry-run           Print actions; do not call docker
  --json              Print a JSON record on stdout (logs on stderr)
  --help              Show help

Env: REDROID_NAME, ADB_PORT, REDROID_IMAGE, REDROID_COMPOSE_FILE
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name) NAME="${2:-}"; shift 2 ;;
        --adb-port) ADB_PORT="${2:-}"; shift 2 ;;
        --image) IMAGE="${2:-}"; shift 2 ;;
        --down) ACTION="down"; shift ;;
        --dry-run) DRY_RUN="true"; shift ;;
        --json) JSON="true"; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

if [[ ! "$NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]]; then
    echo "Invalid container name: $NAME" >&2
    exit 1
fi
if [[ ! "$ADB_PORT" =~ ^[0-9]+$ ]] || [[ "$ADB_PORT" -lt 1 ]] || [[ "$ADB_PORT" -gt 65535 ]]; then
    echo "Invalid ADB port: $ADB_PORT" >&2
    exit 1
fi

log() { echo "[redroid-up] $*" >&2; }

emit_json() {
    local status="$1"
    printf '{"runtime":"redroid","name":"%s","adb_connect":"127.0.0.1:%s","image":"%s","status":"%s","compose":"%s"}\n' \
        "$NAME" "$ADB_PORT" "$IMAGE" "$status" "$COMPOSE_FILE"
}

compose_cmd() {
    echo docker compose -f "$COMPOSE_FILE" -p "$NAME"
}

export REDROID_NAME="$NAME"
export ADB_PORT
export REDROID_IMAGE="$IMAGE"

if [[ "$ACTION" == "down" ]]; then
    if [[ "$DRY_RUN" == "true" ]]; then
        log "dry-run: $(compose_cmd) down -v --remove-orphans"
        [[ "$JSON" == "true" ]] && emit_json "removed"
        exit 0
    fi
    if ! command -v docker >/dev/null 2>&1; then
        echo "docker not found" >&2
        exit 2
    fi
    docker compose -f "$COMPOSE_FILE" -p "$NAME" down -v --remove-orphans >/dev/null
    log "removed $NAME"
    [[ "$JSON" == "true" ]] && emit_json "removed"
    exit 0
fi

if [[ "$DRY_RUN" == "true" ]]; then
    log "dry-run: $(compose_cmd) up -d  (image=$IMAGE adb=127.0.0.1:$ADB_PORT)"
    log "no camera devices will be mounted (Cuttlefish owns virtual cam)"
    [[ "$JSON" == "true" ]] && emit_json "started"
    exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "docker not found; use --dry-run for offline checks" >&2
    exit 2
fi
if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "compose file missing: $COMPOSE_FILE" >&2
    exit 1
fi

docker compose -f "$COMPOSE_FILE" -p "$NAME" up -d
log "started $NAME adb=127.0.0.1:$ADB_PORT image=$IMAGE"
[[ "$JSON" == "true" ]] && emit_json "started"
