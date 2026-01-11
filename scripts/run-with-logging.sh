#!/bin/bash
# run-with-logging.sh
# Wrapper script to run any command with logging
# Usage: ./run-with-logging.sh <log_file> <command> [args...]

set -euo pipefail

LOG_FILE="$1"
shift
COMMAND="$1"
shift
ARGS=("$@")

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    local level="$1"
    shift
    local msg="$*"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] [$level] $msg" | tee -a "$LOG_FILE"
}

log "INFO" "Executing: $COMMAND ${ARGS[*]}"
log "INFO" "Working directory: $(pwd)"

# Run command and capture output
if "$COMMAND" "${ARGS[@]}" >> "$LOG_FILE" 2>&1; then
    log "SUCCESS" "Command completed successfully"
    exit 0
else
    EXIT_CODE=$?
    log "ERROR" "Command failed with exit code: $EXIT_CODE"
    exit $EXIT_CODE
fi


