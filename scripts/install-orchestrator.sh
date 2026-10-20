#!/usr/bin/env bash
# Install or refresh the orchestrator on this host from a checkout of this repo.
#   sudo ./scripts/install-orchestrator.sh [--pool /path/to/pool.json]
# Idempotent. Never prints a token.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POOL_SRC=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pool) POOL_SRC="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
log() { echo "[install-orchestrator] $*"; }
log "[1/5] python + venv"
if ! command -v python3 >/dev/null; then apt-get update -qq && apt-get install -y -qq python3 python3-venv python3-pip curl; fi
mkdir -p /opt/cloud-phone-orchestrator/orchestrator /etc/cloud-phone /var/lib/cloud-phone-orchestrator /etc/default
log "[2/5] code"
cp "$PROJECT_ROOT/orchestrator/"*.py /opt/cloud-phone-orchestrator/orchestrator/
cp "$PROJECT_ROOT/orchestrator/requirements.txt" /opt/cloud-phone-orchestrator/
[ -d /opt/cloud-phone-orchestrator/venv ] || python3 -m venv /opt/cloud-phone-orchestrator/venv
/opt/cloud-phone-orchestrator/venv/bin/pip install -q --upgrade pip
/opt/cloud-phone-orchestrator/venv/bin/pip install -q -r /opt/cloud-phone-orchestrator/requirements.txt
log "[3/5] config"
if [[ -n "$POOL_SRC" ]]; then cp "$POOL_SRC" /etc/cloud-phone/pool.json; fi
[ -f /etc/cloud-phone/pool.json ] || cp "$PROJECT_ROOT/config/pool.example.json" /etc/cloud-phone/pool.json
if [[ ! -f /etc/default/cloud-phone-orchestrator ]]; then
  cp "$PROJECT_ROOT/config/orchestrator.env.example" /etc/default/cloud-phone-orchestrator
  chmod 600 /etc/default/cloud-phone-orchestrator
  log "wrote /etc/default/cloud-phone-orchestrator — set ORCH_API_TOKEN and ORCH_CONTROL_API_TOKEN"
fi
log "[4/5] unit"
cp "$PROJECT_ROOT/systemd/cloud-phone-orchestrator.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable cloud-phone-orchestrator.service >/dev/null
systemctl restart cloud-phone-orchestrator.service
log "[5/5] verify"
for i in $(seq 1 15); do
  if curl -sf http://127.0.0.1:8090/health >/dev/null; then
    curl -s http://127.0.0.1:8090/health | head -c 400; echo
    curl -s http://127.0.0.1:8090/pool | head -c 400; echo
    log "orchestrator is up on :8090"
    exit 0
  fi
  sleep 2
done
log "orchestrator did not answer on :8090"; journalctl -u cloud-phone-orchestrator -n 30 --no-pager; exit 1
