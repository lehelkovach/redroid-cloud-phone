#!/bin/bash
# Build Custom Cloud Phone Docker Image
#
# Usage:
#   ./build.sh                              # Build with defaults
#   ./build.sh --gapps                      # Include Google Apps
#   ./build.sh --push myregistry.com/img    # Build and push
#   ./build.sh --android 11                 # Specific Android version

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Defaults
IMAGE_NAME="cloud-phone"
IMAGE_TAG="latest"
BASE_IMAGE="redroid/redroid:11.0.0-latest"
INCLUDE_GAPPS=false
PUSH_TO=""
ANDROID_VERSION=""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

usage() {
    cat <<EOF
Build Custom Cloud Phone Docker Image

Usage: $0 [OPTIONS]

Options:
  --name NAME           Image name (default: cloud-phone)
  --tag TAG             Image tag (default: latest)
  --base IMAGE          Base Redroid image (default: redroid/redroid:11.0.0-latest)
  --android VERSION     Android version (11, 12, 13)
  --gapps               Include Google Apps (requires gapps/ directory)
  --push REGISTRY       Push to registry after build
  --no-cache            Build without cache
  --help                Show this help

Examples:
  # Basic build
  $0

  # Build with GApps for Android 11
  $0 --android 11 --gapps

  # Build and push to registry
  $0 --push myregistry.com/cloud-phone --tag v1.0

Directory Structure:
  docker/
  ├── Dockerfile
  ├── build.sh (this script)
  ├── apps/          # Place APKs here to pre-install
  └── gapps/         # Place GApps files here (if --gapps)

EOF
    exit 0
}

# Parse arguments
DOCKER_ARGS=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --name) IMAGE_NAME="$2"; shift 2 ;;
        --tag) IMAGE_TAG="$2"; shift 2 ;;
        --base) BASE_IMAGE="$2"; shift 2 ;;
        --android)
            ANDROID_VERSION="$2"
            case "$2" in
                11) BASE_IMAGE="redroid/redroid:11.0.0-latest" ;;
                12) BASE_IMAGE="redroid/redroid:12.0.0-latest" ;;
                13) BASE_IMAGE="redroid/redroid:13.0.0-latest" ;;
                *) log_error "Unknown Android version: $2"; exit 1 ;;
            esac
            shift 2
            ;;
        --gapps) INCLUDE_GAPPS=true; shift ;;
        --push) PUSH_TO="$2"; shift 2 ;;
        --no-cache) DOCKER_ARGS="$DOCKER_ARGS --no-cache"; shift ;;
        --help|-h) usage ;;
        *) log_error "Unknown option: $1"; usage ;;
    esac
done

# Create required directories
mkdir -p apps gapps

# Check for GApps
if [[ "$INCLUDE_GAPPS" == "true" ]]; then
    if [[ ! -d "gapps" ]] || [[ -z "$(ls -A gapps 2>/dev/null)" ]]; then
        log_warn "GApps directory empty. Download GApps files:"
        echo ""
        echo "Option 1: NikGApps (recommended for Android 11+)"
        echo "  wget -O gapps/nikgapps.zip 'https://sourceforge.net/projects/nikgapps/files/...'"
        echo "  unzip gapps/nikgapps.zip -d gapps/"
        echo ""
        echo "Option 2: Use pre-built GApps image"
        echo "  ./build.sh --base redroid/redroid:11.0.0-gapps"
        echo ""
        log_warn "Continuing without GApps..."
        INCLUDE_GAPPS=false
    fi
fi

# Full image name
FULL_IMAGE="${IMAGE_NAME}:${IMAGE_TAG}"
if [[ -n "$PUSH_TO" ]]; then
    FULL_IMAGE="${PUSH_TO}:${IMAGE_TAG}"
fi

log_info "Building image: $FULL_IMAGE"
log_info "Base image: $BASE_IMAGE"
[[ "$INCLUDE_GAPPS" == "true" ]] && log_info "Including GApps: yes"
echo ""

# Build
docker build \
    $DOCKER_ARGS \
    --build-arg BASE_IMAGE="$BASE_IMAGE" \
    --build-arg INCLUDE_GAPPS="$INCLUDE_GAPPS" \
    -t "$FULL_IMAGE" \
    .

log_info "Build complete: $FULL_IMAGE"

# Also tag with local name if pushing
if [[ -n "$PUSH_TO" ]]; then
    docker tag "$FULL_IMAGE" "${IMAGE_NAME}:${IMAGE_TAG}"
    
    log_info "Pushing to registry..."
    docker push "$FULL_IMAGE"
    log_info "Push complete"
fi

# Summary
echo ""
echo "Image built successfully!"
echo ""
echo "Run locally:"
echo "  docker run -itd --privileged --name redroid -p 5555:5555 -p 5900:5900 $FULL_IMAGE"
echo ""
echo "Or use docker-compose:"
echo "  REDROID_IMAGE=$FULL_IMAGE docker-compose up -d"
echo ""
if [[ -n "$PUSH_TO" ]]; then
    echo "Pull from registry:"
    echo "  docker pull $FULL_IMAGE"
fi
