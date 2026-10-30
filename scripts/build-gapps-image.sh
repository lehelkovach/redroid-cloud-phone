#!/bin/bash
# build-gapps-image.sh
# Bake Google Play (GApps) into a Redroid image at build time.
#
# Why bake rather than push into a running phone (install-gapps-redroid.sh):
# Play services must be a privileged system app with its permission
# allow-list in place before first boot, and a push into a running
# container's /system does not survive `docker rm` or a fleet redeploy. A
# derived image puts the GApps tree into /system as one extra layer, the way
# the community redroid-script does it, and every container started from it
# has Play from its first boot.
#
# The image layer is a plain COPY, with no RUN step, so an x86_64 machine can
# build the arm64 image for the OCI Ampere fleet without qemu/binfmt.
#
#   ./scripts/build-gapps-image.sh --zip ~/MindTheGapps-13.0.0-arm64-20240226.zip --dry-run
#   ./scripts/build-gapps-image.sh --zip-url https://... --sha256 <hex> --android 13.0.0 --arch arm64
#   ./scripts/build-gapps-image.sh --zip ... --save /tmp/redroid-gapps.tar    # carry to a host
#
# It never downloads GApps on its own, never pushes to a registry, and builds
# in a temporary directory so the proprietary zip cannot end up in git. See
# docs/GAPPS-AND-APP-INSTALL.md for the licensing position.

set -euo pipefail

_BG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/log.sh
source "$_BG_DIR/lib/log.sh"
LOG_TYPE=GAP

ANDROID="${REDROID_GAPPS_ANDROID:-13.0.0}"
ARCH="${REDROID_GAPPS_ARCH:-}"
BASE_IMAGE="${REDROID_GAPPS_BASE:-}"
TAG="${REDROID_GAPPS_TAG:-}"
ZIP_PATH="${GAPPS_ZIP:-}"
ZIP_URL="${GAPPS_ZIP_URL:-}"
ZIP_SHA256="${GAPPS_ZIP_SHA256:-}"
SAVE_PATH=""
EMIT_DOCKERFILE=""
DRY_RUN="false"
JSON="false"
SKIP_SETUP_WIZARD="true"
ALLOW_VERSION_MISMATCH="false"
REQUIRE_ARCH_MATCH="false"
# Redroid's own entrypoint (docker image inspect redroid/redroid:13.0.0-latest).
# Used when the base cannot be inspected (dry runs, no Docker).
DEFAULT_ENTRYPOINT='"/init", "qemu=1", "androidboot.hardware=redroid"'
CTX=""
DL_TMP=""

cleanup() {
    [[ -n "$CTX" && -d "$CTX" ]] && rm -rf "$CTX"
    [[ -n "$DL_TMP" && -f "$DL_TMP" ]] && rm -f "$DL_TMP"
    return 0
}
trap cleanup EXIT

usage() {
    cat <<'EOF'
Usage:
  ./scripts/build-gapps-image.sh [OPTIONS]

Bakes a GApps zip into a Redroid image (one COPY layer onto /system).

Source (one of):
  --zip PATH             GApps zip on disk (else $GAPPS_ZIP)
  --zip-url URL          HTTPS URL to fetch (else $GAPPS_ZIP_URL); needs --sha256
  --sha256 HEX           Expected SHA-256 of the zip (else $GAPPS_ZIP_SHA256).
                         Required with --zip-url, checked when given with --zip.

Image:
  --android VERSION      Redroid Android version (default 13.0.0; e.g. 12.0.0,
                         13.0.0_64only, 14.0.0). Must match the zip's version.
  --arch arm64|amd64     Target CPU (default: this machine's). OCI A1 = arm64.
  --base IMAGE           Base image (default redroid/redroid:<android>-latest)
  --tag TAG              Output tag (default cloud-phone/redroid-gapps:<android>-<arch>)
  --keep-setup-wizard    Do not add ro.setupwizard.mode=DISABLED to the entrypoint
  --allow-version-mismatch  Build even if the zip names another Android version
  --require-arch-match   Refuse when the zip's arch cannot be read from its name/contents

Output:
  --save PATH            docker save the image to a tarball (to docker load on a host)
  --emit-dockerfile PATH Write the generated Dockerfile here
  --dry-run              Validate the zip and print the plan; no Docker needed
  --json                 Print a JSON summary on stdout (logs stay on stderr)
  --help

Accepted zips: flashable packages with a system/ tree, such as MindTheGapps
(the recommended one) or LiteGapps "files" already unpacked to system/.
OpenGApps zips (Core/*.tar.lz) are refused: unpack them first.

This script never pushes the image anywhere. It contains Google's proprietary
apps; keep it in a private registry or move it with --save.
EOF
}

die() { log_error "$*"; exit "${EXIT_CODE:-1}"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --zip) ZIP_PATH="${2:-}"; shift 2 ;;
        --zip-url) ZIP_URL="${2:-}"; shift 2 ;;
        --sha256) ZIP_SHA256="${2:-}"; shift 2 ;;
        --android) ANDROID="${2:-}"; shift 2 ;;
        --arch) ARCH="${2:-}"; shift 2 ;;
        --base) BASE_IMAGE="${2:-}"; shift 2 ;;
        --tag) TAG="${2:-}"; shift 2 ;;
        --keep-setup-wizard) SKIP_SETUP_WIZARD="false"; shift ;;
        --allow-version-mismatch) ALLOW_VERSION_MISMATCH="true"; shift ;;
        --require-arch-match) REQUIRE_ARCH_MATCH="true"; shift ;;
        --save) SAVE_PATH="${2:-}"; shift 2 ;;
        --emit-dockerfile) EMIT_DOCKERFILE="${2:-}"; shift 2 ;;
        --dry-run) DRY_RUN="true"; shift ;;
        --json) JSON="true"; shift ;;
        --help|-h) usage; exit 0 ;;
        *) log_error "Unknown option: $1"; usage >&2; exit 64 ;;
    esac
done

# --- argument validation (exit 64 = usage error) -----------------------------

host_arch() {
    case "$(uname -m)" in
        x86_64|amd64) echo amd64 ;;
        aarch64|arm64) echo arm64 ;;
        *) echo unknown ;;
    esac
}

[[ -z "$ARCH" ]] && ARCH="$(host_arch)"
case "$ARCH" in
    arm64|aarch64) ARCH=arm64 ;;
    amd64|x86_64) ARCH=amd64 ;;
    *) EXIT_CODE=64 die "--arch must be arm64 or amd64 (got '$ARCH')" ;;
esac
PLATFORM="linux/$ARCH"

if [[ ! "$ANDROID" =~ ^[0-9]{1,2}\.[0-9]\.[0-9](_64only)?$ ]]; then
    EXIT_CODE=64 die "--android must look like 13.0.0 or 13.0.0_64only (got '$ANDROID')"
fi
ANDROID_MAJOR="${ANDROID%%.*}"
[[ -z "$BASE_IMAGE" ]] && BASE_IMAGE="redroid/redroid:${ANDROID}-latest"
[[ -z "$TAG" ]] && TAG="cloud-phone/redroid-gapps:${ANDROID}-${ARCH}"
if [[ ! "$TAG" =~ ^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9_.-]+)?$ ]]; then
    EXIT_CODE=64 die "invalid --tag '$TAG'"
fi

if [[ -n "$ZIP_PATH" && -n "$ZIP_URL" ]]; then
    EXIT_CODE=64 die "give --zip or --zip-url, not both"
fi
if [[ -z "$ZIP_PATH" && -z "$ZIP_URL" ]]; then
    EXIT_CODE=64 die "no GApps zip: pass --zip PATH or --zip-url URL --sha256 HEX (this repo ships none)"
fi
if [[ -n "$ZIP_SHA256" ]]; then
    ZIP_SHA256="$(echo "$ZIP_SHA256" | tr '[:upper:]' '[:lower:]')"
    [[ "$ZIP_SHA256" =~ ^[0-9a-f]{64}$ ]] || EXIT_CODE=64 die "--sha256 must be 64 hex characters"
fi
if [[ -n "$ZIP_URL" ]]; then
    [[ "$ZIP_URL" == https://* ]] || EXIT_CODE=64 die "--zip-url must be https://"
    [[ -n "$ZIP_SHA256" ]] || EXIT_CODE=64 die "--zip-url needs --sha256: a GApps zip becomes privileged system code, so it is never used unverified"
fi

# URL without query string or credentials, for logs.
safe_url() { local u="${1%%\?*}"; u="${u%%#*}"; echo "$u" | sed -E 's#(https://)[^/@]+@#\1#'; }

# --- fetch and verify --------------------------------------------------------

sha256_of() { sha256sum "$1" | awk '{print $1}'; }

fetch_zip() {
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "dry-run: would download $(safe_url "$ZIP_URL") and check sha256 $ZIP_SHA256"
        return 0
    fi
    DL_TMP="$(mktemp "${TMPDIR:-/tmp}/gapps-zip.XXXXXX")"
    log_info "downloading $(safe_url "$ZIP_URL")"
    curl -fsSL --proto '=https' --max-time 600 "$ZIP_URL" -o "$DL_TMP" \
        || die "download failed: $(safe_url "$ZIP_URL")"
    ZIP_PATH="$DL_TMP"
}

verify_sha() {
    [[ -z "$ZIP_SHA256" ]] && { log_warn "no --sha256 given; record this one: $(sha256_of "$ZIP_PATH")"; return 0; }
    local got
    got="$(sha256_of "$ZIP_PATH")"
    if [[ "$got" != "$ZIP_SHA256" ]]; then
        EXIT_CODE=3 die "sha256 mismatch for $(basename "$ZIP_PATH"): expected $ZIP_SHA256. Not building."
    fi
    log_info "sha256 ok"
}

# --- zip checks --------------------------------------------------------------

ZIP_LISTING=""

check_layout() {
    [[ -f "$ZIP_PATH" ]] || EXIT_CODE=1 die "zip not found: $ZIP_PATH"
    [[ -s "$ZIP_PATH" ]] || EXIT_CODE=2 die "zip is empty (0 bytes): $ZIP_PATH"
    command -v unzip >/dev/null 2>&1 || die "unzip is required"
    ZIP_LISTING="$(unzip -Z1 "$ZIP_PATH" 2>/dev/null)" || EXIT_CODE=2 die "not a readable zip: $ZIP_PATH"

    if grep -qE '^Core/.*\.tar\.lz$' <<<"$ZIP_LISTING"; then
        EXIT_CODE=2 die "this is an OpenGApps zip (Core/*.tar.lz); unpack it to a system/ tree first or use MindTheGapps"
    fi
    grep -qE '^system/' <<<"$ZIP_LISTING" \
        || EXIT_CODE=2 die "zip has no system/ tree to layer onto /system"
    grep -qiE '^system/.*(GmsCore|PrebuiltGmsCore)[^/]*\.apk$' <<<"$ZIP_LISTING" \
        || EXIT_CODE=2 die "zip has no Play services APK (GmsCore) under system/"
    grep -qiE '^system/.*(Phonesky|Vending)[^/]*\.apk$' <<<"$ZIP_LISTING" \
        || EXIT_CODE=2 die "zip has no Play Store APK (Phonesky) under system/"
    if ! grep -qiE '^system/.*etc/permissions/.*\.xml$' <<<"$ZIP_LISTING"; then
        log_warn "zip carries no etc/permissions/*.xml; Play services may lose its privileged permissions"
    fi
    log_info "zip layout ok: $(grep -c '' <<<"$ZIP_LISTING") entries"
}

# arm64 / amd64 from the file name first (every GApps release names it), then
# from native-library paths inside the zip.
zip_arch() {
    local hay
    hay="$(printf '%s\n%s' "$(basename "$ZIP_PATH")" "$ZIP_LISTING" | tr '[:upper:]' '[:lower:]')"
    local arm=false x86=false
    grep -qE 'arm64|aarch64|arm64-v8a' <<<"$hay" && arm=true
    grep -qE 'x86_64|x86-64' <<<"$hay" && x86=true
    if [[ "$arm" == true && "$x86" == false ]]; then echo arm64
    elif [[ "$x86" == true && "$arm" == false ]]; then echo amd64
    else echo unknown
    fi
}

# Android version from the file name (MindTheGapps-13.0.0-..., LiteGapps-arm64-14.0-...).
zip_android_major() {
    local name
    name="$(basename "$ZIP_PATH")"
    if [[ "$name" =~ [-_]([0-9]{1,2})\.[0-9](\.[0-9])?[-_] ]]; then
        echo "${BASH_REMATCH[1]}"
    else
        echo unknown
    fi
}

check_arch_and_version() {
    local za zv
    za="$(zip_arch)"
    if [[ "$za" == unknown ]]; then
        [[ "$REQUIRE_ARCH_MATCH" == true ]] && EXIT_CODE=2 die "cannot tell the zip's CPU arch and --require-arch-match was given"
        log_warn "cannot tell the zip's CPU arch from its name or contents; continuing"
    elif [[ "$za" != "$ARCH" ]]; then
        EXIT_CODE=2 die "arch mismatch: zip is $za, target is $ARCH. An OCI A1 host needs an arm64 zip."
    fi
    zv="$(zip_android_major)"
    if [[ "$zv" == unknown ]]; then
        log_warn "cannot read an Android version from the zip name; make sure it is for Android $ANDROID_MAJOR"
    elif [[ "$zv" != "$ANDROID_MAJOR" ]]; then
        if [[ "$ALLOW_VERSION_MISMATCH" == true ]]; then
            log_warn "zip is for Android $zv, image is Android $ANDROID_MAJOR (--allow-version-mismatch)"
        else
            EXIT_CODE=2 die "version mismatch: zip is for Android $zv, image is $ANDROID. GApps must match the Android version."
        fi
    fi
    ZIP_ARCH="$za"
    ZIP_ANDROID="$zv"
}

# --- Dockerfile --------------------------------------------------------------

base_entrypoint() {
    local ep=""
    if [[ "$DRY_RUN" != "true" ]] && command -v docker >/dev/null 2>&1; then
        ep="$(docker image inspect --platform "$PLATFORM" --format '{{json .Config.Entrypoint}}' "$BASE_IMAGE" 2>/dev/null \
              || docker image inspect --format '{{json .Config.Entrypoint}}' "$BASE_IMAGE" 2>/dev/null || true)"
    fi
    if [[ -n "$ep" && "$ep" != "null" && "$ep" == \[*\] ]]; then
        ep="${ep#[}"; ep="${ep%]}"
        echo "$ep" | sed 's/","/", "/g'
    else
        echo "$DEFAULT_ENTRYPOINT"
    fi
}

generate_dockerfile() {
    local zip_sha="$1" ep
    ep="$(base_entrypoint)"
    cat <<EOF
# Generated by scripts/build-gapps-image.sh. Do not commit; the build context
# holds Google's proprietary apps.
FROM $BASE_IMAGE
LABEL org.opencontainers.image.title="redroid-gapps" \\
      cloud-phone.base-image="$BASE_IMAGE" \\
      cloud-phone.gapps-zip="$(basename "$ZIP_PATH")" \\
      cloud-phone.gapps-zip-sha256="$zip_sha"
# Redroid keeps /product and /system_ext as symlinks into /system, so the
# zip's system/ tree lands on every partition from one copy.
COPY --chown=0:0 gapps/system/ /system/
EOF
    if [[ "$SKIP_SETUP_WIZARD" == true ]]; then
        cat <<EOF
# Restated from the base image, plus the GApps setup wizard switched off, as
# redroid's own GApps build docs recommend.
ENTRYPOINT [$ep, "ro.setupwizard.mode=DISABLED"]
EOF
    fi
}

emit_json() {
    local status="$1" zip_sha="$2"
    printf '{"status":"%s","tag":"%s","base_image":"%s","platform":"%s","android":"%s","zip":"%s","zip_sha256":"%s","zip_arch":"%s","save":"%s"}\n' \
        "$status" "$TAG" "$BASE_IMAGE" "$PLATFORM" "$ANDROID" "$(basename "$ZIP_PATH")" "$zip_sha" "${ZIP_ARCH:-unknown}" "$SAVE_PATH"
}

build() {
    command -v docker >/dev/null 2>&1 || die "docker is required (use --dry-run without it)"
    CTX="$(mktemp -d "${TMPDIR:-/tmp}/redroid-gapps-ctx.XXXXXX")"
    log_info "extracting system/ from the zip into a temporary build context"
    unzip -q "$ZIP_PATH" 'system/*' -d "$CTX/gapps"
    # Android reads system apps and their permission XML as root:root 0644 in
    # 0755 directories; zips built on Windows or by hand often lack that.
    find "$CTX/gapps" -type d -exec chmod 0755 {} +
    find "$CTX/gapps" -type f -exec chmod 0644 {} +
    generate_dockerfile "$1" > "$CTX/Dockerfile"

    log_info "building $TAG for $PLATFORM from $BASE_IMAGE"
    if docker buildx version >/dev/null 2>&1; then
        docker buildx build --platform "$PLATFORM" --tag "$TAG" --load "$CTX" >&2
    else
        # Ubuntu's docker.io ships without buildx, and with BuildKit asked for
        # Docker 29 refuses to build ("buildx component is missing"). The
        # layer is a plain COPY, so the legacy builder does the same job.
        log_warn "docker buildx is not installed; using the legacy builder (apt install docker-buildx to use BuildKit)"
        DOCKER_BUILDKIT=0 docker build --platform "$PLATFORM" --tag "$TAG" "$CTX" >&2
    fi
    if [[ -n "$SAVE_PATH" ]]; then
        log_info "saving image to $SAVE_PATH"
        docker save "$TAG" -o "$SAVE_PATH"
    fi
}

main() {
    [[ -n "$ZIP_URL" ]] && fetch_zip
    if [[ "$DRY_RUN" == "true" && -n "$ZIP_URL" && -z "$ZIP_PATH" ]]; then
        log_info "dry-run with --zip-url: zip contents not checked until it is downloaded"
        ZIP_PATH="$(safe_url "$ZIP_URL")"; ZIP_PATH="${ZIP_PATH##*/}"
        [[ -n "$EMIT_DOCKERFILE" ]] && generate_dockerfile "$ZIP_SHA256" > "$EMIT_DOCKERFILE"
        if [[ "$JSON" == true ]]; then emit_json "planned" "$ZIP_SHA256"; else generate_dockerfile "$ZIP_SHA256"; fi
        return 0
    fi
    check_layout
    verify_sha
    check_arch_and_version
    local zip_sha
    zip_sha="${ZIP_SHA256:-$(sha256_of "$ZIP_PATH")}"

    [[ -n "$EMIT_DOCKERFILE" ]] && { generate_dockerfile "$zip_sha" > "$EMIT_DOCKERFILE"; log_info "wrote $EMIT_DOCKERFILE"; }

    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "dry-run: nothing built"
        if [[ "$JSON" == true ]]; then emit_json "planned" "$zip_sha"; else generate_dockerfile "$zip_sha"; fi
        return 0
    fi

    build "$zip_sha"
    log_info "built $TAG. Run it with: REDROID_GAPPS_IMAGE=$TAG docker compose -f docker/redroid-gapps-compose.yml --profile gapps up -d"
    log_info "then check Play: ./cloud-phone gapps-check --adb 127.0.0.1:\${ADB_PORT:-5555}"
    [[ "$JSON" == true ]] && emit_json "built" "$zip_sha"
    return 0
}

main
