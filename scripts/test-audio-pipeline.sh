#!/bin/bash
# test-audio-pipeline.sh
# Tests the ALSA loopback microphone pipeline
#
# Usage: sudo ./test-audio-pipeline.sh

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=========================================="
echo "ALSA Loopback Microphone Test"
echo "==========================================${NC}"
echo ""

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}This script must be run as root (use sudo)${NC}"
   exit 1
fi

# Check snd-aloop module
echo -e "${BLUE}[1/5] Checking ALSA Loopback Module${NC}"
if lsmod | grep -q snd_aloop; then
    echo -e "  ${GREEN}✓${NC} snd-aloop module loaded"
else
    echo -e "  ${RED}✗${NC} snd-aloop module not loaded"
    echo "  Loading module..."
    modprobe snd-aloop index=10 id=Loopback pcm_substreams=1
    sleep 1
    if lsmod | grep -q snd_aloop; then
        echo -e "  ${GREEN}✓${NC} Module loaded successfully"
    else
        echo -e "  ${RED}✗${NC} Failed to load module"
        exit 1
    fi
fi

echo ""

# List ALSA devices
echo -e "${BLUE}[2/5] Checking ALSA Devices${NC}"
echo "Playback devices (aplay -l):"
aplay -l 2>/dev/null | grep -A 1 "Loopback" || echo -e "  ${RED}✗${NC} No Loopback playback device found"

echo ""
echo "Recording devices (arecord -l):"
arecord -l 2>/dev/null | grep -A 1 "Loopback" || echo -e "  ${RED}✗${NC} No Loopback recording device found"

echo ""

# Test recording from loopback
echo -e "${BLUE}[3/5] Testing Loopback Recording${NC}"
TEST_FILE="/tmp/loopback-test.wav"
if arecord -D hw:Loopback,1,0 -f cd -d 2 "$TEST_FILE" 2>/dev/null; then
    if [ -f "$TEST_FILE" ] && [ -s "$TEST_FILE" ]; then
        echo -e "  ${GREEN}✓${NC} Can record from Loopback"
        FILE_SIZE=$(stat -f%z "$TEST_FILE" 2>/dev/null || stat -c%s "$TEST_FILE" 2>/dev/null)
        echo "  File size: $FILE_SIZE bytes"
        rm -f "$TEST_FILE"
    else
        echo -e "  ${YELLOW}○${NC} Recording test file is empty (may be normal if no audio input)"
    fi
else
    echo -e "  ${YELLOW}○${NC} Recording test failed (may need audio input)"
fi

echo ""

# Check if FFmpeg is using loopback
echo -e "${BLUE}[4/5] Checking FFmpeg Bridge${NC}"
if systemctl is-active --quiet ffmpeg-bridge; then
    echo -e "  ${GREEN}✓${NC} FFmpeg bridge service is running"
    
    # Check if it's outputting to loopback
    if journalctl -u ffmpeg-bridge --no-pager -n 20 | grep -q "Loopback\|hw:Loopback"; then
        echo -e "  ${GREEN}✓${NC} FFmpeg is configured for ALSA Loopback"
    else
        echo -e "  ${YELLOW}○${NC} FFmpeg may not be using Loopback (check logs)"
    fi
else
    echo -e "  ${YELLOW}○${NC} FFmpeg bridge service is not running"
fi

echo ""

# Check Redroid audio access
echo -e "${BLUE}[5/5] Checking Redroid Audio Access${NC}"
if command -v docker &>/dev/null; then
    if docker ps --format '{{.Names}}' | grep -q "^redroid$"; then
        echo -e "  ${GREEN}✓${NC} Redroid is running"
        echo ""
        echo "  To use Loopback as microphone in Redroid:"
        echo "  1. Open an audio recording app in Redroid"
        echo "  2. Select 'Loopback' or 'hw:Loopback,0,0' as input source"
        echo "  3. Audio from OBS stream will be available"
    else
        echo -e "  ${YELLOW}○${NC} Redroid is not running"
    fi
else
    echo -e "  ${YELLOW}○${NC} Docker not installed"
fi

echo ""
echo -e "${BLUE}=========================================="
echo "Test Summary"
echo "==========================================${NC}"
echo ""
echo "ALSA Loopback Device:"
echo "  Playback: hw:Loopback,0,0 (what FFmpeg writes to)"
echo "  Recording: hw:Loopback,1,0 (what Redroid reads from)"
echo ""
echo "When OBS streams audio:"
echo "  1. OBS → RTMP (port 1935)"
echo "  2. nginx-rtmp receives stream"
echo "  3. FFmpeg extracts audio → hw:Loopback,0,0"
echo "  4. Redroid reads from hw:Loopback,1,0 (microphone)"
echo ""


