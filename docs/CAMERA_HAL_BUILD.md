# Camera HAL Build - Handoff Document

## Status: 90% Complete - Service Binary Built, impl.so Needs 4 Fixes

### What Was Accomplished

The camera provider **service binary** has been successfully compiled from AOSP source
on the OCI build instance and deployed to the dev instance. The **impl.so** (camera HAL
implementation library) is partially compiled (7/11 source files). Once the remaining
4 files compile, Android apps will detect the virtual camera.

### Architecture

```
OBS → rtmp://132.226.155.1/live/cam → nginx-rtmp → ffmpeg-bridge → /dev/video0
                                                                        ↓
                                                               v4l2loopback (v0.15.3)
                                                                        ↓
                                                     Redroid container (/dev/video0)
                                                                        ↓
                                              camera.provider@2.4-impl.so (passthrough HAL)
                                                                        ↓
                                              ExternalCameraProviderImpl_2_4 → scans /dev/video*
                                                                        ↓
                                                            cameraserver → Camera2 API
                                                                        ↓
                                                              Android camera apps
```

### Infrastructure

| Resource | Status | Details |
|----------|--------|---------|
| Dev instance | `132.226.155.1` | Running Redroid 13.0.0_64only, all services active |
| Build instance | `152.70.146.56` | 200GB disk, AOSP source + build tools ready |
| v4l2loopback | v0.15.3 | `exclusive_caps=1, max_buffers=32` on `/dev/video0` |
| ffmpeg-bridge | Working | Full diagnostic logging, RTMP→v4l2 pipeline verified (117 frames) |
| ALSA Loopback | Working | Virtual mic captures 352KB audio |
| Service binary | **BUILT** | `/tmp/android.hardware.camera.provider@2.4-external-service` |
| impl.so | **7/11 compiled** | 4 source files need fixes (see below) |

### Build Instance (`152.70.146.56`)

SSH: `ssh -i ~/.ssh/waydroid_oci ubuntu@152.70.146.56`

Build tree at `~/aosp/`:
- AOSP repos synced: ~10GB (minimal: build system, camera, HIDL, frameworks)
- HIDL headers generated: 203 files in `~/aosp/generated/`
- Redroid .so libs for linking: `~/aosp/syslibs/`
- Compiled objects: `~/aosp/obj/`
- **Built service binary**: `~/aosp/android.hardware.camera.provider@2.4-external-service`

### What Needs to Be Fixed (4 source files)

All on the build instance at `~/aosp/`:

#### 1. `ExternalCameraUtils.cpp` (3.4)
- **Error**: Lambda return type incompatible with `boolean`
- **Fix**: Replace the `empty_output_buffer` lambda at line ~446:
  ```cpp
  // Change the lambda to:
  dmgr.mgr.empty_output_buffer = [](j_compress_ptr) -> boolean { return FALSE; };
  ```
- The `sed` patch didn't work because the ALOGV macro inside the lambda breaks parsing

#### 2. `ExternalCameraDeviceSession.cpp` (3.5)
- **Error**: `<stdatomic.h>` incompatible with `<atomic>` (clang 15 libc++)
- **Fix**: Compile with `-std=c++2b` (C++23 mode) which resolves the conflict
- **Also**: Needs `#include "include/convert.h"` path fix - add `-I hardware/interfaces/camera/device/3.4/default`

#### 3. `ExternalCameraDeviceSession.cpp` (3.6)
- **Error**: Same stdatomic + convert.h issues as 3.5
- **Fix**: Same as above

#### 4. `ExternalCameraOfflineSession.cpp` (3.6)
- **Error**: `Mutex::timedLock` not found + stdatomic
- **Fix**: Patch `timedLock(NS_TO_SECOND)` → `tryLock()` (already done for 3.4 version)
- **Fix**: Compile with `-std=c++2b`

### Compilation Commands

Working command for files that compile (on build instance):
```bash
cd ~/aosp
LIBCXX15=/usr/lib/llvm-15/include/c++/v1

clang++-15 -std=c++17 -fPIC -D__ANDROID_API__=33 -D__LP64__ -DPAGE_SIZE=4096 -DHAVE_PTHREADS=1 \
  -nostdinc++ -isystem $LIBCXX15 -include /tmp/compat_fixes.h \
  -Wno-inconsistent-missing-override -Wno-error \
  -I generated \
  -I system/libhidl/transport/include -I system/libhidl/base/include \
  -I system/core/libutils/include -I system/core/libcutils/include \
  -I system/core/libsystem/include -I system/core/libsync/include \
  -I system/logging/liblog/include -I system/libbase/include \
  -I system/libfmq/include -I system/libfmq/base \
  -I system/libhwbinder/include \
  -I frameworks/native/include -I frameworks/native/libs/binder/include \
  -I frameworks/native/libs/ui/include -I frameworks/native/libs/nativewindow/include \
  -I frameworks/native/libs/nativebase/include -I frameworks/native/libs/arect/include \
  -I hardware/libhardware/include \
  -I hardware/interfaces/camera/common/1.0/default/include \
  -I hardware/interfaces/camera/device/3.2/default -I hardware/interfaces/camera/device/3.2/default/include \
  -I hardware/interfaces/camera/device/3.3/default -I hardware/interfaces/camera/device/3.3/default/include \
  -I hardware/interfaces/camera/device/3.4/default -I hardware/interfaces/camera/device/3.4/default/include \
  -I hardware/interfaces/camera/device/3.4/default/include/ext_device_v3_4_impl \
  -I hardware/interfaces/camera/device/3.5/default -I hardware/interfaces/camera/device/3.5/default/include \
  -I hardware/interfaces/camera/device/3.5/default/include/ext_device_v3_5_impl \
  -I hardware/interfaces/camera/device/3.6/default -I hardware/interfaces/camera/device/3.6/default/include \
  -I hardware/interfaces/camera/device/3.6/default/include/ext_device_v3_6_impl \
  -I hardware/interfaces/camera/provider/2.4/default \
  -I external/libyuv/files/include -I external/tinyxml2 \
  -I system/media/camera/include -I system/media/private/camera/include \
  -c -o obj/<name>.o <source.cpp>
```

For files needing C++23 (stdatomic fix), change `-std=c++17` to `-std=c++2b`.

### Linking Command (once all 11 .o files exist)

```bash
clang++-15 -shared -nostdlib++ \
  -o "android.hardware.camera.provider@2.4-impl.so" \
  obj/*.o \
  -L syslibs \
  -lhidlbase -lutils -lcutils -llog -lbinder -lbase -lhardware -ljpeg \
  -l:android.hardware.camera.provider@2.4.so \
  -l:android.hardware.camera.common@1.0.so \
  -l:libc++.so \
  -Wl,--allow-shlib-undefined

# Fix library names from glibc to bionic
patchelf --replace-needed libc.so.6 libc.so android.hardware.camera.provider@2.4-impl.so
patchelf --replace-needed libm.so.6 libm.so android.hardware.camera.provider@2.4-impl.so
patchelf --replace-needed libgcc_s.so.1 libdl.so android.hardware.camera.provider@2.4-impl.so
patchelf --replace-needed libjpeg.so.8 libjpeg.so android.hardware.camera.provider@2.4-impl.so
```

### Deployment (once impl.so is built)

```bash
# Copy from build instance to local
scp -i ~/.ssh/waydroid_oci ubuntu@152.70.146.56:~/aosp/android.hardware.camera.provider@2.4-impl.so /tmp/

# Copy to dev instance
scp -i ~/.ssh/waydroid_oci /tmp/android.hardware.camera.provider@2.4-impl.so ubuntu@132.226.155.1:/tmp/

# Install into Redroid container
ssh -i ~/.ssh/waydroid_oci ubuntu@132.226.155.1 '
sudo docker cp /tmp/android.hardware.camera.provider@2.4-impl.so \
  redroid:/vendor/lib64/hw/android.hardware.camera.provider@2.4-impl.so
sudo docker exec redroid chmod 644 /vendor/lib64/hw/android.hardware.camera.provider@2.4-impl.so

# Copy Redroid system libs to /vendor/lib64 for sphal namespace
sudo docker exec redroid sh -c "
for lib in android.hardware.camera.common@1.0.so android.hardware.camera.device@1.0.so \
  android.hardware.camera.device@3.2.so android.hardware.camera.device@3.3.so \
  android.hardware.camera.device@3.4.so android.hardware.camera.device@3.5.so \
  android.hardware.camera.device@3.6.so android.hardware.camera.provider@2.4.so \
  android.hardware.graphics.mapper@2.0.so android.hardware.graphics.mapper@3.0.so \
  android.hardware.graphics.mapper@4.0.so android.hardware.graphics.allocator@2.0.so \
  android.hidl.allocator@1.0.so android.hidl.memory@1.0.so \
  libcamera_metadata.so libtinyxml2.so libyuv.so libjpeg.so libexif.so; do
  [ -f /system/lib64/\$lib ] && [ ! -f /vendor/lib64/\$lib ] && \
    cp /system/lib64/\$lib /vendor/lib64/\$lib
done"

# Restart and test
sudo docker restart redroid
# Wait for boot, feed video, check dumpsys media.camera
'
```

### Compat Header (`/tmp/compat_fixes.h` on build instance)

```c
#include <stdio.h>
#include <sys/ioctl.h>
#include <linux/videodev2.h>
#include <sys/mman.h>
#include <jpeglib.h>
#define HAVE_PTHREADS 1
#define _POSIX_TIMEOUTS 1
```

### Key Discoveries

1. **v4l2loopback 0.12.3 (Ubuntu package) is broken on kernel 5.15** - reports M2M caps instead of Capture/Output. Must upgrade to v0.15.3 from source.
2. **`exclusive_caps=1` is required** with v0.15.3 for proper concurrent read+write.
3. **`max_buffers=32`** prevents frame starvation for readers.
4. **Waydroid HAL binaries are ABI-incompatible** with Redroid due to different RefBase implementations. Must compile from same AOSP source.
5. **AOSP build system is x86-only** but `hidl-gen` (musl variant) runs on ARM64 via qemu-user. System clang-15 can compile the source.
6. **sphal linker namespace** only searches `/vendor/lib64/`, not `/system/lib64/`. All dependencies must be in vendor.

### Build Instance Cleanup

The build instance `152.70.146.56` costs money. Once the impl.so is built:
```bash
# Terminate build instance
oci --profile redroid-cloud-phone --auth security_token compute instance terminate \
  --instance-id ocid1.instance.oc1.phx.anyhqljrgmifkaqctzrsdrfcqb7v52rrmxcxidwmq2bspmvo7f2ljurhasta \
  --preserve-boot-volume false --force
```
