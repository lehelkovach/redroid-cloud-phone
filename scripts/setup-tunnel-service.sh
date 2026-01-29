#!/bin/bash
# setup-tunnel-service.sh
# Sets up systemd user service for persistent SSH tunnel
#
# Usage: ./setup-tunnel-service.sh [INSTANCE_IP]

set -euo pipefail

INSTANCE_IP="${1:-137.131.52.69}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Setting up persistent SSH tunnel service..."
echo "Instance IP: $INSTANCE_IP"
echo ""

# Create systemd user directory
mkdir -p ~/.config/systemd/user

# Create service file
cat > ~/.config/systemd/user/redroid-tunnel.service << EOF
[Unit]
Description=SSH Tunnel for Redroid VNC and API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/ssh -i $HOME/.ssh/redroid_oci -o StrictHostKeyChecking=no -o ServerAliveInterval=60 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -N -L 5900:localhost:5900 -L 8080:localhost:8080 ubuntu@${INSTANCE_IP}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

# Reload systemd
systemctl --user daemon-reload

echo "Service created: ~/.config/systemd/user/redroid-tunnel.service"
echo ""
echo "To enable (start on login):"
echo "  systemctl --user enable redroid-tunnel.service"
echo ""
echo "To start now:"
echo "  systemctl --user start redroid-tunnel.service"
echo ""
echo "To check status:"
echo "  systemctl --user status redroid-tunnel.service"
echo ""
echo "To view logs:"
echo "  journalctl --user -u redroid-tunnel.service -f"

