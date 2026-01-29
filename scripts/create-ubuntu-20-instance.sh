#!/bin/bash
# Create Oracle Cloud instance with Ubuntu 20.04 (older kernel for Redroid testing)

set -euo pipefail

INSTANCE_NAME="${1:-redroid-ubuntu20-test}"
COMPARTMENT_ID="${COMPARTMENT_ID:-ocid1.tenancy.oc1..aaaaaaaak44wevthunqrdp6h6noor4o5t34a7jqejla6wd22v47admhyzoca}"
SUBNET_ID="${SUBNET_ID:-ocid1.subnet.oc1.phx.aaaaaaaalpdm6cgqxuairct2uzbn74p7u5x4dqqvfleqoxhjcy6pn6fzeh2q}"
SSH_KEY_FILE="${SSH_KEY_FILE:-$HOME/.ssh/redroid_oci.pub}"

echo "=========================================="
echo "  Create Ubuntu 20.04 Instance"
echo "  For Redroid Testing (Kernel 5.x)"
echo "=========================================="
echo ""

# Check OCI CLI
if ! command -v oci &> /dev/null; then
    echo "Error: OCI CLI not installed"
    exit 1
fi

# Check SSH key
if [[ ! -f "$SSH_KEY_FILE" ]]; then
    echo "Error: SSH key not found: $SSH_KEY_FILE"
    exit 1
fi

echo "Finding Ubuntu 20.04 ARM image..."
UBUNTU_IMAGE=$(oci compute image list \
    --compartment-id "$COMPARTMENT_ID" \
    --operating-system "Canonical Ubuntu" \
    --operating-system-version "20.04" \
    --shape "VM.Standard.A1.Flex" \
    --query 'data[0].id' \
    --raw-output 2>/dev/null || echo "")

if [[ -z "$UBUNTU_IMAGE" ]]; then
    echo "Error: Ubuntu 20.04 ARM image not found"
    echo "Available Ubuntu images:"
    oci compute image list \
        --compartment-id "$COMPARTMENT_ID" \
        --operating-system "Canonical Ubuntu" \
        --shape "VM.Standard.A1.Flex" \
        --query 'data[*].[operating-system-version,id]' \
        --output table
    exit 1
fi

echo "✓ Found Ubuntu 20.04 image: $UBUNTU_IMAGE"
echo ""

echo "Creating instance: $INSTANCE_NAME"
echo "Shape: VM.Standard.A1.Flex (2 OCPU, 8GB RAM)"
echo ""

INSTANCE_OCID=$(oci compute instance launch \
    --compartment-id "$COMPARTMENT_ID" \
    --availability-domain "ABpi:PHX-AD-1" \
    --shape "VM.Standard.A1.Flex" \
    --shape-config '{"ocpus":2,"memoryInGBs":8}' \
    --image-id "$UBUNTU_IMAGE" \
    --subnet-id "$SUBNET_ID" \
    --display-name "$INSTANCE_NAME" \
    --ssh-authorized-keys-file "$SSH_KEY_FILE" \
    --assign-public-ip true \
    --wait-for-state RUNNING \
    --query 'data.id' \
    --raw-output)

if [[ -z "$INSTANCE_OCID" ]]; then
    echo "Error: Failed to create instance"
    exit 1
fi

echo "✓ Instance created: $INSTANCE_OCID"
echo ""

echo "Getting public IP..."
sleep 5
PUBLIC_IP=$(oci compute instance list-vnics \
    --instance-id "$INSTANCE_OCID" \
    --query 'data[0]."public-ip"' \
    --raw-output 2>/dev/null || echo "")

if [[ -z "$PUBLIC_IP" ]] || [[ "$PUBLIC_IP" == "null" ]]; then
    echo "⚠ Could not get public IP immediately"
    echo "Try: oci compute instance list-vnics --instance-id $INSTANCE_OCID"
else
    echo "✓ Public IP: $PUBLIC_IP"
fi

echo ""
echo "=========================================="
echo "  Instance Created Successfully"
echo "=========================================="
echo ""
echo "Instance Details:"
echo "  Name: $INSTANCE_NAME"
echo "  OCID: $INSTANCE_OCID"
echo "  Public IP: ${PUBLIC_IP:-<pending>}"
echo "  OS: Ubuntu 20.04 (Kernel 5.x)"
echo ""
echo "Next Steps:"
echo "  1. Wait for SSH to be ready (~30 seconds)"
echo "  2. Test kernel version:"
echo "     ssh -i $SSH_KEY_FILE ubuntu@${PUBLIC_IP:-<IP>} 'uname -r'"
echo "  3. Run Redroid test:"
echo "     ./scripts/test-ubuntu-20.04.sh ${PUBLIC_IP:-<IP>}"
echo "  4. Deploy Redroid:"
echo "     ./scripts/deploy-to-instance.sh ${PUBLIC_IP:-<IP>}"
echo ""








