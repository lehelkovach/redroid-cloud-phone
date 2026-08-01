#!/usr/bin/env bash
#
# Make the phone VM's Appium server able to actually drive the device.
#
# A bare `appium` install answers GET /status but cannot create a session: it
# ships no drivers, and its ADB helper refuses to start without ANDROID_HOME.
# This script fixes both, idempotently:
#
#   1. installs the UiAutomator2 driver at a version compatible with the
#      installed Appium major (Appium 2.x needs driver <5; the current default
#      driver requires Appium 3.x and fails the compatibility check),
#   2. builds a minimal ANDROID_HOME whose platform-tools/adb points at the
#      system adb,
#   3. drops in a systemd override so appium.service sees ANDROID_HOME,
#      ANDROID_SDK_ROOT and APPIUM_HOME,
#   4. verifies a real session can be created against the device.
#
# Usage (on the phone VM):
#   sudo ./scripts/setup-appium-uiautomator2.sh [--adb-target 127.0.0.1:5555]
#
set -euo pipefail

ADB_TARGET="${ADB_TARGET:-127.0.0.1:5555}"
APPIUM_URL="${APPIUM_URL:-http://127.0.0.1:4723}"
ANDROID_HOME_DIR="${ANDROID_HOME_DIR:-/opt/android-sdk}"
APPIUM_HOME_DIR="${APPIUM_HOME_DIR:-/root/.appium}"
# Newest UiAutomator2 line whose peer range still admits Appium 2.x.
DRIVER_PIN_APPIUM2="${DRIVER_PIN_APPIUM2:-appium-uiautomator2-driver@4.2.9}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --adb-target) ADB_TARGET="$2"; shift 2 ;;
    --appium-url) APPIUM_URL="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

log() { printf '\n== %s\n' "$*"; }

if [[ "${EUID}" -ne 0 ]]; then
  echo "must run as root (sudo)" >&2
  exit 1
fi

command -v appium >/dev/null || { echo "appium not installed (npm i -g appium)" >&2; exit 1; }
command -v adb >/dev/null || { echo "adb not installed (apt install adb)" >&2; exit 1; }

APPIUM_VERSION="$(appium --version 2>/dev/null || echo unknown)"
APPIUM_MAJOR="${APPIUM_VERSION%%.*}"
log "appium ${APPIUM_VERSION} (major ${APPIUM_MAJOR})"

log "minimal ANDROID_HOME at ${ANDROID_HOME_DIR}"
mkdir -p "${ANDROID_HOME_DIR}/platform-tools"
ln -sfn "$(command -v adb)" "${ANDROID_HOME_DIR}/platform-tools/adb"

log "systemd override for appium.service"
install -d /etc/systemd/system/appium.service.d
cat > /etc/systemd/system/appium.service.d/override.conf <<EOF
[Service]
Environment=ANDROID_HOME=${ANDROID_HOME_DIR}
Environment=ANDROID_SDK_ROOT=${ANDROID_HOME_DIR}
Environment=APPIUM_HOME=${APPIUM_HOME_DIR}
EOF

export ANDROID_HOME="${ANDROID_HOME_DIR}"
export ANDROID_SDK_ROOT="${ANDROID_HOME_DIR}"
export APPIUM_HOME="${APPIUM_HOME_DIR}"

if appium driver list --installed 2>&1 | grep -q "uiautomator2.*installed"; then
  log "uiautomator2 driver already installed"
else
  log "installing uiautomator2 driver"
  # Appium 2.x cannot take the default (Appium 3) driver, so pin it.
  if [[ "${APPIUM_MAJOR}" == "2" ]]; then
    appium driver install --source=npm "${DRIVER_PIN_APPIUM2}"
  else
    appium driver install uiautomator2
  fi
fi

log "restarting appium.service"
systemctl daemon-reload
systemctl enable --now appium >/dev/null 2>&1 || true
systemctl restart appium

for _ in $(seq 1 30); do
  if curl -fsS -m 3 "${APPIUM_URL}/status" >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS -m 5 "${APPIUM_URL}/status" || { echo "appium did not come up" >&2; exit 1; }

log "verifying a real UiAutomator2 session against ${ADB_TARGET}"
adb connect "${ADB_TARGET}" >/dev/null 2>&1 || true
SESSION_JSON="$(curl -sS -m 180 -H 'Content-Type: application/json' \
  -X POST "${APPIUM_URL}/session" -d "{
    \"capabilities\": {\"alwaysMatch\": {
      \"platformName\": \"Android\",
      \"appium:automationName\": \"UiAutomator2\",
      \"appium:udid\": \"${ADB_TARGET}\",
      \"appium:newCommandTimeout\": 120,
      \"appium:uiautomator2ServerInstallTimeout\": 120000
    }, \"firstMatch\": [{}]}}")"

SESSION_ID="$(printf '%s' "${SESSION_JSON}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin).get("value",{}).get("sessionId",""))' 2>/dev/null || true)"

if [[ -z "${SESSION_ID}" ]]; then
  echo "FAILED to create UiAutomator2 session:" >&2
  printf '%s\n' "${SESSION_JSON}" | head -c 800 >&2
  exit 1
fi

ELEMENTS="$(curl -sS -m 30 "${APPIUM_URL}/session/${SESSION_ID}/source" \
  | python3 -c 'import sys,json,re; print(len(re.findall(r"<[a-zA-Z.]+", json.load(sys.stdin)["value"])))')"
curl -sS -m 30 -X DELETE "${APPIUM_URL}/session/${SESSION_ID}" >/dev/null

log "OK — session created, page source had ${ELEMENTS} nodes"
echo "Appium UiAutomator2 is ready on ${APPIUM_URL} (udid ${ADB_TARGET})."
