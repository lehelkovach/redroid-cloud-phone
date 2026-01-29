#!/bin/bash
#
# fix-camera-hal.sh - Attempt to enable virtual camera support in Redroid
#
# This script tries multiple approaches to enable camera access in Redroid:
# 1. Verify v4l2loopback device is properly mounted
# 2. Create camera provider HIDL manifest
# 3. Restart camera services
#
# Note: Full camera HAL support requires a custom Redroid build.
# This script provides workarounds for existing limitations.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_header()  { echo -e "\n${BLUE}=== $* ===${NC}"; }

CONTAINER_NAME="${CONTAINER_NAME:-redroid}"
VIDEO_DEVICE="${VIDEO_DEVICE:-/dev/video42}"

# Check if running on remote or local
run_cmd() {
    if [[ -n "${VM_HOST:-}" ]]; then
        ssh -o ConnectTimeout=5 "${SSH_USER:-ubuntu}@${VM_HOST}" "$@"
    else
        eval "$@"
    fi
}

docker_exec() {
    run_cmd "sudo docker exec $CONTAINER_NAME $*"
}

check_prerequisites() {
    log_header "Checking Prerequisites"
    
    # Check v4l2loopback
    log_info "Checking v4l2loopback module..."
    if run_cmd "lsmod | grep -q v4l2loopback"; then
        log_info "v4l2loopback module loaded"
    else
        log_error "v4l2loopback module not loaded"
        log_info "Loading v4l2loopback..."
        run_cmd "sudo modprobe v4l2loopback video_nr=42 card_label=VirtualCam exclusive_caps=1"
    fi
    
    # Check video device
    log_info "Checking video device..."
    if run_cmd "test -e $VIDEO_DEVICE"; then
        log_info "$VIDEO_DEVICE exists"
    else
        log_error "$VIDEO_DEVICE not found"
        return 1
    fi
    
    # Check container
    log_info "Checking Redroid container..."
    if run_cmd "sudo docker ps | grep -q $CONTAINER_NAME"; then
        log_info "Container $CONTAINER_NAME is running"
    else
        log_error "Container $CONTAINER_NAME not running"
        return 1
    fi
    
    # Check device in container
    log_info "Checking video device in container..."
    if docker_exec "test -e $VIDEO_DEVICE" 2>/dev/null; then
        log_info "$VIDEO_DEVICE accessible in container"
    else
        log_warn "$VIDEO_DEVICE not found in container - may need to restart with device mount"
        return 1
    fi
}

check_camera_status() {
    log_header "Camera Status"
    
    log_info "Camera service status:"
    docker_exec "getprop init.svc.cameraserver" || echo "not running"
    
    log_info "Number of cameras detected:"
    docker_exec "dumpsys media.camera 2>/dev/null | grep 'Number of camera devices'" || echo "Unable to query"
    
    log_info "Camera provider status:"
    docker_exec "getprop init.svc.vendor.camera-provider-2-4" 2>/dev/null || echo "not found"
}

try_enable_external_camera() {
    log_header "Attempting External Camera Configuration"
    
    # Set properties that might help
    log_info "Setting camera-related properties..."
    docker_exec "setprop ro.hardware.camera v4l2" 2>/dev/null || true
    docker_exec "setprop ro.camera.device /dev/video42" 2>/dev/null || true
    docker_exec "setprop camera.v4l2.device /dev/video42" 2>/dev/null || true
    
    # Try to create a minimal external camera config
    log_info "Attempting to create external camera config..."
    docker_exec "mkdir -p /vendor/etc/camera" 2>/dev/null || true
    
    # Create external camera config
    docker_exec "cat > /data/local/tmp/external_camera_config.xml << 'EOF'
<?xml version=\"1.0\" encoding=\"UTF-8\" ?>
<ExternalCamera>
    <Provider>
        <DevicePath>/dev/video42</DevicePath>
    </Provider>
</ExternalCamera>
EOF" 2>/dev/null || true
    
    # Try to copy to vendor
    docker_exec "cp /data/local/tmp/external_camera_config.xml /vendor/etc/camera/external_camera_config.xml" 2>/dev/null || log_warn "Cannot write to /vendor (read-only)"
}

restart_camera_services() {
    log_header "Restarting Camera Services"
    
    log_info "Stopping camera server..."
    docker_exec "stop cameraserver" 2>/dev/null || true
    sleep 2
    
    log_info "Starting camera server..."
    docker_exec "start cameraserver" 2>/dev/null || true
    sleep 3
    
    log_info "Camera service status after restart:"
    docker_exec "getprop init.svc.cameraserver"
}

check_v4l2_capabilities() {
    log_header "V4L2 Device Capabilities"
    
    log_info "Checking host device capabilities..."
    run_cmd "v4l2-ctl -d $VIDEO_DEVICE --all 2>/dev/null | head -30" || log_warn "v4l2-ctl not available"
    
    log_info "Device name:"
    run_cmd "cat /sys/class/video4linux/video42/name 2>/dev/null" || echo "unknown"
}

suggest_workarounds() {
    log_header "Recommended Workarounds"
    
    cat << 'EOF'
The Redroid base image does not include a Camera HAL, which means:
- Android Camera API cannot detect the virtual camera
- Camera apps cannot access /dev/video42 directly

WORKAROUNDS:

1. Use VLC to play RTMP stream directly:
   - Install VLC: adb install vlc.apk
   - Open stream: rtmp://127.0.0.1/live/cam

2. Use IP Webcam receiver app:
   - Install IP Webcam or similar receiver app
   - Configure to receive stream from nginx-rtmp

3. Build custom Redroid with External Camera Provider:
   - Clone AOSP hardware/interfaces
   - Build camera/provider/2.4/default with external camera support
   - Create custom Redroid image

4. Use WebRTC for browser-based camera:
   - Stream via WebRTC to a web app in Android Chrome

STREAMING TO VIRTUAL CAMERA:

Even without Camera HAL, the virtual camera pipeline works:
  OBS -> rtmp://VM_IP/live/cam -> ffmpeg-bridge -> /dev/video42

The video data IS being written to /dev/video42, but Android apps
cannot see it because there's no Camera HAL to translate V4L2 to
Android Camera API.

For apps that need camera input, use VLC or a streaming receiver.
EOF
}

show_status_summary() {
    log_header "Status Summary"
    
    echo ""
    echo "Host Device:"
    run_cmd "ls -la $VIDEO_DEVICE 2>/dev/null" || echo "  Not found"
    
    echo ""
    echo "Container Device:"
    docker_exec "ls -la $VIDEO_DEVICE 2>/dev/null" || echo "  Not found"
    
    echo ""
    echo "Camera Service:"
    docker_exec "dumpsys media.camera 2>/dev/null | head -10" || echo "  Unable to query"
    
    echo ""
    echo "Camera HAL Status:"
    if docker_exec "ls /vendor/lib64/hw/camera*.so 2>/dev/null"; then
        log_info "Camera HAL found"
    else
        log_warn "No Camera HAL - apps cannot detect virtual camera"
    fi
}

usage() {
    cat << EOF
Fix Camera HAL - Attempt to enable virtual camera in Redroid

Usage: $0 [command]

Commands:
  check       Check current camera status
  fix         Attempt to fix/enable camera
  status      Show detailed status
  workarounds Show workaround suggestions

Environment Variables:
  VM_HOST         Remote VM IP (if running remotely)
  SSH_USER        SSH user (default: ubuntu)
  CONTAINER_NAME  Docker container name (default: redroid)
  VIDEO_DEVICE    Video device path (default: /dev/video42)

Examples:
  $0 check
  VM_HOST=132.226.155.1 $0 fix
  $0 workarounds

EOF
}

main() {
    case "${1:-check}" in
        check)
            check_prerequisites
            check_camera_status
            check_v4l2_capabilities
            ;;
        fix)
            check_prerequisites
            try_enable_external_camera
            restart_camera_services
            check_camera_status
            show_status_summary
            suggest_workarounds
            ;;
        status)
            show_status_summary
            ;;
        workarounds)
            suggest_workarounds
            ;;
        --help|-h|help)
            usage
            ;;
        *)
            log_error "Unknown command: $1"
            usage
            exit 1
            ;;
    esac
}

main "$@"
