# Quick Start Guide

## For New Installation

```bash
# Clone the repository
git clone https://github.com/lehelkovach/redroid-cloud-phone.git
cd redroid-cloud-phone

# Run the Redroid installer (recommended)
sudo ./install-redroid.sh
```

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
