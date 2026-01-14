#!/bin/bash
# Deploy Cloud Phone from Golden Image
#
# Rapidly deploys a new cloud phone instance from a pre-built golden image.
# Much faster than fresh installation (~2 min vs ~15 min).
#
# Usage:
#   ./deploy-from-golden.sh [options]
#
# Options:
#   --name NAME           Instance name
#   --image-id OCID       Golden image OCID (or set GOLDEN_IMAGE_ID env)
#   --ocpus N             Number of OCPUs (default: 2)
#   --memory N            Memory in GB (default: 8)
#   --config FILE         Post-deployment config file
#   --proxy URL           Set proxy after deployment
#   --gps LAT,LON         Set GPS after deployment

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Defaults
INSTANCE_NAME="cloud-phone-$(date +%Y%m%d-%H%M%S)"
GOLDEN_IMAGE_ID="${GOLDEN_IMAGE_ID:-}"
OCPUS=2
MEMORY_GB=8
CONFIG_FILE=""
PROXY_URL=""
GPS_COORDS=""

# OCI settings
COMPARTMENT_ID="${COMPARTMENT_ID:-}"
SUBNET_ID="${SUBNET_ID:-}"
AVAILABILITY_DOMAIN="${AVAILABILITY_DOMAIN:-}"
SSH_KEY_FILE="${SSH_KEY_FILE:-$HOME/.ssh/waydroid_oci.pub}"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

usage() {
    cat <<EOF
Deploy Cloud Phone from Golden Image

Usage: $0 [OPTIONS]

Options:
  --name NAME           Instance name (default: cloud-phone-TIMESTAMP)
  --image-id OCID       Golden image OCID (or set GOLDEN_IMAGE_ID env var)
  --ocpus N             Number of OCPUs (default: 2)
  --memory N            Memory in GB (default: 8)
  --config FILE         Post-deployment config JSON file
  --proxy URL           Proxy URL (e.g., socks5://host:port)
  --gps LAT,LON         GPS coordinates (e.g., 37.7749,-122.4194)
  --list-images         List available golden images
  --help                Show this help

Environment Variables:
  GOLDEN_IMAGE_ID       Default golden image OCID
  COMPARTMENT_ID        OCI compartment ID
  SUBNET_ID             OCI subnet ID
  AVAILABILITY_DOMAIN   OCI availability domain
  SSH_KEY_FILE          SSH public key file

Examples:
  # Deploy with default settings
  GOLDEN_IMAGE_ID=ocid1.image... $0

  # Deploy with custom config
  $0 --image-id ocid1.image... --name my-phone --proxy socks5://proxy:1080

  # Deploy multiple instances
  for i in 1 2 3; do
    $0 --name phone-\$i &
  done
  wait

EOF
    exit 0
}

list_golden_images() {
    log_info "Available golden images:"
    oci compute image list \
        --compartment-id "$COMPARTMENT_ID" \
        --query 'data[?starts_with("display-name", `cloud-phone`)].[display-name,id,"time-created"]' \
        --output table
    exit 0
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --name) INSTANCE_NAME="$2"; shift 2 ;;
            --image-id) GOLDEN_IMAGE_ID="$2"; shift 2 ;;
            --ocpus) OCPUS="$2"; shift 2 ;;
            --memory) MEMORY_GB="$2"; shift 2 ;;
            --config) CONFIG_FILE="$2"; shift 2 ;;
            --proxy) PROXY_URL="$2"; shift 2 ;;
            --gps) GPS_COORDS="$2"; shift 2 ;;
            --list-images) list_golden_images ;;
            --help|-h) usage ;;
            *) log_error "Unknown option: $1"; usage ;;
        esac
    done
}

validate() {
    local errors=0
    
    if [[ -z "$GOLDEN_IMAGE_ID" ]]; then
        log_error "Golden image ID required (--image-id or GOLDEN_IMAGE_ID env)"
        errors=$((errors + 1))
    fi
    
    if [[ -z "$COMPARTMENT_ID" ]]; then
        log_error "COMPARTMENT_ID required"
        errors=$((errors + 1))
    fi
    
    if [[ -z "$SUBNET_ID" ]]; then
        log_error "SUBNET_ID required"
        errors=$((errors + 1))
    fi
    
    if [[ -z "$AVAILABILITY_DOMAIN" ]]; then
        log_error "AVAILABILITY_DOMAIN required"
        errors=$((errors + 1))
    fi
    
    if [[ ! -f "$SSH_KEY_FILE" ]]; then
        log_error "SSH key not found: $SSH_KEY_FILE"
        errors=$((errors + 1))
    fi
    
    return $errors
}

deploy() {
    log_info "Deploying from golden image..."
    echo "  Name: $INSTANCE_NAME"
    echo "  Image: ${GOLDEN_IMAGE_ID:0:40}..."
    echo "  Size: $OCPUS OCPUs, ${MEMORY_GB}GB RAM"
    echo ""
    
    # Create instance
    INSTANCE_OCID=$(oci compute instance launch \
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
        --raw-output 2>&1) || {
        log_error "Failed to create instance: $INSTANCE_OCID"
        exit 1
    }
    
    log_info "Instance created: ${INSTANCE_OCID:0:50}..."
    
    # Get public IP
    sleep 5
    PUBLIC_IP=""
    for i in {1..30}; do
        PUBLIC_IP=$(oci compute instance list-vnics \
            --instance-id "$INSTANCE_OCID" \
            --query 'data[0]."public-ip"' \
            --raw-output 2>/dev/null)
        [[ -n "$PUBLIC_IP" ]] && [[ "$PUBLIC_IP" != "null" ]] && break
        sleep 2
    done
    
    if [[ -z "$PUBLIC_IP" ]] || [[ "$PUBLIC_IP" == "null" ]]; then
        log_error "Could not get public IP"
        exit 1
    fi
    
    log_info "Public IP: $PUBLIC_IP"
    
    # Wait for SSH
    log_info "Waiting for SSH..."
    SSH_KEY_PRIVATE="${SSH_KEY_FILE%.pub}"
    SSH_CMD="ssh -i $SSH_KEY_PRIVATE -o StrictHostKeyChecking=no -o ConnectTimeout=5"
    
    for i in {1..60}; do
        if $SSH_CMD ubuntu@$PUBLIC_IP 'echo ready' &>/dev/null; then
            break
        fi
        sleep 2
    done
    
    log_info "SSH ready"
    
    # Start services (they may be stopped from golden image prep)
    log_info "Starting services..."
    $SSH_CMD ubuntu@$PUBLIC_IP << 'STARTUP_EOF'
sudo systemctl start docker
sleep 2
sudo systemctl start redroid-cloud-phone.target
sleep 5
STARTUP_EOF
    
    # Apply post-deployment configuration
    if [[ -n "$CONFIG_FILE" ]] && [[ -f "$CONFIG_FILE" ]]; then
        log_info "Applying configuration..."
        scp -i "$SSH_KEY_PRIVATE" -o StrictHostKeyChecking=no \
            "$CONFIG_FILE" ubuntu@$PUBLIC_IP:/tmp/config.json
        $SSH_CMD ubuntu@$PUBLIC_IP \
            'sudo cp /tmp/config.json /etc/cloud-phone/config.json'
    fi
    
    # Configure proxy
    if [[ -n "$PROXY_URL" ]]; then
        log_info "Configuring proxy: $PROXY_URL"
        local proxy_type="${PROXY_URL%%://*}"
        local hostport="${PROXY_URL#*://}"
        local host="${hostport%%:*}"
        local port="${hostport##*:}"
        
        $SSH_CMD ubuntu@$PUBLIC_IP \
            "sudo /opt/waydroid-scripts/proxy-control.sh enable $proxy_type $host $port"
    fi
    
    # Configure GPS
    if [[ -n "$GPS_COORDS" ]]; then
        log_info "Configuring GPS: $GPS_COORDS"
        local lat="${GPS_COORDS%%,*}"
        local lon="${GPS_COORDS##*,}"
        
        $SSH_CMD ubuntu@$PUBLIC_IP << GPS_EOF
adb -s 127.0.0.1:5555 wait-for-device
adb -s 127.0.0.1:5555 shell settings put secure mock_location 1
curl -s -X POST http://localhost:8080/location \
    -H "Content-Type: application/json" \
    -d '{"enabled":true,"latitude":$lat,"longitude":$lon}'
GPS_EOF
    fi
    
    # Verify
    log_info "Verifying deployment..."
    CONTAINER_STATUS=$($SSH_CMD ubuntu@$PUBLIC_IP \
        'sudo docker ps --format "{{.Names}}:{{.Status}}" | grep redroid || echo "not running"')
    
    echo ""
    echo -e "${BLUE}========================================${NC}"
    echo "  Deployment Complete!"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    echo "Instance: $INSTANCE_NAME"
    echo "IP:       $PUBLIC_IP"
    echo "OCID:     $INSTANCE_OCID"
    echo "Status:   $CONTAINER_STATUS"
    echo ""
    echo "Connect:"
    echo "  VNC:  ssh -i $SSH_KEY_PRIVATE -L 5900:localhost:5900 ubuntu@$PUBLIC_IP -N"
    echo "  ADB:  adb connect $PUBLIC_IP:5555"
    echo "  API:  ssh -i $SSH_KEY_PRIVATE -L 8080:localhost:8080 ubuntu@$PUBLIC_IP -N"
    echo ""
    
    # Save instance info
    cat > /tmp/instance-$INSTANCE_NAME.json <<EOF
{
  "instance_name": "$INSTANCE_NAME",
  "instance_ocid": "$INSTANCE_OCID",
  "public_ip": "$PUBLIC_IP",
  "golden_image": "$GOLDEN_IMAGE_ID",
  "deployed_at": "$(date -Iseconds)"
}
EOF
}

# Main
parse_args "$@"

if ! validate; then
    exit 1
fi

deploy
