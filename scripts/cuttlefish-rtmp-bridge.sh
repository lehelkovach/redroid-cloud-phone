#!/bin/bash
# cuttlefish-rtmp-bridge.sh
# OBS RTMP -> dual camera sink bridge for Cuttlefish-oriented workflows.
#
# This script does not hardcode Cuttlefish camera injection internals. Instead it:
# 1) ingests RTMP from OBS/nginx-rtmp
# 2) normalizes video format (yuv420p, fps, resolution)
# 3) publishes to independently configurable FRONT/BACK sinks
# 4) optionally starts front/back injector commands that consume those sinks
#
# Usage:
#   ./scripts/cuttlefish-rtmp-bridge.sh [OPTIONS]
#
# Options:
#   --rtmp-url URL            Input RTMP URL (default: rtmp://127.0.0.1/live/cam)
#   --front-sink URI          Front camera sink URI (default: udp://127.0.0.1:23000?pkt_size=1316)
#   --back-sink URI           Back camera sink URI (default: udp://127.0.0.1:23001?pkt_size=1316)
#   --mic-sink URI            Mic sink URI (default: udp://127.0.0.1:23010?pkt_size=1316)
#   --video-width N           Output width (default: 1280)
#   --video-height N          Output height (default: 720)
#   --video-fps N             Output fps (default: 30)
#   --video-bitrate RATE      Output bitrate (default: 4M)
#   --audio-rate N            Audio sample rate (default: 44100)
#   --audio-channels N        Audio channels (default: 2)
#   --audio-bitrate RATE      Audio bitrate (default: 128k)
#   --log-dir DIR             Log directory (default: /tmp/cuttlefish-bridge)
#   --front-cmd CMD           Optional command consuming {FRONT_URI}
#   --back-cmd CMD            Optional command consuming {BACK_URI}
#   --mic-cmd CMD             Optional command consuming {MIC_URI}
#   --probe-interval SEC      Stream probe interval (default: 2)
#   --retry-delay SEC         Restart delay (default: 3)
#   --dry-run                 Print resolved commands and exit
#   --help                    Show help
#
# Placeholders supported in --front-cmd / --back-cmd:
#   {FRONT_URI} {BACK_URI} {RTMP_URL} {LOG_DIR}

set -euo pipefail

BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

RTMP_URL="rtmp://127.0.0.1/live/cam"
FRONT_SINK_URI="udp://127.0.0.1:23000?pkt_size=1316"
BACK_SINK_URI="udp://127.0.0.1:23001?pkt_size=1316"
MIC_SINK_URI="udp://127.0.0.1:23010?pkt_size=1316"
VIDEO_WIDTH="1280"
VIDEO_HEIGHT="720"
VIDEO_FPS="30"
VIDEO_BITRATE="4M"
AUDIO_RATE="44100"
AUDIO_CHANNELS="2"
AUDIO_BITRATE="128k"
LOG_DIR="/tmp/cuttlefish-bridge"
FRONT_CMD=""
BACK_CMD=""
MIC_CMD=""
PROBE_INTERVAL="2"
RETRY_DELAY="3"
DRY_RUN="false"

STREAM_COUNT=0
TOTAL_ERRORS=0
FFMPEG_PID=""
FRONT_PID=""
BACK_PID=""
MIC_PID=""

usage() {
    cat <<'EOF'
Usage:
  ./scripts/cuttlefish-rtmp-bridge.sh [OPTIONS]

Options:
  --rtmp-url URL            Input RTMP URL (default: rtmp://127.0.0.1/live/cam)
  --front-sink URI          Front camera sink URI (default: udp://127.0.0.1:23000?pkt_size=1316)
  --back-sink URI           Back camera sink URI (default: udp://127.0.0.1:23001?pkt_size=1316)
  --mic-sink URI            Mic sink URI (default: udp://127.0.0.1:23010?pkt_size=1316)
  --video-width N           Output width (default: 1280)
  --video-height N          Output height (default: 720)
  --video-fps N             Output fps (default: 30)
  --video-bitrate RATE      Output bitrate (default: 4M)
  --audio-rate N            Audio sample rate (default: 44100)
  --audio-channels N        Audio channels (default: 2)
  --audio-bitrate RATE      Audio bitrate (default: 128k)
  --log-dir DIR             Log directory (default: /tmp/cuttlefish-bridge)
  --front-cmd CMD           Optional command consuming {FRONT_URI}
  --back-cmd CMD            Optional command consuming {BACK_URI}
  --mic-cmd CMD             Optional command consuming {MIC_URI}
  --probe-interval SEC      Stream probe interval (default: 2)
  --retry-delay SEC         Restart delay (default: 3)
  --dry-run                 Print resolved commands and exit
  --help                    Show help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --rtmp-url) RTMP_URL="${2:-$RTMP_URL}"; shift 2 ;;
        --front-sink) FRONT_SINK_URI="${2:-$FRONT_SINK_URI}"; shift 2 ;;
        --back-sink) BACK_SINK_URI="${2:-$BACK_SINK_URI}"; shift 2 ;;
        --mic-sink) MIC_SINK_URI="${2:-$MIC_SINK_URI}"; shift 2 ;;
        --video-width) VIDEO_WIDTH="${2:-$VIDEO_WIDTH}"; shift 2 ;;
        --video-height) VIDEO_HEIGHT="${2:-$VIDEO_HEIGHT}"; shift 2 ;;
        --video-fps) VIDEO_FPS="${2:-$VIDEO_FPS}"; shift 2 ;;
        --video-bitrate) VIDEO_BITRATE="${2:-$VIDEO_BITRATE}"; shift 2 ;;
        --audio-rate) AUDIO_RATE="${2:-$AUDIO_RATE}"; shift 2 ;;
        --audio-channels) AUDIO_CHANNELS="${2:-$AUDIO_CHANNELS}"; shift 2 ;;
        --audio-bitrate) AUDIO_BITRATE="${2:-$AUDIO_BITRATE}"; shift 2 ;;
        --log-dir) LOG_DIR="${2:-$LOG_DIR}"; shift 2 ;;
        --front-cmd) FRONT_CMD="${2:-}"; shift 2 ;;
        --back-cmd) BACK_CMD="${2:-}"; shift 2 ;;
        --mic-cmd) MIC_CMD="${2:-}"; shift 2 ;;
        --probe-interval) PROBE_INTERVAL="${2:-$PROBE_INTERVAL}"; shift 2 ;;
        --retry-delay) RETRY_DELAY="${2:-$RETRY_DELAY}"; shift 2 ;;
        --dry-run) DRY_RUN="true"; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

mkdir -p "$LOG_DIR"
BRIDGE_LOG="$LOG_DIR/bridge.log"

log() {
    local msg="$1"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [bridge] $msg" | tee -a "$BRIDGE_LOG"
}

warn() {
    local msg="$1"
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] [bridge] ${YELLOW}WARN${NC}: $msg" | tee -a "$BRIDGE_LOG"
}

err() {
    local msg="$1"
    TOTAL_ERRORS=$((TOTAL_ERRORS + 1))
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] [bridge] ${RED}ERROR${NC}: $msg" | tee -a "$BRIDGE_LOG" >&2
}

resolve_cmd() {
    local raw="$1"
    local out="$raw"
    out="${out//\{FRONT_URI\}/$FRONT_SINK_URI}"
    out="${out//\{BACK_URI\}/$BACK_SINK_URI}"
    out="${out//\{MIC_URI\}/$MIC_SINK_URI}"
    out="${out//\{RTMP_URL\}/$RTMP_URL}"
    out="${out//\{LOG_DIR\}/$LOG_DIR}"
    echo "$out"
}

stop_pid_if_running() {
    local pid="$1"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    fi
}

cleanup() {
    log "Stopping bridge (streams=$STREAM_COUNT errors=$TOTAL_ERRORS)"
    stop_pid_if_running "$FFMPEG_PID"
    stop_pid_if_running "$FRONT_PID"
    stop_pid_if_running "$BACK_PID"
    stop_pid_if_running "$MIC_PID"
    exit 0
}

trap cleanup SIGINT SIGTERM SIGQUIT

echo -e "${BLUE}==========================================${NC}"
echo -e "${BLUE}Cuttlefish RTMP Bridge${NC}"
echo -e "${BLUE}==========================================${NC}"
echo "RTMP:       $RTMP_URL"
echo "Front sink: $FRONT_SINK_URI"
echo "Back sink:  $BACK_SINK_URI"
echo "Mic sink:   $MIC_SINK_URI"
echo "Video:      ${VIDEO_WIDTH}x${VIDEO_HEIGHT} @ ${VIDEO_FPS}fps (${VIDEO_BITRATE})"
echo "Audio:      ${AUDIO_RATE}Hz ${AUDIO_CHANNELS}ch (${AUDIO_BITRATE})"
echo "Log dir:    $LOG_DIR"
echo ""

command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg is required"; exit 1; }
command -v ffprobe >/dev/null 2>&1 || { echo "ffprobe is required"; exit 1; }

RESOLVED_FRONT_CMD=""
RESOLVED_BACK_CMD=""
RESOLVED_MIC_CMD=""
if [[ -n "$FRONT_CMD" ]]; then
    RESOLVED_FRONT_CMD="$(resolve_cmd "$FRONT_CMD")"
fi
if [[ -n "$BACK_CMD" ]]; then
    RESOLVED_BACK_CMD="$(resolve_cmd "$BACK_CMD")"
fi
if [[ -n "$MIC_CMD" ]]; then
    RESOLVED_MIC_CMD="$(resolve_cmd "$MIC_CMD")"
fi

if [[ "$DRY_RUN" == "true" ]]; then
    echo "Dry run:"
    echo "  Front cmd: ${RESOLVED_FRONT_CMD:-<none>}"
    echo "  Back cmd:  ${RESOLVED_BACK_CMD:-<none>}"
    echo "  Mic cmd:   ${RESOLVED_MIC_CMD:-<none>}"
    echo "  FFmpeg outputs:"
    echo "    - $FRONT_SINK_URI"
    echo "    - $BACK_SINK_URI"
    echo "    - $MIC_SINK_URI"
    exit 0
fi

if [[ -n "$RESOLVED_FRONT_CMD" ]]; then
    log "Starting front injector command"
    log "Front cmd: $RESOLVED_FRONT_CMD"
    bash -lc "$RESOLVED_FRONT_CMD" >>"$LOG_DIR/front-injector.log" 2>&1 &
    FRONT_PID=$!
fi

if [[ -n "$RESOLVED_BACK_CMD" ]]; then
    log "Starting back injector command"
    log "Back cmd: $RESOLVED_BACK_CMD"
    bash -lc "$RESOLVED_BACK_CMD" >>"$LOG_DIR/back-injector.log" 2>&1 &
    BACK_PID=$!
fi

if [[ -n "$RESOLVED_MIC_CMD" ]]; then
    log "Starting mic injector command"
    log "Mic cmd: $RESOLVED_MIC_CMD"
    bash -lc "$RESOLVED_MIC_CMD" >>"$LOG_DIR/mic-injector.log" 2>&1 &
    MIC_PID=$!
fi

log "Bridge started, waiting for RTMP stream: $RTMP_URL"

while true; do
    if timeout 6 ffprobe -v error -show_streams "$RTMP_URL" >/dev/null 2>&1; then
        STREAM_COUNT=$((STREAM_COUNT + 1))
        OUT_LOG="$LOG_DIR/ffmpeg-stream-${STREAM_COUNT}.log"
        log "Stream detected. Starting ffmpeg worker #$STREAM_COUNT"

        ffmpeg \
            -hide_banner \
            -loglevel warning \
            -nostdin \
            -reconnect 1 \
            -reconnect_at_eof 1 \
            -reconnect_streamed 1 \
            -reconnect_delay_max 2 \
            -i "$RTMP_URL" \
            -map 0:v:0 \
            -filter:v "scale=${VIDEO_WIDTH}:${VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,pad=${VIDEO_WIDTH}:${VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2,format=yuv420p,fps=${VIDEO_FPS}" \
            -c:v libx264 -preset veryfast -tune zerolatency -pix_fmt yuv420p -b:v "$VIDEO_BITRATE" \
            -f tee "[f=mpegts:onfail=ignore]$FRONT_SINK_URI|[f=mpegts:onfail=ignore]$BACK_SINK_URI" \
            -map 0:a:0? \
            -c:a aac -b:a "$AUDIO_BITRATE" -ar "$AUDIO_RATE" -ac "$AUDIO_CHANNELS" \
            -f mpegts "$MIC_SINK_URI" \
            >>"$OUT_LOG" 2>&1 &
        FFMPEG_PID=$!

        wait "$FFMPEG_PID" || true
        EXIT_CODE=$?
        if [[ "$EXIT_CODE" -ne 0 ]]; then
            err "ffmpeg exited with code $EXIT_CODE. See $OUT_LOG"
        else
            log "ffmpeg stream worker ended normally"
        fi
        sleep "$RETRY_DELAY"
    else
        sleep "$PROBE_INTERVAL"
    fi
done
