#!/bin/bash
# create-instance.sh
# Creates a new OCI instance for waydroid deployment
#
# Usage: ./create-instance.sh [instance-name]
# Example: ./create-instance.sh waydroid-test-1

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

INSTANCE_NAME="${1:-waydroid-test-$(date +%s)}"

# Load configuration from launch-fleet.sh (but skip validation)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Read config values without sourcing (to avoid image validation)
if [[ -f "$SCRIPT_DIR/launch-fleet.sh" ]]; then
    COMPARTMENT_ID=$(grep "^COMPARTMENT_ID=" "$SCRIPT_DIR/launch-fleet.sh" | head -1 | cut -d'"' -f2 || echo "")
    SUBNET_ID=$(grep "^SUBNET_ID=" "$SCRIPT_DIR/launch-fleet.sh" | head -1 | cut -d'"' -f2 || echo "")
    SSH_KEY_FILE=$(grep "^SSH_KEY_FILE=" "$SCRIPT_DIR/launch-fleet.sh" | head -1 | cut -d'"' -f2 || echo "")
    SHAPE=$(grep "^SHAPE=" "$SCRIPT_DIR/launch-fleet.sh" | head -1 | cut -d'"' -f2 || echo "")
    OCPUS=$(grep "^OCPUS=" "$SCRIPT_DIR/launch-fleet.sh" | head -1 | cut -d'=' -f2 || echo "")
    MEMORY_GB=$(grep "^MEMORY_GB=" "$SCRIPT_DIR/launch-fleet.sh" | head -1 | cut -d'=' -f2 || echo "")
    # Parse availability domains array
    AD_START=$(grep -n "^AVAILABILITY_DOMAINS=(" "$SCRIPT_DIR/launch-fleet.sh" | cut -d':' -f1)
    if [[ -n "$AD_START" ]]; then
        AD_LINES=$(sed -n "${AD_START},/^)$/p" "$SCRIPT_DIR/launch-fleet.sh" | grep -o '"[^"]*"' | tr -d '"')
        readarray -t AVAILABILITY_DOMAINS <<< "$AD_LINES"
    fi
fi

# Override with defaults if not set
COMPARTMENT_ID="${COMPARTMENT_ID:-ocid1.tenancy.oc1..aaaaaaaak44wevthunqrdp6h6noor4o5t34a7jqejla6wd22v47admhyzoca}"
SUBNET_ID="${SUBNET_ID:-ocid1.subnet.oc1.phx.aaaaaaaalpdm6cgqxuairct2uzbn74p7u5x4dqqvfleqoxhjcy6pn6fzeh2q}"
# Expand ${HOME} if present
if [[ "$SSH_KEY_FILE" == *'${HOME}'* ]]; then
    SSH_KEY_FILE="${HOME}/.ssh/waydroid_oci.pub"
fi
SSH_KEY_FILE="${SSH_KEY_FILE:-${HOME}/.ssh/waydroid_oci.pub}"
SHAPE="${SHAPE:-VM.Standard.A1.Flex}"
OCPUS="${OCPUS:-2}"
MEMORY_GB="${MEMORY_GB:-8}"
AVAILABILITY_DOMAINS=("${AVAILABILITY_DOMAINS[@]:-ABpi:PHX-AD-1 ABpi:PHX-AD-2 ABpi:PHX-AD-3}")

# Check prerequisites
if ! command -v oci &> /dev/null; then
    echo -e "${RED}Error: OCI CLI is not installed.${NC}"
    exit 1
fi

if [[ ! -f "$SSH_KEY_FILE" ]]; then
    echo -e "${RED}Error: SSH key file not found: $SSH_KEY_FILE${NC}"
    exit 1
fi

if [[ -z "$SUBNET_ID" ]] || [[ "$SUBNET_ID" == *"xxx"* ]]; then
    echo -e "${YELLOW}Warning: SUBNET_ID not configured.${NC}"
    echo "Please set SUBNET_ID in launch-fleet.sh or provide it:"
    echo "  export SUBNET_ID=\"ocid1.subnet.oc1.phx.xxx\""
    echo ""
    echo "Finding available subnets..."
    oci network subnet list --compartment-id "$COMPARTMENT_ID" --query 'data[*].[display-name,id,"lifecycle-state"]' --output table 2>/dev/null || true
    exit 1
fi

# Get Ubuntu 22.04 ARM image
echo -e "${BLUE}Finding Ubuntu 22.04 ARM image...${NC}"
UBUNTU_IMAGE=$(oci compute image list \
    --compartment-id "$COMPARTMENT_ID" \
    --operating-system "Canonical Ubuntu" \
    --operating-system-version "22.04" \
    --shape "VM.Standard.A1.Flex" \
    --query 'data[0].id' \
    --raw-output 2>/dev/null || echo "")

if [[ -z "$UBUNTU_IMAGE" ]]; then
    echo -e "${YELLOW}Ubuntu 22.04 not found, trying 24.04...${NC}"
    UBUNTU_IMAGE=$(oci compute image list \
        --compartment-id "$COMPARTMENT_ID" \
        --operating-system "Canonical Ubuntu" \
        --operating-system-version "24.04" \
        --shape "VM.Standard.A1.Flex" \
        --query 'data[0].id' \
        --raw-output 2>/dev/null || echo "")
fi

if [[ -z "$UBUNTU_IMAGE" ]]; then
    echo -e "${RED}Error: Could not find Ubuntu ARM image.${NC}"
    echo "Please find an image manually:"
    echo "  oci compute image list --compartment-id $COMPARTMENT_ID --operating-system \"Canonical Ubuntu\""
    exit 1
fi

echo -e "${GREEN}Using image: $UBUNTU_IMAGE${NC}"

# Select availability domain (round-robin or first available)
AD="${AVAILABILITY_DOMAINS[0]}"
if [[ ${#AVAILABILITY_DOMAINS[@]} -gt 1 ]]; then
    # Try to find an AD with capacity
    for ad in "${AVAILABILITY_DOMAINS[@]}"; do
        echo -e "${BLUE}Trying availability domain: $ad${NC}"
        AD="$ad"
        break
    done
fi

echo ""
echo -e "${BLUE}=========================================="
echo "Creating OCI Instance"
echo "==========================================${NC}"
echo "Name: $INSTANCE_NAME"
echo "Shape: $SHAPE ($OCPUS OCPU, ${MEMORY_GB}GB RAM)"
echo "Availability Domain: $AD"
echo "Subnet: $SUBNET_ID"
echo ""

# Create instance
echo -e "${BLUE}Launching instance...${NC}"
INSTANCE_OCID=$(oci compute instance launch \
    --compartment-id "$COMPARTMENT_ID" \
    --availability-domain "$AD" \
    --shape "$SHAPE" \
    --shape-config "{\"ocpus\":$OCPUS,\"memoryInGBs\":$MEMORY_GB}" \
    --image-id "$UBUNTU_IMAGE" \
    --subnet-id "$SUBNET_ID" \
    --display-name "$INSTANCE_NAME" \
    --ssh-authorized-keys-file "$SSH_KEY_FILE" \
    --assign-public-ip true \
    --wait-for-state RUNNING \
    --query 'data.id' \
    --raw-output)

if [[ -z "$INSTANCE_OCID" ]]; then
    echo -e "${RED}Error: Failed to create instance${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Instance created: $INSTANCE_OCID${NC}"

# Get public IP
echo -e "${BLUE}Getting public IP...${NC}"
sleep 5  # Wait for VNIC attachment
PUBLIC_IP=$(oci compute instance list-vnics \
    --instance-id "$INSTANCE_OCID" \
    --query 'data[0]."public-ip"' \
    --raw-output 2>/dev/null || echo "")

if [[ -z "$PUBLIC_IP" ]] || [[ "$PUBLIC_IP" == "null" ]]; then
    echo -e "${YELLOW}Warning: Could not get public IP immediately.${NC}"
    echo "Try: oci compute instance list-vnics --instance-id $INSTANCE_OCID"
else
    echo -e "${GREEN}✓ Public IP: $PUBLIC_IP${NC}"
fi

echo ""
echo -e "${BLUE}=========================================="
echo "Instance Created Successfully"
echo "==========================================${NC}"
echo ""
echo "Instance OCID: $INSTANCE_OCID"
echo "Public IP: ${PUBLIC_IP:-N/A}"
echo ""
echo "Wait 30-60 seconds for SSH to be ready, then:"
echo "  ssh -i ${SSH_KEY_FILE%.pub} ubuntu@${PUBLIC_IP:-YOUR_IP}"
echo ""
echo "To deploy waydroid:"
echo "  ./scripts/deploy-to-instance.sh ${PUBLIC_IP:-YOUR_IP}"
echo ""

# Save instance info
echo "$INSTANCE_OCID|$PUBLIC_IP|$INSTANCE_NAME" > /tmp/waydroid-instance-info.txt
echo -e "${GREEN}Instance info saved to /tmp/waydroid-instance-info.txt${NC}"

