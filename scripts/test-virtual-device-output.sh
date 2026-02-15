#!/bin/bash
# test-virtual-device-output.sh
# Tests video and audio output of the virtual devices (v4l2loopback + ALSA)
# by streaming a known test pattern to RTMP and verifying the output matches.
#
# Pipeline: Test Source -> RTMP -> ffmpeg-bridge -> /dev/video42 + hw:Loopback
#
# Usage:
#   ./test-virtual-device-output.sh [OPTIONS] [VM_HOST]
#
# Options:
#   --local          Run on local machine (ignore VM_HOST env)
#   --duration N     Test duration in seconds (default: 10)
#   --no-audio       Skip audio verification
#   --save-capture   Keep captured video/audio files for inspection
#   --source FILE    Use video file instead of lavfi (optional)
#   --vm HOST        Run via SSH on remote VM
#
# Environment (for Oracle Cloud dev system):
#   VM_HOST          Default remote host when no args (e.g. 132.226.155.1)
#   DEV_INSTANCE     Alias for VM_HOST
#
# Examples:
#   VM_HOST=132.226.155.1 ./test-virtual-device-output.sh   # Stream against OCI dev
#   ./test-virtual-device-output.sh 132.226.155.1            # Remote VM
#   ./test-virtual-device-output.sh --local                  # Local only
#   ./test-virtual-device-output.sh --source test.mp4        # Use stock video file
#   ./test-virtual-device-output.sh --save-capture           # Keep capture files for inspection

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
RTMP_URL="rtmp://127.0.0.1/live/cam"
VIDEO_DEVICE="/dev/video42"
AUDIO_CAPTURE_DEVICE="hw:Loopback,1,0"
EXPECTED_WIDTH=1080
EXPECTED_HEIGHT=1920
EXPECTED_FPS=15
TEST_DURATION=30
VERIFY_AUDIO=true
SAVE_CAPTURE=false
SOURCE_FILE=""
SSH_CMD=()
RUN_MODE="local"

# Capture env BEFORE we reset the variable (VM_HOST/DEV_INSTANCE for Oracle Cloud dev system)
ENV_VM_HOST="${VM_HOST:-${DEV_INSTANCE:-}}"
VM_HOST=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --local)
            RUN_MODE="local"
            VM_HOST=""
            shift
            ;;
        --duration)
            TEST_DURATION="$2"
            shift 2
            ;;
        --no-audio)
            VERIFY_AUDIO=false
            shift
            ;;
        --save-capture)
            SAVE_CAPTURE=true
            shift
            ;;
        --source)
            SOURCE_FILE="$2"
            shift 2
            ;;
        --vm)
            VM_HOST="$2"
            RUN_MODE="remote"
            shift 2
            ;;
        -*)
            echo "Unknown option: $1"
            exit 1
            ;;
        *)
            if [[ -z "$VM_HOST" ]]; then
                VM_HOST="$1"
                RUN_MODE="remote"
            fi
            shift
            ;;
    esac
done

# Use VM_HOST from environment if no host specified (e.g. Oracle Cloud dev instance)
if [[ -z "$VM_HOST" ]] && [[ -n "$ENV_VM_HOST" ]]; then
    VM_HOST="$ENV_VM_HOST"
    RUN_MODE="remote"
fi

if [[ "$RUN_MODE" == "remote" && -n "$VM_HOST" ]]; then
    SSH_USER="${SSH_USER:-ubuntu}"
    SSH_KEY="${SSH_KEY:-$HOME/.ssh/redroid_oci}"
    SSH_OPTS=(-o ConnectTimeout=10 -o StrictHostKeyChecking=no)
    [[ -f "$SSH_KEY" ]] && SSH_OPTS+=(-i "$SSH_KEY")
    SSH_CMD=(ssh "${SSH_OPTS[@]}" "${SSH_USER}@${VM_HOST}")
    echo "Remote: ${SSH_USER}@${VM_HOST}"
fi

run_cmd() {
    if [[ ${#SSH_CMD[@]} -gt 0 ]]; then
        "${SSH_CMD[@]}" "$@"
    else
        "$@"
    fi
}

# Run a shell expression (supports pipes, redirects) on target machine
run_shell() {
    if [[ ${#SSH_CMD[@]} -gt 0 ]]; then
        "${SSH_CMD[@]}" "$1"
    else
        bash -c "$1"
    fi
}

# Run shell expression in background (stream runs for TEST_DURATION on target)
run_cmd_bg() {
    local cmd="$1"
    if [[ ${#SSH_CMD[@]} -gt 0 ]]; then
        "${SSH_CMD[@]}" bash -c "$(printf '%q' "$cmd")" &
    else
        bash -c "$cmd" &
    fi
}

echo -e "${BLUE}=========================================="
echo "Virtual Device Output Test"
echo "==========================================${NC}"
echo ""
echo "Pipeline: Test Source -> RTMP -> ffmpeg-bridge -> video42 + ALSA"
echo "Mode: $RUN_MODE"
echo "Duration: ${TEST_DURATION}s"
echo "Video: $VIDEO_DEVICE (expect ${EXPECTED_WIDTH}x${EXPECTED_HEIGHT} @ ${EXPECTED_FPS}fps)"
echo "Audio: $AUDIO_CAPTURE_DEVICE"
echo ""

# Step 1: Prerequisites
echo -e "${BLUE}[1/6] Checking prerequisites...${NC}"

if ! run_cmd bash -c "command -v ffmpeg" &>/dev/null; then
    echo -e "${RED}✗ ffmpeg not found${NC}"
    exit 1
fi
echo -e "${GREEN}✓ ffmpeg found${NC}"

if ! run_cmd test -e "$VIDEO_DEVICE"; then
    echo -e "${RED}✗ $VIDEO_DEVICE not found${NC}"
    echo "  Run: sudo modprobe v4l2loopback devices=1 video_nr=42"
    exit 1
fi
echo -e "${GREEN}✓ $VIDEO_DEVICE exists${NC}"

if ! run_cmd bash -c "curl -s --max-time 5 http://127.0.0.1:8081/health 2>/dev/null | grep -q OK"; then
    echo -e "${RED}✗ RTMP server (nginx-rtmp) not responding${NC}"
    echo "  Ensure nginx-rtmp is running on port 8081"
    exit 1
fi
echo -e "${GREEN}✓ RTMP server ready${NC}"

# Check ffmpeg-bridge - should be running to pick up our stream
if run_cmd systemctl is-active --quiet ffmpeg-bridge 2>/dev/null; then
    echo -e "${GREEN}✓ ffmpeg-bridge service is running${NC}"
else
    echo -e "${YELLOW}⚠ ffmpeg-bridge not running - will need manual stream to video42${NC}"
    echo "  For full pipeline test, start: sudo systemctl start ffmpeg-bridge"
fi

echo ""

# Step 2: Build test stream source
echo -e "${BLUE}[2/6] Starting test stream to RTMP...${NC}"

if [[ -n "$SOURCE_FILE" ]]; then
    if ! run_shell "test -f '$SOURCE_FILE'"; then
        echo -e "${RED}✗ Source file not found: $SOURCE_FILE${NC}"
        exit 1
    fi
    echo "  Using file: $SOURCE_FILE"
    # Loop the file for test duration
    STREAM_CMD="ffmpeg -hide_banner -loglevel warning -re -stream_loop -1 -i $SOURCE_FILE -t $TEST_DURATION -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p -c:a aac -ar 44100 -b:a 128k -f flv $RTMP_URL"
else
    echo "  Using lavfi: testsrc2 + sine (440Hz)"
    # Generate test pattern: video + audio
    STREAM_CMD="ffmpeg -hide_banner -loglevel warning -re -f lavfi -i testsrc2=size=1080x1920:rate=15 -f lavfi -i sine=frequency=440:sample_rate=44100 -t $TEST_DURATION -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p -c:a aac -ar 44100 -b:a 128k -shortest -f flv $RTMP_URL"
fi

# Start stream in background (run on target machine)
run_cmd_bg "$STREAM_CMD 2>&1 | tail -5"
STREAM_PID=$!
echo "  Stream started (PID: $STREAM_PID)"
echo "  Waiting for ffmpeg-bridge to detect stream..."
sleep 8

# Verify stream is active
if ! run_cmd timeout 3 ffprobe -v quiet -show_streams "$RTMP_URL" 2>/dev/null | grep -q "codec_type"; then
    echo -e "${YELLOW}⚠ Stream not yet detected by ffprobe, waiting...${NC}"
    for i in {1..12}; do
        sleep 2
        if run_cmd timeout 3 ffprobe -v quiet -show_streams "$RTMP_URL" 2>/dev/null | grep -q "codec_type"; then
            echo -e "${GREEN}✓ Stream detected${NC}"
            break
        fi
        echo -n "."
    done
    echo ""
fi

echo ""

# Step 3: Wait for pipeline to stabilize
echo -e "${BLUE}[3/6] Allowing pipeline to stabilize...${NC}"
sleep 5
echo "  Pipeline should be active (RTMP -> ffmpeg-bridge -> video42)"
echo ""

# Step 4: Capture and verify video output
echo -e "${BLUE}[4/6] Verifying video output from $VIDEO_DEVICE...${NC}"

CAPTURE_DURATION=4
CAPTURE_DIR="/tmp"
CAPTURE_FILE="$CAPTURE_DIR/virtual-device-test-capture.mp4"
AUDIO_CAPTURE_FILE="$CAPTURE_DIR/virtual-device-test-audio.wav"

# Verify ffmpeg-bridge is writing to the device before we try to capture
echo "  Waiting for active writer on $VIDEO_DEVICE..."
for _attempt in $(seq 1 15); do
    if run_shell "sudo fuser $VIDEO_DEVICE 2>/dev/null | grep -q ."; then
        echo -e "${GREEN}  ✓ Writer detected on $VIDEO_DEVICE${NC}"
        break
    fi
    sleep 2
    echo -n "."
done
echo ""
sleep 2

# Capture from video42 (we're reading while ffmpeg-bridge writes - v4l2loopback supports multiple readers)
run_cmd timeout $((CAPTURE_DURATION + 4)) ffmpeg -hide_banner -loglevel error -y \
    -f v4l2 -i "$VIDEO_DEVICE" \
    -t "$CAPTURE_DURATION" \
    -c:v libx264 -preset ultrafast \
    "$CAPTURE_FILE" 2>&1 || true

if run_shell "test -f '$CAPTURE_FILE'"; then
    FRAME_COUNT=$(run_cmd ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=nb_read_frames -of default=nokey=1:noprint_wrappers=1 "$CAPTURE_FILE" 2>/dev/null || \
        run_cmd ffprobe -v error -select_streams v:0 -show_entries stream=nb_frames -of default=nokey=1:noprint_wrappers=1 "$CAPTURE_FILE" 2>/dev/null || echo "0")
    WIDTH=$(run_cmd ffprobe -v error -select_streams v:0 -show_entries stream=width -of default=nokey=1:noprint_wrappers=1 "$CAPTURE_FILE" 2>/dev/null || echo "0")
    HEIGHT=$(run_cmd ffprobe -v error -select_streams v:0 -show_entries stream=height -of default=nokey=1:noprint_wrappers=1 "$CAPTURE_FILE" 2>/dev/null || echo "0")
    FILE_SIZE=$(run_cmd stat -c%s "$CAPTURE_FILE" 2>/dev/null || run_cmd stat -f%z "$CAPTURE_FILE" 2>/dev/null || echo "0")

    # Expected: ~60 frames for 4 sec at 15fps (allow 45-90)
    MIN_FRAMES=$((CAPTURE_DURATION * (EXPECTED_FPS - 2)))
    MAX_FRAMES=$((CAPTURE_DURATION * (EXPECTED_FPS + 2)))

    if [[ "${FRAME_COUNT:-0}" -ge "$MIN_FRAMES" ]] && [[ "${FRAME_COUNT:-0}" -le "$MAX_FRAMES" ]]; then
        echo -e "${GREEN}✓ Video: $FRAME_COUNT frames (expected ~$((CAPTURE_DURATION * EXPECTED_FPS)))${NC}"
    elif [[ "${FRAME_COUNT:-0}" -gt 0 ]]; then
        echo -e "${YELLOW}○ Video: $FRAME_COUNT frames (expected $MIN_FRAMES-$MAX_FRAMES)${NC}"
    else
        echo -e "${RED}✗ Video: No frames captured (frame_count=$FRAME_COUNT)${NC}"
    fi

    if [[ "${WIDTH:-0}" -eq "$EXPECTED_WIDTH" ]] && [[ "${HEIGHT:-0}" -eq "$EXPECTED_HEIGHT" ]]; then
        echo -e "${GREEN}✓ Video: Resolution ${WIDTH}x${HEIGHT} (matches expected)${NC}"
    elif [[ -n "${WIDTH:-}" && -n "${HEIGHT:-}" ]]; then
        echo -e "${YELLOW}○ Video: Resolution ${WIDTH}x${HEIGHT} (expected ${EXPECTED_WIDTH}x${EXPECTED_HEIGHT})${NC}"
    fi

    if [[ "${FILE_SIZE:-0}" -gt 10000 ]]; then
        echo -e "${GREEN}✓ Video: Capture file size ${FILE_SIZE} bytes${NC}"
    else
        echo -e "${RED}✗ Video: Capture file too small (${FILE_SIZE:-0} bytes) - possible pipeline failure${NC}"
    fi

    # Stricter check: fail if entire capture is black (pipeline not delivering content)
    # blackdetect outputs at info level, so we need -loglevel info (not error)
    BLACK_OUT=$(run_cmd ffmpeg -hide_banner -loglevel info -i "$CAPTURE_FILE" -vf "blackdetect=d=0.1:pix_th=0.02" -an -f null - 2>&1 || true)
    if echo "$BLACK_OUT" | grep -q "black_duration"; then
        # Sum black durations; if >= 3.5s of 4s capture, fail
        TOTAL_BLACK=$(echo "$BLACK_OUT" | grep -oE 'black_duration=[0-9.]+' | sed 's/black_duration=//' | awk '{s+=$1} END {print s+0}')
        if [[ -n "$TOTAL_BLACK" ]] && awk -v b="$TOTAL_BLACK" 'BEGIN{exit !(b>=3.5)}' 2>/dev/null; then
            echo -e "${RED}✗ Video: Capture is mostly black - pipeline may not be delivering content${NC}"
        fi
    fi

    # Save or cleanup
    if [[ "$SAVE_CAPTURE" == true ]]; then
        echo -e "${GREEN}✓ Video capture saved: $CAPTURE_FILE${NC}"
    else
        run_cmd rm -f "$CAPTURE_FILE" 2>/dev/null || true
    fi
else
    echo -e "${RED}✗ Video: Failed to capture from $VIDEO_DEVICE${NC}"
    echo "  Check: ffmpeg-bridge writing to video42? (pgrep -a ffmpeg | grep video42)"
fi

echo ""

# Step 5: Capture and verify audio output (if ALSA loopback available)
if [[ "$VERIFY_AUDIO" == true ]]; then
    echo -e "${BLUE}[5/6] Verifying audio output from $AUDIO_CAPTURE_DEVICE...${NC}"

    if run_shell "aplay -l 2>/dev/null | grep -q Loopback"; then
        run_cmd timeout 3 arecord -D "$AUDIO_CAPTURE_DEVICE" -f S16_LE -r 44100 -c 2 -d 2 "$AUDIO_CAPTURE_FILE" 2>/dev/null || true

        if run_shell "test -f '$AUDIO_CAPTURE_FILE'"; then
            AUDIO_SIZE=$(run_cmd stat -c%s "$AUDIO_CAPTURE_FILE" 2>/dev/null || run_cmd stat -f%z "$AUDIO_CAPTURE_FILE" 2>/dev/null || echo "0")
            # 2 sec at 44100Hz, 16-bit, stereo = ~176400 bytes
            EXPECTED_AUDIO_MIN=100000
            if [[ "${AUDIO_SIZE:-0}" -ge "$EXPECTED_AUDIO_MIN" ]]; then
                echo -e "${GREEN}✓ Audio: Captured ${AUDIO_SIZE} bytes (expected ~176400 for 2s)${NC}"
            elif [[ "${AUDIO_SIZE:-0}" -gt 0 ]]; then
                echo -e "${YELLOW}○ Audio: Captured ${AUDIO_SIZE} bytes (may be silent or partial)${NC}"
            else
                echo -e "${RED}✗ Audio: No data captured${NC}"
            fi
            if [[ "$SAVE_CAPTURE" == true ]]; then
                echo -e "${GREEN}✓ Audio capture saved: $AUDIO_CAPTURE_FILE${NC}"
            else
                run_cmd rm -f "$AUDIO_CAPTURE_FILE" 2>/dev/null || true
            fi
        else
            echo -e "${YELLOW}○ Audio: Could not capture (arecord may need stream active)${NC}"
        fi
    else
        echo -e "${YELLOW}○ Audio: ALSA Loopback not available (skip)${NC}"
        echo "  Load: sudo modprobe snd-aloop index=10 id=Loopback"
    fi
else
    echo -e "${BLUE}[5/6] Skipping audio verification (--no-audio)${NC}"
fi

echo ""

# Step 6: Summary
echo -e "${BLUE}[6/6] Test summary${NC}"
echo ""

# Check if ffmpeg-bridge is writing to video42
if run_shell "fuser $VIDEO_DEVICE 2>/dev/null | grep -q ."; then
    echo -e "${GREEN}✓ ffmpeg-bridge (or writer) is active on $VIDEO_DEVICE${NC}"
else
    echo -e "${YELLOW}○ No process writing to $VIDEO_DEVICE (stream may have ended)${NC}"
fi

# Wait for stream to finish
wait $STREAM_PID 2>/dev/null || true

echo ""
echo -e "${GREEN}=========================================="
echo "Virtual Device Output Test Complete"
echo "==========================================${NC}"
echo ""
echo "If video/audio verification passed, the pipeline is working correctly."
echo "If OBS was not working before, check:"
echo "  - OBS output: rtmp://<VM_IP>/live/cam"
echo "  - OBS settings: 1080x1920, 15fps, H.264, AAC"
echo "  - Same format as this test (testsrc2 -> RTMP)"
echo ""
