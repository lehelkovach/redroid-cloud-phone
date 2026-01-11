#!/bin/bash
# deploy-to-instance.sh
# Deploys waydroid to a remote OCI instance
#
# Usage: ./deploy-to-instance.sh <PUBLIC_IP> [SSH_KEY]
# Example: ./deploy-to-instance.sh 123.45.67.89 ~/.ssh/waydroid_oci

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <PUBLIC_IP> [SSH_KEY]"
    echo "Example: $0 123.45.67.89 ~/.ssh/waydroid_oci"
    exit 1
fi

PUBLIC_IP="$1"
SSH_KEY="${2:-${HOME}/.ssh/waydroid_oci}"
SSH_USER="ubuntu"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -f "$SSH_KEY" ]]; then
    echo -e "${RED}Error: SSH key not found: $SSH_KEY${NC}"
    exit 1
fi

echo -e "${BLUE}=========================================="
echo "Deploying Waydroid to Instance"
echo "==========================================${NC}"
echo "IP: $PUBLIC_IP"
echo "User: $SSH_USER"
echo ""

# Test SSH connection
echo -e "${BLUE}[1/6] Testing SSH connection...${NC}"
if ! ssh -i "$SSH_KEY" -o ConnectTimeout=10 -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" "echo 'SSH connection successful'" 2>/dev/null; then
    echo -e "${YELLOW}SSH not ready yet, waiting 30 seconds...${NC}"
    sleep 30
    if ! ssh -i "$SSH_KEY" -o ConnectTimeout=10 -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" "echo 'SSH connection successful'" 2>/dev/null; then
        echo -e "${RED}Error: Cannot connect to instance.${NC}"
        echo "Please verify:"
        echo "  1. Instance is running"
        echo "  2. Security list allows SSH (port 22)"
        echo "  3. Public IP is correct: $PUBLIC_IP"
        exit 1
    fi
fi
echo -e "${GREEN}✓ SSH connection successful${NC}"

# Create tarball of project
echo -e "${BLUE}[2/6] Creating deployment package...${NC}"
TEMP_DIR=$(mktemp -d)
TARBALL="$TEMP_DIR/waydroid-cloud-phone.tar.gz"
cd "$PROJECT_ROOT"
tar czf "$TARBALL" \
    --exclude='.git' \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.DS_Store' \
    --exclude='*.swp' \
    --exclude='*.swo' \
    install.sh \
    scripts/ \
    api/ \
    systemd/ \
    config/ \
    README.md \
    DEPLOYMENT.md \
    WORKFLOW.md \
    ARCHITECTURE.md \
    WORKFLOW.md \
    ARCHITECTURE.md

echo -e "${GREEN}✓ Package created: $(du -h "$TARBALL" | cut -f1)${NC}"

# Upload tarball
echo -e "${BLUE}[3/6] Uploading to instance...${NC}"
scp -i "$SSH_KEY" -o StrictHostKeyChecking=no "$TARBALL" "$SSH_USER@$PUBLIC_IP:/tmp/waydroid-cloud-phone.tar.gz"
echo -e "${GREEN}✓ Upload complete${NC}"

# Extract and install
echo -e "${BLUE}[4/6] Extracting and installing on instance...${NC}"
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" << 'ENDSSH'
set -e
cd /tmp
rm -rf waydroid-cloud-phone
tar xzf waydroid-cloud-phone.tar.gz
# Check if extraction created a subdirectory or extracted directly
if [ -d waydroid-cloud-phone ]; then
    cd waydroid-cloud-phone
elif [ -f install.sh ]; then
    # Files extracted directly to /tmp
    cd /tmp
else
    echo "Error: Could not find waydroid-cloud-phone directory or install.sh"
    ls -la /tmp/
    exit 1
fi
echo "Running installer..."
sudo ./install.sh
ENDSSH

echo -e "${GREEN}✓ Installation complete${NC}"

# Reboot
echo -e "${BLUE}[5/6] Rebooting instance (required for kernel modules)...${NC}"
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" "sudo reboot" || true
echo -e "${YELLOW}Instance is rebooting, waiting 60 seconds...${NC}"
sleep 60

# Wait for SSH to come back
echo -e "${BLUE}Waiting for instance to come back online...${NC}"
for i in {1..30}; do
    if ssh -i "$SSH_KEY" -o ConnectTimeout=5 -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" "echo 'ready'" &>/dev/null; then
        echo -e "${GREEN}✓ Instance is back online${NC}"
        break
    fi
    echo -n "."
    sleep 2
done
echo ""

# Fix v4l2loopback if needed
echo -e "${BLUE}[6/7] Fixing v4l2loopback (if needed)...${NC}"
scp -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SCRIPT_DIR/../scripts/fix-v4l2loopback.sh" "$SSH_USER@$PUBLIC_IP:/tmp/fix-v4l2loopback.sh"
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" "sudo bash /tmp/fix-v4l2loopback.sh" || echo -e "${YELLOW}Note: v4l2loopback fix may have failed, but continuing...${NC}"

# Initialize waydroid
echo -e "${BLUE}[7/7] Initializing Waydroid...${NC}"
echo -e "${YELLOW}Note: This will download ~1GB and take 5-10 minutes${NC}"
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" << 'ENDSSH'
sudo /opt/waydroid-scripts/init-waydroid.sh <<< "1"
ENDSSH

echo ""
echo -e "${BLUE}=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Start services:"
echo "     ssh -i $SSH_KEY $SSH_USER@$PUBLIC_IP 'sudo systemctl start waydroid-cloud-phone.target'"
echo ""
echo "  2. Run comprehensive tests:"
echo "     ./scripts/test-full-suite.sh $PUBLIC_IP"
echo ""
echo "     This will test:"
echo "     - Installation and services"
echo "     - API automation commands"
echo "     - RTMP streaming (start/stop/restart)"
echo "     - Service recovery (restart tests)"
echo "     - Network connectivity"
echo ""
echo "  3. Quick health check:"
echo "     ssh -i $SSH_KEY $SSH_USER@$PUBLIC_IP 'sudo /opt/waydroid-scripts/health-check.sh'"
echo ""
echo "  4. Create golden image (after tests pass):"
echo "     ./scripts/create-golden-image.sh $PUBLIC_IP waydroid-cloud-phone-v1"
echo ""

# Cleanup
rm -f "$TARBALL"
rmdir "$TEMP_DIR" 2>/dev/null || true

