# Alternative Solutions for Android Cloud Phone on ARM Linux

This document lists working alternatives to Waydroid for running Android on ARM Linux headless/cloud deployments.

## 🚀 Recommended: Redroid (Docker-based)

**Redroid** is a Docker-based Android container solution specifically designed for cloud deployments. It's actively maintained and known to work well on ARM64.

### Resources:
- **GitHub**: https://github.com/remote-android/redroid-doc
- **Docker Hub**: https://hub.docker.com/r/redroid/redroid
- **Documentation**: https://github.com/remote-android/redroid-doc

### Quick Start (ARM64):
```bash
# Pull ARM64 image
docker pull redroid/redroid:latest-arm64

# Run with VNC (port 5900)
docker run -itd \
  --privileged \
  --restart=unless-stopped \
  -p 5555:5555 \
  -p 5900:5900 \
  -v ~/redroid-data:/data \
  redroid/redroid:latest-arm64 \
  androidboot.redroid_gpu_mode=guest

# Connect via ADB
adb connect localhost:5555

# Connect via VNC (password: redroid)
# Use any VNC viewer to connect to localhost:5900
```

### Features:
- ✅ Works on ARM64
- ✅ Docker-based (easy deployment)
- ✅ Supports ADB
- ✅ VNC access
- ✅ Active development
- ✅ Better binder support than Waydroid
- ✅ Can run headless

### For Oracle Cloud ARM:
```bash
# Install Docker
sudo apt update
sudo apt install -y docker.io docker-compose
sudo systemctl enable docker
sudo systemctl start docker

# Run Redroid
sudo docker run -itd \
  --privileged \
  --restart=unless-stopped \
  --name redroid \
  -p 5555:5555 \
  -p 5900:5900 \
  -v /opt/redroid-data:/data \
  redroid/redroid:latest-arm64 \
  androidboot.redroid_gpu_mode=guest \
  androidboot.redroid_width=1280 \
  androidboot.redroid_height=720

# Enable ADB over network
sudo docker exec redroid setprop service.adb.tcp.port 5555
sudo docker exec redroid start adbd
```

---

## 📦 Anbox Cloud (Canonical)

**Anbox Cloud** is Canonical's commercial solution for running Android at scale. It uses LXD containers and supports ARM.

### Resources:
- **Website**: https://anbox-cloud.io/
- **Documentation**: https://discourse.ubuntu.com/c/anbox-cloud/
- **GitHub**: https://github.com/anbox-cloud

### Notes:
- Commercial product (may have free tier for testing)
- Uses LXD instead of Docker
- Designed for production cloud deployments
- Supports both ARM and x86
- GPU acceleration available

### Installation:
```bash
# Requires Ubuntu Pro or subscription
# See: https://anbox-cloud.io/docs/installation
```

---

## 🐳 Android-x86 Docker (⚠️ NOT RECOMMENDED - Outdated)

**Status:** ❌ **Old/Inactive** - Last updated August 2019, not maintained

While Android-x86 Docker projects exist (like budtmo/docker-android), they are **not recommended** because:

- ❌ **Outdated** - No updates since 2019
- ❌ **No native ARM64** - Only x86 with QEMU emulation (very slow)
- ❌ **Security risks** - Old Android versions with vulnerabilities
- ❌ **Missing features** - No modern Android APIs or virtual device support
- ❌ **Poor performance** - QEMU emulation adds significant overhead

### Resources (for reference only):
- **GitHub**: https://github.com/budtmo/docker-android (inactive)
- **Docker Hub**: https://hub.docker.com/r/budtmo/docker-android

### Recommendation:
**Don't use old docker-android projects.** Use **Redroid** or **Waydroid** instead for modern, native ARM64 support.

---

## 🔧 Alternative: Genymotion Cloud

**Genymotion** offers cloud-based Android instances, but it's a commercial service.

### Resources:
- **Website**: https://www.genymotion.com/
- **Cloud**: https://www.genymotion.com/cloud/

### Notes:
- Commercial service (paid)
- Managed cloud instances
- Good for testing/CI/CD
- May have free tier for personal use

---

## 🎯 Comparison Table

| Solution | ARM Support | Docker | Free | Headless | VNC | ADB | Status |
|----------|-------------|--------|------|----------|-----|-----|--------|
| **Redroid** | ✅ ARM64 | ✅ | ✅ | ✅ | ✅ | ✅ | ⭐ Active |
| **Anbox Cloud** | ✅ ARM | ❌ (LXD) | ⚠️ Commercial | ✅ | ✅ | ✅ | ⭐ Active |
| **Waydroid** | ✅ ARM | ❌ | ✅ | ⚠️ Complex | ✅ | ✅ | ⚠️ Issues |
| **Genymotion** | ✅ | ❌ | ⚠️ Paid | ✅ | ✅ | ✅ | ⭐ Managed |

---

## 🚀 Migration Guide: Waydroid → Redroid

If you want to switch from Waydroid to Redroid:

### 1. Stop Waydroid Services
```bash
sudo systemctl stop waydroid-session waydroid-container
sudo systemctl disable waydroid-session waydroid-container
```

### 2. Install Docker
```bash
sudo apt update
sudo apt install -y docker.io docker-compose
sudo systemctl enable docker
sudo systemctl start docker
```

### 3. Deploy Redroid
```bash
# Create data directory
sudo mkdir -p /opt/redroid-data
sudo chmod 777 /opt/redroid-data

# Run Redroid container
sudo docker run -itd \
  --privileged \
  --restart=unless-stopped \
  --name redroid \
  -p 5555:5555 \
  -p 5900:5900 \
  -v /opt/redroid-data:/data \
  redroid/redroid:latest-arm64 \
  androidboot.redroid_gpu_mode=guest \
  androidboot.redroid_width=1280 \
  androidboot.redroid_height=720 \
  androidboot.redroid_fps=30

# Enable ADB
sudo docker exec redroid setprop service.adb.tcp.port 5555
sudo docker exec redroid start adbd
```

### 4. Connect via ADB
```bash
adb connect localhost:5555
adb devices
```

### 5. Connect via VNC
```bash
# Default password: redroid
# Use TigerVNC or any VNC client
vncviewer localhost:5900
```

### 6. Install GAPPS (if needed)
Redroid images come with GAPPS pre-installed in some variants. Check available tags:
```bash
docker search redroid
```

---

## 📝 Next Steps

1. **Try Redroid first** - It's the most mature Docker-based solution for ARM
2. **Test on Oracle Cloud ARM** - Should work better than Waydroid
3. **Integrate with existing scripts** - Modify your deployment scripts to use Docker instead of systemd services
4. **Update VNC/RTMP setup** - Redroid exposes VNC on port 5900 by default

---

## 🔗 Useful Links

- Redroid GitHub: https://github.com/remote-android/redroid-doc
- Redroid Docker Hub: https://hub.docker.com/r/redroid/redroid
- Redroid Issues/Discussions: https://github.com/remote-android/redroid-doc/issues
- Anbox Cloud Docs: https://discourse.ubuntu.com/c/anbox-cloud/
- Android Container Solutions: https://github.com/topics/android-container

---

## 💡 Recommendation

**Start with Redroid** - It's Docker-based, actively maintained, and known to work well on ARM64. It should solve the binder/zygote issues you're experiencing with Waydroid.


