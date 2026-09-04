#!/bin/bash
# Unified test runner with coverage.
#
#   ./cloud-phone test                # offline suites (no device, no OCI)
#   ./cloud-phone test --coverage     # + line coverage report
#   ./cloud-phone test --suite procedures
#   ./cloud-phone test --live --api-url http://127.0.0.1:8080
#
# Offline suites never touch a phone, Docker, or OCI, so they are safe in CI.
# Live suites need a reachable Control API and are skipped unless --live.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/log.sh"
LOG_TYPE=TST

cd "$PROJECT_ROOT"

OFFLINE_SUITES=(
    "logging:tests.test_logging"
    "control-api:tests.test_control_api"
    "procedures:tests.test_procedures"
    "orchestrator:tests.test_orchestrator_unit"
    "sessions:tests.test_user_sessions"
    "gapps:tests.test_gapps_zip"
    "mobile-e2e:tests.test_mobile_e2e_scenario"
    "procedure-api:tests.test_procedure_api"
)

SCRIPT_SUITES=(
    "orchestrator-integration:tests/test_orchestrator_integration.py"
    "orchestrator-e2e:tests/test_orchestrator_e2e.py"
)

COVERAGE="false"
FAIL_UNDER="${COVERAGE_FAIL_UNDER:-80}"
SUITE=""
LIVE="false"
API_URL="${CLOUD_PHONE_API_URL:-http://127.0.0.1:8080}"
VERBOSE=""
REPORT_DIR="${TEST_REPORT_DIR:-$PROJECT_ROOT/.test-reports}"

usage() {
    cat <<'EOF'
Usage: ./scripts/run-tests.sh [OPTIONS]

  --coverage           Measure line coverage (needs `coverage`)
  --fail-under N       Coverage floor, default 80 (COVERAGE_FAIL_UNDER)
  --suite NAME         Run one suite (see --list)
  --list               List suites
  --live               Also run suites needing a real Control API
  --api-url URL        Control API for --live
  --verbose            Per-test output
  --report-dir DIR     Where to write logs (default .test-reports)
  --help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --coverage) COVERAGE="true"; shift ;;
        --fail-under) FAIL_UNDER="${2:-80}"; shift 2 ;;
        --suite) SUITE="${2:-}"; shift 2 ;;
        --live) LIVE="true"; shift ;;
        --api-url) API_URL="${2:-}"; shift 2 ;;
        --verbose|-v) VERBOSE="-v"; shift ;;
        --report-dir) REPORT_DIR="${2:-}"; shift 2 ;;
        --list)
            for entry in "${OFFLINE_SUITES[@]}"; do echo "  ${entry%%:*} (offline)"; done
            for entry in "${SCRIPT_SUITES[@]}"; do echo "  ${entry%%:*} (offline, script)"; done
            echo "  agent-api (live)"
            echo "  connectivity (live)"
            exit 0
            ;;
        --help|-h) usage; exit 0 ;;
        *) log_error "Unknown option: $1"; usage; exit 1 ;;
    esac
done

mkdir -p "$REPORT_DIR"
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

PYTHON="${PYTHON:-python3}"
COVERAGE_BIN=""
if [[ "$COVERAGE" == "true" ]]; then
    if "$PYTHON" -m coverage --version >/dev/null 2>&1; then
        COVERAGE_BIN="$PYTHON -m coverage"
        $COVERAGE_BIN erase >/dev/null 2>&1 || true
    else
        log_warn "coverage not installed (pip install coverage); running without it"
        COVERAGE="false"
    fi
fi

PASS=0
FAIL=0
FAILED_SUITES=()

run_python_suite() {
    local name="$1" target="$2" kind="$3"
    local logfile="$REPORT_DIR/${name}.log"
    log_info "suite ${name} (${kind})"

    if [[ "$COVERAGE" == "true" ]]; then
        $COVERAGE_BIN run --append -m unittest "$target" $VERBOSE >"$logfile" 2>&1
    else
        "$PYTHON" -m unittest "$target" $VERBOSE >"$logfile" 2>&1
    fi
    local status=$?

    if [[ $status -eq 0 ]]; then
        local count
        count="$(grep -oE '^Ran [0-9]+' "$logfile" | tail -1 | awk '{print $2}')"
        log_info "  PASS ${name} (${count:-?} tests)"
        PASS=$((PASS + 1))
    else
        log_error "  FAIL ${name} — see ${logfile}"
        tail -25 "$logfile" >&2
        FAIL=$((FAIL + 1))
        FAILED_SUITES+=("$name")
    fi
}

run_script_suite() {
    local name="$1" path="$2"
    local logfile="$REPORT_DIR/${name}.log"
    log_info "suite ${name} (offline, script)"

    if [[ "$COVERAGE" == "true" ]]; then
        $COVERAGE_BIN run --append "$path" >"$logfile" 2>&1
    else
        "$PYTHON" "$path" >"$logfile" 2>&1
    fi
    local status=$?

    if [[ $status -eq 0 ]]; then
        log_info "  PASS ${name}"
        PASS=$((PASS + 1))
    else
        log_error "  FAIL ${name} — see ${logfile}"
        tail -25 "$logfile" >&2
        FAIL=$((FAIL + 1))
        FAILED_SUITES+=("$name")
    fi
}

matches() {
    [[ -z "$SUITE" || "$SUITE" == "$1" ]]
}

for entry in "${OFFLINE_SUITES[@]}"; do
    name="${entry%%:*}"
    target="${entry#*:}"
    matches "$name" && run_python_suite "$name" "$target" "offline"
done

for entry in "${SCRIPT_SUITES[@]}"; do
    name="${entry%%:*}"
    path="${entry#*:}"
    matches "$name" && run_script_suite "$name" "$path"
done

if [[ "$LIVE" == "true" ]]; then
    if matches "agent-api"; then
        log_info "suite agent-api (live -> $API_URL)"
        if "$PYTHON" tests/test_agent_api.py --api-url "$API_URL" \
                >"$REPORT_DIR/agent-api.log" 2>&1; then
            log_info "  PASS agent-api"; PASS=$((PASS + 1))
        else
            log_error "  FAIL agent-api — see $REPORT_DIR/agent-api.log"
            FAIL=$((FAIL + 1)); FAILED_SUITES+=("agent-api")
        fi
    fi
else
    log_info "skipping live suites (pass --live with a reachable Control API)"
fi

COVERAGE_STATUS=0
if [[ "$COVERAGE" == "true" ]]; then
    log_info "coverage report (fail-under=${FAIL_UNDER}%)"
    $COVERAGE_BIN report \
        --include='api/*,orchestrator/*' \
        --omit='*/test_*,*/venv/*,*/.venv/*' \
        --fail-under="$FAIL_UNDER" | tee "$REPORT_DIR/coverage.txt" >&2
    COVERAGE_STATUS=${PIPESTATUS[0]}
    $COVERAGE_BIN html --include='api/*,orchestrator/*' \
        -d "$REPORT_DIR/htmlcov" >/dev/null 2>&1 || true
    $COVERAGE_BIN xml -o "$REPORT_DIR/coverage.xml" >/dev/null 2>&1 || true
    if [[ $COVERAGE_STATUS -ne 0 ]]; then
        log_error "coverage below ${FAIL_UNDER}%"
    fi
fi

echo "" >&2
if [[ $FAIL -eq 0 && $COVERAGE_STATUS -eq 0 ]]; then
    log_info "SUMMARY suites=${PASS} failed=0 reports=${REPORT_DIR}"
    exit 0
fi
log_error "SUMMARY suites_passed=${PASS} failed=${FAIL} [${FAILED_SUITES[*]:-}] reports=${REPORT_DIR}"
exit 1
