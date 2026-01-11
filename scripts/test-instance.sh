#!/bin/bash
# test-instance.sh
# Quick test of a deployed waydroid instance
# For comprehensive tests, use: ./test-full-suite.sh <PUBLIC_IP>
#
# Usage: ./test-instance.sh <PUBLIC_IP> [SSH_KEY]

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <PUBLIC_IP> [SSH_KEY]"
    echo ""
    echo "For comprehensive tests including streaming and automation:"
    echo "  ./scripts/test-full-suite.sh <PUBLIC_IP> [SSH_KEY]"
    exit 1
fi

PUBLIC_IP="$1"
SSH_KEY="${2:-${HOME}/.ssh/waydroid_oci}"
SSH_USER="ubuntu"

echo -e "${BLUE}=========================================="
echo "Quick Instance Test"
echo "==========================================${NC}"
echo "IP: $PUBLIC_IP"
echo ""
echo "For comprehensive tests, run:"
echo "  ./scripts/test-full-suite.sh $PUBLIC_IP"
echo ""

# Run system tests remotely
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" "[ -f /opt/waydroid-scripts/test-system.sh ]"; then
    echo -e "${BLUE}Running system tests on instance...${NC}"
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" "sudo /opt/waydroid-scripts/test-system.sh"
else
    echo -e "${YELLOW}System test script not found on instance, running basic checks...${NC}"
    
    # Basic service checks
    SERVICES=("nginx-rtmp" "xvnc" "waydroid-container" "waydroid-session" "ffmpeg-bridge" "control-api")
    FAILED=0
    
    for service in "${SERVICES[@]}"; do
        if ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" "systemctl is-active --quiet $service" 2>/dev/null; then
            echo -e "  ${GREEN}✓${NC} $service"
        else
            echo -e "  ${RED}✗${NC} $service"
            FAILED=$((FAILED + 1))
        fi
    done
fi

echo ""
echo -e "${BLUE}For full test suite including:${NC}"
echo "  - Installation verification"
echo "  - API automation commands"
echo "  - RTMP streaming tests"
echo "  - Service restart/recovery"
echo "  - Network connectivity"
echo ""
echo "Run: ./scripts/test-full-suite.sh $PUBLIC_IP"
echo ""

