#!/bin/bash
# monitor-oci-logs.sh
# Monitors OCI instance logs and system health

INSTANCE_IP="${1:-137.131.52.69}"
SSH_KEY="${HOME}/.ssh/redroid_oci"
LOG_FILE="${HOME}/.redroid-monitor.log"

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
         echo 'REDROID:' \$(sudo docker ps --format '{{.Names}}:{{.Status}}' | grep -m1 '^redroid' || echo 'none'); \
         echo 'ADB:' \$(adb devices 2>&1 | grep -c device || echo '0');" 2>&1)
    
    log "$HEALTH"
    
    # Check for errors in logs
    ERRORS=$(ssh -i "$SSH_KEY" -o ConnectTimeout=5 -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP \
        "sudo journalctl --since '5 minutes ago' --no-pager | grep -i 'error\|fail\|critical' | tail -5" 2>&1)
    
    if [ -n "$ERRORS" ]; then
        log "RECENT ERRORS:"
        log "$ERRORS"
    fi
    
    # Check redroid container process
    PROCESS=$(ssh -i "$SSH_KEY" -o ConnectTimeout=5 -o StrictHostKeyChecking=no ubuntu@$INSTANCE_IP \
        "ps aux | grep '[d]ocker.*redroid' | head -1" 2>&1)
    
    if [ -n "$PROCESS" ]; then
        log "Redroid container process: $PROCESS"
    else
        log "No redroid container process running"
    fi
    
    sleep 30
done










