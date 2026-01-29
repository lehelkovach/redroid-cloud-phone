#!/bin/bash
# deploy-master.sh
# Master script to deploy redroid cloud phone from start to finish
#
# Usage: ./deploy-master.sh [instance-name]

set -euo pipefail

INSTANCE_NAME="${1:-redroid-test-$(date +%s)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/redroid_oci}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

log "Creating instance: $INSTANCE_NAME"
"$SCRIPT_DIR/scripts/create-instance.sh" "$INSTANCE_NAME"

if [[ ! -f /tmp/redroid-instance-info.txt ]]; then
    echo "Instance info not found at /tmp/redroid-instance-info.txt" >&2
    exit 1
fi

INSTANCE_INFO=$(cat /tmp/redroid-instance-info.txt)
INSTANCE_OCID=$(echo "$INSTANCE_INFO" | cut -d'|' -f1)
PUBLIC_IP=$(echo "$INSTANCE_INFO" | cut -d'|' -f2)

log "Instance OCID: $INSTANCE_OCID"
log "Public IP: $PUBLIC_IP"

log "Deploying redroid to instance"
"$SCRIPT_DIR/scripts/deploy-to-instance.sh" "$PUBLIC_IP" "$SSH_KEY"

log "Running full test suite"
"$SCRIPT_DIR/scripts/test-full-suite.sh" "$PUBLIC_IP" "$SSH_KEY"

log "Done"
