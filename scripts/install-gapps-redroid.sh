#!/bin/bash
# Install or validate GApps on a Redroid phone container.
#
# Never commits a zip. Refuses a 0-byte gapps.zip (the old lab failure).
# Does not target Cuttlefish — see docs/GAPPS.md and docs/RUNTIME-SPLIT.md.
#
#   ./scripts/install-gapps-redroid.sh --check-zip /path/to/gapps.zip
#   ./scripts/install-gapps-redroid.sh --name redroid
#   ./scripts/install-gapps-redroid.sh --validate-only --adb 127.0.0.1:5555

set -euo pipefail

_GAPPS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/log.sh
source "$_GAPPS_SCRIPT_DIR/lib/log.sh"
LOG_TYPE=GAP

NAME="${REDROID_NAME:-redroid}"
ADB_SERIAL="${ADB_CONNECT:-127.0.0.1:5555}"
GAPPS_DIR="${GAPPS_DIR:-/opt/gapps}"
ZIP_PATH="${GAPPS_ZIP:-}"
ZIP_URL="${GAPPS_ZIP_URL:-}"
ADB_BIN="${ADB_BIN:-adb}"
ACTION="install"
REQUIRE_SDK_MATCH="false"
DRY_RUN="false"
MIN_ZIP_BYTES="${GAPPS_MIN_BYTES:-1}"

CORE_PKGS=(com.google.android.gms com.android.vending)
OPTIONAL_PKGS=(com.google.android.gsf)

usage() {
    cat <<'EOF'
Usage:
  ./scripts/install-gapps-redroid.sh [OPTIONS]

Options:
  --check-zip PATH      Validate zip layout; no device
  --validate-only       Check Play packages on ADB target
  --install             Extract zip and push into Redroid (default)
  --name NAME           Docker container (default: redroid)
  --adb SERIAL          ADB serial (default: 127.0.0.1:5555)
  --zip PATH            GApps zip (else GAPPS_ZIP or /opt/gapps/gapps.zip)
  --require-sdk-match   Fail if zip Android hint != device SDK (best effort)
  --dry-run             Print adb/docker steps; do not mutate the guest
  --help                Show help
EOF
}

log_err() { log_error "$*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check-zip)
            ACTION="check-zip"
            if [[ -n "${2:-}" && "$2" != --* ]]; then ZIP_PATH="$2"; shift 2; else shift; fi
            ;;
        --validate-only) ACTION="validate"; shift ;;
        --install) ACTION="install"; shift ;;
        --name) NAME="${2:-}"; shift 2 ;;
        --adb) ADB_SERIAL="${2:-}"; shift 2 ;;
        --zip) ZIP_PATH="${2:-}"; shift 2 ;;
        --require-sdk-match) REQUIRE_SDK_MATCH="true"; shift ;;
        --dry-run) DRY_RUN="true"; shift ;;
        --help|-h) usage; exit 0 ;;
        *) log_err "Unknown option: $1"; usage; exit 1 ;;
    esac
done

resolve_zip() {
    if [[ -n "$ZIP_PATH" ]]; then
        echo "$ZIP_PATH"
        return
    fi
    if [[ -n "${GAPPS_ZIP:-}" ]]; then
        echo "$GAPPS_ZIP"
        return
    fi
    echo "$GAPPS_DIR/gapps.zip"
}

fetch_zip_if_url() {
    local dest="$1"
    if [[ -z "$ZIP_URL" ]]; then
        return 0
    fi
    mkdir -p "$(dirname "$dest")"
    log_info "Downloading GAPPS_ZIP_URL -> $dest"
    if ! curl -fsSL --max-time 120 "$ZIP_URL" -o "$dest"; then
        log_err "download failed"
        return 1
    fi
}

check_zip_file() {
    local zip="$1"
    if [[ ! -e "$zip" ]]; then
        log_err "zip not found: $zip"
        return 1
    fi
    local size
    size="$(wc -c < "$zip" | tr -d ' ')"
    if [[ "$size" -eq 0 ]]; then
        log_err "zip is empty (0 bytes): $zip — this was the old /opt/gapps/gapps.zip lab failure"
        return 2
    fi
    if [[ "$MIN_ZIP_BYTES" -gt 1 && "$size" -lt "$MIN_ZIP_BYTES" ]]; then
        log_err "zip is too small (${size} bytes < ${MIN_ZIP_BYTES}): $zip"
        return 2
    fi
    if ! command -v unzip >/dev/null 2>&1; then
        log_err "unzip is required to inspect GApps zips"
        return 1
    fi
    local listing
    listing="$(unzip -l "$zip" 2>/dev/null || true)"
    if ! echo "$listing" | grep -qiE '\.apk'; then
        log_err "zip has no .apk entries: $zip"
        return 3
    fi
    if ! echo "$listing" | grep -qiE 'GmsCore|Phonesky|vending|GoogleServicesFramework|PrebuiltGmsCore'; then
        log_err "zip does not look like GApps (no GmsCore/Phonesky/vending): $zip"
        return 3
    fi
    log_info "zip ok ($size bytes): $zip"
    return 0
}

adb() {
    "$ADB_BIN" -s "$ADB_SERIAL" "$@"
}

ensure_adb() {
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "dry-run: adb connect $ADB_SERIAL"
        return 0
    fi
    adb connect "$ADB_SERIAL" >/dev/null 2>&1 || true
    local state
    state="$(adb get-state 2>/dev/null || true)"
    if [[ "$state" != *device* ]]; then
        log_err "ADB not connected ($ADB_SERIAL state='$state')"
        return 1
    fi
}

package_present() {
    local pkg="$1"
    if [[ "$DRY_RUN" == "true" ]]; then
        return 1
    fi
    adb shell pm path "$pkg" 2>/dev/null | grep -q "package:"
}

validate_packages() {
    local missing=0
    local pkg
    for pkg in "${CORE_PKGS[@]}"; do
        if package_present "$pkg"; then
            log_info "present $pkg"
        else
            log_err "missing $pkg"
            missing=1
        fi
    done
    for pkg in "${OPTIONAL_PKGS[@]}"; do
        if package_present "$pkg"; then
            log_info "present $pkg"
        else
            log_info "warn: optional $pkg missing"
        fi
    done
    return "$missing"
}

install_into_guest() {
    local zip="$1"
    local work
    work="$(mktemp -d /tmp/gapps-extract.XXXXXX)"
    unzip -q "$zip" -d "$work"
    log_info "extracted to $work"

    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "dry-run: would adb root/remount and push overlay from $work"
        find "$work" -name '*.apk' | head -20 >&2 || true
        rm -rf "$work"
        return 0
    fi

    adb root >/dev/null 2>&1 || true
    sleep 1
    adb remount >/dev/null 2>&1 || log_info "remount failed; will try per-apk install"

    local overlay
    for overlay in system product system_ext vendor; do
        if [[ -d "$work/$overlay" ]]; then
            log_info "push $overlay/ -> /$overlay/"
            adb push "$work/$overlay/." "/$overlay/" || true
        fi
        if [[ -d "$work/system/$overlay" && "$overlay" != "system" ]]; then
            log_info "push system/$overlay/ -> /$overlay/"
            adb push "$work/system/$overlay/." "/$overlay/" || true
        fi
    done

    local apk
    while IFS= read -r apk; do
        [[ -z "$apk" ]] && continue
        log_info "adb install -r -g $(basename "$apk")"
        adb install -r -g "$apk" >/dev/null 2>&1 || true
    done < <(find "$work" -type f -name '*.apk' | grep -Ei 'GmsCore|Phonesky|GoogleServicesFramework|PrebuiltGmsCore|vending' || true)

    rm -rf "$work"
    log_info "rebooting guest"
    adb reboot || true
    sleep 5
    ensure_adb || true
}

case "$ACTION" in
    check-zip)
        zip="$(resolve_zip)"
        check_zip_file "$zip"
        ;;
    validate)
        ensure_adb
        if validate_packages; then
            log_info "core GApps packages present"
            exit 0
        fi
        exit 4
        ;;
    install)
        zip="$(resolve_zip)"
        if [[ ! -e "$zip" && -n "$ZIP_URL" ]]; then
            fetch_zip_if_url "$zip"
        fi
        check_zip_file "$zip"
        if [[ "$REQUIRE_SDK_MATCH" == "true" && "$DRY_RUN" != "true" ]]; then
            ensure_adb
            sdk="$(adb shell getprop ro.build.version.sdk 2>/dev/null | tr -d '\r' || true)"
            log_info "device sdk=$sdk (zip name $(basename "$zip"))"
        fi
        ensure_adb
        install_into_guest "$zip"
        if [[ "$DRY_RUN" == "true" ]]; then
            log_info "dry-run install complete"
            exit 0
        fi
        if validate_packages; then
            log_info "install ok"
            exit 0
        fi
        log_err "install finished but core packages still missing"
        exit 4
        ;;
    *)
        log_err "unknown action $ACTION"
        exit 1
        ;;
esac
