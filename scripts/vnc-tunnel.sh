#!/bin/bash
# vnc-tunnel.sh
# Manages SSH tunnel for VNC, API, and ADB access
#
# Usage:
#   Start:    ./vnc-tunnel.sh start [INSTANCE_IP] [SSH_KEY]
#   Stop:     ./vnc-tunnel.sh stop
#   Status:   ./vnc-tunnel.sh status
#   Restart:  ./vnc-tunnel.sh restart [INSTANCE_IP]
#   Persist:  ./vnc-tunnel.sh persist [INSTANCE_IP]  # Setup systemd user service

set -euo pipefail

INSTANCE_IP="${2:-137.131.52.69}"
SSH_KEY="${3:-${HOME}/.ssh/redroid_oci}"
SSH_USER="ubuntu"
PID_FILE="${HOME}/.redroid-tunnel.pid"
LOG_FILE="${HOME}/.redroid-tunnel.log"

start_tunnel() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "SSH tunnel is already running (PID: $PID)"
            return 0
        else
            rm -f "$PID_FILE"
        fi
    fi
    
    echo "Starting SSH tunnel to $INSTANCE_IP..."
    ssh -i "$SSH_KEY" \
        -o StrictHostKeyChecking=no \
        -o ServerAliveInterval=60 \
        -o ServerAliveCountMax=3 \
        -o ExitOnForwardFailure=yes \
        -f -N \
        -L 5555:localhost:5555 \
        -L 5900:localhost:5900 \
        -L 8080:localhost:8080 \
        "$SSH_USER@$INSTANCE_IP" \
        > "$LOG_FILE" 2>&1
    
    sleep 1
    
    # Find the PID
    PID=$(ps aux | grep "ssh.*5555.*5900.*$INSTANCE_IP" | grep -v grep | awk '{print $2}' | head -1)
    
    if [ -n "$PID" ]; then
        echo "$PID" > "$PID_FILE"
        echo "SSH tunnel started (PID: $PID)"
        echo "ADB: localhost:5555"
        echo "VNC: localhost:5900"
        echo "API: localhost:8080"
        return 0
    else
        echo "Failed to start SSH tunnel. Check $LOG_FILE"
        return 1
    fi
}

stop_tunnel() {
    if [ ! -f "$PID_FILE" ]; then
        echo "No tunnel PID file found. Trying to find and kill tunnel..."
        pkill -f "ssh.*5900.*$INSTANCE_IP" || echo "No tunnel process found"
        return 0
    fi
    
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        kill "$PID" 2>/dev/null || true
        echo "SSH tunnel stopped (PID: $PID)"
    else
        echo "Tunnel process not found"
    fi
    
    rm -f "$PID_FILE"
}

status_tunnel() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "SSH tunnel is RUNNING (PID: $PID)"
            
            # Test ports
            if timeout 1 nc -z localhost 5555 2>/dev/null; then
                echo "  ✓ ADB port 5555 is accessible"
            else
                echo "  ✗ ADB port 5555 is not accessible"
            fi
            
            if timeout 1 nc -z localhost 5900 2>/dev/null; then
                echo "  ✓ VNC port 5900 is accessible"
            else
                echo "  ✗ VNC port 5900 is not accessible"
            fi
            
            if timeout 1 nc -z localhost 8080 2>/dev/null; then
                echo "  ✓ API port 8080 is accessible"
            else
                echo "  ✗ API port 8080 is not accessible"
            fi
            
            return 0
        else
            echo "SSH tunnel PID file exists but process is not running"
            rm -f "$PID_FILE"
            return 1
        fi
    else
        echo "SSH tunnel is NOT running"
        return 1
    fi
}

persist_tunnel() {
    echo "Setting up persistent SSH tunnel service..."
    echo "Instance IP: $INSTANCE_IP"
    echo ""

    # Create systemd user directory
    mkdir -p ~/.config/systemd/user

    # Create service file
    cat > ~/.config/systemd/user/redroid-tunnel.service << EOF
[Unit]
Description=SSH Tunnel for Redroid VNC, API, and ADB
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/ssh -i $SSH_KEY -o StrictHostKeyChecking=no -o ServerAliveInterval=60 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -N -L 5900:localhost:5900 -L 8080:localhost:8080 -L 5555:localhost:5555 $SSH_USER@${INSTANCE_IP}
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
    echo "Commands:"
    echo "  Enable (start on login):  systemctl --user enable redroid-tunnel.service"
    echo "  Start now:                systemctl --user start redroid-tunnel.service"
    echo "  Check status:             systemctl --user status redroid-tunnel.service"
    echo "  View logs:                journalctl --user -u redroid-tunnel.service -f"
    echo ""
    echo "Tunneled ports:"
    echo "  5555  ADB"
    echo "  5900  VNC"
    echo "  8080  Control API"
}

case "${1:-status}" in
    start)
        start_tunnel
        ;;
    stop)
        stop_tunnel
        ;;
    restart)
        stop_tunnel
        sleep 1
        start_tunnel
        ;;
    status)
        status_tunnel
        ;;
    persist)
        persist_tunnel
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|persist} [INSTANCE_IP] [SSH_KEY]"
        echo ""
        echo "Commands:"
        echo "  start    Start SSH tunnel (foreground aware)"
        echo "  stop     Stop SSH tunnel"
        echo "  restart  Restart SSH tunnel"
        echo "  status   Check tunnel status and port accessibility"
        echo "  persist  Setup systemd user service for persistent tunnel"
        echo ""
        echo "Examples:"
        echo "  $0 start 129.146.1.2"
        echo "  $0 start 129.146.1.2 ~/.ssh/my_key"
        echo "  $0 persist 129.146.1.2"
        exit 1
        ;;
esac

