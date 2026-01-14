# Anti-Detection Guide

This guide explains how to configure Cloud Phone to avoid detection as an emulator/virtual device.

## Overview

Many apps detect emulators and virtual devices to prevent automation, fraud, or cheating. Cloud Phone includes comprehensive anti-detection features to appear as a real physical device.

## Detection Vectors Addressed

| Vector | Description | Mitigation |
|--------|-------------|------------|
| Build Properties | `ro.product.*`, `ro.build.*` | Device profile spoofing |
| Hardware IDs | IMEI, Serial, MAC, Android ID | Random ID generation |
| Emulator Artifacts | `/dev/goldfish`, qemu files | File removal/hiding |
| Debug Flags | `ro.debuggable`, adb secure | Property modification |
| Root Indicators | su binary, Magisk | File permission changes |
| Boot State | Verified boot, locked bootloader | Property spoofing |
| GL Renderer | SwiftShader, llvmpipe | GPU property modification |
| Battery | Always charging, fake stats | Battery state spoofing |
| Sensors | Missing accelerometer, etc. | Sensor property config |

## Quick Start

### Via Script

```bash
# Apply Samsung Galaxy S21 profile with all anti-detection
./scripts/anti-detection.sh apply samsung-galaxy-s21

# Or use random profile
./scripts/anti-detection.sh apply random

# Check status
./scripts/anti-detection.sh status

# Generate new hardware IDs
./scripts/anti-detection.sh generate-ids
```

### Via API

```bash
# Apply full anti-detection
curl -X POST http://localhost:8080/device/antidetect \
  -H "Content-Type: application/json" \
  -d '{
    "profile": "samsung-galaxy-s21",
    "hide_root": true,
    "hide_emulator": true,
    "spoof_battery": true
  }'

# Check status
curl http://localhost:8080/device/antidetect/status

# List available profiles
curl http://localhost:8080/device/identity/profiles

# Generate new IDs only
curl -X POST http://localhost:8080/device/identity/generate
```

### Via Deployment

```bash
# Deploy with anti-detection enabled
./scripts/deploy-cloud-phone.sh \
  --name my-phone \
  --config antidetect-config.json
```

### Pre-built Anti-Detection Image

```bash
# Build image with anti-detection baked in
cd docker/
docker build -f Dockerfile.antidetect -t cloud-phone:antidetect .

# Use the image
REDROID_IMAGE=cloud-phone:antidetect docker-compose up -d
```

## Device Profiles

### Available Profiles

| Profile | Description |
|---------|-------------|
| `samsung-galaxy-s21` | Samsung Galaxy S21 5G (SM-G991B) |
| `google-pixel-6` | Google Pixel 6 |
| `oneplus-9-pro` | OnePlus 9 Pro |
| `random` | Randomly select a profile |

### Profile Contents

Each profile sets 50+ device properties including:

```properties
# Product identification
ro.product.brand=samsung
ro.product.model=SM-G991B
ro.product.device=o1s
ro.product.manufacturer=samsung

# Build information
ro.build.fingerprint=samsung/o1sxeea/o1s:12/SP1A.210812.016/G991BXXS5CVK1:user/release-keys
ro.build.type=user
ro.build.tags=release-keys

# Security state
ro.boot.verifiedbootstate=green
ro.boot.flash.locked=1
ro.debuggable=0
ro.secure=1
```

### Custom Profiles

Create custom profiles in `config/device-profiles/`:

```bash
# Create custom profile
cat > config/device-profiles/my-device.prop <<EOF
ro.product.brand=xiaomi
ro.product.model=M2102K1G
ro.product.device=alioth
ro.build.fingerprint=Xiaomi/alioth/alioth:12/...
# ... more properties
EOF

# Apply custom profile
./scripts/anti-detection.sh apply my-device
```

## Hardware ID Spoofing

### Generated IDs

The system generates realistic:

- **IMEI** - Valid 15-digit with Luhn checksum
- **Serial Number** - 11-character alphanumeric
- **MAC Address** - Using real vendor prefixes
- **Android ID** - 16-character hex
- **Advertising ID** - UUID format

### Manual ID Setting

```bash
# Via API
curl -X POST http://localhost:8080/device/identity \
  -H "Content-Type: application/json" \
  -d '{
    "profile": "samsung-galaxy-s21",
    "generate_ids": false,
    "custom": {
      "android_id": "abc123def456789a",
      "serial": "R5CW12345AB"
    }
  }'
```

## API Reference

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/device/identity` | GET | Get current device identity |
| `/device/identity` | POST | Set device identity (profile + IDs) |
| `/device/identity/profiles` | GET | List available profiles |
| `/device/identity/generate` | POST | Generate new random IDs |
| `/device/antidetect` | POST | Apply full anti-detection |
| `/device/antidetect/status` | GET | Check anti-detection status |
| `/device/antidetect/reset` | POST | Reset to default state |

### Example Responses

```json
// GET /device/antidetect/status
{
  "enabled": true,
  "profile": "samsung-galaxy-s21",
  "checks": {
    "debuggable": true,
    "qemu_hidden": true,
    "boot_state_green": true,
    "user_build": true
  },
  "score": "4/4",
  "detection_risk": "low"
}
```

## Limitations

### Cannot Bypass

1. **Play Integrity (Strong)** - Hardware-backed attestation checks the actual hardware
2. **Deep Binary Analysis** - Inspection of system libraries reveals virtualization
3. **Timing Attacks** - Some apps measure performance characteristics
4. **Network Fingerprinting** - IP reputation, data center detection
5. **Behavioral Analysis** - Unusual usage patterns

### Partial Bypass

1. **SafetyNet Basic** - Usually passes with proper configuration
2. **Root Detection** - Most checks bypassed, some may persist
3. **GL Renderer** - Software rendering may be detectable

## Best Practices

### For Maximum Stealth

1. **Use anti-detection Docker image** - Properties baked into system
2. **Generate fresh IDs** for each use case
3. **Use residential proxies** - Avoid data center IP detection
4. **Vary usage patterns** - Don't behave like a bot
5. **Keep Android version realistic** - Match the profile's expected version

### Configuration Example

```json
{
  "redroid": {
    "image": "cloud-phone:antidetect"
  },
  "network": {
    "proxy": {
      "enabled": true,
      "type": "socks5",
      "host": "residential-proxy.example.com",
      "port": 1080
    }
  },
  "antidetect": {
    "enabled": true,
    "profile": "samsung-galaxy-s21",
    "rotate_ids": true,
    "battery_simulation": true
  }
}
```

### Per-Session ID Rotation

```bash
# Rotate IDs before each session
curl -X POST http://localhost:8080/device/identity/generate
curl -X POST http://localhost:8080/device/antidetect \
  -d '{"profile": "random"}'
```

## Troubleshooting

### App Still Detects Emulator

1. **Check status**: `curl http://localhost:8080/device/antidetect/status`
2. **Verify properties**: `adb shell getprop | grep -E "(qemu|debug|virtual)"`
3. **Use pre-built image**: Properties in build.prop are more persistent
4. **Try different profile**: Some apps look for specific device signatures

### SafetyNet Failing

1. SafetyNet **Basic** should pass with anti-detection enabled
2. SafetyNet **Hardware** (CTS profile match) will fail on virtual devices
3. Consider using Magisk + Universal SafetyNet Fix for additional bypass

### Properties Reset After Reboot

- Use the `Dockerfile.antidetect` image - properties are permanent
- Or run `anti-detection.sh apply` on each container start

## Files Reference

```
config/device-profiles/
├── samsung-galaxy-s21.prop    # Samsung profile
├── google-pixel-6.prop        # Pixel profile
└── oneplus-9-pro.prop         # OnePlus profile

scripts/
└── anti-detection.sh          # Anti-detection script

docker/
└── Dockerfile.antidetect      # Pre-configured image

api/
└── server.py                  # API with device endpoints
```
