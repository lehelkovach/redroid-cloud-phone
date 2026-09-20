#!/bin/bash
# test-cuttlefish-rtmp-bridge.sh
# Run-level validation of the OBS RTMP bridge:
#   synthetic OBS-like publisher -> nginx-rtmp -> cuttlefish-rtmp-bridge -> sinks
#
# clean mode (default) proves the pixel-copy contract:
#   - the publisher sends LOSSLESS H.264 (x264 -qp 0) of a deterministic
#     test pattern (testsrc2) plus OBS-style junk metadata
#   - every frame the bridge writes must be byte-identical to the same
#     frame rendered locally (framemd5 membership), with no gaps in the
#     sequence and no duplicated frames (contiguity)
#   - sink files must be an exact multiple of the frame size (no headers,
#     no trailers, no torn frames)
#   - framed sinks (y4m over UDP) and the mic PCM must carry none of the
#     fingerprints that entered the pipeline (obs-output, x264, Lavf, NGINX)
#
# encode mode (--mode encode) runs the legacy MPEG-TS checks.
#
# Usage:
#   ./scripts/test-cuttlefish-rtmp-bridge.sh [OPTIONS] [VM_HOST]
#
# Options:
#   --local                     Run locally (default)
#   --vm HOST                   Run remotely via SSH
#   --ssh-user USER             SSH user (default: ubuntu)
#   --ssh-key PATH              SSH key (default: ~/.ssh/android_arm_cloud_phone_oci)
#   --scripts-dir DIR           Where cuttlefish-rtmp-bridge.sh lives on the target
#                               (default: this dir locally, /opt/cloud-phone-scripts remotely)
#   --mode clean|encode         Bridge mode under test (default: clean)
#   --duration SEC              Publisher duration (default: 12)
#   --size WxH                  Test pattern size (default: 640x360)
#   --fps N                     Test pattern fps (default: 30)
#   --rtmp-url URL              Input RTMP URL (default: rtmp://127.0.0.1/live/cam)
#   --udp-port PORT             UDP port for the back-sink y4m path (default: 23001)
#   --work-dir DIR              Scratch dir on the target (default: /tmp/cf-bridge-test)
#   --keep-artifacts            Keep generated files/logs
#   --help                      Show help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ENV_VM_HOST="${VM_HOST:-${DEV_INSTANCE:-}}"
VM_HOST=""
RUN_MODE="local"
SSH_USER="ubuntu"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/android_arm_cloud_phone_oci}"
SCRIPTS_DIR=""

MODE="clean"
DURATION="12"
SIZE="640x360"
FPS="30"
RTMP_URL="rtmp://127.0.0.1/live/cam"
UDP_PORT="23001"
WORK="/tmp/cf-bridge-test"
KEEP_ARTIFACTS="false"

PASS=0
FAIL=0
WARN=0

usage() {
    sed -n '/^# Usage:/,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --local) RUN_MODE="local"; VM_HOST=""; shift ;;
        --vm) VM_HOST="${2:-}"; RUN_MODE="remote"; shift 2 ;;
        --ssh-user) SSH_USER="${2:-ubuntu}"; shift 2 ;;
        --ssh-key) SSH_KEY="${2:-$HOME/.ssh/android_arm_cloud_phone_oci}"; shift 2 ;;
        --scripts-dir) SCRIPTS_DIR="${2:-}"; shift 2 ;;
        --mode) MODE="${2:-clean}"; shift 2 ;;
        --duration) DURATION="${2:-12}"; shift 2 ;;
        --size) SIZE="${2:-$SIZE}"; shift 2 ;;
        --fps) FPS="${2:-$FPS}"; shift 2 ;;
        --rtmp-url) RTMP_URL="${2:-$RTMP_URL}"; shift 2 ;;
        --udp-port) UDP_PORT="${2:-$UDP_PORT}"; shift 2 ;;
        --work-dir) WORK="${2:-$WORK}"; shift 2 ;;
        --keep-artifacts) KEEP_ARTIFACTS="true"; shift ;;
        --help|-h) usage; exit 0 ;;
        -*) echo "Unknown option: $1" >&2; usage; exit 1 ;;
        *)
            if [[ -z "$VM_HOST" ]]; then VM_HOST="$1"; RUN_MODE="remote"; fi
            shift
            ;;
    esac
done

if [[ -z "$VM_HOST" && -n "$ENV_VM_HOST" ]]; then
    VM_HOST="$ENV_VM_HOST"
    RUN_MODE="remote"
fi

case "$MODE" in
    clean|encode) ;;
    *) echo "Unsupported --mode '$MODE'" >&2; exit 1 ;;
esac

SSH_CMD=()
if [[ "$RUN_MODE" == "remote" && -n "$VM_HOST" ]]; then
    SSH_OPTS=(-o ConnectTimeout=10 -o StrictHostKeyChecking=no)
    [[ -f "$SSH_KEY" ]] && SSH_OPTS+=(-i "$SSH_KEY")
    SSH_CMD=(ssh "${SSH_OPTS[@]}" "${SSH_USER}@${VM_HOST}")
    SCRIPTS_DIR="${SCRIPTS_DIR:-/opt/cloud-phone-scripts}"
else
    SCRIPTS_DIR="${SCRIPTS_DIR:-$SCRIPT_DIR}"
fi
BRIDGE="$SCRIPTS_DIR/cuttlefish-rtmp-bridge.sh"

run_shell() {
    if [[ ${#SSH_CMD[@]} -gt 0 ]]; then
        "${SSH_CMD[@]}" "$1"
    else
        bash -c "$1"
    fi
}

pass() { echo -e "${GREEN}PASS${NC}: $1"; PASS=$((PASS + 1)); }
fail() { echo -e "${RED}FAIL${NC}: $1"; FAIL=$((FAIL + 1)); }
warn() { echo -e "${YELLOW}WARN${NC}: $1"; WARN=$((WARN + 1)); }

WIDTH="${SIZE%x*}"
HEIGHT="${SIZE#*x}"
FRAME_BYTES=$((WIDTH * HEIGHT * 3 / 2))
# Fingerprints that enter the pipeline and must not come out. Case-sensitive
# multi-character tokens so that pixel noise cannot match by accident.
FINGERPRINTS='obs-output|libobs|x264 - core|Lavf|Lavc|NGINX RTMP|RtmpSampleAccess|junk-title|ExifJunk'

echo -e "${BLUE}==========================================${NC}"
echo -e "${BLUE}Cuttlefish RTMP Bridge Test (${MODE})${NC}"
echo -e "${BLUE}==========================================${NC}"
[[ -n "$VM_HOST" ]] && echo "Target: ${SSH_USER}@${VM_HOST}" || echo "Target: localhost"
echo "Bridge:   $BRIDGE"
echo "Pattern:  testsrc2 ${SIZE}@${FPS} lossless x264 for ${DURATION}s (+ junk metadata)"
echo "RTMP URL: $RTMP_URL"
echo "Work dir: $WORK"
echo ""

run_shell "command -v ffmpeg >/dev/null 2>&1" && pass "ffmpeg available" || fail "ffmpeg missing"
run_shell "command -v ffprobe >/dev/null 2>&1" && pass "ffprobe available" || fail "ffprobe missing"
run_shell "test -f '$BRIDGE'" && pass "bridge script present" || fail "bridge script missing at $BRIDGE"

if run_shell "curl -s --max-time 3 http://127.0.0.1:8081/health 2>/dev/null | grep -q OK"; then
    pass "nginx-rtmp health endpoint responds OK"
else
    warn "nginx-rtmp health check did not return OK"
fi

if [[ "$FAIL" -gt 0 ]]; then
    echo -e "${BLUE}Summary:${NC} PASS=$PASS FAIL=$FAIL WARN=$WARN"
    exit 1
fi

run_shell "rm -rf '$WORK' && mkdir -p '$WORK/bridge'"

# Synthetic OBS: lossless so that a pixel-exact comparison is possible, with the
# kind of metadata a real OBS session carries (encoder tag, comment, title).
SOURCE_CMD="ffmpeg -hide_banner -loglevel error -re -f lavfi -i testsrc2=size=${SIZE}:rate=${FPS} -f lavfi -i sine=frequency=880:sample_rate=48000 -t ${DURATION} -metadata encoder='obs-output module (libobs version 30.2.3)' -metadata title=junk-title -metadata comment=ExifJunk -c:v libx264 -preset ultrafast -tune zerolatency -qp 0 -pix_fmt yuv420p -g ${FPS} -c:a aac -ar 48000 -ac 1 -b:a 96k -f flv '${RTMP_URL}'"

if [[ "$MODE" == "clean" ]]; then
    FRONT_SINK="file:$WORK/front.yuv"
    BACK_SINK="udp://127.0.0.1:${UDP_PORT}?pkt_size=1316"
    MIC_SINK="file:$WORK/mic.pcm"
    # Reader for the UDP y4m path, records the header and the raw frames.
    # Raw video over loopback UDP is ~10 MB/s at 640x360@30 (40 MB/s at 720p).
    # The reader must ask for a large socket buffer or the kernel drops packets
    # and the y4m demuxer loses sync; buffer_size is capped by net.core.rmem_max.
    RMEM="$(run_shell "sysctl -n net.core.rmem_max 2>/dev/null || echo 0" | tr -d '\r')"
    if [[ "${RMEM:-0}" -ge 4194304 ]]; then
        pass "net.core.rmem_max=${RMEM} (>= 4 MiB, UDP raw-video readers can buffer)"
    else
        warn "net.core.rmem_max=${RMEM} < 4 MiB: UDP readers will drop frames (installer sets /etc/sysctl.d/90-cloud-phone-rtmp-bridge.conf)"
    fi
    LISTENER_CMD="ffmpeg -hide_banner -loglevel error -y -f yuv4mpegpipe -i 'udp://127.0.0.1:${UDP_PORT}?buffer_size=4194304&fifo_size=5000000&overrun_nonfatal=1' -map 0:v -c:v copy -f yuv4mpegpipe '$WORK/back.y4m'"
    run_shell "nohup bash -c \"$LISTENER_CMD\" >'$WORK/listener.log' 2>&1 & echo \$! >'$WORK/listener.pid'"
else
    FRONT_SINK="file:$WORK/front.ts"
    BACK_SINK="file:$WORK/back.ts"
    MIC_SINK="file:$WORK/mic.ts"
fi

BRIDGE_CMD="bash '$BRIDGE' --mode $MODE --rtmp-url '$RTMP_URL' --front-sink '$FRONT_SINK' --back-sink '$BACK_SINK' --mic-sink '$MIC_SINK' --log-dir '$WORK/bridge' --probe-interval 1 --retry-delay 1"
run_shell "nohup bash -c \"$BRIDGE_CMD\" >'$WORK/bridge-stdout.log' 2>&1 & echo \$! >'$WORK/bridge.pid'"
sleep 2
run_shell "$SOURCE_CMD >'$WORK/source.log' 2>&1 || true"
sleep 3
# SIGTERM -> bridge cleanup -> graceful ffmpeg stop with every packet flushed.
run_shell "kill -TERM \$(cat '$WORK/bridge.pid') 2>/dev/null || true; for i in 1 2 3 4 5 6 7 8; do kill -0 \$(cat '$WORK/bridge.pid') 2>/dev/null || break; sleep 1; done; kill -KILL \$(cat '$WORK/bridge.pid') 2>/dev/null || true; pkill -KILL -f \"[c]uttlefish-rtmp-bridge.sh .*log-dir $WORK/bridge\" 2>/dev/null || true"
if [[ "$MODE" == "clean" ]]; then
    run_shell "kill -INT \$(cat '$WORK/listener.pid') 2>/dev/null || true; sleep 1; kill -KILL \$(cat '$WORK/listener.pid') 2>/dev/null || true"
fi

if run_shell "grep -q 'Stream detected' '$WORK/bridge/bridge.log'"; then
    pass "bridge attached a worker to the stream"
else
    fail "bridge never attached a worker (see $WORK/bridge/bridge.log)"
fi
if run_shell "grep -q 'ERROR' '$WORK/bridge/bridge.log'"; then
    fail "bridge log contains ERROR lines"
else
    pass "bridge log has no ERROR lines"
fi

if [[ "$MODE" == "clean" ]]; then
    # Reference: the same deterministic pattern rendered locally.
    run_shell "ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=${SIZE}:rate=${FPS} -t $((DURATION + 2)) -pix_fmt yuv420p -f framemd5 - | awk '/^[0-9]/{print \$NF}' >'$WORK/ref.seq'"

    check_raw_video() {
        local label="$1" path="$2"
        if ! run_shell "test -s '$path'"; then
            fail "$label: no data at $path"
            return
        fi
        local size frames rem
        size="$(run_shell "stat -c%s '$path'" | tr -d '\r')"
        frames=$((size / FRAME_BYTES)); rem=$((size % FRAME_BYTES))
        if [[ "$rem" -eq 0 && "$frames" -gt 0 ]]; then
            pass "$label: ${frames} frames, exact multiple of ${FRAME_BYTES} bytes (no headers, no torn frames)"
        else
            fail "$label: ${size} bytes = ${frames} frames + ${rem} stray bytes"
        fi
        if [[ "$frames" -lt $((FPS * 2)) ]]; then
            fail "$label: only ${frames} frames captured (expected at least $((FPS * 2)))"
        fi
        run_shell "ffmpeg -hide_banner -loglevel error -f rawvideo -video_size ${SIZE} -pix_fmt yuv420p -i '$path' -f framemd5 - | awk '/^[0-9]/{print \$NF}' >'$path.seq'"
        # Position of every output frame in the reference sequence.
        local stats
        stats="$(run_shell "awk 'NR==FNR{idx[\$1]=NR; next} {p=(\$1 in idx)?idx[\$1]:-1; if(p<0)unk++; else if(prev && p!=prev+1)gaps++; prev=p; n++} END{printf \"%d %d %d\", n, unk+0, gaps+0}' '$WORK/ref.seq' '$path.seq'" | tr -d '\r')"
        local n unk gaps
        read -r n unk gaps <<<"$stats"
        if [[ "${unk:-1}" -eq 0 ]]; then
            pass "$label: all ${n} frames byte-identical to the source pattern (pixel-exact copy)"
        else
            fail "$label: ${unk} of ${n} frames do not match the source pattern"
        fi
        if [[ "${gaps:-1}" -eq 0 ]]; then
            pass "$label: frames contiguous (no drops, no duplicates)"
        else
            fail "$label: ${gaps} gaps/duplicates in the frame sequence"
        fi
    }

    check_raw_video "front sink (file, rawvideo)" "$WORK/front.yuv"

    if run_shell "test -s '$WORK/back.y4m'"; then
        HEADER="$(run_shell "head -c 120 '$WORK/back.y4m' | head -1" | tr -d '\r')"
        if [[ "$HEADER" == "YUV4MPEG2 W${WIDTH} H${HEIGHT} F${FPS}:1"* ]]; then
            pass "back sink (udp, y4m): header '$HEADER'"
        else
            fail "back sink (udp, y4m): unexpected header '$HEADER'"
        fi
        run_shell "ffmpeg -hide_banner -loglevel error -y -f yuv4mpegpipe -i '$WORK/back.y4m' -c:v rawvideo -f rawvideo '$WORK/back.yuv'"
        check_raw_video "back sink (udp, y4m)" "$WORK/back.yuv"
        if run_shell "strings '$WORK/back.y4m' | grep -qE '$FINGERPRINTS'"; then
            fail "back sink (udp, y4m): carries a fingerprint from the input"
        else
            pass "back sink (udp, y4m): no input fingerprint survived (obs/x264/Lavf/NGINX)"
        fi
    else
        fail "back sink (udp, y4m): listener captured nothing (see $WORK/listener.log)"
    fi

    if run_shell "test -s '$WORK/mic.pcm'"; then
        MIC_BYTES="$(run_shell "stat -c%s '$WORK/mic.pcm'" | tr -d '\r')"
        MIC_MS=$((MIC_BYTES * 1000 / (48000 * 2)))
        if [[ "$MIC_MS" -ge 1000 ]]; then
            pass "mic sink (s16le): ${MIC_MS}ms of raw PCM"
        else
            fail "mic sink (s16le): only ${MIC_MS}ms of raw PCM"
        fi
        if run_shell "strings '$WORK/mic.pcm' | grep -qE '$FINGERPRINTS'"; then
            fail "mic sink: carries a fingerprint from the input"
        else
            pass "mic sink: no input fingerprint survived"
        fi
    else
        fail "mic sink file not produced"
    fi
else
    for f in front back; do
        if run_shell "test -s '$WORK/$f.ts'"; then
            pass "$f sink produced data"
        else
            fail "$f sink file not produced"
        fi
        if run_shell "ffprobe -v error -show_streams '$WORK/$f.ts' 2>/dev/null | grep -q codec_type=video"; then
            pass "$f sink contains a video stream"
        else
            fail "$f sink missing valid video stream"
        fi
        if run_shell "strings '$WORK/$f.ts' | grep -qE 'obs-output|libobs|x264 - core|junk-title|ExifJunk'"; then
            fail "$f sink still carries OBS/x264 fingerprints"
        else
            pass "$f sink: OBS metadata and x264 SEI stripped"
        fi
    done
    if run_shell "ffprobe -v error -show_streams '$WORK/mic.ts' 2>/dev/null | grep -q codec_type=audio"; then
        pass "mic sink contains an audio stream"
    else
        fail "mic sink missing valid audio stream"
    fi
fi

if [[ "$KEEP_ARTIFACTS" == "true" ]]; then
    pass "artifacts kept under $WORK"
else
    run_shell "rm -rf '$WORK'"
fi

echo ""
echo -e "${BLUE}Summary:${NC} PASS=$PASS FAIL=$FAIL WARN=$WARN"
if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
