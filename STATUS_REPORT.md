# Status Report - Waydroid Cloud Phone Project

**Date:** January 9, 2025  
**Instance:** Oracle Cloud ARM (Ampere A1 Flex) - 161.153.55.58  
**Last Update:** Testing Redroid alternative

---

## 🎯 Current Status: **MIGRATING TO REDROID**

### Summary
After extensive troubleshooting of Waydroid, we've identified persistent binder/zygote issues that appear to be kernel-related (Oracle Cloud's 6.8 kernel). We're now testing **Redroid** as a Docker-based alternative that should work better on ARM64.

---

## ✅ Completed Actions

### 1. Waydroid Troubleshooting (Previous Attempts)
- ✅ Fixed network bridge (`waydroid0`)
- ✅ Fixed rootfs mounting issues
- ✅ Fixed PulseAudio socket issues
- ✅ Fixed binderfs mounting
- ✅ Added VM-specific properties (`ro.hardware.gralloc=default`, `ro.hardware.egl=swiftshader`)
- ✅ Changed binder protocol to `aidl2`
- ⚠️ **Still failing:** Binder VMA errors (`binder_alloc_buf, no vma`)
- ⚠️ **Still failing:** Zygote crashes repeatedly

### 2. Redroid Setup (In Progress)
- ✅ Docker installed successfully
- ✅ Redroid `latest` image pulled successfully
- ✅ Created `/opt/redroid-data` directory
- ⏳ Container startup attempted (connection timed out during execution)

---

## 🔴 Current Issues

### 1. SSH Connection Timeout
- **Status:** Cannot connect to instance (161.153.55.58)
- **Possible Causes:**
  - Instance rebooting
  - Network/firewall issue
  - Instance stopped/terminated
  - Security group blocking SSH

### 2. Waydroid Issues (Historical)
- **Binder VMA errors:** `binder_alloc_buf, no vma` - persistent kernel-level issue
- **Zygote crashes:** Android init process failing to start
- **Root cause:** Suspected kernel compatibility (6.8.0-1038-oracle)

---

## 📋 Next Steps

### Immediate Actions Needed:
1. **Verify Instance Status**
   ```bash
   # Check if instance is running
   oci compute instance get --instance-id <your-instance-id>
   
   # Or try SSH again
   ssh -i ~/.ssh/waydroid_oci ubuntu@161.153.55.58
   ```

2. **Complete Redroid Deployment** (once connection restored)
   ```bash
   # Check if Redroid container is running
   sudo docker ps -a | grep redroid
   
   # If not running, start it:
   sudo docker run -itd \
     --privileged \
     --restart=unless-stopped \
     --name redroid \
     -p 5555:5555 \
     -p 5900:5900 \
     -v /opt/redroid-data:/data \
     redroid/redroid:latest \
     androidboot.redroid_gpu_mode=guest \
     androidboot.redroid_width=1280 \
     androidboot.redroid_height=720 \
     androidboot.redroid_fps=30
   
   # Enable ADB
   sudo docker exec redroid setprop service.adb.tcp.port 5555
   sudo docker exec redroid start adbd
   ```

3. **Test Redroid Connection**
   ```bash
   # From local machine
   adb connect 161.153.55.58:5555
   adb devices
   
   # Check Android version
   adb shell getprop ro.build.version.release
   ```

4. **Set Up VNC Access**
   - Redroid exposes VNC on port 5900
   - Default password: `redroid`
   - Connect with: `vncviewer 161.153.55.58:5900`

---

## 📊 Comparison: Waydroid vs Redroid

| Feature | Waydroid | Redroid |
|---------|----------|---------|
| **Container Type** | LXC | Docker |
| **ARM64 Support** | ✅ Yes | ✅ Yes |
| **Binder Support** | ⚠️ Issues on Oracle Cloud | ✅ Better |
| **Deployment** | Complex (systemd services) | Simple (Docker) |
| **Status** | ❌ Not working | ⏳ Testing |
| **VNC** | ✅ Yes (via Weston) | ✅ Yes (built-in) |
| **ADB** | ✅ Yes | ✅ Yes |
| **GAPPS** | Manual install | Pre-installed options |

---

## 🔧 Configuration Files Created

### Waydroid (Current - Not Working)
- `/etc/systemd/system/waydroid-container.service`
- `/etc/systemd/system/waydroid-session.service`
- `/etc/systemd/system/weston.service`
- `/var/lib/waydroid/waydroid.cfg`

### Redroid (New - Testing)
- Docker container: `redroid`
- Data volume: `/opt/redroid-data`
- Ports: 5555 (ADB), 5900 (VNC)

---

## 📁 Project Files

### Scripts Created:
- ✅ `scripts/test-redroid.sh` - Redroid deployment script
- ✅ `scripts/troubleshoot-waydroid.sh` - Waydroid diagnostics
- ✅ `scripts/get-troubleshoot-log.sh` - Retrieve logs
- ✅ `scripts/fix-waydroid-boot.sh` - Boot fixes (partial success)

### Documentation:
- ✅ `ALTERNATIVES.md` - Alternative solutions guide
- ✅ `TROUBLESHOOTING.md` - Waydroid troubleshooting guide
- ✅ `STATUS_REPORT.md` - This file

---

## 🚨 Known Problems

### Waydroid Issues:
1. **Binder VMA Errors**
   - Error: `binder_alloc_buf, no vma`
   - Root cause: Kernel compatibility issue
   - Status: Unresolved

2. **Zygote Crashes**
   - Error: Zygote process crashes repeatedly
   - Root cause: Related to binder issues
   - Status: Unresolved

3. **Overlay Mount Read-Only**
   - Error: `libprocessgroup failed to create and chown uid_0 --read only file system`
   - Fix: Removed custom mount script, let Waydroid handle mounts
   - Status: Partially resolved

### Redroid Issues:
1. **Connection Timeout**
   - Error: SSH connection timed out during deployment
   - Status: Need to verify instance status

---

## 💡 Recommendations

### Short Term:
1. **Verify instance is running** and accessible
2. **Complete Redroid deployment** once connection restored
3. **Test Redroid** with ADB and VNC
4. **Compare performance** vs Waydroid

### Long Term:
1. **If Redroid works:** Migrate fully to Redroid
   - Remove Waydroid services
   - Update deployment scripts
   - Update documentation

2. **If Redroid fails:** Consider alternatives:
   - Anbox Cloud (commercial)
   - Genymotion Cloud (commercial)
   - Custom Android-x86 with QEMU (slower)

---

## 📞 Connection Information

### Instance Details:
- **IP:** 161.153.55.58
- **SSH Key:** `~/.ssh/waydroid_oci`
- **User:** `ubuntu`
- **OS:** Ubuntu 22.04.5 LTS
- **Kernel:** 6.8.0-1038-oracle (aarch64)

### Ports:
- **SSH:** 22
- **VNC (Waydroid):** 5901
- **VNC (Redroid):** 5900
- **ADB:** 5555
- **RTMP:** 1935

---

## 🔍 Debugging Commands

### Check Instance Status:
```bash
# SSH connection
ssh -i ~/.ssh/waydroid_oci ubuntu@161.153.55.58

# Check Docker
sudo docker ps -a
sudo docker logs redroid

# Check Waydroid (if still installed)
sudo systemctl status waydroid-container
sudo systemctl status waydroid-session
waydroid status
```

### Check Redroid:
```bash
# Container status
sudo docker ps -a | grep redroid

# Container logs
sudo docker logs -f redroid

# ADB connection
adb connect 161.153.55.58:5555
adb devices
adb shell getprop ro.build.version.release
```

### Check Waydroid (if needed):
```bash
# Service status
sudo systemctl status waydroid-container waydroid-session

# Waydroid status
waydroid status

# Container logs
sudo journalctl -u waydroid-container -n 50
sudo journalctl -u waydroid-session -n 50
```

---

## 📝 Notes

- **Oracle Cloud ARM** uses kernel 6.8, which may have compatibility issues with Waydroid's binder implementation
- **Redroid** uses Docker, which provides better isolation and may avoid kernel-level issues
- **Docker** was successfully installed and Redroid image was pulled before connection timeout
- **Next step:** Verify instance accessibility and complete Redroid deployment

---

## 🎯 Success Criteria

### Redroid Success:
- ✅ Container starts and stays running
- ✅ Android boots successfully
- ✅ ADB connection works (`adb devices` shows device)
- ✅ VNC access works (can see Android UI)
- ✅ No binder/zygote errors in logs

### Full Migration Success:
- ✅ Redroid running stable
- ✅ Waydroid services stopped/removed
- ✅ VNC/RTMP streaming working
- ✅ Google Play Store accessible
- ✅ Persistent tunnel connection

---

**Last Updated:** January 9, 2025  
**Status:** ⏳ Waiting for instance connection to complete Redroid deployment







