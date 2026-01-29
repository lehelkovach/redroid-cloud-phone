# Research Notes

This document consolidates research conducted during project development.

## Android Cloud Solutions Comparison

### Evaluated Options

| Solution | Type | Virtual Camera | Free Tier | Status |
|----------|------|----------------|-----------|--------|
| **Redroid** | Docker | Requires HAL | OCI ARM | ✅ Chosen |
| Waydroid | LXC | Limited | - | ❌ Not Docker |
| Anbox Cloud | Managed | Yes | No | ❌ Cost |
| Genymotion Cloud | Managed | Yes | Trial only | ❌ Cost |
| Android-x86 | VM | Yes | OCI | Alternative |
| QEMU/KVM Android | VM | Yes | OCI | Complex |

### Why Redroid

1. **Docker-based** - Easy deployment, portable
2. **OCI Compatible** - Works with Always Free ARM instances
3. **GPU Support** - Hardware acceleration available
4. **Active Development** - Regular updates
5. **Documentation** - Well documented

### Redroid Limitations

- No camera HAL in base images
- Requires Ubuntu 20.04 (kernel 5.x) for virtual devices
- ARM64 only for free OCI tier

## Cloud Provider Research

### Oracle Cloud (OCI)

**Pros:**
- Always Free ARM instances (4 OCPUs, 24GB RAM)
- Generous bandwidth (10TB/month)
- Good ARM performance

**Cons:**
- Limited availability (capacity issues)
- ARM-only for free tier

### Alternatives Evaluated

| Provider | Free Tier | ARM | Notes |
|----------|-----------|-----|-------|
| AWS | Limited | Yes | More expensive |
| GCP | $300 credit | Yes | Time-limited |
| Azure | $200 credit | Limited | Time-limited |
| Hetzner | None | Yes | Cheap paid option |

## Virtual Device Research

### v4l2loopback

- Kernel module for virtual video devices
- Requires kernel headers to compile
- **Issue:** Fails on kernel 6.8+ (Ubuntu 22.04)
- **Solution:** Use Ubuntu 20.04 with kernel 5.x

### snd-aloop

- ALSA loopback for virtual audio
- Built into Ubuntu kernels
- Works reliably

### Camera HAL Research

Android's camera stack requires:

```
Apps → CameraService → Camera Provider (HIDL) → Camera HAL → Hardware
```

Redroid is missing the Camera HAL layer. Options:

1. **Build from AOSP** - Complex, requires AOSP build environment
2. **Extract from Android-x86** - Version mismatch (HAL 1.0 vs 3.x)
3. **Use alternative viewer** - VLC can play RTMP directly

See [CAMERA_HAL_FIX.md](CAMERA_HAL_FIX.md) for details.

## GitHub Repositories Referenced

### Android Containers

- [remote-android/redroid-doc](https://github.com/remote-android/redroid-doc) - Redroid documentation
- [nicknash/nicknash](https://github.com/nicknash/nicknash) - Custom Redroid builds

### Virtual Devices

- [umlaeute/v4l2loopback](https://github.com/umlaeute/v4l2loopback) - Virtual video device
- [nicknash/nicknash-camera-hal](https://github.com/nicknash/nicknash-camera-hal) - Camera HAL attempts

### Streaming

- [arut/nginx-rtmp-module](https://github.com/arut/nginx-rtmp-module) - RTMP server

## Bare Metal Android

Research into running Android directly on cloud instances:

### Options Evaluated

1. **Android-x86 in VM** - Works but heavy
2. **AOSP Custom Build** - Complex, maintenance burden
3. **Genymotion** - Commercial, works well

### Conclusion

Docker-based (Redroid) is more practical for:
- Easy deployment
- Reproducibility
- Container orchestration
- Resource efficiency

## Commercial Solutions

For reference, commercial alternatives:

| Solution | Virtual Camera | Pricing |
|----------|----------------|---------|
| GeeLark | Yes | ~$10/month |
| Genymotion Cloud | Yes | ~$50/month |
| Anbox Cloud | Yes | Contact sales |
| AWS Device Farm | Yes | Pay per minute |

These are alternatives if the open-source approach doesn't meet requirements.

## References

- [Redroid Documentation](https://github.com/remote-android/redroid-doc)
- [AOSP Camera HAL](https://source.android.com/devices/camera)
- [v4l2loopback Wiki](https://github.com/umlaeute/v4l2loopback/wiki)
- [OCI Always Free](https://www.oracle.com/cloud/free/)
