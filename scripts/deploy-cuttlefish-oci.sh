#!/bin/bash
# deploy-cuttlefish-oci.sh
# Create or reuse an OCI ARM64 instance and install Cuttlefish+RTMP bridge stack.
#
# Usage:
#   ./scripts/deploy-cuttlefish-oci.sh [OPTIONS]
#
# Options:
#   --name NAME              Instance name (default: cuttlefish-phone-TIMESTAMP)
#   --to-instance IP         Reuse existing instance IP (skip create)
#   --image-id OCID          Launch from specific image (recommended for golden)
#   --ocpus N                OCPUs for new instance (default: 4)
#   --memory N               Memory GB for new instance (default: 24)
#   --ssh-key-file FILE      SSH public key file (default: ~/.ssh/redroid_oci.pub)
#   --ssh-user USER          SSH username (default: ubuntu)
#   --instance-name NAME     Cuttlefish instance name (default: cvd-arm64-1)
#   --webrtc-port PORT       WebRTC signaling port (default: 8443)
#   --rtmp-url URL           RTMP ingest URL (default: rtmp://127.0.0.1/live/cam)
#   --front-sink URI         Front sink URI
#   --back-sink URI          Back sink URI
#   --mic-sink URI           Mic sink URI
#   --skip-tools-check       Skip launch_cvd/cvd presence check in installer
#   --dry-run                Print actions only
#   --help                   Show help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTANCE_NAME="cuttlefish-phone-$(date +%Y%m%d-%H%M%S)"
TARGET_IP=""
IMAGE_ID=""
OCPUS="4"
MEMORY_GB="24"
SSH_KEY_FILE="${SSH_KEY_FILE:-$HOME/.ssh/redroid_oci.pub}"
SSH_USER="ubuntu"
CF_INSTANCE_NAME="cvd-arm64-1"
WEBRTC_PORT="8443"
RTMP_URL="rtmp://127.0.0.1/live/cam"
FRONT_SINK_URI="udp://127.0.0.1:23000?pkt_size=1316"
BACK_SINK_URI="udp://127.0.0.1:23001?pkt_size=1316"
MIC_SINK_URI="udp://127.0.0.1:23010?pkt_size=1316"
SKIP_TOOLS_CHECK="false"
DRY_RUN="false"

COMPARTMENT_ID="${COMPARTMENT_ID:-}"
SUBNET_ID="${SUBNET_ID:-}"
AVAILABILITY_DOMAIN="${AVAILABILITY_DOMAIN:-}"
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
Usage:
  ./scripts/deploy-cuttlefish-oci.sh [OPTIONS]

Options:
  --name NAME              Instance name (default: cuttlefish-phone-TIMESTAMP)
  --to-instance IP         Reuse existing instance IP (skip create)
  --image-id OCID          Launch from specific image (recommended for golden)
  --ocpus N                OCPUs for new instance (default: 4)
  --memory N               Memory GB for new instance (default: 24)
  --ssh-key-file FILE      SSH public key file (default: ~/.ssh/redroid_oci.pub)
  --ssh-user USER          SSH username (default: ubuntu)
  --instance-name NAME     Cuttlefish instance name (default: cvd-arm64-1)
  --webrtc-port PORT       WebRTC signaling port (default: 8443)
  --rtmp-url URL           RTMP ingest URL
  --front-sink URI         Front sink URI
  --back-sink URI          Back sink URI
  --mic-sink URI           Mic sink URI
  --skip-tools-check       Skip launch_cvd/cvd check
  --dry-run                Print actions only
  --help                   Show help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name) INSTANCE_NAME="${2:-$INSTANCE_NAME}"; shift 2 ;;
        --to-instance) TARGET_IP="${2:-}"; shift 2 ;;
        --image-id) IMAGE_ID="${2:-}"; shift 2 ;;
        --ocpus) OCPUS="${2:-$OCPUS}"; shift 2 ;;
        --memory) MEMORY_GB="${2:-$MEMORY_GB}"; shift 2 ;;
        --ssh-key-file) SSH_KEY_FILE="${2:-$SSH_KEY_FILE}"; shift 2 ;;
        --ssh-user) SSH_USER="${2:-$SSH_USER}"; shift 2 ;;
        --instance-name) CF_INSTANCE_NAME="${2:-$CF_INSTANCE_NAME}"; shift 2 ;;
        --webrtc-port) WEBRTC_PORT="${2:-$WEBRTC_PORT}"; shift 2 ;;
        --rtmp-url) RTMP_URL="${2:-$RTMP_URL}"; shift 2 ;;
        --front-sink) FRONT_SINK_URI="${2:-$FRONT_SINK_URI}"; shift 2 ;;
        --back-sink) BACK_SINK_URI="${2:-$BACK_SINK_URI}"; shift 2 ;;
        --mic-sink) MIC_SINK_URI="${2:-$MIC_SINK_URI}"; shift 2 ;;
        --skip-tools-check) SKIP_TOOLS_CHECK="true"; shift ;;
        --dry-run) DRY_RUN="true"; shift ;;
        --help|-h) usage; exit 0 ;;
        *) log_error "Unknown option: $1"; usage; exit 1 ;;
    esac
done

if [[ "$OCPUS" -lt 4 ]] || [[ "$MEMORY_GB" -lt 24 ]]; then
    log_warn "Cuttlefish recommended baseline is 4 OCPU / 24GB."
    log_warn "Selected: ${OCPUS} OCPU / ${MEMORY_GB}GB"
fi

if [[ "$DRY_RUN" == "true" ]]; then
    echo "Dry run configuration:"
    echo "  target_ip: ${TARGET_IP:-<new-instance>}"
    echo "  instance_name: $INSTANCE_NAME"
    echo "  shape: VM.Standard.A1.Flex ${OCPUS} OCPU ${MEMORY_GB}GB"
    echo "  image_id: ${IMAGE_ID:-<auto ubuntu arm64>}"
    echo "  cuttlefish_instance: $CF_INSTANCE_NAME"
    echo "  webrtc_port: $WEBRTC_PORT"
    exit 0
fi

if [[ -z "$TARGET_IP" ]]; then
    if ! command -v oci >/dev/null 2>&1; then
        log_error "OCI CLI is required to create instances."
        exit 1
    fi
    if [[ -z "$COMPARTMENT_ID" || -z "$SUBNET_ID" || -z "$AVAILABILITY_DOMAIN" ]]; then
        log_error "COMPARTMENT_ID, SUBNET_ID, and AVAILABILITY_DOMAIN are required."
        exit 1
    fi
    if [[ ! -f "$SSH_KEY_FILE" ]]; then
        log_error "SSH public key file not found: $SSH_KEY_FILE"
        exit 1
    fi

    if [[ -z "$IMAGE_ID" ]]; then
        log_info "Selecting Ubuntu ARM64 image (24.04 then 22.04)..."
        IMAGE_ID=$(oci compute image list "${OCI_AUTH_ARGS[@]}" \
            --compartment-id "$COMPARTMENT_ID" \
            --operating-system "Canonical Ubuntu" \
            --operating-system-version "24.04" \
            --shape "VM.Standard.A1.Flex" \
            --query 'data[0].id' --raw-output 2>/dev/null || true)
        if [[ -z "$IMAGE_ID" || "$IMAGE_ID" == "null" ]]; then
            IMAGE_ID=$(oci compute image list "${OCI_AUTH_ARGS[@]}" \
                --compartment-id "$COMPARTMENT_ID" \
                --operating-system "Canonical Ubuntu" \
                --operating-system-version "22.04" \
                --shape "VM.Standard.A1.Flex" \
                --query 'data[0].id' --raw-output 2>/dev/null || true)
        fi
    fi

    if [[ -z "$IMAGE_ID" || "$IMAGE_ID" == "null" ]]; then
        log_error "Could not resolve a launch image. Use --image-id."
        exit 1
    fi

    log_info "Creating OCI instance..."
    INSTANCE_OCID=$(oci compute instance launch "${OCI_AUTH_ARGS[@]}" \
        --compartment-id "$COMPARTMENT_ID" \
        --availability-domain "$AVAILABILITY_DOMAIN" \
        --shape "VM.Standard.A1.Flex" \
        --shape-config "{\"ocpus\":$OCPUS,\"memoryInGBs\":$MEMORY_GB}" \
        --image-id "$IMAGE_ID" \
        --subnet-id "$SUBNET_ID" \
        --display-name "$INSTANCE_NAME" \
        --ssh-authorized-keys-file "$SSH_KEY_FILE" \
        --assign-public-ip true \
        --wait-for-state RUNNING \
        --query 'data.id' --raw-output)

    sleep 5
    TARGET_IP=$(oci compute instance list-vnics "${OCI_AUTH_ARGS[@]}" \
        --instance-id "$INSTANCE_OCID" \
        --query 'data[0]."public-ip"' --raw-output)
    log_info "Instance ready: $TARGET_IP"
else
    log_info "Reusing existing instance: $TARGET_IP"
fi

SSH_KEY_PRIVATE="${SSH_KEY_FILE%.pub}"
if [[ ! -f "$SSH_KEY_PRIVATE" ]]; then
    log_error "SSH private key not found: $SSH_KEY_PRIVATE"
    exit 1
fi
SSH_CMD=(ssh -i "$SSH_KEY_PRIVATE" -o StrictHostKeyChecking=no -o ConnectTimeout=8 "${SSH_USER}@${TARGET_IP}")

log_info "Waiting for SSH..."
for _ in {1..80}; do
    if "${SSH_CMD[@]}" "echo ready" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

log_info "Uploading project bundle..."
TARBALL="$(mktemp /tmp/cuttlefish-deploy.XXXXXX.tar.gz)"
tar czf "$TARBALL" \
    --exclude='.git' \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    -C "$PROJECT_ROOT" .
scp -i "$SSH_KEY_PRIVATE" -o StrictHostKeyChecking=no "$TARBALL" "${SSH_USER}@${TARGET_IP}:/tmp/redroid-cloud-phone.tar.gz"
rm -f "$TARBALL"

log_info "Installing stack on remote host..."
REMOTE_INSTALL_CMD="sudo /tmp/redroid-cloud-phone/scripts/install-cuttlefish-cloud-phone.sh --instance-name \"$CF_INSTANCE_NAME\" --webrtc-port \"$WEBRTC_PORT\" --rtmp-url \"$RTMP_URL\" --front-sink \"$FRONT_SINK_URI\" --back-sink \"$BACK_SINK_URI\""
REMOTE_INSTALL_CMD="$REMOTE_INSTALL_CMD --mic-sink \"$MIC_SINK_URI\""
if [[ "$SKIP_TOOLS_CHECK" == "true" ]]; then
    REMOTE_INSTALL_CMD="$REMOTE_INSTALL_CMD --skip-tools-check"
fi
"${SSH_CMD[@]}" bash -lc "set -euo pipefail; rm -rf /tmp/redroid-cloud-phone && mkdir -p /tmp/redroid-cloud-phone && tar xzf /tmp/redroid-cloud-phone.tar.gz -C /tmp/redroid-cloud-phone; $REMOTE_INSTALL_CMD"

log_info "Running Phase 1 validation..."
"${SSH_CMD[@]}" "bash /opt/redroid-scripts/cuttlefish-phase1-validate.sh --local --instance-name \"$CF_INSTANCE_NAME\" --webrtc-port \"$WEBRTC_PORT\" || true"

echo ""
echo -e "${BLUE}==========================================${NC}"
echo -e "${BLUE}Cuttlefish OCI deployment complete${NC}"
echo -e "${BLUE}==========================================${NC}"
echo "Host IP: $TARGET_IP"
echo "WebRTC:  https://$TARGET_IP:$WEBRTC_PORT"
echo "RTMP In: rtmp://$TARGET_IP/live/cam (stream key: cam)"
echo ""
echo "Next checks:"
echo "  ssh -i $SSH_KEY_PRIVATE ${SSH_USER}@$TARGET_IP 'sudo systemctl status cuttlefish-cloud-phone.target'"
echo "  ssh -i $SSH_KEY_PRIVATE ${SSH_USER}@$TARGET_IP 'sudo journalctl -u cuttlefish-rtmp-bridge.service -n 100 --no-pager'"
