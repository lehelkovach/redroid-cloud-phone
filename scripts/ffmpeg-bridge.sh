#!/bin/bash
# FFmpeg bridge: RTMP → Virtual Camera + ALSA Loopback
# Runs as systemd service

set -e

# Configuration
RTMP_URL="rtmp://127.0.0.1/live/cam"
VIDEO_DEVICE="/dev/video42"
AUDIO_DEVICE="hw:Loopback,0,0"

# Video settings (match OBS output)
VIDEO_WIDTH=1080
VIDEO_HEIGHT=1920
VIDEO_FPS=15

# Audio settings
AUDIO_RATE=44100
AUDIO_CHANNELS=2

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

cleanup() {
    log "Stopping FFmpeg bridge..."
    pkill -P $$ 2>/dev/null || true
    exit 0
}

trap cleanup SIGTERM SIGINT SIGQUIT

log "========================================"
log "FFmpeg RTMP Bridge"
log "========================================"
log "RTMP Source: $RTMP_URL"
log "Video Output: $VIDEO_DEVICE"
log "Audio Output: $AUDIO_DEVICE"
log ""

# Check video device
if [ ! -e "$VIDEO_DEVICE" ]; then
    log "ERROR: Video device $VIDEO_DEVICE not found"
    log "Ensure v4l2loopback module is loaded:"
    log "  sudo modprobe v4l2loopback devices=1 video_nr=42"
    exit 1
fi

# Check ALSA loopback
if ! aplay -l 2>/dev/null | grep -q "Loopback"; then
    log "WARNING: ALSA Loopback device not found"
    log "Ensure snd-aloop module is loaded:"
    log "  sudo modprobe snd-aloop index=10 id=Loopback"
    AUDIO_OUTPUT=""
else
    AUDIO_OUTPUT="-f alsa $AUDIO_DEVICE"
fi

# Wait for nginx-rtmp to be ready
log "Waiting for RTMP server..."
for i in {1..30}; do
    if curl -s http://127.0.0.1:8081/health > /dev/null 2>&1; then
        log "RTMP server is ready"
        break
    fi
    sleep 1
done

# Main loop - reconnect on stream end
while true; do
    log "Waiting for RTMP stream at $RTMP_URL..."
    
    # Check if stream is active (poll every 2 seconds)
    while true; do
        # Try to probe the stream
        if ffprobe -v quiet -show_streams "$RTMP_URL" 2>/dev/null | grep -q "codec_type"; then
            log "Stream detected, starting pipeline..."
            break
        fi
        sleep 2
    done
    
    # Build FFmpeg command as an array to avoid eval/quoting issues
    FFMPEG_ARGS=(
        -hide_banner -loglevel warning
    )

    # Add reconnect flags only if supported by this ffmpeg build
    if ! ffmpeg -hide_banner -reconnect 1 -f lavfi -i anullsrc -t 0.1 -f null - 2>&1 | grep -qi "Option reconnect not found"; then
        FFMPEG_ARGS+=(-reconnect 1 -reconnect_at_eof 1 -reconnect_streamed 1 -reconnect_delay_max 2)
    fi

    FFMPEG_ARGS+=(
        -i "$RTMP_URL"
        -map 0:v:0
        -vf "scale=${VIDEO_WIDTH}:${VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,pad=${VIDEO_WIDTH}:${VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
        -r "$VIDEO_FPS"
        -f v4l2 "$VIDEO_DEVICE"
    )
    
    # Audio output to ALSA loopback (if available)
    if [ -n "$AUDIO_OUTPUT" ]; then
        FFMPEG_ARGS+=(-map 0:a:0 -ar "$AUDIO_RATE" -ac "$AUDIO_CHANNELS" -f alsa "$AUDIO_DEVICE")
    fi
    
    log "Starting FFmpeg: ffmpeg ${FFMPEG_ARGS[*]}"
    
    # Run FFmpeg
    ffmpeg "${FFMPEG_ARGS[@]}" &
    FFMPEG_PID=$!
    
    log "FFmpeg started (PID: $FFMPEG_PID)"
    
    # Wait for FFmpeg to exit
    wait $FFMPEG_PID
    EXIT_CODE=$?
    
    log "FFmpeg exited with code: $EXIT_CODE"
    
    # Brief pause before retry
    sleep 2
done
