#!/bin/bash
# FFmpeg bridge: RTMP → Virtual Camera (/dev/video42) + ALSA Loopback (virtual mic)
# Runs as systemd service (ffmpeg-bridge.service)
#
# Pipeline: OBS -> rtmp://IP/live/cam -> nginx-rtmp -> this bridge -> /dev/video42 + hw:Loopback
#
# Comprehensive logging for diagnosing pipeline issues.

set -e

# Configuration (override via environment)
RTMP_URL="${RTMP_URL:-rtmp://127.0.0.1/live/cam}"
VIDEO_DEVICE="${VIDEO_DEVICE:-/dev/video42}"
AUDIO_DEVICE="${AUDIO_DEVICE:-hw:Loopback,0,0}"
VIDEO_WIDTH="${VIDEO_WIDTH:-1080}"
VIDEO_HEIGHT="${VIDEO_HEIGHT:-1920}"
VIDEO_FPS="${VIDEO_FPS:-15}"
AUDIO_RATE="${AUDIO_RATE:-44100}"
AUDIO_CHANNELS="${AUDIO_CHANNELS:-2}"
PROBE_INTERVAL="${PROBE_INTERVAL:-2}"
RETRY_DELAY="${RETRY_DELAY:-3}"
LOG_LEVEL="${FFMPEG_LOG_LEVEL:-warning}"
STATS_INTERVAL=10

# Counters for diagnostics
STREAM_COUNT=0
TOTAL_ERRORS=0
LAST_STREAM_START=""
LAST_STREAM_END=""

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [bridge] $1"
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [bridge] ERROR: $1" >&2
    TOTAL_ERRORS=$((TOTAL_ERRORS + 1))
}

log_debug() {
    if [[ "$LOG_LEVEL" == "debug" || "$LOG_LEVEL" == "info" ]]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [bridge] DEBUG: $1"
    fi
}

cleanup() {
    log "Shutting down FFmpeg bridge (streams=$STREAM_COUNT errors=$TOTAL_ERRORS)"
    # Kill all child processes
    local children
    children=$(jobs -p 2>/dev/null) || true
    if [[ -n "$children" ]]; then
        kill $children 2>/dev/null || true
        wait $children 2>/dev/null || true
    fi
    pkill -P $$ 2>/dev/null || true
    exit 0
}

trap cleanup SIGTERM SIGINT SIGQUIT

# =========================================================================
# Startup diagnostics
# =========================================================================
log "========================================"
log "FFmpeg RTMP Bridge - Starting"
log "========================================"
log "Config:"
log "  RTMP Source:    $RTMP_URL"
log "  Video Output:   $VIDEO_DEVICE (${VIDEO_WIDTH}x${VIDEO_HEIGHT} @ ${VIDEO_FPS}fps)"
log "  Audio Output:   $AUDIO_DEVICE (${AUDIO_RATE}Hz, ${AUDIO_CHANNELS}ch)"
log "  Log Level:      $LOG_LEVEL"
log ""

# ---- Check video device ----
if [ ! -e "$VIDEO_DEVICE" ]; then
    log_error "Video device $VIDEO_DEVICE not found"
    log "  v4l2loopback module may not be loaded."
    log "  Run: sudo modprobe v4l2loopback devices=1 video_nr=42 card_label=VirtualCam exclusive_caps=0 max_openers=10"
    exit 1
fi

# Check v4l2loopback version and capabilities
V4L2_VER=$(modinfo v4l2loopback 2>/dev/null | grep '^version:' | awk '{print $2}' || echo "unknown")
V4L2_CAPS=$(v4l2-ctl --device="$VIDEO_DEVICE" --all 2>/dev/null | head -15 || echo "")
log "v4l2loopback: version=$V4L2_VER device=$VIDEO_DEVICE"

if echo "$V4L2_CAPS" | grep -q "Video Memory-to-Memory"; then
    log_error "v4l2loopback reports M2M capability (version $V4L2_VER is too old for this kernel)"
    log "  Upgrade: git clone https://github.com/umlaeute/v4l2loopback && make && sudo make install"
    log "  Then reload: sudo rmmod v4l2loopback && sudo modprobe v4l2loopback devices=1 video_nr=42 exclusive_caps=0 max_openers=10"
    exit 1
fi

if echo "$V4L2_CAPS" | grep -q "Video Output"; then
    log "v4l2loopback capability: Video Output (writer mode) - OK"
elif echo "$V4L2_CAPS" | grep -q "Video Capture"; then
    log "v4l2loopback capability: Video Capture - OK"
else
    log "WARNING: Could not determine v4l2loopback capabilities. Proceeding anyway."
fi

EXCL_CAPS=$(cat /sys/module/v4l2loopback/parameters/exclusive_caps 2>/dev/null | head -c1 || echo "?")
if [[ "$EXCL_CAPS" == "Y" ]]; then
    log "WARNING: exclusive_caps=1 detected. Readers may not be able to open video42 while we write."
    log "  Consider: sudo rmmod v4l2loopback && sudo modprobe v4l2loopback devices=1 video_nr=42 exclusive_caps=0 max_openers=10"
fi

# ---- Check ALSA loopback ----
AUDIO_ENABLED=false
if aplay -l 2>/dev/null | grep -q "Loopback"; then
    AUDIO_ENABLED=true
    log "ALSA Loopback: detected - audio output enabled"
else
    log "WARNING: ALSA Loopback device not found. Audio output disabled."
    log "  Run: sudo modprobe snd-aloop index=10 id=Loopback pcm_substreams=1"
fi

# ---- Check ffmpeg ----
FFMPEG_VER=$(ffmpeg -version 2>&1 | head -1 || echo "unknown")
log "ffmpeg: $FFMPEG_VER"

# ---- Check reconnect support ----
RECONNECT_SUPPORTED=false
if ! ffmpeg -hide_banner -reconnect 1 -f lavfi -i anullsrc -t 0.1 -f null - 2>&1 | grep -qi "Option reconnect not found"; then
    RECONNECT_SUPPORTED=true
    log "ffmpeg reconnect: supported"
else
    log "ffmpeg reconnect: NOT supported (older ffmpeg version)"
fi

# ---- Wait for nginx-rtmp ----
log "Waiting for RTMP server (nginx-rtmp)..."
RTMP_READY=false
for i in {1..60}; do
    if curl -s --max-time 2 http://127.0.0.1:8081/health > /dev/null 2>&1; then
        log "RTMP server is ready (waited ${i}s)"
        RTMP_READY=true
        break
    fi
    sleep 1
done

if [[ "$RTMP_READY" != "true" ]]; then
    log_error "RTMP server not ready after 60 seconds. Continuing anyway (may fail)."
fi

log ""
log "Bridge ready. Waiting for RTMP streams on $RTMP_URL"
log "========================================"

# =========================================================================
# Main loop - detect stream, bridge to virtual devices, retry on disconnect
# =========================================================================
while true; do
    log "Polling for RTMP stream at $RTMP_URL (every ${PROBE_INTERVAL}s)..."

    # Poll for active stream
    POLL_COUNT=0
    while true; do
        POLL_COUNT=$((POLL_COUNT + 1))
        if timeout 8 ffprobe -v quiet -show_streams "$RTMP_URL" 2>/dev/null | grep -q "codec_type"; then
            log "Stream detected after ${POLL_COUNT} probes"
            break
        fi
        # Log every 30 probes (~60s) so we know it's still alive
        if (( POLL_COUNT % 30 == 0 )); then
            log "Still waiting for stream (${POLL_COUNT} probes, $((POLL_COUNT * PROBE_INTERVAL))s elapsed)"
        fi
        sleep "$PROBE_INTERVAL"
    done

    STREAM_COUNT=$((STREAM_COUNT + 1))
    LAST_STREAM_START=$(date '+%Y-%m-%d %H:%M:%S')
    log "--- Stream #${STREAM_COUNT} starting at ${LAST_STREAM_START} ---"

    # Probe stream details for logging
    STREAM_INFO=$(timeout 10 ffprobe -v quiet -show_streams -show_format "$RTMP_URL" 2>/dev/null || true)
    if [[ -n "$STREAM_INFO" ]]; then
        VCODEC=$(echo "$STREAM_INFO" | grep 'codec_name' | head -1 | cut -d= -f2)
        VRES=$(echo "$STREAM_INFO" | grep -E 'width|height' | head -2 | tr '\n' ' ')
        ACODEC=$(echo "$STREAM_INFO" | grep 'codec_name' | tail -1 | cut -d= -f2)
        ASAMPLE=$(echo "$STREAM_INFO" | grep 'sample_rate' | head -1 | cut -d= -f2)
        log "Stream info: video=${VCODEC} ${VRES} audio=${ACODEC} rate=${ASAMPLE}"
    fi

    # Build FFmpeg command
    FFMPEG_ARGS=(-hide_banner -loglevel "$LOG_LEVEL" -nostdin)

    if [[ "$RECONNECT_SUPPORTED" == "true" ]]; then
        FFMPEG_ARGS+=(-reconnect 1 -reconnect_at_eof 1 -reconnect_streamed 1 -reconnect_delay_max 2)
    fi

    FFMPEG_ARGS+=(
        -i "$RTMP_URL"
        -map 0:v:0
        -vf "scale=${VIDEO_WIDTH}:${VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,pad=${VIDEO_WIDTH}:${VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
        -r "$VIDEO_FPS"
        -f v4l2 "$VIDEO_DEVICE"
    )

    # Audio output (if ALSA loopback available)
    if [[ "$AUDIO_ENABLED" == "true" ]]; then
        FFMPEG_ARGS+=(-map 0:a:0 -ar "$AUDIO_RATE" -ac "$AUDIO_CHANNELS" -f alsa "$AUDIO_DEVICE")
    fi

    log "Running: ffmpeg ${FFMPEG_ARGS[*]}"

    # Run FFmpeg, capture stderr for diagnostics
    FFMPEG_LOG="/tmp/ffmpeg-bridge-stream-${STREAM_COUNT}.log"
    ffmpeg "${FFMPEG_ARGS[@]}" 2>"$FFMPEG_LOG" &
    FFMPEG_PID=$!

    log "FFmpeg started (PID: $FFMPEG_PID, log: $FFMPEG_LOG)"

    # Monitor FFmpeg while it runs
    MONITOR_COUNT=0
    while kill -0 "$FFMPEG_PID" 2>/dev/null; do
        sleep "$STATS_INTERVAL"
        MONITOR_COUNT=$((MONITOR_COUNT + 1))

        # Check if process is actually writing to video device
        if fuser "$VIDEO_DEVICE" 2>/dev/null | grep -q "$FFMPEG_PID"; then
            SIGNAL=$(v4l2-ctl --device="$VIDEO_DEVICE" --get-input 2>/dev/null | grep -o 'ok\|no signal' || echo "unknown")
            if (( MONITOR_COUNT % 6 == 0 )); then  # Every ~60s
                log "Stream #${STREAM_COUNT} running (${MONITOR_COUNT}x${STATS_INTERVAL}s, v4l2_signal=${SIGNAL})"
            fi
        else
            log_debug "FFmpeg PID $FFMPEG_PID not writing to $VIDEO_DEVICE (may be buffering)"
        fi

        # Check for errors in ffmpeg log
        if [[ -f "$FFMPEG_LOG" ]]; then
            ERROR_COUNT=$(grep -c -iE 'error|failed|broken pipe|connection refused' "$FFMPEG_LOG" 2>/dev/null || true)
            ERROR_COUNT=$(echo "$ERROR_COUNT" | tr -d '[:space:]' | head -c 10)
            ERROR_COUNT=${ERROR_COUNT:-0}
            if [[ "$ERROR_COUNT" =~ ^[0-9]+$ ]] && [[ "$ERROR_COUNT" -gt 0 ]] && (( MONITOR_COUNT % 3 == 0 )); then
                LAST_ERR=$(grep -iE 'error|failed|broken' "$FFMPEG_LOG" | tail -1)
                log "WARNING: FFmpeg errors detected ($ERROR_COUNT total). Latest: $LAST_ERR"
            fi
        fi
    done

    # FFmpeg exited
    wait "$FFMPEG_PID" 2>/dev/null
    EXIT_CODE=$?
    LAST_STREAM_END=$(date '+%Y-%m-%d %H:%M:%S')

    log "--- Stream #${STREAM_COUNT} ended (exit=$EXIT_CODE, started=$LAST_STREAM_START, ended=$LAST_STREAM_END) ---"

    # Log last few lines of ffmpeg output for diagnostics
    if [[ -f "$FFMPEG_LOG" ]]; then
        TAIL=$(tail -5 "$FFMPEG_LOG" 2>/dev/null)
        if [[ -n "$TAIL" ]]; then
            log "FFmpeg last output:"
            while IFS= read -r line; do
                log "  > $line"
            done <<< "$TAIL"
        fi

        # Keep only last 5 stream logs
        if [[ $STREAM_COUNT -gt 5 ]]; then
            rm -f "/tmp/ffmpeg-bridge-stream-$((STREAM_COUNT - 5)).log" 2>/dev/null || true
        fi
    fi

    if [[ $EXIT_CODE -ne 0 ]]; then
        TOTAL_ERRORS=$((TOTAL_ERRORS + 1))
        log "Stream ended with error (total_errors=$TOTAL_ERRORS). Retrying in ${RETRY_DELAY}s..."
    else
        log "Stream ended normally. Waiting for next stream..."
    fi

    sleep "$RETRY_DELAY"
done
