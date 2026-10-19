#!/bin/bash
# cuttlefish-ingest-e2e.sh
# End to end: a known looping A/V clip -> nginx-rtmp on the Cuttlefish host ->
# cuttlefish-rtmp-bridge -> front/back/mic sinks -> the GUEST's camera and mic,
# sampled and matched back to the clip (tools/ingest_probe.py).
#
# Rungs (each prints PASS/FAIL/WARN; the exit code is the sum of FAILs):
#   I0  ffmpeg/ffprobe present; the probe tool answers
#   I1  nginx-rtmp /health on :8081 (WARN when absent: the loop still runs
#       against whatever listens on --rtmp-url)
#   I2  clip generated (colour blocks + tone), bridge started to file sinks,
#       clip published in a loop for --duration seconds
#   I3  sinks judged: front/back cycle through the palette, mic carries the tone
#   I4  guest camera: open the camera app, screencap, centre colour is in the
#       palette (needs an injector bound to the bridge sinks; --skip-guest skips)
#   I5  guest mic: `tinycap` 3 s from the default input, pull, tone detected
#
# Usage:
#   ./scripts/cuttlefish-ingest-e2e.sh --local [--duration 20] [--skip-guest]
#   ./scripts/cuttlefish-ingest-e2e.sh --vm <OCI_PUBLIC_IP> [--ssh-key PATH]
#   ./scripts/cuttlefish-ingest-e2e.sh --local --dry-run     # print the plan
#
# Options:
#   --local / --vm HOST        where the host side runs (default: local)
#   --ssh-user USER            SSH user (default: ubuntu)
#   --ssh-key PATH             SSH key (default: ~/.ssh/android_arm_cloud_phone_oci)
#   --rtmp-url URL             publish target (default: rtmp://127.0.0.1/live/cam)
#   --duration SEC             seconds to publish (default: 20)
#   --tone HZ                  tone frequency in the clip (default: 1000)
#   --adb SERIAL               guest adb serial (default: first device)
#   --camera-package PKG       camera app to open on the guest (default: com.android.camera2)
#   --skip-guest               stop after I3 (no guest sampling)
#   --keep-artifacts           keep /tmp/cf-ingest-e2e/*
#   --dry-run                  print every command, run nothing
#   --help

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=lib/log.sh
source "$SCRIPT_DIR/lib/log.sh"
LOG_TYPE=CVD

RUN_MODE="local"
VM_HOST=""
SSH_USER="ubuntu"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/android_arm_cloud_phone_oci}"
RTMP_URL="rtmp://127.0.0.1/live/cam"
DURATION="20"
TONE="1000"
ADB_SERIAL=""
CAMERA_PKG="com.android.camera2"
SKIP_GUEST="false"
KEEP="false"
DRY_RUN="false"
WORK="/tmp/cf-ingest-e2e"
PROBE="$ROOT/tools/ingest_probe.py"
BRIDGE="$SCRIPT_DIR/cuttlefish-rtmp-bridge.sh"

usage() { sed -n '2,36p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --local) RUN_MODE="local"; VM_HOST=""; shift ;;
        --vm) VM_HOST="${2:-}"; RUN_MODE="remote"; shift 2 ;;
        --ssh-user) SSH_USER="${2:-ubuntu}"; shift 2 ;;
        --ssh-key) SSH_KEY="${2:-$SSH_KEY}"; shift 2 ;;
        --rtmp-url) RTMP_URL="${2:-$RTMP_URL}"; shift 2 ;;
        --duration) DURATION="${2:-20}"; shift 2 ;;
        --tone) TONE="${2:-1000}"; shift 2 ;;
        --adb) ADB_SERIAL="${2:-}"; shift 2 ;;
        --camera-package) CAMERA_PKG="${2:-$CAMERA_PKG}"; shift 2 ;;
        --skip-guest) SKIP_GUEST="true"; shift ;;
        --keep-artifacts) KEEP="true"; shift ;;
        --dry-run) DRY_RUN="true"; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

PASS=0; FAIL=0; WARN=0
pass() { log_info "PASS $1"; echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { log_error "FAIL $1"; echo "FAIL: $1"; FAIL=$((FAIL + 1)); }
warn() { log_warn "WARN $1"; echo "WARN: $1"; WARN=$((WARN + 1)); }

SSH_CMD=()
if [[ "$RUN_MODE" == "remote" ]]; then
    SSH_OPTS=(-o ConnectTimeout=10 -o StrictHostKeyChecking=no)
    [[ -f "$SSH_KEY" ]] && SSH_OPTS+=(-i "$SSH_KEY")
    SSH_CMD=(ssh "${SSH_OPTS[@]}" "${SSH_USER}@${VM_HOST}")
fi

# Every host-side command goes through here, so --dry-run prints the plan and
# --vm runs the same plan over ssh (the repo is expected at the same path).
run_host() {
    if [[ "$DRY_RUN" == "true" ]]; then echo "[host] $1"; return 0; fi
    if [[ ${#SSH_CMD[@]} -gt 0 ]]; then "${SSH_CMD[@]}" "$1"; else bash -c "$1"; fi
}
adb_() {
    local serial_opt=""
    [[ -n "$ADB_SERIAL" ]] && serial_opt="-s $ADB_SERIAL"
    run_host "adb $serial_opt $1"
}

echo "=========================================="
echo "Cuttlefish ingest e2e"
echo "=========================================="
echo "Target:    ${VM_HOST:-localhost}"
echo "RTMP:      $RTMP_URL"
echo "Duration:  ${DURATION}s  tone: ${TONE} Hz"
echo "Guest:     $([[ "$SKIP_GUEST" == "true" ]] && echo skipped || echo "camera=$CAMERA_PKG mic=tinycap")"
echo

# ---- I0 ----------------------------------------------------------------------
if run_host "command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null"; then pass "I0 ffmpeg/ffprobe available"; else fail "I0 ffmpeg/ffprobe missing"; fi
if run_host "python3 $PROBE --help >/dev/null"; then pass "I0 ingest_probe.py answers"; else fail "I0 ingest_probe.py missing or broken"; fi

# ---- I1 ----------------------------------------------------------------------
if run_host "curl -s --max-time 3 http://127.0.0.1:8081/health 2>/dev/null | grep -q OK"; then
    pass "I1 nginx-rtmp /health OK"
else
    warn "I1 nginx-rtmp /health did not answer (publishing to $RTMP_URL anyway)"
fi

# ---- I2 ----------------------------------------------------------------------
run_host "mkdir -p $WORK && rm -f $WORK/front.ts $WORK/back.ts $WORK/mic.ts $WORK/bridge.pid"
if run_host "python3 $PROBE clip $WORK/clip.mp4 --tone $TONE >$WORK/clip.json"; then pass "I2 clip generated ($WORK/clip.mp4)"; else fail "I2 clip generation failed"; fi

BRIDGE_CMD="timeout $((DURATION + 25)) /bin/bash $BRIDGE --rtmp-url '$RTMP_URL' --front-sink 'file:$WORK/front.ts' --back-sink 'file:$WORK/back.ts' --mic-sink 'file:$WORK/mic.ts' --log-dir $WORK/bridge"
run_host "$BRIDGE_CMD >$WORK/bridge.log 2>&1 & echo \$! >$WORK/bridge.pid"
[[ "$DRY_RUN" == "true" ]] || sleep 3
PUBLISH_CMD="python3 $PROBE stream-cmd $WORK/clip.mp4 '$RTMP_URL' --seconds $DURATION | python3 -c 'import json,subprocess,sys; a=json.load(sys.stdin)[\"argv\"]; sys.exit(subprocess.run(a).returncode)'"
if run_host "$PUBLISH_CMD >$WORK/publish.log 2>&1"; then pass "I2 published the loop for ${DURATION}s"; else fail "I2 publish failed (see $WORK/publish.log)"; fi
[[ "$DRY_RUN" == "true" ]] || sleep 3
run_host "if [ -f $WORK/bridge.pid ]; then kill \$(cat $WORK/bridge.pid) 2>/dev/null || true; fi"

# ---- I3 ----------------------------------------------------------------------
if run_host "python3 $PROBE verify --front $WORK/front.ts --back $WORK/back.ts --mic $WORK/mic.ts --tone $TONE >$WORK/sinks.json"; then
    pass "I3 sinks carry the clip (colours cycle, tone ${TONE} Hz)"
else
    fail "I3 sinks do not match the clip (see $WORK/sinks.json)"
fi

# ---- I4 / I5 -----------------------------------------------------------------
if [[ "$SKIP_GUEST" == "true" ]]; then
    warn "I4/I5 guest sampling skipped (--skip-guest)"
else
    if adb_ "shell getprop sys.boot_completed 2>/dev/null | grep -q 1"; then
        pass "I4 guest booted"
        # The camera rung needs the bridge's sinks bound to the guest's camera
        # (--front-cmd/--back-cmd injectors, docs/CUTTLEFISH_PHASE2_RTMP_BRIDGE.md).
        adb_ "shell monkey -p $CAMERA_PKG -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1 || true"
        [[ "$DRY_RUN" == "true" ]] || sleep 4
        mkdir -p "$WORK"
        if adb_ "exec-out screencap -p > $WORK/guest-camera.png" && [[ "$DRY_RUN" == "true" || -s "$WORK/guest-camera.png" ]]; then
            if [[ "$DRY_RUN" == "true" ]] || { python3 "$PROBE" frame-color "$WORK/guest-camera.png" > "$WORK/guest-camera.json" && grep -q '"color": "' "$WORK/guest-camera.json"; }; then
                pass "I4 guest camera preview shows a palette colour"
            else
                fail "I4 guest camera preview is not the clip (see $WORK/guest-camera.json; is an injector bound to the sinks?)"
            fi
        else
            fail "I4 screencap failed"
        fi
        # tinycap ships on userdebug Cuttlefish builds; it records the default
        # input device, which is what an app's mic request reads.
        if adb_ "shell tinycap /sdcard/cf-mic.wav -c 1 -r 48000 -b 16 -T 3 >/dev/null 2>&1"; then
            adb_ "pull /sdcard/cf-mic.wav $WORK/guest-mic.wav >/dev/null"
            if [[ "$DRY_RUN" == "true" ]] || python3 "$PROBE" verify-audio "$WORK/guest-mic.wav" --tone "$TONE" > "$WORK/guest-mic.json"; then
                pass "I5 guest mic carries the ${TONE} Hz tone"
            else
                fail "I5 guest mic does not carry the tone (see $WORK/guest-mic.json)"
            fi
        else
            fail "I5 tinycap failed on the guest (not a userdebug build, or no input device)"
        fi
    else
        fail "I4 guest not booted / adb unreachable"
    fi
fi

if [[ "$KEEP" != "true" && "$DRY_RUN" != "true" ]]; then
    run_host "rm -f $WORK/front.ts $WORK/back.ts $WORK/mic.ts $WORK/clip.mp4 $WORK/publish.log $WORK/bridge.pid"
fi

echo
echo "Summary: PASS=$PASS FAIL=$FAIL WARN=$WARN"
exit "$FAIL"
