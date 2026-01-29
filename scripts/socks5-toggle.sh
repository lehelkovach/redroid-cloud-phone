#!/bin/bash
# SOCKS5 Proxy Toggle Script
# Routes all instance traffic through SOCKS5 proxy using tun2socks

set -e

COMMAND="${1:-status}"
SOCKS5_HOST="${2:-}"
SOCKS5_PORT="${3:-1080}"
SOCKS5_USER="${4:-}"
SOCKS5_PASS="${5:-}"

CONFIG_FILE="/etc/tun2socks.env"
ROUTING_SCRIPT="/opt/redroid-scripts/socks5-routing.sh"

usage() {
    echo "Usage: $0 <command> [options]"
    echo ""
    echo "Commands:"
    echo "  enable <host> <port> [user] [pass]  - Enable SOCKS5 routing"
    echo "  disable                             - Disable SOCKS5 routing"
    echo "  status                              - Show current status"
    echo ""
    echo "Examples:"
    echo "  $0 enable proxy.example.com 1080"
    echo "  $0 enable proxy.example.com 1080 myuser mypass"
    echo "  $0 disable"
    echo ""
}

get_default_gateway() {
    ip route | grep "^default" | head -1 | awk '{print $3}'
}

get_default_interface() {
    ip route | grep "^default" | head -1 | awk '{print $5}'
}

enable_socks5() {
    if [ -z "$SOCKS5_HOST" ]; then
        echo "Error: SOCKS5 host required"
        usage
        exit 1
    fi
    
    echo "Enabling SOCKS5 routing..."
    echo "Proxy: $SOCKS5_HOST:$SOCKS5_PORT"
    
    # Build proxy URL
    if [ -n "$SOCKS5_USER" ]; then
        PROXY_URL="${SOCKS5_USER}:${SOCKS5_PASS}@${SOCKS5_HOST}:${SOCKS5_PORT}"
    else
        PROXY_URL="${SOCKS5_HOST}:${SOCKS5_PORT}"
    fi
    
    # Save configuration
    cat > "$CONFIG_FILE" << EOF
SOCKS5_PROXY=$PROXY_URL
SOCKS5_HOST=$SOCKS5_HOST
SOCKS5_PORT=$SOCKS5_PORT
EOF
    
    # Get current gateway info before enabling
    DEFAULT_GW=$(get_default_gateway)
    DEFAULT_IF=$(get_default_interface)
    
    echo "Current gateway: $DEFAULT_GW via $DEFAULT_IF"
    
    # Resolve proxy IP (need direct route to proxy)
    PROXY_IP=$(getent hosts "$SOCKS5_HOST" | awk '{print $1}' | head -1)
    if [ -z "$PROXY_IP" ]; then
        PROXY_IP="$SOCKS5_HOST"  # Assume it's already an IP
    fi
    
    # Create routing script
    cat > "$ROUTING_SCRIPT" << EOF
#!/bin/bash
# SOCKS5 routing rules

# Add direct route to SOCKS5 proxy
ip route add $PROXY_IP via $DEFAULT_GW dev $DEFAULT_IF 2>/dev/null || true

# Route everything else through tun0
ip route del default 2>/dev/null || true
ip route add default via 198.18.0.1 dev tun0

# Save original gateway for restore
echo "$DEFAULT_GW" > /var/run/original-gateway
echo "$DEFAULT_IF" > /var/run/original-interface
EOF
    chmod +x "$ROUTING_SCRIPT"
    
    # Enable and start tun2socks
    systemctl enable tun2socks.service
    systemctl restart tun2socks.service
    
    # Wait for tun0 to come up
    sleep 3
    
    # Apply routing
    if ip link show tun0 &>/dev/null; then
        "$ROUTING_SCRIPT"
        echo ""
        echo "SOCKS5 routing ENABLED"
        echo "All traffic now routes through: $SOCKS5_HOST:$SOCKS5_PORT"
    else
        echo "Error: tun0 device not created"
        systemctl status tun2socks.service
        exit 1
    fi
}

disable_socks5() {
    echo "Disabling SOCKS5 routing..."
    
    # Stop tun2socks
    systemctl stop tun2socks.service 2>/dev/null || true
    systemctl disable tun2socks.service 2>/dev/null || true
    
    # Restore original routing
    if [ -f /var/run/original-gateway ] && [ -f /var/run/original-interface ]; then
        ORIG_GW=$(cat /var/run/original-gateway)
        ORIG_IF=$(cat /var/run/original-interface)
        
        ip route del default 2>/dev/null || true
        ip route add default via "$ORIG_GW" dev "$ORIG_IF" 2>/dev/null || true
        
        rm -f /var/run/original-gateway /var/run/original-interface
    fi
    
    # Clean up
    ip link set dev tun0 down 2>/dev/null || true
    ip tuntap del mode tun dev tun0 2>/dev/null || true
    
    rm -f "$CONFIG_FILE" "$ROUTING_SCRIPT"
    
    echo ""
    echo "SOCKS5 routing DISABLED"
    echo "Traffic now uses direct connection"
}

show_status() {
    echo "SOCKS5 Proxy Status"
    echo "==================="
    
    if systemctl is-active --quiet tun2socks.service; then
        echo "Status: ENABLED"
        
        if [ -f "$CONFIG_FILE" ]; then
            source "$CONFIG_FILE"
            echo "Proxy: $SOCKS5_HOST:$SOCKS5_PORT"
        fi
        
        echo ""
        echo "tun0 device:"
        ip addr show tun0 2>/dev/null || echo "  (not found)"
        
        echo ""
        echo "Routing table:"
        ip route | head -5
    else
        echo "Status: DISABLED"
        echo "Traffic uses direct connection"
    fi
    
    echo ""
    echo "Current public IP:"
    curl -s --max-time 5 ifconfig.me 2>/dev/null || echo "(unable to determine)"
    echo ""
}

# Check root
if [[ $EUID -ne 0 ]] && [[ "$COMMAND" != "status" ]]; then
    echo "Error: Run with sudo"
    exit 1
fi

# Execute command
case "$COMMAND" in
    enable)
        enable_socks5
        ;;
    disable)
        disable_socks5
        ;;
    status)
        show_status
        ;;
    *)
        usage
        exit 1
        ;;
esac
