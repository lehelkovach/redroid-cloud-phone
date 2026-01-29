#!/bin/bash
# Fetch OCI instance serial console log to diagnose boot/launch issues.
# Usage: ./scripts/get-oci-console-log.sh <instance-id-or-name> [output-dir]

set -euo pipefail

INSTANCE_REF="${1:-}"
OUTPUT_DIR="${2:-/tmp}"

if [[ -z "$INSTANCE_REF" ]]; then
  echo "Usage: $0 <instance-id-or-name> [output-dir]" >&2
  exit 1
fi

if ! command -v oci &>/dev/null; then
  echo "Error: OCI CLI not installed" >&2
  exit 1
fi

OCI_CONFIG="${OCI_CONFIG:-$HOME/.oci/config}"
if [[ ! -f "$OCI_CONFIG" ]]; then
  echo "Error: OCI config not found at $OCI_CONFIG" >&2
  exit 1
fi

TENANCY_OCID="$(awk -F= '/^tenancy=/{print $2}' "$OCI_CONFIG" | tail -n1)"
COMPARTMENT_ID="${COMPARTMENT_ID:-$TENANCY_OCID}"
SECURITY_TOKEN_FILE="${SECURITY_TOKEN_FILE:-$HOME/.oci/sessions/DEFAULT/token}"
OCI_AUTH_ARGS=()
if [[ -f "$SECURITY_TOKEN_FILE" ]]; then
  OCI_AUTH_ARGS+=(--auth security_token)
fi

resolve_instance_id() {
  local ref="$1"
  if [[ "$ref" == ocid1.instance* ]]; then
    echo "$ref"
    return 0
  fi

  local instance_id
  instance_id="$(
    oci compute instance list "${OCI_AUTH_ARGS[@]}" \
      --compartment-id "$COMPARTMENT_ID" \
      --output json 2>/dev/null > /tmp/oci-instances.json
    python3 - "$ref" <<'PY'
import json, sys
name = sys.argv[1]
with open('/tmp/oci-instances.json') as f:
    data = json.load(f).get("data", [])
for inst in data:
    if inst.get("display-name") == name:
        print(inst.get("id", ""))
        break
PY
  )"

  if [[ -z "$instance_id" ]]; then
    echo "Error: instance not found for name: $ref" >&2
    exit 1
  fi
  echo "$instance_id"
}

INSTANCE_ID="$(resolve_instance_id "$INSTANCE_REF")"

echo "Instance: $INSTANCE_ID"
echo "Compartment: $COMPARTMENT_ID"
echo ""

HISTORY_ID="$(
  oci compute console-history list "${OCI_AUTH_ARGS[@]}" \
    --compartment-id "$COMPARTMENT_ID" \
    --instance-id "$INSTANCE_ID" \
    --sort-by TIMECREATED \
    --sort-order DESC \
    --query 'data[0].id' \
    --raw-output 2>/dev/null || true
)"

if [[ -z "$HISTORY_ID" ]] || [[ "$HISTORY_ID" == "null" ]]; then
  echo "No console history found. Capturing a new snapshot..."
  HISTORY_ID="$(
    oci compute console-history capture "${OCI_AUTH_ARGS[@]}" \
      --instance-id "$INSTANCE_ID" \
      --query 'data.id' \
      --raw-output
  )"

  for i in {1..60}; do
    state="$(
      oci compute console-history get "${OCI_AUTH_ARGS[@]}" \
        --instance-console-history-id "$HISTORY_ID" \
        --query 'data."lifecycle-state"' \
        --raw-output
    )"
    if [[ "$state" == "SUCCEEDED" ]]; then
      break
    fi
    sleep 2
    if [[ $i -eq 60 ]]; then
      echo "Error: console history capture did not complete" >&2
      exit 1
    fi
  done
fi

mkdir -p "$OUTPUT_DIR"
OUT_FILE="$OUTPUT_DIR/console-history-${INSTANCE_ID##*.}-$(date +%Y%m%d-%H%M%S).log"

oci compute console-history get-content "${OCI_AUTH_ARGS[@]}" \
  --instance-console-history-id "$HISTORY_ID" \
  --file "$OUT_FILE"

echo ""
echo "Console log saved to: $OUT_FILE"
echo ""
echo "---- last 200 lines ----"
tail -n 200 "$OUT_FILE" || true
