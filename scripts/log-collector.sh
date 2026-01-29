#!/bin/bash
#
# log-collector.sh - Collect and route logs from Redroid, ADB, and services
#
# This script captures logs from:
# - Redroid Docker container
# - Android logcat via ADB
# - System services (nginx-rtmp, ffmpeg-bridge, control-api)
#
# Usage:
#   ./log-collector.sh start      # Start log collection
#   ./log-collector.sh stop       # Stop log collection
#   ./log-collector.sh status     # Check collector status
#   ./log-collector.sh tail       # Tail all logs
#   ./log-collector.sh sync       # Sync logs to remote (if configured)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load config
CONFIG_FILE="${CLOUD_PHONE_CONFIG:-$PROJECT_ROOT/config/cloud-phone-config.json}"
if [[ ! -f "$CONFIG_FILE" ]]; then
    CONFIG_FILE="$PROJECT_ROOT/config/cloud-phone-config.example.json"
fi

# Parse config with jq (if available) or use defaults
if command -v jq &>/dev/null && [[ -f "$CONFIG_FILE" ]]; then
    LOG_DIR=$(jq -r '.logging.directory // "/var/log/cloud-phone"' "$CONFIG_FILE")
    LOG_LEVEL=$(jq -r '.logging.level // "INFO"' "$CONFIG_FILE")
    LOG_MAX_SIZE=$(jq -r '.logging.max_size_mb // 100' "$CONFIG_FILE")
    LOG_MAX_FILES=$(jq -r '.logging.max_files // 10' "$CONFIG_FILE")
    LOGCAT_ENABLED=$(jq -r '.logging.redroid_capture.logcat // true' "$CONFIG_FILE")
    LOGCAT_FILTER=$(jq -r '.logging.redroid_capture.logcat_filter // "*:W"' "$CONFIG_FILE")
    CONTAINER_LOGS=$(jq -r '.logging.redroid_capture.container_logs // true' "$CONFIG_FILE")
    REMOTE_ENABLED=$(jq -r '.logging.remote.enabled // false' "$CONFIG_FILE")
    REMOTE_HOST=$(jq -r '.logging.remote.host // ""' "$CONFIG_FILE")
    REMOTE_USER=$(jq -r '.logging.remote.user // "ubuntu"' "$CONFIG_FILE")
    REMOTE_KEY=$(jq -r '.logging.remote.ssh_key // "~/.ssh/redroid_oci"' "$CONFIG_FILE")
    REMOTE_LOG_DIR=$(jq -r '.logging.remote.remote_log_dir // "/var/log/cloud-phone"' "$CONFIG_FILE")
    STREAM_REALTIME=$(jq -r '.logging.remote.stream_realtime // false' "$CONFIG_FILE")
    ADB_CONNECT=$(jq -r '.environment.ADB_CONNECT // "127.0.0.1:5555"' "$CONFIG_FILE")
else
    LOG_DIR="/var/log/cloud-phone"
    LOG_LEVEL="INFO"
    LOG_MAX_SIZE=100
    LOG_MAX_FILES=10
    LOGCAT_ENABLED=true
    LOGCAT_FILTER="*:W"
    CONTAINER_LOGS=true
    REMOTE_ENABLED=false
    REMOTE_HOST=""
    REMOTE_USER="ubuntu"
    REMOTE_KEY="~/.ssh/redroid_oci"
    REMOTE_LOG_DIR="/var/log/cloud-phone"
    STREAM_REALTIME=false
    ADB_CONNECT="127.0.0.1:5555"
fi

# Expand tilde in paths
REMOTE_KEY="${REMOTE_KEY/#\~/$HOME}"

# Log files
MAIN_LOG="$LOG_DIR/cloud-phone.log"
UNIFIED_LOG="$LOG_DIR/unified.log"  # All sources combined with labels
REDROID_LOG="$LOG_DIR/redroid.log"
ADB_LOG="$LOG_DIR/adb.log"
LOGCAT_LOG="$LOG_DIR/logcat.log"
STREAMING_LOG="$LOG_DIR/streaming.log"
API_LOG="$LOG_DIR/api.log"

# PID files
LOGCAT_PID_FILE="/var/run/cloud-phone-logcat.pid"
CONTAINER_LOG_PID_FILE="/var/run/cloud-phone-container-log.pid"
REMOTE_STREAM_PID_FILE="/var/run/cloud-phone-remote-stream.pid"
SERVICE_LOG_PID_FILE="/var/run/cloud-phone-service-log.pid"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m'

# Log type labels
declare -A LOG_TYPES=(
    [SYSTEM]="SYS"
    [REDROID]="RDR"
    [LOGCAT]="LCT"
    [ADB]="ADB"
    [API]="API"
    [STREAM]="STR"
    [NGINX]="NGX"
    [FFMPEG]="FFM"
    [DOCKER]="DKR"
)

declare -A LOG_COLORS=(
    [SYS]="$GREEN"
    [RDR]="$CYAN"
    [LCT]="$MAGENTA"
    [ADB]="$BLUE"
    [API]="$YELLOW"
    [STR]="$RED"
    [NGX]="$GREEN"
    [FFM]="$CYAN"
    [DKR]="$BLUE"
)

# Format log entry with type label
# Format: TIMESTAMP [TYPE] [LEVEL] MESSAGE
# JSON format: {"ts":"...","type":"...","level":"...","msg":"..."}
format_log() {
    local log_type="$1"
    local level="$2"
    local message="$3"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S.%3N')
    
    local type_label="${LOG_TYPES[$log_type]:-$log_type}"
    
    if [[ "${LOG_FORMAT:-text}" == "json" ]]; then
        # JSON format for structured logging
        printf '{"ts":"%s","type":"%s","level":"%s","msg":"%s"}\n' \
            "$timestamp" "$type_label" "$level" "${message//\"/\\\"}"
    else
        # Text format with type label
        printf '%s [%s] [%s] %s\n' "$timestamp" "$type_label" "$level" "$message"
    fi
}

# Write to both individual and unified logs
write_log() {
    local log_type="$1"
    local level="$2"
    local message="$3"
    local target_log="$4"
    
    local formatted
    formatted=$(format_log "$log_type" "$level" "$message")
    
    # Write to individual log
    echo "$formatted" >> "$target_log"
    
    # Write to unified log
    echo "$formatted" >> "$UNIFIED_LOG"
}

# Colored console output + logging
log_info()  { 
    local msg="$*"
    local formatted
    formatted=$(format_log "SYSTEM" "INFO" "$msg")
    echo -e "${GREEN}$formatted${NC}"
    echo "$formatted" >> "$MAIN_LOG"
    echo "$formatted" >> "$UNIFIED_LOG"
}

log_warn()  { 
    local msg="$*"
    local formatted
    formatted=$(format_log "SYSTEM" "WARN" "$msg")
    echo -e "${YELLOW}$formatted${NC}"
    echo "$formatted" >> "$MAIN_LOG"
    echo "$formatted" >> "$UNIFIED_LOG"
}

log_error() { 
    local msg="$*"
    local formatted
    formatted=$(format_log "SYSTEM" "ERROR" "$msg")
    echo -e "${RED}$formatted${NC}" >&2
    echo "$formatted" >> "$MAIN_LOG"
    echo "$formatted" >> "$UNIFIED_LOG"
}

# Initialize log directory
init_logs() {
    sudo mkdir -p "$LOG_DIR"
    sudo chown -R "${USER:-root}:${USER:-root}" "$LOG_DIR" 2>/dev/null || true
    touch "$MAIN_LOG" "$UNIFIED_LOG" "$REDROID_LOG" "$ADB_LOG" "$LOGCAT_LOG" "$STREAMING_LOG" "$API_LOG"
    log_info "Log directory initialized: $LOG_DIR"
    log_info "Log format: ${LOG_FORMAT:-text}"
    log_info "Log types available: ${!LOG_TYPES[*]}"
}

# Rotate logs if they exceed max size
rotate_logs() {
    local log_file="$1"
    local max_size=$((LOG_MAX_SIZE * 1024 * 1024))  # Convert MB to bytes
    
    if [[ -f "$log_file" ]]; then
        local size=$(stat -f%z "$log_file" 2>/dev/null || stat -c%s "$log_file" 2>/dev/null || echo 0)
        if [[ $size -gt $max_size ]]; then
            for i in $(seq $((LOG_MAX_FILES - 1)) -1 1); do
                [[ -f "${log_file}.$i" ]] && mv "${log_file}.$i" "${log_file}.$((i + 1))"
            done
            mv "$log_file" "${log_file}.1"
            touch "$log_file"
            log_info "Rotated log: $log_file"
        fi
    fi
}

# Start logcat capture
start_logcat() {
    if [[ "$LOGCAT_ENABLED" != "true" ]]; then
        log_info "Logcat capture disabled in config"
        return 0
    fi
    
    if [[ -f "$LOGCAT_PID_FILE" ]] && kill -0 "$(cat "$LOGCAT_PID_FILE")" 2>/dev/null; then
        log_info "Logcat capture already running (PID: $(cat "$LOGCAT_PID_FILE"))"
        return 0
    fi
    
    log_info "Starting logcat capture (filter: $LOGCAT_FILTER)..."
    
    # Ensure ADB is connected
    adb connect "$ADB_CONNECT" &>/dev/null || true
    sleep 2
    
    # Start logcat in background with labels
    (
        while true; do
            adb -s "$ADB_CONNECT" logcat -v time "$LOGCAT_FILTER" 2>/dev/null | while IFS= read -r line; do
                # Parse logcat level from line (V/D/I/W/E/F)
                local level="INFO"
                if [[ "$line" =~ ^[0-9]{2}-[0-9]{2}\ [0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]+\ ([VDIWEF])/ ]]; then
                    case "${BASH_REMATCH[1]}" in
                        V) level="VERBOSE" ;;
                        D) level="DEBUG" ;;
                        I) level="INFO" ;;
                        W) level="WARN" ;;
                        E) level="ERROR" ;;
                        F) level="FATAL" ;;
                    esac
                fi
                write_log "LOGCAT" "$level" "$line" "$LOGCAT_LOG"
            done
            sleep 5  # Retry on disconnect
        done
    ) &
    echo $! | sudo tee "$LOGCAT_PID_FILE" > /dev/null
    
    log_info "Logcat capture started (PID: $(cat "$LOGCAT_PID_FILE"))"
}

# Stop logcat capture
stop_logcat() {
    if [[ -f "$LOGCAT_PID_FILE" ]]; then
        local pid=$(cat "$LOGCAT_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            # Also kill child processes
            pkill -P "$pid" 2>/dev/null || true
            log_info "Logcat capture stopped"
        fi
        sudo rm -f "$LOGCAT_PID_FILE"
    fi
}

# Start Docker container log capture
start_container_logs() {
    if [[ "$CONTAINER_LOGS" != "true" ]]; then
        log_info "Container log capture disabled in config"
        return 0
    fi
    
    if [[ -f "$CONTAINER_LOG_PID_FILE" ]] && kill -0 "$(cat "$CONTAINER_LOG_PID_FILE")" 2>/dev/null; then
        log_info "Container log capture already running"
        return 0
    fi
    
    if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "redroid"; then
        log_warn "Redroid container not running, skipping container log capture"
        return 0
    fi
    
    log_info "Starting container log capture..."
    
    (
        docker logs -f redroid 2>&1 | while IFS= read -r line; do
            # Try to detect log level from Docker output
            local level="INFO"
            if [[ "$line" =~ (ERROR|error|Error|FATAL|fatal|Fatal|FAIL|fail|Fail) ]]; then
                level="ERROR"
            elif [[ "$line" =~ (WARN|warn|Warn|WARNING|warning|Warning) ]]; then
                level="WARN"
            elif [[ "$line" =~ (DEBUG|debug|Debug) ]]; then
                level="DEBUG"
            fi
            write_log "REDROID" "$level" "$line" "$REDROID_LOG"
            rotate_logs "$REDROID_LOG"
        done
    ) &
    echo $! | sudo tee "$CONTAINER_LOG_PID_FILE" > /dev/null
    
    log_info "Container log capture started (PID: $(cat "$CONTAINER_LOG_PID_FILE"))"
}

# Stop container log capture
stop_container_logs() {
    if [[ -f "$CONTAINER_LOG_PID_FILE" ]]; then
        local pid=$(cat "$CONTAINER_LOG_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            pkill -P "$pid" 2>/dev/null || true
            log_info "Container log capture stopped"
        fi
        sudo rm -f "$CONTAINER_LOG_PID_FILE"
    fi
}

# Map service names to log types
get_service_log_type() {
    local service="$1"
    case "$service" in
        nginx-rtmp)        echo "NGINX" ;;
        ffmpeg-bridge)     echo "FFMPEG" ;;
        control-api)       echo "API" ;;
        redroid-container) echo "DOCKER" ;;
        *)                 echo "SYSTEM" ;;
    esac
}

# Capture service logs to files (one-time capture)
capture_service_logs() {
    log_info "Capturing service logs..."
    
    # Capture recent logs from systemd services with labels
    for service in nginx-rtmp ffmpeg-bridge control-api redroid-container; do
        local log_type
        log_type=$(get_service_log_type "$service")
        local log_file="$LOG_DIR/${service}.log"
        
        if systemctl is-active --quiet "$service" 2>/dev/null; then
            journalctl -u "$service" --no-pager -n 1000 -o short-precise 2>/dev/null | \
            while IFS= read -r line; do
                local level="INFO"
                if [[ "$line" =~ (error|ERROR|Error|fail|FAIL|Fail) ]]; then
                    level="ERROR"
                elif [[ "$line" =~ (warn|WARN|Warn|warning|WARNING|Warning) ]]; then
                    level="WARN"
                fi
                write_log "$log_type" "$level" "$line" "$log_file"
            done
        fi
    done
    
    log_info "Service logs captured"
}

# Start continuous service log capture
start_service_logs() {
    if [[ -f "$SERVICE_LOG_PID_FILE" ]] && kill -0 "$(cat "$SERVICE_LOG_PID_FILE")" 2>/dev/null; then
        log_info "Service log capture already running"
        return 0
    fi
    
    log_info "Starting continuous service log capture..."
    
    (
        # Follow all relevant service logs
        journalctl -f -u nginx-rtmp -u ffmpeg-bridge -u control-api -o short-precise 2>/dev/null | \
        while IFS= read -r line; do
            # Determine source service from log line
            local log_type="SYSTEM"
            local log_file="$STREAMING_LOG"
            
            if [[ "$line" =~ nginx-rtmp ]]; then
                log_type="NGINX"
                log_file="$STREAMING_LOG"
            elif [[ "$line" =~ ffmpeg-bridge|ffmpeg ]]; then
                log_type="FFMPEG"
                log_file="$STREAMING_LOG"
            elif [[ "$line" =~ control-api|gunicorn|flask ]]; then
                log_type="API"
                log_file="$API_LOG"
            fi
            
            local level="INFO"
            if [[ "$line" =~ (error|ERROR|Error|fail|FAIL|Fail) ]]; then
                level="ERROR"
            elif [[ "$line" =~ (warn|WARN|Warn|warning|WARNING|Warning) ]]; then
                level="WARN"
            fi
            
            write_log "$log_type" "$level" "$line" "$log_file"
        done
    ) &
    echo $! | sudo tee "$SERVICE_LOG_PID_FILE" > /dev/null
    
    log_info "Service log capture started (PID: $(cat "$SERVICE_LOG_PID_FILE"))"
}

# Stop service log capture
stop_service_logs() {
    if [[ -f "$SERVICE_LOG_PID_FILE" ]]; then
        local pid=$(cat "$SERVICE_LOG_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            pkill -P "$pid" 2>/dev/null || true
            log_info "Service log capture stopped"
        fi
        sudo rm -f "$SERVICE_LOG_PID_FILE"
    fi
}

# Start remote log streaming
start_remote_stream() {
    if [[ "$REMOTE_ENABLED" != "true" ]] || [[ -z "$REMOTE_HOST" ]]; then
        log_info "Remote log streaming disabled or no host configured"
        return 0
    fi
    
    if [[ "$STREAM_REALTIME" != "true" ]]; then
        log_info "Realtime streaming disabled, use 'sync' for periodic sync"
        return 0
    fi
    
    if [[ -f "$REMOTE_STREAM_PID_FILE" ]] && kill -0 "$(cat "$REMOTE_STREAM_PID_FILE")" 2>/dev/null; then
        log_info "Remote streaming already running"
        return 0
    fi
    
    log_info "Starting remote log streaming to $REMOTE_USER@$REMOTE_HOST..."
    
    # Stream logs via SSH
    (
        tail -F "$MAIN_LOG" "$REDROID_LOG" "$LOGCAT_LOG" 2>/dev/null | \
        ssh -i "$REMOTE_KEY" -o StrictHostKeyChecking=no \
            "$REMOTE_USER@$REMOTE_HOST" \
            "cat >> $REMOTE_LOG_DIR/remote-stream.log"
    ) &
    echo $! | sudo tee "$REMOTE_STREAM_PID_FILE" > /dev/null
    
    log_info "Remote streaming started (PID: $(cat "$REMOTE_STREAM_PID_FILE"))"
}

# Stop remote streaming
stop_remote_stream() {
    if [[ -f "$REMOTE_STREAM_PID_FILE" ]]; then
        local pid=$(cat "$REMOTE_STREAM_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            pkill -P "$pid" 2>/dev/null || true
            log_info "Remote streaming stopped"
        fi
        sudo rm -f "$REMOTE_STREAM_PID_FILE"
    fi
}

# Sync logs to remote host
sync_logs() {
    if [[ "$REMOTE_ENABLED" != "true" ]] || [[ -z "$REMOTE_HOST" ]]; then
        log_warn "Remote sync disabled or no host configured"
        return 1
    fi
    
    log_info "Syncing logs to $REMOTE_USER@$REMOTE_HOST:$REMOTE_LOG_DIR..."
    
    # Create remote directory
    ssh -i "$REMOTE_KEY" -o StrictHostKeyChecking=no \
        "$REMOTE_USER@$REMOTE_HOST" \
        "mkdir -p $REMOTE_LOG_DIR" 2>/dev/null
    
    # Sync logs
    rsync -avz --progress \
        -e "ssh -i $REMOTE_KEY -o StrictHostKeyChecking=no" \
        "$LOG_DIR/" \
        "$REMOTE_USER@$REMOTE_HOST:$REMOTE_LOG_DIR/"
    
    log_info "Logs synced successfully"
}

# Fetch logs from remote VM
fetch_remote_logs() {
    local remote_host="${1:-$REMOTE_HOST}"
    local ssh_key="${2:-$REMOTE_KEY}"
    local ssh_user="${3:-$REMOTE_USER}"
    
    if [[ -z "$remote_host" ]]; then
        log_error "No remote host specified"
        return 1
    fi
    
    log_info "Fetching logs from $ssh_user@$remote_host..."
    
    local local_dest="$LOG_DIR/remote-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$local_dest"
    
    # Fetch logs via SCP
    scp -i "$ssh_key" -o StrictHostKeyChecking=no -r \
        "$ssh_user@$remote_host:/var/log/cloud-phone/*" \
        "$local_dest/" 2>/dev/null || true
    
    # Also fetch journald logs
    ssh -i "$ssh_key" -o StrictHostKeyChecking=no \
        "$ssh_user@$remote_host" \
        "sudo journalctl -u 'redroid*' -u 'nginx-rtmp' -u 'ffmpeg-bridge' -u 'control-api' --no-pager -n 5000" \
        > "$local_dest/journald.log" 2>/dev/null || true
    
    log_info "Remote logs fetched to: $local_dest"
}

# Start all log collection
start_all() {
    init_logs
    log_info "Starting log collection..."
    
    start_container_logs
    start_logcat
    start_service_logs
    capture_service_logs  # Initial capture
    start_remote_stream
    
    log_info "Log collection started"
}

# Stop all log collection
stop_all() {
    log_info "Stopping log collection..."
    
    stop_remote_stream
    stop_service_logs
    stop_logcat
    stop_container_logs
    
    log_info "Log collection stopped"
}

# Show status
show_status() {
    echo "=== Log Collector Status ==="
    echo ""
    echo "Log Directory: $LOG_DIR"
    echo "Config File: $CONFIG_FILE"
    echo "Log Format: ${LOG_FORMAT:-text}"
    echo ""
    
    echo "Log Type Labels:"
    for type in "${!LOG_TYPES[@]}"; do
        echo "  $type -> [${LOG_TYPES[$type]}]"
    done
    echo ""
    
    echo "Processes:"
    if [[ -f "$LOGCAT_PID_FILE" ]] && kill -0 "$(cat "$LOGCAT_PID_FILE")" 2>/dev/null; then
        echo "  ✓ Logcat capture: Running (PID: $(cat "$LOGCAT_PID_FILE"))"
    else
        echo "  ✗ Logcat capture: Stopped"
    fi
    
    if [[ -f "$CONTAINER_LOG_PID_FILE" ]] && kill -0 "$(cat "$CONTAINER_LOG_PID_FILE")" 2>/dev/null; then
        echo "  ✓ Container logs: Running (PID: $(cat "$CONTAINER_LOG_PID_FILE"))"
    else
        echo "  ✗ Container logs: Stopped"
    fi
    
    if [[ -f "$SERVICE_LOG_PID_FILE" ]] && kill -0 "$(cat "$SERVICE_LOG_PID_FILE")" 2>/dev/null; then
        echo "  ✓ Service logs: Running (PID: $(cat "$SERVICE_LOG_PID_FILE"))"
    else
        echo "  ✗ Service logs: Stopped"
    fi
    
    if [[ -f "$REMOTE_STREAM_PID_FILE" ]] && kill -0 "$(cat "$REMOTE_STREAM_PID_FILE")" 2>/dev/null; then
        echo "  ✓ Remote stream: Running (PID: $(cat "$REMOTE_STREAM_PID_FILE"))"
    else
        echo "  ✗ Remote stream: Stopped"
    fi
    
    echo ""
    echo "Log Files:"
    for log in "$MAIN_LOG" "$UNIFIED_LOG" "$REDROID_LOG" "$LOGCAT_LOG" "$ADB_LOG" "$STREAMING_LOG" "$API_LOG"; do
        if [[ -f "$log" ]]; then
            local size=$(du -h "$log" | cut -f1)
            local lines=$(wc -l < "$log" 2>/dev/null || echo 0)
            echo "  $(basename "$log"): $size ($lines lines)"
        fi
    done
    
    echo ""
    echo "Remote Sync:"
    echo "  Enabled: $REMOTE_ENABLED"
    [[ "$REMOTE_ENABLED" == "true" ]] && echo "  Host: $REMOTE_USER@$REMOTE_HOST"
}

# Tail all logs (unified)
tail_logs() {
    local lines="${1:-50}"
    
    echo "=== Tailing unified log (Ctrl+C to exit) ==="
    echo "Filter by type: grep '\\[RDR\\]' for Redroid, '\\[LCT\\]' for logcat, etc."
    echo ""
    tail -f -n "$lines" "$UNIFIED_LOG" 2>/dev/null || \
    tail -f -n "$lines" "$MAIN_LOG" 2>/dev/null || \
    echo "No logs available"
}

# Filter logs by type
filter_logs() {
    local log_type="${1:-}"
    local lines="${2:-100}"
    local log_file="${3:-$UNIFIED_LOG}"
    
    if [[ -z "$log_type" ]]; then
        echo "Available log types:"
        for type in "${!LOG_TYPES[@]}"; do
            echo "  $type (label: ${LOG_TYPES[$type]})"
        done
        echo ""
        echo "Usage: $0 filter <TYPE> [LINES] [LOG_FILE]"
        return 0
    fi
    
    # Get the label for the type
    local label="${LOG_TYPES[$log_type]:-$log_type}"
    
    echo "=== Filtering logs by type: $log_type ([$label]) ==="
    echo ""
    
    if [[ -f "$log_file" ]]; then
        grep "\\[$label\\]" "$log_file" | tail -n "$lines"
    else
        echo "Log file not found: $log_file"
    fi
}

# Filter logs by level
filter_level() {
    local level="${1:-ERROR}"
    local lines="${2:-100}"
    local log_file="${3:-$UNIFIED_LOG}"
    
    echo "=== Filtering logs by level: $level ==="
    echo ""
    
    if [[ -f "$log_file" ]]; then
        grep "\\[$level\\]" "$log_file" | tail -n "$lines"
    else
        echo "Log file not found: $log_file"
    fi
}

# Search logs
search_logs() {
    local pattern="${1:-}"
    local log_file="${2:-$UNIFIED_LOG}"
    
    if [[ -z "$pattern" ]]; then
        echo "Usage: $0 search <PATTERN> [LOG_FILE]"
        return 1
    fi
    
    echo "=== Searching for: $pattern ==="
    echo ""
    
    if [[ -f "$log_file" ]]; then
        grep -i "$pattern" "$log_file" | tail -n 100
    else
        echo "Log file not found: $log_file"
    fi
}

# Usage
usage() {
    cat <<EOF
Log Collector - Capture and route logs from Redroid Cloud Phone

Usage: $0 <command> [options]

Commands:
  start              Start all log collection
  stop               Stop all log collection
  status             Show collector status
  tail [N]           Tail unified log (default: 50 lines)
  filter TYPE [N]    Filter by log type (REDROID, LOGCAT, API, etc.)
  level LEVEL [N]    Filter by level (DEBUG, INFO, WARN, ERROR)
  search PATTERN     Search logs for pattern
  sync               Sync logs to remote host
  fetch [HOST]       Fetch logs from remote VM
  rotate             Rotate log files now
  init               Initialize log directory

Log Types (use with 'filter'):
  SYSTEM   [SYS]  - System/CLI messages
  REDROID  [RDR]  - Redroid container output
  LOGCAT   [LCT]  - Android logcat
  ADB      [ADB]  - ADB commands
  API      [API]  - Control API logs
  STREAM   [STR]  - Streaming logs
  NGINX    [NGX]  - nginx-rtmp logs
  FFMPEG   [FFM]  - FFmpeg bridge logs
  DOCKER   [DKR]  - Docker logs

Log Levels:
  DEBUG, INFO, WARN, ERROR, FATAL, VERBOSE

Log Format:
  Text:  TIMESTAMP [TYPE] [LEVEL] MESSAGE
  JSON:  {"ts":"...","type":"...","level":"...","msg":"..."}
         (set LOG_FORMAT=json to enable)

Configuration:
  Config file: $CONFIG_FILE
  Log directory: $LOG_DIR
  Unified log: $UNIFIED_LOG

Environment Variables:
  CLOUD_PHONE_CONFIG    Path to config file
  LOG_FORMAT            Output format (text/json)

Examples:
  $0 start                    # Start collection
  $0 tail 100                 # Tail last 100 lines
  $0 filter REDROID 50        # Filter Redroid logs
  $0 filter LOGCAT            # Filter Android logcat
  $0 level ERROR              # Show only errors
  $0 search "camera"          # Search for "camera"

EOF
    exit 0
}

# Main
case "${1:-}" in
    start)
        start_all
        ;;
    stop)
        stop_all
        ;;
    status)
        show_status
        ;;
    tail)
        tail_logs "${2:-50}"
        ;;
    filter)
        filter_logs "${2:-}" "${3:-100}" "${4:-$UNIFIED_LOG}"
        ;;
    level)
        filter_level "${2:-ERROR}" "${3:-100}" "${4:-$UNIFIED_LOG}"
        ;;
    search)
        search_logs "${2:-}" "${3:-$UNIFIED_LOG}"
        ;;
    sync)
        sync_logs
        ;;
    fetch)
        fetch_remote_logs "${2:-}" "${3:-}" "${4:-}"
        ;;
    rotate)
        for log in "$MAIN_LOG" "$UNIFIED_LOG" "$REDROID_LOG" "$LOGCAT_LOG" "$ADB_LOG" "$STREAMING_LOG" "$API_LOG"; do
            rotate_logs "$log"
        done
        ;;
    init)
        init_logs
        ;;
    --help|-h|help)
        usage
        ;;
    *)
        usage
        ;;
esac
