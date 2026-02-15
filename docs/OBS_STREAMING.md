# OBS Streaming to Redroid Cloud Phone

This guide documents the exact OBS settings required for streaming to the virtual camera/audio pipeline. These settings match the ffmpeg-bridge and have been verified by `test-virtual-device-output.sh`.

## Quick Setup

### Stream Settings (Settings → Stream)

| Setting | Value |
|---------|-------|
| **Service** | Custom... |
| **Server** | `rtmp://<INSTANCE_IP>/live` |
| **Stream Key** | `cam` |

Replace `<INSTANCE_IP>` with your Oracle Cloud instance public IP (e.g. `132.226.155.1`).

### Output Settings (Settings → Output)

| Setting | Value |
|---------|-------|
| **Output Mode** | Advanced |
| **Audio Encoder** | AAC |
| **Audio Bitrate** | 128 kbps |
| **Video Encoder** | x264 |
| **Video Bitrate** | 2500–4000 kbps |
| **Keyframe Interval** | 2 (or 30 for 15fps) |

### Video Settings (Settings → Video)

| Setting | Value |
|---------|-------|
| **Base (Canvas) Resolution** | 1080 x 1920 (portrait) or 1920 x 1080 (landscape) |
| **Output (Scaled) Resolution** | Same as base |
| **FPS** | 15 or 30 |

**Important:** The ffmpeg-bridge expects 1080×1920 (portrait) and scales/pads to that. Use 1080×1920 for best compatibility.

### Audio Settings (Settings → Audio)

| Setting | Value |
|---------|-------|
| **Sample Rate** | 44100 Hz |
| **Channels** | Stereo |

## Pipeline Overview

```
OBS → rtmp://<IP>/live/cam → nginx-rtmp → ffmpeg-bridge → /dev/video42 + ALSA Loopback
```

- **Video** goes to v4l2loopback (`/dev/video42`) → virtual camera (requires Camera HAL for Android apps; use VLC workaround)
- **Audio** goes to ALSA Loopback (`hw:Loopback,0,0`) → virtual microphone

## Verify Pipeline

Run the virtual device output test to confirm the pipeline works:

```bash
# Set your instance IP
export VM_HOST=132.226.155.1

# Run test (streams test pattern, verifies output)
./scripts/test-virtual-device-output.sh

# With capture saved for manual inspection
./scripts/test-virtual-device-output.sh --save-capture
```

If the test passes but OBS does not work, compare your OBS settings with the values above.

## Common Issues

### Stream not detected

- Ensure port **1935** is open in OCI security list (ingress)
- On the VM: `curl http://127.0.0.1:8081/health` (nginx-rtmp health)
- Verify ffmpeg-bridge: `ssh ubuntu@<IP> 'sudo systemctl status ffmpeg-bridge'`

### Black or corrupted video

- Match resolution: **1080×1920** (portrait) or ensure OBS output is scaled to that
- Use **H.264** with **YUV420** pixel format
- Set **15 fps** to match ffmpeg-bridge default

### No audio in Android

- Ensure **AAC** at **44100 Hz**, **stereo**
- Check snd-aloop: `aplay -l | grep Loopback`
- Redroid must be started with `/dev/snd` passthrough (see `setup-redroid-virtual-devices.sh`)

### OBS "Failed to connect"

- Check firewall/security list allows TCP 1935
- Verify instance is running: `ssh ubuntu@<IP> 'sudo systemctl status nginx-rtmp'`
- Try streaming from the same network first (e.g. from the VM itself with `rtmp://127.0.0.1/live/cam`)

## Reference: ffmpeg-bridge Expected Format

The ffmpeg-bridge (`scripts/ffmpeg-bridge.sh`) is configured for:

- **Video:** 1080×1920, 15 fps, YUV420, H.264
- **Audio:** 44100 Hz, stereo, AAC

OBS settings should match these for reliable operation.
