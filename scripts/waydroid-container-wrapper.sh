#!/bin/bash
# waydroid-container-wrapper.sh
# Wrapper script with logging for waydroid container start

LOG_FILE="/var/log/waydroid-container-wrapper.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $@" | tee -a "$LOG_FILE"
}

log "=== Waydroid Container Start Wrapper ==="
log "PID: $$"
log "User: $(whoami)"
log "PATH: $PATH"

# Check if waydroid is already running
log "Checking for existing waydroid processes..."
ps aux | grep -E "[p]ython.*waydroid|[w]aydroid container" | tee -a "$LOG_FILE"

# Check for lock files
log "Checking for lock files..."
ls -la /run/waydroid* /tmp/waydroid* /var/lib/waydroid/*.lock 2>/dev/null | tee -a "$LOG_FILE" || log "No lock files found"

# Check rootfs
log "Checking rootfs..."
ls -la /var/lib/waydroid/rootfs | tee -a "$LOG_FILE"

# Check images
log "Checking images..."
ls -lh /var/lib/waydroid/images/ | tee -a "$LOG_FILE"

# Check binder
log "Checking binder..."
mount | grep binder | tee -a "$LOG_FILE"
ls -la /dev/binderfs/ | tee -a "$LOG_FILE"

# Try to start container
log "Starting waydroid container..."
/usr/bin/waydroid container start 2>&1 | tee -a "$LOG_FILE"
EXIT_CODE=${PIPESTATUS[0]}
log "Exit code: $EXIT_CODE"

# Check if container started
sleep 5
log "Checking LXC container status..."
sudo lxc-ls -f | tee -a "$LOG_FILE"

log "=== End of wrapper ==="
exit $EXIT_CODE










