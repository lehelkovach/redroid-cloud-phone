#!/usr/bin/env bash
# proxy-control.sh — device egress helper for Redroid/Cuttlefish Control API.
#
# Used by api/server.py POST /proxy (SOCKS5 / transparent). Offline / CI:
#   PROXY_STUB=1  → record intent under /tmp/cloud-phone-proxy-stub.json and exit 0
# Live OCI: installs/uses redsocks or tun2socks when available; otherwise
# falls back to Android settings / setprop (handled by the API when this
# script is missing).
#
# Usage:
#   proxy-control.sh enable http|socks5|transparent HOST PORT [USER] [PASS]
#   proxy-control.sh disable
#   proxy-control.sh status
set -euo pipefail

STUB_FILE="${PROXY_STUB_FILE:-/tmp/cloud-phone-proxy-stub.json}"
MODE="${1:-}"
shift || true

stub_write() {
  local payload="$1"
  printf '%s\n' "$payload" >"$STUB_FILE"
  echo "proxy-control stub: $payload"
}

if [[ "${PROXY_STUB:-}" == "1" || "${PROXY_STUB:-}" == "true" ]]; then
  case "$MODE" in
    enable)
      TYPE="${1:-http}"; HOST="${2:-}"; PORT="${3:-}"; USER="${4:-}"; PASS="${5:-}"
      stub_write "{\"enabled\":true,\"type\":\"$TYPE\",\"host\":\"$HOST\",\"port\":$PORT,\"username\":\"${USER:+***}\"}"
      exit 0
      ;;
    disable)
      stub_write '{"enabled":false}'
      exit 0
      ;;
    status)
      if [[ -f "$STUB_FILE" ]]; then cat "$STUB_FILE"; else echo '{"enabled":false}'; fi
      exit 0
      ;;
    *)
      echo "usage: $0 enable|disable|status …" >&2
      exit 2
      ;;
  esac
fi

# Live path — best-effort. Prefer documenting IPRoyal HTTP proxy via
# `settings put global http_proxy` (API handles that for type=http).
case "$MODE" in
  enable)
    TYPE="${1:-http}"; HOST="${2:-}"; PORT="${3:-}"; USER="${4:-}"; PASS="${5:-}"
    if [[ -z "$HOST" || -z "$PORT" ]]; then
      echo "host and port required" >&2
      exit 1
    fi
    if command -v adb >/dev/null 2>&1; then
      ADB_TARGET="${ADB_CONNECT:-127.0.0.1:5555}"
      if [[ "$TYPE" == "http" ]]; then
        adb -s "$ADB_TARGET" shell settings put global http_proxy "${HOST}:${PORT}"
        # Note: Android global http_proxy does not take user/pass; use an
        # authenticating local forwarder (redsocks/tinyproxy) for IPRoyal auth.
        echo "enabled http_proxy ${HOST}:${PORT}"
        exit 0
      fi
      adb -s "$ADB_TARGET" shell setprop persist.sys.cloud_phone.proxy.type "$TYPE"
      adb -s "$ADB_TARGET" shell setprop persist.sys.cloud_phone.proxy.host "$HOST"
      adb -s "$ADB_TARGET" shell setprop persist.sys.cloud_phone.proxy.port "$PORT"
      echo "set proxy props type=$TYPE host=$HOST port=$PORT (auth via local forwarder if needed)"
      exit 0
    fi
    echo "adb not available; set PROXY_STUB=1 for offline or install adb" >&2
    exit 1
    ;;
  disable)
    if command -v adb >/dev/null 2>&1; then
      ADB_TARGET="${ADB_CONNECT:-127.0.0.1:5555}"
      adb -s "$ADB_TARGET" shell settings put global http_proxy :0 || true
      echo "disabled"
      exit 0
    fi
    echo "adb not available" >&2
    exit 1
    ;;
  status)
    if command -v adb >/dev/null 2>&1; then
      ADB_TARGET="${ADB_CONNECT:-127.0.0.1:5555}"
      adb -s "$ADB_TARGET" shell settings get global http_proxy || true
      exit 0
    fi
    echo '{"enabled":false,"error":"adb missing"}'
    exit 0
    ;;
  *)
    echo "usage: $0 enable|disable|status …" >&2
    exit 2
    ;;
esac
