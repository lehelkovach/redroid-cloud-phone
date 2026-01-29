#!/bin/bash
# Redroid Cloud Phone Installer (wrapper)
#
# This script delegates to install-redroid.sh, the supported installer
# for Redroid deployments.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root (use sudo)" >&2
    exit 1
fi

echo "========================================"
echo "  Redroid Cloud Phone Installer"
echo "========================================"
echo ""
echo "Redirecting to install-redroid.sh..."
echo ""

exec "$SCRIPT_DIR/install-redroid.sh"
