#!/bin/bash
# setup-redroid-binder.sh
# Enable Android binder/ashmem + binderfs so the redroid container can boot.
#
# Why: OCI Ampere (A1.Flex) instances ship the Oracle kernel which *includes* the
# binder_linux/ashmem_linux modules but does NOT load them or mount binderfs by
# default, and exposes no /dev/kvm (so Cuttlefish can't run on these VMs). Without
# binder, redroid's Android never reaches `adb` "device" state. This installs a
# oneshot systemd unit that loads the modules and mounts binderfs before redroid.
#
# Idempotent. Run with sudo on the instance (or bake into the golden image).
set -euo pipefail

echo "[1/3] Persist module load on boot"
printf 'binder_linux\nashmem_linux\n' | sudo tee /etc/modules-load.d/redroid-binder.conf >/dev/null

echo "[2/3] Install binderfs mount unit (ordered before redroid-container)"
sudo tee /etc/systemd/system/redroid-binderfs.service >/dev/null <<'UNIT'
[Unit]
Description=Load Android binder/ashmem and mount binderfs for redroid
DefaultDependencies=no
After=systemd-modules-load.service
Before=redroid-container.service docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/sbin/modprobe binder_linux
ExecStart=/sbin/modprobe ashmem_linux
ExecStart=/bin/mkdir -p /dev/binderfs
ExecStart=/bin/sh -c 'mountpoint -q /dev/binderfs || mount -t binder binder /dev/binderfs'

[Install]
WantedBy=multi-user.target
UNIT

echo "[3/3] Enable + start now"
sudo systemctl daemon-reload
sudo systemctl enable --now redroid-binderfs.service
ls -l /dev/binderfs
echo "binder/binderfs ready. (Re)start redroid with: sudo systemctl restart redroid-container.service"
