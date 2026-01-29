#!/bin/bash
#
# service-orchestrator.sh - Orchestrate Cloud Phone services with dependency ordering
#
# This script manages service startup/shutdown with proper dependency ordering,
# health checks, and timeout handling.
#
# Usage:
#   ./service-orchestrator.sh start   # Start all services in order
#   ./service-orchestrator.sh stop    # Stop all services in reverse order
#   ./service-orchestrator.sh restart # Restart all services
#   ./service-orchestrator.sh status  # Show detailed status
#   ./service-orchestrator.sh health  # Run health checks

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Service definitions in dependency order (services with lower priority start first)
# Format: SERVICE:PRIORITY:TIMEOUT:HEALTH_CHECK
declare -a SERVICES=(
    "redroid-container:1:120:docker exec redroid getprop sys.boot_completed | grep -q 1"
    "nginx-rtmp:2:30:curl -sf http://127.0.0.1:8081/stat | grep -q rtmp"
    "ffmpeg-bridge:3:30:pgrep -f ffmpeg"
    "control-api:4:60:curl -sf http://127.0.0.1:8080/health"
    "log-collector:5:30:test -f /var/run/cloud-phone-logcat.pid"
)

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Logging
log_info()    { echo -e "${GREEN}[INFO]${NC} $(date '+%H:%M:%S') $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $(date '+%H:%M:%S') $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*" >&2; }
log_success() { echo -e "${GREEN}[✓]${NC} $*"; }
log_fail()    { echo -e "${RED}[✗]${NC} $*"; }
log_header()  { echo -e "\n${BLUE}═══════════════════════════════════════════════════════════${NC}"; echo -e "${BLUE}  $*${NC}"; echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}\n"; }

# Parse service definition
parse_service() {
    local def="$1"
    IFS=':' read -r SERVICE PRIORITY TIMEOUT HEALTH_CHECK <<< "$def"
}

# Wait for service to be healthy
wait_for_health() {
    local service="$1"
    local timeout="$2"
    local health_check="$3"
    local start_time=$(date +%s)
    
    log_info "Waiting for $service to be healthy (timeout: ${timeout}s)..."
    
    while true; do
        local elapsed=$(($(date +%s) - start_time))
        
        if [[ $elapsed -ge $timeout ]]; then
            log_error "$service health check timed out after ${timeout}s"
            return 1
        fi
        
        if eval "$health_check" &>/dev/null; then
            log_success "$service is healthy (took ${elapsed}s)"
            return 0
        fi
        
        sleep 2
    done
}

# Start a single service
start_service() {
    local service="$1"
    local timeout="$2"
    local health_check="$3"
    
    log_info "Starting $service..."
    
    # Check if already running
    if systemctl is-active --quiet "$service"; then
        log_info "$service is already running"
        return 0
    fi
    
    # Start the service
    if ! systemctl start "$service"; then
        log_error "Failed to start $service"
        systemctl status "$service" --no-pager || true
        return 1
    fi
    
    # Wait for health
    if ! wait_for_health "$service" "$timeout" "$health_check"; then
        log_error "$service started but health check failed"
        journalctl -u "$service" -n 20 --no-pager || true
        return 1
    fi
    
    return 0
}

# Stop a single service
stop_service() {
    local service="$1"
    
    log_info "Stopping $service..."
    
    if ! systemctl is-active --quiet "$service"; then
        log_info "$service is already stopped"
        return 0
    fi
    
    if systemctl stop "$service"; then
        log_success "$service stopped"
        return 0
    else
        log_error "Failed to stop $service"
        return 1
    fi
}

# Get service status with color
get_status() {
    local service="$1"
    local status
    status=$(systemctl is-active "$service" 2>/dev/null || echo "unknown")
    
    case "$status" in
        active)     echo -e "${GREEN}active${NC}" ;;
        inactive)   echo -e "${YELLOW}inactive${NC}" ;;
        failed)     echo -e "${RED}failed${NC}" ;;
        *)          echo -e "${RED}$status${NC}" ;;
    esac
}

# Start all services in order
cmd_start() {
    log_header "Starting Cloud Phone Services"
    
    local failed=0
    
    # Sort services by priority
    IFS=$'\n' sorted=($(sort -t: -k2 -n <<< "${SERVICES[*]}")); unset IFS
    
    for def in "${sorted[@]}"; do
        parse_service "$def"
        
        echo -e "\n${CYAN}[$PRIORITY/5]${NC} $SERVICE"
        
        if ! start_service "$SERVICE" "$TIMEOUT" "$HEALTH_CHECK"; then
            ((failed++))
            log_error "Service $SERVICE failed to start properly"
            
            # Ask to continue or abort
            if [[ -t 0 ]]; then
                read -p "Continue with remaining services? [y/N] " -n 1 -r
                echo
                if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                    log_error "Aborting startup"
                    return 1
                fi
            else
                # Non-interactive: continue
                log_warn "Continuing with remaining services..."
            fi
        fi
    done
    
    echo ""
    if [[ $failed -eq 0 ]]; then
        log_header "All Services Started Successfully"
    else
        log_header "Startup Complete with $failed Failures"
    fi
    
    cmd_status
    
    return $failed
}

# Stop all services in reverse order
cmd_stop() {
    log_header "Stopping Cloud Phone Services"
    
    local failed=0
    
    # Sort services by priority (reverse order for stop)
    IFS=$'\n' sorted=($(sort -t: -k2 -rn <<< "${SERVICES[*]}")); unset IFS
    
    for def in "${sorted[@]}"; do
        parse_service "$def"
        
        if ! stop_service "$SERVICE"; then
            ((failed++))
        fi
    done
    
    # Also stop the target
    log_info "Stopping target..."
    systemctl stop redroid-cloud-phone.target 2>/dev/null || true
    
    echo ""
    if [[ $failed -eq 0 ]]; then
        log_success "All services stopped"
    else
        log_warn "$failed services had issues stopping"
    fi
    
    return 0
}

# Restart all services
cmd_restart() {
    cmd_stop
    sleep 5
    cmd_start
}

# Show detailed status
cmd_status() {
    log_header "Service Status"
    
    printf "%-25s %-12s %-10s %s\n" "SERVICE" "STATUS" "PID" "UPTIME"
    printf "%-25s %-12s %-10s %s\n" "-------" "------" "---" "------"
    
    for def in "${SERVICES[@]}"; do
        parse_service "$def"
        
        local status
        status=$(get_status "$SERVICE")
        
        local pid=""
        local uptime=""
        
        if systemctl is-active --quiet "$SERVICE"; then
            pid=$(systemctl show "$SERVICE" --property=MainPID --value 2>/dev/null || echo "")
            uptime=$(systemctl show "$SERVICE" --property=ActiveEnterTimestamp --value 2>/dev/null | cut -d' ' -f2-3 || echo "")
        fi
        
        printf "%-25s %-20s %-10s %s\n" "$SERVICE" "$status" "$pid" "$uptime"
    done
    
    echo ""
    
    # Show target status
    echo -e "Target: $(get_status redroid-cloud-phone.target)"
    
    # Show resource usage
    echo ""
    log_info "Resource Usage:"
    echo ""
    
    # Docker containers
    if docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null | grep -E "redroid|cloud-phone"; then
        :
    fi
    
    # Memory usage
    echo ""
    free -h | head -2
}

# Run health checks
cmd_health() {
    log_header "Health Checks"
    
    local passed=0
    local failed=0
    
    for def in "${SERVICES[@]}"; do
        parse_service "$def"
        
        printf "%-25s " "$SERVICE"
        
        if ! systemctl is-active --quiet "$SERVICE"; then
            echo -e "${YELLOW}[SKIP]${NC} not running"
            continue
        fi
        
        if eval "$HEALTH_CHECK" &>/dev/null; then
            echo -e "${GREEN}[PASS]${NC}"
            ((passed++))
        else
            echo -e "${RED}[FAIL]${NC}"
            ((failed++))
        fi
    done
    
    echo ""
    echo "Results: $passed passed, $failed failed"
    
    return $failed
}

# Show dependency tree
cmd_deps() {
    log_header "Service Dependencies"
    
    for def in "${SERVICES[@]}"; do
        parse_service "$def"
        
        echo -e "\n${CYAN}$SERVICE${NC} (priority: $PRIORITY)"
        echo "  After:"
        systemctl show "$SERVICE" --property=After --value 2>/dev/null | tr ' ' '\n' | grep -v '^$' | sed 's/^/    /'
        echo "  Wants:"
        systemctl show "$SERVICE" --property=Wants --value 2>/dev/null | tr ' ' '\n' | grep -v '^$' | sed 's/^/    /'
    done
}

# Usage
usage() {
    cat <<EOF
Service Orchestrator - Manage Cloud Phone services with dependency ordering

Usage: $0 <command>

Commands:
  start     Start all services in dependency order
  stop      Stop all services in reverse order
  restart   Restart all services
  status    Show detailed service status
  health    Run health checks on all services
  deps      Show service dependencies

Services (in start order):
  1. redroid-container  - Core Android VM
  2. nginx-rtmp         - RTMP streaming server
  3. ffmpeg-bridge      - Video/audio bridge
  4. control-api        - REST API server
  5. log-collector      - Log aggregation

Examples:
  $0 start      # Start all services
  $0 health     # Check all services
  $0 status     # View status

EOF
    exit 0
}

# Main
case "${1:-}" in
    start)    cmd_start ;;
    stop)     cmd_stop ;;
    restart)  cmd_restart ;;
    status)   cmd_status ;;
    health)   cmd_health ;;
    deps)     cmd_deps ;;
    --help|-h|help) usage ;;
    *)        usage ;;
esac
