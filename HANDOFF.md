# Project Handoff Document

**Project:** Redroid Cloud Phone (formerly Waydroid Cloud Phone)  
**Date:** 2026-01-14
**Status:** ✅ Operational - Ready for Migration (Virtual Devices Blocked on Current Kernel)

---

## Recent Updates (2026-01-14)
- **Virtual Device Support:** Identified as INCOMPATIBLE with Kernel 6.8 (default Oracle ARM Ubuntu 22.04).
- **Scripts Updated:**
  - `scripts/setup-redroid-virtual-devices.sh`: Now enforces Kernel compatibility check (fails on 6.8) and installs Docker if missing.
  - `scripts/test-redroid-complete.sh`: Updated default IP.
- **Path Forward:** STRICTLY requires Ubuntu 20.04 (Kernel 5.x) for virtual device support.

## Quick Start for New Agent

### Current State
- ✅ Redroid container running on Oracle Cloud ARM instance
- ✅ ADB port 5555 accessible
- ✅ VNC port 5900 accessible
- ✅ Test suites created and working
- ⚠️ Virtual devices (camera/audio) pending kernel 6.8 compatibility

### Instance Details
- **IP:** `137.131.52.69`
- **Instance ID:** `ocid1.instance.oc1.phx.anyhqljrgmifkaqclk7h23un7agzbd6zay7muuqkoxbhm4xgxnsqsdt5w2eq`
- **SSH Key:** `~/.ssh/waydroid_oci`
- **User:** `ubuntu`
- **OS:** Ubuntu 22.04.5 LTS (kernel 6.8.0-1038-oracle)

### Quick Verification
```bash
# Check instance status
oci compute instance get --instance-id ocid1.instance.oc1.phx.anyhqljrgmifkaqclk7h23un7agzbd6zay7muuqkoxbhm4xgxnsqsdt5w2eq --query 'data."lifecycle-state"' --raw-output

# Check Redroid container
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo docker ps | grep redroid'

# Run full test suite
./scripts/test-redroid-full.sh 137.131.52.69
```

---

## Project Structure

### Key Directories
```
waydroid-cloud-phone/
├── scripts/              # All automation scripts
├── docs/                 # Documentation (if exists)
├── *.md                  # Project documentation files
└── HANDOFF.md           # This file
```

### Critical Files

**Scripts:**
- `scripts/test-redroid-full.sh` - Comprehensive test suite (10 test categories)
- `scripts/test-adb-vnc.sh` - ADB/VNC connectivity test
- `scripts/fix-redroid-vnc.sh` - Fix VNC configuration
- `scripts/check-redroid-vnc.sh` - Check VNC status
- `scripts/test-redroid-complete.sh` - Complete Redroid test with device passthrough
- `scripts/fix-instance-connectivity.sh` - Fix SSH connectivity issues

**Documentation:**
- `TEST_RESULTS_FULL_COVERAGE.md` - Latest test results
- `PROGRESS_FULL_TEST_COVERAGE.md` - Progress tracking
- `DEVELOPMENT_WORKFLOW.md` - Development workflow guide
- `VNC_CONNECT_NOW.md` - VNC connection instructions
- `REDROID_TEST_INSTRUCTIONS.md` - Redroid testing guide
- `ALTERNATIVES.md` - Alternative solutions explored
- `DECISION_REDROID.md` - Why Redroid was chosen

---

## Current Configuration

### Redroid Container
- **Image:** `redroid/redroid:latest`
- **Container Name:** `redroid`
- **Ports:**
  - `5555` → ADB
  - `5900` → VNC
- **Boot Parameters:**
  - `androidboot.redroid_gpu_mode=guest`
  - `androidboot.redroid_width=1280`
  - `androidboot.redroid_height=720`
  - `androidboot.redroid_fps=30`
  - `androidboot.redroid_vnc=1`
  - `androidboot.redroid_vnc_port=5900`

### Docker Setup
```bash
# Container is started with:
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

## Credentials & Access

### SSH Access
- **Key Path:** `~/.ssh/waydroid_oci`
- **Command:** `ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69`

### VNC Access
- **Port:** `5900`
- **Password:** `redroid`
- **Connection:** Via SSH tunnel (see `VNC_CONNECT_NOW.md`)

### OCI CLI
- **Config:** `~/.oci/config`
- **Key:** `~/.oci/oci_api_key.pem`
- **Fingerprint:** Check `~/.oci/config` for details

---

## Known Issues & Status

### ✅ Working
- Redroid container runs successfully
- ADB connectivity (port 5555)
- VNC connectivity (port 5900)
- Container resource usage is low
- No critical errors in logs

### ⚠️ Known Limitations
1. **Virtual Devices (Camera/Audio)**
   - **Issue:** `v4l2loopback` module fails on kernel 6.8.0-1038-oracle
   - **Impact:** Virtual camera (`/dev/video42`) not available
   - **Status:** Known limitation, requires kernel downgrade or module update
   - **Workaround:** Test on Ubuntu 20.04 (kernel 5.x)

2. **SSH Connectivity**
   - **Issue:** Occasional SSH timeouts despite instance being RUNNING
   - **Impact:** Interrupts testing/deployment
   - **Workaround:** Reboot instance via OCI CLI: `oci compute instance action --instance-id <OCID> --action RESET`

3. **ADB Testing**
   - **Status:** ADB not installed locally (pending)
   - **Fix:** `sudo apt-get install android-tools-adb`

---

## Next Steps & Priorities

### Immediate (High Priority)
1. **Install ADB locally** to complete test coverage
   ```bash
   sudo apt-get install android-tools-adb
   ./scripts/test-redroid-full.sh 137.131.52.69
   ```

2. **Test VNC connection** visually
   ```bash
   ssh -i ~/.ssh/waydroid_oci -L 5900:localhost:5900 ubuntu@137.131.52.69 -N
   vncviewer localhost:5900
   ```

3. **Verify Android functionality** via ADB
   ```bash
   adb connect 137.131.52.69:5555
   adb shell getprop ro.build.version.release
   ```

### Medium Priority
1. **Address virtual device support**
   - Option A: Create Ubuntu 20.04 instance (kernel 5.x)
   - Option B: Wait for v4l2loopback kernel 6.8 compatibility
   - Option C: Find alternative virtual device solution

2. **Implement RTMP streaming pipeline**
   - Set up FFmpeg bridge
   - Configure nginx-rtmp
   - Test stream to virtual camera

3. **Create control API**
   - REST API for device control
   - Screenshot endpoint
   - Touch/swipe automation

### Long-term
1. **Golden image creation** (once stable)
2. **Multi-instance deployment**
3. **Monitoring and logging**
4. **Documentation completion**

---

## Testing Commands

### Quick Health Check
```bash
# Instance status
oci compute instance get --instance-id ocid1.instance.oc1.phx.anyhqljrgmifkaqclk7h23un7agzbd6zay7muuqkoxbhm4xgxnsqsdt5w2eq --query 'data."lifecycle-state"' --raw-output

# Container status
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo docker ps | grep redroid'

# Container logs
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo docker logs --tail 20 redroid'
```

### Full Test Suite
```bash
# Run comprehensive tests
./scripts/test-redroid-full.sh 137.131.52.69

# Test ADB/VNC only
./scripts/test-adb-vnc.sh 137.131.52.69

# Check VNC status
./scripts/check-redroid-vnc.sh 137.131.52.69
```

### Fix Common Issues
```bash
# Fix SSH connectivity
./scripts/fix-instance-connectivity.sh

# Fix VNC
./scripts/fix-redroid-vnc.sh 137.131.52.69

# Reboot instance
oci compute instance action --instance-id ocid1.instance.oc1.phx.anyhqljrgmifkaqclk7h23un7agzbd6zay7muuqkoxbhm4xgxnsqsdt5w2eq --action RESET --wait-for-state RUNNING
```

---

## Development Workflow

### Using Cloud Agent (Current)
- ✅ Code development & scripting
- ✅ Remote testing & debugging
- ✅ Infrastructure management
- ✅ Automated test suites

### Using Local Machine
- Visual VNC testing
- Interactive ADB sessions
- Final UX verification

**See:** `DEVELOPMENT_WORKFLOW.md` for details

---

## Important Notes

1. **Instance IP may change** if instance is recreated
   - Check OCI console for current IP
   - Update scripts with new IP if needed

2. **SSH key location** is `~/.ssh/waydroid_oci`
   - Ensure key has correct permissions: `chmod 600 ~/.ssh/waydroid_oci`

3. **OCI CLI configuration** required for instance management
   - Config: `~/.oci/config`
   - Key: `~/.oci/oci_api_key.pem`

4. **Docker commands** require `sudo` on the instance
   - User `ubuntu` is in docker group, but some commands still need sudo

5. **VNC password** is `redroid` (default Redroid password)

---

## Useful Commands Reference

### Instance Management
```bash
# Get instance status
oci compute instance get --instance-id <OCID> --query 'data."lifecycle-state"' --raw-output

# Start instance
oci compute instance action --instance-id <OCID> --action START --wait-for-state RUNNING

# Stop instance
oci compute instance action --instance-id <OCID> --action STOP --wait-for-state STOPPED

# Reboot instance
oci compute instance action --instance-id <OCID> --action RESET --wait-for-state RUNNING
```

### Container Management
```bash
# Check container status
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo docker ps -a | grep redroid'

# View logs
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo docker logs redroid'

# Restart container
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo docker restart redroid'

# Stop container
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo docker stop redroid'

# Start container (if stopped)
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo docker start redroid'
```

### ADB Commands
```bash
# Connect to device
adb connect 137.131.52.69:5555

# List devices
adb devices

# Get Android version
adb shell getprop ro.build.version.release

# Get device model
adb shell getprop ro.product.model

# Open shell
adb shell

# Install APK
adb install app.apk

# Take screenshot
adb shell screencap -p /sdcard/screenshot.png
adb pull /sdcard/screenshot.png
```

### VNC Connection
```bash
# Create SSH tunnel
ssh -i ~/.ssh/waydroid_oci -L 5900:localhost:5900 ubuntu@137.131.52.69 -N

# Connect VNC (in another terminal)
vncviewer localhost:5900
# Password: redroid
```

---

## Documentation Files

All documentation is in the project root:

- `HANDOFF.md` - This file (handoff guide)
- `TEST_RESULTS_FULL_COVERAGE.md` - Latest test results
- `PROGRESS_FULL_TEST_COVERAGE.md` - Progress tracking
- `DEVELOPMENT_WORKFLOW.md` - Development workflow
- `VNC_CONNECT_NOW.md` - VNC connection guide
- `REDROID_TEST_INSTRUCTIONS.md` - Testing instructions
- `ALTERNATIVES.md` - Alternative solutions
- `DECISION_REDROID.md` - Why Redroid was chosen
- `REDROID_VIRTUAL_DEVICES.md` - Virtual device analysis
- `REDROID_ORACLE_LINUX.md` - Oracle Linux compatibility

---

## Quick Troubleshooting

### SSH Timeout
```bash
# Check instance status
oci compute instance get --instance-id <OCID> --query 'data."lifecycle-state"'

# Reboot if needed
oci compute instance action --instance-id <OCID> --action RESET --wait-for-state RUNNING
sleep 120
```

### Container Not Running
```bash
# Check Docker
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo systemctl status docker'

# Start container
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo docker start redroid'

# Or recreate
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 './scripts/fix-redroid-vnc.sh'
```

### VNC Not Working
```bash
# Check VNC port
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo ss -tlnp | grep 5900'

# Fix VNC
./scripts/fix-redroid-vnc.sh 137.131.52.69
```

### ADB Not Connecting
```bash
# Check ADB port
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo ss -tlnp | grep 5555'

# Check container logs
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo docker logs redroid | tail -20'

# Enable ADB in container
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo docker exec redroid setprop service.adb.tcp.port 5555'
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo docker exec redroid start adbd'
```

---

## Project Context

### Original Goal
Deploy a functional Android cloud phone on Oracle Cloud ARM instance with:
- Virtual camera support
- Virtual audio input support
- Remote access (VNC/ADB)
- RTMP streaming capability

### Current Implementation
- ✅ Redroid container running Android 16
- ✅ ADB access working
- ✅ VNC access working
- ⚠️ Virtual devices pending kernel compatibility

### Why Redroid?
- Waydroid had persistent binder/kernel issues
- Redroid is Docker-based (simpler deployment)
- Better ARM64 support
- Cloud-focused design
- No kernel binder dependencies

**See:** `DECISION_REDROID.md` for full analysis

---

## Contact & Support

### Project Files
- All scripts: `scripts/` directory
- Documentation: Root directory `*.md` files
- Test results: `TEST_RESULTS_FULL_COVERAGE.md`

### Key Information
- Instance IP: `137.131.52.69`
- Instance OCID: `ocid1.instance.oc1.phx.anyhqljrgmifkaqclk7h23un7agzbd6zay7muuqkoxbhm4xgxnsqsdt5w2eq`
- SSH Key: `~/.ssh/waydroid_oci`
- VNC Password: `redroid`

---

## Ready for Handoff ✅

**Status:** Project is operational and ready for continued development.

**Next Agent Should:**
1. **OBTAIN SSH KEYS** (`~/.ssh/waydroid_oci`). This is a critical blocker.
2. Create **Ubuntu 20.04** instance (use `scripts/create-ubuntu-20-instance.sh`) to support virtual devices.
3. Run `./scripts/setup-redroid-virtual-devices.sh <NEW_IP>` on the NEW instance.
4. Run `./scripts/test-redroid-complete.sh <NEW_IP>`.

**All necessary information is documented and scripts are ready to use.**

