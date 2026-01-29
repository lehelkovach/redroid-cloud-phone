# Fixing Camera Detection in Redroid

## Current Status

**Issue**: Android apps cannot detect the virtual camera.

| Component | Status |
|-----------|--------|
| `/dev/video42` in container | ✅ Mounted |
| `v4l2loopback` module | ✅ Loaded |
| `cameraserver` running | ✅ Active |
| Camera HAL library | ❌ **Missing** |
| External Camera Provider | ❌ **Missing** |
| Cameras detected | **0** |

## Root Cause

Redroid's base image does not include a Camera HAL (Hardware Abstraction Layer). The HAL is required to translate V4L2 devices to Android's Camera API.

```
┌─────────────────────────────────────────────────────────────┐
│                    Android Camera Stack                      │
├─────────────────────────────────────────────────────────────┤
│  Camera Apps (Open Camera, Firefox, etc.)                   │
│                          ↓                                   │
│  Android Camera API (Camera2 API)                           │
│                          ↓                                   │
│  CameraService (system/bin/cameraserver)      ← RUNNING ✅  │
│                          ↓                                   │
│  Camera Provider HIDL (2.4/2.5/2.6)           ← MISSING ❌  │
│                          ↓                                   │
│  Camera HAL (camera.external.so)              ← MISSING ❌  │
│                          ↓                                   │
│  V4L2 Device (/dev/video42)                   ← MOUNTED ✅  │
└─────────────────────────────────────────────────────────────┘
```

## Solution Options

### Option 1: VLC Workaround (Immediate)

Since camera HAL is missing, use VLC to play the RTMP stream directly:

```bash
# Via ADB
adb shell am start -a android.intent.action.VIEW \
  -d "rtmp://127.0.0.1/live/cam" \
  -n org.videolan.vlc/.gui.video.VideoPlayerActivity

# Or manually in VLC:
# 1. Open VLC
# 2. More → Stream
# 3. Enter: rtmp://127.0.0.1/live/cam
```

This bypasses Android's camera system entirely and displays the RTMP stream.

### Option 2: Build Custom Redroid with External Camera Provider

Build Redroid from source with AOSP's external camera provider included.

**Required Components:**
- `android.hardware.camera.provider@2.4-external-service`
- `camera.external.so` HAL module
- `/vendor/etc/external_camera_config.xml`
- VINTF manifest entries

**Steps:**

1. Clone Redroid source:
   ```bash
   mkdir redroid && cd redroid
   repo init -u https://github.com/nicknash/nicknash.git -b nicknash-11
   repo sync -c -j$(nproc)
   ```

2. Modify `device/redroid/redroid.mk` to add external camera:
   ```makefile
   # External camera provider
   PRODUCT_PACKAGES += \
       android.hardware.camera.provider@2.4-external-service \
       camera.external
   
   PRODUCT_PROPERTY_OVERRIDES += \
       ro.hardware.camera=external
   
   # Camera config
   PRODUCT_COPY_FILES += \
       device/redroid/external_camera_config.xml:$(TARGET_COPY_OUT_VENDOR)/etc/external_camera_config.xml
   ```

3. Create `external_camera_config.xml`:
   ```xml
   <?xml version="1.0" encoding="UTF-8" ?>
   <ExternalCamera>
       <Provider>
           <ignore>
               <id>0</id>  <!-- Ignore /dev/video0 if present -->
           </ignore>
           <DevicePath>/dev/video42</DevicePath>
       </Provider>
       <Device>
           <Orientation>0</Orientation>
           <HighResolution>
               <Width>1920</Width>
               <Height>1080</Height>
           </HighResolution>
           <Sensor>
               <Width>1920</Width>
               <Height>1080</Height>
           </Sensor>
       </Device>
   </ExternalCamera>
   ```

4. Build:
   ```bash
   source build/envsetup.sh
   lunch redroid_arm64-userdebug
   make -j$(nproc)
   ```

### Option 3: Extract HAL from Android-x86

Android-x86 includes a working V4L2 camera HAL.

1. Download Android-x86 ISO:
   ```bash
   wget https://osdn.net/projects/android-x86/downloads/71931/android-x86_64-9.0-r2.iso
   ```

2. Extract camera HAL:
   ```bash
   mkdir android-x86 && cd android-x86
   7z x ../android-x86_64-9.0-r2.iso system.sfs
   unsquashfs system.sfs
   cp squashfs-root/system/lib64/hw/camera.x86.so .
   ```

3. Copy to Redroid:
   ```bash
   docker cp camera.x86.so redroid:/vendor/lib64/hw/camera.v4l2.so
   docker exec redroid chmod 644 /vendor/lib64/hw/camera.v4l2.so
   ```

**Note**: This approach has limited compatibility due to API differences between Android versions.

### Option 4: Use Pre-built Community Images

Check for community Redroid images with camera support:

```bash
# Search Docker Hub
docker search redroid camera

# Known images with extended features:
docker pull teddynight/redroid
```

## Verification Commands

```bash
# Check if camera HAL exists
adb shell ls -la /vendor/lib64/hw/camera*.so

# Check camera count
adb shell dumpsys media.camera | grep "Number of camera"
# Output: "Number of camera devices: 0" (no HAL)
# Expected: "Number of camera devices: 1" (with HAL)

# Check v4l2 device in container
adb shell ls -la /dev/video*
# Should show: /dev/video42

# Check camera property
adb shell getprop ro.hardware.camera
# Current: "v4l2" (property set but no HAL)

# Check camera provider service
adb shell getprop init.svc.vendor.camera-provider-2-4
# Current: empty (not running)
```

## Pipeline Verification

Even without Camera HAL, the streaming pipeline works:

```bash
# 1. Send test stream from local machine
ffmpeg -f lavfi -i testsrc2=size=1280x720:rate=30 \
  -t 10 -c:v libx264 -preset ultrafast \
  -f flv rtmp://VM_IP/live/cam

# 2. Verify RTMP server receives stream
curl -s http://VM_IP:8081/stat | grep -c '<stream>'

# 3. Verify ffmpeg-bridge is writing to video42
ssh ubuntu@VM_IP 'pgrep -a ffmpeg | grep video42'

# 4. Verify data on video42
ssh ubuntu@VM_IP 'sudo timeout 1 cat /dev/video42 | wc -c'
# Should show bytes if stream is active
```

## Current Workarounds Summary

| Workaround | Pros | Cons |
|------------|------|------|
| VLC RTMP | Works now, no changes needed | Not a camera, apps can't use it |
| Custom build | Full camera support | Requires AOSP build (hours) |
| Extract HAL | Quick if compatible | Version mismatch issues |
| Community image | Pre-built | May lack other features |

## Fix Script

A diagnostic and fix script is available:

```bash
# Check current status
./scripts/fix-camera-hal.sh check

# Attempt fixes
VM_HOST=132.226.155.1 ./scripts/fix-camera-hal.sh fix

# Show workarounds
./scripts/fix-camera-hal.sh workarounds
```

## References

- [AOSP External Camera Provider](https://android.googlesource.com/platform/hardware/interfaces/+/main/camera/provider/2.4/default/)
- [AOSP V4L2 Camera HAL](https://android.googlesource.com/platform/hardware/libhardware/+/master/modules/camera/3_4/)
- [Antmicro Camera HAL](https://github.com/antmicro/android-camera-hal)
- [Redroid Device Config](https://github.com/remote-android/device_redroid)
- [Redroid v4l2 Issue #14](https://github.com/remote-android/redroid-doc/issues/14)
