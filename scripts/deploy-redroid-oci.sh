#!/bin/bash
# deploy-redroid-oci.sh
# Create or reuse an OCI ARM64 instance and install Redroid + GApps automation stack.
# Camera/mic ingest stays on Cuttlefish (deploy-cuttlefish-oci.sh).
#
# Usage:
#   ./scripts/deploy-redroid-oci.sh [OPTIONS]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTANCE_NAME="redroid-phone-$(date +%Y%m%d-%H%M%S)"
TARGET_IP=""
IMAGE_ID=""
OCPUS="2"
MEMORY_GB="8"
SSH_KEY_FILE="${SSH_KEY_FILE:-$HOME/.ssh/android_arm_cloud_phone_oci.pub}"
SSH_USER="ubuntu"
NAME="${REDROID_NAME:-redroid}"
ADB_PORT="${ADB_PORT:-5555}"
REDROID_IMAGE="${REDROID_IMAGE:-redroid/redroid:11.0.0-latest}"
SKIP_PULL="false"
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
  ./scripts/deploy-redroid-oci.sh [OPTIONS]

Options:
  --name NAME              OCI instance name
  --to-instance IP         Reuse existing instance IP (skip create)
  --image-id OCID          Launch from specific Ubuntu or Redroid golden image
  --ocpus N                OCPUs (default: 2)
  --memory N               Memory GB (default: 8)
  --ssh-key-file FILE      SSH public key
  --ssh-user USER          SSH username (default: ubuntu)
  --redroid-name NAME      Container name (default: redroid)
  --adb-port PORT          Host ADB port (default: 5555)
  --redroid-image IMAGE    Redroid docker tag
  --skip-pull              Do not docker pull on the guest
  --dry-run                Print actions only
  --help                   Show help

GApps: set GAPPS_ZIP to a local zip (scp'd) or GAPPS_ZIP_URL on the guest.
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
        --redroid-name) NAME="${2:-$NAME}"; shift 2 ;;
        --adb-port) ADB_PORT="${2:-$ADB_PORT}"; shift 2 ;;
        --redroid-image) REDROID_IMAGE="${2:-$REDROID_IMAGE}"; shift 2 ;;
        --skip-pull) SKIP_PULL="true"; shift ;;
        --dry-run) DRY_RUN="true"; shift ;;
        --help|-h) usage; exit 0 ;;
        *) log_error "Unknown option: $1"; usage; exit 1 ;;
    esac
done

if [[ "$DRY_RUN" == "true" ]]; then
    echo "Dry run configuration:"
    echo "  runtime: redroid"
    echo "  target_ip: ${TARGET_IP:-<new-instance>}"
    echo "  instance_name: $INSTANCE_NAME"
    echo "  shape: VM.Standard.A1.Flex ${OCPUS} OCPU ${MEMORY_GB}GB"
    echo "  image_id: ${IMAGE_ID:-<auto ubuntu arm64>}"
    echo "  redroid_name: $NAME"
    echo "  adb_port: $ADB_PORT"
    echo "  redroid_image: $REDROID_IMAGE"
    echo "  camera: none (Cuttlefish owns ingest)"
    exit 0
fi

INSTANCE_OCID=""
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
TARBALL="$(mktemp /tmp/redroid-deploy.XXXXXX.tar.gz)"
tar czf "$TARBALL" \
    --exclude='.git' \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='gapps.zip' \
    -C "$PROJECT_ROOT" .
scp -i "$SSH_KEY_PRIVATE" -o StrictHostKeyChecking=no "$TARBALL" "${SSH_USER}@${TARGET_IP}:/tmp/redroid-cloud-phone.tar.gz"
rm -f "$TARBALL"

if [[ -n "${GAPPS_ZIP:-}" && -f "${GAPPS_ZIP}" ]]; then
    log_info "Uploading operator GApps zip (not committed)..."
    "${SSH_CMD[@]}" "sudo mkdir -p /opt/gapps && sudo chown ${SSH_USER}:${SSH_USER} /opt/gapps"
    scp -i "$SSH_KEY_PRIVATE" -o StrictHostKeyChecking=no "$GAPPS_ZIP" "${SSH_USER}@${TARGET_IP}:/opt/gapps/gapps.zip"
fi

log_info "Installing Redroid stack on remote host..."
REMOTE_INSTALL_CMD="sudo GAPPS_ZIP=/opt/gapps/gapps.zip REDROID_IMAGE='$REDROID_IMAGE' /tmp/redroid-cloud-phone/scripts/install-redroid-cloud-phone.sh --name '$NAME' --adb-port '$ADB_PORT' --image '$REDROID_IMAGE'"
if [[ -n "${GAPPS_ZIP_URL:-}" ]]; then
    REMOTE_INSTALL_CMD="sudo GAPPS_ZIP_URL='$GAPPS_ZIP_URL' $REMOTE_INSTALL_CMD"
fi
if [[ "$SKIP_PULL" == "true" ]]; then
    REMOTE_INSTALL_CMD="$REMOTE_INSTALL_CMD --skip-pull"
fi
"${SSH_CMD[@]}" bash -lc "set -euo pipefail; rm -rf /tmp/redroid-cloud-phone && mkdir -p /tmp/redroid-cloud-phone && tar xzf /tmp/redroid-cloud-phone.tar.gz -C /tmp/redroid-cloud-phone; $REMOTE_INSTALL_CMD"

cat > "/tmp/instance-$INSTANCE_NAME.json" <<EOF
{
  "instance_name": "$INSTANCE_NAME",
  "instance_ocid": "${INSTANCE_OCID}",
  "public_ip": "$TARGET_IP",
  "platform": "redroid",
  "purpose": "automation",
  "adb_port": $ADB_PORT,
  "deployed_at": "$(date -Iseconds)"
}
EOF

echo ""
echo -e "${BLUE}==========================================${NC}"
echo -e "${BLUE}Redroid OCI deployment complete${NC}"
echo -e "${BLUE}==========================================${NC}"
echo "Host IP: $TARGET_IP"
echo "Control: http://$TARGET_IP:8080/health"
echo "ADB:     $TARGET_IP:$ADB_PORT"
echo ""
echo "Next:"
echo "  ssh -i $SSH_KEY_PRIVATE ${SSH_USER}@$TARGET_IP 'sudo systemctl status redroid-cloud-phone.target'"
echo "  ./scripts/verify-redroid-phone.sh --vm $TARGET_IP"
echo "  COMPARTMENT_ID=... ./cloud-phone create-golden $TARGET_IP cloud-phone-redroid-gapps-v1 redroid"
