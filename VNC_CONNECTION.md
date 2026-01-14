# VNC Connection Guide

## Quick Connect

**Connection Details:**
- **Host:** `137.131.52.69`
- **Port:** `5900`
- **Password:** `redroid`
- **Full Address:** `137.131.52.69:5900`

---

## Method 1: TigerVNC Viewer (Recommended)

### Install (if needed):
```bash
# Ubuntu/Debian
sudo apt-get install tigervnc-viewer

# macOS
brew install tigervnc-viewer

# Windows
# Download from: https://www.tigervnc.org/
```

### Connect:
```bash
vncviewer 137.131.52.69:5900
```

When prompted, enter password: `redroid`

---

## Method 2: Remmina (Linux)

```bash
# Install
sudo apt-get install remmina

# Connect
remmina -c vnc://137.131.52.69:5900
```

Password: `redroid`

---

## Method 3: Vinagre (Linux)

```bash
# Install
sudo apt-get install vinagre

# Connect
vinagre 137.131.52.69:5900
```

Password: `redroid`

---

## Method 4: Browser (noVNC)

If you have noVNC setup, you can connect via browser:
```
http://137.131.52.69:6080/vnc.html
```

---

## Method 5: SSH Tunnel (if direct connection fails)

If port 5900 is blocked, create SSH tunnel:

```bash
# Create tunnel
ssh -i ~/.ssh/waydroid_oci -L 5900:localhost:5900 ubuntu@137.131.52.69 -N

# Then connect locally
vncviewer localhost:5900
```

Password: `redroid`

---

## Troubleshooting

### Connection Refused

1. **Check instance is running:**
   ```bash
   oci compute instance get --instance-id ocid1.instance.oc1.phx.anyhqljrgmifkaqclk7h23un7agzbd6zay7muuqkoxbhm4xgxnsqsdt5w2eq
   ```

2. **Check VNC is listening:**
   ```bash
   ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'sudo ss -tlnp | grep 5900'
   ```

3. **Check security list:**
   - OCI Console → Networking → Security Lists
   - Ensure port 5900 is allowed for ingress

4. **Wait 2-3 minutes** for security list changes to propagate

### Can't Connect

1. **Verify Redroid container is running:**
   ```bash
   ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'docker ps | grep redroid'
   ```

2. **Check VNC service inside container:**
   ```bash
   ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'docker exec redroid sh -c "pgrep -f vnc"'
   ```

3. **Restart Redroid if needed:**
   ```bash
   ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'docker restart redroid'
   ```

### Wrong Password

Default Redroid VNC password is `redroid`. If changed, check:
```bash
ssh -i ~/.ssh/waydroid_oci ubuntu@137.131.52.69 'docker exec redroid cat /data/system/password.txt 2>/dev/null || echo "Using default: redroid"'
```

---

## What You Should See

Once connected, you should see:
- Android 16 home screen
- Standard Android interface
- Ability to interact with touch/mouse

---

## Quick Test Script

Run this to test VNC connectivity:

```bash
./scripts/test-adb-vnc.sh 137.131.52.69
```

This will check both ADB and VNC ports.



