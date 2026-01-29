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
REDROID_LOG="$LOG_DIR/redroid.log"
ADB_LOG="$LOG_DIR/adb.log"
LOGCAT_LOG="$LOG_DIR/logcat.log"
STREAMING_LOG="$LOG_DIR/streaming.log"

# PID files
LOGCAT_PID_FILE="/var/run/cloud-phone-logcat.pid"
CONTAINER_LOG_PID_FILE="/var/run/cloud-phone-container-log.pid"
REMOTE_STREAM_PID_FILE="/var/run/cloud-phone-remote-stream.pid"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$MAIN_LOG"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$MAIN_LOG"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$MAIN_LOG" >&2; }

# Initialize log directory
init_logs() {
    sudo mkdir -p "$LOG_DIR"
    sudo chown -R "${USER:-root}:${USER:-root}" "$LOG_DIR" 2>/dev/null || true
    touch "$MAIN_LOG" "$REDROID_LOG" "$ADB_LOG" "$LOGCAT_LOG" "$STREAMING_LOG"
    log_info "Log directory initialized: $LOG_DIR"
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
    
    # Start logcat in background
    (
        while true; do
            adb -s "$ADB_CONNECT" logcat "$LOGCAT_FILTER" 2>/dev/null >> "$LOGCAT_LOG"
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
        docker logs -f redroid 2>&1 | while read -r line; do
            echo "$(date '+%Y-%m-%d %H:%M:%S') $line" >> "$REDROID_LOG"
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

# Capture service logs to files
capture_service_logs() {
    log_info "Capturing service logs..."
    
    # Capture recent logs from systemd services
    for service in nginx-rtmp ffmpeg-bridge control-api redroid-container; do
        local log_file="$LOG_DIR/${service}.log"
        if systemctl is-active --quiet "$service" 2>/dev/null; then
            journalctl -u "$service" --no-pager -n 1000 >> "$log_file" 2>/dev/null || true
        fi
    done
    
    log_info "Service logs captured"
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
    capture_service_logs
    start_remote_stream
    
    log_info "Log collection started"
}

# Stop all log collection
stop_all() {
    log_info "Stopping log collection..."
    
    stop_remote_stream
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
    
    if [[ -f "$REMOTE_STREAM_PID_FILE" ]] && kill -0 "$(cat "$REMOTE_STREAM_PID_FILE")" 2>/dev/null; then
        echo "  ✓ Remote stream: Running (PID: $(cat "$REMOTE_STREAM_PID_FILE"))"
    else
        echo "  ✗ Remote stream: Stopped"
    fi
    
    echo ""
    echo "Log Files:"
    for log in "$MAIN_LOG" "$REDROID_LOG" "$LOGCAT_LOG" "$ADB_LOG" "$STREAMING_LOG"; do
        if [[ -f "$log" ]]; then
            local size=$(du -h "$log" | cut -f1)
            local lines=$(wc -l < "$log")
            echo "  $log: $size ($lines lines)"
        fi
    done
    
    echo ""
    echo "Remote Sync:"
    echo "  Enabled: $REMOTE_ENABLED"
    [[ "$REMOTE_ENABLED" == "true" ]] && echo "  Host: $REMOTE_USER@$REMOTE_HOST"
}

# Tail all logs
tail_logs() {
    local lines="${1:-50}"
    
    echo "=== Tailing logs (Ctrl+C to exit) ==="
    tail -f -n "$lines" "$MAIN_LOG" "$REDROID_LOG" "$LOGCAT_LOG" 2>/dev/null || \
    tail -f -n "$lines" "$MAIN_LOG" 2>/dev/null || \
    echo "No logs available"
}

# Usage
usage() {
    cat <<EOF
Log Collector - Capture and route logs from Redroid Cloud Phone

Usage: $0 <command> [options]

Commands:
  start         Start all log collection
  stop          Stop all log collection
  status        Show collector status
  tail [N]      Tail all logs (default: 50 lines)
  sync          Sync logs to remote host
  fetch [HOST]  Fetch logs from remote VM
  rotate        Rotate log files now
  init          Initialize log directory

Configuration:
  Config file: $CONFIG_FILE
  Log directory: $LOG_DIR

Environment Variables:
  CLOUD_PHONE_CONFIG    Path to config file

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
    sync)
        sync_logs
        ;;
    fetch)
        fetch_remote_logs "${2:-}" "${3:-}" "${4:-}"
        ;;
    rotate)
        for log in "$MAIN_LOG" "$REDROID_LOG" "$LOGCAT_LOG" "$ADB_LOG" "$STREAMING_LOG"; do
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
