#!/bin/bash
# Proxy Control Script for Cloud Phone
#
# Supports:
# - HTTP proxy (Android global settings)
# - SOCKS5 proxy (via redsocks/tun2socks)
# - Transparent proxy (iptables redirect)
#
# Usage:
#   ./proxy-control.sh enable socks5 <host> <port> [username] [password]
#   ./proxy-control.sh enable http <host> <port>
#   ./proxy-control.sh enable transparent <host> <port>
#   ./proxy-control.sh disable
#   ./proxy-control.sh status

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADB_TARGET="${ADB_CONNECT:-127.0.0.1:5555}"
REDSOCKS_CONF="/etc/redsocks.conf"
TUN2SOCKS_PID="/var/run/tun2socks.pid"

log_info() { echo "[INFO] $1"; }
log_error() { echo "[ERROR] $1" >&2; }

adb_cmd() {
    adb -s "$ADB_TARGET" "$@"
}

adb_shell() {
    adb_cmd shell "$@"
}

# Check if running in container context or host
detect_context() {
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "redroid"; then
        echo "redroid"
    else
        echo "host"
    fi
}

enable_http_proxy() {
    local host="$1"
    local port="$2"
    
    log_info "Setting HTTP proxy to $host:$port"
    
    # Set Android global proxy
    adb_shell "settings put global http_proxy $host:$port"
    
    # Also set via setprop for older Android
    adb_shell "setprop persist.sys.http.proxy $host:$port"
    
    log_info "HTTP proxy enabled"
}

enable_socks5_proxy() {
    local host="$1"
    local port="$2"
    local username="${3:-}"
    local password="${4:-}"
    
    log_info "Setting SOCKS5 proxy to $host:$port"
    
    # Method 1: Use tun2socks if available (preferred)
    if command -v tun2socks &>/dev/null; then
        log_info "Using tun2socks for SOCKS5"
        
        # Stop existing instance
        if [[ -f "$TUN2SOCKS_PID" ]]; then
            kill "$(cat $TUN2SOCKS_PID)" 2>/dev/null || true
            rm -f "$TUN2SOCKS_PID"
        fi
        
        # Create TUN interface
        ip tuntap add dev tun0 mode tun 2>/dev/null || true
        ip addr add 10.0.85.1/24 dev tun0 2>/dev/null || true
        ip link set dev tun0 up
        
        # Build proxy URL
        local proxy_url="socks5://$host:$port"
        if [[ -n "$username" ]] && [[ -n "$password" ]]; then
            proxy_url="socks5://$username:$password@$host:$port"
        fi
        
        # Start tun2socks
        tun2socks -device tun0 -proxy "$proxy_url" &
        echo $! > "$TUN2SOCKS_PID"
        
        # Route traffic through TUN
        # Get container/Redroid network namespace
        local context=$(detect_context)
        if [[ "$context" == "redroid" ]]; then
            local container_pid=$(docker inspect -f '{{.State.Pid}}' redroid)
            nsenter -t "$container_pid" -n ip route add default via 10.0.85.1 dev tun0
        fi
        
        log_info "tun2socks started with PID $(cat $TUN2SOCKS_PID)"
    
    # Method 2: Use redsocks
    elif command -v redsocks &>/dev/null; then
        log_info "Using redsocks for SOCKS5"
        
        # Generate config
        cat > "$REDSOCKS_CONF" <<EOF
base {
    log_debug = off;
    log_info = on;
    log = "syslog:daemon";
    daemon = on;
    redirector = iptables;
}
redsocks {
    local_ip = 127.0.0.1;
    local_port = 12345;
    ip = $host;
    port = $port;
    type = socks5;
$(if [[ -n "$username" ]]; then echo "    login = \"$username\";"; fi)
$(if [[ -n "$password" ]]; then echo "    password = \"$password\";"; fi)
}
EOF
        
        # Restart redsocks
        systemctl restart redsocks 2>/dev/null || redsocks -c "$REDSOCKS_CONF"
        
        # Setup iptables redirect
        iptables -t nat -N REDSOCKS 2>/dev/null || iptables -t nat -F REDSOCKS
        iptables -t nat -A REDSOCKS -d 0.0.0.0/8 -j RETURN
        iptables -t nat -A REDSOCKS -d 10.0.0.0/8 -j RETURN
        iptables -t nat -A REDSOCKS -d 127.0.0.0/8 -j RETURN
        iptables -t nat -A REDSOCKS -d 169.254.0.0/16 -j RETURN
        iptables -t nat -A REDSOCKS -d 172.16.0.0/12 -j RETURN
        iptables -t nat -A REDSOCKS -d 192.168.0.0/16 -j RETURN
        iptables -t nat -A REDSOCKS -p tcp -j REDIRECT --to-ports 12345
        
        # Apply to OUTPUT and PREROUTING
        iptables -t nat -A OUTPUT -p tcp -j REDSOCKS
        iptables -t nat -A PREROUTING -p tcp -j REDSOCKS
        
        log_info "redsocks configured"
    
    # Method 3: Fallback - set in Android (app-level only)
    else
        log_info "No system proxy tool available, using app-level settings"
        adb_shell "setprop persist.proxy.socks5.host $host"
        adb_shell "setprop persist.proxy.socks5.port $port"
        
        # Some apps respect these
        adb_shell "settings put global socks_proxy_host $host"
        adb_shell "settings put global socks_proxy_port $port"
    fi
    
    log_info "SOCKS5 proxy enabled"
}

enable_transparent_proxy() {
    local host="$1"
    local port="$2"
    
    log_info "Setting transparent proxy to $host:$port"
    
    # Get container network namespace
    local context=$(detect_context)
    
    if [[ "$context" == "redroid" ]]; then
        local container_pid=$(docker inspect -f '{{.State.Pid}}' redroid)
        
        # Setup iptables in container namespace
        nsenter -t "$container_pid" -n iptables -t nat -N PROXY 2>/dev/null || \
            nsenter -t "$container_pid" -n iptables -t nat -F PROXY
        
        nsenter -t "$container_pid" -n iptables -t nat -A PROXY -d 10.0.0.0/8 -j RETURN
        nsenter -t "$container_pid" -n iptables -t nat -A PROXY -d 172.16.0.0/12 -j RETURN
        nsenter -t "$container_pid" -n iptables -t nat -A PROXY -d 192.168.0.0/16 -j RETURN
        nsenter -t "$container_pid" -n iptables -t nat -A PROXY -d 127.0.0.0/8 -j RETURN
        nsenter -t "$container_pid" -n iptables -t nat -A PROXY -p tcp -j DNAT --to-destination "$host:$port"
        
        nsenter -t "$container_pid" -n iptables -t nat -A OUTPUT -p tcp -j PROXY
        nsenter -t "$container_pid" -n iptables -t nat -A PREROUTING -p tcp -j PROXY
    else
        # Host-level transparent proxy
        iptables -t nat -N PROXY 2>/dev/null || iptables -t nat -F PROXY
        iptables -t nat -A PROXY -d 10.0.0.0/8 -j RETURN
        iptables -t nat -A PROXY -d 172.16.0.0/12 -j RETURN
        iptables -t nat -A PROXY -d 192.168.0.0/16 -j RETURN
        iptables -t nat -A PROXY -d 127.0.0.0/8 -j RETURN
        iptables -t nat -A PROXY -p tcp -j DNAT --to-destination "$host:$port"
        
        iptables -t nat -A OUTPUT -p tcp -j PROXY
        iptables -t nat -A PREROUTING -p tcp -j PROXY
    fi
    
    log_info "Transparent proxy enabled"
}

disable_proxy() {
    log_info "Disabling proxy..."
    
    # Clear Android proxy settings
    adb_shell "settings put global http_proxy :0" 2>/dev/null || true
    adb_shell "settings delete global http_proxy" 2>/dev/null || true
    adb_shell "setprop persist.sys.http.proxy ''" 2>/dev/null || true
    
    # Stop tun2socks
    if [[ -f "$TUN2SOCKS_PID" ]]; then
        kill "$(cat $TUN2SOCKS_PID)" 2>/dev/null || true
        rm -f "$TUN2SOCKS_PID"
    fi
    
    # Remove TUN interface
    ip link del tun0 2>/dev/null || true
    
    # Stop redsocks
    systemctl stop redsocks 2>/dev/null || true
    killall redsocks 2>/dev/null || true
    
    # Clear iptables rules
    iptables -t nat -F REDSOCKS 2>/dev/null || true
    iptables -t nat -X REDSOCKS 2>/dev/null || true
    iptables -t nat -F PROXY 2>/dev/null || true
    iptables -t nat -X PROXY 2>/dev/null || true
    
    # Clear in container namespace if running
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "redroid"; then
        local container_pid=$(docker inspect -f '{{.State.Pid}}' redroid)
        nsenter -t "$container_pid" -n iptables -t nat -F PROXY 2>/dev/null || true
        nsenter -t "$container_pid" -n iptables -t nat -X PROXY 2>/dev/null || true
    fi
    
    log_info "Proxy disabled"
}

show_status() {
    echo "=== Proxy Status ==="
    echo ""
    
    # Android settings
    echo "Android HTTP Proxy:"
    adb_shell "settings get global http_proxy" 2>/dev/null || echo "  (not set)"
    
    echo ""
    echo "SOCKS5 Settings:"
    adb_shell "getprop persist.proxy.socks5.host" 2>/dev/null || echo "  host: (not set)"
    adb_shell "getprop persist.proxy.socks5.port" 2>/dev/null || echo "  port: (not set)"
    
    echo ""
    echo "tun2socks:"
    if [[ -f "$TUN2SOCKS_PID" ]] && kill -0 "$(cat $TUN2SOCKS_PID)" 2>/dev/null; then
        echo "  Running (PID: $(cat $TUN2SOCKS_PID))"
    else
        echo "  Not running"
    fi
    
    echo ""
    echo "redsocks:"
    if pgrep -x redsocks &>/dev/null; then
        echo "  Running"
    else
        echo "  Not running"
    fi
    
    echo ""
    echo "iptables NAT chains:"
    iptables -t nat -L REDSOCKS -n 2>/dev/null | head -5 || echo "  REDSOCKS: not configured"
    iptables -t nat -L PROXY -n 2>/dev/null | head -5 || echo "  PROXY: not configured"
}

# Main
case "${1:-status}" in
    enable)
        type="${2:-socks5}"
        host="${3:-}"
        port="${4:-}"
        username="${5:-}"
        password="${6:-}"
        
        if [[ -z "$host" ]] || [[ -z "$port" ]]; then
            log_error "Usage: $0 enable <type> <host> <port> [username] [password]"
            exit 1
        fi
        
        case "$type" in
            http)
                enable_http_proxy "$host" "$port"
                ;;
            socks5)
                enable_socks5_proxy "$host" "$port" "$username" "$password"
                ;;
            transparent)
                enable_transparent_proxy "$host" "$port"
                ;;
            *)
                log_error "Unknown proxy type: $type (use http, socks5, or transparent)"
                exit 1
                ;;
        esac
        ;;
    disable)
        disable_proxy
        ;;
    status)
        show_status
        ;;
    *)
        echo "Usage: $0 {enable|disable|status}"
        echo ""
        echo "Examples:"
        echo "  $0 enable socks5 proxy.example.com 1080"
        echo "  $0 enable socks5 proxy.example.com 1080 user pass"
        echo "  $0 enable http proxy.example.com 8080"
        echo "  $0 enable transparent proxy.example.com 8080"
        echo "  $0 disable"
        echo "  $0 status"
        exit 1
        ;;
esac
