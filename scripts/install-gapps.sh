#!/bin/bash
# Install Google Apps (GApps) into Redroid Container
#
# Supports multiple GApps variants:
# - pico: Minimum (Play Store + Services only)
# - nano: Small (+ Play Services, Sync)
# - micro: Medium (+ Calendar, Exchange)
# - mini: Larger (+ Maps, YouTube, Gmail)
# - full: Everything
#
# Usage:
#   ./install-gapps.sh [variant]
#   ./install-gapps.sh pico
#   ./install-gapps.sh --mindthegapps
#
# Note: This script requires an active Redroid container

set -euo pipefail

VARIANT="${1:-pico}"
CONTAINER="${REDROID_CONTAINER:-redroid}"
ADB_TARGET="${ADB_CONNECT:-127.0.0.1:5555}"
GAPPS_DIR="/opt/gapps"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

# Check prerequisites
check_prereqs() {
    if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
        log_error "Redroid container '$CONTAINER' not running"
        exit 1
    fi
    
    if ! command -v adb &>/dev/null; then
        log_error "ADB not installed"
        exit 1
    fi
    
    # Ensure ADB connected
    if ! adb -s "$ADB_TARGET" get-state &>/dev/null; then
        log_info "Connecting to ADB..."
        adb connect "$ADB_TARGET" || true
        sleep 2
    fi
}

# Download OpenGApps
download_opengapps() {
    local arch="arm64"
    local android="11.0"
    local variant="$1"
    
    mkdir -p "$GAPPS_DIR"
    cd "$GAPPS_DIR"
    
    log_info "Downloading OpenGApps ($variant)..."
    
    # OpenGApps download URL pattern
    # Note: OpenGApps may have limited ARM64 Android 11 support
    local base_url="https://sourceforge.net/projects/opengapps/files"
    local download_url="${base_url}/${arch}/${android}/${variant}/download"
    
    # Try alternative: NikGApps (better Android 11+ support)
    if [[ "$variant" == "pico" ]] || [[ "$variant" == "core" ]]; then
        log_info "Trying NikGApps (better Android 11 support)..."
        local nikgapps_url="https://sourceforge.net/projects/nikgapps/files/Releases/NikGapps-arm64-11-signed.zip/download"
        
        if wget -q --show-progress -O gapps.zip "$nikgapps_url" 2>/dev/null; then
            log_info "Downloaded NikGApps"
            return 0
        fi
    fi
    
    # Fallback to MindTheGapps (specifically for LineageOS/Redroid)
    log_info "Downloading MindTheGapps..."
    local mtg_url="https://github.com/AdrianDC/AdrianDC/releases/download/latest/mindthegapps-arm64-11.0.0.zip"
    
    # Alternative mirror
    local mtg_mirror="https://downloads.codefi.re/jdcteam/nicholaschum/mindthegapps/MindTheGapps-11.0.0-arm64-20211108_160621.zip"
    
    if wget -q --show-progress -O gapps.zip "$mtg_url" 2>/dev/null || \
       wget -q --show-progress -O gapps.zip "$mtg_mirror" 2>/dev/null; then
        log_info "Downloaded MindTheGapps"
        return 0
    fi
    
    log_error "Failed to download GApps. Manual download required."
    echo ""
    echo "Download manually from:"
    echo "  NikGApps: https://nikgapps.com/"
    echo "  MindTheGapps: https://gitlab.com/nicholaschum/mindthegapps"
    echo ""
    echo "Place the zip file at: $GAPPS_DIR/gapps.zip"
    echo "Then run: $0 --install-local"
    
    return 1
}

# Extract and install GApps
install_gapps() {
    cd "$GAPPS_DIR"
    
    if [[ ! -f "gapps.zip" ]]; then
        log_error "gapps.zip not found in $GAPPS_DIR"
        return 1
    fi
    
    log_info "Extracting GApps..."
    rm -rf extracted
    mkdir -p extracted
    unzip -q gapps.zip -d extracted
    
    log_info "Installing GApps to Redroid..."
    
    # Method 1: Copy directly to container (best effort; may fail on read-only /system)
    local direct_copy_ok=true
    if ! docker exec "$CONTAINER" sh -c "mount -o remount,rw /system" >/dev/null 2>&1; then
        log_warn "Unable to remount /system as rw; skipping direct copy"
        direct_copy_ok=false
    fi

    if [[ "$direct_copy_ok" == "true" ]] && docker exec "$CONTAINER" test -d /system/priv-app; then
        # Find and copy priv-app APKs
        find extracted -name "*.apk" -path "*priv-app*" | while read -r apk; do
            local app_name
            app_name=$(basename "$(dirname "$apk")")
            log_info "  Installing (priv): $app_name"
            docker exec "$CONTAINER" mkdir -p "/system/priv-app/$app_name" || true
            docker cp "$apk" "$CONTAINER:/system/priv-app/$app_name/" || true
        done
        
        # Find and copy app APKs
        find extracted -name "*.apk" -path "*app/*" | while read -r apk; do
            local app_name
            app_name=$(basename "$(dirname "$apk")")
            log_info "  Installing (app): $app_name"
            docker exec "$CONTAINER" mkdir -p "/system/app/$app_name" || true
            docker cp "$apk" "$CONTAINER:/system/app/$app_name/" || true
        done
        
        # Copy libs if present
        if [[ -d "extracted/system/lib64" ]]; then
            docker cp extracted/system/lib64/. "$CONTAINER:/system/lib64/" || true
        fi
        
        # Copy framework files
        if [[ -d "extracted/system/framework" ]]; then
            docker cp extracted/system/framework/. "$CONTAINER:/system/framework/" || true
        fi
    fi
    
    # Method 2: Install via ADB (for APKs)
    log_info "Installing via ADB..."
    
    # Core GApps packages
    local core_apks=(
        "GoogleServicesFramework"
        "GmsCore"  # Google Play Services
        "GoogleLoginService"
        "Phonesky"  # Play Store
    )
    
    for pkg in "${core_apks[@]}"; do
        local apk=$(find extracted -name "*.apk" -path "*$pkg*" | head -1)
        if [[ -n "$apk" ]]; then
            log_info "  Installing via ADB: $pkg"
            adb -s "$ADB_TARGET" install -r "$apk" 2>/dev/null || true
        fi
    done
    
    log_info "GApps installation complete"
}

# Alternative: Use pre-built Redroid image with GApps
use_gapps_image() {
    log_info "Switching to Redroid image with GApps..."
    
    # Community images with GApps pre-installed
    local gapps_images=(
        "redroid/redroid:11.0.0-gapps"
        "redroid/redroid:12.0.0-gapps"
        "abing7k/redroid:gapps"
    )
    
    echo ""
    echo "Available Redroid images with GApps:"
    for img in "${gapps_images[@]}"; do
        echo "  - $img"
    done
    echo ""
    echo "To use, update your config or run:"
    echo "  docker pull <image>"
    echo "  Then restart Redroid with the new image"
    echo ""
    echo "Or modify /opt/redroid-env.conf:"
    echo "  REDROID_IMAGE=redroid/redroid:11.0.0-gapps"
    echo ""
    echo "Then restart:"
    echo "  sudo systemctl restart redroid-container"
}

# Setup for Google Play certification
setup_device_certification() {
    log_info "Setting up device certification..."
    
    # Get device ID for Google registration
    local device_id=$(adb -s "$ADB_TARGET" shell 'settings get secure android_id' 2>/dev/null)
    local gsf_id=$(adb -s "$ADB_TARGET" shell 'cat /data/data/com.google.android.gsf/databases/gservices.db 2>/dev/null | sqlite3 -batch "select value from main where name = '"'"'android_id'"'"'" 2>/dev/null' || echo "unknown")
    
    echo ""
    echo "Device Registration (for uncertified device):"
    echo ""
    echo "If Play Store shows 'Device not certified', register at:"
    echo "  https://www.google.com/android/uncertified/"
    echo ""
    echo "Your device IDs:"
    echo "  Android ID: $device_id"
    echo "  GSF ID: $gsf_id"
    echo ""
}

# Main
main() {
    case "$VARIANT" in
        --help|-h)
            echo "Usage: $0 [variant|option]"
            echo ""
            echo "Variants: pico, nano, micro, mini, full"
            echo ""
            echo "Options:"
            echo "  --use-image      Show pre-built GApps images"
            echo "  --install-local  Install from $GAPPS_DIR/gapps.zip"
            echo "  --setup-cert     Setup device certification"
            echo ""
            exit 0
            ;;
        --use-image)
            use_gapps_image
            exit 0
            ;;
        --install-local)
            check_prereqs
            install_gapps
            setup_device_certification
            exit 0
            ;;
        --setup-cert)
            check_prereqs
            setup_device_certification
            exit 0
            ;;
        pico|nano|micro|mini|full)
            check_prereqs
            
            if download_opengapps "$VARIANT"; then
                install_gapps
                setup_device_certification
            else
                log_warn "Automatic download failed"
                use_gapps_image
            fi
            ;;
        *)
            log_error "Unknown variant: $VARIANT"
            echo "Use: pico, nano, micro, mini, full"
            exit 1
            ;;
    esac
    
    log_info "Done! Restart Redroid for changes to take effect:"
    echo "  sudo systemctl restart redroid-container"
}

main "$@"
