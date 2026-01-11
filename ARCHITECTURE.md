# Architecture Notes

## SOCKS5 Proxy (Optional)

**Default Behavior**: All traffic routes directly to the internet. The SOCKS5 proxy is completely optional and **disabled by default**.

- The `tun2socks.service` is installed but **not enabled** during installation
- Apps in Waydroid use the instance's direct internet connection by default
- Only enable if you need to route traffic through a SOCKS5 proxy

To enable:
```bash
sudo /opt/waydroid-scripts/socks5-toggle.sh enable proxy.example.com 1080
```

To disable (return to direct routing):
```bash
sudo /opt/waydroid-scripts/socks5-toggle.sh disable
```

## nginx-rtmp Location

**nginx-rtmp must run on the same instance as Waydroid** - it cannot be on a separate instance.

### Why Same Instance?

1. **Localhost Communication**: FFmpeg bridge reads from `rtmp://127.0.0.1/live/cam` (localhost only)
2. **Kernel Devices**: Outputs to `/dev/video42` (v4l2loopback) - a kernel device that must be on the same machine
3. **ALSA Loopback**: Outputs to ALSA Loopback (`hw:Loopback,0,0`) - an audio device on the same machine
4. **Waydroid Access**: Waydroid needs direct access to these virtual devices to use them as camera/microphone

### Architecture Flow

```
OBS Studio (External)
    ↓ (RTMP over internet)
nginx-rtmp (:1935) on instance
    ↓ (localhost RTMP)
FFmpeg Bridge (reads from 127.0.0.1)
    ↓
/dev/video42 (v4l2loopback kernel device)
    ↓
Waydroid Android (sees as camera)
```

If nginx-rtmp were on a separate instance, FFmpeg would need to:
- Read RTMP over the network (adds latency)
- Still output to local kernel devices (impossible from remote)
- Require complex network routing

**Conclusion**: Keep nginx-rtmp on the same instance for low latency and direct device access.

## Resource Usage

nginx-rtmp is lightweight:
- Minimal CPU usage (just RTMP protocol handling)
- Low memory footprint (~10-20MB)
- No significant impact on Waydroid performance

Running on the same instance is the recommended and only practical approach.

