#!/bin/bash
# generate-test-video.sh
# Generates a test video file for virtual device output testing.
# Use with: ./test-virtual-device-output.sh --source /path/to/output.mp4
#
# Usage: ./generate-test-video.sh [OUTPUT_FILE] [DURATION]
#   OUTPUT_FILE  Default: /tmp/redroid-test-video.mp4
#   DURATION     Default: 15 seconds

set -euo pipefail

OUTPUT="${1:-/tmp/redroid-test-video.mp4}"
DURATION="${2:-15}"

# Match ffmpeg-bridge expected format
WIDTH=1080
HEIGHT=1920
FPS=15
AUDIO_RATE=44100

echo "Generating test video..."
echo "  Output: $OUTPUT"
echo "  Duration: ${DURATION}s"
echo "  Format: ${WIDTH}x${HEIGHT} @ ${FPS}fps, H.264 + AAC"
echo ""

ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "testsrc2=size=${WIDTH}x${HEIGHT}:rate=${FPS}" \
    -f lavfi -i "sine=frequency=440:sample_rate=${AUDIO_RATE}" \
    -t "$DURATION" \
    -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p \
    -c:a aac -ar "${AUDIO_RATE}" -b:a 128k \
    -shortest \
    "$OUTPUT"

echo "Done. Use with: ./test-virtual-device-output.sh --source $OUTPUT"
