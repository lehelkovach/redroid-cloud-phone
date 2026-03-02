#!/bin/bash
# Create Cuttlefish golden image from a prepared OCI instance.

set -euo pipefail

INSTANCE_IP="${1:-}"
IMAGE_NAME="${2:-cloud-phone-cuttlefish-$(date +%Y%m%d)}"
PLATFORM="${3:-cuttlefish}"

SSH_KEY="${SSH_KEY_FILE:-$HOME/.ssh/android_arm_cloud_phone_oci}"
COMPARTMENT_ID="${COMPARTMENT_ID:-}"

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
    cat <<'EOF'
Usage: ./scripts/create-golden-image.sh <instance-ip> [image-name] [platform]

Arguments:
  instance-ip     Public IP of source instance
  image-name      Custom image display name (default: cloud-phone-cuttlefish-YYYYMMDD)
  platform        cuttlefish (required value)
EOF
}

[[ -n "$INSTANCE_IP" ]] || { usage; exit 1; }
[[ "$PLATFORM" == "cuttlefish" ]] || { log_error "Only cuttlefish platform is supported."; exit 1; }
command -v oci >/dev/null 2>&1 || { log_error "OCI CLI is required."; exit 1; }
[[ -n "$COMPARTMENT_ID" ]] || { log_error "COMPARTMENT_ID required."; exit 1; }

SSH_CMD="ssh -i $SSH_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=10"

log_header "Create Golden Image"
log_info "Finding source instance by IP..."
INSTANCE_OCID=$(oci compute instance list \
    --compartment-id "$COMPARTMENT_ID" \
    --query "data[?\"primary-public-ip\"=='$INSTANCE_IP'].id | [0]" \
    --raw-output 2>/dev/null)
[[ -n "$INSTANCE_OCID" && "$INSTANCE_OCID" != "null" ]] || { log_error "Instance with IP $INSTANCE_IP not found."; exit 1; }

log_info "Stopping Cuttlefish services and cleaning instance..."
$SSH_CMD ubuntu@"$INSTANCE_IP" 'sudo /opt/cloud-phone-scripts/prepare-golden-image.sh --platform cuttlefish'

log_info "Stopping instance..."
oci compute instance action --instance-id "$INSTANCE_OCID" --action STOP --wait-for-state STOPPED >/dev/null

log_info "Creating custom image (may take 10-30 minutes)..."
IMAGE_OCID=$(oci compute image create \
    --compartment-id "$COMPARTMENT_ID" \
    --instance-id "$INSTANCE_OCID" \
    --display-name "$IMAGE_NAME" \
    --wait-for-state AVAILABLE \
    --query 'data.id' \
    --raw-output)

log_info "Restarting instance..."
oci compute instance action --instance-id "$INSTANCE_OCID" --action START --wait-for-state RUNNING >/dev/null

$SSH_CMD ubuntu@"$INSTANCE_IP" 'sudo systemctl start cuttlefish-cloud-phone.target' || true

cat > /tmp/golden-image-info.json <<EOF
{
  "image_name": "$IMAGE_NAME",
  "image_ocid": "$IMAGE_OCID",
  "platform": "cuttlefish",
  "compartment_id": "$COMPARTMENT_ID",
  "created_from": "$INSTANCE_OCID",
  "created_at": "$(date -Iseconds)"
}
EOF

echo ""
log_header "Golden Image Created"
echo "Name:      $IMAGE_NAME"
echo "Image OCID:$IMAGE_OCID"
echo ""
echo "Deploy with:"
echo "  GOLDEN_IMAGE_ID=$IMAGE_OCID ./scripts/deploy-from-golden.sh --platform cuttlefish --name phone-1"
