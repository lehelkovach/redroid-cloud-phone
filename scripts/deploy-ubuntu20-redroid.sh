#!/bin/bash
# Deploy Redroid Cloud Phone on Ubuntu 20.04 (Kernel 5.x)
# This script creates an instance and deploys Redroid with full virtual device support
#
# Usage: ./deploy-ubuntu20-redroid.sh [instance-name]
# Example: ./deploy-ubuntu20-redroid.sh redroid-prod-1

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

INSTANCE_NAME="${1:-redroid-ubuntu20-$(date +%Y%m%d-%H%M%S)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Configuration - Update these for your OCI account
COMPARTMENT_ID="${COMPARTMENT_ID:-ocid1.tenancy.oc1..aaaaaaaak44wevthunqrdp6h6noor4o5t34a7jqejla6wd22v47admhyzoca}"
SUBNET_ID="${SUBNET_ID:-ocid1.subnet.oc1.phx.aaaaaaaalpdm6cgqxuairct2uzbn74p7u5x4dqqvfleqoxhjcy6pn6fzeh2q}"
SSH_KEY_FILE="${SSH_KEY_FILE:-$HOME/.ssh/waydroid_oci.pub}"
SSH_PRIVATE_KEY="${SSH_KEY_FILE%.pub}"
AVAILABILITY_DOMAIN="${AVAILABILITY_DOMAIN:-ABpi:PHX-AD-1}"

echo -e "${BLUE}=========================================="
echo "  Redroid Cloud Phone Deployment"
echo "  Ubuntu 20.04 (Kernel 5.x) + Virtual Devices"
echo "==========================================${NC}"
echo ""

# ============================================
# Step 0: Prerequisites
# ============================================
echo -e "${BLUE}[0/5] Checking prerequisites...${NC}"

if ! command -v oci &> /dev/null; then
    echo -e "${RED}Error: OCI CLI not installed${NC}"
    echo "Install: https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm"
    exit 1
fi
echo -e "${GREEN}✓${NC} OCI CLI found"

if [[ ! -f "$SSH_KEY_FILE" ]]; then
    echo -e "${RED}Error: SSH public key not found: $SSH_KEY_FILE${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} SSH key found"

if [[ ! -f "$SSH_PRIVATE_KEY" ]]; then
    echo -e "${RED}Error: SSH private key not found: $SSH_PRIVATE_KEY${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} SSH private key found"

# ============================================
# Step 1: Find Ubuntu 20.04 Image
# ============================================
echo ""
echo -e "${BLUE}[1/5] Finding Ubuntu 20.04 ARM image...${NC}"

UBUNTU_IMAGE=$(oci compute image list \
    --compartment-id "$COMPARTMENT_ID" \
    --operating-system "Canonical Ubuntu" \
    --operating-system-version "20.04" \
    --shape "VM.Standard.A1.Flex" \
    --query 'data[0].id' \
    --raw-output 2>/dev/null || echo "")

if [[ -z "$UBUNTU_IMAGE" ]]; then
    echo -e "${RED}Error: Ubuntu 20.04 ARM image not found${NC}"
    echo "Available images:"
    oci compute image list \
        --compartment-id "$COMPARTMENT_ID" \
        --operating-system "Canonical Ubuntu" \
        --shape "VM.Standard.A1.Flex" \
        --query 'data[*].[display-name,"operating-system-version",id]' \
        --output table 2>/dev/null || true
    exit 1
fi
echo -e "${GREEN}✓${NC} Found image: ${UBUNTU_IMAGE:0:40}..."

# ============================================
# Step 2: Create Instance
# ============================================
echo ""
echo -e "${BLUE}[2/5] Creating Oracle Cloud instance...${NC}"
echo "  Name: $INSTANCE_NAME"
echo "  Shape: VM.Standard.A1.Flex (2 OCPU, 8GB RAM)"
echo "  OS: Ubuntu 20.04 (Kernel 5.x)"
echo ""

INSTANCE_OCID=$(oci compute instance launch \
    --compartment-id "$COMPARTMENT_ID" \
    --availability-domain "$AVAILABILITY_DOMAIN" \
    --shape "VM.Standard.A1.Flex" \
    --shape-config '{"ocpus":2,"memoryInGBs":8}' \
    --image-id "$UBUNTU_IMAGE" \
    --subnet-id "$SUBNET_ID" \
    --display-name "$INSTANCE_NAME" \
    --ssh-authorized-keys-file "$SSH_KEY_FILE" \
    --assign-public-ip true \
    --wait-for-state RUNNING \
    --query 'data.id' \
    --raw-output 2>&1) || {
    echo -e "${RED}Error: Failed to create instance${NC}"
    echo "$INSTANCE_OCID"
    exit 1
}

echo -e "${GREEN}✓${NC} Instance created: ${INSTANCE_OCID:0:50}..."

# Get public IP
sleep 5
PUBLIC_IP=$(oci compute instance list-vnics \
    --instance-id "$INSTANCE_OCID" \
    --query 'data[0]."public-ip"' \
    --raw-output 2>/dev/null || echo "")

if [[ -z "$PUBLIC_IP" ]] || [[ "$PUBLIC_IP" == "null" ]]; then
    echo -e "${YELLOW}Waiting for public IP...${NC}"
    for i in {1..30}; do
        sleep 2
        PUBLIC_IP=$(oci compute instance list-vnics \
            --instance-id "$INSTANCE_OCID" \
            --query 'data[0]."public-ip"' \
            --raw-output 2>/dev/null || echo "")
        if [[ -n "$PUBLIC_IP" ]] && [[ "$PUBLIC_IP" != "null" ]]; then
            break
        fi
    done
fi

if [[ -z "$PUBLIC_IP" ]] || [[ "$PUBLIC_IP" == "null" ]]; then
    echo -e "${RED}Error: Could not get public IP${NC}"
    exit 1
fi

echo -e "${GREEN}✓${NC} Public IP: $PUBLIC_IP"

# ============================================
# Step 3: Wait for SSH
# ============================================
echo ""
echo -e "${BLUE}[3/5] Waiting for SSH to be ready...${NC}"

SSH_CMD="ssh -i $SSH_PRIVATE_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=5"

for i in {1..60}; do
    if $SSH_CMD ubuntu@$PUBLIC_IP 'echo ready' &>/dev/null; then
        echo -e "${GREEN}✓${NC} SSH is ready"
        break
    fi
    echo -n "."
    sleep 2
done
echo ""

# Verify kernel version
KERNEL_VERSION=$($SSH_CMD ubuntu@$PUBLIC_IP 'uname -r')
echo -e "${GREEN}✓${NC} Kernel version: $KERNEL_VERSION"

if [[ ! "$KERNEL_VERSION" =~ ^5\. ]]; then
    echo -e "${YELLOW}Warning: Expected kernel 5.x but got $KERNEL_VERSION${NC}"
fi

# ============================================
# Step 4: Deploy Redroid
# ============================================
echo ""
echo -e "${BLUE}[4/5] Deploying Redroid Cloud Phone...${NC}"

# Create deployment tarball
TEMP_DIR=$(mktemp -d)
TARBALL="$TEMP_DIR/redroid-cloud-phone.tar.gz"

cd "$PROJECT_ROOT"
tar czf "$TARBALL" \
    --exclude='.git' \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    install-redroid.sh \
    install.sh \
    scripts/ \
    api/ \
    systemd/ \
    config/

echo "  Uploading deployment package..."
scp -i "$SSH_PRIVATE_KEY" -o StrictHostKeyChecking=no "$TARBALL" ubuntu@$PUBLIC_IP:/tmp/

echo "  Running installer..."
$SSH_CMD ubuntu@$PUBLIC_IP << 'ENDSSH'
set -e
cd /tmp
rm -rf redroid-cloud-phone
mkdir -p redroid-cloud-phone
tar xzf redroid-cloud-phone.tar.gz -C redroid-cloud-phone
cd redroid-cloud-phone

# Run installer
sudo ./install-redroid.sh

# Start services
sudo systemctl start redroid-cloud-phone.target

# Wait for Redroid to boot
echo "Waiting for Redroid to boot..."
sleep 20

# Verify
sudo docker ps | grep redroid || echo "Warning: Redroid container may not be running"
ENDSSH

rm -rf "$TEMP_DIR"
echo -e "${GREEN}✓${NC} Deployment complete"

# ============================================
# Step 5: Verify Installation
# ============================================
echo ""
echo -e "${BLUE}[5/5] Verifying installation...${NC}"

# Check container
CONTAINER_STATUS=$($SSH_CMD ubuntu@$PUBLIC_IP 'sudo docker ps --format "{{.Names}}:{{.Status}}" | grep redroid || echo "not running"')
if echo "$CONTAINER_STATUS" | grep -q "Up"; then
    echo -e "${GREEN}✓${NC} Redroid container: $CONTAINER_STATUS"
else
    echo -e "${RED}✗${NC} Redroid container: $CONTAINER_STATUS"
fi

# Check virtual devices
V4L2_STATUS=$($SSH_CMD ubuntu@$PUBLIC_IP 'lsmod | grep v4l2loopback && echo "loaded" || echo "not loaded"')
if echo "$V4L2_STATUS" | grep -q "loaded"; then
    echo -e "${GREEN}✓${NC} v4l2loopback module loaded"
else
    echo -e "${YELLOW}○${NC} v4l2loopback not loaded (may need reboot)"
fi

VIDEO42_EXISTS=$($SSH_CMD ubuntu@$PUBLIC_IP '[ -e /dev/video42 ] && echo "exists" || echo "not found"')
if [[ "$VIDEO42_EXISTS" == "exists" ]]; then
    echo -e "${GREEN}✓${NC} /dev/video42 virtual camera exists"
else
    echo -e "${YELLOW}○${NC} /dev/video42 not found (reboot may be needed)"
fi

# Check ports
ADB_PORT=$($SSH_CMD ubuntu@$PUBLIC_IP 'ss -tlnp | grep :5555 && echo "listening" || echo "not listening"')
if echo "$ADB_PORT" | grep -q "listening"; then
    echo -e "${GREEN}✓${NC} ADB port 5555 listening"
else
    echo -e "${YELLOW}○${NC} ADB port 5555 not yet listening"
fi

VNC_PORT=$($SSH_CMD ubuntu@$PUBLIC_IP 'ss -tlnp | grep :5900 && echo "listening" || echo "not listening"')
if echo "$VNC_PORT" | grep -q "listening"; then
    echo -e "${GREEN}✓${NC} VNC port 5900 listening"
else
    echo -e "${YELLOW}○${NC} VNC port 5900 not yet listening"
fi

# ============================================
# Summary
# ============================================
echo ""
echo -e "${BLUE}=========================================="
echo "  Deployment Complete!"
echo "==========================================${NC}"
echo ""
echo "Instance Details:"
echo "  Name: $INSTANCE_NAME"
echo "  OCID: $INSTANCE_OCID"
echo "  IP: $PUBLIC_IP"
echo "  Kernel: $KERNEL_VERSION"
echo ""
echo "Connect via VNC:"
echo "  ssh -i $SSH_PRIVATE_KEY -L 5900:localhost:5900 ubuntu@$PUBLIC_IP -N"
echo "  vncviewer localhost:5900  # password: redroid"
echo ""
echo "Connect via ADB:"
echo "  adb connect $PUBLIC_IP:5555"
echo ""
echo "Health check:"
echo "  ssh -i $SSH_PRIVATE_KEY ubuntu@$PUBLIC_IP 'sudo /opt/waydroid-scripts/health-check.sh'"
echo ""
echo "Run full test:"
echo "  ./scripts/test-redroid-full.sh $PUBLIC_IP"
echo ""

# Save instance info
cat > /tmp/redroid-instance-info.txt << EOF
INSTANCE_NAME=$INSTANCE_NAME
INSTANCE_OCID=$INSTANCE_OCID
PUBLIC_IP=$PUBLIC_IP
KERNEL=$KERNEL_VERSION
SSH_KEY=$SSH_PRIVATE_KEY
EOF
echo -e "${GREEN}Instance info saved to /tmp/redroid-instance-info.txt${NC}"
