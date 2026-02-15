#!/bin/bash
# test-redroid-camera-diag.sh
# Diagnostic test that checks Redroid container logs and kernel module state
# to verify virtual camera and audio pipeline prerequisites.
#
# Produces clear PASS/FAIL assertions for each component.
#
# Usage:
#   ./test-redroid-camera-diag.sh [VM_HOST]
#   VM_HOST=132.226.155.1 ./test-redroid-camera-diag.sh

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0
CONTAINER="${CONTAINER_NAME:-redroid}"

ENV_VM_HOST="${VM_HOST:-${DEV_INSTANCE:-}}"
VM_HOST=""

if [[ $# -ge 1 && "$1" != "--local" ]]; then
    VM_HOST="$1"
elif [[ -n "$ENV_VM_HOST" ]]; then
    VM_HOST="$ENV_VM_HOST"
fi

SSH_CMD=()
if [[ -n "$VM_HOST" ]]; then
    SSH_USER="${SSH_USER:-ubuntu}"
    SSH_KEY="${SSH_KEY:-$HOME/.ssh/redroid_oci}"
    SSH_OPTS=(-o ConnectTimeout=10 -o StrictHostKeyChecking=no)
    [[ -f "$SSH_KEY" ]] && SSH_OPTS+=(-i "$SSH_KEY")
    SSH_CMD=(ssh "${SSH_OPTS[@]}" "${SSH_USER}@${VM_HOST}")
fi

run() {
    if [[ ${#SSH_CMD[@]} -gt 0 ]]; then
        "${SSH_CMD[@]}" "$@"
    else
        "$@"
    fi
}

shell() {
    if [[ ${#SSH_CMD[@]} -gt 0 ]]; then
        "${SSH_CMD[@]}" "$1"
    else
        bash -c "$1"
    fi
}

pass() { echo -e "  ${GREEN}PASS${NC}: $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${NC}: $1"; echo -e "       ${RED}→ $2${NC}"; FAIL=$((FAIL + 1)); }
warn() { echo -e "  ${YELLOW}WARN${NC}: $1"; WARN=$((WARN + 1)); }

echo -e "${BLUE}=========================================="
echo "Redroid Camera & Audio Diagnostic Test"
echo "==========================================${NC}"
echo ""
[[ -n "$VM_HOST" ]] && echo "Target: $VM_HOST" || echo "Target: localhost"
echo ""

# =========================================================================
echo -e "${BLUE}[1/7] Host Kernel Modules${NC}"
# =========================================================================

# v4l2loopback
V4L2_VER=$(shell "modinfo v4l2loopback 2>/dev/null | grep '^version:' | awk '{print \$2}'" || true)
if [[ -n "$V4L2_VER" ]]; then
    if [[ "$V4L2_VER" == "0.12.3" || "$V4L2_VER" < "0.12.5" ]]; then
        fail "v4l2loopback version $V4L2_VER is too old" \
             "Upgrade to v0.12.7+ or v0.15+. Old versions report M2M caps instead of Capture/Output on kernel 5.15+."
    else
        pass "v4l2loopback version $V4L2_VER"
    fi
else
    if shell "lsmod | grep -q v4l2loopback"; then
        warn "v4l2loopback loaded but version unknown"
    else
        fail "v4l2loopback kernel module not loaded" \
             "Run: sudo modprobe v4l2loopback devices=1 video_nr=42 card_label=VirtualCam exclusive_caps=0 max_openers=10"
    fi
fi

# Video device
if run test -e /dev/video42; then
    pass "/dev/video42 exists"
else
    fail "/dev/video42 does not exist" \
         "v4l2loopback not loaded or configured with wrong video_nr"
fi

# v4l2 capabilities
V4L2_CAPS=$(shell "v4l2-ctl --device=/dev/video42 --all 2>&1 | head -20" || true)
if echo "$V4L2_CAPS" | grep -q "Video Capture"; then
    pass "v4l2loopback has Video Capture capability"
elif echo "$V4L2_CAPS" | grep -q "Video Memory-to-Memory"; then
    fail "v4l2loopback reports M2M instead of Capture" \
         "v4l2loopback version too old for this kernel. Upgrade: git clone https://github.com/umlaeute/v4l2loopback && make && sudo make install"
else
    warn "Could not determine v4l2loopback capabilities"
fi

# exclusive_caps check
EXCL=$(shell "cat /sys/module/v4l2loopback/parameters/exclusive_caps 2>/dev/null | head -c1" || true)
if [[ "$EXCL" == "Y" ]]; then
    warn "exclusive_caps=1 is set. This can prevent concurrent read+write. Use exclusive_caps=0 with v0.15+"
fi

# snd-aloop
if shell "lsmod | grep -q snd_aloop"; then
    pass "snd-aloop (ALSA loopback) module loaded"
else
    fail "snd-aloop kernel module not loaded" \
         "Run: sudo modprobe snd-aloop index=10 id=Loopback pcm_substreams=1"
fi

if shell "aplay -l 2>/dev/null | grep -q Loopback"; then
    pass "ALSA Loopback device visible"
else
    fail "ALSA Loopback device not visible to aplay" \
         "snd-aloop may be loaded but misconfigured"
fi

echo ""

# =========================================================================
echo -e "${BLUE}[2/7] Docker & Redroid Container${NC}"
# =========================================================================

if shell "sudo docker ps --format '{{.Names}}' | grep -qx '$CONTAINER'"; then
    pass "Redroid container '$CONTAINER' is running"
else
    fail "Redroid container '$CONTAINER' not running" \
         "Start: sudo systemctl start redroid-container"
fi

REDROID_IMAGE=$(shell "sudo docker inspect $CONTAINER --format '{{.Config.Image}}' 2>/dev/null" || true)
if [[ -n "$REDROID_IMAGE" ]]; then
    pass "Redroid image: $REDROID_IMAGE"
else
    warn "Could not determine Redroid image"
fi

echo ""

# =========================================================================
echo -e "${BLUE}[3/7] Virtual Devices Inside Container${NC}"
# =========================================================================

# /dev/video42 in container
if shell "sudo docker exec $CONTAINER test -e /dev/video42 2>/dev/null"; then
    pass "/dev/video42 mounted inside container"
else
    fail "/dev/video42 not visible inside Redroid container" \
         "Container needs --device=/dev/video42 or /dev/video42 mapped in docker run"
fi

# /dev/snd in container
if shell "sudo docker exec $CONTAINER test -d /dev/snd 2>/dev/null"; then
    pass "/dev/snd mounted inside container"
else
    fail "/dev/snd not visible inside Redroid container" \
         "Container needs --device=/dev/snd or -v /dev/snd:/dev/snd"
fi

# ALSA loopback inside container
ASOUND_CARDS=$(shell "sudo docker exec $CONTAINER cat /proc/asound/cards 2>/dev/null" || true)
if echo "$ASOUND_CARDS" | grep -q "Loopback"; then
    pass "ALSA Loopback visible inside Redroid (/proc/asound/cards)"
else
    warn "ALSA Loopback NOT visible inside Redroid. Virtual mic will not work."
fi

echo ""

# =========================================================================
echo -e "${BLUE}[4/7] Redroid Camera HAL (Android-side)${NC}"
# =========================================================================

# Camera HAL library
CAMERA_HAL=$(shell "sudo docker exec $CONTAINER ls /vendor/lib64/hw/camera*.so /vendor/lib/hw/camera*.so 2>/dev/null" || true)
if [[ -n "$CAMERA_HAL" ]]; then
    pass "Camera HAL found: $CAMERA_HAL"
else
    fail "No Camera HAL (camera.*.so) found in Redroid /vendor/lib*/hw/" \
         "Standard Redroid images do not include Camera HAL. Need custom image with external camera provider."
fi

# Camera provider service
CAM_PROVIDER_RC=$(shell "sudo docker exec $CONTAINER ls /vendor/etc/init/*camera*provider*.rc 2>/dev/null" || true)
if [[ -n "$CAM_PROVIDER_RC" ]]; then
    pass "Camera provider init script: $CAM_PROVIDER_RC"
else
    fail "No camera provider HIDL service init script in container" \
         "Need android.hardware.camera.provider@2.4-external-service in the image"
fi

# Camera provider in manifest
CAM_MANIFEST=$(shell "sudo docker exec $CONTAINER grep -rl 'camera.provider' /vendor/etc/vintf/ 2>/dev/null" || true)
if [[ -n "$CAM_MANIFEST" ]]; then
    pass "Camera provider declared in VINTF manifest"
else
    fail "Camera provider NOT in VINTF manifest" \
         "Need <hal> entry for android.hardware.camera.provider@2.4 in device manifest"
fi

# external_camera_config.xml
EXT_CAM_CFG=$(shell "sudo docker exec $CONTAINER ls /vendor/etc/external_camera_config.xml 2>/dev/null" || true)
if [[ -n "$EXT_CAM_CFG" ]]; then
    pass "external_camera_config.xml found"
else
    fail "external_camera_config.xml missing" \
         "Required for external camera provider to discover /dev/video42"
fi

echo ""

# =========================================================================
echo -e "${BLUE}[5/7] Redroid Camera Service Logs${NC}"
# =========================================================================

# cameraserver process
CAM_SVC=$(shell "sudo docker exec $CONTAINER getprop init.svc.cameraserver 2>/dev/null" || true)
if [[ "$CAM_SVC" == "running" ]]; then
    pass "Android cameraserver is running"
else
    warn "cameraserver status: '${CAM_SVC:-not found}'"
fi

# camera provider process
CAM_PROV_SVC=$(shell "sudo docker exec $CONTAINER getprop init.svc.vendor.camera-provider-2-4 2>/dev/null" || true)
CAM_PROV_EXT=$(shell "sudo docker exec $CONTAINER getprop init.svc.vendor.camera-provider-2-4-ext 2>/dev/null" || true)
if [[ "$CAM_PROV_SVC" == "running" || "$CAM_PROV_EXT" == "running" ]]; then
    pass "Camera provider service is running"
else
    fail "Camera provider service NOT running (vendor.camera-provider-2-4: '${CAM_PROV_SVC:-}', ext: '${CAM_PROV_EXT:-}')" \
         "Camera HAL/provider not included in Redroid image"
fi

# Number of cameras detected
NUM_CAMS=$(shell "sudo docker exec $CONTAINER sh -c 'dumpsys media.camera 2>/dev/null | grep \"Number of camera devices\" | grep -o \"[0-9]*\" | tail -1'" || echo "0")
if [[ "${NUM_CAMS:-0}" -gt 0 ]]; then
    pass "Android detects $NUM_CAMS camera(s)"
else
    fail "Android detects 0 cameras" \
         "Camera HAL is missing from this Redroid image. Apps cannot see /dev/video42."
fi

echo ""

# =========================================================================
echo -e "${BLUE}[6/7] Streaming Pipeline Services${NC}"
# =========================================================================

for svc in nginx-rtmp ffmpeg-bridge control-api; do
    STATUS=$(run systemctl is-active "$svc" 2>/dev/null || echo "not found")
    if [[ "$STATUS" == "active" ]]; then
        pass "$svc service is active"
    else
        fail "$svc service is $STATUS" \
             "Run: sudo systemctl start $svc"
    fi
done

# ffmpeg writing to video42
if shell "sudo fuser /dev/video42 2>/dev/null | grep -q ."; then
    pass "A process is writing to /dev/video42"
else
    warn "No process currently writing to /dev/video42 (normal if no active stream)"
fi

echo ""

# =========================================================================
echo -e "${BLUE}[7/7] Audio Pipeline${NC}"
# =========================================================================

AUDIO_SVC=$(shell "sudo docker exec $CONTAINER getprop init.svc.audioserver 2>/dev/null" || true)
if [[ "$AUDIO_SVC" == "running" ]]; then
    pass "Android audioserver is running"
else
    warn "audioserver status: '${AUDIO_SVC:-not found}'"
fi

echo ""

# =========================================================================
# Summary
# =========================================================================
echo -e "${BLUE}=========================================="
echo "Diagnostic Summary"
echo "==========================================${NC}"
echo ""
echo -e "  ${GREEN}PASS${NC}: $PASS"
echo -e "  ${RED}FAIL${NC}: $FAIL"
echo -e "  ${YELLOW}WARN${NC}: $WARN"
echo ""

if [[ $FAIL -gt 0 ]]; then
    echo -e "${RED}DIAGNOSIS: $FAIL check(s) failed.${NC}"
    echo ""
    # Provide targeted advice
    if [[ -z "$CAMERA_HAL" || -z "$CAM_PROVIDER_RC" ]]; then
        echo -e "${RED}CRITICAL: Camera HAL missing from Redroid image.${NC}"
        echo ""
        echo "The standard redroid/redroid images do NOT include a Camera HAL."
        echo "Android apps cannot detect /dev/video42 as a camera without it."
        echo ""
        echo "To fix this, you need a custom Redroid image with:"
        echo "  - android.hardware.camera.provider@2.4-external-service"
        echo "  - external_camera_config.xml pointing to /dev/video42"
        echo "  - VINTF manifest entry for camera provider"
        echo ""
        echo "Build with: ./docker/build.sh --camera"
        echo "See:        docs/CAMERA_HAL_FIX.md"
    fi
    echo ""
    exit 1
else
    echo -e "${GREEN}All critical checks passed.${NC}"
    exit 0
fi
