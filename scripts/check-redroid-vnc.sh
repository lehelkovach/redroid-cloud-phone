#!/bin/bash
# Check Redroid VNC status and provide correct connection info

set -euo pipefail

INSTANCE_IP="${1:-137.131.52.69}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/waydroid_oci}"

echo "=========================================="
echo "  Redroid VNC Status Check"
echo "=========================================="
echo ""

ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP << 'ENDSSH'
set -euo pipefail

echo "=== Redroid Container Status ==="
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "redroid|NAMES" || echo "Redroid not running"
echo ""

echo "=== Checking Port Mappings ==="
docker port redroid 2>/dev/null || echo "Container not found"
echo ""

echo "=== Checking VNC Process Inside Container ==="
VNC_PID=$(docker exec redroid sh -c 'pgrep -f vnc' 2>/dev/null || echo "")
if [[ -n "$VNC_PID" ]]; then
    echo "✓ VNC process running (PID: $VNC_PID)"
    docker exec redroid sh -c 'ps aux | grep vnc | grep -v grep' 2>/dev/null || true
else
    echo "✗ VNC process not found inside container"
fi
echo ""

echo "=== Checking Listening Ports ==="
echo "Host ports:"
sudo ss -tlnp | grep -E "5900|5901" || echo "  No VNC ports listening on host"
echo ""

echo "Container ports:"
docker exec redroid sh -c 'netstat -tlnp 2>/dev/null | grep -E "5900|5901" || ss -tlnp 2>/dev/null | grep -E "5900|5901" || echo "  Cannot check container ports"' || echo "  Cannot check container ports"
echo ""

echo "=== Redroid Container Logs (last 10 lines) ==="
docker logs redroid --tail 10 2>&1 | grep -i vnc || echo "  No VNC-related logs"
echo ""

echo "=== Testing VNC Connection from Host ==="
if timeout 2 bash -c 'echo > /dev/tcp/localhost/5900' 2>&1; then
    echo "✓ Port 5900 is accessible from host"
else
    echo "✗ Port 5900 not accessible from host"
fi
echo ""

ENDSSH

echo ""
echo "=========================================="
echo "  Connection Instructions"
echo "=========================================="
echo ""
echo "If Redroid is running with -p 5900:5900:"
echo ""
echo "  SSH Tunnel (correct):"
echo "    ssh -i $SSH_KEY -L 5900:localhost:5900 ubuntu@$INSTANCE_IP -N"
echo ""
echo "  Then connect:"
echo "    vncviewer localhost:5900"
echo ""
echo "  Password: redroid"
echo ""
echo "If VNC is not working, Redroid may need to be restarted with VNC enabled."
echo ""


