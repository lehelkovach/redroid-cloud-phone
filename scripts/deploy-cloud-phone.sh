#!/bin/bash
# Deploy Cloud Phone with Full Configuration
#
# This script deploys a fully configured Redroid cloud phone to Oracle Cloud
# with support for:
# - Custom instance sizing (OCPUs, memory)
# - Proxy configuration (HTTP, SOCKS5)
# - GPS spoofing
# - Google Play Store (GApps)
# - Custom Redroid images
# - VNC/scrcpy viewing options
#
# Usage:
#   ./deploy-cloud-phone.sh --config config.json
#   ./deploy-cloud-phone.sh --name my-phone --ocpus 2 --memory 8 --proxy socks5://host:port
#   ./deploy-cloud-phone.sh --help
#
# Environment Variables:
#   COMPARTMENT_ID, SUBNET_ID, AVAILABILITY_DOMAIN, SSH_KEY_FILE

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Default values
INSTANCE_NAME="cloud-phone-$(date +%Y%m%d-%H%M%S)"
OCPUS=2
MEMORY_GB=8
OS_VERSION="20.04"
REDROID_IMAGE="redroid/redroid:latest"
REDROID_WIDTH=1280
REDROID_HEIGHT=720
REDROID_FPS=30
VNC_ENABLED=true
VNC_PORT=5900
ADB_PORT=5555
PROXY_ENABLED=false
PROXY_TYPE=""
PROXY_HOST=""
PROXY_PORT=""
PROXY_USER=""
PROXY_PASS=""
GPS_ENABLED=false
GPS_LAT=""
GPS_LON=""
GAPPS_ENABLED=false
GAPPS_VARIANT="pico"
VIEWING_METHOD="vnc"
API_ENABLED=true
API_TOKEN=""
DRY_RUN=false

# OCI defaults (from environment or hardcoded)
COMPARTMENT_ID="${COMPARTMENT_ID:-}"
SUBNET_ID="${SUBNET_ID:-}"
AVAILABILITY_DOMAIN="${AVAILABILITY_DOMAIN:-}"
SSH_KEY_FILE="${SSH_KEY_FILE:-$HOME/.ssh/id_rsa.pub}"

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
Deploy Cloud Phone - Parameterized Redroid Deployment

Usage: $0 [OPTIONS]

Instance Options:
  --name NAME           Instance name (default: cloud-phone-TIMESTAMP)
  --ocpus N             Number of OCPUs (1-4, default: 2)
  --memory N            Memory in GB (1-24, default: 8)
  --os-version VER      Ubuntu version: 20.04 or 22.04 (default: 20.04)

Redroid Options:
  --image IMAGE         Docker image (default: redroid/redroid:latest)
  --width W             Screen width (default: 1280)
  --height H            Screen height (default: 720)
  --fps N               Frames per second (default: 30)
  --vnc-port PORT       VNC port (default: 5900)
  --adb-port PORT       ADB port (default: 5555)
  --no-vnc              Disable VNC (headless mode)

Proxy Options:
  --proxy URL           Proxy URL: socks5://host:port or http://host:port
  --proxy-user USER     Proxy username
  --proxy-pass PASS     Proxy password

GPS Options:
  --gps LAT,LON         GPS coordinates (e.g., 37.7749,-122.4194)

Google Play:
  --gapps               Enable Google Play Store
  --gapps-variant VAR   GApps variant: pico, nano, micro, mini, full (default: pico)

API Options:
  --api-token TOKEN     API authentication token
  --no-api              Disable control API

Viewing Options:
  --viewing METHOD      Viewing method: vnc, scrcpy, webrtc, none (default: vnc)

Configuration:
  --config FILE         Load configuration from JSON file

Other:
  --dry-run             Show what would be deployed without deploying
  --help                Show this help message

Examples:
  # Basic deployment
  $0 --name my-phone

  # High-performance instance with proxy
  $0 --name proxy-phone --ocpus 4 --memory 16 --proxy socks5://proxy.example.com:1080

  # Phone with GPS and Google Play
  $0 --name gps-phone --gps 37.7749,-122.4194 --gapps

  # Headless phone for automation
  $0 --name automation-phone --no-vnc --viewing none

  # From config file
  $0 --config my-phone-config.json

EOF
    exit 0
}

parse_config_file() {
    local file="$1"
    
    if [[ ! -f "$file" ]]; then
        log_error "Config file not found: $file"
        exit 1
    fi
    
    # Parse JSON config (requires jq)
    if ! command -v jq &>/dev/null; then
        log_error "jq required for JSON config parsing"
        exit 1
    fi
    
    # Instance settings
    INSTANCE_NAME=$(jq -r '.instance.name // empty' "$file")
    [[ -n "$(jq -r '.instance.ocpus // empty' "$file")" ]] && OCPUS=$(jq -r '.instance.ocpus' "$file")
    [[ -n "$(jq -r '.instance.memory_gb // empty' "$file")" ]] && MEMORY_GB=$(jq -r '.instance.memory_gb' "$file")
    [[ -n "$(jq -r '.instance.os_version // empty' "$file")" ]] && OS_VERSION=$(jq -r '.instance.os_version' "$file")
    [[ -n "$(jq -r '.instance.compartment_id // empty' "$file")" ]] && COMPARTMENT_ID=$(jq -r '.instance.compartment_id' "$file")
    [[ -n "$(jq -r '.instance.subnet_id // empty' "$file")" ]] && SUBNET_ID=$(jq -r '.instance.subnet_id' "$file")
    [[ -n "$(jq -r '.instance.availability_domain // empty' "$file")" ]] && AVAILABILITY_DOMAIN=$(jq -r '.instance.availability_domain' "$file")
    
    # Redroid settings
    [[ -n "$(jq -r '.redroid.image // empty' "$file")" ]] && REDROID_IMAGE=$(jq -r '.redroid.image' "$file")
    [[ -n "$(jq -r '.redroid.width // empty' "$file")" ]] && REDROID_WIDTH=$(jq -r '.redroid.width' "$file")
    [[ -n "$(jq -r '.redroid.height // empty' "$file")" ]] && REDROID_HEIGHT=$(jq -r '.redroid.height' "$file")
    [[ -n "$(jq -r '.redroid.fps // empty' "$file")" ]] && REDROID_FPS=$(jq -r '.redroid.fps' "$file")
    [[ "$(jq -r '.redroid.vnc_enabled // true' "$file")" == "false" ]] && VNC_ENABLED=false
    [[ -n "$(jq -r '.redroid.vnc_port // empty' "$file")" ]] && VNC_PORT=$(jq -r '.redroid.vnc_port' "$file")
    [[ -n "$(jq -r '.redroid.adb_port // empty' "$file")" ]] && ADB_PORT=$(jq -r '.redroid.adb_port' "$file")
    
    # GApps
    [[ "$(jq -r '.redroid.gapps.enabled // false' "$file")" == "true" ]] && GAPPS_ENABLED=true
    [[ -n "$(jq -r '.redroid.gapps.variant // empty' "$file")" ]] && GAPPS_VARIANT=$(jq -r '.redroid.gapps.variant' "$file")
    
    # Network/Proxy
    if [[ "$(jq -r '.network.proxy.enabled // false' "$file")" == "true" ]]; then
        PROXY_ENABLED=true
        PROXY_TYPE=$(jq -r '.network.proxy.type // "socks5"' "$file")
        PROXY_HOST=$(jq -r '.network.proxy.host // empty' "$file")
        PROXY_PORT=$(jq -r '.network.proxy.port // empty' "$file")
        PROXY_USER=$(jq -r '.network.proxy.username // empty' "$file")
        PROXY_PASS=$(jq -r '.network.proxy.password // empty' "$file")
    fi
    
    # GPS
    if [[ "$(jq -r '.location.enabled // false' "$file")" == "true" ]]; then
        GPS_ENABLED=true
        GPS_LAT=$(jq -r '.location.latitude // empty' "$file")
        GPS_LON=$(jq -r '.location.longitude // empty' "$file")
    fi
    
    # API
    [[ "$(jq -r '.api.enabled // true' "$file")" == "false" ]] && API_ENABLED=false
    [[ -n "$(jq -r '.api.auth.token // empty' "$file")" ]] && API_TOKEN=$(jq -r '.api.auth.token' "$file")
    
    # Viewing
    [[ -n "$(jq -r '.viewing.method // empty' "$file")" ]] && VIEWING_METHOD=$(jq -r '.viewing.method' "$file")
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --name) INSTANCE_NAME="$2"; shift 2 ;;
            --ocpus) OCPUS="$2"; shift 2 ;;
            --memory) MEMORY_GB="$2"; shift 2 ;;
            --os-version) OS_VERSION="$2"; shift 2 ;;
            --image) REDROID_IMAGE="$2"; shift 2 ;;
            --width) REDROID_WIDTH="$2"; shift 2 ;;
            --height) REDROID_HEIGHT="$2"; shift 2 ;;
            --fps) REDROID_FPS="$2"; shift 2 ;;
            --vnc-port) VNC_PORT="$2"; shift 2 ;;
            --adb-port) ADB_PORT="$2"; shift 2 ;;
            --no-vnc) VNC_ENABLED=false; shift ;;
            --proxy)
                PROXY_ENABLED=true
                # Parse URL: type://host:port
                local url="$2"
                PROXY_TYPE="${url%%://*}"
                local hostport="${url#*://}"
                PROXY_HOST="${hostport%%:*}"
                PROXY_PORT="${hostport##*:}"
                shift 2
                ;;
            --proxy-user) PROXY_USER="$2"; shift 2 ;;
            --proxy-pass) PROXY_PASS="$2"; shift 2 ;;
            --gps)
                GPS_ENABLED=true
                GPS_LAT="${2%%,*}"
                GPS_LON="${2##*,}"
                shift 2
                ;;
            --gapps) GAPPS_ENABLED=true; shift ;;
            --gapps-variant) GAPPS_VARIANT="$2"; shift 2 ;;
            --api-token) API_TOKEN="$2"; shift 2 ;;
            --no-api) API_ENABLED=false; shift ;;
            --viewing) VIEWING_METHOD="$2"; shift 2 ;;
            --config) parse_config_file "$2"; shift 2 ;;
            --dry-run) DRY_RUN=true; shift ;;
            --help|-h) usage ;;
            *) log_error "Unknown option: $1"; usage ;;
        esac
    done
}

validate_config() {
    local errors=0
    
    # Check OCI CLI
    if ! command -v oci &>/dev/null; then
        log_error "OCI CLI not installed"
        errors=$((errors + 1))
    fi
    
    # Check required OCI params
    if [[ -z "$COMPARTMENT_ID" ]]; then
        log_error "COMPARTMENT_ID required (set env or use --config)"
        errors=$((errors + 1))
    fi
    
    if [[ -z "$SUBNET_ID" ]]; then
        log_error "SUBNET_ID required (set env or use --config)"
        errors=$((errors + 1))
    fi
    
    # Check SSH key
    if [[ ! -f "$SSH_KEY_FILE" ]]; then
        log_error "SSH public key not found: $SSH_KEY_FILE"
        errors=$((errors + 1))
    fi
    
    # Validate ranges
    if [[ "$OCPUS" -lt 1 ]] || [[ "$OCPUS" -gt 4 ]]; then
        log_error "OCPUs must be 1-4 (Always Free tier limit)"
        errors=$((errors + 1))
    fi
    
    if [[ "$MEMORY_GB" -lt 1 ]] || [[ "$MEMORY_GB" -gt 24 ]]; then
        log_error "Memory must be 1-24 GB (Always Free tier limit)"
        errors=$((errors + 1))
    fi
    
    if [[ "$OS_VERSION" != "20.04" ]] && [[ "$OS_VERSION" != "22.04" ]]; then
        log_error "OS version must be 20.04 or 22.04"
        errors=$((errors + 1))
    fi
    
    # Validate proxy if enabled
    if [[ "$PROXY_ENABLED" == "true" ]]; then
        if [[ -z "$PROXY_HOST" ]] || [[ -z "$PROXY_PORT" ]]; then
            log_error "Proxy enabled but host/port not specified"
            errors=$((errors + 1))
        fi
    fi
    
    return $errors
}

show_config() {
    log_header "Deployment Configuration"
    echo ""
    echo "Instance:"
    echo "  Name:     $INSTANCE_NAME"
    echo "  OCPUs:    $OCPUS"
    echo "  Memory:   ${MEMORY_GB}GB"
    echo "  OS:       Ubuntu $OS_VERSION"
    echo ""
    echo "Redroid:"
    echo "  Image:    $REDROID_IMAGE"
    echo "  Screen:   ${REDROID_WIDTH}x${REDROID_HEIGHT}@${REDROID_FPS}fps"
    echo "  VNC:      $VNC_ENABLED (port $VNC_PORT)"
    echo "  ADB:      port $ADB_PORT"
    echo "  GApps:    $GAPPS_ENABLED ($GAPPS_VARIANT)"
    echo ""
    echo "Network:"
    if [[ "$PROXY_ENABLED" == "true" ]]; then
        echo "  Proxy:    $PROXY_TYPE://$PROXY_HOST:$PROXY_PORT"
    else
        echo "  Proxy:    disabled"
    fi
    echo ""
    echo "Location:"
    if [[ "$GPS_ENABLED" == "true" ]]; then
        echo "  GPS:      $GPS_LAT, $GPS_LON"
    else
        echo "  GPS:      disabled"
    fi
    echo ""
    echo "API:"
    echo "  Enabled:  $API_ENABLED"
    echo "  Auth:     $([ -n "$API_TOKEN" ] && echo "enabled" || echo "disabled")"
    echo ""
    echo "Viewing:    $VIEWING_METHOD"
    echo ""
}

create_instance_config() {
    # Generate the cloud-phone config.json for the remote instance
    cat <<EOF
{
  "redroid": {
    "image": "$REDROID_IMAGE",
    "width": $REDROID_WIDTH,
    "height": $REDROID_HEIGHT,
    "fps": $REDROID_FPS,
    "vnc_enabled": $VNC_ENABLED,
    "vnc_port": $VNC_PORT,
    "adb_port": $ADB_PORT,
    "gapps": {
      "enabled": $GAPPS_ENABLED,
      "variant": "$GAPPS_VARIANT"
    }
  },
  "network": {
    "proxy": {
      "enabled": $PROXY_ENABLED,
      "type": "$PROXY_TYPE",
      "host": "$PROXY_HOST",
      "port": ${PROXY_PORT:-0},
      "username": "$PROXY_USER",
      "password": "$PROXY_PASS"
    }
  },
  "location": {
    "enabled": $GPS_ENABLED,
    "latitude": ${GPS_LAT:-0},
    "longitude": ${GPS_LON:-0}
  },
  "api": {
    "enabled": $API_ENABLED,
    "auth": {
      "enabled": $([ -n "$API_TOKEN" ] && echo true || echo false),
      "token": "$API_TOKEN"
    }
  },
  "viewing": {
    "method": "$VIEWING_METHOD"
  }
}
EOF
}

deploy() {
    log_header "Starting Deployment"
    echo ""
    
    # Step 1: Find image
    log_info "Finding Ubuntu $OS_VERSION ARM image..."
    local image_id=$(oci compute image list \
        --compartment-id "$COMPARTMENT_ID" \
        --operating-system "Canonical Ubuntu" \
        --operating-system-version "$OS_VERSION" \
        --shape "VM.Standard.A1.Flex" \
        --query 'data[0].id' \
        --raw-output 2>/dev/null)
    
    if [[ -z "$image_id" ]]; then
        log_error "Ubuntu $OS_VERSION ARM image not found"
        exit 1
    fi
    log_info "Found image: ${image_id:0:50}..."
    
    # Step 2: Create instance
    log_info "Creating instance: $INSTANCE_NAME"
    local instance_id=$(oci compute instance launch \
        --compartment-id "$COMPARTMENT_ID" \
        --availability-domain "$AVAILABILITY_DOMAIN" \
        --shape "VM.Standard.A1.Flex" \
        --shape-config "{\"ocpus\":$OCPUS,\"memoryInGBs\":$MEMORY_GB}" \
        --image-id "$image_id" \
        --subnet-id "$SUBNET_ID" \
        --display-name "$INSTANCE_NAME" \
        --ssh-authorized-keys-file "$SSH_KEY_FILE" \
        --assign-public-ip true \
        --wait-for-state RUNNING \
        --query 'data.id' \
        --raw-output 2>&1) || {
        log_error "Failed to create instance: $instance_id"
        exit 1
    }
    log_info "Instance created: ${instance_id:0:50}..."
    
    # Step 3: Get public IP
    sleep 5
    local public_ip=""
    for i in {1..30}; do
        public_ip=$(oci compute instance list-vnics \
            --instance-id "$instance_id" \
            --query 'data[0]."public-ip"' \
            --raw-output 2>/dev/null)
        [[ -n "$public_ip" ]] && [[ "$public_ip" != "null" ]] && break
        sleep 2
    done
    
    if [[ -z "$public_ip" ]] || [[ "$public_ip" == "null" ]]; then
        log_error "Could not get public IP"
        exit 1
    fi
    log_info "Public IP: $public_ip"
    
    # Step 4: Wait for SSH
    log_info "Waiting for SSH..."
    local ssh_key="${SSH_KEY_FILE%.pub}"
    local ssh_cmd="ssh -i $ssh_key -o StrictHostKeyChecking=no -o ConnectTimeout=5"
    
    for i in {1..60}; do
        if $ssh_cmd ubuntu@$public_ip 'echo ready' &>/dev/null; then
            log_info "SSH ready"
            break
        fi
        sleep 2
    done
    
    # Step 5: Upload project files
    log_info "Uploading project files..."
    local tarball=$(mktemp)
    cd "$PROJECT_ROOT"
    tar czf "$tarball" \
        --exclude='.git' \
        --exclude='*.pyc' \
        --exclude='__pycache__' \
        install-redroid.sh \
        install.sh \
        scripts/ \
        api/ \
        systemd/ \
        config/
    
    scp -i "$ssh_key" -o StrictHostKeyChecking=no "$tarball" ubuntu@$public_ip:/tmp/cloud-phone.tar.gz
    rm "$tarball"
    
    # Generate and upload config
    local config_json=$(create_instance_config)
    echo "$config_json" | $ssh_cmd ubuntu@$public_ip 'sudo mkdir -p /etc/cloud-phone && sudo tee /etc/cloud-phone/config.json > /dev/null'
    
    # Step 6: Run installation
    log_info "Running installation..."
    $ssh_cmd ubuntu@$public_ip << ENDSSH
set -e
cd /tmp
rm -rf cloud-phone-deploy
mkdir -p cloud-phone-deploy
tar xzf cloud-phone.tar.gz -C cloud-phone-deploy
cd cloud-phone-deploy

# Run installer
sudo ./install-redroid.sh

# Configure Redroid with custom settings
sudo mkdir -p /opt/redroid-data
sudo tee /opt/redroid-env.conf > /dev/null <<EOF2
REDROID_IMAGE=$REDROID_IMAGE
REDROID_WIDTH=$REDROID_WIDTH
REDROID_HEIGHT=$REDROID_HEIGHT
REDROID_FPS=$REDROID_FPS
REDROID_VNC_ENABLED=$([ "$VNC_ENABLED" == "true" ] && echo 1 || echo 0)
REDROID_VNC_PORT=$VNC_PORT
REDROID_ADB_PORT=$ADB_PORT
EOF2

# Install GApps if enabled
if [ "$GAPPS_ENABLED" = "true" ]; then
    echo "Installing Google Play Store (GApps $GAPPS_VARIANT)..."
    sudo /opt/waydroid-scripts/install-gapps.sh "$GAPPS_VARIANT" || echo "GApps installation may require manual setup"
fi

# Start services
sudo systemctl daemon-reload
sudo systemctl start redroid-cloud-phone.target

# Wait for boot
echo "Waiting for Redroid to boot..."
sleep 30

# Configure proxy if enabled
if [ "$PROXY_ENABLED" = "true" ]; then
    echo "Configuring proxy..."
    sudo /opt/waydroid-scripts/proxy-control.sh enable "$PROXY_TYPE" "$PROXY_HOST" "$PROXY_PORT" "$PROXY_USER" "$PROXY_PASS" || true
fi

# Configure GPS if enabled
if [ "$GPS_ENABLED" = "true" ]; then
    echo "Configuring GPS..."
    adb -s 127.0.0.1:5555 shell settings put secure mock_location 1 || true
fi

# Verify
docker ps | grep redroid || echo "Warning: Container may not be running"
ENDSSH

    log_info "Installation complete"
    
    # Step 7: Summary
    echo ""
    log_header "Deployment Complete"
    echo ""
    echo "Instance: $INSTANCE_NAME"
    echo "IP:       $public_ip"
    echo "OCID:     $instance_id"
    echo ""
    echo "Connect:"
    echo "  VNC:  ssh -i $ssh_key -L $VNC_PORT:localhost:$VNC_PORT ubuntu@$public_ip -N"
    echo "        vncviewer localhost:$VNC_PORT"
    echo ""
    echo "  ADB:  adb connect $public_ip:$ADB_PORT"
    echo ""
    echo "  API:  ssh -i $ssh_key -L 8080:localhost:8080 ubuntu@$public_ip -N"
    echo "        curl http://localhost:8080/health"
    echo ""
    echo "Health Check:"
    echo "  ssh -i $ssh_key ubuntu@$public_ip 'sudo /opt/waydroid-scripts/health-check.sh'"
    echo ""
    
    # Save instance info
    cat > /tmp/cloud-phone-$INSTANCE_NAME.json <<EOF
{
  "instance_name": "$INSTANCE_NAME",
  "instance_id": "$instance_id",
  "public_ip": "$public_ip",
  "ssh_key": "$ssh_key",
  "config": $(create_instance_config)
}
EOF
    log_info "Instance info saved to /tmp/cloud-phone-$INSTANCE_NAME.json"
}

# Main
parse_args "$@"

if ! validate_config; then
    log_error "Configuration validation failed"
    exit 1
fi

show_config

if [[ "$DRY_RUN" == "true" ]]; then
    log_info "Dry run - no deployment performed"
    echo ""
    echo "Generated config:"
    create_instance_config
    exit 0
fi

read -p "Deploy with this configuration? [y/N] " confirm
if [[ "$confirm" != "y" ]] && [[ "$confirm" != "Y" ]]; then
    log_info "Deployment cancelled"
    exit 0
fi

deploy
