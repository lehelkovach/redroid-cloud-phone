# Current Status Report

**Last Updated:** 2026-01-14  
**Project:** Redroid Cloud Phone on Oracle Cloud ARM

---

## 🎯 Project Overview

This project deploys a cloud-based Android phone using Redroid (Docker-based Android) on Oracle Cloud ARM instances. The goal is to provide:

- **Remote Android access** via VNC and ADB
- **Virtual camera support** for RTMP streaming
- **Virtual audio input** for streaming microphone
- **Control API** for automation

---

## ✅ What's Working

### Core Functionality
| Component | Status | Notes |
|-----------|--------|-------|
| Redroid Container | ✅ Working | Docker-based Android 16 on ARM64 |
| ADB Access | ✅ Working | Port 5555 for Android debugging |
| VNC Access | ✅ Working | Port 5900 (password: `redroid`) |
| Control API | ✅ Working | 11 endpoints for automation |
| Test Suites | ✅ Working | Comprehensive test scripts |
| Systemd Services | ✅ Working | Redroid target and services |

### Scripts & Automation
| Script | Purpose | Status |
|--------|---------|--------|
| `install-redroid.sh` | One-command Redroid installation | ✅ Ready |
| `test-redroid-full.sh` | 10-category comprehensive test suite | ✅ Ready |
| `health-check.sh` | Quick health check (Redroid + Waydroid) | ✅ Ready |
| `test-api.sh` | Control API endpoint tests | ✅ Ready |
| `ffmpeg-bridge.sh` | RTMP to virtual device bridge | ✅ Ready |
| `redroid-container.sh` | Idempotent container launcher | ✅ Ready |

### Systemd Services
| Service | Purpose | Status |
|---------|---------|--------|
| `redroid-container.service` | Manages Redroid container | ✅ Ready |
| `redroid-cloud-phone.target` | Starts all Redroid services | ✅ Ready |
| `nginx-rtmp.service` | RTMP server | ✅ Ready |
| `ffmpeg-bridge.service` | Stream to virtual devices | ✅ Ready |
| `control-api.service` | REST API server | ✅ Ready |

---

## ⚠️ Known Limitations

### Virtual Device Support (Kernel 6.8+ Incompatibility)
- **Issue**: `v4l2loopback` module fails to build on kernel 6.8+
- **Impact**: Virtual camera (`/dev/video42`) and audio not available
- **Root Cause**: Oracle ARM Ubuntu 22.04 ships with kernel 6.8
- **Workaround**: Use Ubuntu 20.04 with kernel 5.x

### RTMP Streaming Pipeline
- **Status**: Scripts ready, but blocked by virtual device issue
- **When Working**: OBS → nginx-rtmp → FFmpeg → virtual camera → Android

---

## 📁 Project Structure

```
redroid-cloud-phone/
├── api/
│   ├── server.py           # Control API server (11 endpoints)
│   └── requirements.txt    # Python dependencies
├── config/
│   └── nginx-rtmp.conf     # RTMP server configuration
├── scripts/
│   ├── install-redroid.sh          # Main installer
│   ├── redroid-container.sh        # Container launcher
│   ├── test-redroid-full.sh        # Full test suite
│   ├── health-check.sh             # Health check
│   ├── ffmpeg-bridge.sh            # RTMP bridge
│   └── ... (40+ scripts)
├── systemd/
│   ├── redroid-container.service   # Container service
│   ├── redroid-cloud-phone.target  # Service target
│   └── ... (12 service files)
└── *.md                            # Documentation
```

---

## 🚀 Quick Start

### Fresh Installation
```bash
# Clone and install
git clone https://github.com/lehelkovach/redroid-cloud-phone.git
cd redroid-cloud-phone
sudo ./install-redroid.sh

# Start all services
sudo systemctl start redroid-cloud-phone.target
```

### Verify Installation
```bash
# Health check
sudo /opt/waydroid-scripts/health-check.sh

# Full test (requires SSH key)
./scripts/test-redroid-full.sh 137.131.52.69
```

### Connect to Android
```bash
# VNC (via SSH tunnel)
ssh -L 5900:localhost:5900 ubuntu@137.131.52.69 -N
vncviewer localhost:5900  # password: redroid

# ADB
adb connect 137.131.52.69:5555
adb shell
```

### Control API
```bash
# Via SSH tunnel
ssh -L 8080:localhost:8080 ubuntu@137.131.52.69 -N

# Test endpoints
curl http://localhost:8080/health
curl http://localhost:8080/device/info
curl -X POST -H "Content-Type: application/json" \
  -d '{"x":540,"y":960}' http://localhost:8080/device/tap
```

---

## 📊 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/device/info` | Screen dimensions and density |
| GET | `/device/screenshot` | PNG screenshot |
| POST | `/device/tap` | Tap at coordinates |
| POST | `/device/swipe` | Swipe gesture |
| POST | `/device/press` | Long press |
| POST | `/device/text` | Input text |
| POST | `/device/key` | Press key |
| POST | `/device/shell` | Run shell command |
| POST | `/device/app/start` | Start app |
| POST | `/device/app/stop` | Stop app |

---

## 📋 Instance Details

| Item | Value |
|------|-------|
| **Instance IP** | 137.131.52.69 |
| **SSH Key** | ~/.ssh/waydroid_oci |
| **VNC Port** | 5900 |
| **VNC Password** | redroid |
| **ADB Port** | 5555 |
| **API Port** | 8080 (localhost) |
| **RTMP Port** | 1935 |
| **OS** | Ubuntu 22.04.5 LTS |
| **Kernel** | 6.8.0-1038-oracle |

---

## 🔧 Next Steps

### For Full Virtual Device Support
1. Create Ubuntu 20.04 instance (kernel 5.x)
2. Run `install-redroid.sh`
3. Virtual camera and audio will work

### For Current Instance
1. ✅ Redroid container is operational
2. ✅ ADB and VNC access working
3. ✅ Control API functional
4. ⚠️ Virtual devices blocked by kernel

---

**Status**: ✅ Core Features Operational | ⚠️ Virtual Devices Require Kernel 5.x
