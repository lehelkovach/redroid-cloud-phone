#!/bin/bash
# get-oci-config.sh
# Helper script to discover OCIDs needed for launch-fleet.sh
#
# Usage: ./get-oci-config.sh

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=========================================="
echo "OCI Configuration Discovery"
echo "==========================================${NC}"
echo ""

# Check if OCI CLI is installed
if ! command -v oci &> /dev/null; then
    echo -e "${YELLOW}Error: OCI CLI is not installed.${NC}"
    echo "Install from: https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm"
    exit 1
fi

# Check if OCI CLI is configured
if ! oci iam region list &>/dev/null; then
    echo -e "${YELLOW}Error: OCI CLI is not configured.${NC}"
    echo "Run: oci setup config"
    exit 1
fi

echo -e "${GREEN}[1] Compartments${NC}"
echo "-------------------"
oci iam compartment list --all --query 'data[*].[name,id]' --output table
echo ""
echo -e "${YELLOW}Copy the COMPARTMENT_ID (ocid1.compartment.oc1..xxx)${NC}"
echo ""

# Get default compartment if available
DEFAULT_COMP=$(oci iam compartment list --all --query 'data[?name==`root`].id | [0]' --raw-output 2>/dev/null || echo "")
if [[ -n "$DEFAULT_COMP" ]]; then
    echo -e "${GREEN}Default (root) compartment:${NC} $DEFAULT_COMP"
    echo ""
fi

echo -e "${GREEN}[2] Availability Domains${NC}"
echo "-------------------"
oci iam availability-domain list --query 'data[*].[name]' --output table
echo ""
echo -e "${YELLOW}Copy the full AD names (e.g., XXX:US-ASHBURN-AD-1)${NC}"
echo ""

echo -e "${GREEN}[3] Virtual Cloud Networks (VCNs)${NC}"
echo "-------------------"
if [[ -n "${DEFAULT_COMP:-}" ]]; then
    echo "Listing VCNs in root compartment..."
    oci network vcn list --compartment-id "$DEFAULT_COMP" --query 'data[*].[display-name,id,"lifecycle-state"]' --output table 2>/dev/null || echo "No VCNs found or insufficient permissions"
else
    echo -e "${YELLOW}Note: Specify a compartment-id to list VCNs${NC}"
    echo "Example: oci network vcn list --compartment-id YOUR_COMPARTMENT_ID"
fi
echo ""

echo -e "${GREEN}[4] Subnets${NC}"
echo "-------------------"
if [[ -n "${DEFAULT_COMP:-}" ]]; then
    echo "Listing subnets in root compartment..."
    oci network subnet list --compartment-id "$DEFAULT_COMP" --query 'data[*].[display-name,id,"lifecycle-state"]' --output table 2>/dev/null || echo "No subnets found or insufficient permissions"
else
    echo -e "${YELLOW}Note: Specify a compartment-id to list subnets${NC}"
    echo "Example: oci network subnet list --compartment-id YOUR_COMPARTMENT_ID"
fi
echo ""

echo -e "${GREEN}[5] Custom Images (Golden Images)${NC}"
echo "-------------------"
if [[ -n "${DEFAULT_COMP:-}" ]]; then
    echo "Listing custom images in root compartment..."
    oci compute image list --compartment-id "$DEFAULT_COMP" --operating-system "Custom" --query 'data[*].[display-name,id,"lifecycle-state"]' --output table 2>/dev/null || echo "No custom images found"
else
    echo -e "${YELLOW}Note: Specify a compartment-id to list images${NC}"
    echo "Example: oci compute image list --compartment-id YOUR_COMPARTMENT_ID --operating-system Custom"
fi
echo ""

echo -e "${GREEN}[6] Existing Instances${NC}"
echo "-------------------"
if [[ -n "${DEFAULT_COMP:-}" ]]; then
    echo "Listing instances in root compartment..."
    oci compute instance list --compartment-id "$DEFAULT_COMP" --query 'data[*].[display-name,id,"lifecycle-state"]' --output table 2>/dev/null || echo "No instances found"
else
    echo -e "${YELLOW}Note: Specify a compartment-id to list instances${NC}"
    echo "Example: oci compute instance list --compartment-id YOUR_COMPARTMENT_ID"
fi
echo ""

echo -e "${BLUE}=========================================="
echo "Configuration Template"
echo "==========================================${NC}"
echo ""
echo "Copy these into scripts/launch-fleet.sh:"
echo ""
echo "IMAGE_ID=\"ocid1.image.oc1.iad.xxx\""
echo "COMPARTMENT_ID=\"ocid1.compartment.oc1..xxx\""
echo "SUBNET_ID=\"ocid1.subnet.oc1.iad.xxx\""
echo "SSH_KEY_FILE=\"\${HOME}/.ssh/waydroid_oci.pub\""
echo ""
echo "AVAILABILITY_DOMAINS=("
echo "    \"XXX:US-ASHBURN-AD-1\""
echo "    \"XXX:US-ASHBURN-AD-2\""
echo "    \"XXX:US-ASHBURN-AD-3\""
echo ")"
echo ""

