#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Fix Google Play Services / Play Store sign-in issues.

What it does:
- Ensures a valid android_id (not 0/null)
- Clears GSF/GMS/Play Store data
- Triggers GSF check-in

Usage:
  ./fix-play-services.sh
  ADB_CONNECT=127.0.0.1:5555 ./fix-play-services.sh
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

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

get_android_id() {
  adb -s "$ADB_TARGET" shell settings get secure android_id | tr -d '\r'
}

ANDROID_ID="$(get_android_id)"
if [[ -z "$ANDROID_ID" || "$ANDROID_ID" == "0" || "$ANDROID_ID" == "null" ]]; then
  NEW_ID="$(python3 - <<'PY'
import random
print(''.join(random.choice('0123456789abcdef') for _ in range(16)))
PY
)"
  echo "Setting android_id to $NEW_ID"
  adb -s "$ADB_TARGET" shell "settings put secure android_id '$NEW_ID'" || true

  if adb -s "$ADB_TARGET" shell "command -v sqlite3" >/dev/null 2>&1; then
    adb -s "$ADB_TARGET" shell "sqlite3 /data/data/com.google.android.gsf/databases/gservices.db \
      \"update main set value='$NEW_ID' where name='android_id';\" " || true
  else
    adb -s "$ADB_TARGET" shell \
      "content insert --uri content://com.google.android.gsf.gservices \
       --bind name:s:android_id --bind value:s:$NEW_ID" || true
  fi
else
  echo "android_id is $ANDROID_ID"
fi

echo "Clearing Play Services, Play Store, and GSF data..."
adb -s "$ADB_TARGET" shell "pm clear com.google.android.gms" || true
adb -s "$ADB_TARGET" shell "pm clear com.android.vending" || true
adb -s "$ADB_TARGET" shell "pm clear com.google.android.gsf" || true

echo "Requesting GSF check-in..."
adb -s "$ADB_TARGET" shell "am broadcast -a com.google.android.gsf.action.REQUEST_CHECKIN" >/dev/null 2>&1 || true

echo "Starting Play Store..."
adb -s "$ADB_TARGET" shell "am start -n com.android.vending/com.google.android.finsky.activities.MainActivity" >/dev/null 2>&1 || true

echo "If Play Store still shows 'Device not certified':"
echo "  1) Run: adb -s $ADB_TARGET shell settings get secure android_id"
echo "  2) Register at: https://www.google.com/android/uncertified/"
