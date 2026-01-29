#!/bin/bash
# Deploy Cloud Phone and Run Tests
#
# This script:
# 1. Deploys a new Cloud Phone instance on Oracle Cloud Free Tier
# 2. Waits for it to be ready
# 3. Runs the full test suite
# 4. Outputs results to log files
#
# Usage:
#   ./deploy-and-test.sh                    # Deploy new instance and test
#   ./deploy-and-test.sh --existing IP      # Test existing instance
#   ./deploy-and-test.sh --cleanup          # Delete test instance after

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Configuration
INSTANCE_NAME="${INSTANCE_NAME:-cloud-phone-test-$(date +%Y%m%d-%H%M%S)}"
EXISTING_IP=""
CLEANUP_AFTER=false
OCPUS="${OCPUS:-2}"
MEMORY_GB="${MEMORY_GB:-8}"
OS_VERSION="${OS_VERSION:-20.04}"

# OCI Config
COMPARTMENT_ID="${COMPARTMENT_ID:-}"
SUBNET_ID="${SUBNET_ID:-}"
AVAILABILITY_DOMAIN="${AVAILABILITY_DOMAIN:-}"
SSH_KEY_FILE="${SSH_KEY_FILE:-$HOME/.ssh/redroid_oci.pub}"
SSH_PRIVATE_KEY="${SSH_KEY_FILE%.pub}"

# Logging
LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/deployment-logs}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
DEPLOY_LOG="$LOG_DIR/deploy-$TIMESTAMP.log"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1" | tee -a "$DEPLOY_LOG"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$DEPLOY_LOG"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" | tee -a "$DEPLOY_LOG" >&2; }
log_header() { echo -e "${BLUE}========================================${NC}" | tee -a "$DEPLOY_LOG"; echo -e "${BLUE}  $1${NC}" | tee -a "$DEPLOY_LOG"; echo -e "${BLUE}========================================${NC}" | tee -a "$DEPLOY_LOG"; }

usage() {
    cat <<EOF
Deploy Cloud Phone and Run Tests

Usage: $0 [OPTIONS]

Options:
  --name NAME           Instance name (default: cloud-phone-test-TIMESTAMP)
  --existing IP         Test existing instance instead of deploying new
  --cleanup             Delete instance after testing
  --ocpus N             Number of OCPUs (default: 2)
  --memory N            Memory in GB (default: 8)
  --os-version VER      Ubuntu version (default: 20.04)
  --log-dir DIR         Log directory
  --help                Show this help

Environment Variables:
  COMPARTMENT_ID        OCI compartment OCID
  SUBNET_ID             OCI subnet OCID
  AVAILABILITY_DOMAIN   OCI availability domain
  SSH_KEY_FILE          SSH public key file

Examples:
  # Full deployment and test
  $0

  # Test existing instance
  $0 --existing 129.146.123.45

  # Deploy, test, then cleanup
  $0 --cleanup

EOF
    exit 0
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --name) INSTANCE_NAME="$2"; shift 2 ;;
        --existing) EXISTING_IP="$2"; shift 2 ;;
        --cleanup) CLEANUP_AFTER=true; shift ;;
        --ocpus) OCPUS="$2"; shift 2 ;;
        --memory) MEMORY_GB="$2"; shift 2 ;;
        --os-version) OS_VERSION="$2"; shift 2 ;;
        --log-dir) LOG_DIR="$2"; shift 2 ;;
        --help|-h) usage ;;
        *) log_error "Unknown option: $1"; usage ;;
    esac
done

# Create log directory
mkdir -p "$LOG_DIR"

# Initialize log
echo "Deploy and Test Log" > "$DEPLOY_LOG"
echo "===================" >> "$DEPLOY_LOG"
echo "Timestamp: $(date)" >> "$DEPLOY_LOG"
echo "" >> "$DEPLOY_LOG"

log_header "Cloud Phone Deploy and Test"
echo ""

# =============================================================================
# Validation
# =============================================================================

validate_prerequisites() {
    local errors=0
    
    if [[ -z "$EXISTING_IP" ]]; then
        # Need OCI CLI for deployment
        if ! command -v oci &>/dev/null; then
            log_error "OCI CLI not installed"
            errors=$((errors + 1))
        fi
        
        if [[ -z "$COMPARTMENT_ID" ]]; then
            log_error "COMPARTMENT_ID not set"
            errors=$((errors + 1))
        fi
        
        if [[ -z "$SUBNET_ID" ]]; then
            log_error "SUBNET_ID not set"
            errors=$((errors + 1))
        fi
        
        if [[ ! -f "$SSH_KEY_FILE" ]]; then
            log_error "SSH public key not found: $SSH_KEY_FILE"
            errors=$((errors + 1))
        fi
    else
        if [[ ! -f "$SSH_PRIVATE_KEY" ]]; then
            log_error "SSH private key not found: $SSH_PRIVATE_KEY"
            errors=$((errors + 1))
        fi
    fi
    
    return $errors
}

# =============================================================================
# Deployment
# =============================================================================

INSTANCE_OCID=""
PUBLIC_IP=""

deploy_instance() {
    log_header "Deploying Instance"
    
    log_info "Finding Ubuntu $OS_VERSION ARM image..."
    
    IMAGE_OCID=$(oci compute image list \
        --compartment-id "$COMPARTMENT_ID" \
        --operating-system "Canonical Ubuntu" \
        --operating-system-version "$OS_VERSION" \
        --shape "VM.Standard.A1.Flex" \
        --query 'data[0].id' \
        --raw-output 2>/dev/null)
    
    if [[ -z "$IMAGE_OCID" ]]; then
        log_error "Ubuntu $OS_VERSION ARM image not found"
        return 1
    fi
    
    log_info "Image: ${IMAGE_OCID:0:50}..."
    
    log_info "Creating instance: $INSTANCE_NAME"
    log_info "  Shape: VM.Standard.A1.Flex"
    log_info "  OCPUs: $OCPUS"
    log_info "  Memory: ${MEMORY_GB}GB"
    log_info "  OS: Ubuntu $OS_VERSION"
    
    INSTANCE_OCID=$(oci compute instance launch \
        --compartment-id "$COMPARTMENT_ID" \
        --availability-domain "$AVAILABILITY_DOMAIN" \
        --shape "VM.Standard.A1.Flex" \
        --shape-config "{\"ocpus\":$OCPUS,\"memoryInGBs\":$MEMORY_GB}" \
        --image-id "$IMAGE_OCID" \
        --subnet-id "$SUBNET_ID" \
        --display-name "$INSTANCE_NAME" \
        --ssh-authorized-keys-file "$SSH_KEY_FILE" \
        --assign-public-ip true \
        --wait-for-state RUNNING \
        --query 'data.id' \
        --raw-output 2>&1) || {
        log_error "Failed to create instance: $INSTANCE_OCID"
        return 1
    }
    
    log_info "Instance created: ${INSTANCE_OCID:0:50}..."
    
    # Get public IP
    sleep 5
    for i in {1..30}; do
        PUBLIC_IP=$(oci compute instance list-vnics \
            --instance-id "$INSTANCE_OCID" \
            --query 'data[0]."public-ip"' \
            --raw-output 2>/dev/null)
        [[ -n "$PUBLIC_IP" ]] && [[ "$PUBLIC_IP" != "null" ]] && break
        sleep 2
    done
    
    if [[ -z "$PUBLIC_IP" ]] || [[ "$PUBLIC_IP" == "null" ]]; then
        log_error "Could not get public IP"
        return 1
    fi
    
    log_info "Public IP: $PUBLIC_IP"
    echo "$PUBLIC_IP" > "$LOG_DIR/instance-ip.txt"
    echo "$INSTANCE_OCID" > "$LOG_DIR/instance-ocid.txt"
}

wait_for_ssh() {
    log_info "Waiting for SSH to be ready..."
    
    local SSH_CMD="ssh -i $SSH_PRIVATE_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=5"
    
    for i in {1..60}; do
        if $SSH_CMD ubuntu@$PUBLIC_IP 'echo ready' &>/dev/null; then
            log_info "SSH is ready"
            return 0
        fi
        echo -n "." >&2
        sleep 2
    done
    
    echo "" >&2
    log_error "SSH timeout"
    return 1
}

install_cloud_phone() {
    log_header "Installing Cloud Phone"
    
    local SSH_CMD="ssh -i $SSH_PRIVATE_KEY -o StrictHostKeyChecking=no"
    
    # Create deployment tarball
    log_info "Uploading project files..."
    
    local TARBALL=$(mktemp)
    cd "$PROJECT_ROOT"
    tar czf "$TARBALL" \
        --exclude='.git' \
        --exclude='*.pyc' \
        --exclude='__pycache__' \
        --exclude='test-logs' \
        --exclude='deployment-logs' \
        install-redroid.sh \
        scripts/ \
        api/ \
        systemd/ \
        config/ \
        tests/
    
    scp -i "$SSH_PRIVATE_KEY" -o StrictHostKeyChecking=no "$TARBALL" ubuntu@$PUBLIC_IP:/tmp/cloud-phone.tar.gz
    rm "$TARBALL"
    
    # Install
    log_info "Running installer..."
    
    $SSH_CMD ubuntu@$PUBLIC_IP << 'INSTALL_EOF'
set -e
cd /tmp
rm -rf cloud-phone
mkdir -p cloud-phone
tar xzf cloud-phone.tar.gz -C cloud-phone
cd cloud-phone

# Make scripts executable
chmod +x install-redroid.sh scripts/*.sh

# Run installer
sudo ./install-redroid.sh

# Copy API files
sudo mkdir -p /opt/cloud-phone-api
sudo cp api/agent_api.py /opt/cloud-phone-api/
sudo cp api/requirements.txt /opt/cloud-phone-api/

# Install API dependencies
sudo python3 -m venv /opt/cloud-phone-api/venv
sudo /opt/cloud-phone-api/venv/bin/pip install flask requests

# Create systemd service for agent API
sudo tee /etc/systemd/system/agent-api.service > /dev/null <<'SVCEOF'
[Unit]
Description=Cloud Phone Agent API
After=docker.service redroid-container.service
Wants=redroid-container.service

[Service]
Type=simple
WorkingDirectory=/opt/cloud-phone-api
Environment=ADB_HOST=127.0.0.1
Environment=ADB_PORT=5555
Environment=API_HOST=0.0.0.0
Environment=API_PORT=8080
Environment=LOG_DIR=/var/log/cloud-phone
ExecStart=/opt/cloud-phone-api/venv/bin/python agent_api.py
Restart=always
RestartSec=10

[Install]
WantedBy=redroid-cloud-phone.target
SVCEOF

# Reload and start
sudo systemctl daemon-reload
sudo systemctl enable agent-api

# Start everything
sudo systemctl start redroid-cloud-phone.target

echo "Waiting for services to start..."
sleep 30

# Start agent API
sudo systemctl start agent-api

echo "Installation complete!"
INSTALL_EOF
    
    log_info "Installation complete"
}

wait_for_ready() {
    log_info "Waiting for Cloud Phone to be ready..."
    
    local SSH_CMD="ssh -i $SSH_PRIVATE_KEY -o StrictHostKeyChecking=no"
    
    # Wait for Redroid container
    for i in {1..60}; do
        if $SSH_CMD ubuntu@$PUBLIC_IP 'sudo docker ps | grep -q redroid'; then
            log_info "Redroid container is running"
            break
        fi
        sleep 5
    done
    
    # Wait for API
    for i in {1..30}; do
        if $SSH_CMD ubuntu@$PUBLIC_IP 'curl -sf http://localhost:8080/health > /dev/null 2>&1'; then
            log_info "API is responding"
            return 0
        fi
        sleep 5
    done
    
    log_warn "API may not be fully ready, continuing anyway..."
    return 0
}

# =============================================================================
# Testing
# =============================================================================

run_tests() {
    log_header "Running Tests"
    
    # Setup SSH tunnel
    log_info "Setting up SSH tunnel..."
    
    ssh -i "$SSH_PRIVATE_KEY" -o StrictHostKeyChecking=no \
        -L 8080:localhost:8080 \
        -L 5900:localhost:5900 \
        ubuntu@$PUBLIC_IP -N &
    TUNNEL_PID=$!
    
    sleep 3
    
    if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
        log_error "SSH tunnel failed"
        return 1
    fi
    
    log_info "SSH tunnel established (PID: $TUNNEL_PID)"
    
    # Run test suite
    log_info "Running test suite..."
    
    cd "$PROJECT_ROOT"
    
    # Install test dependencies locally
    pip3 install -q requests 2>/dev/null || true
    
    # Run tests
    TEST_LOG="$LOG_DIR/test-results-$TIMESTAMP.log"
    TEST_JSON="$LOG_DIR/test-results-$TIMESTAMP.json"
    
    if python3 tests/test_agent_api.py \
        --api-url http://localhost:8080 \
        --log-file "$TEST_LOG" \
        --output-json "$TEST_JSON" \
        --verbose; then
        TEST_RESULT="PASSED"
        log_info "✓ All tests passed"
    else
        TEST_RESULT="FAILED"
        log_error "✗ Some tests failed"
    fi
    
    # Cleanup tunnel
    kill "$TUNNEL_PID" 2>/dev/null || true
    
    return $([ "$TEST_RESULT" == "PASSED" ] && echo 0 || echo 1)
}

# =============================================================================
# Cleanup
# =============================================================================

cleanup_instance() {
    if [[ -n "$INSTANCE_OCID" ]] && [[ "$CLEANUP_AFTER" == "true" ]]; then
        log_header "Cleanup"
        log_info "Terminating instance: $INSTANCE_NAME"
        
        oci compute instance terminate \
            --instance-id "$INSTANCE_OCID" \
            --preserve-boot-volume false \
            --force \
            --wait-for-state TERMINATED || log_warn "Cleanup may have failed"
        
        log_info "Instance terminated"
    fi
}

# =============================================================================
# Main
# =============================================================================

main() {
    # Validate
    if ! validate_prerequisites; then
        log_error "Prerequisites check failed"
        exit 1
    fi
    
    # Deploy or use existing
    if [[ -n "$EXISTING_IP" ]]; then
        PUBLIC_IP="$EXISTING_IP"
        log_info "Using existing instance: $PUBLIC_IP"
    else
        deploy_instance || exit 1
        wait_for_ssh || exit 1
        install_cloud_phone || exit 1
        wait_for_ready || exit 1
    fi
    
    # Run tests
    run_tests
    TEST_EXIT_CODE=$?
    
    # Cleanup if requested
    cleanup_instance
    
    # Summary
    log_header "Summary"
    
    echo ""
    echo "Instance: $INSTANCE_NAME"
    echo "IP: $PUBLIC_IP"
    if [[ -n "$INSTANCE_OCID" ]]; then
        echo "OCID: $INSTANCE_OCID"
    fi
    echo ""
    echo "Logs:"
    echo "  Deployment: $DEPLOY_LOG"
    echo "  Test Results: $LOG_DIR/test-results-$TIMESTAMP.json"
    echo ""
    
    if [[ -f "$LOG_DIR/test-results-$TIMESTAMP.json" ]]; then
        echo "Test Summary:"
        jq '.summary' "$LOG_DIR/test-results-$TIMESTAMP.json"
    fi
    
    echo ""
    
    if [[ "$CLEANUP_AFTER" != "true" ]] && [[ -z "$EXISTING_IP" ]]; then
        echo "Connect to instance:"
        echo "  SSH: ssh -i $SSH_PRIVATE_KEY ubuntu@$PUBLIC_IP"
        echo "  VNC: ssh -i $SSH_PRIVATE_KEY -L 5900:localhost:5900 ubuntu@$PUBLIC_IP -N"
        echo "  API: ssh -i $SSH_PRIVATE_KEY -L 8080:localhost:8080 ubuntu@$PUBLIC_IP -N"
        echo ""
    fi
    
    exit $TEST_EXIT_CODE
}

# Run
main
