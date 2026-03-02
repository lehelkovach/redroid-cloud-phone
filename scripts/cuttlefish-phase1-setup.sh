#!/bin/bash
# cuttlefish-phase1-setup.sh
# Phase 1 bring-up helper for Cuttlefish ARM64 on OCI.
#
# Goals:
# - Install base dependencies
# - Verify KVM availability
# - Install/verify Cuttlefish host tools (launch_cvd, cvd)
# - Start a single Cuttlefish instance with WebRTC enabled
#
# Usage:
#   ./scripts/cuttlefish-phase1-setup.sh [OPTIONS] [VM_HOST]
#
# Options:
#   --local                     Run on local host
#   --vm HOST                   Run remotely via SSH
#   --ssh-user USER             SSH user (default: ubuntu)
#   --ssh-key PATH              SSH key (default: ~/.ssh/android_arm_cloud_phone_oci)
#   --no-install                Skip apt package installation
#   --skip-launch               Skip launch_cvd
#   --instance-name NAME        CVD instance name (default: cvd-arm64-1)
#   --webrtc-port PORT          WebRTC port (default: 8443)
#   --base-instance-num N       Base instance number (default: 1)
#   --help                      Show help
#
# Notes:
# - This script assumes Ubuntu 22.04/24.04 on ARM64.
# - If launch_cvd is not available after install, use docs/CUTTLEFISH_PHASE1.md
#   to install Cuttlefish host tools from source/debs.

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

ENV_VM_HOST="${VM_HOST:-${DEV_INSTANCE:-}}"
VM_HOST=""
RUN_MODE="local"

SSH_USER="ubuntu"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/android_arm_cloud_phone_oci}"

DO_INSTALL=true
DO_LAUNCH=true
INSTANCE_NAME="cvd-arm64-1"
WEBRTC_PORT="8443"
BASE_INSTANCE_NUM="1"

usage() {
    sed -n '1,40p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --local)
            RUN_MODE="local"
            VM_HOST=""
            shift
            ;;
        --vm)
            VM_HOST="${2:-}"
            RUN_MODE="remote"
            shift 2
            ;;
        --ssh-user)
            SSH_USER="${2:-ubuntu}"
            shift 2
            ;;
        --ssh-key)
            SSH_KEY="${2:-$HOME/.ssh/android_arm_cloud_phone_oci}"
            shift 2
            ;;
        --no-install)
            DO_INSTALL=false
            shift
            ;;
        --skip-launch)
            DO_LAUNCH=false
            shift
            ;;
        --instance-name)
            INSTANCE_NAME="${2:-cvd-arm64-1}"
            shift 2
            ;;
        --webrtc-port)
            WEBRTC_PORT="${2:-8443}"
            shift 2
            ;;
        --base-instance-num)
            BASE_INSTANCE_NUM="${2:-1}"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        -*)
            echo "Unknown option: $1" >&2
            usage
            exit 1
            ;;
        *)
            if [[ -z "$VM_HOST" ]]; then
                VM_HOST="$1"
                RUN_MODE="remote"
            fi
            shift
            ;;
    esac
done

if [[ -z "$VM_HOST" && -n "$ENV_VM_HOST" ]]; then
    VM_HOST="$ENV_VM_HOST"
    RUN_MODE="remote"
fi

SSH_CMD=()
if [[ "$RUN_MODE" == "remote" && -n "$VM_HOST" ]]; then
    SSH_OPTS=(-o ConnectTimeout=10 -o StrictHostKeyChecking=no)
    [[ -f "$SSH_KEY" ]] && SSH_OPTS+=(-i "$SSH_KEY")
    SSH_CMD=(ssh "${SSH_OPTS[@]}" "${SSH_USER}@${VM_HOST}")
fi

run_cmd() {
    if [[ ${#SSH_CMD[@]} -gt 0 ]]; then
        "${SSH_CMD[@]}" "$@"
    else
        "$@"
    fi
}

run_shell() {
    if [[ ${#SSH_CMD[@]} -gt 0 ]]; then
        "${SSH_CMD[@]}" "$1"
    else
        bash -c "$1"
    fi
}

say() {
    echo -e "${BLUE}[phase1-setup]${NC} $1"
}

ok() {
    echo -e "${GREEN}[OK]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

die() {
    echo -e "${RED}[FAIL] $1${NC}" >&2
    exit 1
}

echo -e "${BLUE}=========================================="
echo "Cuttlefish Phase 1 Setup"
echo "==========================================${NC}"
[[ -n "$VM_HOST" ]] && echo "Target: ${SSH_USER}@${VM_HOST}" || echo "Target: localhost"
echo "Mode: $RUN_MODE"
echo "Instance: $INSTANCE_NAME"
echo "WebRTC: https://<host>:$WEBRTC_PORT"
echo ""

say "Checking platform architecture"
ARCH="$(run_cmd uname -m)"
if [[ "$ARCH" != "aarch64" && "$ARCH" != "arm64" ]]; then
    warn "Detected architecture '$ARCH' (expected ARM64). Continuing."
else
    ok "ARM64 host detected ($ARCH)"
fi

say "Checking KVM"
if run_shell "test -e /dev/kvm"; then
    ok "/dev/kvm exists"
else
    die "/dev/kvm is missing. Cuttlefish requires virtualization support."
fi

if [[ "$DO_INSTALL" == true ]]; then
    say "Installing baseline packages"
    run_shell "sudo apt-get update -y >/dev/null"
    run_shell "sudo apt-get install -y git curl unzip adb qemu-kvm bridge-utils dnsmasq iptables iproute2 jq >/dev/null"
    ok "Baseline packages installed"
else
    warn "Skipping package installation (--no-install)"
fi

say "Checking Cuttlefish host tools"
if run_shell "command -v launch_cvd >/dev/null 2>&1" && run_shell "command -v cvd >/dev/null 2>&1"; then
    ok "launch_cvd and cvd are available"
else
    warn "launch_cvd/cvd not found."
    warn "Install Cuttlefish host tools and rerun:"
    warn "  See docs/CUTTLEFISH_PHASE1.md (Install Cuttlefish Host Tools section)"
    exit 2
fi

if [[ "$DO_LAUNCH" == true ]]; then
    say "Stopping previous Cuttlefish instances (best effort)"
    run_shell "cvd stop --clear_instance_dirs --instance_name $INSTANCE_NAME >/dev/null 2>&1 || true"

    say "Launching Cuttlefish instance"
    run_shell "HOME=\$HOME launch_cvd --daemon --instance_name=$INSTANCE_NAME --base_instance_num=$BASE_INSTANCE_NUM --start_webrtc=true --webrtc_sig_server_port=$WEBRTC_PORT >/tmp/${INSTANCE_NAME}-launch.log 2>&1"
    sleep 10

    if run_shell "cvd fleet | grep -q $INSTANCE_NAME"; then
        ok "Cuttlefish instance is running ($INSTANCE_NAME)"
    else
        warn "Instance did not appear in cvd fleet yet. Check logs:"
        warn "  /tmp/${INSTANCE_NAME}-launch.log"
    fi
else
    warn "Skipping launch (--skip-launch)"
fi

echo ""
echo -e "${BLUE}Next:${NC}"
echo "  1) Run validation:"
echo "     ./scripts/cuttlefish-phase1-validate.sh ${VM_HOST:-"--local"} --instance-name $INSTANCE_NAME --webrtc-port $WEBRTC_PORT"
echo "  2) If remote, open security rules for TCP ${WEBRTC_PORT} and 15550-15599 plus UDP 15550-15599"
echo ""
