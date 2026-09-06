#!/bin/bash
# TDD ladder runner for cloud-phone.
#
#   ./cloud-phone test              # rungs 0–3 (offline)
#   ./cloud-phone test --rung 3     # dual-pool e2e only
#   ./cloud-phone test --list
#   ./cloud-phone test --live       # also R4 (needs CLOUD_PHONE_LIVE=1 + API)
#
# Offline rungs never touch Docker, OCI, or a proprietary GApps zip.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON="${PYTHON:-python3}"
RUNG=""
LIVE="false"
VERBOSE="-v"
SHOW_LOGS="true"
REPORT_DIR="${TEST_REPORT_DIR:-$PROJECT_ROOT/.test-reports}"
LIST="false"
SUITE_TIMEOUT="${SUITE_TIMEOUT:-90}"

# Capture user LOG_LEVEL before log.sh defaults it to INFO.
USER_LOG_LEVEL="${LOG_LEVEL:-}"
export CLOUD_PHONE_VERBOSE="${CLOUD_PHONE_VERBOSE:-1}"
export LOG_LEVEL="${USER_LOG_LEVEL:-DEBUG}"
export ORCH_LOG_LEVEL="${ORCH_LOG_LEVEL:-DEBUG}"

if [[ -f "$SCRIPT_DIR/lib/log.sh" ]]; then
    # shellcheck source=lib/log.sh
    source "$SCRIPT_DIR/lib/log.sh"
    LOG_TYPE=TST
    LOG_LEVEL="${USER_LOG_LEVEL:-DEBUG}"
else
    log_info() { echo "[INFO] $*"; }
    log_error() { echo "[ERROR] $*" >&2; }
fi

cd "$PROJECT_ROOT"

# name:test_file.py  (unittest discover -p)
RUNG0=(
    "gapps-zip:test_gapps_zip.py"
    "gapps-health:test_gapps_health.py"
    "orchestrator-unit:test_orchestrator_unit.py"
    "scripts-contract:test_scripts_contract.py"
    "logging:test_logging.py"
    "ui-control:test_ui_control.py"
)
RUNG1=(
    "runtime-pool:test_runtime_pool.py"
    "control-api:test_control_api.py"
)
RUNG2=(
    "orchestrator-integration:test_orchestrator_integration.py"
    "orchestrator-e2e:test_orchestrator_e2e.py"
)
RUNG3=(
    "ladder-e2e:test_ladder_e2e.py"
)
RUNG4=(
    "live:test_live.py"
)

usage() {
    cat <<'EOF'
Usage: ./scripts/run-tests.sh [OPTIONS]

  --rung N        0=unit 1=component 2=process-integration 3=dual-pool-e2e 4=live
  --live          Include rung 4 (or set CLOUD_PHONE_LIVE=1)
  --list          Print the ladder
  --quiet         Less unittest noise, hide labeled log excerpts
  --verbose       Unittest -v and dump ADB/Appium/commandlet/VNC log lines
  --show-logs     Dump labeled log excerpts after each suite
  --report-dir D  Log directory (default .test-reports)
  --help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --rung) RUNG="${2:-}"; shift 2 ;;
        --live) LIVE="true"; shift ;;
        --list) LIST="true"; shift ;;
        --quiet) VERBOSE=""; SHOW_LOGS="false"; shift ;;
        --verbose|-v) VERBOSE="-v"; SHOW_LOGS="true"; shift ;;
        --show-logs) SHOW_LOGS="true"; shift ;;
        --report-dir) REPORT_DIR="${2:-}"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) log_error "Unknown option: $1"; usage; exit 1 ;;
    esac
done

print_ladder() {
    cat <<'EOF'
TDD ladder (offline through R3):

  R0 unit                 GApps zip, purpose mapping, script contracts, labeled logs, UI commandlets
  R1 component            Orchestrator pool (Flask test client) + Control API (patched ADB, Appium/VNC logs)
  R2 process integration  Real orchestrator process + one fake Control API
  R3 dual-pool e2e        Redroid(GApps) + Cuttlefish(ingest) processes, sessions, Play launch, verbose IO logs
  R4 live                 Real Control API (CLOUD_PHONE_LIVE=1) — skipped offline
EOF
    echo
    echo "Suites:"
    for entry in "${RUNG0[@]}"; do echo "  rung0  ${entry%%:*}"; done
    for entry in "${RUNG1[@]}"; do echo "  rung1  ${entry%%:*}"; done
    for entry in "${RUNG2[@]}"; do echo "  rung2  ${entry%%:*}"; done
    for entry in "${RUNG3[@]}"; do echo "  rung3  ${entry%%:*}"; done
    for entry in "${RUNG4[@]}"; do echo "  rung4  ${entry%%:*} (live)"; done
}

if [[ "$LIST" == "true" ]]; then
    print_ladder
    exit 0
fi

suites_for_rung() {
    case "$1" in
        0) printf '%s\n' "${RUNG0[@]}" ;;
        1) printf '%s\n' "${RUNG1[@]}" ;;
        2) printf '%s\n' "${RUNG2[@]}" ;;
        3) printf '%s\n' "${RUNG3[@]}" ;;
        4) printf '%s\n' "${RUNG4[@]}" ;;
        *) return 1 ;;
    esac
}

RUNGS=(0 1 2 3)
if [[ -n "$RUNG" ]]; then
    RUNGS=("$RUNG")
fi
if [[ "$LIVE" == "true" || "${CLOUD_PHONE_LIVE:-}" == "1" ]]; then
    if [[ -z "$RUNG" ]]; then
        RUNGS=(0 1 2 3 4)
    fi
fi

mkdir -p "$REPORT_DIR"
failed=0
ran=0

echo "TDD ladder using $PYTHON"
print_ladder
echo

for rung in "${RUNGS[@]}"; do
    if [[ "$rung" == "4" && "$LIVE" != "true" && "${CLOUD_PHONE_LIVE:-}" != "1" ]]; then
        log_info "skip R4 live (set --live or CLOUD_PHONE_LIVE=1)"
        continue
    fi
    echo "======== R${rung} ========"
    while IFS= read -r entry; do
        [[ -z "$entry" ]] && continue
        name="${entry%%:*}"
        file="${entry##*:}"
        log="${REPORT_DIR}/r${rung}-${name}.log"
        echo "-- ${name} ($file)"
        log_info "r${rung} ${name} verbose=${CLOUD_PHONE_VERBOSE} log_level=${LOG_LEVEL}"
        if command -v timeout >/dev/null 2>&1; then
            if ! timeout "$SUITE_TIMEOUT" "$PYTHON" -m unittest discover -s tests -p "$file" $VERBOSE >"$log" 2>&1; then
                echo "FAIL ${name}  (log: $log)"
                tail -40 "$log" || true
                failed=$((failed + 1))
            else
                echo "PASS ${name}"
            fi
        elif ! "$PYTHON" -m unittest discover -s tests -p "$file" $VERBOSE >"$log" 2>&1; then
            echo "FAIL ${name}  (log: $log)"
            tail -40 "$log" || true
            failed=$((failed + 1))
        else
            echo "PASS ${name}"
        fi
        if [[ "$SHOW_LOGS" == "true" ]]; then
            echo "--- labeled logs (${name}) ---"
            grep -E '\[(ADB|CMD|APM|VNC|API|ORC|TST)\]' "$log" | tail -80 || true
        fi
        ran=$((ran + 1))
    done < <(suites_for_rung "$rung")
done

echo
if [[ "$failed" -gt 0 ]]; then
    log_error "ladder failed: ${failed}/${ran} suites red"
    exit 1
fi
echo "ladder green: ${ran} suites"
exit 0
