# Deployment Guide: OCI Always Free ARM Waydroid Cloud Phone

Complete step-by-step instructions for deploying on Oracle Cloud Infrastructure.

---

## Prerequisites

- Oracle Cloud account (free tier eligible)
- SSH key pair (or create one during setup)
- OBS Studio (for streaming camera/mic)
- VNC client (optional - can use SSH tunnel + browser)

---

## Part 1: Create OCI Account & Instance

### 1.1 Sign Up for Oracle Cloud

1. Go to [cloud.oracle.com](https://cloud.oracle.com)
2. Click **Sign Up** → Choose your region (closest to you)
3. Complete verification (credit card required but won't be charged for Always Free)
4. Wait for account activation (~5-30 minutes)

### 1.2 Create SSH Key (if you don't have one)

```bash
# On your local machine
ssh-keygen -t ed25519 -C "waydroid-oci"
# Save to ~/.ssh/waydroid_oci
# No passphrase is fine for testing

# View public key (you'll need this)
cat ~/.ssh/waydroid_oci.pub
```

### 1.3 Create ARM Instance

1. Log into OCI Console → **Compute** → **Instances** → **Create Instance**

2. **Name**: `waydroid-phone-1`

3. **Placement**: Leave default (AD-1 or AD-2)

4. **Image and Shape**:
   - Click **Edit**
   - **Image**: Click **Change Image**
     - Select **Ubuntu**
     - Version: **22.04** or **24.04**
     - Image type: **aarch64** (ARM)
   - **Shape**: Click **Change Shape**
     - **Ampere** (ARM processors)
     - Shape: `VM.Standard.A1.Flex`
     - **OCPUs**: `2`
     - **Memory**: `8 GB`

5. **Networking**:
   - Use default VCN or create new
   - **Public IPv4**: Assign public IP (required)
   - Subnet: Public subnet

6. **Add SSH Keys**:
   - Paste your public key from step 1.2

7. **Boot Volume**:
   - Size: `50 GB` (within free tier)
   - Leave encryption default

8. Click **Create**

9. **Wait** for instance to show "Running" (~2-5 minutes)

10. **Copy the Public IP** from instance details

### 1.4 Configure Security List (Firewall)

1. Go to **Networking** → **Virtual Cloud Networks**
2. Click your VCN → **Security Lists** → **Default Security List**
3. **Add Ingress Rules**:

| Stateless | Source | Protocol | Dest Port | Description |
|-----------|--------|----------|-----------|-------------|
| No | 0.0.0.0/0 | TCP | 22 | SSH |
| No | 0.0.0.0/0 | TCP | 1935 | RTMP |

> **Note**: VNC (5901) and API (8080) stay on localhost - accessed via SSH tunnel only.

---

## Part 2: Deploy Waydroid Cloud Phone

### 2.1 Connect to Instance

```bash
# Replace YOUR_IP with your instance's public IP
ssh -i ~/.ssh/waydroid_oci ubuntu@YOUR_IP
```

### 2.2 Upload and Extract

**Option A: From your local machine (SCP)**
```bash
# On your local machine
scp -i ~/.ssh/waydroid_oci waydroid-cloud-phone.tar.gz ubuntu@YOUR_IP:~
```

**Option B: Download directly on server**
```bash
# On the server - if hosted somewhere
# wget https://your-url/waydroid-cloud-phone.tar.gz
```

**Then on the server:**
```bash
cd ~
tar xzf waydroid-cloud-phone.tar.gz
cd waydroid-cloud-phone
```

### 2.3 Run Installer

```bash
sudo ./install.sh
```

This takes 5-15 minutes. It will:
- Install all packages (nginx, waydroid, vnc, etc.)
- Configure kernel modules
- Set up systemd services
- Install the control API

### 2.4 Reboot

```bash
sudo reboot
```

Wait 1-2 minutes, then reconnect:
```bash
ssh -i ~/.ssh/waydroid_oci ubuntu@YOUR_IP
```

### 2.5 Verify Kernel Modules

```bash
lsmod | grep -E "v4l2|snd_aloop|binder"
```

Expected output:
```
v4l2loopback           ...
snd_aloop              ...
```

Check devices:
```bash
ls -la /dev/video42
ls -la /dev/binderfs/
```

### 2.6 Initialize Waydroid

```bash
sudo /opt/waydroid-scripts/init-waydroid.sh
```

- Choose `1` for GAPPS (Google Play) or `2` for VANILLA
- Wait for download (~800MB-1.5GB)
- Wait for Android to boot (~1-2 minutes)

### 2.7 Start All Services

```bash
sudo systemctl start waydroid-cloud-phone.target
```

### 2.8 Verify Everything is Running

```bash
sudo /opt/waydroid-scripts/health-check.sh
```

All services should show green checkmarks.

---

## Part 3: Connect & Use

### 3.1 Create SSH Tunnel

From your **local machine**:

```bash
ssh -i ~/.ssh/waydroid_oci \
    -L 5901:localhost:5901 \
    -L 8080:localhost:8080 \
    -N ubuntu@YOUR_IP
```

Leave this terminal open. The flags:
- `-L 5901:localhost:5901` → VNC
- `-L 8080:localhost:8080` → Control API
- `-N` → Don't open shell, just tunnel

### 3.2 Connect VNC

**Option A: VNC Client**
- Open your VNC client (RealVNC, TigerVNC, etc.)
- Connect to: `localhost:5901`
- Password: `waydroid` (change this!)

**Option B: Browser (noVNC)**
If you want web-based VNC, install noVNC:
```bash
# On server
sudo apt install novnc websockify
websockify --web /usr/share/novnc 6080 localhost:5901
```
Then add `-L 6080:localhost:6080` to your SSH tunnel and open `http://localhost:6080`

### 3.3 Test Control API

```bash
# Device info
curl http://localhost:8080/device/info

# Screenshot
curl http://localhost:8080/device/screenshot > screenshot.png
open screenshot.png  # or xdg-open on Linux

# Tap center of screen
curl -X POST http://localhost:8080/device/tap \
  -H "Content-Type: application/json" \
  -d '{"x": 0.5, "y": 0.5, "mode": "norm"}'
```

### 3.4 Stream from OBS

1. Open **OBS Studio**
2. **Settings** → **Stream**:
   - Service: `Custom`
   - Server: `rtmp://YOUR_IP/live`
   - Stream Key: `cam`
3. Add sources (webcam, mic)
4. Click **Start Streaming**

The stream will appear as Android's camera input.

---

## Part 4: Optional Configuration

### 4.1 Enable SOCKS5 Proxy

Route all traffic through a SOCKS5 proxy:

```bash
# Enable
sudo /opt/waydroid-scripts/socks5-toggle.sh enable proxy.example.com 1080

# With authentication
sudo /opt/waydroid-scripts/socks5-toggle.sh enable proxy.example.com 1080 user pass

# Check status
sudo /opt/waydroid-scripts/socks5-toggle.sh status

# Disable
sudo /opt/waydroid-scripts/socks5-toggle.sh disable
```

### 4.2 Change VNC Password

```bash
sudo -u waydroid vncpasswd /home/waydroid/.vnc/passwd
# Enter new password twice
sudo systemctl restart xvnc
```

### 4.3 Enable Auto-Start on Boot

```bash
sudo systemctl enable waydroid-cloud-phone.target
```

### 4.4 Adjust Display Resolution

Edit `/etc/systemd/system/xvnc.service`:
```bash
sudo nano /etc/systemd/system/xvnc.service
# Change -geometry 1080x1920 to your preferred resolution
sudo systemctl daemon-reload
sudo systemctl restart xvnc waydroid-session
```

---

## Part 5: Create Golden Image (For Scaling)

Once everything works, create a golden image to quickly launch multiple instances.

### 5.1 Prepare Instance for Golden Image

**On your instance:**

```bash
# SSH into your instance
ssh -i ~/.ssh/waydroid_oci ubuntu@YOUR_IP

# Run the preparation script
sudo /opt/waydroid-scripts/prepare-golden-image.sh
```

Or manually:
```bash
# Stop services
sudo systemctl stop waydroid-cloud-phone.target

# Clean logs
sudo journalctl --vacuum-time=1d

# Clear bash history
history -c
rm -f ~/.bash_history

# Clear cloud-init
sudo cloud-init clean --logs

# Shutdown
sudo shutdown -h now
```

### 5.2 Create Custom Image in OCI Console

1. **OCI Console** → **Compute** → **Instances**
2. Click your instance → **More Actions** → **Create Custom Image**
3. **Name**: `waydroid-cloud-phone-v1`
4. **Description**: `Waydroid Android cloud phone with VNC, ADB API, RTMP ingest`
5. Click **Create Custom Image**
6. Wait 10-20 minutes (status: "Provisioning" → "Available")

### 5.3 Launch New Instances from Golden Image

**Option A: Using the Launch Script (Recommended)**

1. **Install OCI CLI** (if not already installed):
   ```bash
   # See: https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm
   ```

2. **Edit the script** with your OCIDs:
   ```bash
   nano scripts/launch-fleet.sh
   # Set: IMAGE_ID, COMPARTMENT_ID, SUBNET_ID, SSH_KEY_FILE
   # Adjust: OCPUS, MEMORY_GB, AVAILABILITY_DOMAINS
   ```

3. **Launch instances**:
   ```bash
   ./scripts/launch-fleet.sh 2  # Launch 2 instances
   ```

**Option B: Via OCI Console**

1. **Compute** → **Instances** → **Create Instance**
2. **Image**: Click **Change Image** → **My Images** → Select your golden image
3. **Shape**: `VM.Standard.A1.Flex` (adjust OCPU/RAM as needed)
4. **Networking**: Same VCN, assign public IP
5. **SSH Key**: Add your public key
6. Click **Create**

### 5.4 Start Services on New Instances

After launching, each new instance just needs:

```bash
# SSH in
ssh -i ~/.ssh/waydroid_oci ubuntu@NEW_INSTANCE_IP

# Start services (everything is pre-installed)
sudo systemctl start waydroid-cloud-phone.target

# Check status
sudo /opt/waydroid-scripts/health-check.sh
```

### 5.5 Fleet Sizing (Free Tier)

| Config | Instances | Reliability |
|--------|-----------|-------------|
| 2 OCPU / 8 GB | **2** | High ✓ |
| 1 OCPU / 6 GB | **3-4** | Medium |
| 1 OCPU / 4 GB | **4-6** | Low |

**Recommendation**: Start with 2 × (2 OCPU / 8 GB) for stability.

---

## Troubleshooting

### Instance Won't Create (Out of Capacity)

ARM instances are popular. Try:
- Different availability domain (AD-1, AD-2, AD-3)
- Smaller shape temporarily, then resize
- Try again later (capacity changes)
- Different region

### Waydroid Won't Start

```bash
# Check binder
ls /dev/binderfs/

# If empty, mount binderfs
sudo mount -t binder binder /dev/binderfs

# Check logs
journalctl -u waydroid-container -u waydroid-session -f
```

### No Video in Camera App

```bash
# Check FFmpeg bridge
journalctl -u ffmpeg-bridge -f

# Check video device
v4l2-ctl --device=/dev/video42 --all

# Test with ffmpeg manually
ffmpeg -f v4l2 -i /dev/video42 -frames 1 test.jpg
```

### VNC Black Screen

```bash
# Check Xvnc
journalctl -u xvnc -f

# Check XFCE
journalctl -u xfce-session -f

# Restart display stack
sudo systemctl restart xvnc waydroid-session
```

### API Returns Errors

```bash
# Check ADB connection
adb devices

# Reconnect to Waydroid
adb connect 192.168.240.112:5555

# Check API logs
journalctl -u control-api -f
```

### SSH Connection Refused

- Verify security list has port 22 open
- Check instance is running
- Verify correct IP address
- Try: `ssh -vvv` for debug output

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start all | `sudo systemctl start waydroid-cloud-phone.target` |
| Stop all | `sudo systemctl stop waydroid-cloud-phone.target` |
| Status | `sudo /opt/waydroid-scripts/health-check.sh` |
| Logs | `journalctl -u SERVICE_NAME -f` |
| VNC tunnel | `ssh -L 5901:localhost:5901 -N ubuntu@IP` |
| API tunnel | `ssh -L 8080:localhost:8080 -N ubuntu@IP` |
| Screenshot | `curl localhost:8080/device/screenshot > s.png` |
| Tap | `curl -X POST localhost:8080/device/tap -d '{"x":540,"y":960}'` |
| SOCKS5 on | `sudo /opt/waydroid-scripts/socks5-toggle.sh enable HOST PORT` |
| SOCKS5 off | `sudo /opt/waydroid-scripts/socks5-toggle.sh disable` |

---

## Cost Summary

| Resource | Free Tier Limit | Our Usage |
|----------|-----------------|-----------|
| ARM OCPUs | 4 | 2 per instance |
| RAM | 24 GB | 8 GB per instance |
| Boot Volume | 200 GB total | 50 GB per instance |
| Outbound Data | 10 TB/month | Varies |

**Result**: 2 instances completely free, or 3-4 smaller instances.
