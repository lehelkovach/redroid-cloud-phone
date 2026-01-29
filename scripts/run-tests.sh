#!/bin/bash
# Cloud Phone Test Runner
#
# Runs comprehensive tests against a live Cloud Phone instance.
# All output is logged to files for later analysis.
#
# Usage:
#   ./run-tests.sh                           # Test local instance
#   ./run-tests.sh --api-url http://host:8080
#   ./run-tests.sh --instance-ip 129.146.x.x  # Test remote instance (via SSH tunnel)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Configuration
API_URL="${API_URL:-http://localhost:8080}"
INSTANCE_IP="${INSTANCE_IP:-}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/redroid_oci}"
LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/test-logs}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
TEST_RUN_ID="test-run-$TIMESTAMP"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }
log_header() { echo -e "${BLUE}========================================${NC}"; echo -e "${BLUE}  $1${NC}"; echo -e "${BLUE}========================================${NC}"; }

usage() {
    cat <<EOF
Cloud Phone Test Runner

Usage: $0 [OPTIONS]

Options:
  --api-url URL         API URL (default: http://localhost:8080)
  --instance-ip IP      Remote instance IP (creates SSH tunnel automatically)
  --ssh-key PATH        SSH key for remote instance
  --log-dir DIR         Log directory (default: ./test-logs)
  --skip-api-tests      Skip API tests
  --skip-health         Skip health checks
  --quick               Quick test (skip slow tests)
  --help                Show this help

Examples:
  # Test local instance
  $0

  # Test remote instance (creates SSH tunnel)
  $0 --instance-ip 129.146.123.45

  # Test with custom log directory
  $0 --log-dir /var/log/cloud-phone-tests

EOF
    exit 0
}

SKIP_API=false
SKIP_HEALTH=false
QUICK_MODE=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --api-url) API_URL="$2"; shift 2 ;;
        --instance-ip) INSTANCE_IP="$2"; shift 2 ;;
        --ssh-key) SSH_KEY="$2"; shift 2 ;;
        --log-dir) LOG_DIR="$2"; shift 2 ;;
        --skip-api-tests) SKIP_API=true; shift ;;
        --skip-health) SKIP_HEALTH=true; shift ;;
        --quick) QUICK_MODE=true; shift ;;
        --help|-h) usage ;;
        *) log_error "Unknown option: $1"; usage ;;
    esac
done

# Create log directory
mkdir -p "$LOG_DIR"
MAIN_LOG="$LOG_DIR/$TEST_RUN_ID.log"
JSON_LOG="$LOG_DIR/$TEST_RUN_ID.json"

# Start logging
exec > >(tee -a "$MAIN_LOG") 2>&1

log_header "Cloud Phone Test Runner"
echo "Test Run ID: $TEST_RUN_ID"
echo "Timestamp: $(date)"
echo "Log File: $MAIN_LOG"
echo ""

# Setup SSH tunnel if testing remote instance
TUNNEL_PID=""
cleanup() {
    if [[ -n "$TUNNEL_PID" ]]; then
        log_info "Closing SSH tunnel..."
        kill "$TUNNEL_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

if [[ -n "$INSTANCE_IP" ]]; then
    log_info "Setting up SSH tunnel to $INSTANCE_IP..."
    
    if [[ ! -f "$SSH_KEY" ]]; then
        log_error "SSH key not found: $SSH_KEY"
        exit 1
    fi
    
    # Start SSH tunnel (API on 8080, VNC on 5900)
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no \
        -L 8080:localhost:8080 \
        -L 5900:localhost:5900 \
        ubuntu@"$INSTANCE_IP" -N &
    TUNNEL_PID=$!
    
    # Wait for tunnel
    sleep 3
    
    if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
        log_error "Failed to establish SSH tunnel"
        exit 1
    fi
    
    API_URL="http://localhost:8080"
    log_info "SSH tunnel established (PID: $TUNNEL_PID)"
fi

echo "API URL: $API_URL"
echo ""

# =============================================================================
# Health Checks
# =============================================================================

if [[ "$SKIP_HEALTH" != "true" ]]; then
    log_header "Health Checks"
    
    # Test API connectivity
    log_info "Testing API connectivity..."
    if curl -sf "$API_URL/health" > /dev/null 2>&1; then
        log_info "✓ API is reachable"
        curl -s "$API_URL/health" | jq .
    else
        log_error "✗ Cannot reach API at $API_URL"
        log_error "Make sure the Cloud Phone is running and API is started"
        exit 1
    fi
    
    echo ""
    
    # Test ADB via API
    log_info "Testing ADB connection..."
    HEALTH=$(curl -s "$API_URL/health")
    ADB_CONNECTED=$(echo "$HEALTH" | jq -r '.data.adb_connected')
    
    if [[ "$ADB_CONNECTED" == "true" ]]; then
        log_info "✓ ADB is connected"
    else
        log_error "✗ ADB not connected"
        log_warn "Attempting to reconnect..."
        # The API should auto-connect, but let's give it time
        sleep 5
        HEALTH=$(curl -s "$API_URL/health")
        ADB_CONNECTED=$(echo "$HEALTH" | jq -r '.data.adb_connected')
        if [[ "$ADB_CONNECTED" != "true" ]]; then
            log_error "ADB connection failed. Check Redroid container."
            exit 1
        fi
    fi
    
    echo ""
    
    # Get device info
    log_info "Device Information:"
    curl -s "$API_URL/device/info" | jq '.data'
    echo ""
fi

# =============================================================================
# API Tests
# =============================================================================

if [[ "$SKIP_API" != "true" ]]; then
    log_header "API Tests"
    
    # Check if Python and dependencies are available
    if ! command -v python3 &>/dev/null; then
        log_error "Python3 not found. Installing..."
        apt-get update && apt-get install -y python3 python3-pip || true
    fi
    
    # Install test dependencies
    log_info "Installing test dependencies..."
    pip3 install -q requests 2>/dev/null || true
    
    # Run test suite
    log_info "Running API test suite..."
    echo ""
    
    TEST_ARGS="--api-url $API_URL --log-file $LOG_DIR/$TEST_RUN_ID-api.log --output-json $JSON_LOG"
    
    if [[ "$QUICK_MODE" == "true" ]]; then
        log_warn "Quick mode - running subset of tests"
    fi
    
    if python3 "$PROJECT_ROOT/tests/test_agent_api.py" $TEST_ARGS; then
        log_info "✓ API tests passed"
        API_TEST_RESULT="PASSED"
    else
        log_error "✗ API tests failed"
        API_TEST_RESULT="FAILED"
    fi
    
    echo ""
fi

# =============================================================================
# Screenshot Test
# =============================================================================

log_header "Screenshot Test"

log_info "Taking screenshot..."
SCREENSHOT_FILE="$LOG_DIR/$TEST_RUN_ID-screenshot.png"

if curl -sf "$API_URL/screen/screenshot?format=png" -o "$SCREENSHOT_FILE"; then
    FILE_SIZE=$(stat -f%z "$SCREENSHOT_FILE" 2>/dev/null || stat -c%s "$SCREENSHOT_FILE")
    log_info "✓ Screenshot saved: $SCREENSHOT_FILE ($FILE_SIZE bytes)"
else
    log_error "✗ Screenshot capture failed"
fi

echo ""

# =============================================================================
# Interaction Test
# =============================================================================

log_header "Interaction Test"

log_info "Testing touch input..."

# Go home
curl -sf -X POST "$API_URL/input/home" | jq -r '.success' | grep -q true && \
    log_info "✓ Home button" || log_error "✗ Home button"

sleep 1

# Tap center
curl -sf -X POST "$API_URL/input/tap" \
    -H "Content-Type: application/json" \
    -d '{"x": 50, "y": 50, "percentage": true}' | jq -r '.success' | grep -q true && \
    log_info "✓ Tap (percentage)" || log_error "✗ Tap (percentage)"

sleep 1

# Swipe up
curl -sf -X POST "$API_URL/input/swipe" \
    -H "Content-Type: application/json" \
    -d '{"x1": 50, "y1": 75, "x2": 50, "y2": 25, "percentage": true}' | jq -r '.success' | grep -q true && \
    log_info "✓ Swipe up" || log_error "✗ Swipe up"

sleep 1

# Go home again
curl -sf -X POST "$API_URL/input/home" > /dev/null

echo ""

# =============================================================================
# Summary
# =============================================================================

log_header "Test Summary"

echo "Test Run: $TEST_RUN_ID"
echo "Logs saved to: $LOG_DIR/"
echo ""
echo "Log files:"
ls -la "$LOG_DIR/$TEST_RUN_ID"* 2>/dev/null || echo "  (none)"
echo ""

if [[ -f "$JSON_LOG" ]]; then
    log_info "API Test Results:"
    jq '.summary' "$JSON_LOG"
fi

echo ""
log_info "Test run complete!"

# Exit with appropriate code
if [[ "${API_TEST_RESULT:-SKIPPED}" == "FAILED" ]]; then
    exit 1
fi
exit 0
