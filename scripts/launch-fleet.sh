#!/bin/bash
# launch-fleet.sh
# Launches multiple OCI instances from a golden image
#
# Prerequisites:
# - OCI CLI installed and configured
# - Golden image already created
#
# Usage:
#   1. Run ./get-oci-config.sh to discover your OCIDs (optional but helpful)
#   2. Edit the configuration variables below with your OCIDs
#   3. Run: ./launch-fleet.sh [count]
#
# Example:
#   ./launch-fleet.sh 2  # Launch 2 instances

set -euo pipefail

# ============================================
# CONFIGURATION - EDIT THESE VALUES
# ============================================

# Your golden image OCID (get from: oci compute image list --compartment-id ... --operating-system Custom)
# Create one first: OCI Console → Compute → Instances → [Your Instance] → More Actions → Create Custom Image
IMAGE_ID="ocid1.image.oc1.phx.xxx"

# Your compartment OCID
# Using root compartment (tenancy) - required for VCN/Subnet creation
# Alternative: ocid1.compartment.oc1..aaaaaaaapbv337zkwryqt32hrmf5fsglbzjbzrctv5ucvxcelstjivg3knwa (ManagedCompartmentForPaaS)
COMPARTMENT_ID="ocid1.tenancy.oc1..aaaaaaaak44wevthunqrdp6h6noor4o5t34a7jqejla6wd22v47admhyzoca"

# Your subnet OCID (for the VCN where instances will be created)
# Created via: ./scripts/setup-networking.sh
SUBNET_ID="ocid1.subnet.oc1.phx.aaaaaaaalpdm6cgqxuairct2uzbn74p7u5x4dqqvfleqoxhjcy6pn6fzeh2q"

# SSH public key file path
SSH_KEY_FILE="${HOME}/.ssh/waydroid_oci.pub"

# Instance shape configuration
SHAPE="VM.Standard.A1.Flex"
OCPUS=2
MEMORY_GB=8

# Availability domains (Phoenix region - PHX)
# Discovered via: oci iam availability-domain list
AVAILABILITY_DOMAINS=(
    "ABpi:PHX-AD-1"
    "ABpi:PHX-AD-2"
    "ABpi:PHX-AD-3"
)

# ============================================
# SCRIPT LOGIC
# ============================================

# Number of instances to launch (default: 2)
INSTANCE_COUNT="${1:-2}"

# Validate OCI CLI is installed
if ! command -v oci &> /dev/null; then
    echo "Error: OCI CLI is not installed."
    echo "Install from: https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm"
    exit 1
fi

# Validate SSH key exists
if [[ ! -f "$SSH_KEY_FILE" ]]; then
    echo "Error: SSH key file not found: $SSH_KEY_FILE"
    exit 1
fi

# Validate configuration
if [[ "$IMAGE_ID" == "ocid1.image.oc1.iad.xxx" ]] || \
   [[ "$COMPARTMENT_ID" == "ocid1.compartment.oc1..xxx" ]] || \
   [[ "$SUBNET_ID" == "ocid1.subnet.oc1.iad.xxx" ]]; then
    echo "Error: Please edit the configuration variables in this script first!"
    echo "Required: IMAGE_ID, COMPARTMENT_ID, SUBNET_ID"
    exit 1
fi

# Check if image exists
echo "Validating golden image..."
if ! oci compute image get --image-id "$IMAGE_ID" &>/dev/null; then
    echo "Error: Image not found: $IMAGE_ID"
    echo "Verify the IMAGE_ID is correct and you have access to it."
    exit 1
fi

echo "=========================================="
echo "Launching $INSTANCE_COUNT instance(s) from golden image"
echo "=========================================="
echo "Image: $IMAGE_ID"
echo "Shape: $SHAPE ($OCPUS OCPU, ${MEMORY_GB}GB RAM)"
echo ""

# Launch instances
for i in $(seq 1 "$INSTANCE_COUNT"); do
    INSTANCE_NAME="waydroid-phone-$i"
    
    # Rotate through availability domains
    AD_INDEX=$(( (i - 1) % ${#AVAILABILITY_DOMAINS[@]} ))
    AD="${AVAILABILITY_DOMAINS[$AD_INDEX]}"
    
    echo "[$i/$INSTANCE_COUNT] Launching $INSTANCE_NAME in $AD..."
    
    oci compute instance launch \
        --compartment-id "$COMPARTMENT_ID" \
        --availability-domain "$AD" \
        --shape "$SHAPE" \
        --shape-config "{\"ocpus\":$OCPUS,\"memoryInGBs\":$MEMORY_GB}" \
        --image-id "$IMAGE_ID" \
        --subnet-id "$SUBNET_ID" \
        --display-name "$INSTANCE_NAME" \
        --ssh-authorized-keys-file "$SSH_KEY_FILE" \
        --assign-public-ip true \
        --wait-for-state RUNNING \
        --query 'data."lifecycle-state"' \
        --raw-output
    
    if [[ $? -eq 0 ]]; then
        echo "  ✓ $INSTANCE_NAME is RUNNING"
        
        # Get public IP
        PUBLIC_IP=$(oci compute instance list-vnics \
            --instance-id "$(oci compute instance list \
                --compartment-id "$COMPARTMENT_ID" \
                --display-name "$INSTANCE_NAME" \
                --query 'data[0].id' \
                --raw-output)" \
            --query 'data[0]."public-ip"' \
            --raw-output 2>/dev/null || echo "N/A")
        
        echo "  Public IP: $PUBLIC_IP"
    else
        echo "  ✗ Failed to launch $INSTANCE_NAME"
    fi
    
    echo ""
done

echo "=========================================="
echo "Launch complete!"
echo "=========================================="
echo ""
echo "Next steps for each instance:"
echo "1. SSH in: ssh -i ~/.ssh/waydroid_oci ubuntu@<PUBLIC_IP>"
echo "2. Start services: sudo systemctl start waydroid-cloud-phone.target"
echo "3. Check status: sudo /opt/waydroid-scripts/health-check.sh"
echo ""
echo "To list all instances:"
echo "  oci compute instance list --compartment-id $COMPARTMENT_ID --display-name waydroid-phone"
echo ""

