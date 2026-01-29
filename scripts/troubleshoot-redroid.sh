#!/bin/bash
# troubleshoot-redroid.sh
# Collects diagnostics for Redroid Cloud Phone
#
# Usage: sudo bash /opt/redroid-scripts/troubleshoot-redroid.sh

set -euo pipefail

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOG_FILE="/var/log/redroid-troubleshoot-${TIMESTAMP}.log"
LATEST_LOG="/var/log/redroid-troubleshoot-latest.log"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "=== Redroid Troubleshoot ==="
log "Host: $(hostname)"
log "Kernel: $(uname -r)"
log "Uptime: $(uptime -p)"

log ""
log "=== Services ==="
systemctl status docker --no-pager | head -20 | tee -a "$LOG_FILE" || true
systemctl status redroid-container --no-pager | head -20 | tee -a "$LOG_FILE" || true
systemctl status nginx-rtmp --no-pager | head -20 | tee -a "$LOG_FILE" || true
systemctl status ffmpeg-bridge --no-pager | head -20 | tee -a "$LOG_FILE" || true
systemctl status control-api --no-pager | head -20 | tee -a "$LOG_FILE" || true

log ""
log "=== Docker ==="
docker ps -a | tee -a "$LOG_FILE" || true
docker logs --tail 50 redroid 2>/dev/null | tee -a "$LOG_FILE" || true

log ""
log "=== Kernel Modules ==="
lsmod | grep -E "v4l2loopback|snd_aloop|binder" | tee -a "$LOG_FILE" || true

log ""
log "=== Devices ==="
ls -la /dev/video42 2>/dev/null | tee -a "$LOG_FILE" || true
aplay -l 2>/dev/null | grep -A2 Loopback | tee -a "$LOG_FILE" || true
mount | grep binderfs | tee -a "$LOG_FILE" || true

log ""
log "=== Ports ==="
ss -tlnp | grep -E ":(1935|5555|5900|8080)" | tee -a "$LOG_FILE" || true

log ""
log "=== API Health ==="
curl -s http://127.0.0.1:8080/health | tee -a "$LOG_FILE" || true

log ""
log "=== Recent Errors ==="
journalctl --since "15 minutes ago" --no-pager | grep -i "error\\|fail\\|critical" | tail -20 | tee -a "$LOG_FILE" || true

ln -sfn "$LOG_FILE" "$LATEST_LOG"
log ""
log "Saved: $LOG_FILE"
log "Latest: $LATEST_LOG"
