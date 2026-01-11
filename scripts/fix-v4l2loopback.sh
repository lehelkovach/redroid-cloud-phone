#!/bin/bash
# fix-v4l2loopback.sh
# Fixes v4l2loopback build issue on kernel 6.8+ (strlcpy -> strscpy)
#
# Usage: Run on instance before creating golden image

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=========================================="
echo "Fixing v4l2loopback for Kernel 6.8+"
echo "==========================================${NC}"
echo ""

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}This script must be run as root (use sudo)${NC}"
   exit 1
fi

KERNEL_VERSION=$(uname -r)
echo -e "${BLUE}Kernel version: $KERNEL_VERSION${NC}"

# Check if v4l2loopback is already working
if lsmod | grep -q v4l2loopback; then
    echo -e "${GREEN}✓ v4l2loopback module is already loaded${NC}"
    if [ -e /dev/video42 ]; then
        echo -e "${GREEN}✓ /dev/video42 exists${NC}"
        echo -e "${GREEN}v4l2loopback is working!${NC}"
        exit 0
    fi
fi

# Remove broken DKMS installation
echo -e "${BLUE}Removing broken v4l2loopback-dkms...${NC}"
apt-get remove -y v4l2loopback-dkms 2>/dev/null || true
apt-get purge -y v4l2loopback-dkms 2>/dev/null || true
rm -rf /var/lib/dkms/v4l2loopback 2>/dev/null || true

# Install build dependencies
echo -e "${BLUE}Installing build dependencies...${NC}"
apt-get update
apt-get install -y \
    build-essential \
    linux-headers-${KERNEL_VERSION} \
    git \
    dkms

# Build from source with fix
echo -e "${BLUE}Building v4l2loopback from source...${NC}"
cd /tmp
rm -rf v4l2loopback

# Clone latest v4l2loopback (has kernel 6.8+ fixes)
git clone https://github.com/umlaeute/v4l2loopback.git
cd v4l2loopback

# Check if we need to apply strlcpy fix
if grep -q "strlcpy" v4l2loopback.c 2>/dev/null; then
    echo -e "${YELLOW}Applying strlcpy -> strscpy patch...${NC}"
    # Replace strlcpy with strscpy (kernel 6.8+ compatible)
    sed -i 's/strlcpy/strscpy/g' v4l2loopback.c
    
    # Add strscpy include if not present
    if ! grep -q "#include <linux/string.h>" v4l2loopback.c; then
        sed -i '/^#include/a #include <linux/string.h>' v4l2loopback.c
    fi
fi

# Build and install
echo -e "${BLUE}Building module...${NC}"
make clean || true
make

echo -e "${BLUE}Installing module...${NC}"
make install
depmod -a

# Load module
echo -e "${BLUE}Loading v4l2loopback module...${NC}"
modprobe v4l2loopback devices=1 video_nr=42 card_label="VirtualCam" exclusive_caps=1

# Verify
if [ -e /dev/video42 ]; then
    echo -e "${GREEN}✓ /dev/video42 created successfully${NC}"
    echo -e "${GREEN}✓ v4l2loopback is working!${NC}"
    
    # Make it persistent
    echo "v4l2loopback" > /etc/modules-load.d/v4l2loopback.conf
    echo 'options v4l2loopback devices=1 video_nr=42 card_label="VirtualCam" exclusive_caps=1' > /etc/modprobe.d/v4l2loopback.conf
    
    echo ""
    echo -e "${GREEN}=========================================="
    echo "v4l2loopback Fixed Successfully!"
    echo "==========================================${NC}"
    echo ""
    echo "Module will load automatically on boot."
    exit 0
else
    echo -e "${RED}✗ Failed to create /dev/video42${NC}"
    echo "Check dmesg for errors:"
    dmesg | tail -20
    exit 1
fi

