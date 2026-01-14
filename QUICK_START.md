# Quick Start Guide

## For New Installation

### Option 1: Full Deployment with Virtual Devices (Recommended)

Deploy Ubuntu 20.04 instance with kernel 5.x for full virtual camera/audio support:

```bash
# Clone the repository
git clone https://github.com/lehelkovach/redroid-cloud-phone.git
cd redroid-cloud-phone

# Deploy to new Ubuntu 20.04 instance (requires OCI CLI)
./scripts/deploy-ubuntu20-redroid.sh my-cloud-phone
```

This automatically:
- Creates Oracle Cloud ARM instance with Ubuntu 20.04 (kernel 5.x)
- Installs Docker, Redroid, and all dependencies
- Sets up virtual camera (/dev/video42) and audio
- Starts all services

### Option 2: Manual Install on Existing Instance

SSH into an existing instance and run:

```bash
# Clone the repository
git clone https://github.com/lehelkovach/redroid-cloud-phone.git
cd redroid-cloud-phone

# Run the Redroid installer
sudo ./install-redroid.sh
sudo systemctl start redroid-cloud-phone.target
```

**Note:** Virtual devices only work on kernel 5.x (Ubuntu 20.04). Ubuntu 22.04 (kernel 6.8+) will have Redroid working but without virtual camera/audio.

## For Existing Installation

### 1. Verify Current State
```bash
# Check instance (if using OCI CLI)
oci compute instance get --instance-id ocid1.instance.oc1.phx.anyhqljrgmifkaqclk7h23un7agzbd6zay7muuqkoxbhm4xgxnsqsdt5w2eq --query 'data."lifecycle-state"'

# Check Redroid container
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo docker ps | grep redroid'

# Run full test suite
./scripts/test-redroid-full.sh 137.131.52.69

# Quick health check
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo /opt/waydroid-scripts/health-check.sh'
```

### 2. Key Information
| Item | Value |
|------|-------|
| **Instance IP** | 137.131.52.69 |
| **SSH Key** | ~/.ssh/waydroid_oci |
| **VNC Port** | 5900 |
| **VNC Password** | redroid |
| **ADB Port** | 5555 |
| **Status** | ✅ Operational |

### 3. Connect to Android

**Via VNC:**
```bash
# Create SSH tunnel
ssh -i ~/.ssh/waydroid_oci -L 5900:localhost:5900 ubuntu@137.131.52.69 -N

# In another terminal, connect VNC
vncviewer localhost:5900
# Password: redroid
```

**Via ADB:**
```bash
# Install ADB if needed
sudo apt-get install android-tools-adb

# Connect to Redroid
adb connect 137.131.52.69:5555
adb devices
adb shell
```

### 4. Key Scripts

| Script | Purpose |
|--------|---------|
| `./scripts/test-redroid-full.sh` | Comprehensive 10-category test suite |
| `./scripts/health-check.sh` | Quick health check |
| `./scripts/fix-redroid-vnc.sh` | Fix VNC issues |
| `./scripts/fix-v4l2loopback.sh` | Fix virtual camera module |
| `./scripts/test-api.sh` | Test Control API endpoints |

### 5. Next Steps

1. ✅ Run test suite to verify everything works
2. 🔧 Fix virtual devices if needed (kernel 6.8 compatibility)
3. 📡 Set up RTMP streaming (optional)
4. 🤖 Use Control API for automation

### 6. Read More
- `HANDOFF.md` - Complete handoff documentation
- `CURRENT_STATUS.md` - Current project status
- `ARCHITECTURE.md` - System architecture details
- `TROUBLESHOOTING.md` - Common issues and fixes
