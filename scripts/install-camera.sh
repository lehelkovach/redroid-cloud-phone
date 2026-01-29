#!/usr/bin/env bash
set -euo pipefail

APP_ID="net.sourceforge.opencamera"
APK_URL=""
APK_PATH=""

usage() {
  cat <<'EOF'
Install a camera app (Open Camera) in the Redroid container via ADB.

Usage:
  ./install-camera.sh
  ./install-camera.sh --apk-url <URL>
  ./install-camera.sh --apk-path </path/to/apk>

Notes:
- Defaults to downloading the latest Open Camera APK from F-Droid.
- Requires adb to be available and connected to the device.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apk-url) APK_URL="$2"; shift 2 ;;
    --apk-path) APK_PATH="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

if ! command -v adb >/dev/null 2>&1; then
  echo "adb not found. Install Android platform tools." >&2
  exit 1
fi

ADB_TARGET="${ADB_CONNECT:-127.0.0.1:5555}"
adb connect "$ADB_TARGET" >/dev/null 2>&1 || true
adb -s "$ADB_TARGET" get-state >/dev/null 2>&1 || {
  echo "ADB not connected to $ADB_TARGET" >&2
  exit 1
}

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

if [[ -n "$APK_PATH" ]]; then
  APK_FILE="$APK_PATH"
elif [[ -n "$APK_URL" ]]; then
  APK_FILE="$TMP_DIR/opencamera.apk"
  curl -L "$APK_URL" -o "$APK_FILE"
else
  APK_FILE="$TMP_DIR/opencamera.apk"
  python3 - <<'PY' "$APK_FILE"
import json
import sys
import urllib.request

apk_out = sys.argv[1]
index_url = "https://f-droid.org/repo/index-v1.json"
data = json.loads(urllib.request.urlopen(index_url).read().decode())
pkg = data["packages"].get("net.sourceforge.opencamera")
if not pkg:
    raise SystemExit("Open Camera not found in F-Droid index.")
versions = pkg.get("versions", {})
latest = sorted(versions.items(), key=lambda x: int(x[0]))[-1]
apk_name = latest[1]["file"]["name"]
apk_url = f"https://f-droid.org/repo/{apk_name}"
print(apk_url)
urllib.request.urlretrieve(apk_url, apk_out)
PY
fi

echo "Installing camera APK: $APK_FILE"
adb -s "$ADB_TARGET" install -r "$APK_FILE"

echo "Launching Open Camera..."
adb -s "$ADB_TARGET" shell monkey -p "$APP_ID" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1 || true
echo "Done."
