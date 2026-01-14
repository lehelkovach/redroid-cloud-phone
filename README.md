# Redroid Cloud Phone

**Android cloud phone deployment on Oracle Cloud ARM using Redroid**

[![Status](https://img.shields.io/badge/status-operational-success)](https://github.com)
[![Platform](https://img.shields.io/badge/platform-Oracle%20Cloud%20ARM-blue)](https://www.oracle.com/cloud/)
[![Container](https://img.shields.io/badge/container-Redroid-green)](https://github.com/remote-android/redroid-doc)

---

## 🚀 Quick Start

### Prerequisites
- Oracle Cloud Infrastructure account (Always Free tier eligible)
- OCI CLI configured (`~/.oci/config`)
- SSH key for instance access
- ADB tools (optional, for Android debugging)

### Install on a fresh instance

**Option 1: Full deployment with virtual devices (Ubuntu 20.04 - Recommended)**

Deploy a new instance with kernel 5.x for full virtual camera/audio support:

```bash
# From your local machine with OCI CLI configured
./scripts/deploy-ubuntu20-redroid.sh my-cloud-phone
```

This creates an Ubuntu 20.04 instance and installs everything automatically.

**Option 2: Manual install on existing instance**

SSH into your instance and run:

```bash
sudo ./install-redroid.sh
sudo systemctl start redroid-cloud-phone.target
```

Note: On Ubuntu 22.04 (kernel 6.8+), virtual devices won't work. Use Ubuntu 20.04.

**Option 3: Legacy Waydroid installer**

```bash
sudo ./install.sh
```

**Option 4: Full-featured deployment with proxy, GPS, GApps**

```bash
# Deploy with all features
./scripts/deploy-cloud-phone.sh \
  --name my-phone \
  --proxy socks5://proxy.example.com:1080 \
  --gps 37.7749,-122.4194 \
  --gapps \
  --ocpus 2 \
  --memory 8

# Or use a config file
./scripts/deploy-cloud-phone.sh --config my-config.json
```

See **[FEATURES.md](FEATURES.md)** for all configuration options.

### Quick Verification

```bash
# Check instance status
oci compute instance get --instance-id <OCID> --query 'data."lifecycle-state"'

# Check Redroid container
ssh -i ~/.ssh/waydroid_oci ubuntu@<INSTANCE_IP> 'sudo docker ps | grep redroid'

# Run full test suite
./scripts/test-redroid-full.sh <INSTANCE_IP>
```

### Connect to Android

**VNC (Visual Access):**
```bash
# Terminal 1: Create SSH tunnel
ssh -i ~/.ssh/waydroid_oci -L 5900:localhost:5900 ubuntu@<INSTANCE_IP> -N

# Terminal 2: Connect VNC
vncviewer localhost:5900
# Password: redroid
```

**ADB (Command Line):**
```bash
adb connect <INSTANCE_IP>:5555
adb devices
adb shell getprop ro.build.version.release
```

---

## 📋 Project Status

### ✅ Operational
- Redroid container running on Oracle Cloud ARM
- ADB access working (port 5555)
- VNC access working (port 5900)
- **Proxy support** (HTTP, SOCKS5, Transparent)
- **GPS/Location spoofing**
- **Google Play Store** (GApps) installation
- **Control API** with full ADB interface
- **Multiple viewing methods** (VNC, scrcpy, WebRTC, headless)
- **Parameterized deployment** with config files
- Test suites created and passing
- Comprehensive documentation

### ⚠️ Known Limitations
- Virtual devices (camera/audio) require kernel 5.x
- Requires Ubuntu 20.04 for full virtual device support
- GApps may require device certification registration

---

## 📁 Project Structure

```
redroid-cloud-phone/
├── scripts/                      # Automation scripts
│   ├── deploy-cloud-phone.sh     # Full-featured deployment
│   ├── deploy-ubuntu20-redroid.sh # Ubuntu 20.04 deployment
│   ├── proxy-control.sh          # Proxy configuration
│   ├── install-gapps.sh          # Google Play installer
│   ├── viewing-control.sh        # VNC/scrcpy/WebRTC control
│   ├── redroid-container.sh      # Container management
│   ├── health-check.sh           # System health check
│   ├── test-redroid-full.sh      # Comprehensive test suite
│   └── ...                       # More scripts
├── api/
│   └── server.py                 # Control API server
├── config/
│   ├── cloud-phone-config.schema.json   # Config schema
│   ├── cloud-phone-config.example.json  # Example config
│   └── nginx-rtmp.conf           # RTMP config
├── systemd/                      # Systemd service files
├── *.md                          # Documentation
│   ├── FEATURES.md               # All features guide
│   ├── HANDOFF.md                # Complete handoff guide
│   └── ...                       # More docs
└── README.md                     # This file
```

---

## 🏗️ Architecture

### Current Implementation: **Redroid**

**Why Redroid?**
- ✅ Docker-based (simpler deployment)
- ✅ Better ARM64 support
- ✅ No kernel binder dependencies
- ✅ Cloud-focused design
- ✅ Currently operational

**Previous Attempt:** Waydroid (LXC-based) - Encountered kernel 6.8 binder compatibility issues

### Components

- **Redroid Container**: Docker-based Android
- **ADB**: Android Debug Bridge (port 5555)
- **VNC**: Virtual Network Computing (port 5900)
- **Control API**: REST API for automation (port 8080)
- **Oracle Cloud ARM**: Ampere A1 Flex instance

---

## 🎛️ Features

### Proxy Support

Route all Android traffic through a proxy:

```bash
# Via deployment
./scripts/deploy-cloud-phone.sh --proxy socks5://proxy:1080

# Via API
curl -X POST http://localhost:8080/proxy \
  -d '{"enabled":true,"type":"socks5","host":"proxy","port":1080}'
```

### GPS Spoofing

Set fake GPS location:

```bash
# Via deployment
./scripts/deploy-cloud-phone.sh --gps 37.7749,-122.4194

# Via API
curl -X POST http://localhost:8080/location \
  -d '{"enabled":true,"latitude":37.7749,"longitude":-122.4194}'
```

### Google Play Store

```bash
./scripts/deploy-cloud-phone.sh --gapps --gapps-variant pico
```

### Control API

Full REST API for automation:

```bash
# Screenshot
curl -o screen.png http://localhost:8080/device/screenshot

# Tap screen
curl -X POST http://localhost:8080/device/input \
  -d '{"type":"tap","x":500,"y":500}'

# Run ADB command
curl -X POST http://localhost:8080/adb/shell \
  -d '{"command":"pm list packages"}'
```

See **[FEATURES.md](FEATURES.md)** for complete documentation

---

## 📚 Documentation

### Essential Reading
- **[HANDOFF.md](HANDOFF.md)** - Complete handoff guide for new developers/agents
- **[QUICK_START.md](QUICK_START.md)** - Quick reference guide
- **[TEST_RESULTS_FULL_COVERAGE.md](TEST_RESULTS_FULL_COVERAGE.md)** - Latest test results

### Development Guides
- **[DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md)** - Development workflow (cloud agent vs local)
- **[PROJECT_NAME.md](PROJECT_NAME.md)** - Project name clarification (Redroid vs Waydroid)

### Technical Docs
- **[DECISION_REDROID.md](DECISION_REDROID.md)** - Why Redroid was chosen
- **[REDROID_VIRTUAL_DEVICES.md](REDROID_VIRTUAL_DEVICES.md)** - Virtual device support analysis
- **[REDROID_ORACLE_LINUX.md](REDROID_ORACLE_LINUX.md)** - Oracle Linux compatibility

---

## 🧪 Testing

### Run Full Test Suite

```bash
./scripts/test-redroid-full.sh <INSTANCE_IP>
```

**Test Coverage:**
1. Instance Connectivity
2. Docker Status
3. Redroid Container Status
4. Port Mappings
5. Container Logs Health
6. ADB Connectivity
7. Android System Information
8. VNC Port Accessibility
9. Container Resource Usage
10. Virtual Device Support

### Individual Tests

```bash
# ADB/VNC test
./scripts/test-adb-vnc.sh <INSTANCE_IP>

# VNC status check
./scripts/check-redroid-vnc.sh <INSTANCE_IP>

# Complete Redroid test
./scripts/test-redroid-complete.sh <INSTANCE_IP>
```

---

## 🔧 Setup & Configuration

### Quick Install (Redroid - Recommended)

```bash
# Clone the repository
git clone https://github.com/lehelkovach/redroid-cloud-phone.git
cd redroid-cloud-phone

# Run the Redroid installer
sudo ./install-redroid.sh
```

This will:
- Install Docker and required packages
- Pull and start the Redroid container
- Configure VNC (port 5900) and ADB (port 5555)
- Set up virtual device modules (if compatible)

### Instance Details
- **OS**: Ubuntu 22.04.5 LTS
- **Kernel**: 6.8.0-1038-oracle (aarch64)
- **Instance Type**: Oracle Cloud ARM (Ampere A1 Flex)
- **Container**: Redroid (Docker)

### Redroid Configuration

```bash
docker run -itd \
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
  androidboot.redroid_fps=30 \
  androidboot.redroid_vnc=1 \
  androidboot.redroid_vnc_port=5900
```

---

## 🚨 Troubleshooting

### SSH Timeout
```bash
# Check instance status
oci compute instance get --instance-id <OCID> --query 'data."lifecycle-state"'

# Reboot if needed
oci compute instance action --instance-id <OCID> --action RESET --wait-for-state RUNNING
```

### Container Not Running
```bash
# Check Docker
ssh -i ~/.ssh/waydroid_oci ubuntu@<INSTANCE_IP> 'sudo systemctl status docker'

# Start container
ssh -i ~/.ssh/waydroid_oci ubuntu@<INSTANCE_IP> 'sudo docker start redroid'
```

### VNC Not Working
```bash
# Fix VNC
./scripts/fix-redroid-vnc.sh <INSTANCE_IP>
```

See **[HANDOFF.md](HANDOFF.md)** for complete troubleshooting guide.

---

## 🔐 Security Notes

**Important:** This repository excludes sensitive files via `.gitignore`:
- SSH private keys
- OCI API keys
- Instance IPs (if sensitive)
- Credentials and secrets

**Never commit:**
- `~/.ssh/waydroid_oci` (SSH key)
- `~/.oci/oci_api_key.pem` (OCI credentials)
- `~/.oci/config` (OCI config with credentials)

---

## 📝 Next Steps

### Immediate
1. ✅ Install ADB locally: `sudo apt-get install android-tools-adb`
2. ✅ Test VNC connection visually
3. ✅ Verify Android functionality via ADB

### Medium Priority
1. Address virtual device support (kernel 6.8 compatibility)
2. Implement RTMP streaming pipeline
3. Create control API

### Long-term
1. Golden image creation
2. Multi-instance deployment
3. Monitoring and logging

---

## 🤝 Contributing

This project is set up for handoff to other developers/agents. See **[HANDOFF.md](HANDOFF.md)** for complete handoff instructions.

### Development Workflow
- **Cloud Agent**: Code development, remote testing, automation
- **Local Machine**: Visual VNC testing, interactive ADB sessions

See **[DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md)** for details.

---

## 📄 License

[Add your license here]

---

## 🙏 Acknowledgments

- **Redroid**: Docker-based Android container solution
- **Oracle Cloud**: Always Free tier ARM instances
- **Waydroid**: Initial exploration (switched to Redroid for better compatibility)

---

## 📞 Support

For issues, questions, or contributions:
1. Check **[HANDOFF.md](HANDOFF.md)** for troubleshooting
2. Review **[TEST_RESULTS_FULL_COVERAGE.md](TEST_RESULTS_FULL_COVERAGE.md)** for current status
3. Run test suite: `./scripts/test-redroid-full.sh <INSTANCE_IP>`

---

**Status:** ✅ Operational - Full Featured

**Last Updated:** 2026-01-14
