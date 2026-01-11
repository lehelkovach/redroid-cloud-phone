#!/bin/bash
# create-golden-image.sh
# Creates a golden image from a running waydroid instance
#
# Usage: ./create-golden-image.sh <PUBLIC_IP> <IMAGE_NAME> [SSH_KEY]
# Example: ./create-golden-image.sh 123.45.67.89 waydroid-cloud-phone-v1

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <PUBLIC_IP> <IMAGE_NAME> [SSH_KEY]"
    echo "Example: $0 123.45.67.89 waydroid-cloud-phone-v1"
    exit 1
fi

PUBLIC_IP="$1"
IMAGE_NAME="$2"
SSH_KEY="${3:-${HOME}/.ssh/waydroid_oci}"
SSH_USER="ubuntu"

# Load configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/launch-fleet.sh" 2>/dev/null || true
COMPARTMENT_ID="${COMPARTMENT_ID:-ocid1.tenancy.oc1..aaaaaaaak44wevthunqrdp6h6noor4o5t34a7jqejla6wd22v47admhyzoca}"

# Check prerequisites
if ! command -v oci &> /dev/null; then
    echo -e "${RED}Error: OCI CLI is not installed.${NC}"
    exit 1
fi

echo -e "${BLUE}=========================================="
echo "Creating Golden Image"
echo "==========================================${NC}"
echo "Instance IP: $PUBLIC_IP"
echo "Image Name: $IMAGE_NAME"
echo ""

# Get instance OCID from IP
echo -e "${BLUE}[1/5] Finding instance OCID...${NC}"
INSTANCE_OCID=$(oci compute instance list \
    --compartment-id "$COMPARTMENT_ID" \
    --query "data[?\"lifecycle-state\"=='RUNNING'].{id:id,\"public-ip\":\"vnics[0].\"public-ip\"}" \
    --output json 2>/dev/null | \
    jq -r ".[] | select(.\"public-ip\" == \"$PUBLIC_IP\") | .id" | head -1)

if [[ -z "$INSTANCE_OCID" ]]; then
    echo -e "${YELLOW}Could not find instance by IP, trying alternative method...${NC}"
    # Try to find by display name or list all
    INSTANCE_OCID=$(oci compute instance list \
        --compartment-id "$COMPARTMENT_ID" \
        --lifecycle-state RUNNING \
        --query 'data[0].id' \
        --raw-output 2>/dev/null || echo "")
fi

if [[ -z "$INSTANCE_OCID" ]]; then
    echo -e "${RED}Error: Could not find instance.${NC}"
    echo "Please provide the instance OCID manually:"
    echo "  export INSTANCE_OCID=\"ocid1.instance.oc1.phx.xxx\""
    echo "Or find it:"
    echo "  oci compute instance list --compartment-id $COMPARTMENT_ID"
    exit 1
fi

echo -e "${GREEN}✓ Found instance: $INSTANCE_OCID${NC}"

# Prepare instance
echo -e "${BLUE}[2/5] Preparing instance for imaging...${NC}"
echo -e "${YELLOW}This will stop services and clean up the instance${NC}"
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" "sudo /opt/waydroid-scripts/prepare-golden-image.sh"

echo -e "${GREEN}✓ Instance prepared${NC}"

# Shutdown instance
echo -e "${BLUE}[3/5] Shutting down instance...${NC}"
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" "sudo shutdown -h now" || true

# Wait for instance to stop
echo -e "${YELLOW}Waiting for instance to stop (this may take 1-2 minutes)...${NC}"
oci compute instance wait \
    --instance-id "$INSTANCE_OCID" \
    --wait-for-state STOPPED \
    --max-wait-seconds 300 2>/dev/null || {
    echo -e "${YELLOW}Instance may still be stopping. Proceeding anyway...${NC}"
}

echo -e "${GREEN}✓ Instance stopped${NC}"

# Create custom image
echo -e "${BLUE}[4/5] Creating custom image...${NC}"
echo -e "${YELLOW}This will take 10-20 minutes...${NC}"

IMAGE_OCID=$(oci compute image create \
    --compartment-id "$COMPARTMENT_ID" \
    --instance-id "$INSTANCE_OCID" \
    --display-name "$IMAGE_NAME" \
    --query 'data.id' \
    --raw-output)

if [[ -z "$IMAGE_OCID" ]]; then
    echo -e "${RED}Error: Failed to create image${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Image creation started: $IMAGE_OCID${NC}"

# Wait for image to be available
echo -e "${BLUE}[5/5] Waiting for image to be available...${NC}"
echo -e "${YELLOW}This may take 10-20 minutes. You can check status with:${NC}"
echo "  oci compute image get --image-id $IMAGE_OCID --query 'data.\"lifecycle-state\"' --raw-output"

oci compute image wait \
    --image-id "$IMAGE_OCID" \
    --wait-for-state AVAILABLE \
    --max-wait-seconds 1800 2>/dev/null || {
    echo -e "${YELLOW}Image is still being created. Check status manually.${NC}"
}

# Check final status
IMAGE_STATE=$(oci compute image get \
    --image-id "$IMAGE_OCID" \
    --query 'data."lifecycle-state"' \
    --raw-output 2>/dev/null || echo "UNKNOWN")

echo ""
if [[ "$IMAGE_STATE" == "AVAILABLE" ]]; then
    echo -e "${GREEN}=========================================="
    echo "Golden Image Created Successfully!"
    echo "==========================================${NC}"
    echo ""
    echo "Image OCID: $IMAGE_OCID"
    echo "Image Name: $IMAGE_NAME"
    echo ""
    echo "Update launch-fleet.sh with:"
    echo "  IMAGE_ID=\"$IMAGE_OCID\""
    echo ""
    echo "Then launch instances:"
    echo "  ./scripts/launch-fleet.sh 2"
    echo ""
else
    echo -e "${YELLOW}Image status: $IMAGE_STATE${NC}"
    echo "Check status:"
    echo "  oci compute image get --image-id $IMAGE_OCID"
    echo ""
fi

