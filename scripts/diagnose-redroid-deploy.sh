#!/bin/bash
# Step-wise deployment diagnostics for OCI Redroid instance.
# Usage: ./scripts/diagnose-redroid-deploy.sh <instance-id-or-name> [public-ip]

set -euo pipefail

INSTANCE_REF="${1:-}"
PUBLIC_IP="${2:-}"
OUTPUT_DIR="${OUTPUT_DIR:-/tmp}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/redroid_oci}"
OCI_CONFIG="${OCI_CONFIG:-$HOME/.oci/config}"
SECURITY_TOKEN_FILE="${SECURITY_TOKEN_FILE:-$HOME/.oci/sessions/DEFAULT/token}"
OCI_AUTH_ARGS=()

if [[ -z "$INSTANCE_REF" ]]; then
  echo "Usage: $0 <instance-id-or-name> [public-ip]" >&2
  exit 1
fi

if [[ -f "$SECURITY_TOKEN_FILE" ]]; then
  OCI_AUTH_ARGS+=(--auth security_token)
fi

TENANCY_OCID=""
if [[ -f "$OCI_CONFIG" ]]; then
  TENANCY_OCID="$(awk -F= '/^tenancy=/{print $2}' "$OCI_CONFIG" | tail -n1)"
fi
COMPARTMENT_ID="${COMPARTMENT_ID:-$TENANCY_OCID}"

mkdir -p "$OUTPUT_DIR"
LOG_FILE="$OUTPUT_DIR/diagnose-redroid-$(date +%Y%m%d-%H%M%S).log"

log() { echo "$@" | tee -a "$LOG_FILE"; }
step() { log ""; log "==> $*"; }
ok() { log "[OK] $*"; }
warn() { log "[WARN] $*"; }
fail() { log "[FAIL] $*"; }

resolve_instance_id() {
  local ref="$1"
  if [[ "$ref" == ocid1.instance* ]]; then
    echo "$ref"
    return 0
  fi
  if ! command -v oci &>/dev/null; then
    return 1
  fi
  if [[ -z "$COMPARTMENT_ID" ]]; then
    return 1
  fi
  oci compute instance list "${OCI_AUTH_ARGS[@]}" \
    --compartment-id "$COMPARTMENT_ID" \
    --output json 2>/dev/null > /tmp/oci-instances.json || true
  python3 - "$ref" <<'PY'
import json, sys
name = sys.argv[1]
try:
    with open('/tmp/oci-instances.json') as f:
        data = json.load(f).get("data", [])
    for inst in data:
        if inst.get("display-name") == name:
            print(inst.get("id", ""))
            break
except Exception:
    pass
PY
}

resolve_public_ip() {
  local instance_id="$1"
  if ! command -v oci &>/dev/null; then
    return 1
  fi
  oci compute instance list-vnics "${OCI_AUTH_ARGS[@]}" \
    --instance-id "$instance_id" \
    --query 'data[0]."public-ip"' \
    --raw-output 2>/dev/null || true
}

check_port() {
  local host="$1"
  local port="$2"
  if command -v nc &>/dev/null; then
    if nc -vz "$host" "$port" &>/dev/null; then
      ok "Port $port reachable on $host"
      return 0
    fi
  else
    if (echo >"/dev/tcp/$host/$port") >/dev/null 2>&1; then
      ok "Port $port reachable on $host"
      return 0
    fi
  fi
  warn "Port $port not reachable on $host"
  return 1
}

step "Resolve instance and IP"
INSTANCE_ID="$(resolve_instance_id "$INSTANCE_REF")"
if [[ -z "$INSTANCE_ID" ]]; then
  fail "Could not resolve instance id from: $INSTANCE_REF"
  exit 1
fi
ok "Instance: $INSTANCE_ID"

if [[ -z "$PUBLIC_IP" ]]; then
  PUBLIC_IP="$(resolve_public_ip "$INSTANCE_ID")"
fi
if [[ -n "$PUBLIC_IP" && "$PUBLIC_IP" != "null" ]]; then
  ok "Public IP: $PUBLIC_IP"
else
  warn "Public IP not found"
fi

step "Instance lifecycle state"
if command -v oci &>/dev/null && [[ -n "$COMPARTMENT_ID" ]]; then
  state="$(oci compute instance get "${OCI_AUTH_ARGS[@]}" --instance-id "$INSTANCE_ID" --query 'data.\"lifecycle-state\"' --raw-output 2>/dev/null || true)"
  if [[ -n "$state" ]]; then
    ok "Lifecycle state: $state"
  else
    warn "Could not fetch lifecycle state"
  fi
else
  warn "OCI CLI or COMPARTMENT_ID missing; skipping lifecycle state"
fi

step "Serial console log snapshot"
if [[ -x "$(dirname "$0")/get-oci-console-log.sh" ]]; then
  if "$(dirname "$0")/get-oci-console-log.sh" "$INSTANCE_ID" "$OUTPUT_DIR" >>"$LOG_FILE" 2>&1; then
    ok "Console history captured"
  else
    warn "Console history capture failed"
  fi
else
  warn "get-oci-console-log.sh not found"
fi

if [[ -n "$PUBLIC_IP" && "$PUBLIC_IP" != "null" ]]; then
  step "Network reachability"
  check_port "$PUBLIC_IP" 22 || true
  check_port "$PUBLIC_IP" 5555 || true
  check_port "$PUBLIC_IP" 5900 || true
  check_port "$PUBLIC_IP" 8080 || true

  step "HTTP health (Control API)"
  if command -v curl &>/dev/null; then
    if curl -s --max-time 5 "http://$PUBLIC_IP:8080/health" >>"$LOG_FILE" 2>&1; then
      ok "Control API responded"
    else
      warn "Control API did not respond"
    fi
  else
    warn "curl not installed; skipping Control API check"
  fi

  step "SSH access"
  if [[ -f "$SSH_KEY" ]]; then
    if ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 ubuntu@"$PUBLIC_IP" "echo ok" &>/dev/null; then
      ok "SSH available"
    else
      warn "SSH not available (banner exchange or auth failure)"
    fi
  else
    warn "SSH key not found at $SSH_KEY"
  fi

  if [[ -f "$SSH_KEY" ]] && ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 ubuntu@"$PUBLIC_IP" "echo ok" &>/dev/null; then
    step "Remote service status (SSH)"
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no ubuntu@"$PUBLIC_IP" <<'ENDSSH' >>"$LOG_FILE" 2>&1 || true
set -e
echo "--- uname ---"
uname -a
echo "--- uptime ---"
uptime
echo "--- disk ---"
df -h /
echo "--- memory ---"
free -h
echo "--- docker ps ---"
sudo docker ps -a || true
echo "--- systemd targets ---"
sudo systemctl --no-pager status redroid-cloud-phone.target || true
sudo systemctl --no-pager status redroid-container.service || true
sudo systemctl --no-pager status control-api.service || true
echo "--- journal (redroid-container) ---"
sudo journalctl -u redroid-container.service --no-pager -n 200 || true
echo "--- journal (control-api) ---"
sudo journalctl -u control-api.service --no-pager -n 200 || true
echo "--- docker logs (redroid) ---"
sudo docker logs --tail 200 redroid || true
echo "--- devices ---"
ls -la /dev/video* || true
ls -la /dev/snd || true
echo "--- kernel modules ---"
lsmod | egrep 'v4l2loopback|snd_aloop' || true
ENDSSH
    ok "Remote logs collected"
  else
    warn "Skipping remote logs (SSH unavailable)"
  fi
else
  warn "No public IP; skipping network/SSH checks"
fi

step "Summary"
log "Diagnostics log: $LOG_FILE"
log "Next: review this log for the first failed step and its error output."
