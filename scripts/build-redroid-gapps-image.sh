#!/bin/bash
# build-redroid-gapps-image.sh
# Bake a Redroid image that already contains GApps, for the ARM64 phone fleet.
#
# Why bake instead of installing at runtime: `install-gapps-redroid.sh` pushes
# an extracted zip into a running container, but Redroid's `/system` is a
# read-only image layer, so the push does not survive `docker rm` or a fleet
# redeploy. Worse, `adb install` cannot make GmsCore a *privileged* app — it
# has to be in priv-app with a privapp-permissions whitelist before first boot,
# or Play sign-in fails in a way that looks like a network fault.
#
# Arch is the trap this script exists to close. The phone fleet runs OCI
# VM.Standard.A1.Flex (Ampere, arm64). Every prebuilt community GApps image on
# Docker Hub is amd64-only, so the obvious shortcut yields a phone that cannot
# run Play at all. The official `redroid/redroid` base *is* multi-arch, so we
# derive from it and supply an arm64 GApps zip ourselves.
#
#   ./scripts/build-redroid-gapps-image.sh --zip /opt/gapps/MindTheGapps-arm64.zip --dry-run
#   ./scripts/build-redroid-gapps-image.sh --zip … --ship-to ubuntu@10.0.1.127
#
# Never commits or bakes a zip into this repo: the zip is read from a path you
# supply and the build context is a temp dir.

set -euo pipefail

_GB_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/log.sh
source "$_GB_SCRIPT_DIR/lib/log.sh"
LOG_TYPE=BAK

PROJECT_ROOT="$(cd "$_GB_SCRIPT_DIR/.." && pwd)"

# Keep the default base identical to docker/redroid-compose.yml. If these two
# drift, a baked phone and a vanilla phone stop being the same Android.
BASE_IMAGE="${REDROID_BASE_IMAGE:-redroid/redroid:11.0.0-latest}"
PLATFORM="${REDROID_GAPPS_PLATFORM:-linux/arm64}"
TAG="${REDROID_GAPPS_TAG:-redroid-gapps:11.0.0-arm64}"
ZIP_PATH="${GAPPS_ZIP:-}"
ZIP_URL="${GAPPS_ZIP_URL:-}"
SAVE_PATH=""
SHIP_TO=""
SSH_KEY="${SSH_KEY_PRIVATE:-}"
EMIT_DOCKERFILE=""
DO_PUSH="false"
DRY_RUN="false"
REQUIRE_ARCH_MATCH="false"
# Global so the EXIT trap can still see it once main() has returned.
CTX=""
cleanup() { [[ -n "$CTX" && -d "$CTX" ]] && rm -rf "$CTX"; return 0; }
trap cleanup EXIT

# MindTheGapps lays its tree out under `system/`; NikGApps and OpenGApps put
# some partitions at the top level. Both shapes are handled.
OVERLAYS=(system product system_ext vendor)

usage() {
    cat <<'EOF'
Usage:
  ./scripts/build-redroid-gapps-image.sh [OPTIONS]

Bakes GApps into a Redroid image for the ARM64 (Ampere) phone fleet.

Options:
  --zip PATH             MindTheGapps / NikGApps / OpenGApps arm64 zip
                         (else $GAPPS_ZIP, else /opt/gapps/gapps.zip)
  --zip-url URL          Download the zip first (else $GAPPS_ZIP_URL)
  --base IMAGE           Base image (default: redroid/redroid:11.0.0-latest)
  --tag TAG              Output tag (default: redroid-gapps:11.0.0-arm64)
  --platform PLATFORM    Build platform (default: linux/arm64)
  --require-arch-match   Fail, rather than warn, when the zip arch is unreadable
  --save PATH            docker save the image to a tarball
  --ship-to USER@HOST    Save, scp, and docker load on a phone VM (no registry)
  --ssh-key PATH         Private key for --ship-to
  --push                 docker push the tag to its registry
  --emit-dockerfile PATH Write the generated Dockerfile here (for review/tests)
  --dry-run              Print the plan and generated Dockerfile; needs no Docker
  --help                 Show help

Notes:
  The fleet is arm64, so an amd64 GApps zip is refused by default. Community
  prebuilt "mindthegapps" images on Docker Hub are amd64-only and cannot be
  used here; the official redroid/redroid base is multi-arch, which is why this
  derives from it instead.

  The result is hosted the same way a vanilla phone is: pass the tag as
  REDROID_IMAGE to docker/redroid-compose.yml, or as --redroid-image to
  scripts/deploy-redroid-oci.sh. Confirm with `./cloud-phone gapps-check`.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --zip) ZIP_PATH="${2:-}"; shift 2 ;;
        --zip-url) ZIP_URL="${2:-}"; shift 2 ;;
        --base) BASE_IMAGE="${2:-$BASE_IMAGE}"; shift 2 ;;
        --tag) TAG="${2:-$TAG}"; shift 2 ;;
        --platform) PLATFORM="${2:-$PLATFORM}"; shift 2 ;;
        --require-arch-match) REQUIRE_ARCH_MATCH="true"; shift ;;
        --save) SAVE_PATH="${2:-}"; shift 2 ;;
        --ship-to) SHIP_TO="${2:-}"; shift 2 ;;
        --ssh-key) SSH_KEY="${2:-}"; shift 2 ;;
        --push) DO_PUSH="true"; shift ;;
        --emit-dockerfile) EMIT_DOCKERFILE="${2:-}"; shift 2 ;;
        --dry-run) DRY_RUN="true"; shift ;;
        --help|-h) usage; exit 0 ;;
        *) log_error "Unknown option: $1"; usage; exit 1 ;;
    esac
done

resolve_zip() {
    if [[ -n "$ZIP_PATH" ]]; then echo "$ZIP_PATH"; return; fi
    echo "${GAPPS_DIR:-/opt/gapps}/gapps.zip"
}

# Zip validation is deliberately delegated rather than reimplemented: the
# installer already refuses the 0-byte lab file and non-GApps archives, and two
# copies of that logic would drift.
validate_zip() {
    local zip="$1"
    bash "$_GB_SCRIPT_DIR/install-gapps-redroid.sh" --check-zip "$zip"
}

fetch_zip_if_url() {
    local dest="$1"
    [[ -z "$ZIP_URL" ]] && return 0
    [[ -e "$dest" ]] && return 0
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "dry-run: would download $ZIP_URL -> $dest"
        return 0
    fi
    mkdir -p "$(dirname "$dest")"
    log_info "downloading GApps zip -> $dest"
    curl -fsSL --max-time 300 "$ZIP_URL" -o "$dest" || { log_error "download failed"; return 1; }
}

# arm64 / x86_64 read off the file name first (every upstream GApps release
# names it) and the entry paths second.
detect_zip_arch() {
    local zip="$1" base listing hay
    base="$(basename "$zip")"
    listing="$(unzip -l "$zip" 2>/dev/null || true)"
    hay="$(printf '%s\n%s' "$base" "$listing" | tr '[:upper:]' '[:lower:]')"

    local saw_arm="false" saw_x86="false"
    grep -qE 'arm64|aarch64' <<<"$hay" && saw_arm="true"
    grep -qE 'x86_64|x86-64|[^a-z]x86[^_a-z]' <<<"$hay" && saw_x86="true"

    if [[ "$saw_arm" == "true" && "$saw_x86" == "false" ]]; then echo "arm64"; return; fi
    if [[ "$saw_x86" == "true" && "$saw_arm" == "false" ]]; then echo "amd64"; return; fi
    echo "unknown"
}

platform_arch() {
    case "$1" in
        */arm64|*/arm64/*|linux/aarch64) echo "arm64" ;;
        */amd64|*/x86_64) echo "amd64" ;;
        *) echo "unknown" ;;
    esac
}

check_arch() {
    local zip="$1" want got
    want="$(platform_arch "$PLATFORM")"
    got="$(detect_zip_arch "$zip")"
    log_info "zip arch=$got target platform=$PLATFORM ($want)"

    if [[ "$got" == "unknown" ]]; then
        if [[ "$REQUIRE_ARCH_MATCH" == "true" ]]; then
            log_error "cannot read the zip's arch and --require-arch-match was given"
            return 1
        fi
        log_info "warn: cannot tell the zip's arch from its name or contents; continuing"
        return 0
    fi
    if [[ "$want" != "unknown" && "$got" != "$want" ]]; then
        log_error "arch mismatch: zip is $got but the target platform is $PLATFORM."
        log_error "The phone fleet is Ampere arm64 — an $got zip yields a phone that cannot run Play."
        return 1
    fi
}

# Which partitions the zip actually carries, and where each must land.
#
# The subtlety that bit the first version of this: MindTheGapps ships only
# `system/product/...`, so a naive "does the zip contain system/ ?" test says
# yes and copies the whole tree to /system as well. GmsCore then exists at
# /system/product/priv-app — the wrong partition — in addition to the right
# one, and the guest gets two copies of Play Services. `system` therefore
# counts as a payload only when it holds something that is not itself another
# partition.
zip_overlays() {
    local zip="$1" listing overlay found=()
    listing="$(unzip -l "$zip" 2>/dev/null || true)"

    for overlay in product system_ext vendor; do
        if grep -qE "[[:space:]]system/$overlay/" <<<"$listing"; then
            found+=("system/$overlay:/$overlay")
        elif grep -qE "[[:space:]]$overlay/" <<<"$listing"; then
            found+=("$overlay:/$overlay")
        fi
    done

    # Nested `system/system/` is unambiguous.
    if grep -qE "[[:space:]]system/system/" <<<"$listing"; then
        found+=("system/system:/system")
    elif grep -qE "[[:space:]](system/)?(priv-app|app|framework|etc|lib|lib64)/" <<<"$listing"; then
        # Real /system payload: entries under system/ that are not a partition.
        if grep -qE "[[:space:]]system/(priv-app|app|framework|etc|lib|lib64)/" <<<"$listing"; then
            found+=("system:/system")
        elif grep -qE "^[[:space:]]*[0-9]+.*[[:space:]](priv-app|app|framework)/" <<<"$listing"; then
            found+=(".:/system")
        fi
    fi

    [[ ${#found[@]} -eq 0 ]] && return 0
    printf '%s\n' "${found[@]}"
}

generate_dockerfile() {
    local zip="$1" mapping src dst
    cat <<EOF
# Generated by scripts/build-redroid-gapps-image.sh — do not commit.
# Base is pinned to the same image docker/redroid-compose.yml runs, so a baked
# phone and a vanilla phone are the same Android build.
FROM $BASE_IMAGE

EOF
    while IFS= read -r mapping; do
        [[ -z "$mapping" ]] && continue
        src="${mapping%%:*}"
        dst="${mapping##*:}"
        printf 'COPY --chown=0:0 gapps/%s/ %s/\n' "$src" "$dst"
    done < <(zip_overlays "$zip")

    cat <<'EOF'

# GmsCore is only privileged if its whitelist is on the image before first
# boot. Without this the app installs and then silently loses its permissions,
# which surfaces as Play sign-in failing like a network error.
RUN set -eu; \
    for d in /system /product /system_ext /vendor; do \
        [ -d "$d/etc/permissions" ] || continue; \
        find "$d/etc/permissions" -name 'privapp-permissions*.xml' -exec chmod 0644 {} + ; \
    done; \
    find /system /product /system_ext /vendor -name '*.apk' -exec chmod 0644 {} + 2>/dev/null || true

EOF
    cat <<'EOF'
# COPY does not touch ENTRYPOINT, but adding the setup-wizard property does, so
# the redroid boot argument has to be restated in full here or the guest never
# finishes booting.
ENTRYPOINT ["/init", "androidboot.hardware=redroid", "ro.setupwizard.mode=DISABLED"]
EOF
}

print_plan() {
    local zip="$1"
    cat <<EOF

  base_image:    $BASE_IMAGE
  platform:      $PLATFORM
  tag:           $TAG
  gapps_zip:     $zip
  overlays:      $(zip_overlays "$zip" | tr '\n' ' ')
  save:          ${SAVE_PATH:-<none>}
  ship_to:       ${SHIP_TO:-<none>}
  push:          $DO_PUSH

Host it the same way a vanilla phone is hosted:

  REDROID_IMAGE=$TAG docker compose -f docker/redroid-compose.yml -p phone-1 up -d
  ./scripts/deploy-redroid-oci.sh --redroid-image $TAG
  ./cloud-phone gapps-check      # the baked phone still has to prove Play is there

EOF
}

ship_plan() {
    local tarball="$1" key_arg=""
    [[ -n "$SSH_KEY" ]] && key_arg=" -i $SSH_KEY"
    cat <<EOF
  docker save $TAG -o $tarball
  scp$key_arg $tarball $SHIP_TO:/tmp/$(basename "$tarball")
  ssh$key_arg $SHIP_TO docker load -i /tmp/$(basename "$tarball")
  ssh$key_arg $SHIP_TO 'REDROID_IMAGE=$TAG docker compose -f docker/redroid-compose.yml up -d'

EOF
}

require_docker() {
    command -v docker >/dev/null 2>&1 || { log_error "docker is required (use --dry-run to plan without it)"; return 1; }
    docker buildx version >/dev/null 2>&1 || { log_error "docker buildx is required to build $PLATFORM"; return 1; }
}

main() {
    local zip
    zip="$(resolve_zip)"
    fetch_zip_if_url "$zip"
    validate_zip "$zip"
    check_arch "$zip"

    if [[ -n "$EMIT_DOCKERFILE" ]]; then
        generate_dockerfile "$zip" > "$EMIT_DOCKERFILE"
        log_info "wrote Dockerfile -> $EMIT_DOCKERFILE"
    fi

    local tarball="${SAVE_PATH:-}"
    if [[ -z "$tarball" && -n "$SHIP_TO" ]]; then
        tarball="${TMPDIR:-/tmp}/$(echo "$TAG" | tr '/:' '__').tar"
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "dry-run: no image is built and nothing is shipped"
        print_plan "$zip"
        echo "--- generated Dockerfile ---"
        generate_dockerfile "$zip"
        echo "--- end Dockerfile ---"
        if [[ -n "$SHIP_TO" ]]; then
            echo "--- hosting (no registry needed) ---"
            ship_plan "$tarball"
        fi
        if [[ "$DO_PUSH" == "true" ]]; then
            echo "--- registry ---"
            echo "  docker push $TAG"
            echo
        fi
        return 0
    fi

    require_docker

    # The context is always a temp dir: the zip is proprietary, and copying it
    # into the working tree is how it would end up committed.
    CTX="$(mktemp -d "${TMPDIR:-/tmp}/redroid-gapps-ctx.XXXXXX")"

    log_info "extracting GApps into the build context"
    mkdir -p "$CTX/gapps"
    unzip -q "$zip" -d "$CTX/gapps"
    generate_dockerfile "$zip" > "$CTX/Dockerfile"

    log_info "building $TAG for $PLATFORM"
    docker buildx build \
        --platform "$PLATFORM" \
        --tag "$TAG" \
        --file "$CTX/Dockerfile" \
        --load \
        "$CTX"

    if [[ -n "$tarball" ]]; then
        log_info "saving -> $tarball"
        docker save "$TAG" -o "$tarball"
    fi

    if [[ -n "$SHIP_TO" ]]; then
        local key_arg=()
        [[ -n "$SSH_KEY" ]] && key_arg=(-i "$SSH_KEY")
        log_info "shipping to $SHIP_TO"
        scp "${key_arg[@]}" -o StrictHostKeyChecking=no "$tarball" "$SHIP_TO:/tmp/$(basename "$tarball")"
        ssh "${key_arg[@]}" -o StrictHostKeyChecking=no "$SHIP_TO" "docker load -i /tmp/$(basename "$tarball")"
        log_info "loaded on $SHIP_TO — start it with REDROID_IMAGE=$TAG"
    fi

    if [[ "$DO_PUSH" == "true" ]]; then
        log_info "pushing $TAG"
        docker push "$TAG"
    fi

    log_info "done. Verify Play with: ./cloud-phone gapps-check"
}

main "$@"
