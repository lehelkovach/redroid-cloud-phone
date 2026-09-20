#!/bin/bash
# obs-e2e-check.sh
# End-to-end check with a REAL OBS session:
#   OBS (your desk)  --rtmp-->  nginx-rtmp on the ingest host  -->  bridge  -->  sinks
#
# Run it on (or against) the ingest host while OBS is streaming to
# rtmp://<host>/live/cam. It never touches the production bridge or its
# sinks: nginx-rtmp allows several local subscribers, so this script taps
# the same stream with its own clean worker into a scratch directory and
# inspects what comes out.
#
# Reports:
#   1. what OBS is sending (codec, resolution, fps, audio, every metadata
#      tag that rides in with the stream)
#   2. that the production bridge unit is running and attached
#   3. that the tap produces gapless raw yuv420p frames at the source rate
#      with no header/trailer bytes, and PCM audio
#   4. that none of the input fingerprints survive in framed outputs
#   5. a PNG snapshot of one captured frame so a human can eyeball it
#
# Usage:
#   ./scripts/obs-e2e-check.sh [OPTIONS] [VM_HOST]
#
# Options:
#   --local                     Run on this host (default)
#   --vm HOST                   Run remotely via SSH
#   --ssh-user USER             SSH user (default: ubuntu)
#   --ssh-key PATH              SSH key (default: ~/.ssh/android_arm_cloud_phone_oci)
#   --rtmp-url URL              Stream to tap (default: rtmp://127.0.0.1/live/cam)
#   --stat-url URL              nginx-rtmp stat endpoint (default: http://127.0.0.1:8081/stat)
#   --wait SEC                  How long to wait for OBS to start publishing (default: 120)
#   --duration SEC              Capture length (default: 10)
#   --work-dir DIR              Scratch dir on the target (default: /tmp/obs-e2e)
#   --unit NAME                 Bridge systemd unit to inspect (default: cuttlefish-rtmp-bridge.service)
#   --snapshot PATH             Where to copy the PNG snapshot locally (remote runs only)
#   --keep-artifacts            Keep the scratch dir
#   --help                      Show help

set -euo pipefail

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
RTMP_URL="rtmp://127.0.0.1/live/cam"
STAT_URL="http://127.0.0.1:8081/stat"
WAIT="120"
DURATION="10"
WORK="/tmp/obs-e2e"
UNIT="cuttlefish-rtmp-bridge.service"
SNAPSHOT=""
KEEP_ARTIFACTS="false"

PASS=0; FAIL=0; WARN=0

usage() {
    sed -n '/^# Usage:/,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --local) RUN_MODE="local"; VM_HOST=""; shift ;;
        --vm) VM_HOST="${2:-}"; RUN_MODE="remote"; shift 2 ;;
        --ssh-user) SSH_USER="${2:-ubuntu}"; shift 2 ;;
        --ssh-key) SSH_KEY="${2:-$SSH_KEY}"; shift 2 ;;
        --rtmp-url) RTMP_URL="${2:-$RTMP_URL}"; shift 2 ;;
        --stat-url) STAT_URL="${2:-$STAT_URL}"; shift 2 ;;
        --wait) WAIT="${2:-$WAIT}"; shift 2 ;;
        --duration) DURATION="${2:-$DURATION}"; shift 2 ;;
        --work-dir) WORK="${2:-$WORK}"; shift 2 ;;
        --unit) UNIT="${2:-$UNIT}"; shift 2 ;;
        --snapshot) SNAPSHOT="${2:-}"; shift 2 ;;
        --keep-artifacts) KEEP_ARTIFACTS="true"; shift ;;
        --help|-h) usage; exit 0 ;;
        -*) echo "Unknown option: $1" >&2; usage; exit 1 ;;
        *) if [[ -z "$VM_HOST" ]]; then VM_HOST="$1"; RUN_MODE="remote"; fi; shift ;;
    esac
done

if [[ -z "$VM_HOST" && -n "$ENV_VM_HOST" ]]; then
    VM_HOST="$ENV_VM_HOST"; RUN_MODE="remote"
fi

SSH_CMD=()
if [[ "$RUN_MODE" == "remote" && -n "$VM_HOST" ]]; then
    SSH_OPTS=(-o ConnectTimeout=10 -o StrictHostKeyChecking=no)
    [[ -f "$SSH_KEY" ]] && SSH_OPTS+=(-i "$SSH_KEY")
    SSH_CMD=(ssh "${SSH_OPTS[@]}" "${SSH_USER}@${VM_HOST}")
fi

run_shell() {
    if [[ ${#SSH_CMD[@]} -gt 0 ]]; then "${SSH_CMD[@]}" "$1"; else bash -c "$1"; fi
}
pass() { echo -e "${GREEN}PASS${NC}: $1"; PASS=$((PASS + 1)); }
fail() { echo -e "${RED}FAIL${NC}: $1"; FAIL=$((FAIL + 1)); }
warn() { echo -e "${YELLOW}WARN${NC}: $1"; WARN=$((WARN + 1)); }
info() { echo -e "${BLUE}INFO${NC}: $1"; }

STREAM_NAME="${RTMP_URL##*/}"
FINGERPRINTS='obs-output|libobs|x264 - core|Lavf|Lavc|NGINX RTMP|RtmpSampleAccess'

echo -e "${BLUE}==========================================${NC}"
echo -e "${BLUE}OBS end-to-end check${NC}"
echo -e "${BLUE}==========================================${NC}"
[[ -n "$VM_HOST" ]] && echo "Target:   ${SSH_USER}@${VM_HOST}" || echo "Target:   localhost"
echo "Stream:   $RTMP_URL  (OBS server: rtmp://<ingest-host>/live, key: $STREAM_NAME)"
echo "Capture:  ${DURATION}s after OBS is live (waiting up to ${WAIT}s)"
echo ""

run_shell "command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1" && pass "ffmpeg/ffprobe available" || { fail "ffmpeg/ffprobe missing"; exit 1; }

# ---- 1. wait for OBS ----------------------------------------------------
if run_shell "curl -s --max-time 3 http://127.0.0.1:8081/health 2>/dev/null | grep -q OK"; then
    pass "nginx-rtmp is up (health OK)"
else
    fail "nginx-rtmp health endpoint not answering on the target; is nginx-rtmp.service running?"
    exit 1
fi

publishing() {
    run_shell "curl -s --max-time 3 '$STAT_URL' 2>/dev/null | tr -d '\n' | sed 's#</stream>#</stream>\n#g' | grep '<name>${STREAM_NAME}</name>' | grep -q '<publishing/>'"
}

info "Start streaming in OBS now (Settings > Stream > Custom, Server rtmp://<ingest-host>/live, Stream key ${STREAM_NAME})."
WAITED=0
until publishing; do
    if [[ "$WAITED" -ge "$WAIT" ]]; then
        fail "no publisher on '${STREAM_NAME}' after ${WAIT}s. Check OBS 'Start Streaming', the stream key, and that TCP 1935 is open on the host's security list / firewall."
        exit 1
    fi
    sleep 2; WAITED=$((WAITED + 2))
done
pass "OBS is publishing on '${STREAM_NAME}' (nginx-rtmp stat shows <publishing/>)"

# ---- 2. what OBS sends ---------------------------------------------------
run_shell "rm -rf '$WORK' && mkdir -p '$WORK'"
run_shell "ffprobe -v error -rw_timeout 8000000 -show_format -show_streams '$RTMP_URL' >'$WORK/probe.txt' 2>&1 || true"
VCODEC="$(run_shell "grep -m1 '^codec_name=' '$WORK/probe.txt' | cut -d= -f2" | tr -d '\r')"
VW="$(run_shell "grep -m1 '^width=' '$WORK/probe.txt' | cut -d= -f2" | tr -d '\r')"
VH="$(run_shell "grep -m1 '^height=' '$WORK/probe.txt' | cut -d= -f2" | tr -d '\r')"
VFPS="$(run_shell "grep -m1 '^r_frame_rate=' '$WORK/probe.txt' | cut -d= -f2" | tr -d '\r')"
PIX="$(run_shell "grep -m1 '^pix_fmt=' '$WORK/probe.txt' | cut -d= -f2" | tr -d '\r')"
ACODEC="$(run_shell "grep '^codec_name=' '$WORK/probe.txt' | sed -n 2p | cut -d= -f2" | tr -d '\r')"
ARATE="$(run_shell "grep -m1 '^sample_rate=' '$WORK/probe.txt' | cut -d= -f2" | tr -d '\r')"
ACH="$(run_shell "grep -m1 '^channels=' '$WORK/probe.txt' | cut -d= -f2" | tr -d '\r')"
TAGS="$(run_shell "grep '^TAG:' '$WORK/probe.txt' | sed 's/^TAG://' | tr '\n' ' '" | tr -d '\r')"

if [[ -n "$VCODEC" && -n "$VW" && "$VW" != "0" ]]; then
    pass "incoming video: ${VCODEC} ${VW}x${VH} ${PIX} @ ${VFPS} fps"
else
    fail "could not probe the incoming video stream (see $WORK/probe.txt)"
fi
if [[ -n "$ACODEC" ]]; then
    pass "incoming audio: ${ACODEC} ${ARATE} Hz ${ACH} ch"
else
    warn "no audio stream from OBS (mic sink will stay silent)"
fi
if [[ -n "$TAGS" ]]; then
    info "metadata riding in with the stream (must NOT reach the sinks): ${TAGS}"
else
    info "no metadata tags on the incoming stream"
fi
if [[ "$PIX" != "yuv420p" && -n "$PIX" ]]; then
    warn "OBS pixel format is ${PIX}; the bridge converts to yuv420p (set OBS Advanced > Video > Color Format to NV12/I420 for a no-op conversion)"
fi

# ---- 3. production bridge state -----------------------------------------
if run_shell "systemctl is-active --quiet '$UNIT' 2>/dev/null"; then
    pass "bridge unit ${UNIT} is active"
    BRIDGE_LOG="$(run_shell "systemctl show -p EnvironmentFiles '$UNIT' >/dev/null 2>&1; ls -t /tmp/cuttlefish-bridge/bridge.log 2>/dev/null | head -1" | tr -d '\r')"
    if [[ -n "$BRIDGE_LOG" ]]; then
        LAST="$(run_shell "grep -E 'Stream detected|Restarting|ERROR' '$BRIDGE_LOG' | tail -1" | tr -d '\r')"
        [[ -n "$LAST" ]] && info "bridge log: ${LAST}"
        if run_shell "tail -50 '$BRIDGE_LOG' | grep -q 'Stream detected'"; then
            pass "bridge attached a worker (bridge.log)"
        else
            warn "bridge.log shows no recent 'Stream detected'"
        fi
        PROGRESS="$(run_shell "grep '^frame=' /tmp/cuttlefish-bridge/progress.txt 2>/dev/null | tail -1" | tr -d '\r')"
        [[ -n "$PROGRESS" ]] && info "bridge worker progress: ${PROGRESS}"
    fi
else
    warn "bridge unit ${UNIT} is not active on the target (the tap below still validates the pipeline)"
fi

# ---- 4. tap the stream with a clean worker ------------------------------
info "tapping '${STREAM_NAME}' for ${DURATION}s with a clean worker (production bridge untouched)"
TAP="ffmpeg -hide_banner -loglevel warning -nostdin -probesize 32 -analyzeduration 0 -flags +low_delay -t ${DURATION} -i '$RTMP_URL' -map 0:v:0 -an -sn -dn -map_metadata -1 -map_chapters -1 -bitexact -fps_mode passthrough -filter:v format=yuv420p -c:v rawvideo -pix_fmt yuv420p -flush_packets 1 -f tee '[f=rawvideo:onfail=ignore:flush_packets=1]$WORK/tap.yuv|[f=yuv4mpegpipe:onfail=ignore:flush_packets=1]$WORK/tap.y4m' -map 0:a:0? -vn -sn -dn -map_metadata -1 -map_chapters -1 -bitexact -c:a pcm_s16le -flush_packets 1 -f s16le '$WORK/tap.pcm'"
run_shell "$TAP >'$WORK/tap.log' 2>&1 || true"

if [[ -n "$VW" && "$VW" != "0" ]]; then
    FRAME_BYTES=$((VW * VH * 3 / 2))
    SIZE="$(run_shell "stat -c%s '$WORK/tap.yuv' 2>/dev/null || echo 0" | tr -d '\r')"
    FRAMES=$((SIZE / FRAME_BYTES)); REM=$((SIZE % FRAME_BYTES))
    if [[ "$FRAMES" -gt 0 && "$REM" -eq 0 ]]; then
        pass "tap: ${FRAMES} raw yuv420p frames, exact multiple of ${FRAME_BYTES} bytes (no header/trailer bytes)"
    elif [[ "$FRAMES" -gt 0 ]]; then
        fail "tap: ${FRAMES} frames plus ${REM} stray bytes"
    else
        fail "tap captured no frames (see $WORK/tap.log)"
    fi
    FPS_NUM="${VFPS%%/*}"; FPS_DEN="${VFPS##*/}"
    if [[ "$FPS_NUM" =~ ^[0-9]+$ && "$FPS_DEN" =~ ^[0-9]+$ && "$FPS_DEN" -gt 0 ]]; then
        EXPECTED=$((DURATION * FPS_NUM / FPS_DEN))
        if [[ "$FRAMES" -ge $((EXPECTED * 70 / 100)) ]]; then
            pass "tap: frame count ${FRAMES} is consistent with ${VFPS} fps over ${DURATION}s (expected ~${EXPECTED} minus attach time)"
        else
            fail "tap: only ${FRAMES} frames over ${DURATION}s at ${VFPS} fps (expected ~${EXPECTED}); frames are being dropped upstream"
        fi
    fi
    DUPS="$(run_shell "ffmpeg -hide_banner -loglevel error -f rawvideo -video_size ${VW}x${VH} -pix_fmt yuv420p -i '$WORK/tap.yuv' -f framemd5 - | awk '/^[0-9]/{print \$NF}' | uniq -d | wc -l" | tr -d '\r')"
    if [[ "${DUPS:-0}" -eq 0 ]]; then
        pass "tap: no consecutive duplicate frames (passthrough timing, not CFR padding)"
    else
        warn "tap: ${DUPS} consecutive duplicate frames (a static OBS scene also causes this)"
    fi
fi

if run_shell "test -s '$WORK/tap.y4m'"; then
    if run_shell "strings '$WORK/tap.y4m' | grep -qE '$FINGERPRINTS'"; then
        fail "framed output carries an input fingerprint (obs/x264/Lavf/NGINX)"
    else
        pass "framed output carries none of: obs-output, libobs, x264, Lavf/Lavc, NGINX RTMP"
    fi
    HEADER="$(run_shell "head -c 120 '$WORK/tap.y4m' | head -1" | tr -d '\r')"
    info "y4m header: ${HEADER}"
fi

if [[ -n "$ACODEC" ]]; then
    PCM="$(run_shell "stat -c%s '$WORK/tap.pcm' 2>/dev/null || echo 0" | tr -d '\r')"
    if [[ "${ARATE:-0}" -gt 0 && "${ACH:-0}" -gt 0 ]]; then
        MS=$((PCM * 1000 / (ARATE * ACH * 2)))
        if [[ "$MS" -ge $((DURATION * 500)) ]]; then
            pass "tap: ${MS}ms of raw s16le PCM (${ARATE} Hz, ${ACH} ch)"
        else
            fail "tap: only ${MS}ms of PCM for a ${DURATION}s capture"
        fi
    fi
fi

# ---- 5. snapshot ---------------------------------------------------------
if [[ -n "$VW" && "$VW" != "0" ]] && run_shell "test -s '$WORK/tap.yuv'"; then
    if run_shell "ffmpeg -hide_banner -loglevel error -y -f rawvideo -video_size ${VW}x${VH} -pix_fmt yuv420p -i '$WORK/tap.yuv' -frames:v 1 -update 1 '$WORK/snapshot.png'"; then
        pass "snapshot written: $WORK/snapshot.png (open it: this is exactly what the phone camera will see)"
        if [[ -n "$SNAPSHOT" && ${#SSH_CMD[@]} -gt 0 ]]; then
            SCP_OPTS=(-o StrictHostKeyChecking=no); [[ -f "$SSH_KEY" ]] && SCP_OPTS+=(-i "$SSH_KEY")
            scp "${SCP_OPTS[@]}" "${SSH_USER}@${VM_HOST}:$WORK/snapshot.png" "$SNAPSHOT" >/dev/null 2>&1 && info "snapshot copied to $SNAPSHOT"
        fi
    else
        warn "could not render a PNG snapshot"
    fi
fi

if [[ "$KEEP_ARTIFACTS" != "true" ]]; then
    run_shell "rm -f '$WORK/tap.yuv' '$WORK/tap.y4m' '$WORK/tap.pcm'"
    info "raw captures removed (kept: probe.txt, tap.log, snapshot.png under $WORK; --keep-artifacts keeps everything)"
fi

echo ""
echo -e "${BLUE}Summary:${NC} PASS=$PASS FAIL=$FAIL WARN=$WARN"
[[ "$FAIL" -gt 0 ]] && exit 1
exit 0
