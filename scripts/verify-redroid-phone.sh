#!/bin/bash
# verify-redroid-phone.sh
# Check Control API + optional GApps packages on a Redroid automation host.
#
# Usage:
#   ./scripts/verify-redroid-phone.sh --vm <IP>
#   ./scripts/verify-redroid-phone.sh --local

set -euo pipefail

VM=""
LOCAL="false"
PORT="${API_PORT:-8080}"
REQUIRE_GAPPS="${REQUIRE_GAPPS:-false}"

usage() {
    cat <<'EOF'
Usage:
  ./scripts/verify-redroid-phone.sh --vm <IP> [--require-gapps]
  ./scripts/verify-redroid-phone.sh --local
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --vm) VM="${2:-}"; shift 2 ;;
        --local) LOCAL="true"; shift ;;
        --require-gapps) REQUIRE_GAPPS="true"; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

if [[ "$LOCAL" == "true" ]]; then
    BASE="http://127.0.0.1:${PORT}"
else
    [[ -n "$VM" ]] || { usage; exit 1; }
    BASE="http://${VM}:${PORT}"
fi

echo "Checking $BASE/health ..."
BODY="$(curl -fsS --max-time 10 "$BASE/health")"
echo "$BODY"

python3 - "$BODY" "$REQUIRE_GAPPS" <<'PY'
import json, sys
body = json.loads(sys.argv[1])
require = sys.argv[2].lower() in {"1", "true", "yes"}
status = body.get("status")
if status not in {"healthy", "ok", "degraded"}:
    raise SystemExit(f"unexpected health status: {status}")
gapps = body.get("gapps") or {}
print("gapps:", json.dumps(gapps))
if require and not gapps.get("ready"):
    raise SystemExit("GApps not ready (com.android.vending / GMS missing)")
print("redroid phone health: PASS")
PY
