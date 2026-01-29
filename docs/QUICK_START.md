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

## Interface & Control

### ADB + Scrcpy (Recommended)
```bash
adb connect <INSTANCE_IP>:5555
scrcpy -s <INSTANCE_IP>:5555
```

### Control API
```bash
curl http://<INSTANCE_IP>:8080/health
curl -X POST http://<INSTANCE_IP>:8080/device/input \
  -H "Content-Type: application/json" \
  -d '{"type":"tap","x":540,"y":960}'
```

### Control API CLI
```bash
python scripts/control-client.py --api-url http://<INSTANCE_IP>:8080 health
python scripts/control-client.py --api-url http://<INSTANCE_IP>:8080 tap --x 540 --y 960
python scripts/control-client.py --api-url http://<INSTANCE_IP>:8080 screenshot --out /tmp/screen.png
```

### Job Queue (Poll)
```bash
python scripts/control-client.py --api-url http://<INSTANCE_IP>:8080 job-submit \
  --type adb_shell --payload '{"command":"getprop ro.build.version.release"}'

python scripts/control-client.py --api-url http://<INSTANCE_IP>:8080 job-poll \
  --job-id <JOB_ID>
```

### Orchestrator (Mock + E2E)
```bash
# Run orchestrator in mock mode (uses local mock control API)
python orchestrator/server.py

# Run orchestrator in OCI mode
export ORCH_DEPLOY_MODE=oci
export GOLDEN_IMAGE_ID=<ocid>
export ORCH_MAX_INSTANCES=3
python orchestrator/server.py

# Run mock-agent E2E test
python tests/test_orchestrator_e2e.py

# Run unit tests
python -m unittest tests/test_orchestrator_unit.py

# Run integration test (mock control API + orchestrator routing)
python tests/test_orchestrator_integration.py
```

### Agent Bus + Orchestrator

See `docs/AGENT_COORDINATION.md` for orchestrator auth/phone routing and the
link to the external agent bus service.

If the API is bound to localhost, expose it:
```bash
sudo sed -i 's/^Environment=API_HOST=.*/Environment=API_HOST=0.0.0.0/' /etc/systemd/system/control-api.service
sudo systemctl daemon-reload
sudo systemctl restart control-api.service
```

### Camera App (for OBS testing)
```bash
sudo /opt/redroid-scripts/install-camera.sh
```

### Play Store Sign‑In Fix
```bash
sudo /opt/redroid-scripts/fix-play-services.sh
```

### OBS → Virtual Camera/Audio
```bash
# On the VM
sudo systemctl start nginx-rtmp.service
sudo systemctl start ffmpeg-bridge.service

# In OBS (Custom RTMP)
# Server: rtmp://<INSTANCE_IP>/live
# Key: cam
```

**Auto‑start & recovery:** `nginx-rtmp.service` and `ffmpeg-bridge.service`
are enabled by the installer and set to restart automatically.
```bash
sudo systemctl enable nginx-rtmp.service ffmpeg-bridge.service
sudo systemctl start redroid-cloud-phone.target
```

### Google Play (GApps)
```bash
# Place gapps.zip on the instance (MindTheGapps/NikGApps for Android 11)
sudo mkdir -p /opt/gapps
sudo mv /path/to/gapps.zip /opt/gapps/gapps.zip

# Install into Redroid and restart
sudo bash scripts/install-gapps.sh --install-local
sudo systemctl restart redroid-container.service
```

## For Existing Installation

### 1. Verify Current State
```bash
# Check instance (if using OCI CLI)
oci compute instance get --instance-id <OCID> --query 'data."lifecycle-state"'

# Check Redroid container
ssh -i ~/.ssh/redroid_oci ubuntu@<INSTANCE_IP> 'sudo docker ps | grep redroid'

# Run full test suite
./scripts/test-redroid-full.sh <INSTANCE_IP>

# Quick health check
ssh -i ~/.ssh/redroid_oci ubuntu@<INSTANCE_IP> 'sudo /opt/redroid-scripts/health-check.sh'
```

### 2. Key Information
| Item | Value |
|------|-------|
| **Instance IP** | <INSTANCE_IP> |
| **SSH Key** | ~/.ssh/redroid_oci |
| **VNC Port** | 5900 (may not be available on all images) |
| **VNC Password** | redroid |
| **ADB Port** | 5555 |
| **Status** | ✅ Operational |

### 3. Connect to Android

**Via VNC (if enabled by image):**
```bash
# Create SSH tunnel
ssh -i ~/.ssh/redroid_oci -L 5900:localhost:5900 ubuntu@<INSTANCE_IP> -N

# In another terminal, connect VNC
vncviewer localhost:5900
# Password: redroid
```

**Via ADB:**
```bash
# Install ADB if needed
sudo apt-get install android-tools-adb

# Connect to Redroid
adb connect <INSTANCE_IP>:5555
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
