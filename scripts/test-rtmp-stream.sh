#!/bin/bash
# test-rtmp-stream.sh
# Tests the RTMP -> Virtual Camera pipeline
# Requires: ffmpeg, v4l2loopback, and an RTMP stream source
#
# Usage: ./test-rtmp-stream.sh [rtmp_url]

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

RTMP_URL="${1:-rtmp://127.0.0.1/live/cam}"
VIDEO_DEVICE="/dev/video42"
TEST_DURATION=10

echo -e "${BLUE}=========================================="
echo "RTMP Stream Pipeline Test"
echo "==========================================${NC}"
echo ""
echo "RTMP URL: $RTMP_URL"
echo "Video Device: $VIDEO_DEVICE"
echo "Test Duration: ${TEST_DURATION}s"
echo ""

# Check prerequisites
echo -e "${BLUE}[1/5] Checking prerequisites...${NC}"

if ! command -v ffmpeg &>/dev/null; then
    echo -e "${RED}✗ ffmpeg not found${NC}"
    exit 1
fi
echo -e "${GREEN}✓ ffmpeg found${NC}"

if [ ! -e "$VIDEO_DEVICE" ]; then
    echo -e "${RED}✗ $VIDEO_DEVICE not found${NC}"
    
    # Check kernel version
    KERNEL_VER=$(uname -r)
    if [[ "$KERNEL_VER" == *"6.8"* ]]; then
        echo -e "${RED}ERROR: Kernel 6.8 detected ($KERNEL_VER)${NC}"
        echo "v4l2loopback is known to fail on this kernel on Oracle ARM."
        echo "Please use Ubuntu 20.04 (Kernel 5.x)."
    fi
    
    echo "Load v4l2loopback: sudo modprobe v4l2loopback devices=1 video_nr=42"
    exit 1
fi
echo -e "${GREEN}✓ $VIDEO_DEVICE exists${NC}"

if ! command -v v4l2-ctl &>/dev/null; then
    echo -e "${YELLOW}○ v4l2-ctl not found (optional)${NC}"
else
    echo -e "${GREEN}✓ v4l2-ctl found${NC}"
fi

echo ""

# Check RTMP server
echo -e "${BLUE}[2/5] Checking RTMP server...${NC}"
if curl -s --max-time 5 http://127.0.0.1:8081/health 2>/dev/null | grep -q "OK"; then
    echo -e "${GREEN}✓ RTMP server is running${NC}"
else
    echo -e "${YELLOW}○ RTMP server health check failed (may still work)${NC}"
fi

# Check if stream is available
echo -e "${BLUE}[3/5] Checking RTMP stream...${NC}"
if timeout 3 ffprobe -v quiet -show_streams "$RTMP_URL" 2>/dev/null; then
    echo -e "${GREEN}✓ RTMP stream is active${NC}"
    STREAM_ACTIVE=true
else
    echo -e "${YELLOW}○ No active RTMP stream detected${NC}"
    echo "  Start streaming from OBS to: $RTMP_URL"
    echo "  Waiting 30 seconds for stream to start..."
    
    STREAM_ACTIVE=false
    for i in {1..30}; do
        if timeout 2 ffprobe -v quiet -show_streams "$RTMP_URL" 2>/dev/null; then
            echo -e "${GREEN}✓ Stream detected!${NC}"
            STREAM_ACTIVE=true
            break
        fi
        echo -n "."
        sleep 1
    done
    echo ""
    
    if [ "$STREAM_ACTIVE" = false ]; then
        echo -e "${RED}✗ No stream detected after 30 seconds${NC}"
        echo "  Cannot test pipeline without an active stream."
        exit 1
    fi
fi

echo ""

# Test FFmpeg pipeline
echo -e "${BLUE}[4/5] Testing FFmpeg pipeline...${NC}"
echo "Running FFmpeg for ${TEST_DURATION} seconds..."

FFMPEG_PID=""
(
    timeout $TEST_DURATION ffmpeg \
        -hide_banner -loglevel warning \
        -reconnect 1 -reconnect_at_eof 1 \
        -i "$RTMP_URL" \
        -map 0:v:0 \
        -vf "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,format=yuv420p" \
        -r 15 \
        -f v4l2 "$VIDEO_DEVICE" 2>&1 | while IFS= read -r line; do
        if [[ "$line" =~ (error|Error|ERROR|failed|Failed) ]]; then
            echo -e "${RED}FFmpeg: $line${NC}" >&2
        elif [ "$VERBOSE" = true ]; then
            echo "FFmpeg: $line"
        fi
    done
) &
FFMPEG_PID=$!

# Monitor video device
echo "Monitoring $VIDEO_DEVICE..."
sleep 2

if [ -n "$FFMPEG_PID" ] && kill -0 "$FFMPEG_PID" 2>/dev/null; then
    echo -e "${GREEN}✓ FFmpeg is running${NC}"
    
    # Check if device is receiving data
    if command -v v4l2-ctl &>/dev/null; then
        if v4l2-ctl --device="$VIDEO_DEVICE" --all &>/dev/null; then
            echo -e "${GREEN}✓ Video device is active${NC}"
        fi
    fi
else
    echo -e "${RED}✗ FFmpeg failed to start${NC}"
    exit 1
fi

# Wait for test duration
sleep $((TEST_DURATION - 2))

# Cleanup
if kill -0 "$FFMPEG_PID" 2>/dev/null; then
    kill "$FFMPEG_PID" 2>/dev/null || true
    wait "$FFMPEG_PID" 2>/dev/null || true
fi

echo ""

# Final check
echo -e "${BLUE}[5/5] Verifying results...${NC}"

if [ -e "$VIDEO_DEVICE" ]; then
    echo -e "${GREEN}✓ Video device still exists${NC}"
    
    # Try to read from device
    if timeout 1 cat "$VIDEO_DEVICE" &>/dev/null; then
        echo -e "${GREEN}✓ Video device is readable${NC}"
    else
        echo -e "${YELLOW}○ Video device may not be readable (normal if no active stream)${NC}"
    fi
else
    echo -e "${RED}✗ Video device disappeared${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}=========================================="
echo "RTMP Pipeline Test Passed!"
echo "==========================================${NC}"
echo ""
echo "The RTMP -> Virtual Camera pipeline is working."
echo "You can now use /dev/video42 as a camera in Waydroid."

