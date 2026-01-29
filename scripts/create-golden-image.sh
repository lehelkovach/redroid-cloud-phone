#!/bin/bash
# Create Golden Image for Cloud Phone
#
# This script creates a reusable OCI custom image from a configured instance.
# The golden image can be used to rapidly deploy new cloud phone instances.
#
# Usage:
#   ./create-golden-image.sh <instance-ip> [image-name]
#
# Prerequisites:
#   - OCI CLI configured
#   - SSH access to the instance
#   - Instance must be running with cloud phone installed

set -euo pipefail

INSTANCE_IP="${1:-}"
IMAGE_NAME="${2:-cloud-phone-golden-$(date +%Y%m%d)}"

SSH_KEY="${SSH_KEY_FILE:-$HOME/.ssh/redroid_oci}"
COMPARTMENT_ID="${COMPARTMENT_ID:-}"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }
log_header() { echo -e "${BLUE}=== $1 ===${NC}"; }

usage() {
    cat <<EOF
Create Golden Image for Cloud Phone

Usage: $0 <instance-ip> [image-name]

Arguments:
  instance-ip     IP address of the configured instance
  image-name      Name for the golden image (default: cloud-phone-golden-YYYYMMDD)

Environment Variables:
  SSH_KEY_FILE    Path to SSH private key (default: ~/.ssh/redroid_oci)
  COMPARTMENT_ID  OCI compartment ID for the image

Examples:
  $0 129.146.123.45
  $0 129.146.123.45 my-cloud-phone-v1

The script will:
  1. Prepare the instance (clean logs, remove sensitive data)
  2. Stop the instance
  3. Create a custom image
  4. Restart the instance

EOF
    exit 1
}

if [[ -z "$INSTANCE_IP" ]]; then
    usage
fi

if ! command -v oci &>/dev/null; then
    log_error "OCI CLI not installed"
    exit 1
fi

if [[ -z "$COMPARTMENT_ID" ]]; then
    log_error "COMPARTMENT_ID required"
    exit 1
fi

SSH_CMD="ssh -i $SSH_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=10"

log_header "Creating Golden Image: $IMAGE_NAME"
echo ""

# Step 1: Get instance OCID
log_info "Finding instance OCID..."
INSTANCE_OCID=$(oci compute instance list \
    --compartment-id "$COMPARTMENT_ID" \
    --query "data[?\"primary-public-ip\"=='$INSTANCE_IP'].id | [0]" \
    --raw-output 2>/dev/null)

if [[ -z "$INSTANCE_OCID" ]] || [[ "$INSTANCE_OCID" == "null" ]]; then
    log_error "Could not find instance with IP: $INSTANCE_IP"
    exit 1
fi
log_info "Found instance: ${INSTANCE_OCID:0:50}..."

# Step 2: Prepare instance for imaging
log_info "Preparing instance for imaging..."

$SSH_CMD ubuntu@$INSTANCE_IP << 'PREPARE_EOF'
set -e
echo "Cleaning up for golden image..."

# Stop services gracefully
sudo systemctl stop redroid-cloud-phone.target 2>/dev/null || true
sudo systemctl stop docker 2>/dev/null || true

# Clean Docker (keep images, remove containers)
sudo docker rm -f $(sudo docker ps -aq) 2>/dev/null || true

# Clear logs
sudo journalctl --rotate
sudo journalctl --vacuum-time=1s
sudo rm -rf /var/log/*.gz /var/log/*.1 /var/log/*.old
sudo truncate -s 0 /var/log/syslog 2>/dev/null || true
sudo truncate -s 0 /var/log/auth.log 2>/dev/null || true

# Clear temporary files
sudo rm -rf /tmp/* /var/tmp/*

# Clear bash history
rm -f ~/.bash_history
history -c

# Clear SSH keys (will regenerate on first boot)
# Note: Don't remove authorized_keys, just host keys
sudo rm -f /etc/ssh/ssh_host_*

# Clear cloud-init for re-initialization
sudo cloud-init clean --logs 2>/dev/null || true

# Clear machine-id (regenerates on boot)
sudo truncate -s 0 /etc/machine-id

# Remove any cached credentials
rm -rf ~/.oci ~/.aws ~/.config/gcloud 2>/dev/null || true

# Clear API tokens from config
sudo sed -i 's/"token": ".*"/"token": ""/' /etc/cloud-phone/config.json 2>/dev/null || true

# Sync filesystem
sync

echo "Instance prepared for imaging"
PREPARE_EOF

log_info "Instance prepared"

# Step 3: Stop the instance
log_info "Stopping instance..."
oci compute instance action \
    --instance-id "$INSTANCE_OCID" \
    --action STOP \
    --wait-for-state STOPPED

log_info "Instance stopped"

# Step 4: Create custom image
log_info "Creating custom image (this may take 10-30 minutes)..."

IMAGE_OCID=$(oci compute image create \
    --compartment-id "$COMPARTMENT_ID" \
    --instance-id "$INSTANCE_OCID" \
    --display-name "$IMAGE_NAME" \
    --wait-for-state AVAILABLE \
    --query 'data.id' \
    --raw-output)

log_info "Image created: ${IMAGE_OCID:0:50}..."

# Step 5: Restart the instance
log_info "Restarting instance..."
oci compute instance action \
    --instance-id "$INSTANCE_OCID" \
    --action START \
    --wait-for-state RUNNING

log_info "Instance restarted"

# Wait for SSH
log_info "Waiting for SSH..."
for i in {1..30}; do
    if $SSH_CMD ubuntu@$INSTANCE_IP 'echo ready' &>/dev/null; then
        break
    fi
    sleep 2
done

# Restart services
$SSH_CMD ubuntu@$INSTANCE_IP 'sudo systemctl start docker && sudo systemctl start redroid-cloud-phone.target' || true

# Summary
echo ""
log_header "Golden Image Created Successfully"
echo ""
echo "Image Details:"
echo "  Name: $IMAGE_NAME"
echo "  OCID: $IMAGE_OCID"
echo "  Compartment: $COMPARTMENT_ID"
echo ""
echo "Deploy new instance from this image:"
echo ""
cat <<EOF
oci compute instance launch \\
  --compartment-id "$COMPARTMENT_ID" \\
  --availability-domain "YOUR-AD" \\
  --shape "VM.Standard.A1.Flex" \\
  --shape-config '{"ocpus":2,"memoryInGBs":8}' \\
  --image-id "$IMAGE_OCID" \\
  --subnet-id "YOUR-SUBNET-ID" \\
  --display-name "cloud-phone-from-golden" \\
  --ssh-authorized-keys-file ~/.ssh/your-key.pub \\
  --assign-public-ip true
EOF
echo ""
echo "Or use the deployment script:"
echo "  GOLDEN_IMAGE_ID=$IMAGE_OCID ./scripts/deploy-from-golden.sh"
echo ""

# Save image info
cat > /tmp/golden-image-info.json <<EOF
{
  "image_name": "$IMAGE_NAME",
  "image_ocid": "$IMAGE_OCID",
  "compartment_id": "$COMPARTMENT_ID",
  "created_from": "$INSTANCE_OCID",
  "created_at": "$(date -Iseconds)"
}
EOF
log_info "Image info saved to /tmp/golden-image-info.json"
