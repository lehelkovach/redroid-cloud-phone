#!/bin/bash
# Deploy Cuttlefish Cloud Phone from OCI golden image.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

INSTANCE_NAME="cuttlefish-phone-$(date +%Y%m%d-%H%M%S)"
GOLDEN_IMAGE_ID="${GOLDEN_IMAGE_ID:-}"
OCPUS=4
MEMORY_GB=24
WAIT_CHECK=false
RUN_TESTS=false
PLATFORM="cuttlefish"

COMPARTMENT_ID="${COMPARTMENT_ID:-}"
SUBNET_ID="${SUBNET_ID:-}"
AVAILABILITY_DOMAIN="${AVAILABILITY_DOMAIN:-}"
SSH_KEY_FILE="${SSH_KEY_FILE:-$HOME/.ssh/android_arm_cloud_phone_oci.pub}"
SECURITY_TOKEN_FILE="${SECURITY_TOKEN_FILE:-$HOME/.oci/sessions/DEFAULT/token}"
OCI_AUTH_ARGS=()
if [[ -f "$SECURITY_TOKEN_FILE" ]]; then
    OCI_AUTH_ARGS+=(--auth security_token)
fi

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

usage() {
    cat <<'EOF'
Usage: ./scripts/deploy-from-golden.sh [OPTIONS]

Options:
  --name NAME           Instance name
  --image-id OCID       Golden image OCID (or set GOLDEN_IMAGE_ID env)
  --ocpus N             OCPUs (default: 4)
  --memory N            Memory in GB (default: 24)
  --platform NAME       cuttlefish (required value)
  --wait-check          Run runtime health check after deploy
  --run-tests           Run ingest verification script after deploy
  --list-images         List available golden images
  --help                Show help
EOF
}

list_golden_images() {
    [[ -n "$COMPARTMENT_ID" ]] || { log_error "COMPARTMENT_ID required for --list-images"; exit 1; }
    oci compute image list "${OCI_AUTH_ARGS[@]}" \
        --compartment-id "$COMPARTMENT_ID" \
        --query 'data[?starts_with("display-name", `cloud-phone-cuttlefish`)].[display-name,id,"time-created"]' \
        --output table
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name) INSTANCE_NAME="$2"; shift 2 ;;
        --image-id) GOLDEN_IMAGE_ID="$2"; shift 2 ;;
        --ocpus) OCPUS="$2"; shift 2 ;;
        --memory) MEMORY_GB="$2"; shift 2 ;;
        --platform) PLATFORM="$2"; shift 2 ;;
        --wait-check) WAIT_CHECK=true; shift ;;
        --run-tests) RUN_TESTS=true; shift ;;
        --list-images) list_golden_images; exit 0 ;;
        --help|-h) usage; exit 0 ;;
        *) log_error "Unknown option: $1"; usage; exit 1 ;;
    esac
done

[[ "$PLATFORM" == "cuttlefish" ]] || { log_error "Only --platform cuttlefish is supported."; exit 1; }
[[ -n "$GOLDEN_IMAGE_ID" ]] || { log_error "GOLDEN_IMAGE_ID or --image-id is required."; exit 1; }
[[ -n "$COMPARTMENT_ID" ]] || { log_error "COMPARTMENT_ID required."; exit 1; }
[[ -n "$SUBNET_ID" ]] || { log_error "SUBNET_ID required."; exit 1; }
[[ -n "$AVAILABILITY_DOMAIN" ]] || { log_error "AVAILABILITY_DOMAIN required."; exit 1; }
[[ -f "$SSH_KEY_FILE" ]] || { log_error "SSH key not found: $SSH_KEY_FILE"; exit 1; }

if [[ "$OCPUS" -lt 4 ]] || [[ "$MEMORY_GB" -lt 24 ]]; then
    log_warn "Cuttlefish recommended baseline is 4 OCPU / 24GB."
fi

log_info "Launching instance from golden image..."
INSTANCE_OCID=$(oci compute instance launch "${OCI_AUTH_ARGS[@]}" \
    --compartment-id "$COMPARTMENT_ID" \
    --availability-domain "$AVAILABILITY_DOMAIN" \
    --shape "VM.Standard.A1.Flex" \
    --shape-config "{\"ocpus\":$OCPUS,\"memoryInGBs\":$MEMORY_GB}" \
    --image-id "$GOLDEN_IMAGE_ID" \
    --subnet-id "$SUBNET_ID" \
    --display-name "$INSTANCE_NAME" \
    --ssh-authorized-keys-file "$SSH_KEY_FILE" \
    --assign-public-ip true \
    --wait-for-state RUNNING \
    --query 'data.id' \
    --raw-output)

sleep 5
PUBLIC_IP=$(oci compute instance list-vnics "${OCI_AUTH_ARGS[@]}" \
    --instance-id "$INSTANCE_OCID" \
    --query 'data[0]."public-ip"' \
    --raw-output)

[[ -n "$PUBLIC_IP" && "$PUBLIC_IP" != "null" ]] || { log_error "Failed to resolve public IP"; exit 1; }

SSH_KEY_PRIVATE="${SSH_KEY_FILE%.pub}"
SSH_CMD="ssh -i $SSH_KEY_PRIVATE -o StrictHostKeyChecking=no -o ConnectTimeout=5"

log_info "Waiting for SSH..."
for _ in {1..60}; do
    if $SSH_CMD ubuntu@"$PUBLIC_IP" "echo ready" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

log_info "Starting Cuttlefish target..."
$SSH_CMD ubuntu@"$PUBLIC_IP" 'sudo systemctl start cuttlefish-cloud-phone.target'

if [[ "$WAIT_CHECK" == "true" ]]; then
    log_info "Running runtime validation..."
    $SSH_CMD ubuntu@"$PUBLIC_IP" "/opt/cloud-phone-scripts/cuttlefish-phase1-validate.sh --local" || true
fi

if [[ "$RUN_TESTS" == "true" ]]; then
    log_info "Running ingest verification..."
    "$SCRIPT_DIR/verify-cuttlefish-ingest.sh" --vm "$PUBLIC_IP" || true
fi

cat > "/tmp/instance-$INSTANCE_NAME.json" <<EOF
{
  "instance_name": "$INSTANCE_NAME",
  "instance_ocid": "$INSTANCE_OCID",
  "public_ip": "$PUBLIC_IP",
  "golden_image": "$GOLDEN_IMAGE_ID",
  "platform": "cuttlefish",
  "deployed_at": "$(date -Iseconds)"
}
EOF

echo ""
echo -e "${BLUE}========================================${NC}"
echo "  Deployment Complete"
echo -e "${BLUE}========================================${NC}"
echo "Instance: $INSTANCE_NAME"
echo "IP:       $PUBLIC_IP"
echo "OCID:     $INSTANCE_OCID"
echo "WebRTC:   https://$PUBLIC_IP:8443"
echo "RTMP In:  rtmp://$PUBLIC_IP/live (key: cam)"
