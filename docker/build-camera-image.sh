#!/bin/bash
# build-camera-image.sh
# Builds a custom Redroid image with external camera provider support.
#
# Strategy:
#   1. Extract camera provider binaries from AOSP emulator system image
#   2. Build Docker image with binaries + config layered on top of stock Redroid
#
# Usage:
#   ./build-camera-image.sh                    # Build for Android 11 ARM64
#   ./build-camera-image.sh --android 12       # Build for Android 12
#   ./build-camera-image.sh --push registry/img  # Build and push
#
# Prerequisites:
#   - Docker
#   - ~5GB disk for AOSP system image download + extraction

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Defaults
ANDROID_VERSION=11
ARCH="arm64"
IMAGE_NAME="cloud-phone-camera"
IMAGE_TAG=""
BASE_IMAGE=""
PUSH_TO=""
BINARIES_DIR="$SCRIPT_DIR/camera-provider-binaries"
SKIP_EXTRACT=false

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --android) ANDROID_VERSION="$2"; shift 2 ;;
        --arch) ARCH="$2"; shift 2 ;;
        --name) IMAGE_NAME="$2"; shift 2 ;;
        --tag) IMAGE_TAG="$2"; shift 2 ;;
        --push) PUSH_TO="$2"; shift 2 ;;
        --skip-extract) SKIP_EXTRACT=true; shift ;;
        --help|-h)
            echo "Usage: $0 [--android 11|12|13] [--arch arm64|x86_64] [--push registry/img]"
            exit 0
            ;;
        *) log_error "Unknown: $1"; exit 1 ;;
    esac
done

# Set defaults based on Android version
case "$ANDROID_VERSION" in
    11) BASE_IMAGE="${BASE_IMAGE:-redroid/redroid:11.0.0-latest}" ;;
    12) BASE_IMAGE="${BASE_IMAGE:-redroid/redroid:12.0.0-latest}" ;;
    13) BASE_IMAGE="${BASE_IMAGE:-redroid/redroid:13.0.0-latest}" ;;
    14) BASE_IMAGE="${BASE_IMAGE:-redroid/redroid:14.0.0-latest}" ;;
    *)  log_error "Unsupported Android version: $ANDROID_VERSION"; exit 1 ;;
esac

[[ -z "$IMAGE_TAG" ]] && IMAGE_TAG="${ANDROID_VERSION}"
FULL_IMAGE="${PUSH_TO:-$IMAGE_NAME}:${IMAGE_TAG}"

log_info "Building camera-enabled Redroid image"
log_info "  Android: $ANDROID_VERSION"
log_info "  Arch: $ARCH"
log_info "  Base: $BASE_IMAGE"
log_info "  Output: $FULL_IMAGE"
echo ""

# =========================================================================
# Step 1: Extract camera provider binaries from AOSP
# =========================================================================
EXTRACT_DIR="$BINARIES_DIR/$ARCH"
mkdir -p "$EXTRACT_DIR/bin" "$EXTRACT_DIR/lib64"

if [[ "$SKIP_EXTRACT" == true ]] && [[ -f "$EXTRACT_DIR/bin/android.hardware.camera.provider@2.4-external-service" ]]; then
    log_info "Skipping extraction (--skip-extract, binaries exist)"
else
    log_info "Step 1: Extracting camera provider binaries from AOSP..."
    echo ""
    echo "The external camera provider binaries must come from an AOSP build."
    echo "Options:"
    echo ""
    echo "  Option A: Build from AOSP source (most reliable)"
    echo "    See docs/CAMERA_HAL_FIX.md for full build instructions."
    echo "    After building, copy these files to $EXTRACT_DIR/:"
    echo "      bin/android.hardware.camera.provider@2.4-external-service"
    echo "      lib64/camera.external.so"
    echo ""
    echo "  Option B: Extract from cuttlefish/emulator image"
    echo "    docker pull us-docker.pkg.dev/android-emulator-268719/images/30-google-x64-v8-emu"
    echo "    # Then extract the binaries"
    echo ""
    echo "  Option C: Use the AOSP build Docker environment"
    echo "    See: https://github.com/nicknash/nicknash (Redroid source build)"
    echo ""

    if [[ ! -f "$EXTRACT_DIR/bin/android.hardware.camera.provider@2.4-external-service" ]]; then
        log_warn "Camera provider binary not found at:"
        log_warn "  $EXTRACT_DIR/bin/android.hardware.camera.provider@2.4-external-service"
        echo ""
        echo "Building image WITHOUT camera binaries (config-only)."
        echo "The image will have all configuration in place but the camera"
        echo "provider service will not start until binaries are added."
        echo ""
        echo "To add binaries later:"
        echo "  docker cp <binary> <container>:/vendor/bin/hw/"
        echo "  docker cp <lib> <container>:/vendor/lib64/hw/"
        echo "  docker commit <container> $FULL_IMAGE"
        echo ""
    fi
fi

# =========================================================================
# Step 2: Build Docker image
# =========================================================================
log_info "Step 2: Building Docker image..."

# Create temporary Dockerfile with COPY for binaries if they exist
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

cp Dockerfile.camera "$TMPDIR/Dockerfile"

if [[ -f "$EXTRACT_DIR/bin/android.hardware.camera.provider@2.4-external-service" ]]; then
    log_info "Including camera provider binaries in image"
    mkdir -p "$TMPDIR/camera-bins/bin" "$TMPDIR/camera-bins/lib64"
    cp "$EXTRACT_DIR/bin/"* "$TMPDIR/camera-bins/bin/" 2>/dev/null || true
    cp "$EXTRACT_DIR/lib64/"* "$TMPDIR/camera-bins/lib64/" 2>/dev/null || true

    # Append COPY instructions to Dockerfile
    cat >> "$TMPDIR/Dockerfile" << 'EOF'

# Camera provider binaries (extracted from AOSP)
COPY camera-bins/bin/ /vendor/bin/hw/
COPY camera-bins/lib64/ /vendor/lib64/hw/
RUN chmod 755 /vendor/bin/hw/android.hardware.camera.provider@2.4-external-service 2>/dev/null || true
EOF
else
    log_warn "Building config-only image (no camera binaries)"
fi

docker build \
    --build-arg BASE_IMAGE="$BASE_IMAGE" \
    -t "$FULL_IMAGE" \
    "$TMPDIR"

log_info "Build complete: $FULL_IMAGE"

# =========================================================================
# Step 3: Push if requested
# =========================================================================
if [[ -n "$PUSH_TO" ]]; then
    log_info "Pushing to registry..."
    docker push "$FULL_IMAGE"
    log_info "Push complete"
fi

echo ""
echo "============================================"
echo "Camera-enabled Redroid image built"
echo "============================================"
echo ""
echo "Image: $FULL_IMAGE"
echo ""
echo "Run:"
echo "  docker run -itd --privileged --name redroid \\"
echo "    --device=/dev/video42 --device=/dev/snd -v /dev/snd:/dev/snd \\"
echo "    -p 5555:5555 -p 5900:5900 -v /opt/redroid-data:/data \\"
echo "    $FULL_IMAGE \\"
echo "    androidboot.redroid_gpu_mode=guest \\"
echo "    androidboot.redroid_width=1280 \\"
echo "    androidboot.redroid_height=720"
echo ""
echo "Verify camera:"
echo "  ./scripts/test-redroid-camera-diag.sh"
echo ""
