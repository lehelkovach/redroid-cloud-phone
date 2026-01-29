#!/bin/bash
# vnc-tunnel.sh
# Manages SSH tunnel for VNC and API access
#
# Usage:
#   Start:   ./vnc-tunnel.sh start [INSTANCE_IP]
#   Stop:    ./vnc-tunnel.sh stop
#   Status:  ./vnc-tunnel.sh status
#   Restart: ./vnc-tunnel.sh restart [INSTANCE_IP]

set -euo pipefail

INSTANCE_IP="${2:-137.131.52.69}"
SSH_KEY="${HOME}/.ssh/redroid_oci"
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
        -L 5900:localhost:5900 \
        -L 8080:localhost:8080 \
        "$SSH_USER@$INSTANCE_IP" \
        > "$LOG_FILE" 2>&1
    
    sleep 1
    
    # Find the PID
    PID=$(ps aux | grep "ssh.*5900.*$INSTANCE_IP" | grep -v grep | awk '{print $2}' | head -1)
    
    if [ -n "$PID" ]; then
        echo "$PID" > "$PID_FILE"
        echo "SSH tunnel started (PID: $PID)"
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
            if timeout 1 nc -z localhost 5901 2>/dev/null; then
                echo "  ✓ VNC port 5901 is accessible"
            else
                echo "  ✗ VNC port 5901 is not accessible"
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
    *)
        echo "Usage: $0 {start|stop|restart|status} [INSTANCE_IP]"
        exit 1
        ;;
esac

