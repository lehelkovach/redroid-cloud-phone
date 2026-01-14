#!/bin/bash
# fix-waydroid-boot.sh
# Fixes common Waydroid boot issues based on research and error patterns

set -e

echo "========================================"
echo "  Waydroid Boot Fix Script"
echo "========================================"

# 1. Ensure binder devices exist and have correct permissions
echo "[1/5] Fixing binder devices..."
chmod 666 /dev/binderfs/anbox-* 2>/dev/null || true
ln -sf /dev/binderfs/anbox-binder /dev/anbox-binder 2>/dev/null || true
ln -sf /dev/binderfs/anbox-vndbinder /dev/anbox-vndbinder 2>/dev/null || true
ln -sf /dev/binderfs/anbox-hwbinder /dev/anbox-hwbinder 2>/dev/null || true

# 2. Ensure waydroid0 bridge exists
echo "[2/5] Ensuring waydroid0 bridge..."
if ! ip link show waydroid0 >/dev/null 2>&1; then
    ip link add name waydroid0 type bridge
    ip link set waydroid0 up
fi

# 3. Start waydroid network
echo "[3/5] Starting waydroid network..."
/usr/lib/waydroid/data/scripts/waydroid-net.sh start 2>&1 | grep -v "iptables.*Bad rule" || true

# 4. Set Android properties to allow zygote to start
echo "[4/5] Setting Android properties for boot..."
waydroid shell setprop odsign.verification.done 1 2>&1 || true
waydroid shell setprop ro.crypto.state unencrypted 2>&1 || true

# 5. Create /dev/cgroup in container (needed for process groups)
echo "[5/5] Creating /dev/cgroup in container..."
lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- mkdir -p /dev/cgroup 2>/dev/null || true
lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- mount -t tmpfs tmpfs /dev/cgroup 2>/dev/null || true

echo ""
echo "========================================"
echo "  Fixes applied!"
echo "========================================"
echo ""
echo "Now restart waydroid:"
echo "  sudo systemctl restart waydroid-container waydroid-session"
echo ""
echo "Then manually start zygote if needed:"
echo "  sudo waydroid shell setprop ctl.start zygote"










