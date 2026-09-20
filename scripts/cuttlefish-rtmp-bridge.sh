#!/bin/bash
# cuttlefish-rtmp-bridge.sh
# OBS RTMP -> clean pixel-copy -> front/back camera sinks (+ mic sink).
#
# Pipeline:
#   OBS  --rtmp-->  nginx-rtmp (rtmp://<host>/live/cam)  --rtmp-->  ffmpeg  -->  sinks
#
# Modes:
#   clean  (default) Decode to raw pixels and write them out untouched. No
#                    re-encode, no scaling unless asked, no frame dup/drop,
#                    no container/stream tags, no encoder SEI, no chapters,
#                    no data/subtitle streams. What reaches a sink is the
#                    decoded yuv420p plane data and nothing else. OBS junk
#                    (onMetaData "encoder=obs-output", x264 user-data SEI,
#                    nginx "Server=NGINX RTMP" tags, EXIF carried in by image
#                    sources) cannot survive because only pixels are copied.
#   encode           Legacy path: libx264 -> MPEG-TS on every sink. Still
#                    strips metadata and SEI NAL units, but MPEG-TS/x264 add
#                    their own headers. Use only for consumers that need TS.
#
# Sink URI -> output format (clean mode, --video-format auto):
#   /dev/videoN            v4l2 (raw yuv420p into a v4l2loopback device)
#   file:PATH | PATH       rawvideo (pure yuv420p frames, w*h*1.5 bytes each)
#   pipe:PATH | fifo:PATH  rawvideo into a named pipe (created if missing)
#   udp:// tcp:// unix://  yuv4mpegpipe (y4m: one ASCII header + "FRAME"
#                          markers; no tool identity; the reader must be
#                          listening before the worker starts)
# Audio (--audio-format auto): s16le raw PCM for every sink kind.
#
# Usage:
#   ./scripts/cuttlefish-rtmp-bridge.sh [OPTIONS]
#
# Options:
#   --mode clean|encode       Pixel copy (default) or legacy libx264/mpegts
#   --rtmp-url URL            Input RTMP URL (default: rtmp://127.0.0.1/live/cam)
#   --front-sink URI          Front camera sink URI (default: udp://127.0.0.1:23000?pkt_size=1316)
#   --back-sink URI           Back camera sink URI (default: udp://127.0.0.1:23001?pkt_size=1316)
#   --mic-sink URI            Mic sink URI (default: udp://127.0.0.1:23010?pkt_size=1316)
#   --no-mic                  Do not open a mic sink (video only)
#   --video-format FMT        auto|rawvideo|y4m|nut|v4l2|mpegts (default: auto)
#   --audio-format FMT        auto|s16le|wav|nut|mpegts (default: auto)
#   --video-width N           Output width (clean: source unless set; encode: 1280)
#   --video-height N          Output height (clean: source unless set; encode: 720)
#   --video-fps N             Output fps (clean: source unless set; encode: 30)
#   --video-bitrate RATE      encode mode only (default: 4M)
#   --audio-rate N            Sample rate (clean: source unless set; encode: 44100)
#   --audio-channels N        Channels (clean: source unless set; encode: 2)
#   --audio-bitrate RATE      encode mode only (default: 128k)
#   --stall-timeout SEC       Restart the worker when a publisher is live on nginx
#                             but no frames arrive for SEC seconds (default: 10)
#   --idle-timeout SEC        Detach the worker after SEC seconds with no
#                             publisher; 0 keeps sinks open across OBS sessions (default: 0)
#   --stat-url URL            nginx-rtmp stat endpoint (default: http://127.0.0.1:8081/stat)
#   --log-dir DIR             Log directory (default: /tmp/cuttlefish-bridge)
#   --front-cmd CMD           Optional command consuming {FRONT_URI}
#   --back-cmd CMD            Optional command consuming {BACK_URI}
#   --mic-cmd CMD             Optional command consuming {MIC_URI}
#   --probe-interval SEC      Stream probe interval (default: 2)
#   --retry-delay SEC         Restart delay (default: 3)
#   --dry-run                 Print resolved commands (incl. ffmpeg argv) and exit
#   --help                    Show help
#
# Placeholders supported in --front-cmd / --back-cmd / --mic-cmd:
#   {FRONT_URI} {BACK_URI} {MIC_URI} {RTMP_URL} {LOG_DIR}

set -euo pipefail

BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

MODE="clean"
RTMP_URL="rtmp://127.0.0.1/live/cam"
FRONT_SINK_URI="udp://127.0.0.1:23000?pkt_size=1316"
BACK_SINK_URI="udp://127.0.0.1:23001?pkt_size=1316"
MIC_SINK_URI="udp://127.0.0.1:23010?pkt_size=1316"
WITH_MIC="true"
VIDEO_FORMAT="auto"
AUDIO_FORMAT="auto"
VIDEO_WIDTH=""
VIDEO_HEIGHT=""
VIDEO_FPS=""
VIDEO_BITRATE="4M"
AUDIO_RATE=""
AUDIO_CHANNELS=""
AUDIO_BITRATE="128k"
STALL_TIMEOUT="10"
IDLE_TIMEOUT="0"
STAT_URL="http://127.0.0.1:8081/stat"
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
    sed -n '/^# Usage:/,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode) MODE="${2:-$MODE}"; shift 2 ;;
        --rtmp-url) RTMP_URL="${2:-$RTMP_URL}"; shift 2 ;;
        --front-sink) FRONT_SINK_URI="${2:-$FRONT_SINK_URI}"; shift 2 ;;
        --back-sink) BACK_SINK_URI="${2:-$BACK_SINK_URI}"; shift 2 ;;
        --mic-sink) MIC_SINK_URI="${2:-$MIC_SINK_URI}"; shift 2 ;;
        --no-mic) WITH_MIC="false"; shift ;;
        --video-format) VIDEO_FORMAT="${2:-$VIDEO_FORMAT}"; shift 2 ;;
        --audio-format) AUDIO_FORMAT="${2:-$AUDIO_FORMAT}"; shift 2 ;;
        --video-width) VIDEO_WIDTH="${2:-}"; shift 2 ;;
        --video-height) VIDEO_HEIGHT="${2:-}"; shift 2 ;;
        --video-fps) VIDEO_FPS="${2:-}"; shift 2 ;;
        --video-bitrate) VIDEO_BITRATE="${2:-$VIDEO_BITRATE}"; shift 2 ;;
        --audio-rate) AUDIO_RATE="${2:-}"; shift 2 ;;
        --audio-channels) AUDIO_CHANNELS="${2:-}"; shift 2 ;;
        --audio-bitrate) AUDIO_BITRATE="${2:-$AUDIO_BITRATE}"; shift 2 ;;
        --stall-timeout) STALL_TIMEOUT="${2:-$STALL_TIMEOUT}"; shift 2 ;;
        --idle-timeout) IDLE_TIMEOUT="${2:-$IDLE_TIMEOUT}"; shift 2 ;;
        --stat-url) STAT_URL="${2:-$STAT_URL}"; shift 2 ;;
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

case "$MODE" in
    clean|encode) ;;
    *) echo "Unsupported --mode '$MODE' (expected clean|encode)" >&2; exit 1 ;;
esac

if [[ "$MODE" == "encode" ]]; then
    VIDEO_WIDTH="${VIDEO_WIDTH:-1280}"
    VIDEO_HEIGHT="${VIDEO_HEIGHT:-720}"
    VIDEO_FPS="${VIDEO_FPS:-30}"
    AUDIO_RATE="${AUDIO_RATE:-44100}"
    AUDIO_CHANNELS="${AUDIO_CHANNELS:-2}"
fi

if [[ -n "$VIDEO_WIDTH" && -z "$VIDEO_HEIGHT" ]] || [[ -z "$VIDEO_WIDTH" && -n "$VIDEO_HEIGHT" ]]; then
    echo "--video-width and --video-height must be given together" >&2
    exit 1
fi

mkdir -p "$LOG_DIR"
BRIDGE_LOG="$LOG_DIR/bridge.log"
PROGRESS_FILE="$LOG_DIR/progress.txt"
STREAM_NAME="${RTMP_URL##*/}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [bridge] $1" | tee -a "$BRIDGE_LOG"
}

warn() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] [bridge] ${YELLOW}WARN${NC}: $1" | tee -a "$BRIDGE_LOG"
}

err() {
    TOTAL_ERRORS=$((TOTAL_ERRORS + 1))
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] [bridge] ${RED}ERROR${NC}: $1" | tee -a "$BRIDGE_LOG" >&2
}

resolve_cmd() {
    local out="$1"
    out="${out//\{FRONT_URI\}/$FRONT_SINK_URI}"
    out="${out//\{BACK_URI\}/$BACK_SINK_URI}"
    out="${out//\{MIC_URI\}/$MIC_SINK_URI}"
    out="${out//\{RTMP_URL\}/$RTMP_URL}"
    out="${out//\{LOG_DIR\}/$LOG_DIR}"
    echo "$out"
}

# sink_kind URI -> v4l2 | socket | pipe | file
sink_kind() {
    local uri="$1"
    case "$uri" in
        /dev/video*|file:/dev/video*) echo "v4l2" ;;
        udp://*|tcp://*|unix://*|rtp://*|srt://*) echo "socket" ;;
        pipe:*|fifo:*) echo "pipe" ;;
        *)
            local p="${uri#file:}"
            if [[ -p "$p" ]]; then echo "pipe"; else echo "file"; fi
            ;;
    esac
}

# sink_target URI -> the string ffmpeg should open
sink_target() {
    local uri="$1"
    case "$uri" in
        pipe:*) echo "${uri#pipe:}" ;;
        fifo:*) echo "${uri#fifo:}" ;;
        *) echo "$uri" ;;
    esac
}

# video_muxer URI -> ffmpeg -f name for the video sink
video_muxer() {
    local uri="$1"
    if [[ "$MODE" == "encode" ]]; then
        echo "mpegts"
        return
    fi
    case "$VIDEO_FORMAT" in
        auto)
            case "$(sink_kind "$uri")" in
                v4l2) echo "v4l2" ;;
                socket) echo "yuv4mpegpipe" ;;
                pipe|file) echo "rawvideo" ;;
            esac
            ;;
        rawvideo) echo "rawvideo" ;;
        y4m|yuv4mpegpipe) echo "yuv4mpegpipe" ;;
        nut) echo "nut" ;;
        v4l2) echo "v4l2" ;;
        mpegts) echo "mpegts" ;;
        *) echo "Unsupported --video-format '$VIDEO_FORMAT'" >&2; exit 1 ;;
    esac
}

audio_muxer() {
    if [[ "$MODE" == "encode" ]]; then
        echo "mpegts"
        return
    fi
    case "$AUDIO_FORMAT" in
        auto|s16le) echo "s16le" ;;
        wav) echo "wav" ;;
        nut) echo "nut" ;;
        mpegts) echo "mpegts" ;;
        *) echo "Unsupported --audio-format '$AUDIO_FORMAT'" >&2; exit 1 ;;
    esac
}

ensure_pipe() {
    local uri="$1"
    if [[ "$(sink_kind "$uri")" == "pipe" ]]; then
        local p
        p="$(sink_target "$uri")"
        p="${p#file:}"
        [[ -p "$p" ]] || mkfifo "$p"
    fi
}

# Build the ffmpeg argv into FFMPEG_ARGS (global array).
build_ffmpeg_args() {
    local vf="" front_mux back_mux
    front_mux="$(video_muxer "$FRONT_SINK_URI")"
    back_mux="$(video_muxer "$BACK_SINK_URI")"

    FFMPEG_ARGS=(ffmpeg -hide_banner -loglevel warning -nostdin)
    # Live input. nginx-rtmp hands the subscriber a keyframe first (wait_key),
    # and FLV carries the codec config up front, so a minimal probe attaches
    # in ~1s instead of discarding the first 3-5s while ffmpeg analyzes.
    # (Do not add -fflags nobuffer: it drops the frames read during probing.)
    FFMPEG_ARGS+=(-probesize 32 -analyzeduration 0 -flags +low_delay)
    # Frame counter for the stall/idle monitor in the main loop.
    FFMPEG_ARGS+=(-progress "$PROGRESS_FILE" -stats_period 1)
    FFMPEG_ARGS+=(-i "$RTMP_URL")

    # ---- video output (front + back via tee) ----
    FFMPEG_ARGS+=(-map 0:v:0 -an -sn -dn)
    FFMPEG_ARGS+=(-map_metadata -1 -map_chapters -1 -bitexact)

    if [[ -n "$VIDEO_WIDTH" ]]; then
        vf="scale=${VIDEO_WIDTH}:${VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,pad=${VIDEO_WIDTH}:${VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
    fi
    vf="${vf}format=yuv420p"
    if [[ -n "$VIDEO_FPS" ]]; then
        vf="${vf},fps=${VIDEO_FPS}"
        FFMPEG_ARGS+=(-fps_mode cfr)
    else
        # One decoded frame in, one frame out. No duplication, no drops.
        FFMPEG_ARGS+=(-fps_mode passthrough)
    fi
    FFMPEG_ARGS+=(-filter:v "$vf")

    if [[ "$MODE" == "clean" ]]; then
        FFMPEG_ARGS+=(-c:v rawvideo -pix_fmt yuv420p)
    else
        FFMPEG_ARGS+=(-c:v libx264 -preset veryfast -tune zerolatency -pix_fmt yuv420p -b:v "$VIDEO_BITRATE")
        # Drop SEI NAL units (type 6): x264 writes its version banner there.
        FFMPEG_ARGS+=(-bsf:v filter_units=remove_types=6)
        FFMPEG_ARGS+=(-metadata service_provider=camera -metadata service_name=camera)
    fi
    FFMPEG_ARGS+=(-flush_packets 1)
    # flush_packets must be set on each tee slave: the tee-level flag does not
    # reach them, and an unflushed slave keeps up to one frame in its buffer.
    FFMPEG_ARGS+=(-f tee "[f=${front_mux}:onfail=ignore:flush_packets=1]$(sink_target "$FRONT_SINK_URI")|[f=${back_mux}:onfail=ignore:flush_packets=1]$(sink_target "$BACK_SINK_URI")")

    # ---- audio output (mic) ----
    if [[ "$WITH_MIC" == "true" ]]; then
        FFMPEG_ARGS+=(-map 0:a:0? -vn -sn -dn)
        FFMPEG_ARGS+=(-map_metadata -1 -map_chapters -1 -bitexact)
        if [[ "$MODE" == "clean" ]]; then
            FFMPEG_ARGS+=(-c:a pcm_s16le)
        else
            FFMPEG_ARGS+=(-c:a aac -b:a "$AUDIO_BITRATE")
            FFMPEG_ARGS+=(-metadata service_provider=camera -metadata service_name=camera)
        fi
        [[ -n "$AUDIO_RATE" ]] && FFMPEG_ARGS+=(-ar "$AUDIO_RATE")
        [[ -n "$AUDIO_CHANNELS" ]] && FFMPEG_ARGS+=(-ac "$AUDIO_CHANNELS")
        FFMPEG_ARGS+=(-flush_packets 1 -f "$(audio_muxer)" "$(sink_target "$MIC_SINK_URI")")
    fi
}

stop_pid_if_running() {
    local pid="$1"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    fi
}

# ffmpeg attached to nginx-rtmp with no publisher blocks inside the RTMP
# read and ignores the first SIGINT; the second one forces the exit. Every
# packet was flushed as it was written (-flush_packets 1), so nothing is lost.
stop_ffmpeg() {
    local pid="$1" i
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null || return 0
    kill -INT "$pid" 2>/dev/null || true
    for i in 1 2; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    if kill -0 "$pid" 2>/dev/null; then
        kill -INT "$pid" 2>/dev/null || true
        sleep 1
    fi
    kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
}

# 0 = a publisher is live on our stream, 1 = none, 2 = stat endpoint unreachable
publisher_state() {
    local xml
    xml="$(curl -s --max-time 2 "$STAT_URL" 2>/dev/null)" || return 2
    [[ -n "$xml" ]] || return 2
    if echo "$xml" | tr -d '\n' | sed 's#</stream>#</stream>\n#g' \
            | grep "<name>${STREAM_NAME}</name>" | grep -q '<publishing/>'; then
        return 0
    fi
    return 1
}

progress_frames() {
    grep '^frame=' "$PROGRESS_FILE" 2>/dev/null | tail -1 | cut -d= -f2
}

cleanup() {
    log "Stopping bridge (streams=$STREAM_COUNT errors=$TOTAL_ERRORS)"
    stop_ffmpeg "$FFMPEG_PID"
    stop_pid_if_running "$FRONT_PID"
    stop_pid_if_running "$BACK_PID"
    stop_pid_if_running "$MIC_PID"
    exit 0
}

trap cleanup SIGINT SIGTERM SIGQUIT

RESOLVED_FRONT_CMD=""
RESOLVED_BACK_CMD=""
RESOLVED_MIC_CMD=""
[[ -n "$FRONT_CMD" ]] && RESOLVED_FRONT_CMD="$(resolve_cmd "$FRONT_CMD")"
[[ -n "$BACK_CMD" ]] && RESOLVED_BACK_CMD="$(resolve_cmd "$BACK_CMD")"
[[ -n "$MIC_CMD" ]] && RESOLVED_MIC_CMD="$(resolve_cmd "$MIC_CMD")"

build_ffmpeg_args

echo -e "${BLUE}==========================================${NC}"
echo -e "${BLUE}Cuttlefish RTMP Bridge (${MODE})${NC}"
echo -e "${BLUE}==========================================${NC}"
echo "RTMP:       $RTMP_URL"
echo "Front sink: $FRONT_SINK_URI  [$(video_muxer "$FRONT_SINK_URI")]"
echo "Back sink:  $BACK_SINK_URI  [$(video_muxer "$BACK_SINK_URI")]"
if [[ "$WITH_MIC" == "true" ]]; then
    echo "Mic sink:   $MIC_SINK_URI  [$(audio_muxer)]"
else
    echo "Mic sink:   <disabled>"
fi
if [[ "$MODE" == "clean" ]]; then
    echo "Video:      ${VIDEO_WIDTH:-source}x${VIDEO_HEIGHT:-source} @ ${VIDEO_FPS:-source}fps, rawvideo yuv420p, metadata stripped"
    echo "Audio:      ${AUDIO_RATE:-source}Hz ${AUDIO_CHANNELS:-source}ch, pcm_s16le"
else
    echo "Video:      ${VIDEO_WIDTH}x${VIDEO_HEIGHT} @ ${VIDEO_FPS}fps (${VIDEO_BITRATE}) libx264, SEI stripped"
    echo "Audio:      ${AUDIO_RATE}Hz ${AUDIO_CHANNELS}ch (${AUDIO_BITRATE}) aac"
fi
echo "Stall:      ${STALL_TIMEOUT}s (idle: ${IDLE_TIMEOUT}s, stat: $STAT_URL)"
echo "Log dir:    $LOG_DIR"
echo ""

if [[ "$DRY_RUN" == "true" ]]; then
    echo "Dry run:"
    echo "  Front cmd: ${RESOLVED_FRONT_CMD:-<none>}"
    echo "  Back cmd:  ${RESOLVED_BACK_CMD:-<none>}"
    echo "  Mic cmd:   ${RESOLVED_MIC_CMD:-<none>}"
    echo "  FFmpeg outputs:"
    echo "    - $FRONT_SINK_URI"
    echo "    - $BACK_SINK_URI"
    [[ "$WITH_MIC" == "true" ]] && echo "    - $MIC_SINK_URI"
    echo -n "  FFmpeg cmd:"
    printf ' %q' "${FFMPEG_ARGS[@]}"
    echo ""
    exit 0
fi

command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg is required"; exit 1; }
command -v ffprobe >/dev/null 2>&1 || { echo "ffprobe is required"; exit 1; }

if [[ "$MODE" == "clean" ]]; then
    ensure_pipe "$FRONT_SINK_URI"
    ensure_pipe "$BACK_SINK_URI"
    [[ "$WITH_MIC" == "true" ]] && ensure_pipe "$MIC_SINK_URI"
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

log "Bridge started (mode=$MODE), waiting for RTMP stream: $RTMP_URL"
{ echo -n "ffmpeg argv:"; printf ' %q' "${FFMPEG_ARGS[@]}"; echo; } >>"$BRIDGE_LOG"

while true; do
    if ffprobe -v error -rw_timeout 5000000 -show_streams "$RTMP_URL" >/dev/null 2>&1; then
        STREAM_COUNT=$((STREAM_COUNT + 1))
        OUT_LOG="$LOG_DIR/ffmpeg-stream-${STREAM_COUNT}.log"
        log "Stream detected. Starting ffmpeg worker #$STREAM_COUNT"
        : > "$PROGRESS_FILE"

        "${FFMPEG_ARGS[@]}" >>"$OUT_LOG" 2>&1 &
        FFMPEG_PID=$!

        # Monitor. The worker stays attached to nginx across OBS stop/start
        # (sinks stay open, the injector never sees EOF). It is restarted only
        # when a publisher is live but frames stopped (stall), or optionally
        # detached after --idle-timeout with no publisher at all.
        LAST_FRAMES=""
        LAST_CHANGE=$(date +%s)
        STOP_REASON=""
        while kill -0 "$FFMPEG_PID" 2>/dev/null; do
            sleep 1
            NOW=$(date +%s)
            FRAMES="$(progress_frames)"
            if [[ -n "$FRAMES" && "$FRAMES" != "$LAST_FRAMES" ]]; then
                LAST_FRAMES="$FRAMES"
                LAST_CHANGE=$NOW
                continue
            fi
            QUIET=$((NOW - LAST_CHANGE))
            CHECK_STALL=$([[ "$QUIET" -ge "$STALL_TIMEOUT" ]] && echo 1 || echo 0)
            CHECK_IDLE=$([[ "$IDLE_TIMEOUT" -gt 0 && "$QUIET" -ge "$IDLE_TIMEOUT" ]] && echo 1 || echo 0)
            if [[ "$CHECK_STALL" -eq 1 || "$CHECK_IDLE" -eq 1 ]]; then
                PSTATE=0
                publisher_state || PSTATE=$?
                if [[ "$CHECK_STALL" -eq 1 && "$PSTATE" -eq 0 ]]; then
                    STOP_REASON="stall: publisher live on '$STREAM_NAME' but no frames for ${QUIET}s"
                    break
                fi
                if [[ "$CHECK_IDLE" -eq 1 && "$PSTATE" -eq 1 ]]; then
                    STOP_REASON="idle: no publisher for ${QUIET}s"
                    break
                fi
            fi
        done

        if [[ -n "$STOP_REASON" ]]; then
            warn "Restarting worker #$STREAM_COUNT ($STOP_REASON, frames=${LAST_FRAMES:-0})"
            stop_ffmpeg "$FFMPEG_PID"
            FFMPEG_PID=""
            continue
        else
            EXIT_CODE=0
            wait "$FFMPEG_PID" || EXIT_CODE=$?
            FFMPEG_PID=""
            if [[ "$EXIT_CODE" -ne 0 ]]; then
                err "ffmpeg worker #$STREAM_COUNT exited with code $EXIT_CODE (frames=${LAST_FRAMES:-0}). See $OUT_LOG"
            else
                log "ffmpeg worker #$STREAM_COUNT ended (frames=${LAST_FRAMES:-0})"
            fi
        fi
        sleep "$RETRY_DELAY"
    else
        sleep "$PROBE_INTERVAL"
    fi
done
