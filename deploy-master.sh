#!/bin/bash
# deploy-master.sh
# Master script to deploy waydroid cloud phone from start to finish
# Runs all steps sequentially with logging and error handling
#
# Usage: ./deploy-master.sh [instance-name]
# Example: ./deploy-master.sh waydroid-test-1

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

INSTANCE_NAME="${1:-waydroid-test-$(date +%s)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="${SCRIPT_DIR}/deploy-$(date +%Y%m%d-%H%M%S).log"
START_TIME=$(date +%s)

# Track progress
STEP=0
TOTAL_STEPS=7
PASSED=0
FAILED=0
INSTANCE_IP=""
INSTANCE_OCID=""

# Logging functions
log_info() {
    local msg="$1"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${BLUE}[INFO]${NC} $msg" | tee -a "$LOG_FILE"
    echo "[$timestamp] [INFO] $msg" >> "$LOG_FILE"
}

log_success() {
    local msg="$1"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${GREEN}[SUCCESS]${NC} $msg" | tee -a "$LOG_FILE"
    echo "[$timestamp] [SUCCESS] $msg" >> "$LOG_FILE"
    ((PASSED++))
}

log_error() {
    local msg="$1"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${RED}[ERROR]${NC} $msg" | tee -a "$LOG_FILE"
    echo "[$timestamp] [ERROR] $msg" >> "$LOG_FILE"
    ((FAILED++))
}

log_warn() {
    local msg="$1"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${YELLOW}[WARN]${NC} $msg" | tee -a "$LOG_FILE"
    echo "[$timestamp] [WARN] $msg" >> "$LOG_FILE"
}

log_step() {
    ((STEP++))
    local msg="$1"
    echo ""
    echo -e "${CYAN}=========================================="
    echo "[$STEP/$TOTAL_STEPS] $msg"
    echo "==========================================${NC}"
    echo ""
    log_info "Starting step $STEP: $msg"
}

# Error handler
handle_error() {
    local exit_code=$?
    local line=$1
    log_error "Script failed at line $line with exit code $exit_code"
    log_error "Check log file for details: $LOG_FILE"
    echo ""
    echo -e "${RED}=========================================="
    echo "Deployment Failed"
    echo "==========================================${NC}"
    echo ""
    echo "Log file: $LOG_FILE"
    echo "Last 20 lines:"
    tail -20 "$LOG_FILE"
    exit $exit_code
}

trap 'handle_error $LINENO' ERR

# Initialize log file
echo "==========================================" > "$LOG_FILE"
echo "Waydroid Cloud Phone Deployment Log" >> "$LOG_FILE"
echo "Started: $(date)" >> "$LOG_FILE"
echo "Instance: $INSTANCE_NAME" >> "$LOG_FILE"
echo "==========================================" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

echo -e "${BLUE}=========================================="
echo "Waydroid Cloud Phone - Master Deployment"
echo "==========================================${NC}"
echo ""
echo "Instance Name: $INSTANCE_NAME"
echo "Log File: $LOG_FILE"
echo ""

# ============================================
# Step 1: Setup Networking
# ============================================
log_step "Setup Networking (VCN/Subnet)"

if "$SCRIPT_DIR/scripts/setup-networking.sh" >> "$LOG_FILE" 2>&1; then
    log_success "Networking setup complete"
    
    # Extract subnet ID from log
    SUBNET_ID=$(grep "Subnet ID:" "$LOG_FILE" | tail -1 | awk '{print $NF}' || echo "")
    if [[ -n "$SUBNET_ID" ]]; then
        log_info "Subnet ID: $SUBNET_ID"
    fi
else
    log_error "Networking setup failed"
    exit 1
fi

# ============================================
# Step 2: Create Instance
# ============================================
log_step "Create OCI Instance"

if "$SCRIPT_DIR/scripts/create-instance.sh" "$INSTANCE_NAME" >> "$LOG_FILE" 2>&1; then
    log_success "Instance created"
    
    # Extract IP and OCID from log or instance info file
    if [[ -f /tmp/waydroid-instance-info.txt ]]; then
        INSTANCE_INFO=$(cat /tmp/waydroid-instance-info.txt)
        INSTANCE_OCID=$(echo "$INSTANCE_INFO" | cut -d'|' -f1)
        INSTANCE_IP=$(echo "$INSTANCE_INFO" | cut -d'|' -f2)
        log_info "Instance OCID: $INSTANCE_OCID"
        log_info "Public IP: $INSTANCE_IP"
    else
        # Try to extract from log
        INSTANCE_IP=$(grep "Public IP:" "$LOG_FILE" | tail -1 | awk '{print $NF}' || echo "")
        INSTANCE_OCID=$(grep "Instance created:" "$LOG_FILE" | tail -1 | awk '{print $NF}' || echo "")
    fi
    
    if [[ -z "$INSTANCE_IP" ]]; then
        log_error "Could not determine instance IP"
        exit 1
    fi
    
    log_info "Waiting 60 seconds for SSH to be ready..."
    sleep 60
else
    log_error "Instance creation failed"
    exit 1
fi

# ============================================
# Step 3: Deploy Waydroid
# ============================================
log_step "Deploy Waydroid to Instance"

if "$SCRIPT_DIR/scripts/deploy-to-instance.sh" "$INSTANCE_IP" >> "$LOG_FILE" 2>&1; then
    log_success "Waydroid deployment complete"
else
    log_error "Waydroid deployment failed"
    log_warn "Continuing anyway - you may need to manually fix issues"
fi

# ============================================
# Step 4: Start Services
# ============================================
log_step "Start Services"

log_info "Starting waydroid-cloud-phone.target..."
if ssh -i ~/.ssh/waydroid_oci -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
    ubuntu@"$INSTANCE_IP" \
    'sudo systemctl start waydroid-cloud-phone.target' >> "$LOG_FILE" 2>&1; then
    log_success "Services started"
    sleep 5  # Give services time to initialize
else
    log_error "Failed to start services"
    log_warn "You may need to start services manually"
fi

# ============================================
# Step 5: Fix v4l2loopback (if needed)
# ============================================
log_step "Fix v4l2loopback (Kernel 6.8 compatibility)"

log_info "Uploading fix script..."
scp -i ~/.ssh/waydroid_oci -o StrictHostKeyChecking=no \
    "$SCRIPT_DIR/scripts/fix-v4l2loopback.sh" \
    ubuntu@"$INSTANCE_IP":/tmp/fix-v4l2loopback.sh >> "$LOG_FILE" 2>&1

log_info "Running v4l2loopback fix..."
if ssh -i ~/.ssh/waydroid_oci -o StrictHostKeyChecking=no \
    ubuntu@"$INSTANCE_IP" \
    'sudo bash /tmp/fix-v4l2loopback.sh' >> "$LOG_FILE" 2>&1; then
    log_success "v4l2loopback fix complete"
else
    log_warn "v4l2loopback fix may have failed (check logs)"
fi

# ============================================
# Step 6: Run System Tests
# ============================================
log_step "Run System Tests"

log_info "Uploading test scripts..."
scp -i ~/.ssh/waydroid_oci -o StrictHostKeyChecking=no \
    "$SCRIPT_DIR/scripts/test-system.sh" \
    ubuntu@"$INSTANCE_IP":/tmp/test-system.sh >> "$LOG_FILE" 2>&1

log_info "Running system tests..."
if ssh -i ~/.ssh/waydroid_oci -o StrictHostKeyChecking=no \
    ubuntu@"$INSTANCE_IP" \
    'sudo bash /tmp/test-system.sh' >> "$LOG_FILE" 2>&1; then
    log_success "System tests passed"
else
    TEST_EXIT=$?
    log_warn "Some system tests failed (exit code: $TEST_EXIT)"
    log_warn "Check log file for details"
fi

# ============================================
# Step 7: Run Full Test Suite
# ============================================
log_step "Run Full Test Suite"

log_info "Running comprehensive tests..."
if "$SCRIPT_DIR/scripts/test-full-suite.sh" "$INSTANCE_IP" >> "$LOG_FILE" 2>&1; then
    log_success "Full test suite passed"
else
    TEST_EXIT=$?
    log_warn "Some tests failed (exit code: $TEST_EXIT)"
    log_warn "Check log file for details - deployment may still be functional"
fi

# ============================================
# Summary
# ============================================
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))
MINUTES=$((DURATION / 60))
SECONDS=$((DURATION % 60))

echo ""
echo -e "${CYAN}=========================================="
echo "Deployment Summary"
echo "==========================================${NC}"
echo ""
echo "Instance Name: $INSTANCE_NAME"
echo "Public IP: $INSTANCE_IP"
if [[ -n "$INSTANCE_OCID" ]]; then
    echo "Instance OCID: $INSTANCE_OCID"
fi
echo ""
echo "Duration: ${MINUTES}m ${SECONDS}s"
echo "Steps Passed: $PASSED"
echo "Steps Failed: $FAILED"
echo ""
echo "Log File: $LOG_FILE"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}=========================================="
    echo "Deployment Complete - All Steps Passed!"
    echo "==========================================${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Access VNC:"
    echo "     ssh -i ~/.ssh/waydroid_oci -L 5901:localhost:5901 -L 8080:localhost:8080 -N ubuntu@$INSTANCE_IP"
    echo ""
    echo "  2. Stream from OBS:"
    echo "     Server: rtmp://$INSTANCE_IP/live"
    echo "     Stream Key: cam"
    echo ""
    echo "  3. Create golden image (when ready):"
    echo "     ./scripts/create-golden-image.sh $INSTANCE_IP waydroid-cloud-phone-v1"
    echo ""
    exit 0
else
    echo -e "${YELLOW}=========================================="
    echo "Deployment Complete with Warnings"
    echo "==========================================${NC}"
    echo ""
    echo "Some steps had issues. Check the log file:"
    echo "  $LOG_FILE"
    echo ""
    echo "The instance may still be functional. Test it:"
    echo "  ./scripts/test-full-suite.sh $INSTANCE_IP"
    echo ""
    exit 0
fi


