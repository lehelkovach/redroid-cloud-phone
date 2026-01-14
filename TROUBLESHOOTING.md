# Waydroid Troubleshooting Guide

## Commands to Run in Weston Console

When you're connected via VNC and see the Weston desktop, you can open the console (top-left button) and run these commands:

### Basic Status Checks

```bash
# Check Waydroid status
waydroid status

# Check if Android is accessible via ADB
adb devices

# Check container service
 

# Check session service
sudo systemctl status waydroid-session
```

### Container Diagnostics

```bash
# Check if LXC container exists
sudo lxc-ls -f

# Check container logs
sudo journalctl -u waydroid-container -n 50 --no-pager

# Check if container images exist
sudo ls -lh /var/lib/waydroid/images/

# Check container config
sudo cat /var/lib/waydroid/waydroid.cfg
```

### Kernel Module Checks

```bash
# Check binder modules
lsmod | grep binder

# Check binderfs mount
mount | grep binder

# Check binderfs devices
ls -la /dev/binderfs/
```

### Manual Start Attempts

```bash
# Try starting container manually
sudo waydroid container stop
sudo waydroid container start

# Try starting session manually (after container is running)
export DISPLAY=:1
export XDG_RUNTIME_DIR=/run/user/1002
export WAYLAND_DISPLAY=wayland-0
waydroid session start
```

### System Logs

```bash
# Check kernel logs for errors
sudo journalctl -k -n 50 | grep -i "lxc\|waydroid\|binder"

# Check all Waydroid-related logs
sudo journalctl -u waydroid-container --since "10 minutes ago" --no-pager
```

### Process Checks

```bash
# See what's running
ps aux | grep -E "waydroid|weston|lxc"

# Check network interfaces
ip addr show | grep waydroid
```

### Common Issues and Fixes

#### Container Won't Start

1. **Check if images are corrupted:**
   ```bash
   sudo ls -lh /var/lib/waydroid/images/
   ```
   Should show `system.img` (~2.8GB) and `vendor.img` (~355MB)

2. **Check binder modules:**
   ```bash
   lsmod | grep binder
   mount | grep binder
   ```
   Should show `binder_linux` loaded and `/dev/binderfs` mounted

3. **Try reinitializing (WARNING: This will delete Android data):**
   ```bash
   sudo systemctl stop waydroid-container waydroid-session
   sudo waydroid init -s GAPPS
   ```

#### Session Won't Start

1. **Check Wayland socket:**
   ```bash
   ls -la /run/user/1002/wayland*
   ```
   Should show `wayland-0` socket

2. **Check Weston is running:**
   ```bash
   ps aux | grep weston
   ```

3. **Check environment variables:**
   ```bash
   echo $DISPLAY
   echo $XDG_RUNTIME_DIR
   echo $WAYLAND_DISPLAY
   ```
   Should show:
   - `DISPLAY=:1`
   - `XDG_RUNTIME_DIR=/run/user/1002`
   - `WAYLAND_DISPLAY=wayland-0`

### Quick Diagnostic Script

Run this to get a full status report:

```bash
echo "=== Waydroid Status ==="
waydroid status
echo ""
echo "=== ADB Devices ==="
adb devices
echo ""
echo "=== Container Service ==="
sudo systemctl status waydroid-container --no-pager | head -10
echo ""
echo "=== Binder Modules ==="
lsmod | grep binder
echo ""
echo "=== Binderfs Mount ==="
mount | grep binder
echo ""
echo "=== Images ==="
sudo ls -lh /var/lib/waydroid/images/
echo ""
echo "=== LXC Containers ==="
sudo lxc-ls -f
echo ""
echo "=== Weston Process ==="
ps aux | grep weston | grep -v grep
echo ""
echo "=== Wayland Socket ==="
ls -la /run/user/1002/wayland* 2>/dev/null || echo "Not found"
```










