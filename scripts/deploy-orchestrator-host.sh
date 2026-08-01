#!/usr/bin/env bash
# Install orchestrator on a dedicated host and register existing phone Control APIs.
# Usage:
#   PHONE_API_URLS=http://10.0.1.127:8080 ./scripts/deploy-orchestrator-host.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PHONE_API_URLS="${PHONE_API_URLS:-${ORCH_REGISTER_API_URLS:-}}"
[[ -n "$PHONE_API_URLS" ]] || { echo "Set PHONE_API_URLS=http://<phone-private-ip>:8080" >&2; exit 1; }

sudo useradd -r -m -d /opt/cloud-phone-orchestrator -s /usr/sbin/nologin cloudphone 2>/dev/null || true
sudo mkdir -p /opt/cloud-phone-orchestrator /etc/cloud-phone
sudo cp "$ROOT/orchestrator/server.py" "$ROOT/orchestrator/launch_config.py" "$ROOT/orchestrator/requirements.txt" /opt/cloud-phone-orchestrator/
if [[ ! -x /opt/cloud-phone-orchestrator/venv/bin/python ]]; then
  sudo python3 -m venv /opt/cloud-phone-orchestrator/venv
fi
sudo /opt/cloud-phone-orchestrator/venv/bin/pip install -q -r /opt/cloud-phone-orchestrator/requirements.txt
sudo tee /etc/cloud-phone/orchestrator.env >/dev/null <<ENV
ORCH_HOST=0.0.0.0
ORCH_PORT=8090
ORCH_DEPLOY_MODE=mock
ORCH_MAX_INSTANCES=3
ORCH_REGISTER_API_URLS=${PHONE_API_URLS}
ORCH_MOCK_API_URL=$(echo "$PHONE_API_URLS" | cut -d, -f1)
ENV
sudo cp "$ROOT/systemd/orchestrator.service" /etc/systemd/system/orchestrator.service
sudo sed -i 's|^# EnvironmentFile=.*|EnvironmentFile=-/etc/cloud-phone/orchestrator.env|' /etc/systemd/system/orchestrator.service || true
grep -q EnvironmentFile /etc/systemd/system/orchestrator.service || \
  sudo sed -i '/Environment=ORCH_MAX_INSTANCES/a EnvironmentFile=-/etc/cloud-phone/orchestrator.env' /etc/systemd/system/orchestrator.service
sudo chown -R cloudphone:cloudphone /opt/cloud-phone-orchestrator
sudo systemctl daemon-reload
sudo systemctl enable --now orchestrator.service
curl -sf http://127.0.0.1:8090/health
echo
curl -sS http://127.0.0.1:8090/instances
echo
