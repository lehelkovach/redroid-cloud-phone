#!/bin/bash
# get-troubleshoot-log.sh
# Retrieves the latest troubleshooting log from the remote instance

INSTANCE_IP="${1:-137.131.52.69}"
SSH_KEY="${HOME}/.ssh/redroid_oci"
SSH_USER="ubuntu"
LOG_DIR="${HOME}/redroid-troubleshoot-logs"

mkdir -p "$LOG_DIR"

echo "Fetching latest troubleshooting log from $INSTANCE_IP..."

# Get the latest log file
LATEST_LOG=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$INSTANCE_IP" \
    "ls -t /var/log/redroid-troubleshoot-*.log 2>/dev/null | head -1")

if [ -z "$LATEST_LOG" ]; then
    echo "No log file found. Run the troubleshooting script first:"
    echo "  sudo /opt/redroid-scripts/troubleshoot-redroid.sh"
    exit 1
fi

echo "Found log: $LATEST_LOG"
echo "Downloading..."

# Download the log file
scp -i "$SSH_KEY" -o StrictHostKeyChecking=no \
    "$SSH_USER@$INSTANCE_IP:$LATEST_LOG" \
    "$LOG_DIR/"

LOCAL_FILE="$LOG_DIR/$(basename "$LATEST_LOG")"
echo ""
echo "Log downloaded to: $LOCAL_FILE"
echo ""
echo "To view:"
echo "  cat $LOCAL_FILE"
echo ""
echo "Or open in editor:"
echo "  code $LOCAL_FILE"










