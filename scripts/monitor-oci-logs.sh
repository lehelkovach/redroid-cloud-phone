#!/bin/bash
# monitor-oci-logs.sh
# Monitors OCI instance logs and system health

INSTANCE_IP="${1:-161.153.55.58}"
SSH_KEY="${HOME}/.ssh/waydroid_oci"
LOG_FILE="${HOME}/.waydroid-monitor.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $@" | tee -a "$LOG_FILE"
}

log "=== Starting OCI Instance Monitoring ==="
log "Instance: $INSTANCE_IP"

while true; do
    log ""
    log "=== $(date) ==="
    
    # System health
    HEALTH=$(ssh -i "$SSH_KEY" -o ConnectTimeout=5 -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP \
        "echo 'MEM:' \$(free -h | grep Mem | awk '{print \$3\"/\"\$2}'); \
         echo 'DISK:' \$(df -h / | tail -1 | awk '{print \$5}'); \
         echo 'LOAD:' \$(uptime | awk -F'load average:' '{print \$2}'); \
         echo 'WAYDROID:' \$(waydroid status 2>&1 | head -1); \
         echo 'CONTAINER:' \$(sudo lxc-ls -f 2>&1 | grep waydroid || echo 'none'); \
         echo 'ADB:' \$(adb devices 2>&1 | grep -c device || echo '0');" 2>&1)
    
    log "$HEALTH"
    
    # Check for errors in logs
    ERRORS=$(ssh -i "$SSH_KEY" -o ConnectTimeout=5 -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP \
        "sudo journalctl --since '5 minutes ago' --no-pager | grep -i 'error\|fail\|critical' | tail -5" 2>&1)
    
    if [ -n "$ERRORS" ]; then
        log "RECENT ERRORS:"
        log "$ERRORS"
    fi
    
    # Check waydroid container process
    PROCESS=$(ssh -i "$SSH_KEY" -o ConnectTimeout=5 -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP \
        "ps aux | grep '[p]ython.*waydroid container' | head -1" 2>&1)
    
    if [ -n "$PROCESS" ]; then
        log "Waydroid container process: $PROCESS"
    else
        log "No waydroid container process running"
    fi
    
    sleep 30
done










