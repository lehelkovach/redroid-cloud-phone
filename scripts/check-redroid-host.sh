#!/bin/bash
# check-redroid-host.sh
# Read-only: can this machine run a Redroid phone (and Cuttlefish)?
#
# Checks CPU arch, kernel binder support, ashmem/memfd, Docker, RAM and
# /dev/kvm, and says what is missing. It never loads modules or changes
# anything; it prints the commands to run instead.
#
#   ./scripts/check-redroid-host.sh          # human summary, exit 0 if Redroid can run
#   ./scripts/check-redroid-host.sh --json   # one JSON object on stdout

set -uo pipefail

_CH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/log.sh
source "$_CH_DIR/lib/log.sh"
LOG_TYPE=RDR

JSON="false"
# Overridable so tests can point the checks at fixtures.
PROC_ROOT="${CHECK_PROC_ROOT:-/proc}"
DEV_ROOT="${CHECK_DEV_ROOT:-/dev}"

usage() {
    cat <<'EOF'
Usage: ./scripts/check-redroid-host.sh [--json] [--help]

Read-only host check for Redroid (binder, ashmem/memfd, Docker, RAM) and
Cuttlefish (/dev/kvm). Exit 0 when Redroid can run, 1 when something it
needs is missing.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --json) JSON="true"; shift ;;
        --help|-h) usage; exit 0 ;;
        *) log_error "Unknown option: $1"; usage >&2; exit 64 ;;
    esac
done

arch="$(uname -m)"
kernel="$(uname -r)"

# binder: binderfs registered, a /dev/binder node, or a loadable module.
binder="missing"
if grep -qw binder "$PROC_ROOT/filesystems" 2>/dev/null; then
    binder="binderfs"
elif [[ -e "$DEV_ROOT/binder" ]]; then
    binder="device"
elif command -v modinfo >/dev/null 2>&1 && modinfo binder_linux >/dev/null 2>&1; then
    binder="module_available"
fi

# ashmem was removed from mainline in 5.18; Redroid can use memfd instead
# (androidboot.use_memfd=true, which docker/redroid-gapps-compose.yml sets).
ashmem="missing"
if [[ -e "$DEV_ROOT/ashmem" ]]; then
    ashmem="device"
elif command -v modinfo >/dev/null 2>&1 && modinfo ashmem_linux >/dev/null 2>&1; then
    ashmem="module_available"
fi

docker_state="missing"
if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then docker_state="running"; else docker_state="installed_not_reachable"; fi
fi

mem_kb="$(awk '/^MemTotal:/ {print $2}' "$PROC_ROOT/meminfo" 2>/dev/null || echo 0)"
mem_gb=$(( ${mem_kb:-0} / 1024 / 1024 ))
cpus="$(nproc 2>/dev/null || echo 0)"

kvm="missing"
[[ -e "$DEV_ROOT/kvm" ]] && kvm="present"

redroid_ok="true"
problems=()
case "$binder" in
    binderfs|device) ;;
    module_available) problems+=("binder module present but not loaded: sudo modprobe binder_linux devices=binder,hwbinder,vndbinder") ;;
    *) redroid_ok="false"; problems+=("no binder in this kernel: on Ubuntu, sudo apt install linux-modules-extra-\$(uname -r) then modprobe binder_linux; a kernel without CONFIG_ANDROID_BINDER_IPC cannot run Redroid") ;;
esac
[[ "$binder" == "module_available" ]] && redroid_ok="false"
if [[ "$docker_state" != "running" ]]; then
    redroid_ok="false"; problems+=("docker is $docker_state")
fi
if [[ "$mem_gb" -lt 3 ]]; then
    problems+=("only ${mem_gb} GB RAM; one Redroid phone with Play wants about 4 GB")
fi
cuttlefish_note="ok"
if [[ "$kvm" != "present" ]]; then
    cuttlefish_note="no /dev/kvm: Cuttlefish cannot run here"
fi

if [[ "$JSON" == "true" ]]; then
    printf '{"arch":"%s","kernel":"%s","binder":"%s","ashmem":"%s","memfd_fallback":true,"docker":"%s","mem_gb":%s,"cpus":%s,"kvm":"%s","redroid_ok":%s,"cuttlefish_ok":%s}\n' \
        "$arch" "$kernel" "$binder" "$ashmem" "$docker_state" "$mem_gb" "$cpus" "$kvm" "$redroid_ok" \
        "$([[ "$kvm" == present ]] && echo true || echo false)"
else
    log_info "arch=$arch kernel=$kernel cpus=$cpus mem=${mem_gb}GB"
    log_info "binder=$binder ashmem=$ashmem (memfd fallback available) docker=$docker_state kvm=$kvm"
    for p in "${problems[@]}"; do log_warn "$p"; done
    log_info "cuttlefish: $cuttlefish_note"
    if [[ "$redroid_ok" == "true" ]]; then log_info "Redroid can run on this host"; else log_error "Redroid cannot run on this host yet"; fi
fi

[[ "$redroid_ok" == "true" ]]
