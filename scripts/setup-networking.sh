#!/bin/bash
# setup-networking.sh
# Creates a VCN and subnet for waydroid instances
#
# Usage: ./setup-networking.sh [vcn-name] [subnet-name]

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

VCN_NAME="${1:-waydroid-vcn}"
SUBNET_NAME="${2:-waydroid-subnet}"

# Load configuration (only get compartment, don't validate image)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Try root compartment (tenancy) first, as it has permissions to create VCNs
# Get tenancy ID from OCI config
TENANCY_ID=$(grep "^tenancy=" ~/.oci/config 2>/dev/null | cut -d'=' -f2 || echo "")
# Fallback to compartment if tenancy not found
COMPARTMENT_ID="${TENANCY_ID:-ocid1.tenancy.oc1..aaaaaaaak44wevthunqrdp6h6noor4o5t34a7jqejla6wd22v47admhyzoca}"
# Try to get from launch-fleet.sh without running validation
if [[ -f "$SCRIPT_DIR/launch-fleet.sh" ]] && [[ -z "$TENANCY_ID" ]]; then
    COMPARTMENT_ID=$(grep "^COMPARTMENT_ID=" "$SCRIPT_DIR/launch-fleet.sh" | head -1 | cut -d'"' -f2 || echo "$COMPARTMENT_ID")
fi

echo -e "${BLUE}=========================================="
echo "Setting Up Networking"
echo "==========================================${NC}"
echo "VCN Name: $VCN_NAME"
echo "Subnet Name: $SUBNET_NAME"
echo "Compartment: $COMPARTMENT_ID"
echo ""

# Check if VCN already exists
EXISTING_VCN=$(oci network vcn list \
    --compartment-id "$COMPARTMENT_ID" \
    --display-name "$VCN_NAME" \
    --query 'data[0].id' \
    --raw-output 2>/dev/null || echo "")

if [[ -n "$EXISTING_VCN" ]] && [[ "$EXISTING_VCN" != "null" ]]; then
    echo -e "${YELLOW}VCN already exists: $EXISTING_VCN${NC}"
    VCN_ID="$EXISTING_VCN"
else
    echo -e "${BLUE}Creating VCN...${NC}"
    VCN_ID=$(oci network vcn create \
        --compartment-id "$COMPARTMENT_ID" \
        --display-name "$VCN_NAME" \
        --cidr-block "10.0.0.0/16" \
        --dns-label "waydroid" \
        --wait-for-state AVAILABLE \
        --query 'data.id' \
        --raw-output)
    
    if [[ -z "$VCN_ID" ]]; then
        echo -e "${RED}Error: Failed to create VCN${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ VCN created: $VCN_ID${NC}"
fi

# Create Internet Gateway
echo -e "${BLUE}Creating Internet Gateway...${NC}"
IGW_ID=$(oci network internet-gateway list \
    --compartment-id "$COMPARTMENT_ID" \
    --vcn-id "$VCN_ID" \
    --query 'data[0].id' \
    --raw-output 2>/dev/null || echo "")

if [[ -z "$IGW_ID" ]] || [[ "$IGW_ID" == "null" ]]; then
    IGW_ID=$(oci network internet-gateway create \
        --compartment-id "$COMPARTMENT_ID" \
        --vcn-id "$VCN_ID" \
        --display-name "${VCN_NAME}-igw" \
        --is-enabled true \
        --wait-for-state AVAILABLE \
        --query 'data.id' \
        --raw-output)
    echo -e "${GREEN}✓ Internet Gateway created: $IGW_ID${NC}"
else
    echo -e "${YELLOW}Internet Gateway already exists: $IGW_ID${NC}"
fi

# Get default route table
ROUTE_TABLE_ID=$(oci network vcn get \
    --vcn-id "$VCN_ID" \
    --query 'data."default-route-table-id"' \
    --raw-output)

# Add route to Internet Gateway
echo -e "${BLUE}Configuring route table...${NC}"
oci network route-table update \
    --rt-id "$ROUTE_TABLE_ID" \
    --route-rules '[{"destination": "0.0.0.0/0", "destinationType": "CIDR_BLOCK", "networkEntityId": "'"$IGW_ID"'"}]' \
    --force \
    &>/dev/null || echo -e "${YELLOW}Route may already be configured${NC}"

# Get default security list
SECURITY_LIST_ID=$(oci network vcn get \
    --vcn-id "$VCN_ID" \
    --query 'data."default-security-list-id"' \
    --raw-output)

# Add ingress rules for SSH and RTMP
echo -e "${BLUE}Configuring security list...${NC}"
oci network security-list update \
    --security-list-id "$SECURITY_LIST_ID" \
    --ingress-security-rules '[
        {"source": "0.0.0.0/0", "protocol": "6", "tcpOptions": {"destinationPortRange": {"min": 22, "max": 22}}},
        {"source": "0.0.0.0/0", "protocol": "6", "tcpOptions": {"destinationPortRange": {"min": 1935, "max": 1935}}}
    ]' \
    --force \
    &>/dev/null || echo -e "${YELLOW}Security rules may already be configured${NC}"

# Check if subnet exists
EXISTING_SUBNET=$(oci network subnet list \
    --compartment-id "$COMPARTMENT_ID" \
    --vcn-id "$VCN_ID" \
    --display-name "$SUBNET_NAME" \
    --query 'data[0].id' \
    --raw-output 2>/dev/null || echo "")

if [[ -n "$EXISTING_SUBNET" ]] && [[ "$EXISTING_SUBNET" != "null" ]]; then
    echo -e "${YELLOW}Subnet already exists: $EXISTING_SUBNET${NC}"
    SUBNET_ID="$EXISTING_SUBNET"
else
    # Get availability domain for subnet
    AD=$(oci iam availability-domain list --query 'data[0].name' --raw-output)
    
    echo -e "${BLUE}Creating subnet...${NC}"
    SUBNET_ID=$(oci network subnet create \
        --compartment-id "$COMPARTMENT_ID" \
        --vcn-id "$VCN_ID" \
        --display-name "$SUBNET_NAME" \
        --cidr-block "10.0.1.0/24" \
        --availability-domain "$AD" \
        --dns-label "waydroid" \
        --wait-for-state AVAILABLE \
        --query 'data.id' \
        --raw-output)
    
    if [[ -z "$SUBNET_ID" ]]; then
        echo -e "${RED}Error: Failed to create subnet${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ Subnet created: $SUBNET_ID${NC}"
fi

echo ""
echo -e "${GREEN}=========================================="
echo "Networking Setup Complete!"
echo "==========================================${NC}"
echo ""
echo "VCN ID: $VCN_ID"
echo "Subnet ID: $SUBNET_ID"
echo ""
echo "Update launch-fleet.sh with:"
echo "  SUBNET_ID=\"$SUBNET_ID\""
echo ""
echo "Or export it:"
echo "  export SUBNET_ID=\"$SUBNET_ID\""
echo ""

