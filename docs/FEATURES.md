# Cloud Phone Features

This document describes all configurable features of the Cloud Phone system.

## Table of Contents

- [Deployment Options](#deployment-options)
- [Proxy Configuration](#proxy-configuration)
- [GPS/Location Spoofing](#gpslocation-spoofing)
- [Google Play Store (GApps)](#google-play-store-gapps)
- [Viewing Methods](#viewing-methods)
- [Control API](#control-api)
- [Instance Sizing](#instance-sizing)
- [Custom Redroid Images](#custom-redroid-images)

---

## Deployment Options

### Quick Deployment

```bash
# Basic deployment with defaults
./scripts/deploy-cloud-phone.sh --name my-phone

# High-performance with proxy
./scripts/deploy-cloud-phone.sh \
  --name proxy-phone \
  --ocpus 4 \
  --memory 16 \
  --proxy socks5://proxy.example.com:1080

# With GPS and Google Play
./scripts/deploy-cloud-phone.sh \
  --name gps-phone \
  --gps 37.7749,-122.4194 \
  --gapps

# From configuration file
./scripts/deploy-cloud-phone.sh --config my-config.json
```

### Configuration File

Create a JSON configuration file (see `config/cloud-phone-config.example.json`):

```json
{
  "instance": {
    "name": "my-cloud-phone",
    "ocpus": 2,
    "memory_gb": 8,
    "os_version": "20.04"
  },
  "redroid": {
    "image": "redroid/redroid:11.0.0-latest",
    "width": 1280,
    "height": 720,
    "gapps": { "enabled": true, "variant": "pico" }
  },
  "network": {
    "proxy": {
      "enabled": true,
      "type": "socks5",
      "host": "proxy.example.com",
      "port": 1080
    }
  },
  "location": {
    "enabled": true,
    "latitude": 37.7749,
    "longitude": -122.4194
  }
}
```

---

## Proxy Configuration

Route all Android traffic through a proxy server.

### Supported Proxy Types

| Type | Description | Method |
|------|-------------|--------|
| HTTP | Standard HTTP proxy | Android global settings |
| SOCKS5 | SOCKS5 proxy | tun2socks / redsocks |
| Transparent | Transparent proxy | iptables DNAT |

### Via Deployment Script

```bash
# SOCKS5 proxy
./scripts/deploy-cloud-phone.sh --proxy socks5://host:1080

# HTTP proxy
./scripts/deploy-cloud-phone.sh --proxy http://host:8080

# With authentication
./scripts/deploy-cloud-phone.sh \
  --proxy socks5://host:1080 \
  --proxy-user myuser \
  --proxy-pass mypass
```

### Via Control API

```bash
# Set SOCKS5 proxy
curl -X POST http://localhost:8080/proxy \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "type": "socks5",
    "host": "proxy.example.com",
    "port": 1080
  }'

# Disable proxy
curl -X DELETE http://localhost:8080/proxy
```

### Via Script (on instance)

```bash
# Enable SOCKS5 proxy
sudo /opt/redroid-scripts/proxy-control.sh enable socks5 host 1080

# Enable HTTP proxy
sudo /opt/redroid-scripts/proxy-control.sh enable http host 8080

# Disable
sudo /opt/redroid-scripts/proxy-control.sh disable

# Check status
sudo /opt/redroid-scripts/proxy-control.sh status
```

---

## GPS/Location Spoofing

Set fake GPS coordinates for location-based apps.

### Via Deployment Script

```bash
# Set location (San Francisco)
./scripts/deploy-cloud-phone.sh --gps 37.7749,-122.4194
```

### Via Control API

```bash
# Set location
curl -X POST http://localhost:8080/location \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "latitude": 37.7749,
    "longitude": -122.4194,
    "altitude": 10,
    "accuracy": 5
  }'

# Disable mock location
curl -X DELETE http://localhost:8080/location
```

### Via ADB

```bash
# Enable mock locations
adb shell settings put secure mock_location 1

# Set location via intent
adb shell am broadcast \
  -a android.intent.action.MOCK_LOCATION \
  --ef latitude 37.7749 \
  --ef longitude -122.4194
```

---

## Google Play Store (GApps)

Install Google Play Store and Google services.

### Via Deployment Script

```bash
# Enable with pico variant (minimal)
./scripts/deploy-cloud-phone.sh --gapps

# Specify variant
./scripts/deploy-cloud-phone.sh --gapps --gapps-variant nano
```

### Via Script (on instance)

```bash
# Install GApps (pico variant)
sudo /opt/redroid-scripts/install-gapps.sh pico

# Available variants: pico, nano, micro, mini, full

# Use pre-built image instead
sudo /opt/redroid-scripts/install-gapps.sh --use-image
```

### Pre-built Images with GApps

For easier setup, use a Redroid image with GApps pre-installed:

```json
{
  "redroid": {
    "image": "redroid/redroid:11.0.0-gapps"
  }
}
```

### Device Certification

If Play Store shows "Device not certified":

1. Get device ID:
   ```bash
   adb shell settings get secure android_id
   ```

2. Register at: https://www.google.com/android/uncertified/

### Sign‑In Troubleshooting

If Play Store won't sign in or Play Services errors:
```bash
sudo /opt/redroid-scripts/fix-play-services.sh
```

---

## Viewing Methods

Multiple options for viewing/controlling the Android screen.

### VNC (Default)

Built into Redroid, lowest setup required.

```bash
# SSH tunnel
ssh -L 5900:localhost:5900 ubuntu@YOUR_IP -N

# Connect
vncviewer localhost:5900
# Password: redroid
```

### scrcpy (Low Latency)

Best for interactive use with lower latency than VNC.

```bash
# Start scrcpy server
sudo /opt/redroid-scripts/viewing-control.sh scrcpy start

# On local machine (with SSH tunnel)
scrcpy -s localhost:5555
```

### WebRTC (Browser-based)

View in browser without native client.

```bash
# Start WebRTC server
sudo /opt/redroid-scripts/viewing-control.sh webrtc start

# Access via browser
# http://localhost:8188
```

### Headless (No Display)

For automation without visual output.

```bash
# Deploy in headless mode
./scripts/deploy-cloud-phone.sh --viewing none

# All control via API
curl http://localhost:8080/device/screenshot > screen.png
```

---

## Control API

Full REST API for programmatic control.

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/status` | GET | Device status |
| `/proxy` | GET/POST/DELETE | Proxy configuration |
| `/location` | GET/POST/DELETE | GPS/location |
| `/adb/shell` | POST | Execute shell command |
| `/adb/install` | POST | Install APK |
| `/adb/push` | POST | Upload file |
| `/adb/pull` | POST | Download file |
| `/device/screen` | POST | Control screen |
| `/device/input` | POST | Send input events |
| `/device/screenshot` | GET | Capture screenshot |
| `/jobs` | POST | Create async job |
| `/jobs/<id>` | GET | Poll job status/result |
| `/apps` | GET | List installed apps |
| `/apps/<pkg>/start` | POST | Launch app |
| `/apps/<pkg>/stop` | POST | Force stop app |
| `/settings/<ns>/<key>` | GET/PUT | Android settings |

### Authentication

Enable API authentication:

```bash
# Deploy with token
./scripts/deploy-cloud-phone.sh --api-token mysecrettoken

# Use token in requests
curl -H "Authorization: Bearer mysecrettoken" \
  http://localhost:8080/status
```

### Examples

```bash
# Take screenshot
curl -o screen.png http://localhost:8080/device/screenshot

# Tap screen
curl -X POST http://localhost:8080/device/input \
  -H "Content-Type: application/json" \
  -d '{"type": "tap", "x": 500, "y": 500}'

# Create async ADB job
curl -X POST http://localhost:8080/jobs \
  -H "Content-Type: application/json" \
  -d '{"type":"adb_shell","payload":{"command":"getprop ro.build.version.release"}}'

# Poll job
curl http://localhost:8080/jobs/<JOB_ID>

# Type text
curl -X POST http://localhost:8080/device/input \
  -H "Content-Type: application/json" \
  -d '{"type": "text", "text": "Hello World"}'

# Install APK
curl -X POST http://localhost:8080/adb/install \
  -F "file=@myapp.apk"

# Run shell command
curl -X POST http://localhost:8080/adb/shell \
  -H "Content-Type: application/json" \
  -d '{"command": "pm list packages"}'

# Start an app
curl -X POST http://localhost:8080/apps/com.example.app/start

# Get device info
curl http://localhost:8080/status
```

---

## Instance Sizing

Customize OCI instance resources.

### Options

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `--ocpus` | 2 | 1-4 | Number of ARM CPUs |
| `--memory` | 8 | 1-24 | Memory in GB |
| `--os-version` | 20.04 | 20.04, 22.04 | Ubuntu version |

### Recommendations

| Use Case | OCPUs | Memory | Notes |
|----------|-------|--------|-------|
| Basic testing | 1 | 4GB | Minimum viable |
| Standard use | 2 | 8GB | Default, good balance |
| Gaming/heavy apps | 4 | 12GB | Better performance |
| Multiple instances | 1 | 6GB | Per-instance allocation |

### Examples

```bash
# High-performance instance
./scripts/deploy-cloud-phone.sh \
  --name power-phone \
  --ocpus 4 \
  --memory 16

# Minimal instance
./scripts/deploy-cloud-phone.sh \
  --name minimal-phone \
  --ocpus 1 \
  --memory 4
```

---

## Custom Redroid Images

Use custom or forked Redroid images.

### Via Config

```json
{
  "redroid": {
    "image": "myregistry.com/my-redroid:custom"
  }
}
```

### Via Command Line

```bash
./scripts/deploy-cloud-phone.sh \
  --image myregistry.com/my-redroid:custom
```

### Available Images

| Image | Description |
|-------|-------------|
| `redroid/redroid:11.0.0-latest` | Android 11 (default, stable) |
| `redroid/redroid:latest` | Latest Android |
| `redroid/redroid:12.0.0-latest` | Android 12 |
| `redroid/redroid:13.0.0-latest` | Android 13 |
| `redroid/redroid:11.0.0-gapps` | Android 11 with GApps |

### Building Custom Image

```dockerfile
FROM redroid/redroid:11.0.0-latest

# Add custom apps
COPY myapp.apk /system/priv-app/MyApp/

# Custom configuration
COPY build.prop.patch /
RUN patch /system/build.prop /build.prop.patch
```

```bash
docker build -t my-redroid:custom .
docker push myregistry.com/my-redroid:custom
```

---

## Quick Reference

### All Deployment Options

```bash
./scripts/deploy-cloud-phone.sh \
  --name my-phone \
  --ocpus 2 \
  --memory 8 \
  --os-version 20.04 \
  --image redroid/redroid:11.0.0-latest \
  --width 1280 \
  --height 720 \
  --fps 30 \
  --vnc-port 5900 \
  --adb-port 5555 \
  --proxy socks5://host:port \
  --proxy-user user \
  --proxy-pass pass \
  --gps 37.7749,-122.4194 \
  --gapps \
  --gapps-variant pico \
  --api-token secret \
  --viewing vnc \
  --config config.json \
  --dry-run
```

### Environment Variables

```bash
export COMPARTMENT_ID="ocid1.compartment..."
export SUBNET_ID="ocid1.subnet..."
export AVAILABILITY_DOMAIN="AD-1"
export SSH_KEY_FILE="~/.ssh/id_rsa.pub"
```
